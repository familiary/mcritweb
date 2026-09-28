#!/usr/bin/python
"""The search-backed pages, rendered against results that actually contain rows.

Until now the fake answered every search with an empty result set. Every page that
embeds a search returned 200, and none of them proved anything: an empty list
renders as an empty table whatever the row markup does with an entry. That is the
gap issue #88 left open, and closing it is what these tests are for.

`fixtureData._page` models the cursor protocol - opaque token, a forward cursor only
while results remain, a backward one only off the first page - so the paging links
can be followed rather than merely rendered.

The corpus holds 5 families, 13 samples and 609 functions, which is enough for a
second page at a limit of 10.
"""

import logging
import re
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: (path, the id of a row that must appear). Ids are rendered as links, so their
#: presence is evidence the row macro ran over a real entry.
LISTING_PAGES = [
    ("/explore/families", "/explore/families/"),
    ("/explore/samples", "/explore/samples/"),
    ("/explore/functions", "/explore/functions/"),
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def row_ids(response, pattern):
    return sorted(set(re.findall(pattern, response.get_data(as_text=True))))


# --- the listings render rows ----------------------------------------------------

def test_the_family_listing_renders_the_families(client, as_role, fake_mcrit):
    as_role("visitor")
    page = client.get("/explore/families").get_data(as_text=True)

    for family in fake_mcrit._families.values():
        assert family.family_name in page, f"{family.family_name} is missing from the listing"


def test_the_sample_listing_renders_the_samples(client, as_role, fake_mcrit):
    """Rows are identified by their id link - the filename column is shortened for
    display, so asserting on the whole filename would be asserting on the CSS."""
    as_role("visitor")
    response = client.get("/explore/samples?page=1&limit=25")

    rendered = set(row_ids(response, r"/explore/samples/(\d+)"))
    assert rendered == {str(sample_id) for sample_id in fake_mcrit._samples}


def test_the_function_listing_renders_rows(client, as_role):
    """Function rows link by pichash rather than by function id, so that is what
    proves the row macro ran over real entries."""
    as_role("visitor")
    response = client.get("/explore/functions?page=1&limit=10")

    assert response.status_code == 200
    assert len(row_ids(response, r"query=pichash:(0x[0-9a-f]+)")) >= 5


@pytest.mark.parametrize("path, _marker", LISTING_PAGES)
def test_a_query_that_matches_nothing_still_renders(client, as_role, path, _marker):
    as_role("visitor")
    assert client.get(f"{path}?query=nothingmatchesthis").status_code == 200


# --- paging ----------------------------------------------------------------------

def test_the_sample_listing_pages_forward_to_different_rows(client, as_role):
    """Two pages of ten over thirteen samples. If the cursor were ignored, the
    second page would repeat the first."""
    as_role("visitor")
    first = client.get("/explore/samples?page=1&limit=10")
    second = client.get(follow_cursor(first, "/explore/samples"))

    first_ids = row_ids(first, r"/explore/samples/(\d+)")
    second_ids = row_ids(second, r"/explore/samples/(\d+)")
    assert first_ids and second_ids
    assert not set(first_ids) & set(second_ids), "the second page repeated rows from the first"


def test_paging_forward_then_back_returns_the_first_page(client, as_role):
    as_role("visitor")
    first = client.get("/explore/samples?page=1&limit=10")
    second = client.get(follow_cursor(first, "/explore/samples"))
    back = client.get(follow_cursor(second, "/explore/samples", backward=True))

    assert row_ids(back, r"/explore/samples/(\d+)") == row_ids(first, r"/explore/samples/(\d+)")


def follow_cursor(response, path, backward=False):
    """The next- or previous-page URL the pagination macro rendered."""
    page = response.get_data(as_text=True)
    links = re.findall(rf'href="({re.escape(path)}\?[^"]*cursor=[^"]*)"', page)
    links = [link.replace("&amp;", "&") for link in links]
    wanted = [link for link in links if ("b%3A" in link or "b:" in link) == backward]
    assert wanted, f"no {'backward' if backward else 'forward'} paging link on {path}"
    return wanted[0]


# --- the combined search page ----------------------------------------------------

def test_the_search_page_renders_a_sample_hit(client, as_role, fake_mcrit):
    """This was a 500. `search()` iterated `results['search_results'].values()` and
    read `.sample_id` off each one, but those are dicts off the wire - the very next
    line calls `SampleEntry.fromDict` on the same value. Any query that matched a
    sample took the page down, which the empty-result fake could never show."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    response = client.get(f"/explore/search?query={sample.filename}")

    assert response.status_code == 200
    assert str(sample.sample_id) in row_ids(response, r"/explore/samples/(\d+)")


def test_the_search_page_renders_an_id_match(client, as_role, fake_mcrit):
    """The id_match branch had the same defect one line earlier."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    response = client.get(f"/explore/search?query={sample.sample_id}")

    assert response.status_code == 200
    assert str(sample.sample_id) in row_ids(response, r"/explore/samples/(\d+)")


def test_the_search_page_renders_a_sha256_match(client, as_role, fake_mcrit):
    """A sha256 is unique, so this one names exactly the sample it should."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    response = client.get(f"/explore/search?query={sample.sha256}")

    assert response.status_code == 200
    assert row_ids(response, r"/explore/samples/(\d+)") == [str(sample.sample_id)]


def test_the_search_page_renders_a_family_hit(client, as_role, fake_mcrit):
    as_role("visitor")
    family = next(iter(fake_mcrit._families.values()))
    response = client.get(f"/explore/search?query={family.family_name}&type=family")

    assert response.status_code == 200
    assert family.family_name in response.get_data(as_text=True)


@pytest.mark.parametrize("types", ["family", "sample", "function", "family,sample,function"])
def test_the_search_page_renders_for_every_type_selection(client, as_role, fake_mcrit, types):
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    assert client.get(f"/explore/search?query={sample.filename}&type={types}").status_code == 200


def test_a_search_that_matches_nothing_says_so(client, as_role):
    """The page used to render the heading, the form, and then stop - no table, no
    message, no flash. Nothing on it distinguished "no matches" from "still loading"
    or "the search silently failed". Issue #54."""
    as_role("visitor")
    response = client.get("/explore/search?query=zzzznomatchzzzz")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'Results for "zzzznomatchzzzz"' in page, "the heading is still there"
    assert "Nothing matched" in page


def test_a_search_that_matches_something_does_not_say_nothing_matched(client, as_role, fake_mcrit):
    """The other half of the contract - a page with rows on it must not carry the
    message. It is one flag over three independent sections, so it can be wrong
    in either direction."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    response = client.get(f"/explore/search?query={sample.sha256}")

    assert response.status_code == 200
    assert "Nothing matched" not in response.get_data(as_text=True)


def test_the_empty_search_page_carries_no_message(client, as_role):
    """No query means nothing has been asked yet, which is not the same as a term
    that found nothing."""
    as_role("visitor")
    response = client.get("/explore/search")

    assert response.status_code == 200
    assert "Nothing matched" not in response.get_data(as_text=True)


def test_a_search_the_backend_could_not_answer_does_not_claim_nothing_matched(client, as_role, fake_mcrit, monkeypatch):
    """`search()` reads a failed call as `results is None` and flashes "search ...
    failed!". The page then has no rows either, so the empty-result message would
    fire on top of it and tell the reader their term matched nothing - which is a
    different, and wrong, thing to say."""
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "search_families", lambda *args, **kwargs: None)
    monkeypatch.setattr(fake_mcrit, "search_samples", lambda *args, **kwargs: None)
    monkeypatch.setattr(fake_mcrit, "search_functions", lambda *args, **kwargs: None)

    response = client.get("/explore/search?query=anything")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # #113 replaced "Ups, search ... failed!" with wording that says the backend did
    # not answer. The assertion tracks the intent, not that PR's exact sentence.
    assert "the backend did not answer" in page, "the flashed error is what tells the reader what happened"
    assert "Nothing matched" not in page


def test_the_message_escapes_the_search_term(client, as_role):
    """The term is rendered back into the message, and a search term is whatever a
    caller typed. Autoescaping covers it - this is the test that says so out loud.

    Asserting the escaped term is somewhere on the page proves nothing: the <h1> and
    the form's value= attribute both echo it already, on master too. This looks inside
    the alert block, which is the markup this change added.
    """
    as_role("visitor")
    response = client.get("/explore/search?query=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "<img src=x onerror=alert(1)>" not in page

    alert = re.search(r'<div class="alert alert-info mt-3" role="alert">(.*?)</div>', page, re.S)
    assert alert, "the nothing-matched alert did not render"
    assert "&lt;img src=x onerror=alert(1)&gt;" in alert.group(1)


def test_one_category_failing_does_not_silence_the_answer_for_the_others(client, as_role, fake_mcrit, monkeypatch):
    """Three independent searches behind one flag: families failing used to suppress
    "nothing matched" for samples and functions, which had answered perfectly well and
    found nothing. The reader got one flash about families and then the same blank void
    issue #54 is about, for the two categories that did answer."""
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "search_families", lambda *args, **kwargs: None)

    # all three asked for explicitly: #146 dropped functions from the default types,
    # because a plain function search scans the whole collection (issue #76). This
    # test is about one category failing among several, so it names the several.
    page = client.get(
        "/explore/search?query=zzzznomatchzzzz&type=family,sample,function"
    ).get_data(as_text=True)

    assert "the backend did not answer" in page, "the failure still has to be reported"
    assert "Nothing matched" in page, "and so does the answer for the categories that worked"
    assert "sample, function" in page, "which should name the ones it is talking about"


