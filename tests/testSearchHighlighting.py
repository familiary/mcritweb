#!/usr/bin/python
"""Marking the search term in the rows it matched (issue #45).

Highlighting a search term inside rendered output is the textbook way to introduce
cross-site scripting, and this application is a worse-than-usual place to get it
wrong: *both* halves are attacker-controlled. The needle is typed by whoever is
searching, and the haystack is backend data - a family name is chosen by whoever
submits or renames a family, and a sample filename is chosen by whoever built the
malware. An implementation that assembles `<mark>` around a name in Python and hands
the result to `|safe` renders every one of those payloads as live markup.

So the design avoids the question rather than answering it. `split_search_matches`
returns (chunk, is_match) pairs of plain strings and no markup at all; the `mark()`
macro in `templates/table/links.html` writes the `<mark>` element around an ordinary
autoescaped `{{ chunk }}`. Nothing is ever marked safe, which is what the render
tests at the bottom of this file are here to keep true - each one puts a payload in
*both* the query and the family or sample name, so a regression on either side shows
up as executable markup in the response.

The matching itself uses `str.find`, never a regular expression built from the query,
so a search term cannot become a pathological pattern; and which term is markable in
which column comes from mcrit's own query parser, so the marks agree with what the
backend actually matched rather than with a second idea of the query syntax.

The unit tests come first because the render tests can only reach cases the offline
corpus can produce rows for: `tests/fixtureData.py` matches a query as one literal
substring, so a `field:value` query returns nothing there and field scoping, negation
and the operators have to be pinned down directly on the helper.
"""

import logging
import re
import unittest

import pytest
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.SampleEntry import SampleEntry

from mcritweb.search_highlighting import (
    SEARCH_FIELDS,
    get_highlight_terms,
    split_search_matches,
)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: Terminates an attribute, an element and a text node, then opens a script.
SCRIPT_PAYLOAD = "<script>alert(1)</script>"

#: The other classic: no script element at all, so a filter that only looks for one
#: lets it through. `onerror` fires without any user interaction.
IMG_PAYLOAD = '"><img src=x onerror=alert(1)>'

