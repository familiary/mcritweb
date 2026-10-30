#!/usr/bin/python
"""A control's name says which row it acts on.

An icon-only link or button is named by its `aria-label`, or by its `title`. Every row of
a match table carried the same "Filter by this function", every job row the same "Delete
job" and "Show result", every copy button "Copy to Clipboard": a list of links, or a walk
with Tab, read out a hundred identical entries that each go somewhere else. Two controls
may share a name only when they do the same thing.

Not checked above: links named by the id they show, which the column header around them
qualifies. The pagers have their own checks at the end: a page with several tables has a
pager under each, and each one's nav and links are named after its table.
"""

import collections
import pathlib
import re
import urllib.parse

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page, descendants

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def what_it_does(control):
    """Where a link goes, or what a button acts on. A dialog's opener says what it opens
    the dialog for in its other data-bs- attributes, the sample an edit button edits; a
    button whose handler finds its target from where it sits, `deleteSample(this)`, acts
    on its own row."""
    for attribute in ("href", "data-post", "data-clipboard-value"):
        if control.attrs.get(attribute):
            return control.attrs[attribute]
    if control.attrs.get("data-bs-target"):
        return tuple(sorted((name, value) for name, value in control.attrs.items()
                            if name.startswith("data-bs-") and name != "data-bs-toggle"))
    return id(control)


def left_out(control):
    """A pagination link, or a control in a tab pane not shown: the same user's menu sits
    in the "All users" pane and in the one for their role, never both on screen."""
    node = control.parent
    while node is not None:
        classes = node.classes()
        if "pagination" in classes or ("tab-pane" in classes and "active" not in classes):
            return True
        node = node.parent
    return False


def shared_names(html):
    """{name: number of different targets} for the labelled controls whose name repeats."""
    targets = collections.defaultdict(set)
    for control in Page(html).all("a", "button"):
        # a closed modal's controls are not reachable while another's are; a menu's items
        # are named by their text, which the menu's own toggle qualifies
        if control.in_hidden_container() or left_out(control):
            continue
        name = (control.attrs.get("aria-label") or "").strip()
        if not name and control.tag == "button" and not control.text_content().strip():
            name = (control.attrs.get("title") or "").strip()
        if name:
            targets[name].add(what_it_does(control))
    return {name: len(found) for name, found in targets.items() if len(found) > 1}


@pytest.mark.parametrize("role, path", [("visitor", path) for path in VISITOR_PAGES]
                         + [("contributor", path) for path in CONTRIBUTOR_PAGES]
                         + [("admin", path) for path in ADMIN_PAGES])
