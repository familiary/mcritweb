#!/usr/bin/python
"""Font Awesome icons are hidden from assistive technology.

An icon is a glyph of the icon font, drawn from `content:` as a private-use character, and
a screen reader reads it as nothing or as "unknown character": one result page carried 459
of them. What an icon means is said by the text beside it or by the name of the control it
sits in, so the icon itself is aria-hidden. A control whose only content is an icon is
named by an aria-label, not by its title, which assistive technology need not read; an
icon that says what the value after it is - a family or a sample in a job's description -
has that word beside it, visually hidden. An icon with an onclick is a control rather than
decoration, and wants to become a button, not to be hidden.
"""

import re
from pathlib import Path

import pytest
from testAccessibility import ADMIN_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES, Page

TEMPLATES = Path(__file__).resolve().parent.parent / "mcritweb" / "templates"
ICON_CLASS = re.compile(r"(?:^|\s)(?:fa|fas|far|fab|fa-solid|fa-regular|fa-brands)(?:\s|$)")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def exposed_icons(elements):
    """The icons a screen reader would reach, as their class attribute."""
    return [element.attrs.get("class") for element in elements
            if element.tag == "i" and ICON_CLASS.search(element.attrs.get("class") or "")
            and element.attrs.get("aria-hidden") != "true" and "onclick" not in element.attrs]


def unnamed_icon_controls(page):
    """Links and buttons whose only content is an icon, with no aria-label to name them."""
    found = []
    for control in page.all("a", "button"):
        if control.tag == "a" and not ({"href", "role"} & set(control.attrs)):
            continue
        icons = [child for child in control.children if child.tag == "i"]
        if icons and not control.text_content().strip() and not (control.attrs.get("aria-label") or "").strip():
            found.append(control.attrs.get("id") or control.attrs.get("title") or control.attrs.get("class"))
    return found


def unworded_icons(page):
    """Icons that say what the value after them is, in a job's description, the unique
    blocks report's requested samples and a PicHash's match counts, without the word."""
    found = []
    for element in page.elements:
        if element.tag != "i" or not element.attrs.get("title"):
            continue
        node, where = element.parent, None
        while node is not None and where is None:
            if "job-description" in node.classes() or node.tag == "details" \
                    or (node.tag == "td" and "matched" in element.attrs["title"]):
                where = node
            node = node.parent
        if where is None:
            continue
        siblings = element.parent.children
        after = siblings[siblings.index(element) + 1:siblings.index(element) + 2]
        if not (after and "visually-hidden" in after[0].classes() and after[0].text_content().strip()):
            found.append(element.attrs["title"])
    return found


def test_every_icon_in_the_templates_is_hidden():
    """Every template, rendered on a page here or not."""
    exposed = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for tag in re.findall(r"<i(?:\s[^>]*)?>", line):
                page = Page(tag + "</i>")
                if exposed_icons(page.elements):
                    exposed.append(f"{path.relative_to(TEMPLATES)}:{number}: {tag}")
    assert exposed == []


@pytest.mark.parametrize("role, path", [("visitor", path) for path in VISITOR_PAGES]
                         + [("contributor", path) for path in CONTRIBUTOR_PAGES]
                         + [("admin", path) for path in ADMIN_PAGES])
def test_no_page_exposes_an_icon(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    page = Page(response.get_data(as_text=True))
    assert exposed_icons(page.elements) == []
    assert unnamed_icon_controls(page) == []
    assert unworded_icons(page) == []


def test_the_checker_sees_what_it_claims_to():
    """Without this, a parser that saw nothing would pass every page above."""
    page = Page('<i class="fa-solid fa-bug"></i><i class="fas fa-book" aria-hidden="true"></i><i class="italic">x</i>'
                '<a href="/r" title="Rename"><i class="fa-solid fa-pen" aria-hidden="true"></i></a>'
                '<a href="/e" aria-label="Edit"><i class="fa-solid fa-pen" aria-hidden="true"></i></a>'
                '<table><tr><td class="job-description"><i class="fa-solid fa-bug" title="Family ID" aria-hidden="true"></i> x | '
                '<i class="fa-solid fa-virus" title="Sample ID" aria-hidden="true"></i><span class="visually-hidden">Sample</span> 3'
                '</td></tr></table>')

    assert exposed_icons(page.elements) == ["fa-solid fa-bug"]
    assert unnamed_icon_controls(page) == ["Rename"]
    assert unworded_icons(page) == ["Family ID"]
