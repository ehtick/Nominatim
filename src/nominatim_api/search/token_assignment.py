# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2024 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Create query interpretations where each vertice in the query is assigned
a specific function (expressed as a token type).
"""
from typing import Optional, List, Iterator
import dataclasses

from ..logging import log
from . import query as qmod


@dataclasses.dataclass
class TypedRange:
    """ A token range for a specific type of tokens.
    """
    ttype: qmod.TokenType
    trange: qmod.TokenRange


TypedRangeSeq = List[TypedRange]


@dataclasses.dataclass
class TokenAssignment:
    """ Representation of a possible assignment of token types
        to the tokens in a tokenized query.
    """
    penalty: float = 0.0
    name: Optional[qmod.TokenRange] = None
    address: List[qmod.TokenRange] = dataclasses.field(default_factory=list)
    housenumber: Optional[qmod.TokenRange] = None
    postcode: Optional[qmod.TokenRange] = None
    country: Optional[qmod.TokenRange] = None
    near_item: Optional[qmod.TokenRange] = None
    qualifier: Optional[qmod.TokenRange] = None

    @staticmethod
    def from_ranges(ranges: TypedRangeSeq) -> 'TokenAssignment':
        """ Create a new token assignment from a sequence of typed spans.
        """
        out = TokenAssignment()
        for token in ranges:
            if token.ttype == qmod.TOKEN_PARTIAL:
                out.address.append(token.trange)
            elif token.ttype == qmod.TOKEN_HOUSENUMBER:
                out.housenumber = token.trange
            elif token.ttype == qmod.TOKEN_POSTCODE:
                out.postcode = token.trange
            elif token.ttype == qmod.TOKEN_COUNTRY:
                out.country = token.trange
            elif token.ttype == qmod.TOKEN_NEAR_ITEM:
                out.near_item = token.trange
            elif token.ttype == qmod.TOKEN_QUALIFIER:
                out.qualifier = token.trange
        return out


class _TokenSequence:
    """ Working state used to put together the token assignments.

        Represents an intermediate state while traversing the tokenized
        query.
    """
    def __init__(self, seq: TypedRangeSeq,
                 direction: int = 0, penalty: float = 0.0) -> None:
        self.seq = seq
        self.direction = direction
        self.penalty = penalty

    def __str__(self) -> str:
        seq = ''.join(f'[{r.trange.start} - {r.trange.end}: {r.ttype}]' for r in self.seq)
        return f'{seq} (dir: {self.direction}, penalty: {self.penalty})'

    @property
    def end_pos(self) -> int:
        """ Return the index of the global end of the current sequence.
        """
        return self.seq[-1].trange.end if self.seq else 0

    def has_types(self, *ttypes: qmod.TokenType) -> bool:
        """ Check if the current sequence contains any typed ranges of
            the given types.
        """
        return any(s.ttype in ttypes for s in self.seq)

    def is_final(self) -> bool:
        """ Return true when the sequence cannot be extended by any
            form of token anymore.
        """
        # Country and category must be the final term for left-to-right
        return len(self.seq) > 1 and \
            self.seq[-1].ttype in (qmod.TOKEN_COUNTRY, qmod.TOKEN_NEAR_ITEM)

    def appendable(self, ttype: qmod.TokenType) -> Optional[int]:
        """ Check if the give token type is appendable to the existing sequence.

            Returns None if the token type is not appendable, otherwise the
            new direction of the sequence after adding such a type. The
            token is not added.
        """
        if ttype == qmod.TOKEN_WORD:
            return None

        if not self.seq:
            # Append unconditionally to the empty list
            if ttype == qmod.TOKEN_COUNTRY:
                return -1
            if ttype in (qmod.TOKEN_HOUSENUMBER, qmod.TOKEN_QUALIFIER):
                return 1
            return self.direction

        # Name tokens are always acceptable and don't change direction
        if ttype == qmod.TOKEN_PARTIAL:
            # qualifiers cannot appear in the middle of the query. They need
            # to be near the next phrase.
            if self.direction == -1 \
               and any(t.ttype == qmod.TOKEN_QUALIFIER for t in self.seq[:-1]):
                return None
            return self.direction

        # Other tokens may only appear once
        if self.has_types(ttype):
            return None

        if ttype == qmod.TOKEN_HOUSENUMBER:
            if self.direction == 1:
                if len(self.seq) == 1 and self.seq[0].ttype == qmod.TOKEN_QUALIFIER:
                    return None
                if len(self.seq) > 2 \
                   or self.has_types(qmod.TOKEN_POSTCODE, qmod.TOKEN_COUNTRY):
                    return None  # direction left-to-right: housenumber must come before anything
            elif (self.direction == -1
                  or self.has_types(qmod.TOKEN_POSTCODE, qmod.TOKEN_COUNTRY)):
                return -1  # force direction right-to-left if after other terms

            return self.direction

        if ttype == qmod.TOKEN_POSTCODE:
            if self.direction == -1:
                if self.has_types(qmod.TOKEN_HOUSENUMBER, qmod.TOKEN_QUALIFIER):
                    return None
                return -1
            if self.direction == 1:
                return None if self.has_types(qmod.TOKEN_COUNTRY) else 1
            if self.has_types(qmod.TOKEN_HOUSENUMBER, qmod.TOKEN_QUALIFIER):
                return 1
            return self.direction

        if ttype == qmod.TOKEN_COUNTRY:
            return None if self.direction == -1 else 1

        if ttype == qmod.TOKEN_NEAR_ITEM:
            return self.direction

        if ttype == qmod.TOKEN_QUALIFIER:
            if self.direction == 1:
                if (len(self.seq) == 1
                    and self.seq[0].ttype in (qmod.TOKEN_PARTIAL, qmod.TOKEN_NEAR_ITEM)) \
                   or (len(self.seq) == 2
                       and self.seq[0].ttype == qmod.TOKEN_NEAR_ITEM
                       and self.seq[1].ttype == qmod.TOKEN_PARTIAL):
                    return 1
                return None
            if self.direction == -1:
                return -1

            tempseq = self.seq[1:] if self.seq[0].ttype == qmod.TOKEN_NEAR_ITEM else self.seq
            if len(tempseq) == 0:
                return 1
            if len(tempseq) == 1 and self.seq[0].ttype == qmod.TOKEN_HOUSENUMBER:
                return None
            if len(tempseq) > 1 or self.has_types(qmod.TOKEN_POSTCODE, qmod.TOKEN_COUNTRY):
                return -1
            return 0

        return None

    def advance(self, ttype: qmod.TokenType, end_pos: int,
                force_break: bool, break_penalty: float) -> Optional['_TokenSequence']:
        """ Return a new token sequence state with the given token type
            extended.
        """
        newdir = self.appendable(ttype)
        if newdir is None:
            return None

        if not self.seq:
            newseq = [TypedRange(ttype, qmod.TokenRange(0, end_pos))]
            new_penalty = 0.0
        else:
            last = self.seq[-1]
            if not force_break and last.ttype == ttype:
                # extend the existing range
                newseq = self.seq[:-1] + [TypedRange(ttype, last.trange.replace_end(end_pos))]
                new_penalty = 0.0
            else:
                # start a new range
                newseq = list(self.seq) + [TypedRange(ttype,
                                                      qmod.TokenRange(last.trange.end, end_pos))]
                new_penalty = break_penalty

        return _TokenSequence(newseq, newdir, self.penalty + new_penalty)

    def _adapt_penalty_from_priors(self, priors: int, new_dir: int) -> bool:
        if priors >= 2:
            if self.direction == 0:
                self.direction = new_dir
            else:
                if priors == 2:
                    self.penalty += 0.8
                else:
                    return False

        return True

    def recheck_sequence(self) -> bool:
        """ Check that the sequence is a fully valid token assignment
            and adapt direction and penalties further if necessary.

            This function catches some impossible assignments that need
            forward context and can therefore not be excluded when building
            the assignment.
        """
        # housenumbers may not be further than 2 words from the beginning.
        # If there are two words in front, give it a penalty.
        hnrpos = next((i for i, tr in enumerate(self.seq)
                       if tr.ttype == qmod.TOKEN_HOUSENUMBER),
                      None)
        if hnrpos is not None:
            if self.direction != -1:
                priors = sum(1 for t in self.seq[:hnrpos] if t.ttype == qmod.TOKEN_PARTIAL)
                if not self._adapt_penalty_from_priors(priors, -1):
                    return False
            if self.direction != 1:
                priors = sum(1 for t in self.seq[hnrpos+1:] if t.ttype == qmod.TOKEN_PARTIAL)
                if not self._adapt_penalty_from_priors(priors, 1):
                    return False
            if any(t.ttype == qmod.TOKEN_NEAR_ITEM for t in self.seq):
                self.penalty += 1.0

        return True

    def _get_assignments_postcode(self, base: TokenAssignment,
                                  query_len: int) -> Iterator[TokenAssignment]:
        """ Yield possible assignments of Postcode searches with an
            address component.
        """
        assert base.postcode is not None

        if (base.postcode.start == 0 and self.direction != -1)\
           or (base.postcode.end == query_len and self.direction != 1):
            log().comment('postcode search')
            # <address>,<postcode> should give preference to address search
            if base.postcode.start == 0:
                penalty = self.penalty
            else:
                penalty = self.penalty + 0.1
            penalty += 0.1 * max(0, len(base.address) - 1)
            yield dataclasses.replace(base, penalty=penalty)

    def _get_assignments_address_forward(self, base: TokenAssignment,
                                         query: qmod.QueryStruct) -> Iterator[TokenAssignment]:
        """ Yield possible assignments of address searches with
            left-to-right reading.
        """
        first = base.address[0]

        # The postcode must come after the name.
        if base.postcode and base.postcode < first:
            log().var_dump('skip forward', (base.postcode, first))
            return

        penalty = self.penalty
        if not base.country and self.direction == 1 and query.dir_penalty > 0:
            penalty += query.dir_penalty

        log().comment('first word = name')
        yield dataclasses.replace(base, penalty=penalty,
                                  name=first, address=base.address[1:])

        # To paraphrase:
        #  * if another name term comes after the first one and before the
        #    housenumber
        #  * a qualifier comes after the name
        #  * the containing phrase is strictly typed
        if (base.housenumber and first.end < base.housenumber.start)\
           or (base.qualifier and base.qualifier > first)\
           or (query.nodes[first.start].ptype != qmod.PHRASE_ANY):
            return

        # Penalty for:
        #  * <name>, <street>, <housenumber> , ...
        #  * queries that are comma-separated
        if (base.housenumber and base.housenumber > first) or len(query.source) > 1:
            penalty += 0.25

        if self.direction == 0 and query.dir_penalty > 0:
            penalty += query.dir_penalty

        for i in range(first.start + 1, first.end):
            name, addr = first.split(i)
            log().comment(f'split first word = name ({i - first.start})')
            yield dataclasses.replace(base, name=name, address=[addr] + base.address[1:],
                                      penalty=penalty + query.nodes[i].word_break_penalty)

    def _get_assignments_address_backward(self, base: TokenAssignment,
                                          query: qmod.QueryStruct) -> Iterator[TokenAssignment]:
        """ Yield possible assignments of address searches with
            right-to-left reading.
        """
        last = base.address[-1]

        # The postcode must come before the name for backward direction.
        if base.postcode and base.postcode > last:
            log().var_dump('skip backward', (base.postcode, last))
            return

        penalty = self.penalty
        if not base.country and self.direction == -1 and query.dir_penalty < 0:
            penalty -= query.dir_penalty

        if self.direction == -1 or len(base.address) > 1 or base.postcode:
            log().comment('last word = name')
            yield dataclasses.replace(base, penalty=penalty,
                                      name=last, address=base.address[:-1])

        # To paraphrase:
        #  * if another name term comes before the last one and after the
        #    housenumber
        #  * a qualifier comes before the name
        #  * the containing phrase is strictly typed
        if (base.housenumber and last.start > base.housenumber.end)\
           or (base.qualifier and base.qualifier < last)\
           or (query.nodes[last.start].ptype != qmod.PHRASE_ANY):
            return

        if base.housenumber and base.housenumber < last:
            penalty += 0.4
        if len(query.source) > 1:
            penalty += 0.25

        if self.direction == 0 and query.dir_penalty < 0:
            penalty -= query.dir_penalty

        for i in range(last.start + 1, last.end):
            addr, name = last.split(i)
            log().comment(f'split last word = name ({i - last.start})')
            yield dataclasses.replace(base, name=name, address=base.address[:-1] + [addr],
                                      penalty=penalty + query.nodes[i].word_break_penalty)

    def get_assignments(self, query: qmod.QueryStruct) -> Iterator[TokenAssignment]:
        """ Yield possible assignments for the current sequence.

            This function splits up general name assignments into name
            and address and yields all possible variants of that.
        """
        base = TokenAssignment.from_ranges(self.seq)

        num_addr_tokens = sum(t.end - t.start for t in base.address)
        if num_addr_tokens > 50:
            return

        # Postcode search (postcode-only search is covered in next case)
        if base.postcode is not None and base.address:
            yield from self._get_assignments_postcode(base, query.num_token_slots())

        # Postcode or country-only search
        if not base.address:
            if not base.housenumber and (base.postcode or base.country or base.near_item):
                log().comment('postcode/country search')
                yield dataclasses.replace(base, penalty=self.penalty)
        else:
            # <postcode>,<address> should give preference to postcode search
            if base.postcode and base.postcode.start == 0:
                self.penalty += 0.1

            # Left-to-right reading of the address
            if self.direction != -1:
                yield from self._get_assignments_address_forward(base, query)

            # Right-to-left reading of the address
            if self.direction != 1:
                yield from self._get_assignments_address_backward(base, query)

            # variant for special housenumber searches
            if base.housenumber and not base.qualifier:
                yield dataclasses.replace(base, penalty=self.penalty)


def yield_token_assignments(query: qmod.QueryStruct) -> Iterator[TokenAssignment]:
    """ Return possible word type assignments to word positions.

        The assignments are computed from the concrete tokens listed
        in the tokenized query.

        The result includes the penalty for transitions from one word type to
        another. It does not include penalties for transitions within a
        type.
    """
    todo = [_TokenSequence([], direction=0 if query.source[0].ptype == qmod.PHRASE_ANY else 1)]

    while todo:
        state = todo.pop()
        node = query.nodes[state.end_pos]

        for tlist in node.starting:
            yield from _append_state_to_todo(
                query, todo,
                state.advance(tlist.ttype, tlist.end,
                              True, node.word_break_penalty))

        if node.partial is not None:
            yield from _append_state_to_todo(
                query, todo,
                state.advance(qmod.TOKEN_PARTIAL, state.end_pos + 1,
                              node.btype == qmod.BREAK_PHRASE,
                              node.word_break_penalty))


def _append_state_to_todo(query: qmod.QueryStruct, todo: List[_TokenSequence],
                          newstate: Optional[_TokenSequence]) -> Iterator[TokenAssignment]:
    if newstate is not None:
        if newstate.end_pos == query.num_token_slots():
            if newstate.recheck_sequence():
                log().var_dump('Assignment', newstate)
                yield from newstate.get_assignments(query)
        elif not newstate.is_final():
            todo.append(newstate)
