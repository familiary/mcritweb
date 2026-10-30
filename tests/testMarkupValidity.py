#!/usr/bin/python
"""The pages' markup closes what it opens, and closes nothing it did not open.

A browser repairs either, by rules a reader of the template does not see: a stray </p> on
the jobs page made an empty paragraph, its menu items were closed only by the next one
opening, the navbar's collapsing part was left for </nav> to close, and the family table's
header row was closed without being opened. The headings once wrapped in a <span> are
checked in testHeadingsAndTitles.py.
"""

from html.parser import HTMLParser

import pytest
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
#: elements whose end tag HTML lets the next element or the parent's end imply
IMPLIED_END = {"p", "li", "option", "tr", "td", "th", "tbody", "thead"}


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Strict(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        if tag == "li" and self.stack and self.stack[-1] == "li":
            self.problems.append(f"<li> opened in an open <li>, line {self.getpos()[0]}")
            self.stack.pop()
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag not in self.stack:
            self.problems.append(f"</{tag}> with nothing to close, line {self.getpos()[0]}")
            return
        while self.stack[-1] != tag:
            closed = self.stack.pop()
            if closed not in IMPLIED_END:
                self.problems.append(f"<{closed}> left for </{tag}> to close, line {self.getpos()[0]}")
        self.stack.pop()


def markup_problems(html):
    parser = Strict()
    parser.feed(html)
    parser.close()
    return parser.problems


@pytest.mark.parametrize("role, path", [("visitor", path) for path in VISITOR_PAGES]
                         + [("contributor", path) for path in CONTRIBUTOR_PAGES]
                         + [("admin", path) for path in ADMIN_PAGES])
def test_the_markup_closes_what_it_opens(client, as_role, role, path):
    as_role(role)
    response = client.get(path, follow_redirects=path == "/data/jobs")
    assert response.status_code == 200

    assert markup_problems(response.get_data(as_text=True)) == []


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_the_signed_out_markup_closes_what_it_opens(client, make_user, path):
    make_user("admin")
    assert markup_problems(client.get(path).get_data(as_text=True)) == []


def test_the_checker_sees_what_it_claims_to():
    """Without this, a parser that saw nothing would pass every page above."""
    page = "<nav><div><ul><li>a<li>b</ul></nav></table></p><table><tr><td>x</td></tr></table>"

    assert markup_problems(page) == ["<li> opened in an open <li>, line 1", "<div> left for </nav> to close, line 1",
                                     "</table> with nothing to close, line 1", "</p> with nothing to close, line 1"]
