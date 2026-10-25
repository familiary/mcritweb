#!/usr/bin/python
"""The landmarks a screen reader or keyboard user moves by.

Every page made the keyboard cross two logos, four menus and the help link before its own
content, and had no way past them: a skip link is now the first stop, to a <main> that
takes focus. The navbar and the jobs page's category bar were two unnamed navigation
landmarks; the breadcrumb's name said "navigation" once more than a screen reader does.
The current page of a pager, of the compare pickers' tabs and of the job categories was
shown by colour alone and is marked `aria-current="page"`, and each pager is a named
navigation landmark. The search boxes are search landmarks. tests/testPageStructureBrowser.py
drives the skip link.
"""

import re

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page

PAGES = ([("visitor", path) for path in VISITOR_PAGES] + [("contributor", path) for path in CONTRIBUTOR_PAGES]
         + [("admin", path) for path in ADMIN_PAGES])
FOCUSABLE = {"a", "button", "input", "select", "textarea", "summary"}


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def ancestors(element):
    node = element.parent
    while node is not None:
        yield node
        node = node.parent


def landmark_problems(html):
    page = Page(html)
    problems = []
    focusable = [element for element in page.elements if element.tag in FOCUSABLE
                 and (element.tag != "a" or "href" in element.attrs)
                 and (element.attrs.get("type") or "") != "hidden" and "hidden" not in element.attrs]
    first = focusable[0] if focusable else None
    if first is None or first.attrs.get("href") != "#content" or "skip-link" not in first.classes():
        problems.append(f"the first focusable element is {first.tag if first else None} {first.attrs if first else ''}, not the skip link")
    main = page.all("main")
    if not main or main[0].attrs.get("id") != "content" or main[0].attrs.get("tabindex") != "-1":
        problems.append("<main> is not the skip link's focusable target")
    for nav in page.all("nav"):
        label = (nav.attrs.get("aria-label") or "").strip()
        if not label:
            problems.append(f"an unnamed <nav> {nav.attrs}")
        elif "navigation" in label.lower():
            problems.append(f"<nav aria-label={label!r}> repeats its role in its name")
    for pager in [element for element in page.all("ul") if "pagination" in element.classes()]:
        if not any(node.tag == "nav" for node in ancestors(pager)):
            problems.append("a pager outside a <nav>")
        current = [element for element in page.all("a") if element.attrs.get("aria-current") == "page" and pager in ancestors(element)]
        active = [element for element in page.all("li") if "active" in element.classes() and element.parent is pager]
        if len(current) != len(active):
            problems.append(f"a pager with {len(active)} current page, {len(current)} marked aria-current")
    for form in page.all("form"):
        if form.attrs.get("data-live-search") or re.search(r"search", form.attrs.get("id", ""), re.I) \
                or any(field.attrs.get("name") in ("query", "Search", "query_a", "query_b")
                       for field in page.all("input") if form in ancestors(field)):
            if not any(node.tag == "search" for node in ancestors(form)):
                problems.append(f"a search form outside a <search>: {form.attrs}")
    return problems


@pytest.mark.parametrize("role, path", PAGES)
def test_the_landmarks_are_named_and_reachable(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert landmark_problems(response.get_data(as_text=True)) == []


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_the_signed_out_pages_have_the_skip_link_too(client, make_user, path):
    make_user("admin")
    assert landmark_problems(client.get(path).get_data(as_text=True)) == []


def test_the_checker_sees_what_it_claims_to():
    """Without this, a parser that saw nothing would pass every page above."""
    page = ('<body><header><a href="/">home</a><nav><a href="/x">x</a></nav>'
            '<nav aria-label="Pager navigation"></nav></header><main>'
            '<ul class="pagination"><li class="page-item active"><a href="?p=1">1</a></li></ul>'
            '<form><input name="query"></form></main></body>')

    assert landmark_problems(page) == [
        "the first focusable element is a {'href': '/'}, not the skip link",
        "<main> is not the skip link's focusable target",
        "an unnamed <nav> {}",
        "<nav aria-label='Pager navigation'> repeats its role in its name",
        "a pager outside a <nav>",
        "a pager with 1 current page, 0 marked aria-current",
        "a search form outside a <search>: {}",
    ]


@pytest.mark.parametrize("path, current", [
    ("/analyze/compare", "1vsN"), ("/analyze/compare_versus", "1vs1"), ("/analyze/cross_compare", "Cross"),
    ("/analyze/unique_blocks", "Blocks"), ("/analyze/query", "Query"),
    ("/data/jobs?active=getMatchesForSample", "getMatchesForSample"),
    ("/data/jobs?active=getUniqueBlocks", "Blocks"),
])
def test_the_current_tab_or_category_is_marked(client, as_role, path, current):
    as_role("visitor")
    page = Page(client.get(path).get_data(as_text=True))

    marked = [" ".join(link.text_content().split()) for link in page.all("a")
              if link.attrs.get("aria-current") == "page" and not any("pagination" in node.classes() for node in ancestors(link))]
    assert [text.split(" (")[0] for text in marked] == [current]


def test_the_skip_link_is_hidden_until_it_has_focus():
    """Out of the flow, and in the same place whether shown or not: only its clip changes,
    so its appearing moves nothing, itself included."""
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "mcritweb" / "static" / "style.css").read_text()

    placed = re.search(r"\.skip-link\s*\{([^}]*)\}", css)
    hidden = re.search(r"\.skip-link:where\(:not\(:focus-within, :active\)\)\s*\{([^}]*)\}", css)
    assert placed and "position: fixed" in placed.group(1)
    assert hidden and "clip-path: inset(50%)" in hidden.group(1)
    assert not re.search(r"\b(position|top|left|margin|padding|border)\s*:", hidden.group(1)), hidden.group(1)


def test_the_job_page_s_pagers_are_named(client, as_role):
    """A result page has several pagers; each is a navigation landmark of its own."""
    as_role("visitor")
    page = Page(client.get(f"/data/result/{job_id_of('matches_for_sample')}").get_data(as_text=True))

    pagers = [nav for nav in page.all("nav") if nav.attrs.get("aria-label") == "Pagination"]
    assert len(pagers) >= 2