#: (payload, the fragments whose presence would prove it rendered as *markup* rather
#: than as text). Only fragments no template contains of its own are listed: a bare
#: `<img` would fail on the three `base.html` renders legitimately, and a test that
#: fails on the page logo teaches the next reader to relax it. Nor is a bare `onerror`
#: enough - escaped into text content the payload still reads `onerror=alert(1)&gt;`,
#: and that is inert. What matters is whether the `<` survived.
PAYLOADS = [
    pytest.param(SCRIPT_PAYLOAD, ["<script>alert"], id="script"),
    pytest.param(IMG_PAYLOAD, ["<img src=x", '"><img'], id="img-onerror"),
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The row-marking tests need rows, so serve the captured corpus."""
    return corpus_mcrit


def marks(response):
    """The text inside every <mark> element of a response, in order."""
    return re.findall(r"<mark>(.*?)</mark>", response.get_data(as_text=True), re.DOTALL)


# --- which terms may be marked where ---------------------------------------------

def test_a_bare_term_is_markable_in_every_searched_field():
    """mcrit resolves a bare term to a substring condition on each of the fields in
    MongoDbStorage's `search_fields`, so the mark may appear in any of those columns."""
    assert get_highlight_terms("zeus") == {field: (("zeus", False),) for field in SEARCH_FIELDS}


def test_a_field_scoped_term_is_markable_only_in_that_field():
    """The reason this module parses at all. With one flat list of terms instead,
    `family_id:5` would put a mark on every "5" in every filename on the page."""
    assert get_highlight_terms("filename:foo") == {"filename": (("foo", True),)}


def test_an_equality_condition_is_only_marked_on_an_equal_field():
    """`=` means the field *is* the value, and a row can be on the page because some
    other half of an OR matched. Marking a substring of a field that was never equal
    would claim a hit the backend did not make.

    Found by Codex review: the operator used to be discarded after deciding the term
    was markable, and every retained term was then matched as a substring."""
    terms = get_highlight_terms("family:=zeus OR filename:foo")

    # the family half did not match this row; only its filename half did
    assert split_search_matches("win.vmzeus", terms, "family") == [("win.vmzeus", False)]
    # and a family that really is "zeus" is still marked
    assert split_search_matches("zeus", terms, "family") == [("zeus", True)]


def test_a_substring_operator_still_marks_a_substring():
    assert get_highlight_terms("family:?cita") == {"family": (("cita", False),)}


def test_a_negated_term_is_not_markable():
    """`NOT zeus` returns the rows where "zeus" does *not* occur. Marking it would
    promise a hit that by construction is not there."""
    assert get_highlight_terms("NOT zeus") == {}
    assert get_highlight_terms("family:!?zeus") == {}


def test_a_range_operator_is_not_markable():
    """The value of `num_functions:>5` is not a substring of the field it filtered."""
    assert get_highlight_terms("num_functions:>5") == {}


def test_a_term_under_a_negated_parenthesis_is_not_markable():
    assert get_highlight_terms("NOT (zeus OR citadel)") == {}


def test_terms_of_a_conjunction_are_all_markable():
    assert get_highlight_terms("filename:foo version:1.2")["filename"] == (("foo", True),)
    assert get_highlight_terms("filename:foo version:1.2")["version"] == (("1.2", True),)


def test_a_repeated_term_is_recorded_once():
    assert get_highlight_terms("zeus zeus")["family_name"] == (("zeus", False),)


@pytest.mark.parametrize("query", ["", "   ", None, 0, [], "''", '""'])
def test_an_empty_query_marks_nothing(query):
    """An empty needle occurs in every string at every position: it would mark whole
    tables, and the find() loop over it would not advance. `''` and `""` are the
    parser's way of producing one, and the None/0/[] cases are what a template hands
    over when the page has no query at all."""
    assert get_highlight_terms(query) == {}


@pytest.mark.parametrize("query", ["((", "a AND", "a " * 300])
def test_an_unparseable_query_marks_nothing_instead_of_raising(query):
    """Such a query fails in the backend too, so the page is already showing a search
    failure; marking is cosmetic and has no business turning that into a 500. The
    third case is not a parse error but a RecursionError out of pyparsing, which is
    why the guard catches Exception rather than ValueError."""
    assert get_highlight_terms(query) == {}


# --- splitting a value into marked and unmarked chunks ---------------------------

def rejoin(segments):
    return "".join(chunk for chunk, _ in segments)


def test_text_without_a_hit_is_one_unmarked_chunk():
    assert split_search_matches("win.citadel", {"family_name": ("zeus",)}, "family_name") == [("win.citadel", False)]


def test_a_hit_is_split_out_as_its_own_chunk():
    assert split_search_matches("win.vmzeus", {"family_name": ("zeus",)}, "family_name") == [
        ("win.vm", False),
        ("zeus", True),
    ]


def test_matching_ignores_case_but_the_original_spelling_is_kept():
    """mcrit's substring search is case-insensitive (`re.IGNORECASE`), so the mark has
    to be too - but the cell must still read as the backend spelled it."""
    segments = split_search_matches("Win.VMZeus", {"family_name": ("zeus",)}, "family_name")

    assert segments == [("Win.VM", False), ("Zeus", True)]


def test_overlapping_occurrences_of_one_term_become_one_mark():
    """"aa" occurs at offsets 0, 1 and 2 of "aaaa". Advancing by the length of the
    needle would miss the middle one; emitting all three unmerged would nest or
    duplicate the markup."""
    assert split_search_matches("aaaa", {"x": ("aa",)}, "x") == [("aaaa", True)]


def test_two_terms_overlapping_become_one_mark():
    assert split_search_matches("abcd", {"x": ("abc", "bcd")}, "x") == [("abcd", True)]


def test_adjacent_hits_do_not_produce_empty_chunks():
    segments = split_search_matches("abab", {"x": ("ab",)}, "x")

    assert all(chunk for chunk, _ in segments), f"empty chunk in {segments}"
    assert segments == [("abab", True)]


def test_a_hit_in_the_middle_keeps_both_sides():
    assert split_search_matches("xxzeusyy", {"x": ("zeus",)}, "x") == [
        ("xx", False),
        ("zeus", True),
        ("yy", False),
    ]


@pytest.mark.parametrize(
    "text, terms",
    [
        ("win.vmzeus", ("zeus",)),
        ("aaaa", ("aa",)),
        ("abcd", ("abc", "bcd")),
        ("Ünïcödé-Zeus.exe", ("zeus", "ö")),
        ("İstanbul", ("i", "stan")),
        ("straße", ("ss", "ß")),
        ("no hit at all", ("zeus",)),
        ("", ("zeus",)),
    ],
)
def test_the_chunks_always_rejoin_into_the_original_text(text, terms):
    """The invariant that makes the whole thing safe to reason about: marking decides
    *where* the boundaries go and nothing else, so no character can be dropped,
    duplicated or transliterated on the way to the page.

    The unicode cases are the ones that can break it. `str.lower()` is not
    length-preserving for every code point - "İ".lower() is two characters - so folding
    the whole string and then slicing the original at an index found in the folded copy
    would cut in the wrong place. The fold here is per character and keeps anything that
    does not lower into a single character."""
    assert rejoin(split_search_matches(text, {"x": terms}, "x")) == text


def test_a_multi_character_lowercase_form_does_not_shift_the_marks():
    """"İ" lowercases to two code points. If that shifted the indices, the mark would
    land one character to the left of "stan"."""
    segments = split_search_matches("İstanbul", {"x": ("stan",)}, "x")

    assert segments == [("İ", False), ("stan", True), ("bul", False)]


def test_no_terms_for_this_field_leaves_the_text_alone():
    """A `filename:` query must not mark anything in the version column."""
    assert split_search_matches("1.3.5.1", {"filename": ("1.3",)}, "version") == [("1.3.5.1", False)]


@pytest.mark.parametrize("terms_by_field", [None, {}, "not a mapping", {"x": ()}, {"x": ("",)}])
def test_absent_or_unusable_terms_mark_nothing(terms_by_field):
    """Templates that render a row outside a search page pass no terms at all, and the
    empty-string term is the guard against a needle that matches everywhere."""
    assert split_search_matches("win.vmzeus", terms_by_field, "x") == [("win.vmzeus", False)]


def test_a_value_that_was_already_marked_safe_comes_back_unsafe():
    """The trap in the type system. `Markup` subclasses `str`, and slicing a `Markup`
    yields more `Markup` - so an `isinstance(text, str)` guard would let a value that
    had been through `|safe` upstream out of here still marked safe, and the template
    would render those chunks without escaping them. Nothing hands this helper a Markup
    today; the invariant is that the chunks are plain strings whatever arrives."""
    from markupsafe import Markup

    segments = split_search_matches(Markup("<b>zeus</b>"), {"x": ("zeus",)}, "x")

    assert all(type(chunk) is str for chunk, _ in segments), f"a chunk kept its Markup type: {segments}"
    assert rejoin(segments) == "<b>zeus</b>"


def test_a_none_value_renders_as_empty_rather_than_the_word_none():
    """`function_name` is Optional[str] in FunctionEntry, and `{{ none }}` renders as
    the literal "None" in Jinja. Passing through this helper is the cell's chance to
    stop doing that."""
    assert split_search_matches(None, {"x": ("zeus",)}, "x") == [("", False)]


# --- the marks reach the pages ---------------------------------------------------

def test_the_family_listing_marks_the_matched_name(client, as_role):
    as_role("visitor")
    response = client.get("/explore/families?query=zeus")

    assert response.status_code == 200
    assert marks(response) == ["zeus"], "the matched part of win.vmzeus was not marked"


def test_the_mark_on_a_page_is_case_insensitive(client, as_role):
    as_role("visitor")
    assert marks(client.get("/explore/families?query=ZEUS")) == ["zeus"]


def test_the_sample_listing_marks_the_family_column(client, as_role):
    as_role("visitor")
    marked = marks(client.get("/explore/samples?query=vmzeus"))

    assert marked and set(marked) == {"vmzeus"}


def test_the_sample_listing_marks_the_filename_column(client, as_role, fake_mcrit):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    prefix = sample.filename[:8]

    marked = marks(client.get(f"/explore/samples?query={prefix}"))

    assert prefix in marked, f"{prefix} was not marked in the filename column"


def test_the_sample_listing_marks_the_version_column(client, as_role):
    as_role("visitor")
    assert "1.3.5" in marks(client.get("/explore/samples?query=1.3.5"))


def test_the_function_listing_marks_the_function_name(client, as_role):
    as_role("visitor")
    assert "original_entry" in marks(client.get("/explore/functions?query=original_entry"))


def test_the_combined_search_page_marks_across_its_tables(client, as_role):
    """One query, three tables - families, samples and functions each get the terms."""
    as_role("visitor")
    response = client.get("/explore/search?query=citadel")

    assert response.status_code == 200
    assert marks(response).count("citadel") > 1


class ScopeStrippingClient:
    """A `corpus_mcrit` that ignores the `sample_id:`/`family_id:` scope of a query.

    The single-entry pages build their search as `f"{field}:{id} {what the user typed}"`,
    and `fixtureData._page` matches a query as one literal substring - so that composed
    string matches nothing and those two tables are always empty offline, whatever the
    user typed. That is a property of the fake, not of the pages, but it means their
    marking would otherwise ship on the strength of "it is the same expression as on the
    listing pages".

    This drops the leading scope token and delegates. The rows that come back are then
    filtered by the term but not by the id, which is wrong as a search and exactly right
    for the question here: did the query reach the row macros as marks.
    """

    def __init__(self, backend):
        self._backend = backend

    @staticmethod
    def _strip_scope(query):
        head, _, rest = (query or "").partition(" ")
        return rest if re.fullmatch(r"(sample_id|family_id):-?\d+", head) else query

    def search_samples(self, query, *args, **kwargs):
        return self._backend.search_samples(self._strip_scope(query), *args, **kwargs)

    def search_functions(self, query, *args, **kwargs):
        return self._backend.search_functions(self._strip_scope(query), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._backend, name)


@pytest.fixture
def scope_stripping_mcrit(corpus_mcrit):
    return ScopeStrippingClient(corpus_mcrit)


def test_the_single_family_page_marks_its_sample_rows(client, as_role, scope_stripping_mcrit, app):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: scope_stripping_mcrit
    as_role("visitor")

    response = client.get("/explore/families/1?query=1.3.5")

    assert response.status_code == 200
    assert "1.3.5" in marks(response)


def test_the_single_sample_page_marks_its_function_rows(client, as_role, scope_stripping_mcrit, app):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: scope_stripping_mcrit
    as_role("visitor")

    response = client.get("/explore/samples/1?query=original_entry")

    assert response.status_code == 200
    assert "original_entry" in marks(response)


@pytest.mark.parametrize("path", ["/explore/families", "/explore/samples", "/explore/functions", "/explore/search"])
def test_a_page_without_a_query_marks_nothing(client, as_role, path):
    as_role("visitor")
    assert marks(client.get(path)) == []


@pytest.mark.parametrize("path", ["/explore/families", "/explore/samples", "/explore/functions"])
def test_a_query_that_matches_nothing_marks_nothing(client, as_role, path):
    as_role("visitor")
    assert marks(client.get(f"{path}?query=nothingmatchesthis")) == []


# --- the payloads ----------------------------------------------------------------

def name_a_family(backend, name):
    """Rename the first family in the corpus, in the shape the backend hands over."""
    family_id, entry = next(iter(backend._families.items()))
    backend._families[family_id] = FamilyEntry.fromDict(dict(entry.toDict(), family_name=name))
    return name


def name_a_sample_file(backend, filename):
    sample_id, entry = next(iter(backend._samples.items()))
    backend._samples[sample_id] = SampleEntry.fromDict(dict(entry.toDict(), filename=filename))
    return filename


def assert_nothing_executable(response, payload, forbidden_fragments):
    body = response.get_data(as_text=True)
    assert payload not in body, f"{payload!r} was rendered verbatim"
    for forbidden in forbidden_fragments:
        assert forbidden not in body, f"{forbidden!r} was rendered as live markup"


@pytest.mark.parametrize("payload, forbidden", PAYLOADS)
def test_a_payload_as_the_search_term_is_escaped(client, as_role, fake_mcrit, payload, forbidden):
    """The payload is in the query *and* in the family name it matches, so the row is
    rendered and the marking code runs over both halves of it. Marking the needle
    without escaping it - `Markup(...)`, a `|safe`, an f-string built in the view - puts
    a live script element on the page of anyone who follows the link."""
    name_a_family(fake_mcrit, f"evil {payload}")
    as_role("visitor")

    response = client.get("/explore/families", query_string={"query": payload})

    assert response.status_code == 200
    assert_nothing_executable(response, payload, forbidden)
    assert marks(response), "no mark was produced, so this asserts nothing about escaping"


@pytest.mark.parametrize("payload, forbidden", PAYLOADS)
def test_a_payload_in_a_family_name_is_escaped_inside_the_mark(client, as_role, fake_mcrit, payload, forbidden):
    """The other half of the same danger: the needle is harmless here, and the markup
    arrives from the backend inside the very text being marked up."""
    name_a_family(fake_mcrit, f"zeus{payload}")
    as_role("visitor")

    response = client.get("/explore/families?query=zeus")

    assert response.status_code == 200
    assert_nothing_executable(response, payload, forbidden)
    # two families match "zeus" now: the renamed one and win.vmzeus
    assert set(marks(response)) == {"zeus"}


@pytest.mark.parametrize("payload, forbidden", PAYLOADS)
def test_a_payload_in_a_sample_filename_is_escaped(client, as_role, fake_mcrit, payload, forbidden):
    """Sample rows mark four columns rather than one, and the filename is the field an
    adversary names directly."""
    name_a_sample_file(fake_mcrit, f"evil {payload}")
    as_role("visitor")

    response = client.get("/explore/samples", query_string={"query": payload})

    assert response.status_code == 200
    assert_nothing_executable(response, payload, forbidden)
    assert marks(response), "no mark was produced, so this asserts nothing about escaping"


@pytest.mark.parametrize("payload, forbidden", PAYLOADS)
def test_a_payload_survives_the_combined_search_page(client, as_role, fake_mcrit, payload, forbidden):
    """The search page also echoes the query into a heading and an input value."""
    name_a_family(fake_mcrit, f"evil {payload}")
    as_role("visitor")

    response = client.get("/explore/search", query_string={"query": payload})

    assert response.status_code == 200
    assert_nothing_executable(response, payload, forbidden)


def test_the_marked_chunks_are_escaped_not_merely_absent(client, as_role, fake_mcrit):
    """A guard on the guard above. If `mark()` ever stopped emitting the payload at all
    - a filter that strips angle brackets, say - the "not in body" assertions would
    still pass while the cell silently lied about the row's name. The escaped form has
    to be there, inside a mark, character for character."""
    name_a_family(fake_mcrit, f"evil {SCRIPT_PAYLOAD}")
    as_role("visitor")

    response = client.get("/explore/families", query_string={"query": SCRIPT_PAYLOAD})

    assert "&lt;script&gt;alert" in marks(response)
    assert "&lt;/script&gt;" in marks(response)


# --- the ratchet -----------------------------------------------------------------

def test_the_highlighting_helper_never_builds_markup():
    """Importing `Markup`, `escape` or anything else from the markup layer would mean
    the helper had started producing markup of its own, at which point the escaping
    argument stops holding: the template would be rendering a string it did not escape.
    Marking is a decision about boundaries; the element belongs in the template.

    Read from the syntax tree rather than by grepping the text, so that the module can
    go on *explaining* why it does not use Markup without tripping its own ratchet."""
    import ast

    import mcritweb.search_highlighting as module

    with open(module.__file__, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)

    assert not imported & {"markupsafe", "flask", "jinja2", "html", "Markup", "escape"}, (
        f"search_highlighting.py imports from the markup layer: {sorted(imported)}"
    )


def test_the_mark_macro_does_not_disable_autoescaping():
    """`|safe` on the chunk is the one edit that turns this feature into the hole it was
    written to avoid, and it would look entirely reasonable to someone adding a second
    element around the match."""
    import os

    import mcritweb

    links = os.path.join(os.path.dirname(mcritweb.__file__), "templates", "table", "links.html")
    with open(links, encoding="utf-8") as template:
        source = template.read()
    macro = source[source.index("{%- macro mark("):]
    macro = macro[:macro.index("{%- endmacro -%}")]

    assert "<mark>" in macro, "the mark macro no longer writes the element - re-read this test"
    assert not re.search(r"\|\s*safe\b", macro), "|safe inside the mark macro"


if __name__ == "__main__":
    unittest.main()
