"""Marking the terms of a search query inside the rows that matched them (issue #45).

Both halves of this operation are attacker-controlled. The needle is typed by
whoever is searching, and the haystack is backend data: a family name is chosen by
whoever submits or renames a family, and a sample filename is chosen by whoever
built the malware. So the one rule this module follows is that **it never produces
markup**. `split_search_matches` returns a list of `(chunk, is_match)` pairs of
plain strings, and the `mark()` macro in `templates/table/links.html` writes the
`<mark>` element itself around `{{ chunk }}`. Jinja's autoescaping therefore still
covers every character that came from a query or from the backend, and no `|safe`
and no `Markup` is needed anywhere - which is the point, because either of those is
how a highlighting feature turns into a cross-site scripting hole. See AGENTS.md,
"Autoescaping is your safety net".

Nothing here builds a regular expression out of user input either: matching is
`str.find` in a loop, so a search term cannot become a pathological pattern.

Which term may be marked in which column is decided by mcrit's own query parser, so
the marks agree with what the backend actually matched rather than with a second,
divergent idea of the query syntax. A bare term is a case-insensitive substring
search across the fields in SEARCH_FIELDS; `field:value` applies to that one field;
negated terms and the range operators are not marked at all.
"""

import logging

#: Marking is cosmetic, so its failures are swallowed rather than raised at a reader
#: who only wanted to search. They are logged here so that "why is nothing marked"
#: has an answer other than reading this file. Deliberately the stdlib logger and not
#: `current_app.logger`: this module imports no Flask, which is what keeps it a pure
#: helper that a test can exercise without an application context.
LOG = logging.getLogger(__name__)

#: The fields a bare search term is matched against, which is the union of the
#: `search_fields` of mcrit's MongoDbStorage.findFamilyByString (family_name),
#: .findSampleByString (filename, family, component, version, and sha256 once the
#: term is at least three characters) and .findFunctionByString (function_name).
#: `component` has no column in any table today, so a mark for it never renders;
#: the list mirrors the backend rather than the templates on purpose.
#: The three-character condition on sha256 is deliberately *not* reproduced. It decides
#: what the backend matched, and a mark makes the weaker claim that the term occurs in
#: the text shown - which stays true either way, and the row is on the page regardless.
SEARCH_FIELDS = ("family_name", "filename", "family", "component", "version", "sha256", "function_name")

#: Operators whose value occurs literally inside the field it matched, mapped to
#: whether the value has to be the *whole* field. "?" is the case-insensitive
#: substring search; "" and "=" are equality, and an equality term must only be
#: marked when the field equals it - a row can be on the page because some other
#: half of an OR matched, and marking a substring of a field that was never equal
#: would claim a match the backend did not make. Deliberately absent: the range
#: operators, whose value is not a substring of anything, and the negations
#: ("!=", "!?"), which are satisfied by the value being *missing*.
MARKABLE_OPERATORS = {"": True, "=": True, "?": False}

#: Both built on first use, and both imported there rather than at module scope so
#: that `import mcritweb` stays cheap - the same reason create_app defers its own
#: imports. See issue #88. Constructing the parser compiles a pyparsing grammar,
#: which is not work to redo per request, and its `parse` memoizes on the instance.
_PARSER = None
_NODE_TYPES = None


def _get_parser():
    global _PARSER
    if _PARSER is None:
        from mcrit.index.SearchQueryParser import SearchQueryParser
        _PARSER = SearchQueryParser()
    return _PARSER


def _get_node_types():
    """(NotNode, the two list node types, SearchTermNode, SearchConditionNode)."""
    global _NODE_TYPES
    if _NODE_TYPES is None:
        from mcrit.index.SearchQueryTree import (
            AndNode,
            NotNode,
            OrNode,
            SearchConditionNode,
            SearchTermNode,
        )
        _NODE_TYPES = (NotNode, (AndNode, OrNode), SearchTermNode, SearchConditionNode)
    return _NODE_TYPES


def _add_term(terms_by_field, field, value, is_exact=False):
    """Record `value` as markable in `field`, keeping order and dropping duplicates.

    `is_exact` carries the operator through to the matcher: an equality term marks
    the field only when the field *is* that value.
    """
    if not isinstance(value, str) or not value.strip():
        # an empty needle is in every string at every position: it would mark the
        # whole table, and a find() loop over it would not advance
        return
    values = terms_by_field.setdefault(field, [])
    term = (value, bool(is_exact))
    if term not in values:
        values.append(term)


