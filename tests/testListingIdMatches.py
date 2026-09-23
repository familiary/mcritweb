#!/usr/bin/python
"""A listing page must find what the search page finds.

MCRIT answers a search with two things: `search_results`, the text hits, and `id_match`
(plus `sha_match` for samples), the exact hit on an identifier. `explore.search` reads
both. `explore.families`, `explore.samples` and `explore.functions` read only the first
and drop the other on the floor - so the same query, against the same backend, gave
opposite answers depending on which page you typed it into:

    /explore/functions?query=5              -> "No functions available"
    /explore/search?query=5&type=function   -> function 5

That is a search hiding a record that exists, which is worse than the cosmetic half of
issue #56 (marking those matches, which is what the issue text is about). Reproduced
live for families and functions; the samples path is the same code shape but the corpus
cannot demonstrate it, because every single-digit sample id is also a substring of some
sha256 and so arrives as a text hit anyway.

On the real backend `id_match` is populated for any bare integer (or 0x-integer) up to
0xFFFFFFFF that is a real id, and `sha_match` for any 64-hex string - see
mcrit/index/MinHashIndex.py.
"""

import html
import logging
import re
import unittest
from contextlib import contextmanager

import pytest
from flask import template_rendered

# one description of the exact-match markup, kept where it is asserted in detail
from testExactMatchMarking import rows_of_table

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def row_count(response, kind):
    return response.get_data(as_text=True).count(f"parent_{kind}-table")


@pytest.mark.parametrize(
    "listing,search,kind,query",
    [
        ("/explore/families?query=2", "/explore/search?query=2&type=family", "family", "2"),
        ("/explore/functions?query=5", "/explore/search?query=5&type=function", "function", "5"),
    ],
)
def test_the_listing_page_finds_what_the_search_page_finds(client, as_role, listing, search, kind, query):
    as_role("visitor")

    on_the_listing = row_count(client.get(listing), kind)
    on_the_search_page = row_count(client.get(search), kind)

    assert on_the_search_page > 0, "the premise: the search page does find it"
    assert on_the_listing == on_the_search_page, \
        f"{listing} rendered {on_the_listing} rows where the search page rendered {on_the_search_page}"


@pytest.mark.parametrize(
    "path,kind,placeholder",
    [
        ("/explore/families?query=2", "family", "No families available"),
        ("/explore/functions?query=5", "function", "No functions available"),
    ],
)
def test_the_listing_page_does_not_claim_the_record_is_absent(client, as_role, path, kind, placeholder):
    """The visible symptom: the empty-table placeholder, shown for a record that exists.

    Asserted on the exact string the table macro emits, so this fails if the row stops
    being rendered - not on a phrase that could drift.
    """
    as_role("visitor")

    response = client.get(path)
    page = response.get_data(as_text=True)

    assert row_count(response, kind) > 0, "the record is not listed at all"
    assert placeholder not in page, f"{path} still shows {placeholder!r}"


def test_a_sample_sha256_reaches_the_samples_listing(client, as_role, fake_mcrit):
    """The samples page has the same shape. The corpus cannot stage the id half - every
    single-digit id is a substring of some sha256, so it arrives as a text hit anyway -
    so this drives the sha_match branch with a backend that answers only there."""
    as_role("visitor")
    known = fake_mcrit._samples[6]
    monkey = {
        "search_results": {},
        "cursor": {"forward": None, "backward": None},
        "id_match": None,
        "sha_match": known.toDict(),
    }
    fake_mcrit.search_samples = lambda *args, **kwargs: monkey

    response = client.get(f"/explore/samples?query={known.sha256}")

    assert response.status_code == 200
    assert row_count(response, "sample") == 1
    assert known.sha256[:8] in response.get_data(as_text=True)


def test_a_sample_id_match_reaches_the_samples_listing(client, as_role, fake_mcrit):
    as_role("visitor")
    known = fake_mcrit._samples[6]
    fake_mcrit.search_samples = lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"forward": None, "backward": None},
        "id_match": known.toDict(), "sha_match": None,
    }

    response = client.get("/explore/samples?query=6")

    assert row_count(response, "sample") == 1


