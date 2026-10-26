#!/usr/bin/python
"""The white space of a page is dropped before the page is gzipped.

A result page was a quarter indentation and blank lines, about 1.6 KB of the 16 KB it
weighs gzipped, in a first round trip that carries about 14.6 KB (ADR-0022). What is
checked here is that the page is lighter, and that it is the same page: the same tags,
attributes and text with the white space read the way a browser reads it, and what a
browser shows as written - `pre`, `textarea`, the text of a script, an attribute's line
breaks - as it was. testThinPagesBrowser.py compares the pages as drawn.
"""

import gzip
import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES

from mcritweb import thin_pages
from mcritweb.thin_pages import thin, thin_script

#: the pages the accessibility checks render, each shown to a role that may open it
PAGES = [("visitor", path) for path in VISITOR_PAGES] + [("contributor", path) for path in CONTRIBUTOR_PAGES] \
    + [("admin", path) for path in ADMIN_PAGES]
RESULT = f"/data/result/{job_id_of('matches_for_sample')}"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Events(HTMLParser):
    """A page as a list of what the browser builds: tags with their attributes, and text
    as a browser reads it - with each run of white space one space, except where it is
    shown as it is. The text of a script is kept apart, as ("script", text)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.events = []
        self.depth = {"pre": 0, "textarea": 0, "style": 0}
        self.script = None

    def handle_starttag(self, tag, attrs):
        self.events.append(("start", tag, tuple(attrs)))
        if tag in self.depth:
            self.depth[tag] += 1
        if tag == "script":
            self.script = "code" if dict(attrs).get("type") in (None, "module", "text/javascript") else "data"

    def handle_endtag(self, tag):
        self.events.append(("end", tag))
        if tag in self.depth:
            self.depth[tag] -= 1
        if tag == "script":
            self.script = None

    def handle_data(self, data):
        self.text(data)

    def handle_entityref(self, name):
        self.text(f"&{name};")

    def handle_charref(self, name):
        self.text(f"&#{name};")

    def handle_comment(self, data):
        self.events.append(("comment", data))

    def handle_decl(self, decl):
        self.events.append(("decl", decl))

    def text(self, data):
        kind = "text"
        if self.script:
            kind = f"script-{self.script}"
        elif not any(self.depth.values()):
            data = re.sub(r"[ \t\r\n\f]+", " ", data)
        if self.events and self.events[-1][0] == kind:
            data = self.events.pop()[1] + data
        self.events.append((kind, data))


def events_of(html, script=lambda text: text):
    """The page's events, the text of each script made what `script` makes of it."""
    parser = Events()
    parser.feed(html)
    parser.close()
    events = [("text", script(event[1])) if event[0] == "script-code" else event for event in parser.events]
    # a run of white space that was on its own between two tags is one space or none
    return [event for event in events if event != ("text", " ")]


def untokened(html):
    """The page without what differs from one render to the next: the masked CSRF tokens
    and the nonces."""
    return re.sub(r"[0-9a-f]{64,}", "TOKEN", re.sub(r'nonce="[^"]*"', 'nonce="N"', html))


# --- the pass itself ---------------------------------------------------------------------

def test_indentation_and_blank_lines_between_tags_are_one_line_break():
    assert thin("<ul>\n    <li>a</li>\n\n    <li>b</li>\n  </ul>\n") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>\n"


def test_white_space_without_a_line_break_is_left_where_it_separates_words():
    assert thin("<p>one  two <b>x</b> <i>y</i></p>") == "<p>one  two <b>x</b> <i>y</i></p>"


def test_a_no_break_space_is_text_and_stays():
    assert thin("<p>a\n  b</p>") == "<p>a\n  b</p>"


@pytest.mark.parametrize("element", ['<pre>\n    indented\n\n  code\n</pre>',
                                     '<textarea name="a">\n    typed\n\n</textarea>',
                                     '<style>\n    a {\n\n  color: red;\n  }\n</style>',
                                     '<!--\n    a comment\n\n  -->',
                                     '<script type="application/json" id="data">\n  {"a":\n\n   1}\n</script>'])
def test_what_a_browser_shows_or_reads_as_written_is_kept(element):
    assert thin(f"<div>\n    {element}\n    </div>") == f"<div>\n{element}\n</div>"


