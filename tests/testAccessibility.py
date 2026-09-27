#!/usr/bin/python
"""The markup-level checks Lighthouse's Accessibility and SEO audits make.

Each page is rendered offline against the captured corpus and checked for what can be
decided from the markup alone: a `lang` on <html>, one <main> landmark, a meta
description, an `alt` and intrinsic size on every image, an accessible name on every
link and button, a label on every form control, and no skipped heading level.
Colour contrast and anything else that needs layout is not checked here.

Headings inside a modal or a dropdown menu are left out of the order check: they are
hidden when the page loads, which is when the audit reads the page.
"""

import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fixtureData import job_id_of
from PIL import Image

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
UNLABELLED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
HIDDEN_CONTAINERS = {"modal", "dropdown-menu"}

ANONYMOUS_PAGES = ["/login", "/register"]
VISITOR_PAGES = [
    "/",
    "/explore/families",
    "/explore/samples",
    "/explore/functions",
    "/explore/search?query=a",
    "/data/jobs",
    "/explore/families/0",
    "/explore/samples/0",
    "/explore/functions/0",
    "/explore/statistics",
    "/data/matches/function/0/1",
    "/analyze/query",
    "/analyze/compare",
    "/analyze/compare_versus",
    "/analyze/cross_compare",
    "/analyze/unique_blocks",
    "/settings",
    "/help",
    f"/data/jobs/{job_id_of('matches_for_sample')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
] + [f"/data/result/{job_id_of(report)}" for report in
     ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"]]
CONTRIBUTOR_PAGES = ["/data/submit"]
ADMIN_PAGES = ["/admin/server", "/admin/users/", "/data/import", f"/data/result/{job_id_of('maintenance_rebuild_index')}"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Element:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.text = ""
        self.children = []

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def in_hidden_container(self):
        node = self.parent
        while node is not None:
            if node.classes() & HIDDEN_CONTAINERS:
                return True
            node = node.parent
        return False

    def accessible_name(self):
        # axe's button-name documents title as a name, its link-name does not
        name_attrs = ("aria-label", "aria-labelledby", "title") if self.tag == "button" else ("aria-label", "aria-labelledby")
        for attr in name_attrs:
            if (self.attrs.get(attr) or "").strip():
                return self.attrs[attr]
        parts = [self.text]
        for child in self.children:
            if child.tag == "img":
                parts.append(child.attrs.get("alt") or "")
            else:
                parts.append(child.accessible_name())
        return " ".join(part for part in parts if part).strip()


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", [], None)
        self.stack = [self.root]
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(element)
        self.elements.append(element)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        # tolerate unbalanced markup: close up to the nearest matching open tag
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        self.stack[-1].text += data

    def all(self, *tags):
        return [element for element in self.elements if element.tag in tags]


def problems_of(html):
    page = Page(html)
    problems = []
    html_tags = page.all("html")
    if not html_tags or not html_tags[0].attrs.get("lang"):
        problems.append("<html> has no lang")
    if len(page.all("main")) != 1:
        problems.append(f"{len(page.all('main'))} <main> landmarks, expected one")
    if not any(meta.attrs.get("name") == "description" and (meta.attrs.get("content") or "").strip()
               for meta in page.all("meta")):
        problems.append("no meta description")
    for img in page.all("img"):
        if "alt" not in img.attrs:
            problems.append(f"<img src={img.attrs.get('src')}> has no alt")
        # a rendered match diagram has no fixed size to declare; the static logos do
        if (img.attrs.get("src") or "").startswith("/static/") and not (img.attrs.get("width") and img.attrs.get("height")):
            problems.append(f"<img src={img.attrs.get('src')}> has no width/height")
    for element in page.all("a", "button"):
        if element.tag == "a" and "href" not in element.attrs:
            # without href an <a> has no role, and a name on a roleless element is prohibited
            if "onclick" in element.attrs:
                problems.append(f"<a {element.attrs}> navigates by onclick alone, not crawlable")
            if "aria-label" in element.attrs and "role" not in element.attrs:
                problems.append(f"<a {element.attrs}> has aria-label but no href or role")
            continue
        if not element.accessible_name():
            problems.append(f"<{element.tag} {element.attrs}> has no accessible name")
    ids_labelled = {label.attrs.get("for") for label in page.all("label")}
    for control in page.all("input", "select", "textarea"):
        attrs = control.attrs
        if (attrs.get("type") or "").lower() in UNLABELLED_INPUT_TYPES or "hidden" in attrs:
            continue
        if any((attrs.get(attr) or "").strip() for attr in ("aria-label", "aria-labelledby", "placeholder")):
            continue
        if attrs.get("id") in ids_labelled:
            continue
        node = control.parent
        while node is not None and node.tag != "label":
            node = node.parent
        if node is None:
            problems.append(f"<{control.tag} {attrs}> has no label")
    previous = 0
    for heading in page.all("h1", "h2", "h3", "h4", "h5", "h6"):
        if heading.in_hidden_container():
            continue
        level = int(heading.tag[1])
        if previous and level > previous + 1:
            problems.append(f"<h{previous}> is followed by <{heading.tag}>")
        previous = level
    return problems


def assert_accessible(response):
    assert response.status_code == 200, response.status_code
    problems = problems_of(response.get_data(as_text=True))
    assert not problems, "\n  ".join([response.request.path] + problems)


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_anonymous_pages(client, make_user, path):
    make_user("admin")
    assert_accessible(client.get(path))


def test_first_run_registration(client):
    """With no user yet, /register also asks for the backend and the operation mode."""
    assert_accessible(client.get("/register"))


@pytest.mark.parametrize("path", VISITOR_PAGES)
def test_visitor_pages(client, as_role, path):
    as_role("visitor")
    assert_accessible(client.get(path))


@pytest.mark.parametrize("path", CONTRIBUTOR_PAGES)
def test_contributor_pages(client, as_role, path):
    as_role("contributor")
    assert_accessible(client.get(path))


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages(client, as_role, path):
    as_role("admin")
    assert_accessible(client.get(path))


def test_the_checker_catches_what_it_claims_to():
    """Without this, a parser that saw nothing would pass every page above."""
    bad = ('<!doctype html><html><head></head><body><img src="/static/x.png">'
           '<a href="/r"><i class="fa-solid fa-trash"></i></a><input name="q">'
           '<a onclick="go()">x</a><a aria-label="Last page"></a>'
           '<h1>a</h1><h3>b</h3></body></html>')
    problems = "\n".join(problems_of(bad))
    for expected in ("no lang", "0 <main>", "no meta description", "has no alt", "no width/height",
                     "<a ", "<input ", "<h1> is followed by <h3>",
                     "not crawlable", "aria-label but no href or role"):
        assert expected in problems, expected


def test_the_static_images_declare_their_own_size():
    """The width/height attributes reserve the image's box before it arrives, so they must
    be the file's pixel size: the CSS fixes the height and derives the width from them."""
    mcritweb = Path(__file__).resolve().parent.parent / "mcritweb"
    base = (mcritweb / "templates" / "base.html").read_text()
    for name in ("d20_mcrit_cabaret.png", "fkie_190x52.gif"):
        tags = re.findall(r"<img[^>]*filename='" + re.escape(name) + r"'[^>]*>", base)
        assert tags, f"base.html no longer shows {name}"
        width, height = Image.open(mcritweb / "static" / name).size
        for tag in tags:
            assert f'width="{width}"' in tag and f'height="{height}"' in tag, tag
