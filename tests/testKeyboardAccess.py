#!/usr/bin/python
"""What a keyboard needs from the markup, checked on every page the suite renders.

A control a keyboard cannot reach is invisible to testAccessibility.py, which looks at
names and labels, and Lighthouse does not press Tab either. These checks read the
rendered pages for the shapes that left keyboard users without a way in: an anchor
without an href doing a button's job, which is not in the tab order at all. What the
keys then do is a browser question, answered in testKeyboardAccessBrowser.py.
"""

import copy
import logging
import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of, load
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



@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_a_link_is_announced_as_a_link_and_a_menu_toggle_is_a_button(client, as_role, path):
    """Fifteen links that go to another page carried role="button", so a screen reader
    called them buttons and its user pressed Space, which follows no link. The navbar's
    and the jobs page's menu toggles were the opposite: links to "#" that open a menu,
    on which Space did nothing either."""
    tags = start_tags(render(client, as_role, path))

    posing = [line for tag, attrs, line in tags if tag == "a" and "href" in attrs and attrs.get("role") == "button"]
    assert posing == [], f"{path}: a link poses as a button on lines {posing}"
    toggles = [(tag, line) for tag, attrs, line in tags if attrs.get("data-bs-toggle") == "dropdown"]
    assert toggles and [(tag, line) for tag, line in toggles if tag != "button"] == []


def test_a_pending_account_s_menus_are_disabled_for_assistive_technology_too(client, as_role):
    """An account waiting for a role gets the Explore, Analyze and Data menus switched
    off. `disabled` on the anchors they were did nothing for a screen reader, which read
    three working buttons; on a button it disables it."""
    as_role("pending")
    tags = start_tags(client.get("/").get_data(as_text=True))

    toggles = {attrs["id"]: (tag, "disabled" in attrs) for tag, attrs, _ in tags if attrs.get("id", "").startswith("navbarDropdown")}
    assert toggles == {
        "navbarDropdownExplore": ("button", True),
        "navbarDropdownAnalyze": ("button", True),
        "navbarDropdownData": ("button", True),
        "navbarDropdownUser": ("button", False),
    }


#: a pager entry Bootstrap greys out, with everything inside it
DISABLED_PAGE_ITEM = re.compile(r'<li class="page-item\s+disabled">(.*?)</li>', re.S)


@pytest.mark.parametrize("path", [
    # the first page of a cursor pagination, whose Last page is never offered
    "/explore/functions",
    # the first page of an offset pagination, and on a result page
    f"/data/result/{job_id_of('matches_for_sample')}",
])
def test_a_pager_entry_that_goes_nowhere_is_no_tab_stop_and_says_it_is_disabled(client, as_role, path):
    """First and Previous on the first page were links with a live href, the second to
    `page=0`: a tab stop that reloaded the page, with nothing to tell a screen reader
    why. The cursor pager's Last page put its tab stop on a span without a role, whose
    reason was in a title Bootstrap moves into a tooltip."""
    html = render(client, as_role, path)
    disabled = DISABLED_PAGE_ITEM.findall(html)
    assert disabled, f"{path} no longer starts on a first page"

    for item in disabled:
        tags = start_tags(item)
        assert [tag for tag, attrs, _ in tags if tag == "a" and "href" in attrs] == [], item
        assert all(attrs.get("aria-disabled") == "true" for tag, attrs, _ in tags if tag == "a"), item
        stops = [(tag, attrs) for tag, attrs, _ in tags if "tabindex" in attrs]
        assert all(tag == "a" and attrs.get("aria-label", "").startswith("Last page,") for tag, attrs in stops), item


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_no_anchor_relies_on_a_disabled_attribute(client, as_role, path):
    """An anchor has no `disabled`; what reads as disabled is aria-disabled."""
    tags = start_tags(render(client, as_role, path))

    assert [line for tag, attrs, line in tags if tag == "a" and "disabled" in attrs] == []
    greyed = [(attrs, line) for tag, attrs, line in tags if tag == "a" and "disabled" in (attrs.get("class") or "").split()]
    assert [line for attrs, line in greyed if "href" in attrs or attrs.get("aria-disabled") != "true"] == []


def test_a_jobs_menu_entry_greyed_out_is_disabled_for_a_screen_reader_too(client, as_role):
    """The jobs page greys out a category with nothing in it; the entry was still a link
    Tab stopped on, and nothing said it was unavailable."""
    html = render(client, as_role, "/data/jobs?active=getMatchesForSample")
    entries = [attrs for tag, attrs, _ in start_tags(html) if "dropdown-item" in (attrs.get("class") or "").split() and "disabled" in attrs.get("class").split()]

    assert entries, "no category is greyed out any more - this case has stopped guarding anything"
    assert all("href" not in attrs and attrs.get("aria-disabled") == "true" for attrs in entries)


@pytest.fixture
def serve_report(corpus_mcrit, monkeypatch):
    """Answer every result with a changed copy of a captured report."""
    def _serve(changed):
        monkeypatch.setattr(corpus_mcrit, "getResultForJob", lambda *args, **kwargs: changed)
    return _serve


def test_the_yara_tab_of_a_report_without_a_rule_is_disabled_for_a_screen_reader_too(client, as_role, serve_report):
    """Bootstrap's `disabled` class greys the tab out and keeps it from opening; nothing
    told a screen reader."""
    changed = copy.deepcopy(load("unique_blocks.result"))
    changed["statistics"]["has_yara_rule"] = False
    serve_report(changed)
    tags = start_tags(render(client, as_role, f"/data/result/{job_id_of('unique_blocks')}"))

    tab = next(attrs for tag, attrs, _ in tags if attrs.get("id") == "pills-yara-tab")
    assert "disabled" in tab["class"].split()
    assert tab.get("aria-disabled") == "true"