def test_every_category_failing_still_says_nothing_about_matches(client, as_role, fake_mcrit, monkeypatch):
    """The other side: with nothing answering, "nothing matched" would be a claim about
    searches that never happened."""
    as_role("visitor")
    for method in ("search_families", "search_samples", "search_functions"):
        monkeypatch.setattr(fake_mcrit, method, lambda *args, **kwargs: None)

    page = client.get("/explore/search?query=zzzznomatchzzzz").get_data(as_text=True)

    assert "Nothing matched" not in page


def test_paging_past_the_last_page_of_a_search_still_explains_itself(client, as_role, fake_mcrit, monkeypatch):
    """`hasCurrent` is true whenever the request carried a cursor - which every
    pagination link supplies - and says nothing about whether that page has rows. It was
    what set the "we rendered something" flag, so following "next" off the end produced
    headings over empty tables and no explanation."""
    as_role("visitor")
    empty_with_cursor = {
        "search_results": {}, "cursor": {"current": "c", "forward": None, "backward": "b"},
        "id_match": None, "sha_match": None,
    }
    for method in ("search_families", "search_samples", "search_functions"):
        monkeypatch.setattr(fake_mcrit, method, lambda *a, **k: empty_with_cursor)

    page = client.get(
        "/explore/search?query=citadel&family_cursor=c&sample_cursor=c&function_cursor=c"
    ).get_data(as_text=True)

    assert "Nothing matched" in page

