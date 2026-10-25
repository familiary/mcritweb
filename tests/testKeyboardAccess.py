#!/usr/bin/python
"""What a keyboard needs from the markup, checked on every page the suite renders.

A control a keyboard cannot reach is invisible to testAccessibility.py, which looks at
names and labels, and Lighthouse does not press Tab either. These checks read the
rendered pages for the shapes that left keyboard users without a way in: an anchor
without an href doing a button's job, which is not in the tab order at all. What the
keys then do is a browser question, answered in testKeyboardAccessBrowser.py.
"""

import logging
from html.parser import HTMLParser

import pytest
from testAccessibility import ADMIN_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: every page testAccessibility.py renders, each shown to a role that may open it; an admin
#: sees every control a page has, the edit and rename buttons among them
SIGNED_IN_PAGES = VISITOR_PAGES + CONTRIBUTOR_PAGES + ADMIN_PAGES


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class _StartTags(HTMLParser):
    """Every start tag of a page, as (tag, attributes, line)."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs), self.getpos()[0]))


def start_tags(html):
    parser = _StartTags()
    parser.feed(html)
    return parser.tags


def render(client, as_role, path):
    as_role("admin")
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200, path
    return response.get_data(as_text=True)


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_every_dialog_is_opened_by_a_button(client, as_role, path):
    """The edit, rename and API-usage dialogs were opened by `<a role="button">` without
    an href. An anchor without one is not focusable, so Tab never reached them and a
    keyboard user could not edit or delete a family or sample, rename a function or read
    its API usage. role="button" told a screen reader it was a button all the same."""
    tags = start_tags(render(client, as_role, path))

    openers = [(tag, line) for tag, attrs, line in tags if attrs.get("data-bs-toggle") == "modal"]
    assert [(tag, line) for tag, line in openers if tag != "button"] == []
    posing = [line for tag, attrs, line in tags if tag == "a" and "href" not in attrs and attrs.get("role") == "button"]
    assert posing == [], f"{path}: an anchor without an href poses as a button on lines {posing}"


def test_the_check_sees_an_anchor_that_opens_a_dialog():
    tags = start_tags('<a class="btn" role="button" data-bs-toggle="modal" data-bs-target="#x"></a>')

    assert [(tag, attrs.get("data-bs-toggle")) for tag, attrs, _ in tags] == [("a", "modal")]

