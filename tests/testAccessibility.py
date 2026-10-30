#!/usr/bin/python
"""The markup-level checks Lighthouse's Accessibility and SEO audits make.

Each page is rendered offline against the captured corpus and checked for what can be
decided from the markup alone: a `lang` on <html>, one <main> landmark, a meta
description, an `alt` and intrinsic size on every image, an accessible name on every
link and button, text in every table header, a label on every form control, and no skipped
heading level. Colour contrast and anything else that needs layout is not checked here.

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
    # an exact ID hit adds the badge column to each table
    "/explore/search?query=1",
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
    # with samples selected, both list them with a remove control
    "/analyze/cross_compare?samples=0,1",
    "/analyze/unique_blocks?samples=0,1",
    "/settings",
    "/help",
    f"/data/jobs/{job_id_of('matches_for_sample')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
] + [f"/data/result/{job_id_of(report)}" for report in
     ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"]] + [
    # one family's, one sample's and one function's matches, the result pages the plain
    # URL does not reach (function 2 is matched in that report)
    f"/data/result/{job_id_of('matches_for_sample')}?famid=1",
    f"/data/result/{job_id_of('matches_for_sample_vs')}?samid=3",
    f"/data/result/{job_id_of('matches_for_sample')}?funid=2",
]
CONTRIBUTOR_PAGES = ["/data/submit", "/data/export"]
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

    def text_content(self):
        # what a screen reader can reach: aria-hidden content is skipped, a title is not text
        if self.attrs.get("aria-hidden") == "true":
            return ""
        return "".join([self.text] + [child.text_content() for child in self.children])

    def titles(self):
        found = [self.attrs["title"]] if self.attrs.get("title") else []
        return found + [title for child in self.children for title in child.titles()]

    def visible_words(self):
        """The words the element shows, its text and its children's, as a set of lowercase
        words without punctuation: what is visually hidden or hidden from assistive
        technology is left out, and so is a lone symbol like the x of a close button.
        A word check, so it does not see the order of the words."""
        hidden_style = re.sub(r"\s", "", self.attrs.get("style") or "").lower()
        if (self.classes() & {"visually-hidden", "visually-hidden-focusable", "sr-only", "d-none"}
                or self.attrs.get("aria-hidden") == "true" or "hidden" in self.attrs or "display:none" in hidden_style):
            return []
        words = re.findall(r"\w+", self.text.lower())
        for child in self.children:
            words += child.visible_words()
        return words

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


def descendants(element, tag):
    found = []
    for child in element.children:
        if child.tag == tag:
            found.append(child)
        found.extend(descendants(child, tag))
    return found


def in_named_group(element):
    """Inside a <fieldset> with a <legend>, or inside an element with a group role and a name."""
    node = element.parent
    while node is not None:
        if node.tag == "fieldset" and any(child.tag == "legend" for child in node.children):
            return True
        if node.attrs.get("role") in ("group", "radiogroup") and (node.attrs.get("aria-label") or node.attrs.get("aria-labelledby")):
            return True
        node = node.parent
    return False


def problems_of(html):
    page = Page(html)
    problems = []
    html_tags = page.all("html")
    if not html_tags or not html_tags[0].attrs.get("lang"):
        problems.append("<html> has no lang")
    if len(page.all("main")) != 1:
        problems.append(f"{len(page.all('main'))} <main> landmarks, expected one")
    descriptions = [meta for meta in page.all("meta") if meta.attrs.get("name") == "description"]
    if not any((meta.attrs.get("content") or "").strip() for meta in descriptions):
        problems.append("no meta description")
    # a second one is not a fallback: crawlers take either, and a page's own description
    # (the {% block description %} in base.html) must be the only one
    if len(descriptions) > 1:
        problems.append(f"{len(descriptions)} meta descriptions, expected one")
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
        # WCAG 2.5.3, Lighthouse's label-content-name-mismatch: an aria-label replaces the
        # text, so a word the control shows but its label leaves out is one a voice user
        # says and the control does not answer to
        label = re.findall(r"\w+", (element.attrs.get("aria-label") or "").lower())
        missing = [word for word in element.visible_words() if label and word not in label]
        if missing:
            problems.append(f"<{element.tag} {element.attrs}> shows {missing} but its aria-label leaves it out")

    for header in page.all("th"):
        # td-has-header reads a column's header as text. An icon's title is its tooltip, which
        # assistive technology may skip, so only text inside the cell counts; a visually-hidden
        # span is text, unless it sits inside the aria-hidden icon.
        if not header.text_content().strip():
            titles = [title for child in header.children for title in child.titles()]
            problems.append(f"<th {header.attrs}> has no text" + (f", only an icon titled {titles}" if titles else ""))
    for cell in page.all("td"):
        # a value with its filter icon is two 24px targets: stacked, they sit a 21.6px line
        # apart, which WCAG 2.5.8 refuses. style.css keeps .id and .family cells on one line
        links = [child for child in cell.children if child.tag == "a"]
        filters = [link for link in links if (link.attrs.get("aria-label") or "").startswith("Filter by ")]
        classes = (cell.attrs.get("class") or "").split()
        if len(links) > 1 and filters and not {"id", "family"} & set(classes):
            problems.append(f"<td {cell.attrs}> holds a link and its filter link but may wrap them")
    for table in page.all("table"):
        # and a table whose header row is made of <td> leaves every cell without one. axe
        # applies td-has-header from three rows and three columns up and checks each cell;
        # this catches the plainest case, such a table with no header cell at all
        rows = descendants(table, "tr")
        if len(rows) >= 3 and max(len(descendants(row, "td")) for row in rows) >= 3 \
                and not descendants(table, "th"):
            problems.append(f"<table {table.attrs}> of {len(rows)} rows has no header cell")
    ids_labelled = {label.attrs.get("for") for label in page.all("label")}
    for label in page.all("label"):
        # a label with no `for` and no field inside names nothing: the filter forms captioned
        # a row of fields with one, and a click on it did nothing
        if not label.attrs.get("for") and not any(descendants(label, tag) for tag in ("input", "select", "textarea")):
            problems.append(f"<label {label.attrs}> {label.text_content().strip()!r} names no field")
    for control in page.all("input", "select", "textarea"):
        attrs = control.attrs
        if (attrs.get("type") or "").lower() in UNLABELLED_INPUT_TYPES or "hidden" in attrs:
            continue
        # a description that is the field itself reads its own value out a second time
        if attrs.get("id") and attrs.get("id") in (attrs.get("aria-describedby") or "").split():
            problems.append(f"<{control.tag} {attrs}> is described by itself")
        # an aria-label replaces the visible label as the name (WCAG 2.5.3): the export page's
        # field showed one sentence and answered to another
        if (attrs.get("aria-label") or "").strip() and attrs.get("id") in ids_labelled:
            problems.append(f"<{control.tag} {attrs}> has a visible label and an aria-label that replaces it")
        # a placeholder is not a name: it goes once the field holds a value, and so does
        # whatever it was telling apart
        if any((attrs.get(attr) or "").strip() for attr in ("aria-label", "aria-labelledby")):
            continue
        if attrs.get("id") in ids_labelled:
            continue
        node = control.parent
        while node is not None and node.tag != "label":
            node = node.parent
        if node is None:
            problems.append(f"<{control.tag} {attrs}> has no label")
    for radio in page.all("input"):
        # a radio is one answer to a question, and the group says what the question is: "Dumped,
        # radio button" alone did not
        if (radio.attrs.get("type") or "").lower() == "radio" and not in_named_group(radio):
            problems.append(f"<input {radio.attrs}> is a radio outside a named group")
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
    # the jobs list redirects to the URL naming its tab (#132); the page is behind it
    assert_accessible(client.get(path, follow_redirects=path == "/data/jobs"))


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
           '<h1>a</h1><h3>b</h3>'
           '<table><tr><th id="empty"></th>'
           '<th id="icon"><i class="fa-solid fa-bug" title="Family ID"></i></th>'
           # an unclosed icon swallows the span after it, and aria-hidden hides the span with it
           '<th id="swallowed"><i class="fa-solid fa-bug" title="Family ID" aria-hidden="true">'
           '<span class="visually-hidden">Family ID</span></th>'
           '<th id="named"><i class="fa-solid fa-virus" aria-hidden="true"></i>'
           '<span class="visually-hidden">Sample ID</span></th>'
           '<th id="text">Name</th></tr></table></body></html>')
    problems = "\n".join(problems_of(bad))
    for expected in ("no lang", "0 <main>", "no meta description", "has no alt", "no width/height",
                     "<a ", "<input ", "<h1> is followed by <h3>",
                     "not crawlable", "aria-label but no href or role"):
        assert expected in problems, expected
    shown = ('<!doctype html><html lang="en"><body><main><button aria-label="Analyze sample">'
             '<i aria-hidden="true">x</i>&nbsp;3</button><button aria-label="Edit sample 3">'
             '<i aria-hidden="true">x</i><span class="visually-hidden">Edit</span></button>'
             '<a href="/s" aria-label="Open sample 3">3,</a><button aria-label="Close">&times;</button>'
             '<button aria-label="Open"><span class="d-none">hidden</span>Open</button></main></body></html>')
    mismatches = [problem for problem in problems_of(shown) if "aria-label leaves it out" in problem]
    assert len(mismatches) == 1 and "Analyze sample" in mismatches[0], mismatches
    fields = ('<!doctype html><html lang="en"><body><main><input id="by-placeholder" placeholder="min score">'
              '<label for="self">Score</label><input id="self" aria-describedby="self">'
              '<label for="named">Score</label><input id="named" placeholder="0-100">'
              '<label class="col-6">Filter results to</label><label>Wrapped <input id="wrapped"></label>'
              '<label for="renamed">Sample IDs</label><input id="renamed" aria-label="Export these">'
              '<input type="radio" name="loose" id="loose"><label for="loose">Loose</label>'
              '<fieldset><legend>Mode</legend><input type="radio" name="kept" id="kept"><label for="kept">Kept</label></fieldset>'
              '</main></body></html>')
    field_problems = "\n".join(problems_of(fields))
    assert "'Filter results to' names no field" in field_problems
    assert "'Wrapped' names no field" not in field_problems and "'id': 'wrapped'" not in field_problems
    assert "'id': 'renamed'" in field_problems and "an aria-label that replaces it" in field_problems
    assert "'id': 'loose'" in field_problems and "a radio outside a named group" in field_problems
    assert "'id': 'kept'" not in field_problems
    assert "'id': 'by-placeholder'" in field_problems and "has no label" in field_problems
    assert "'id': 'self'" in field_problems and "is described by itself" in field_problems
    assert "'id': 'named'" not in field_problems
    twice = ('<!doctype html><html lang="en"><head><meta name="description" content="a">'
             '<meta name="description" content="b"></head><body><main></main></body></html>')
    assert "2 meta descriptions, expected one" in problems_of(twice)


    headers = [problem for problem in problems_of(bad) if problem.startswith("<th ")]
    for caught in ("empty", "icon", "swallowed"):
        assert [problem for problem in headers if f"'id': '{caught}'" in problem], caught
    for passed in ("named", "text"):
        assert not [problem for problem in headers if f"'id': '{passed}'" in problem], passed
    assert "only an icon titled ['Family ID']" in "\n".join(headers)
    grid = ('<!doctype html><html lang="en"><body><main><table id="grid">'
            + '<tr><td>#</td><td>family</td><td>ID</td></tr>' * 3 + '</table></main></body></html>')
    assert [problem for problem in problems_of(grid) if "has no header cell" in problem]
    stacked = ('<!doctype html><html lang="en"><body><main><table><tr><td><a href="/f/1">a</a>&nbsp;'
               '<a aria-label="Filter by this family" href="?famid=1"><i></i></a></td></tr></table></main></body></html>')
    assert [problem for problem in problems_of(stacked) if "may wrap them" in problem]
    assert not [problem for problem in problems_of(stacked.replace("<td>", '<td class="family">'))
                if "may wrap them" in problem]
    assert not [problem for problem in problems_of(grid.replace("<td>#</td>", "<th>#</th>", 1))
                if "has no header cell" in problem]


def test_an_id_hit_brings_the_badge_column(client, as_role):
    """/explore/search?query=1 is in the page list for the "Match" column an exact ID hit
    adds; if the fixture stops producing the hit, that column's header goes unchecked."""
    as_role("visitor")
    page = Page(client.get("/explore/search?query=1").get_data(as_text=True))
    assert "Match" in [header.text_content().strip() for header in page.all("th")]


def test_the_static_images_declare_their_own_size():
    """The width/height attributes reserve the image's box before it arrives, so they must
    be the file's pixel size: the CSS fixes the height and derives the width from them."""
    mcritweb = Path(__file__).resolve().parent.parent / "mcritweb"
    base = (mcritweb / "templates" / "base.html").read_text()
    for name in ("d20_mcrit_cabaret.png", "fkie_190x52.gif"):
        # the logos go through themed_img (#70), which writes the <img> from its arguments:
        # the light file first, the dark variant second, then the attributes
        calls = re.findall(r"themed_img\(url_for\('static', filename='" + re.escape(name)
                           + r"'\), url_for\('static', filename='([^']+)'\)([^)]*)\)", base)
        assert calls, f"base.html no longer shows {name}"
        width, height = Image.open(mcritweb / "static" / name).size
        for dark_name, attributes in calls:
            assert f'width="{width}"' in attributes and f'height="{height}"' in attributes, attributes
            # the <source> swaps the file under the same box, so the variant must match it
            assert Image.open(mcritweb / "static" / dark_name).size == (width, height), dark_name