def _one_hit_that_is_also_the_id_match(entry):
    """What mcrit answers when the search term is an entry's id *and* matches its
    name: the same entry in `id_match` and in `search_results`, which are separate
    fields the view reads separately."""
    as_dict = entry.toDict()
    return {
        "search_results": {0: as_dict},
        "cursor": {"forward": None, "backward": None},
        "id_match": as_dict,
        "sha_match": None,
    }


def test_a_family_that_is_both_the_id_match_and_a_hit_is_listed_once(client, as_role, fake_mcrit, monkeypatch):
    """A family named after a number - `win.h1n1` searched for as `1`, say - comes
    back in both fields, and the view appended both. Issue #78, which reported it for
    samples; the sample path was deduplicated at some point and these two were not."""
    as_role("visitor")
    family = next(iter(fake_mcrit._families.values()))
    monkeypatch.setattr(fake_mcrit, "search_families",
                        lambda *args, **kwargs: _one_hit_that_is_also_the_id_match(family))

    response = client.get(f"/explore/search?query={family.family_id}&type=family")

    assert response.status_code == 200
    assert response.get_data(as_text=True).count('class="family-row') == 1


def test_a_function_that_is_both_the_id_match_and_a_hit_is_listed_once(client, as_role, fake_mcrit, monkeypatch):
    """Function names carry offsets - `sub_401000` - so a numeric term matching both
    an id and a name is the ordinary case here rather than a contrived one."""
    as_role("visitor")
    function = next(iter(fake_mcrit._functions.values()))
    monkeypatch.setattr(fake_mcrit, "search_functions",
                        lambda *args, **kwargs: _one_hit_that_is_also_the_id_match(function))

    response = client.get(f"/explore/search?query={function.function_id}&type=function")

    assert response.status_code == 200
    assert response.get_data(as_text=True).count('class="function-row') == 1


def test_a_sample_that_is_both_the_id_match_and_a_hit_is_still_listed_once(client, as_role, fake_mcrit, monkeypatch):
    """The path that was already deduplicated - here so a later tidy-up cannot undo
    it without something failing."""
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))
    monkeypatch.setattr(fake_mcrit, "search_samples",
                        lambda *args, **kwargs: _one_hit_that_is_also_the_id_match(sample))

    response = client.get(f"/explore/search?query={sample.sample_id}&type=sample")

    assert response.status_code == 200
    assert response.get_data(as_text=True).count('class="sample-row') == 1


