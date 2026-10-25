#!/usr/bin/python
"""Every page starts its outline with one <h1>, and no two pages share a title.

Seven pages had no <h1> - the settings, users, submit, import, export and register pages,
and the search page until something was searched - so a screen reader's heading list had
no start there, and the settings and register pages began at <h3>. A dialog's title was an
<h5> under the page's <h2>s. The three comparison pickers were all titled "Compare", and a
link hunt carried the title of the result page it comes from, so their tabs and history
entries could not be told apart. Where the design shows no heading, the <h1> is visually
hidden.
"""

import re

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page

PAGES = ([("visitor", path) for path in VISITOR_PAGES + ["/explore/search"]]
         + [("contributor", path) for path in CONTRIBUTOR_PAGES] + [("admin", path) for path in ADMIN_PAGES])

#: one page of each kind, whose titles a reader tells apart by
DISTINCT_PAGES = [
    "/", "/explore/families", "/explore/samples", "/explore/functions", "/explore/search",
    "/explore/families/1", "/explore/samples/0", "/explore/functions/0", "/explore/statistics",
    "/data/jobs?active=getMatchesForSample", "/analyze/query", "/analyze/compare", "/analyze/compare_versus",
    "/analyze/cross_compare", "/analyze/unique_blocks", "/settings", "/help",
    f"/data/jobs/{job_id_of('matches_for_sample')}", f"/data/result/{job_id_of('matches_for_sample')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}", "/data/matches/function/84/943",
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def heading_problems(html):
    page = Page(html)
    problems = []
    first_level = len(page.all("h1"))
    if first_level != 1:
        problems.append(f"{first_level} <h1>, expected one")
    for heading in page.all("h1", "h2", "h3", "h4", "h5", "h6"):
        if heading.parent is not None and heading.parent.tag == "span":
            problems.append(f"<{heading.tag}> {heading.text_content().strip()!r} inside a <span>")
    for title in [element for element in page.elements if "modal-title" in element.classes()]:
        # the dialog opens over the page, whose <h1> is above its own title
        if title.tag != "h2":
            problems.append(f"a dialog titled by <{title.tag}> {title.text_content().strip()!r}")
    return problems


@pytest.mark.parametrize("role, path", PAGES)
def test_one_h1_and_dialog_titles_one_level_below_it(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert heading_problems(response.get_data(as_text=True)) == []


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_the_signed_out_pages_have_one_h1(client, make_user, path):
    make_user("admin")
    assert heading_problems(client.get(path).get_data(as_text=True)) == []


def test_the_first_run_registration_has_one_h1(client):
    """With no user yet, /register also asks for the backend and the operation mode."""
    assert heading_problems(client.get("/register").get_data(as_text=True)) == []


def test_the_checker_sees_what_it_claims_to():
    """Without this, a parser that saw no headings would pass every page above."""
    page = ('<h3>x</h3><span><h2>y</h2></span>'
            '<div class="modal"><h5 class="modal-title" id="t">Edit</h5></div>')

    assert heading_problems(page) == ["0 <h1>, expected one", "<h2> 'y' inside a <span>", "a dialog titled by <h5> 'Edit'"]


def test_no_two_kinds_of_page_share_a_title(client, as_role):
    as_role("visitor")
    titles = {}
    for path in DISTINCT_PAGES:
        page = client.get(path, follow_redirects=True).get_data(as_text=True)
        titles[path] = " ".join(re.search(r"<title>(.*?)</title>", page, re.S).group(1).split())

    shared = {title: [path for path in titles if titles[path] == title] for title in set(titles.values())}
    assert {title: paths for title, paths in shared.items() if len(paths) > 1} == {}