@pytest.mark.parametrize("kind,path", [("family", "/explore/families"), ("function", "/explore/functions"),
                                       ("sample", "/explore/samples")])
def test_an_exact_hit_that_is_also_a_text_hit_is_listed_once(client, as_role, fake_mcrit, kind, path):
    """The id match is prepended, and the same record can come back among the text hits
    - which is how issue #78 happened on the search page. Not repeating it here."""
    as_role("visitor")
    pool = {"family": fake_mcrit._families, "function": fake_mcrit._functions,
            "sample": fake_mcrit._samples}[kind]
    key = sorted(pool)[0]
    entry = pool[key]
    as_dict = entry if isinstance(entry, dict) else entry.toDict()
    result = {
        "search_results": {str(key): as_dict},
        "cursor": {"forward": None, "backward": None},
        "id_match": as_dict,
        "sha_match": None,
    }
    setattr(fake_mcrit, f"search_{kind}s" if kind != "family" else "search_families",
            lambda *args, **kwargs: result)

    response = client.get(f"{path}?query={key}")

    assert row_count(response, kind) == 1, "the exact hit was listed twice"


def test_a_query_that_matches_nothing_still_lists_nothing(client, as_role, fake_mcrit):
    """The guard: prepending an absent id_match must not conjure a row."""
    as_role("visitor")
    fake_mcrit.search_families = lambda *args, **kwargs: {
        "search_results": {}, "cursor": {"forward": None, "backward": None},
        "id_match": None, "sha_match": None,
    }

    response = client.get("/explore/families?query=zzznothing")

    assert response.status_code == 200
    assert row_count(response, "family") == 0


# --- the exact hit belongs on the first page, and only there ---------------------
#
# mcrit computes `id_match` (and `sha_match`) from the search term *before* the
# cursor is applied and attaches the result to every page it answers - see
# `MinHashIndex.getFamilySearchResults` and its two siblings, where the lookup runs
# at the top of the method and the assignment happens after
# `_getSearchResultTemplate` has already windowed the text hits. A view that
# prepends it unconditionally therefore repeats one row, badge and all, on page 2,
# 3, 4 ... and inflates each of those pages past the limit the reader asked for.
#
# The mirror image matters just as much: the way *back* to page 1 carries the
# backward cursor the second page handed out, so a rule of "prepend only when there
# is no cursor" would drop the record from a page that had just shown it. That is
# issue #56's own failure - a listing hiding a record that exists - so both
# directions are walked below.

#: One of the sizes the `limit` query param accepts. Small enough to page quickly.
PAGE_LIMIT = 10

#: 25 text hits: three pages at PAGE_LIMIT, so there is a middle page as well as a last.
TEXT_HIT_IDS = list(range(100, 125))

#: The record the backend answers as the exact hit. Deliberately not among the text
#: hits, which is the shape mcrit produces for a bare id whose record does not also
#: carry that id in a searchable field.
EXACT_ID = 1


def clone(template, id_field, new_id):
    """A search result dict like the wire carries, under a chosen id."""
    entry = dict(template)
    entry[id_field] = new_id
    return entry


def paged_search(entries, id_field, id_match=None, sha_match=None):
    """A backend that pages the way mcrit's search does, exact hit and all.

    The cursor is an offset rather than mcrit's serialised sort key; the views must
    not read either, and `tests/fixtureData.py` makes the same trade. Two behaviours
    do have to be faithful, because the bug lives in them:

    * the exact hit is derived from the search term, not from the window, so it
      comes back with *every* page;
    * the backward cursor is answered for *any* request that carried a cursor, not
      only for one that is past the first page - `_getSearchResultTemplate` sets it
      whenever `cursor is not None` and the page has rows, so a first page reached
      by paging back still carries one and cannot be recognised by its absence.
    """
    def _search(search_term="", cursor=None, is_ascending=True, sort_by=None, limit=PAGE_LIMIT, *args, **kwargs):
        start = int(cursor) if cursor else 0
        window = entries[start:start + limit]
        return {
            "search_results": {str(entry[id_field]): entry for entry in window},
            "cursor": {
                "forward": str(start + limit) if start + limit < len(entries) else None,
                "backward": str(max(0, start - limit)) if cursor and window else None,
            },
            "id_match": id_match,
            "sha_match": sha_match,
        }
    return _search


