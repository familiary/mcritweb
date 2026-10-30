#!/usr/bin/python
"""Search as you type, on the pages where a search is cheap enough for it.

`static/live_search.js` takes a form marked `data-live-search="<id>"`, fetches the page
the form would load as the term is typed and swaps in that page's element of that id.
These tests hold the markup to what the script needs, keep it off the pages whose
searches are slow (docs/adr/0016-slow-function-search-is-mcrits.md), and check what a
sample page now sends for an offset. tests/testLiveSearchBrowser.py drives the script itself.
"""

import os
import re

import pytest

TEMPLATES = os.path.join(os.path.dirname(__file__), os.pardir, "mcritweb", "templates")

SAMPLE_ID = 5


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def searched_terms(fake_mcrit, method):
    return [args[0] for name, args, _ in fake_mcrit.calls if name == method]


#: how a page loads the script: queued with base.html's later(), which runs it after the
#: first paint (ADR-0021). url_for appends a `?v=` version to the static URL.
LOADS_LIVE_SEARCH = r'<script nonce="[^"]*">later\("[^"]*live_search\.js(?:\?v=\w+)?"\)</script>'


def live_search_forms(page):
    """Each live-search form of a page, as (region id, the region's markup)."""
    found = []
    for region in re.findall(r'<form[^>]*data-live-search="([^"]+)"', page):
        match = re.search(r'<div id="' + re.escape(region) + r'">(.*?)' + LOADS_LIVE_SEARCH, page, re.S)
        found.append((region, match.group(1) if match else None))
    return found


# what the swapped region holds; the corpus double matches a search term as one
# substring, so it finds nothing for the `sample_id:` and `family_id:` queries these two
# pages compose, and they show their empty tables
@pytest.mark.parametrize("url, region, rows", [
    (f"/explore/samples/{SAMPLE_ID}", "sample-functions", "This sample has no functions on record."),
    ("/explore/samples", "sample-list", 'id="sample-table"'),
    ("/explore/families", "family-list", 'id="family-table"'),
    ("/explore/families/3", "family-samples", "This family has no samples."),
])
def test_the_cheap_searches_filter_as_they_are_typed(client, as_role, url, region, rows):
    as_role("visitor")
    page = client.get(url).get_data(as_text=True)

    forms = live_search_forms(page)
    assert [found for found, _ in forms] == [region]
    swapped = forms[0][1]
    assert swapped is not None, "the region is not closed before the script"
    assert rows in swapped
    assert f'id="{region}-status"' in page


@pytest.mark.parametrize("url", ["/explore/functions", "/explore/search"])
def test_unscoped_function_searches_still_wait_for_enter(client, as_role, url):
    """A bare term there scans every function name in the corpus, and stopping the
    request in the browser does not stop the backend's query
    (docs/adr/0016-slow-function-search-is-mcrits.md)."""
    as_role("visitor")
    page = client.get(url + "?query=x").get_data(as_text=True)

    assert "data-live-search" not in page
    assert not re.search(LOADS_LIVE_SEARCH, page)


@pytest.mark.parametrize("typed", ["0x401010", "0X401010", " 0x401010 ", "0xDEADbeef"])
def test_a_bare_hex_number_on_a_sample_page_is_its_offset(client, as_role, fake_mcrit, typed):
    as_role("visitor")
    client.get(f"/explore/samples/{SAMPLE_ID}", query_string={"query": typed})

    assert searched_terms(fake_mcrit, "search_functions") == [f"sample_id:{SAMPLE_ID} offset:{typed.strip()}"]


@pytest.mark.parametrize("typed", ["pre_c_init", "0x401010 init", "offset:0x401010", "401010", "0x", "0xg1"])
def test_anything_else_on_a_sample_page_is_searched_as_typed(client, as_role, fake_mcrit, typed):
    as_role("visitor")
    client.get(f"/explore/samples/{SAMPLE_ID}", query_string={"query": typed})

    assert searched_terms(fake_mcrit, "search_functions") == [f"sample_id:{SAMPLE_ID} {typed}"]


@pytest.mark.parametrize("template, row", [
    ("table/function_row.html", "function-row"),
    ("table/sample_row.html", "sample-row"),
    ("table/family_row.html", "family-row"),
])
def test_row_clicks_reach_rows_swapped_in_later(template, row):
    """A binding made on page load holds only the rows there at the time; delegating
    them is the precondition docs/adr/0014-no-htmx-for-table-reloads.md names for any
    table swap."""
    with open(os.path.join(TEMPLATES, template)) as source:
        js = source.read()

    assert f'$(document).on("click", "tr.{row}"' in js
    assert f'$("tr.{row}").click(' not in js
