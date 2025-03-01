# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2024 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Implementation of query analysis for the ICU tokenizer.
"""
from typing import Tuple, Dict, List, Optional, Iterator, Any, cast
from collections import defaultdict
import dataclasses
import difflib
import re
from itertools import zip_longest

from icu import Transliterator

import sqlalchemy as sa

from ..errors import UsageError
from ..typing import SaRow
from ..sql.sqlalchemy_types import Json
from ..connection import SearchConnection
from ..logging import log
from . import query as qmod
from ..query_preprocessing.config import QueryConfig
from .query_analyzer_factory import AbstractQueryAnalyzer


DB_TO_TOKEN_TYPE = {
    'W': qmod.TOKEN_WORD,
    'w': qmod.TOKEN_PARTIAL,
    'H': qmod.TOKEN_HOUSENUMBER,
    'P': qmod.TOKEN_POSTCODE,
    'C': qmod.TOKEN_COUNTRY
}

PENALTY_IN_TOKEN_BREAK = {
     qmod.BREAK_START: 0.5,
     qmod.BREAK_END: 0.5,
     qmod.BREAK_PHRASE: 0.5,
     qmod.BREAK_SOFT_PHRASE: 0.5,
     qmod.BREAK_WORD: 0.1,
     qmod.BREAK_PART: 0.0,
     qmod.BREAK_TOKEN: 0.0
}


@dataclasses.dataclass
class QueryPart:
    """ Normalized and transliterated form of a single term in the query.

        When the term came out of a split during the transliteration,
        the normalized string is the full word before transliteration.
        Check the subsequent break type to figure out if the word is
        continued.

        Penalty is the break penalty for the break following the token.
    """
    token: str
    normalized: str
    penalty: float


QueryParts = List[QueryPart]
WordDict = Dict[str, List[qmod.TokenRange]]


def extract_words(terms: List[QueryPart], start: int,  words: WordDict) -> None:
    """ Add all combinations of words in the terms list after the
        given position to the word list.
    """
    total = len(terms)
    base_penalty = PENALTY_IN_TOKEN_BREAK[qmod.BREAK_WORD]
    for first in range(start, total):
        word = terms[first].token
        penalty = base_penalty
        words[word].append(qmod.TokenRange(first, first + 1, penalty=penalty))
        for last in range(first + 1, min(first + 20, total)):
            word = ' '.join((word, terms[last].token))
            penalty += terms[last - 1].penalty
            words[word].append(qmod.TokenRange(first, last + 1, penalty=penalty))


@dataclasses.dataclass
class ICUToken(qmod.Token):
    """ Specialised token for ICU tokenizer.
    """
    word_token: str
    info: Optional[Dict[str, Any]]

    def get_category(self) -> Tuple[str, str]:
        assert self.info
        return self.info.get('class', ''), self.info.get('type', '')

    def rematch(self, norm: str) -> None:
        """ Check how well the token matches the given normalized string
            and add a penalty, if necessary.
        """
        if not self.lookup_word:
            return

        seq = difflib.SequenceMatcher(a=self.lookup_word, b=norm)
        distance = 0
        for tag, afrom, ato, bfrom, bto in seq.get_opcodes():
            if tag in ('delete', 'insert') and (afrom == 0 or ato == len(self.lookup_word)):
                distance += 1
            elif tag == 'replace':
                distance += max((ato-afrom), (bto-bfrom))
            elif tag != 'equal':
                distance += abs((ato-afrom) - (bto-bfrom))
        self.penalty += (distance/len(self.lookup_word))

    @staticmethod
    def from_db_row(row: SaRow, base_penalty: float = 0.0) -> 'ICUToken':
        """ Create a ICUToken from the row of the word table.
        """
        count = 1 if row.info is None else row.info.get('count', 1)
        addr_count = 1 if row.info is None else row.info.get('addr_count', 1)

        penalty = base_penalty
        if row.type == 'w':
            penalty += 0.3
        elif row.type == 'W':
            if len(row.word_token) == 1 and row.word_token == row.word:
                penalty += 0.2 if row.word.isdigit() else 0.3
        elif row.type == 'H':
            penalty += sum(0.1 for c in row.word_token if c != ' ' and not c.isdigit())
            if all(not c.isdigit() for c in row.word_token):
                penalty += 0.2 * (len(row.word_token) - 1)
        elif row.type == 'C':
            if len(row.word_token) == 1:
                penalty += 0.3

        if row.info is None:
            lookup_word = row.word
        else:
            lookup_word = row.info.get('lookup', row.word)
        if lookup_word:
            lookup_word = lookup_word.split('@', 1)[0]
        else:
            lookup_word = row.word_token

        return ICUToken(penalty=penalty, token=row.word_id, count=max(1, count),
                        lookup_word=lookup_word,
                        word_token=row.word_token, info=row.info,
                        addr_count=max(1, addr_count))


class ICUQueryAnalyzer(AbstractQueryAnalyzer):
    """ Converter for query strings into a tokenized query
        using the tokens created by a ICU tokenizer.
    """
    def __init__(self, conn: SearchConnection) -> None:
        self.conn = conn

    async def setup(self) -> None:
        """ Set up static data structures needed for the analysis.
        """
        async def _make_normalizer() -> Any:
            rules = await self.conn.get_property('tokenizer_import_normalisation')
            return Transliterator.createFromRules("normalization", rules)

        self.normalizer = await self.conn.get_cached_value('ICUTOK', 'normalizer',
                                                           _make_normalizer)

        async def _make_transliterator() -> Any:
            rules = await self.conn.get_property('tokenizer_import_transliteration')
            return Transliterator.createFromRules("transliteration", rules)

        self.transliterator = await self.conn.get_cached_value('ICUTOK', 'transliterator',
                                                               _make_transliterator)

        await self._setup_preprocessing()

        if 'word' not in self.conn.t.meta.tables:
            sa.Table('word', self.conn.t.meta,
                     sa.Column('word_id', sa.Integer),
                     sa.Column('word_token', sa.Text, nullable=False),
                     sa.Column('type', sa.Text, nullable=False),
                     sa.Column('word', sa.Text),
                     sa.Column('info', Json))

    async def _setup_preprocessing(self) -> None:
        """ Load the rules for preprocessing and set up the handlers.
        """

        rules = self.conn.config.load_sub_configuration('icu_tokenizer.yaml',
                                                        config='TOKENIZER_CONFIG')
        preprocessing_rules = rules.get('query-preprocessing', [])

        self.preprocessors = []

        for func in preprocessing_rules:
            if 'step' not in func:
                raise UsageError("Preprocessing rule is missing the 'step' attribute.")
            if not isinstance(func['step'], str):
                raise UsageError("'step' attribute must be a simple string.")

            module = self.conn.config.load_plugin_module(
                        func['step'], 'nominatim_api.query_preprocessing')
            self.preprocessors.append(
                module.create(QueryConfig(func).set_normalizer(self.normalizer)))

    async def analyze_query(self, phrases: List[qmod.Phrase]) -> qmod.QueryStruct:
        """ Analyze the given list of phrases and return the
            tokenized query.
        """
        log().section('Analyze query (using ICU tokenizer)')
        for func in self.preprocessors:
            phrases = func(phrases)
        query = qmod.QueryStruct(phrases)

        log().var_dump('Normalized query', query.source)
        if not query.source:
            return query

        parts, words = self.split_query(query)
        log().var_dump('Transliterated query', lambda: _dump_transliterated(query, parts))

        for row in await self.lookup_in_db(list(words.keys())):
            for trange in words[row.word_token]:
                token = ICUToken.from_db_row(row, trange.penalty or 0.0)
                if row.type == 'S':
                    if row.info['op'] in ('in', 'near'):
                        if trange.start == 0:
                            query.add_token(trange, qmod.TOKEN_NEAR_ITEM, token)
                    else:
                        if trange.start == 0 and trange.end == query.num_token_slots():
                            query.add_token(trange, qmod.TOKEN_NEAR_ITEM, token)
                        else:
                            query.add_token(trange, qmod.TOKEN_QUALIFIER, token)
                else:
                    query.add_token(trange, DB_TO_TOKEN_TYPE[row.type], token)

        self.add_extra_tokens(query, parts)
        self.rerank_tokens(query, parts)

        log().table_dump('Word tokens', _dump_word_tokens(query))

        return query

    def normalize_text(self, text: str) -> str:
        """ Bring the given text into a normalized form. That is the
            standardized form search will work with. All information removed
            at this stage is inevitably lost.
        """
        return cast(str, self.normalizer.transliterate(text)).strip('-: ')

    def split_query(self, query: qmod.QueryStruct) -> Tuple[QueryParts, WordDict]:
        """ Transliterate the phrases and split them into tokens.

            Returns the list of transliterated tokens together with their
            normalized form and a dictionary of words for lookup together
            with their position.
        """
        parts: QueryParts = []
        phrase_start = 0
        words: WordDict = defaultdict(list)
        for phrase in query.source:
            query.nodes[-1].ptype = phrase.ptype
            phrase_split = re.split('([ :-])', phrase.text)
            # The zip construct will give us the pairs of word/break from
            # the regular expression split. As the split array ends on the
            # final word, we simply use the fillvalue to even out the list and
            # add the phrase break at the end.
            for word, breakchar in zip_longest(*[iter(phrase_split)]*2, fillvalue=','):
                if not word:
                    continue
                trans = self.transliterator.transliterate(word)
                if trans:
                    for term in trans.split(' '):
                        if term:
                            parts.append(QueryPart(term, word,
                                                   PENALTY_IN_TOKEN_BREAK[qmod.BREAK_TOKEN]))
                            query.add_node(qmod.BREAK_TOKEN, phrase.ptype)
                    query.nodes[-1].btype = breakchar
                    parts[-1].penalty = PENALTY_IN_TOKEN_BREAK[breakchar]

            extract_words(parts, phrase_start, words)

            phrase_start = len(parts)
        query.nodes[-1].btype = qmod.BREAK_END

        return parts, words

    async def lookup_in_db(self, words: List[str]) -> 'sa.Result[Any]':
        """ Return the token information from the database for the
            given word tokens.
        """
        t = self.conn.t.meta.tables['word']
        return await self.conn.execute(t.select().where(t.c.word_token.in_(words)))

    def add_extra_tokens(self, query: qmod.QueryStruct, parts: QueryParts) -> None:
        """ Add tokens to query that are not saved in the database.
        """
        for part, node, i in zip(parts, query.nodes, range(1000)):
            if len(part.token) <= 4 and part.token.isdigit()\
               and not node.has_tokens(i+1, qmod.TOKEN_HOUSENUMBER):
                query.add_token(qmod.TokenRange(i, i+1), qmod.TOKEN_HOUSENUMBER,
                                ICUToken(penalty=0.5, token=0,
                                         count=1, addr_count=1, lookup_word=part.token,
                                         word_token=part.token, info=None))

    def rerank_tokens(self, query: qmod.QueryStruct, parts: QueryParts) -> None:
        """ Add penalties to tokens that depend on presence of other token.
        """
        for i, node, tlist in query.iter_token_lists():
            if tlist.ttype == qmod.TOKEN_POSTCODE:
                for repl in node.starting:
                    if repl.end == tlist.end and repl.ttype != qmod.TOKEN_POSTCODE \
                       and (repl.ttype != qmod.TOKEN_HOUSENUMBER
                            or len(tlist.tokens[0].lookup_word) > 4):
                        repl.add_penalty(0.39)
            elif (tlist.ttype == qmod.TOKEN_HOUSENUMBER
                  and len(tlist.tokens[0].lookup_word) <= 3):
                if any(c.isdigit() for c in tlist.tokens[0].lookup_word):
                    for repl in node.starting:
                        if repl.end == tlist.end and repl.ttype != qmod.TOKEN_HOUSENUMBER:
                            repl.add_penalty(0.5 - tlist.tokens[0].penalty)
            elif tlist.ttype not in (qmod.TOKEN_COUNTRY, qmod.TOKEN_PARTIAL):
                norm = parts[i].normalized
                for j in range(i + 1, tlist.end):
                    if node.btype != qmod.BREAK_TOKEN:
                        norm += '  ' + parts[j].normalized
                for token in tlist.tokens:
                    cast(ICUToken, token).rematch(norm)


def _dump_transliterated(query: qmod.QueryStruct, parts: QueryParts) -> str:
    out = query.nodes[0].btype
    for node, part in zip(query.nodes[1:], parts):
        out += part.token + node.btype
    return out


def _dump_word_tokens(query: qmod.QueryStruct) -> Iterator[List[Any]]:
    yield ['type', 'token', 'word_token', 'lookup_word', 'penalty', 'count', 'info']
    for node in query.nodes:
        for tlist in node.starting:
            for token in tlist.tokens:
                t = cast(ICUToken, token)
                yield [tlist.ttype, t.token, t.word_token or '',
                       t.lookup_word or '', t.penalty, t.count, t.info]


async def create_query_analyzer(conn: SearchConnection) -> AbstractQueryAnalyzer:
    """ Create and set up a new query analyzer for a database based
        on the ICU tokenizer.
    """
    out = ICUQueryAnalyzer(conn)
    await out.setup()

    return out