@contextmanager
def rendered_context(app):
    """The context a page was rendered with - the rows themselves, not their markup."""
    contexts = []

    def record(sender, template, context, **extra):
        contexts.append(context)

    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


def id_of(row, id_field):
    """Rows reach the templates as entry objects on two of the pages and as raw dicts
    on the third; `explore.functions` has always kept them as dicts."""
    return row[id_field] if isinstance(row, dict) else getattr(row, id_field)


class Page:
    """One rendered listing page: its rows, its pagination, and its markup."""

    def __init__(self, url, context, response, context_key, id_field, table_id, pagination_key):
        self.url = url
        self.ids = [id_of(row, id_field) for row in context[context_key]]
        # the search page renders three tables and hands each its own pagination,
        # under its query param prefix; a listing page has the one
        self.pagination = context[pagination_key]
        self.html = response.get_data(as_text=True)
        self.table_id = table_id

    @property
    def marked_rows(self):
        """(id, badge label or None) per rendered row, in render order. Reuses the
        marking module's reader so there is one description of that markup."""
        return rows_of_table(self.html, self.table_id)


def fetch(client, app, url, context_key, id_field, table_id, pagination_key="pagination"):
    with rendered_context(app) as contexts:
        response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    # flask-dropzone renders a string template of its own first, so pick the context
    # that actually belongs to the listing rather than the first one
    context = next((ctx for ctx in contexts if context_key in ctx), None)
    assert context is not None, f"{url} did not render the listing"
    return Page(url, context, response, context_key, id_field, table_id, pagination_key)


def walk_forward(client, app, path, listing, prefix=""):
    """Follow the forward cursor across the whole listing, as the "next" link does."""
    _, _, context_key, id_field, _, _, table_id = listing
    pages = []
    cursor = None
    page_number = 1
    while True:
        url = f"{path}&{prefix}page={page_number}&{prefix}limit={PAGE_LIMIT}"
        if cursor is not None:
            url += f"&{prefix}cursor={cursor}"
        page = fetch(client, app, url, context_key, id_field, table_id, f"{prefix}pagination")
        pages.append(page)
        cursor = page.pagination.cursor["forward"]
        page_number += 1
        if cursor is None:
            return pages
        assert page_number < 10, "the fake never ran out of pages"


#: Every href the pagination widget can emit for the way back carries the backward
#: cursor and the decremented page number - the `<` arrow and the "1" number link
#: both call get_link('backward'). The `<<` link is cursorless and is not this.
def way_back_link(page):
    hrefs = {html.unescape(href) for href in re.findall(r'href="([^"]+)"', page.html)
             if "cursor=" in href and re.search(r"(^|&|&amp;)[a-z_]*page=1(&|&amp;|$)", href)}
    assert len(hrefs) == 1, f"expected one cursored way back to page 1, got {sorted(hrefs)}"
    return hrefs.pop()


#: kind, path, template context key, id field, client method, corpus pool, table id
LISTINGS = [
    ("family", "/explore/families", "families", "family_id", "search_families", "_families", "family-table"),
    ("sample", "/explore/samples", "samples", "sample_id", "search_samples", "_samples", "sample-table"),
    ("function", "/explore/functions", "functions", "function_id", "search_functions", "_functions", "function-table"),
]


def corpus_template(fake_mcrit, listing):
    pool = getattr(fake_mcrit, listing[5])
    return pool[sorted(pool)[0]].toDict()


