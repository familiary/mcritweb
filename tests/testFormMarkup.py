#!/usr/bin/python
"""The markup of the forms, as the browser and assistive technology read it.

Each page is rendered offline against the captured corpus and parsed, so these hold for
what a template prints, not for what it looks like in its source.
"""

import collections
from html.parser import HTMLParser

import pytest


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Tags(HTMLParser):
    """Every start tag of a page, as (tag, attributes), in order."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def ids(self):
        return [value for _tag, attrs in self.tags for name, value in attrs if name == "id"]


def page_of(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.get_data(as_text=True)


def test_the_sample_dialog_has_no_stray_quote_in_its_form(client, as_role):
    """`action='...''` closed the value and then opened an attribute named `'`, which a
    browser keeps as an attribute of that name on the form."""
    as_role("contributor")
    stray = [(tag, name) for tag, attrs in Tags(page_of(client, "/explore/samples/0")).tags
             for name, _value in attrs if "'" in name or '"' in name]
    assert stray == []


@pytest.mark.parametrize("path", ["/settings", "/analyze/compare_versus"])
def test_no_id_is_used_twice(client, as_role, path):
    """Settings gave all seven column lists `id="items"`, and the 1 vs 1 page printed the
    sample dialog once per table, so `editSampleModal`, `sample_id`, `sample_family_name`
    and the rest were there twice, and the second dialog's labels named the first one's
    fields. Neither page has an edit control that opens that dialog."""
    as_role("contributor")
    counts = collections.Counter(Tags(page_of(client, path)).ids())
    assert [name for name, count in counts.items() if count > 1] == []


def test_a_page_that_edits_samples_still_has_its_dialog(client, as_role):
    """The dialog is printed where an edit control can open it, and once."""
    as_role("contributor")
    ids = Tags(page_of(client, "/explore/samples")).ids()
    assert ids.count("editSampleModal") == 1