def _collect_terms(node, is_negated, terms_by_field):
    NotNode, list_nodes, SearchTermNode, SearchConditionNode = _get_node_types()
    if isinstance(node, NotNode):
        _collect_terms(node.child, not is_negated, terms_by_field)
    elif isinstance(node, list_nodes):
        for child in node.children:
            _collect_terms(child, is_negated, terms_by_field)
    elif isinstance(node, SearchTermNode):
        if not is_negated:
            for field in SEARCH_FIELDS:
                _add_term(terms_by_field, field, node.value)
    elif isinstance(node, SearchConditionNode):
        if not is_negated and node.operator in MARKABLE_OPERATORS:
            _add_term(terms_by_field, node.field, node.value, MARKABLE_OPERATORS[node.operator])


def get_highlight_terms(query):
    """Field name -> the terms of `query` that may be marked in that field.

    Registered as the `search_terms` template filter. An empty result means "mark
    nothing", which is what an empty, absent or unparseable query yields.
    """
    terms_by_field = {}
    if not isinstance(query, str) or not query.strip():
        return terms_by_field
    # a plain str, for the same reason split_search_matches coerces its text: pyparsing
    # slices the string it was given, and a str subclass propagates through the slices
    query = str(query)
    try:
        tree = _get_parser().parse(query)
    except Exception as error:
        # An unparseable query is one the backend rejects too, so the page is already
        # showing a search failure; marking is cosmetic and simply drops out rather
        # than turning a bad query into a 500. Broad on purpose: pyparsing answers a
        # long enough conjunction with a RecursionError rather than a ParseException.
        LOG.debug("not marking the search term, the query did not parse: %s", error)
        return terms_by_field
    _collect_terms(tree, False, terms_by_field)
    return {field: tuple(values) for field, values in terms_by_field.items()}


def _fold_case(text):
    """Lowercase `text` without changing its length.

    `str.lower()` is not length-preserving for every code point - "İ".lower() is
    two characters - and an index found in a shifted copy would then cut the original
    string in the wrong place, marking a substring other than the one that matched.
    Characters that cannot be lowered in place keep their original form, so they only
    ever fail to match; they never mismatch.
    """
    folded = []
    for character in text:
        lowered = character.lower()
        folded.append(lowered if len(lowered) == 1 else character)
    return "".join(folded)


def _match_spans(text, terms):
    """The (begin, end) ranges of `text` covered by any of `terms`, merged.

    A term is a `(value, is_exact)` pair. A bare string is accepted as a substring
    term as well, so a template passing its own list of words still works.
    """
    folded_text = _fold_case(text)
    spans = []
    for term in terms:
        # `split_search_matches` is a template global, so `terms` is whatever a template
        # passed; anything of another shape is skipped rather than raised over
        if isinstance(term, str):
            value, is_exact = term, False
        elif isinstance(term, (tuple, list)) and len(term) == 2 and isinstance(term[0], str):
            value, is_exact = term[0], bool(term[1])
        else:
            continue
        needle = _fold_case(value)
        if not needle:
            continue
        if is_exact:
            # an equality condition is about the whole field, not a part of it
            if folded_text == needle:
                spans.append((0, len(text)))
            continue
        start = 0
        while True:
            begin = folded_text.find(needle, start)
            if begin < 0:
                break
            spans.append((begin, begin + len(needle)))
            # advance by one rather than by the length of the needle: "aa" occurs
            # twice in "aaa", and the two hits are merged into one span below
            start = begin + 1
    spans.sort()
    merged = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def split_search_matches(text, terms_by_field=None, field=None):
    """Split `text` into consecutive `(chunk, is_match)` pairs of plain strings.

    Registered as the `split_search_matches` template global; the `mark()` macro in
    `templates/table/links.html` is the only caller. Concatenating the chunks always
    reproduces `text` exactly - the marks are the only thing this decides.

    `terms_by_field` is what `get_highlight_terms` returned, and anything else
    (None, an Undefined from a template that passes no query) means "mark nothing".
    """
    if text is None:
        # a None name would otherwise render as the literal "None"
        text = ""
    else:
        # str() unconditionally, not `if not isinstance(text, str)`. A `Markup` *is* a
        # str, and slicing one yields more `Markup` - so a value that had been through
        # |safe upstream would come back out of here still marked safe, and the chunks
        # the template renders would skip autoescaping. `str()` on any str subclass
        # returns a plain str (and the identical object for an exact str, so this costs
        # nothing in the normal case). Nothing passes Markup here today; the point is
        # that the chunks are plain strings no matter what arrives.
        text = str(text)
    terms = terms_by_field.get(field) if isinstance(terms_by_field, dict) else None
    if not text or not terms:
        return [(text, False)]
    spans = _match_spans(text, terms)
    if not spans:
        return [(text, False)]
    segments = []
    position = 0
    for begin, end in spans:
        if begin > position:
            segments.append((text[position:begin], False))
        segments.append((text[begin:end], True))
        position = end
    if position < len(text):
        segments.append((text[position:], False))
    return segments