def test_no_two_labelled_controls_share_a_name_and_differ_in_what_they_do(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert shared_names(response.get_data(as_text=True)) == {}


def test_the_checker_sees_a_shared_name():
    """Without this, a page with no labelled controls would pass the test above."""
    page = ('<a aria-label="Filter" href="?funid=1"></a><a aria-label="Filter" href="?funid=2"></a>'
            '<a aria-label="Same" href="/x"></a><a aria-label="Same" href="/x"></a>'
            '<button title="Copy" data-clipboard-value="a"></button><button title="Copy" data-clipboard-value="b"></button>'
            '<button aria-label="Remove" onclick="drop(this)"></button><button aria-label="Remove" onclick="drop(this)"></button>'
            '<a aria-label="Edit" data-bs-target="#edit" data-bs-sampleid="1"></a><a aria-label="Edit" data-bs-target="#edit" data-bs-sampleid="2"></a>'
            '<a aria-label="API" data-bs-target="#api"></a><a aria-label="API" data-bs-target="#api"></a>'
            '<ul class="pagination"><li><a aria-label="Next page" href="?p=2"></a></li></ul>'
            '<ul class="pagination"><li><a aria-label="Next page" href="?q=2"></a></li></ul>')

    assert shared_names(page) == {"Filter": 2, "Copy": 2, "Remove": 2, "Edit": 2}


#: Pages with more than one table, hence more than one pager.
PAGES_WITH_SEVERAL_PAGERS = [
    "/explore/search?query=a",
    "/analyze/compare_versus",
    "/analyze/cross_compare?samples=0,1",
    "/analyze/unique_blocks?samples=0,1",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
    f"/data/result/{job_id_of('matches_for_sample')}",
    f"/data/result/{job_id_of('matches_for_sample')}?famid=1",
    f"/data/result/{job_id_of('matches_for_sample_vs')}?samid=3",
]


def pagers_of(html):
    """[(the nav's name, [(the link's name, its href), ...])] for each pager on the page."""
    pagers = []
    for nav in Page(html).all("nav"):
        links = [link for list_ in descendants(nav, "ul") if "pagination" in list_.classes()
                 for link in descendants(list_, "a")]
        if links:
            pagers.append((nav.attrs.get("aria-label"),
                           [(link.attrs.get("aria-label") or link.text_content().strip(), link.attrs.get("href"))
                            for link in links]))
    return pagers


@pytest.mark.parametrize("path", PAGES_WITH_SEVERAL_PAGERS)
def test_every_pager_on_a_page_is_named_after_its_table(client, as_role, path):
    """Each table's pager carried a nav named "Pagination" and links named "Next page",
    "Previous page" and so on: a list of the page's landmarks or links read out the same
    names several times over, each going to a different table."""
    as_role("visitor")
    pagers = pagers_of(client.get(path).get_data(as_text=True))
    assert len(pagers) > 1, "the page has no second pager to tell apart"

    nav_names = [name for name, _ in pagers]
    assert len(set(nav_names)) == len(nav_names), nav_names

    targets = collections.defaultdict(set)
    for _, links in pagers:
        for name, href in links:
            if href:
                targets[name].add(href)
    assert {name for name, found in targets.items() if len(found) > 1} == set()


@pytest.mark.parametrize("path, expected", [
    (f"/data/result/{job_id_of('matches_for_sample')}", {"famp", "libp", "funp"}),
    (f"/data/result/{job_id_of('matches_for_sample')}?famid=1", {"famid", "samp", "funp"}),
    (f"/data/linkhunt/{job_id_of('matches_for_sample')}", {"clup", "funp"}),
])
def test_naming_a_pager_adds_no_parameter_to_its_urls(client, as_role, path, expected):
    """The name is given to the widget beside the arguments that become URL parameters,
    so it must not turn up in the address a pager link leads to."""
    as_role("visitor")
    pagers = pagers_of(client.get(path).get_data(as_text=True))

    keys = {key for _, links in pagers for _, href in links if href
            for key in urllib.parse.parse_qs(urllib.parse.urlparse(href).query)}
    assert keys == expected


def names_sent_twice(html):
    """The icon-only links and buttons whose title says what their aria-label says. A
    Bootstrap tooltip is left out: it is built from the title, which it needs."""
    return [control.attrs["aria-label"] for control in Page(html).all("a", "button")
            if control.attrs.get("title") and control.attrs.get("title") == control.attrs.get("aria-label")
            and control.attrs.get("data-bs-toggle") != "tooltip"]


@pytest.mark.parametrize("role, path", [("visitor", path) for path in VISITOR_PAGES]
                         + [("contributor", path) for path in CONTRIBUTOR_PAGES]
                         + [("admin", path) for path in ADMIN_PAGES])
def test_no_control_sends_its_name_twice(client, as_role, role, path):
    """A hundred-row match table named each row's filter link in an aria-label and again,
    word for word, in a title that only a mouse reads. page_loading.js gives the tooltip once
    the page is painted, from the aria-label, so the markup says the name once."""
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert names_sent_twice(response.get_data(as_text=True)) == []


def test_the_checker_sees_a_name_sent_twice():
    """Without this, a page with no such control would pass the test above."""
    page = ('<a aria-label="Filter by function 1" title="Filter by function 1" href="?funid=1"></a>'
            '<button aria-label="Delete job 2" title="Delete job 2"></button>'
            '<a aria-label="First page of matches" title="First page" href="?p=1"></a>'
            '<a aria-label="Documentation" href="/help"></a>')

    assert names_sent_twice(page) == ["Filter by function 1", "Delete job 2"]


def test_the_checker_leaves_a_bootstrap_tooltip_its_title():
    page = ('<button aria-label="Copy" title="Copy" data-bs-toggle="tooltip" data-clipboard-value="a"></button>'
            '<a aria-label="Filter" title="Filter" href="?funid=1"></a>')

    assert names_sent_twice(page) == ["Filter"]


def test_the_icon_tooltips_have_no_file_of_their_own(client, as_role):
    """The tooltips of the icon-only controls were icon_titles.js, a file of its own queued
    on every page. Each file the queue adds is one more ordered request before the page's
    scripts have run: on the build of all the pull requests the index page took 35 to 45 ms
    of blocking time in Lighthouse's mobile run against the base's 8 to 15, and folding the
    every-page files into the ones the base already queued brought it back to 10. The code
    rides in page_loading.js, which every page queues already."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    assert "/static/icon_titles.js" not in page
    assert not (PACKAGE / "static" / "icon_titles.js").exists()
    assert "titleIt(" in (PACKAGE / "static" / "page_loading.js").read_text()
    assert re.search(r"<script( nonce=\"[^\"]*\")?>later\(\"/static/page_loading\.js(\?v=\w+)?\"\)</script>", page)