def test_distinct_families_are_all_listed(client, as_role, fake_mcrit):
    """Deduplication that also drops distinct rows would pass every test above."""
    as_role("visitor")
    response = client.get("/explore/search?query=win&type=family")

    expected = [f for f in fake_mcrit._families.values() if "win" in f.family_name.lower()]
    assert len(expected) > 1, "the corpus has to hold more than one for this to mean anything"
    assert response.get_data(as_text=True).count('class="family-row') == len(expected)

# --- the analyze pages, which share one search-result reader ---------------------

#: (path, the query parameter that reaches `get_unique_samples_from_search_result`)
ANALYZE_PAGES = [
    ("/analyze/compare", "query"),
    ("/analyze/cross_compare", "query"),
    ("/analyze/compare_versus", "query_a"),
    ("/analyze/unique_blocks", "query"),
]


@pytest.mark.parametrize("path, query_param", ANALYZE_PAGES)
def test_the_analyze_pages_render_entries_rather_than_wire_dicts(client, as_role, fake_mcrit, path, query_param):
    """All three read their sample list through the same helper, and nothing pinned
    what it hands the row macro - the route matrix asserts these pages answer 200, but
    it does not distinguish a page of rows from the "no samples available" placeholder.

    The evidence is the short sha column: `getShortSha256` is a method on `SampleEntry`
    with no key of that name in the wire dict, so a raw dict renders as an
    `UndefinedError` rather than as a slightly wrong cell. Issue #64.
    """
    as_role("visitor")
    sample = next(iter(fake_mcrit._samples.values()))

    response = client.get(f"{path}?{query_param}={sample.sample_id}")

    assert response.status_code == 200
    assert sample.getShortSha256() in response.get_data(as_text=True)


# --- the fake's own contract -----------------------------------------------------

def test_the_forward_cursor_is_absent_on_the_last_page(corpus_mcrit):
    assert corpus_mcrit.search_samples("", limit=100)["cursor"]["forward"] is None


def test_the_backward_cursor_is_absent_on_the_first_page(corpus_mcrit):
    assert corpus_mcrit.search_samples("", limit=10)["cursor"]["backward"] is None


def test_a_forward_cursor_yields_the_next_slice(corpus_mcrit):
    first = corpus_mcrit.search_samples("", limit=10)
    second = corpus_mcrit.search_samples("", cursor=first["cursor"]["forward"], limit=10)

    assert not set(first["search_results"]) & set(second["search_results"])
    assert second["cursor"]["backward"] is not None


def test_a_backward_cursor_returns_the_previous_slice(corpus_mcrit):
    first = corpus_mcrit.search_samples("", limit=10)
    second = corpus_mcrit.search_samples("", cursor=first["cursor"]["forward"], limit=10)
    back = corpus_mcrit.search_samples("", cursor=second["cursor"]["backward"], limit=10)

    assert list(back["search_results"]) == list(first["search_results"])


def test_search_results_are_dicts_not_entries(corpus_mcrit):
    """The search_* methods answer the wire format, and `mcritweb.views.search.search_page`
    calls `.fromDict` on every value. Handing back entry objects here would let code that
    read the dict as if it were entries pass, which is exactly the bug this module's
    search-page tests exist to catch."""
    results = corpus_mcrit.search_samples("", limit=1)["search_results"]

    assert all(isinstance(value, dict) for value in results.values())


def test_descending_order_reverses_the_page(corpus_mcrit):
    ascending = corpus_mcrit.search_samples("", limit=100, is_ascending=True)
    descending = corpus_mcrit.search_samples("", limit=100, is_ascending=False)

    assert list(descending["search_results"]) == list(reversed(list(ascending["search_results"])))


if __name__ == "__main__":
    unittest.main()


def test_an_empty_type_parameter_does_not_claim_nothing_matched(client, as_role):
    """`?type=` splits to [""], which is truthy - so the page said "Nothing matched" for
    a request in which no category was searched at all. Only reachable by hand-writing
    the URL: unticking every box in the form sends no `type` at all, which correctly
    falls back to all three."""
    as_role("visitor")

    page = client.get("/explore/search?query=foo&type=").get_data(as_text=True)

    assert "Nothing matched" not in page


def test_an_unknown_type_is_ignored_rather_than_counted(client, as_role):
    as_role("visitor")

    page = client.get("/explore/search?query=foo&type=notacategory").get_data(as_text=True)

    assert "Nothing matched" not in page


def test_the_form_default_still_searches_all_three(client, as_role):
    """The guard must not break the ordinary case: no `type` at all means all three."""
    as_role("visitor")

    page = client.get("/explore/search?query=zzzznomatchzzzz").get_data(as_text=True)

    assert "Nothing matched" in page
