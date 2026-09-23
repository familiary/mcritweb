#!/usr/bin/python
"""An exact hit on an id or a sha256 is marked as such, wherever results are listed.

MCRIT answers a search with `search_results`, the text hits, and beside them `id_match`
(plus `sha_match` for samples), the exact hit on an identifier. Both end up in the same
table, and once they do the exact one is indistinguishable from a substring hit on a
filename - which is what issue #56 asks to fix: mark those matches, and not only on the
search page.

The mark is a badge at the start of the row, through the row decoration convention of
issue #53 (`templates/table/row_decoration.html`); the badge column only exists while
some row has a badge, so an ordinary text search looks exactly as it did before.
"""

import logging
import re
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: the id cell of each row macro. The class is what the row's click handler reads, so
#: it is the one part of the markup that cannot drift silently.
ID_CELL = re.compile(r'<th[^>]*class="(?:id|sample-id|function-id)"[^>]*>\s*(-?\d+)\s*</th>')


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def table_html(page, table_id):
    start = page.find(f'id="{table_id}"')
    assert start != -1, f"no table {table_id!r} on the page"
    return page[start:page.index("</table>", start)]


def table_of(response, table_id):
    return table_html(response.get_data(as_text=True), table_id)


def rows_of_table(page, table_id):
    """(id, badge label or None) per body row, in render order.

    Takes the page markup rather than the response, so a test that already has the
    HTML in hand - testListingIdMatches walks several pages per test - can read the
    same markup contract through the same one description of it.
    """
    body = table_html(page, table_id)
    found = []
    for row in body.split("<tr")[1:]:
        id_cell = ID_CELL.search(row)
        if id_cell is None:
            continue
        badge = re.search(r'<span class="badge[^"]*"[^>]*>([^<]*)</span>', row)
        found.append((int(id_cell.group(1)), badge.group(1) if badge else None))
    return found


def rows_of(response, table_id):
    return rows_of_table(response.get_data(as_text=True), table_id)


def marks_of(response, table_id):
    return {row_id: label for row_id, label in rows_of(response, table_id) if label}


def table_widths(response, table_id):
    """Cells in the header, then in each body row."""
    head, _, rest = table_of(response, table_id).partition("</thead>")
    return [len(re.findall(r"<t[hd][\s>]", part)) for part in [head] + rest.split("<tr")[1:]]


# --- the listing pages -----------------------------------------------------------

def test_the_family_listing_marks_the_id_match(client, as_role):
    as_role("visitor")
    marks = marks_of(client.get("/explore/families?query=2"), "family-table")

    assert marks == {2: "ID match"}


def test_the_function_listing_marks_the_id_match(client, as_role):
    as_role("visitor")
    marks = marks_of(client.get("/explore/functions?query=5"), "function-table")

    assert marks == {5: "ID match"}


def test_the_sample_listing_marks_only_the_exact_hit(client, as_role):
    """`6` is sample 6's id and a substring of every sha256 in the corpus, so the exact
    hit arrives surrounded by twelve text hits. Exactly one of the thirteen is marked."""
    as_role("visitor")
    response = client.get("/explore/samples?query=6")

    listed = rows_of(response, "sample-table")
    assert len(listed) > 1, "the text hits are missing, so this proves nothing"
    assert marks_of(response, "sample-table") == {6: "ID match"}


def test_the_sample_listing_says_a_sha256_matched_by_sha256(client, as_role, fake_mcrit):
    """A sha256 and an id are different facts about a sample, and the badge names which
    one the query hit."""
    as_role("visitor")
    known = fake_mcrit._samples[6]

    response = client.get(f"/explore/samples?query={known.sha256}")

    assert marks_of(response, "sample-table") == {6: "SHA256 match"}


def test_a_text_search_grows_no_match_column(client, as_role):
    """The column is not part of the table's normal shape - a search that hit nothing
    exactly must render as it always did."""
    as_role("visitor")
    response = client.get("/explore/samples?query=citadel")

    assert rows_of(response, "sample-table"), "the query matched nothing, so this proves nothing"
    assert marks_of(response, "sample-table") == {}
    assert 'class="badge' not in table_of(response, "sample-table")


# --- the search page -------------------------------------------------------------

@pytest.mark.parametrize("query,table_id,expected", [
    ("2&type=family", "family-table", {2: "ID match"}),
    ("6&type=sample", "sample-table", {6: "ID match"}),
    ("5&type=function", "function-table", {5: "ID match"}),
])
def test_the_search_page_marks_the_id_match(client, as_role, query, table_id, expected):
    as_role("visitor")

    assert marks_of(client.get(f"/explore/search?query={query}"), table_id) == expected


def test_the_search_page_says_a_sha256_matched_by_sha256(client, as_role, fake_mcrit):
    as_role("visitor")
    known = fake_mcrit._samples[6]

    response = client.get(f"/explore/search?query={known.sha256}&type=sample")

    assert marks_of(response, "sample-table") == {6: "SHA256 match"}


# --- the shape of the table ------------------------------------------------------

@pytest.mark.parametrize("marked,unmarked,table_id", [
    ("/explore/families?query=2", "/explore/families", "family-table"),
    ("/explore/samples?query=6", "/explore/samples", "sample-table"),
    ("/explore/functions?query=5", "/explore/functions", "function-table"),
])
def test_the_mark_costs_one_column_and_no_row_is_left_behind(client, as_role, marked, unmarked, table_id):
    """The header and the rows are handed the same mapping, so both grow the column or
    neither does - a table where only one of them did would render skewed. And the same
    table without an exact hit is exactly one column narrower.

    Counted per section rather than per <tr>, because family_header emits its cells
    without an opening <tr> - a pre-existing quirk this change does not touch."""
    as_role("visitor")

    widths = table_widths(client.get(marked), table_id)
    assert len(widths) > 1, "no rows to compare the header against"
    assert len(set(widths)) == 1, f"{table_id} rendered a header and rows of widths {widths}"
    assert set(table_widths(client.get(unmarked), table_id)) == {widths[0] - 1}


def test_a_family_name_stays_escaped_in_a_marked_table(client, as_role, fake_mcrit):
    """Family names are attacker-influenced - whoever submits a sample names its family.
    Adding a column must not become an excuse to hand any of this row through |safe."""
    as_role("visitor")
    hostile = '<script>alert("xss")</script>'
    fake_mcrit._families[2].family_name = hostile

    response = client.get("/explore/families?query=2")

    page = response.get_data(as_text=True)
    assert marks_of(response, "family-table") == {2: "ID match"}, "the row under test was not marked"
    assert hostile not in page
    assert "&lt;script&gt;" in page


if __name__ == "__main__":
    unittest.main()
