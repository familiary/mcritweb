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
        # "Last page of <table>, ..." where the pager is named after its table
        assert all(tag == "a" and re.match(r"Last page( of [^,]+)?,", attrs.get("aria-label", "")) for tag, attrs in stops), item


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_no_anchor_relies_on_a_disabled_attribute(client, as_role, path):
    """An anchor has no `disabled`; what reads as disabled is aria-disabled, and a greyed
    link that keeps its href is out of the tab order."""
    tags = start_tags(render(client, as_role, path))

    assert [line for tag, attrs, line in tags if tag == "a" and "disabled" in attrs] == []
    greyed = [(attrs, line) for tag, attrs, line in tags if tag == "a" and "disabled" in (attrs.get("class") or "").split()]
    assert [line for attrs, line in greyed if attrs.get("aria-disabled") != "true" or ("href" in attrs and attrs.get("tabindex") != "-1")] == []


def test_a_jobs_menu_entry_greyed_out_is_disabled_for_a_screen_reader_too(client, as_role):
    """The jobs page greys out a category with nothing in it; the entry was still a link
    Tab stopped on, and nothing said it was unavailable. It keeps its href, which
    testMaintenanceJobs.py relies on to find a type's entry, as Bootstrap's own disabled
    menu entries do, out of the tab order."""
    html = render(client, as_role, "/data/jobs?active=getMatchesForSample")
    entries = [attrs for tag, attrs, _ in start_tags(html) if "dropdown-item" in (attrs.get("class") or "").split() and "disabled" in attrs.get("class").split()]

    assert entries, "no category is greyed out any more - this case has stopped guarding anything"
    assert all(attrs.get("tabindex") == "-1" and attrs.get("aria-disabled") == "true" for attrs in entries)


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


def headers(html):
    """Each <th> of a page as (attributes, the markup inside it)."""
    return [(dict(start_tags(match.group(1))[0][1]), match.group(2))
            for match in re.finditer(r"(<th\b[^>]*>)(.*?)</th>", html, re.S)]


@pytest.mark.parametrize("path, label, direction", [
    ("/explore/samples", "Sample ID", "ascending"),
    ("/explore/samples?sort=family&ascending=False", "Family", "descending"),
    ("/explore/functions?sort=offset&ascending=True", "Offset", "ascending"),
    (f"/data/result/{job_id_of('matches_for_sample')}?fampsort=family_name&fampasc=false", "Family", "descending"),
])
def test_a_sortable_header_stays_a_column_header_and_says_how_the_table_is_sorted(client, as_role, path, label, direction):
    """The header was a `th role="button"`, which made it a button rather than the
    column header of the cells below it, and nothing said which column the table was
    sorted by or which way. It is a plain header holding a link that sorts."""
    table_headers = headers(render(client, as_role, path))

    assert [attrs for attrs, _ in table_headers if "role" in attrs] == []
    assert all('class="table-sort" href="' in inner for attrs, inner in table_headers if "onclick" in attrs or "tabindex" in attrs)
    sorted_headers = [(attrs["aria-sort"], inner) for attrs, inner in table_headers if "aria-sort" in attrs]
    assert len(sorted_headers) == 1, sorted_headers
    assert sorted_headers[0][0] == direction
    assert label in sorted_headers[0][1] and 'class="table-sort"' in sorted_headers[0][1]


def test_a_header_with_nothing_to_sort_by_is_no_link(client, as_role):
    """A sortable_header_col given no pagination used to carry an onclick that loaded
    the page it was on."""
    from flask import render_template_string

    as_role("visitor")
    with client.application.test_request_context("/"):
        html = render_template_string(
            "{% from 'table/pagination_widget.html' import sortable_header_col %}"
            "{% call sortable_header_col(none, 'family') %}Family{% endcall %}")

    assert "onclick" not in html and "<a" not in html and "aria-sort" not in html


@pytest.mark.parametrize("tab, selected", [("stats", "pills-statistics-tab"), ("blocks", "pills-blocks-tab"), ("yara", "pills-yara-tab")])
def test_the_tab_a_unique_blocks_link_opens_on_is_the_one_marked_selected(client, as_role, tab, selected):
    """`?tab=blocks` showed the blocks and told a screen reader the statistics tab was
    the selected one: its aria-selected="true" was written whatever the tab."""
    tags = start_tags(render(client, as_role, f"/data/result/{job_id_of('unique_blocks')}?tab={tab}"))

    tabs = {attrs["id"]: attrs for tag, attrs, _ in tags if attrs.get("role") == "tab"}
    assert [tab_id for tab_id, attrs in tabs.items() if attrs["aria-selected"] == "true"] == [selected]
    assert "active" in tabs[selected]["class"].split()


#: a picker row: compare's and compare_versus's sample_row, and the rows cross compare and
#: unique blocks draw themselves - with what follows the row's opening tag
PICKER_ROW = re.compile(r'<tr class="(sample-row parent_table_sample\w*|parent)( row-\w+)?"\s*>\s*(<td[^>]*>.*?</td>)', re.S)


@pytest.mark.parametrize("path, kind", [
    ("/analyze/compare", "radio"),
    ("/analyze/compare_versus", "radio"),
    ("/analyze/cross_compare?samples=0&cache=1", "checkbox"),
    ("/analyze/unique_blocks?samples=0", "checkbox"),
])
def test_every_picker_row_leads_with_the_control_that_picks_it(client, as_role, path, kind):
    """A picker row was picked by a click on the row and nothing else: no tab stop, no
    key, and the only sign that it was picked a background tint. Each row leads with a
    radio button or a checkbox now, ticked for a row the page already holds."""
    rows = PICKER_ROW.findall(render(client, as_role, path))
    assert rows, f"{path} has no picker rows"

    for _, tint, first_cell in rows:
        (tag, attrs, _), = start_tags(first_cell)[1:]
        assert (tag, attrs["type"]) == ("input", kind)
        assert attrs["aria-label"].startswith("Select sample ")
        assert ("checked" in attrs) == (tint.strip() in ("row-pending", "row-selected")), first_cell
        assert ("disabled" in attrs) == (tint.strip() == "row-selected"), first_cell
    if "cache=" in path or "samples=" in path:
        assert {tint.strip() for _, tint, _ in rows} >= {"row-selected"}