def stage(fake_mcrit, listing, exact_key="id_match", exact_is_a_text_hit=False):
    """Wire the listing up with 25 text hits plus an exact hit.

    The exact hit sits outside the text hits by default - the shape mcrit produces
    for a bare id whose record does not also carry that id in a searchable field. With
    `exact_is_a_text_hit` it is the first text hit as well, which is the case a page
    has to fold into one row rather than render twice.
    """
    _, path, _, id_field, method, _, _ = listing
    template = corpus_template(fake_mcrit, listing)
    exact = clone(template, id_field, EXACT_ID)
    entries = [clone(template, id_field, hit_id) for hit_id in TEXT_HIT_IDS]
    if exact_is_a_text_hit:
        entries = [exact] + entries[:-1]
    setattr(fake_mcrit, method, paged_search(entries, id_field, **{exact_key: exact}))
    return path


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_the_exact_hit_is_listed_once_across_the_whole_paged_listing(client, app, as_role, fake_mcrit, listing):
    """The bug: `id_match` arrives with every page, so it was prepended to every page."""
    as_role("visitor")
    path = stage(fake_mcrit, listing)

    pages = walk_forward(client, app, f"{path}?query={EXACT_ID}", listing)

    assert len(pages) == 3, f"expected three pages of {PAGE_LIMIT}, got {[len(page.ids) for page in pages]}"
    appearances = [number for number, page in enumerate(pages, 1) if EXACT_ID in page.ids]
    assert appearances == [1], \
        f"the exact hit was listed on pages {appearances}, but belongs to the first page only"


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_the_exact_hit_leads_the_first_page_and_is_badged(client, app, as_role, fake_mcrit, listing):
    """The placement claim the manual and `exact_matches_to_prepend` both make: the
    record you named is the first row, and it says why it is there."""
    as_role("visitor")
    path = stage(fake_mcrit, listing)

    first = walk_forward(client, app, f"{path}?query={EXACT_ID}", listing)[0]

    assert first.ids[0] == EXACT_ID, f"the exact hit is not the first row: {first.ids}"
    assert first.marked_rows[0] == (EXACT_ID, "ID match"), \
        f"the first row does not carry the exact-match badge: {first.marked_rows[:2]}"


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_no_page_is_inflated_by_the_exact_hit(client, app, as_role, fake_mcrit, listing):
    """Every page carries its full window of text hits, and no page past the first
    carries anything else.

    The exact hit itself is deliberately *not* counted against the limit, so the first
    page carries PAGE_LIMIT + 1 rows: it is an answer to the query rather than a
    member of the paged result set, and making room for it by dropping a text hit
    would hide that hit for good - mcrit builds the forward cursor from the last entry
    it returned (`_getSearchResultTemplate`), so the next page resumes *after* the
    dropped row. See the note on `explore.exact_matches_to_prepend`.
    """
    as_role("visitor")
    path = stage(fake_mcrit, listing)

    pages = walk_forward(client, app, f"{path}?query={EXACT_ID}", listing)

    windows = [[row_id for row_id in page.ids if row_id != EXACT_ID] for page in pages]
    assert [len(window) for window in windows] == [PAGE_LIMIT, PAGE_LIMIT, len(TEXT_HIT_IDS) - 2 * PAGE_LIMIT]
    assert [len(page.ids) for page in pages] == [PAGE_LIMIT + 1, PAGE_LIMIT, len(TEXT_HIT_IDS) - 2 * PAGE_LIMIT]
    assert [row_id for window in windows for row_id in window] == TEXT_HIT_IDS, \
        "the text hits did not survive paging intact"


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_paging_back_to_the_first_page_still_shows_the_exact_hit(client, app, as_role, fake_mcrit, listing):
    """The mirror image, and issue #56's own failure: a record that exists, hidden.

    The way back is a cursor like any other - mcrit answers a backward cursor for any
    request that carried one - so a first page reached with the `<` arrow or the "1"
    number link arrives *with* a cursor. A rule that asks "is there a cursor?" reads
    that as a later page and drops the record the reader searched for, from a page
    that had shown it a moment earlier under the same number.
    """
    as_role("visitor")
    path = stage(fake_mcrit, listing)
    _, _, context_key, id_field, _, _, table_id = listing
    url = f"{path}?query={EXACT_ID}"

    fresh = fetch(client, app, f"{url}&page=1&limit={PAGE_LIMIT}", context_key, id_field, table_id)
    second = fetch(client, app, f"{url}&page=2&limit={PAGE_LIMIT}&cursor={fresh.pagination.cursor['forward']}",
                   context_key, id_field, table_id)
    back = fetch(client, app, way_back_link(second), context_key, id_field, table_id)

    assert "cursor=" in way_back_link(second), "the premise: the way back carries a cursor"
    assert back.ids == fresh.ids, \
        f"page 1 walked back to is not the page 1 the reader first saw: {back.ids} vs {fresh.ids}"
    assert back.marked_rows[0] == (EXACT_ID, "ID match")


