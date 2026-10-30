#!/usr/bin/python
"""Tables have a name, and a label cell is its row's header.

A screen reader lists a page's tables by name and announces the name on entering one; a
result page had six unnamed tables, read as "table, 8 rows" six times, and the 1 vs 1
picker two identical ones. Each table now carries a caption, visually hidden where the
heading above already shows it (statistics.html set the pattern). In the key/value tables
- the job, sample and function details, the statistics, the settings and server pages -
the label was a plain <td>, so moving along a row announced the value without its label;
it is a row header now. `scope` on a <td> means nothing and is gone, and the jobs page's
row of totals, a table only for its layout, is marked as such.
"""

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page

PAGES = ([("visitor", path) for path in VISITOR_PAGES] + [("contributor", path) for path in CONTRIBUTOR_PAGES]
         + [("admin", path) for path in ADMIN_PAGES]
         + [("visitor", f"/data/jobs/{job_id_of('cross_compare')}"), ("admin", "/admin/users/?tab=all")])


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def table_problems(html):
    page = Page(html)
    problems = []
    for table in page.all("table"):
        if table.attrs.get("role") in ("presentation", "none"):
            continue
        elements = [child for child in table.children if child.tag != "#document"]
        named = any((table.attrs.get(attribute) or "").strip() for attribute in ("aria-label", "aria-labelledby"))
        captioned = bool(elements) and elements[0].tag == "caption" and elements[0].text_content().strip()
        if not (named or captioned):
            headers = [" ".join(header.text_content().split()) for header in page.all("th") if _inside(header, table)][:3]
            problems.append(f"a table with no caption or name, headed {headers}")
    for cell in page.all("td"):
        if "scope" in cell.attrs:
            problems.append(f"<td {cell.attrs}> has a scope, which only a header cell can have")
    return problems


def _inside(element, ancestor):
    node = element.parent
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


@pytest.mark.parametrize("role, path", PAGES)
def test_every_table_is_named(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert table_problems(response.get_data(as_text=True)) == []


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_every_table_is_named_signed_out(client, make_user, path):
    make_user("admin")
    assert table_problems(client.get(path).get_data(as_text=True)) == []


def test_the_checker_sees_an_unnamed_table():
    """Without this, a parser that found no tables would pass every page above."""
    bare = "<table><tr><th>a</th></tr><tr><td scope='row'>1</td></tr></table>"
    named = ('<table><caption class="visually-hidden">Jobs</caption><tr><th>a</th></tr></table>'
             '<table aria-label="Jobs"><tr><th>a</th></tr></table><table role="presentation"><tr><td>1</td></tr></table>')

    problems = table_problems(bare)
    assert len(problems) == 2 and "no caption" in problems[0] and "scope" in problems[1]
    assert table_problems(named) == []


def label_cells(html, caption):
    """The first cell of every row of the table captioned `caption`, as (tag, scope, text)."""
    page = Page(html)
    table = next(table for table in page.all("table")
                 if any(child.tag == "caption" and child.text_content().strip() == caption for child in table.children))
    rows = [row for row in page.all("tr") if _inside(row, table)]
    cells = []
    for row in rows:
        first = next(child for child in row.children if child.tag in ("th", "td"))
        cells.append((first.tag, first.attrs.get("scope"), " ".join(first.text_content().split())))
    return cells


@pytest.mark.parametrize("role, path, caption", [
    ("visitor", f"/data/jobs/{job_id_of('matches_for_sample')}", "Job details"),
    ("visitor", "/explore/samples/0", "Sample details"),
    ("visitor", "/explore/functions/0", "Function details"),
    ("visitor", f"/data/result/{job_id_of('matches_for_sample')}", "Matching method statistics"),
    ("visitor", f"/data/result/{job_id_of('unique_blocks')}", "Report"),
    ("visitor", "/settings", "User information"),
    ("admin", "/admin/server", "Server parameters"),
])
def test_a_label_is_the_header_of_its_row(client, as_role, role, path, caption):
    as_role(role)
    cells = label_cells(client.get(path).get_data(as_text=True), caption)

    labels = [cell for cell in cells if cell[1] != "col" and cell[2] not in ("Field", "Entity", "Parameter")]
    assert labels, f"the {caption} table has no rows"
    assert all(tag == "th" and scope == "row" for tag, scope, _ in labels), labels