#: a listing row, the start of its first header cell, and what that cell holds
LISTING_ROW = re.compile(r'<tr class="(sample-row|family-row|function-row|job-row)\b[^"]*"[^>]*>.*?<th\b[^>]*>(.*?)</th>', re.S)
#: where each kind of row goes: its entity's page, or on a cross compare result the row
#: sample's own report
ROW_TARGET = {
    "sample-row": r"/explore/samples/-?\d+|/data/result/\w+",
    "family-row": r"/explore/families/\d+",
    "function-row": r"/explore/functions/-?\d+",
    "job-row": r"/data/jobs/\w+",
}


@pytest.mark.parametrize("path", [
    "/explore/samples",
    "/explore/families",
    "/explore/functions",
    "/explore/search?query=citadel&type=sample",
    "/explore/samples/0",
    "/data/jobs?active=getMatchesForSample",
    f"/data/jobs/{job_id_of('cross_compare')}",
    f"/data/result/{job_id_of('cross_compare')}",
])
def test_every_listing_row_holds_a_link_to_where_it_goes(client, as_role, path):
    """A click on the row was the only way from a list into a sample, family, function or
    job, and a running job's page had no other way in at all. No key reaches a click
    handler on a row; a link in its id cell is what a keyboard follows."""
    rows = LISTING_ROW.findall(render(client, as_role, path))
    assert rows, f"{path} lists nothing - this case has stopped guarding anything"

    for kind, header in rows:
        links = [attrs.get("href", "") for tag, attrs, _ in start_tags(header) if tag == "a"]
        assert len(links) == 1 and re.fullmatch(ROW_TARGET[kind], links[0]), f"{path}: a {kind} leads nowhere: {header}"


def test_every_score_of_the_cross_compare_matrix_is_named_and_the_matrix_is_one_tab_stop(client, as_role):
    """A score was an empty coloured span: its number lived in a hover tooltip, a pair's
    report opened from an onclick on it, and the cells had no headers to say which pair
    they were for. Each is a link or an image named by its score now, under a header
    naming its row's sample and one naming its column's, and only the first is a tab stop:
    the arrow keys move on from there."""
    report = load("cross_compare.result")["unweighted"]
    order = report["clustered_sequence"]
    html = render(client, as_role, f"/data/result/{job_id_of('cross_compare')}")
    pane = html[html.index('id="pills-unweighted"'):html.index('id="cross-lazy-panes"')]
    tags = start_tags(pane)

    assert re.search(r"<table[^>]*>\s*(?:\{#.*?#\}\s*)?<caption class=\"visually-hidden\">MCRIT score of each pair", pane, re.S)
    columns = [attrs for tag, attrs, _ in tags if tag == "th" and "matrix-col" in (attrs.get("class") or "").split()]
    assert [attrs.get("scope") for attrs in columns] == ["col"] * len(order)
    assert len([attrs for tag, attrs, _ in tags if tag == "th" and attrs.get("scope") == "row"]) == len(order)
    scores = [(tag, attrs) for tag, attrs, _ in tags if "score-swatch" in (attrs.get("class") or "").split()]
    assert len(scores) == len(order) ** 2
    expected = ["%.2f%% (%d matches)" % (report["matching_percent"][row][col], report["matching_matches"][row][col]) for row in order for col in order]
    assert [attrs["aria-label"] for _, attrs in scores] == expected
    assert all(tag == "a" if "href" in attrs else (tag, attrs.get("role")) == ("span", "img") for tag, attrs in scores)
    assert [attrs.get("tabindex") for tag, attrs in scores if tag == "a" or "tabindex" in attrs][0] == "0"
    assert [index for index, (tag, attrs) in enumerate(scores) if attrs.get("tabindex") == "0"] == [0]
    assert all(attrs.get("tabindex") == "-1" for tag, attrs in scores[1:] if tag == "a")


def test_the_cross_compare_result_names_the_colours_of_its_matrix(client, as_role):
    """A colour alone stood for a range of scores, and nothing said which."""
    html = render(client, as_role, f"/data/result/{job_id_of('cross_compare')}")

    key = re.search(r'<p class="text-center small">Score:(.*?)</p>', html, re.S).group(1)
    assert key.count('class="score-swatch score-key"') == 11
    assert "90–100%" in key and "under 10%" in key and "0%</span>" in key


def test_the_move_buttons_are_not_in_the_page_but_made_by_the_script(client, as_role):
    """Written into every item, the buttons took /settings from 42 KB to 100 KB, and the
    page is sent uncompressed because it carries the API token, so its size is what a
    phone downloads. The items are their names alone, and static/reorder_list.js adds
    the buttons, into room the stylesheet keeps."""
    as_role("contributor")
    settings = client.get("/settings").get_data(as_text=True)
    assert "data-reorder=" not in settings
    assert "<button" not in settings[settings.index('id="pills-column-settings"'):settings.index('<button id="saveButton"')]
    assert len(settings.encode()) < 48_000
    assert settings.count("data-reorder-item") > 0

    cross = client.get(f"/data/result/{job_id_of('cross_compare')}").get_data(as_text=True)
    assert "data-reorder=" not in cross
    assert cross.count("data-reorder-item") > 0
