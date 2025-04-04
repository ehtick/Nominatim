# Tokenizers

The tokenizer module in Nominatim is responsible for analysing the names given
to OSM objects and the terms of an incoming query in order to make sure, they
can be matched appropriately.

Nominatim currently offers only one tokenizer module, the ICU tokenizer. This section
describes the tokenizer and how it can be configured.

!!! important
    The selection of tokenizer is tied to a database installation. You need to choose
    and configure the tokenizer before starting the initial import. Once the import
    is done, you cannot switch to another tokenizer anymore. Reconfiguring the
    chosen tokenizer is very limited as well. See the comments in each tokenizer
    section.

## ICU tokenizer

The ICU tokenizer uses the [ICU library](http://site.icu-project.org/) to
normalize names and queries. It also offers configurable decomposition and
abbreviation handling.
This tokenizer is currently the default.

To enable the tokenizer add the following line to your project configuration:

```
NOMINATIM_TOKENIZER=icu
```

### How it works

On import the tokenizer processes names in the following three stages:

1. During the **Sanitizer step** incoming names are cleaned up and converted to
   **full names**. This step can be used to regularize spelling, split multi-name
   tags into their parts and tag names with additional attributes. See the
   [Sanitizers section](#sanitizers) below for available cleaning routines.
2. The **Normalization** part removes all information from the full names
   that are not relevant for search.
3. The **Token analysis** step takes the normalized full names and creates
   all transliterated variants under which the name should be searchable.
   See the [Token analysis](#token-analysis) section below for more
   information.

During query time, the tokeinzer is responsible for processing incoming
queries. This happens in two stages:

1. During **query preprocessing** the incoming text is split into name
   chunks and normalised. This usually means applying the same normalisation
   as during the import process but may involve other processing like,
   for example, word break detection.
2. The **token analysis** step breaks down the query parts into tokens,
   looks them up in the database and assigns them possible functions and
   probabilities.

Query processing can be further customized while the rest of the analysis
is hard-coded.

### Configuration

The ICU tokenizer is configured using a YAML file which can be configured using
`NOMINATIM_TOKENIZER_CONFIG`. The configuration is read on import and then
saved as part of the internal database status. Later changes to the variable
have no effect.

Here is an example configuration file:

``` yaml
query-preprocessing:
    - step: split_japanese_phrases
    - step: regex_replace
      replacements:
        - pattern: https?://[^\s]* # Filter URLs starting with http or https
          replace: ''
    - step: normalize

normalization:
    - ":: lower ()"
    - "ß > 'ss'" # German szet is unambiguously equal to double ss
transliteration:
    - !include /etc/nominatim/icu-rules/extended-unicode-to-asccii.yaml
    - ":: Ascii ()"
sanitizers:
    - step: split-name-list
token-analysis:
    - analyzer: generic
      variants:
          - !include icu-rules/variants-ca.yaml
          - words:
              - road -> rd
              - bridge -> bdge,br,brdg,bri,brg
      mutations:
          - pattern: 'ä'
            replacements: ['ä', 'ae']
```

The configuration file contains five sections:
`query-preprocessing`, `normalization`, `transliteration`, `sanitizers` and `token-analysis`.

#### Query preprocessing

The section for `query-preprocessing` defines an ordered list of functions
that are applied to the query before the token analysis.

The following is a list of preprocessors that are shipped with Nominatim.

##### normalize

::: nominatim_api.query_preprocessing.normalize
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### regex-replace

::: nominatim_api.query_preprocessing.regex_replace
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy
    description: 
        This option runs any given regex pattern on the input and replaces values accordingly
    replacements:
        - pattern: regex pattern
          replace: string to replace with


#### Normalization and Transliteration

The normalization and transliteration sections each define a set of
ICU rules that are applied to the names.

The **normalization** rules are applied after sanitation. They should remove
any information that is not relevant for search at all. Usual rules to be
applied here are: lower-casing, removing of special characters, cleanup of
spaces.

The **transliteration** rules are applied at the end of the tokenization
process to transfer the name into an ASCII representation. Transliteration can
be useful to allow for further fuzzy matching, especially between different
scripts.

Each section must contain a list of
[ICU transformation rules](https://unicode-org.github.io/icu/userguide/transforms/general/rules.html).
The rules are applied in the order in which they appear in the file.
You can also include additional rules from external yaml file using the
`!include` tag. The included file must contain a valid YAML list of ICU rules
and may again include other files.

!!! warning
    The ICU rule syntax contains special characters that conflict with the
    YAML syntax. You should therefore always enclose the ICU rules in
    double-quotes.

#### Sanitizers

The sanitizers section defines an ordered list of functions that are applied
to the name and address tags before they are further processed by the tokenizer.
They allows to clean up the tagging and bring it to a standardized form more
suitable for building the search index.

!!! hint
    Sanitizers only have an effect on how the search index is built. They
    do not change the information about each place that is saved in the
    database. In particular, they have no influence on how the results are
    displayed. The returned results always show the original information as
    stored in the OpenStreetMap database.

Each entry contains information of a sanitizer to be applied. It has a
mandatory parameter `step` which gives the name of the sanitizer. Depending
on the type, it may have additional parameters to configure its operation.

The order of the list matters. The sanitizers are applied exactly in the order
that is configured. Each sanitizer works on the results of the previous one.

The following is a list of sanitizers that are shipped with Nominatim.

##### split-name-list

::: nominatim_db.tokenizer.sanitizers.split_name_list
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### strip-brace-terms

::: nominatim_db.tokenizer.sanitizers.strip_brace_terms
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### tag-analyzer-by-language

::: nominatim_db.tokenizer.sanitizers.tag_analyzer_by_language
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### clean-housenumbers

::: nominatim_db.tokenizer.sanitizers.clean_housenumbers
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### clean-postcodes

::: nominatim_db.tokenizer.sanitizers.clean_postcodes
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

##### clean-tiger-tags

::: nominatim_db.tokenizer.sanitizers.clean_tiger_tags
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

#### delete-tags

::: nominatim_db.tokenizer.sanitizers.delete_tags
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

#### tag-japanese

::: nominatim_db.tokenizer.sanitizers.tag_japanese
    options:
        members: False
        heading_level: 6
        docstring_section_style: spacy

#### Token Analysis

Token analyzers take a full name and transform it into one or more normalized
form that are then saved in the search index. In its simplest form, the
analyzer only applies the transliteration rules. More complex analyzers
create additional spelling variants of a name. This is useful to handle
decomposition and abbreviation.

The ICU tokenizer may use different analyzers for different names. To select
the analyzer to be used, the name must be tagged with the `analyzer` attribute
by a sanitizer (see for example the
[tag-analyzer-by-language sanitizer](#tag-analyzer-by-language)).

The token-analysis section contains the list of configured analyzers. Each
analyzer must have an `id` parameter that uniquely identifies the analyzer.
The only exception is the default analyzer that is used when no special
analyzer was selected. There are analysers with special ids:

 * '@housenumber'. If an analyzer with that name is present, it is used
   for normalization of house numbers.
 * '@potcode'. If an analyzer with that name is present, it is used
   for normalization of postcodes.

Different analyzer implementations may exist. To select the implementation,
the `analyzer` parameter must be set. The different implementations are
described in the following.

##### Generic token analyzer

The generic analyzer `generic` is able to create variants from a list of given
abbreviation and decomposition replacements and introduce spelling variations.

###### Variants

The optional 'variants' section defines lists of replacements which create alternative
spellings of a name. To create the variants, a name is scanned from left to
right and the longest matching replacement is applied until the end of the
string is reached.

The variants section must contain a list of replacement groups. Each group
defines a set of properties that describes where the replacements are
applicable. In addition, the word section defines the list of replacements
to be made. The basic replacement description is of the form:

```
<source>[,<source>[...]] => <target>[,<target>[...]]
```

The left side contains one or more `source` terms to be replaced. The right side
lists one or more replacements. Each source is replaced with each replacement
term.

!!! tip
    The source and target terms are internally normalized using the
    normalization rules given in the configuration. This ensures that the
    strings match as expected. In fact, it is better to use unnormalized
    words in the configuration because then it is possible to change the
    rules for normalization later without having to adapt the variant rules.

###### Decomposition

In its standard form, only full words match against the source. There
is a special notation to match the prefix and suffix of a word:

``` yaml
- ~strasse => str  # matches "strasse" as full word and in suffix position
- hinter~ => hntr  # matches "hinter" as full word and in prefix position
```

There is no facility to match a string in the middle of the word. The suffix
and prefix notation automatically trigger the decomposition mode: two variants
are created for each replacement, one with the replacement attached to the word
and one separate. So in above example, the tokenization of "hauptstrasse" will
create the variants "hauptstr" and "haupt str". Similarly, the name "rote strasse"
triggers the variants "rote str" and "rotestr". By having decomposition work
both ways, it is sufficient to create the variants at index time. The variant
rules are not applied at query time.

To avoid automatic decomposition, use the '|' notation:

``` yaml
- ~strasse |=> str
```

simply changes "hauptstrasse" to "hauptstr" and "rote strasse" to "rote str".

###### Initial and final terms

It is also possible to restrict replacements to the beginning and end of a
name:

``` yaml
- ^south => s  # matches only at the beginning of the name
- road$ => rd  # matches only at the end of the name
```

So the first example would trigger a replacement for "south 45th street" but
not for "the south beach restaurant".

###### Replacements vs. variants

The replacement syntax `source => target` works as a pure replacement. It changes
the name instead of creating a variant. To create an additional version, you'd
have to write `source => source,target`. As this is a frequent case, there is
a shortcut notation for it:

```
<source>[,<source>[...]] -> <target>[,<target>[...]]
```

The simple arrow causes an additional variant to be added. Note that
decomposition has an effect here on the source as well. So a rule

``` yaml
- "~strasse -> str"
```

means that for a word like `hauptstrasse` four variants are created:
`hauptstrasse`, `haupt strasse`, `hauptstr` and `haupt str`.

###### Mutations

The 'mutation' section in the configuration describes an additional set of
replacements to be applied after the variants have been computed.

Each mutation is described by two parameters: `pattern` and `replacements`.
The pattern must contain a single regular expression to search for in the
variant name. The regular expressions need to follow the syntax for
[Python regular expressions](file:///usr/share/doc/python3-doc/html/library/re.html#regular-expression-syntax).
Capturing groups are not permitted.
`replacements` must contain a list of strings that the pattern
should be replaced with. Each occurrence of the pattern is replaced with
all given replacements. Be mindful of combinatorial explosion of variants.

###### Modes

The generic analyser supports a special mode `variant-only`. When configured
then it consumes the input token and emits only variants (if any exist). Enable
the mode by adding:

```
  mode: variant-only
```

to the analyser configuration.

##### Housenumber token analyzer

The analyzer `housenumbers` is purpose-made to analyze house numbers. It
creates variants with optional spaces between numbers and letters. Thus,
house numbers of the form '3 a', '3A', '3-A' etc. are all considered equivalent.

The analyzer cannot be customized.

##### Postcode token analyzer

The analyzer `postcodes` is pupose-made to analyze postcodes. It supports
a 'lookup' variant of the token, which produces variants with optional
spaces. Use together with the clean-postcodes sanitizer.

The analyzer cannot be customized.

### Reconfiguration

Changing the configuration after the import is currently not possible, although
this feature may be added at a later time.