def test_a_sample_sha_match_is_not_repeated_on_every_page(client, app, as_role, fake_mcrit):
    """`sha_match` is the samples page's second exact hit and travels the same way."""
    as_role("visitor")
    listing = [entry for entry in LISTINGS if entry[0] == "sample"][0]
    path = stage(fake_mcrit, listing, exact_key="sha_match")

    pages = walk_forward(client, app, f"{path}?query={'a' * 64}", listing)

    appearances = [number for number, page in enumerate(pages, 1) if EXACT_ID in page.ids]
    assert appearances == [1], f"the sha match was listed on pages {appearances}"
    assert pages[0].marked_rows[0] == (EXACT_ID, "SHA256 match")


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_paging_is_untouched_when_there_is_no_exact_hit(client, app, as_role, fake_mcrit, listing):
    """The guard on the guard: suppressing the exact hit must not disturb ordinary paging."""
    as_role("visitor")
    _, path, _, id_field, method, _, _ = listing
    template = corpus_template(fake_mcrit, listing)
    entries = [clone(template, id_field, hit_id) for hit_id in TEXT_HIT_IDS]
    setattr(fake_mcrit, method, paged_search(entries, id_field))

    pages = walk_forward(client, app, f"{path}?query=whatever", listing)

    assert [len(page.ids) for page in pages] == [PAGE_LIMIT, PAGE_LIMIT, len(TEXT_HIT_IDS) - 2 * PAGE_LIMIT]
    assert [row_id for page in pages for row_id in page.ids] == TEXT_HIT_IDS


# --- the search page, which lists the same three tables --------------------------
#
# /explore/search was left out of the first pass at issue #56 and had both halves of
# the defect: the exact hit prepended to every cursor page, and - in the family and
# function branches, which appended instead of keying by id - listed twice on the
# same page whenever it was also a text hit.

@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_the_search_page_lists_an_exact_hit_that_is_also_a_text_hit_once(client, app, as_role, fake_mcrit, listing):
    as_role("visitor")
    kind, _, context_key, id_field, _, _, table_id = listing
    stage(fake_mcrit, listing, exact_is_a_text_hit=True)

    page = fetch(client, app, f"/explore/search?query={EXACT_ID}&type={kind}",
                 context_key, id_field, table_id, f"{kind}_pagination")

    assert page.ids.count(EXACT_ID) == 1, f"the exact hit was listed {page.ids.count(EXACT_ID)} times"
    assert page.marked_rows[0] == (EXACT_ID, "ID match")


@pytest.mark.parametrize("listing", LISTINGS, ids=[entry[0] for entry in LISTINGS])
def test_the_search_page_drops_the_exact_hit_past_the_first_page(client, app, as_role, fake_mcrit, listing):
    """The search page paginates each table under its own query param prefix."""
    as_role("visitor")
    kind = listing[0]
    stage(fake_mcrit, listing)

    pages = walk_forward(client, app, f"/explore/search?query={EXACT_ID}&type={kind}", listing, prefix=f"{kind}_")

    appearances = [number for number, page in enumerate(pages, 1) if EXACT_ID in page.ids]
    assert appearances == [1], f"the exact hit was listed on pages {appearances}"
    assert [len(page.ids) for page in pages] == [PAGE_LIMIT + 1, PAGE_LIMIT, len(TEXT_HIT_IDS) - 2 * PAGE_LIMIT]


if __name__ == "__main__":
    unittest.main()