def test_a_line_break_inside_a_tag_is_kept():
    tag = '<span class="hint" data-hint="one\n            two\n  three" title=\'a > b\n  c\'>'
    assert thin(f"<p>\n  {tag}x</span>\n</p>") == f"<p>\n{tag}x</span>\n</p>"


def test_an_inline_script_loses_its_indentation_blank_lines_and_comment_lines():
    page = '<script nonce="x">\n    // why\n    function f() {\n\n      return "  a  ";   \n    }\n    // done\n</script>'
    assert thin(page) == '<script nonce="x">\nfunction f() {\nreturn "  a  ";\n}\n</script>'


@pytest.mark.parametrize("source", [
    "\n  var a = `one\n    two`;\n",       # the indent is in the value
    "\n  /* a block\n  // comment inside it\n  */\n  run();\n",
    "\n  var a = 'one \\\n    two';\n",     # a continued line
    "later('/static/x.js')",              # nothing to thin
])
def test_a_script_where_a_line_may_be_part_of_a_value_is_left_alone(source):
    assert thin_script(source) == source
    assert thin(f"<script>{source}</script>") == f"<script>{source}</script>"


def test_a_template_literal_on_one_line_does_not_hold_the_script_back():
    assert thin_script("\n  var a = `one two`;\n    // c\n  run();\n") == "\nvar a = `one two`;\nrun();\n"


def test_a_script_that_is_data_is_not_code():
    page = '<script type="text/template">\n   // not a comment\n   <b>x</b>\n</script>'
    assert thin(page) == page


def test_a_thinned_page_is_thinned():
    page = "<!doctype html>\n<html>\n  <body>\n    <pre>\n  x\n</pre>\n    <script>\n      a();\n    </script>\n  </body>\n</html>\n"
    assert thin(thin(page)) == thin(page)


# --- the pages ---------------------------------------------------------------------------

def render(client, path):
    response = client.get(path, follow_redirects=True, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200, path
    body = response.get_data()
    return gzip.decompress(body).decode() if response.headers.get("Content-Encoding") == "gzip" else body.decode()


@pytest.mark.parametrize("role, path", PAGES)
def test_a_thinned_page_is_the_same_page(app, client, as_role, role, path):
    """Same tags, attributes and text, white space read as a browser reads it. A script is
    the original's, thinned by the pass: lines are all it changes."""
    as_role(role)
    # the first look at a cross compare result is not quite the later ones (the result is
    # cached by then), so both are of a page that has been seen
    render(client, path)
    thinned = render(client, path)
    app.config["MCRITWEB_THIN_PAGES"] = False
    whole = render(client, path)

    assert len(thinned) < len(whole), "the page was not thinned"
    seen = events_of(untokened(thinned), script=lambda text: text)
    expected = events_of(untokened(whole), script=thin_script)
    assert seen == expected, path
    assert thin(thinned) == thinned, "a thinned page thins further"


@pytest.mark.parametrize("path", [RESULT, RESULT + "?famid=1", f"/data/result/{job_id_of('cross_compare')}"])
def test_a_result_page_weighs_a_tenth_less_gzipped(app, client, as_role, path):
    """The size the first round trip is spent on. The ratio is what holds whatever else a
    page carries; the hundred rows of a match table are most of it."""
    as_role("visitor")
    thinned = render(client, path)
    app.config["MCRITWEB_THIN_PAGES"] = False
    whole = render(client, path)

    assert len(gzip.compress(thinned.encode(), 6)) <= 0.93 * len(gzip.compress(whole.encode(), 6))


def test_the_pass_runs_before_the_page_is_gzipped(client, as_role):
    as_role("visitor")
    response = client.get(RESULT, headers={"Accept-Encoding": "gzip"})
    assert response.headers["Content-Encoding"] == "gzip"
    assert thin(gzip.decompress(response.get_data()).decode()) == gzip.decompress(response.get_data()).decode()


def test_what_is_not_a_page_is_not_thinned(app, client, as_role):
    """The dot graph of a function is served as text/html, and is text whose indentation
    and line breaks are its own."""
    as_role("visitor")
    body = client.get("/explore/fetchDotGraph/0").get_data(as_text=True)
    assert "\n    " in body or body == body.strip()
    assert not thin_pages.is_a_page(client.get("/static/style.css"))


def test_it_is_off_when_the_configuration_says_so(app, client, as_role):
    as_role("visitor")
    app.config["MCRITWEB_THIN_PAGES"] = False
    assert render(client, RESULT) != thin(render(client, RESULT))
