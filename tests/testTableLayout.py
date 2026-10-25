#!/usr/bin/python
"""The row-action cells and column headings that issue #52 is about.

The issue is a screenshot: table headings and a row's export/analyze buttons wrap
onto a second line when the viewport is narrow, so the row grows a line for no
information. The fix is CSS, which nothing here can render - what these tests pin is
the part that is checkable and the part that actually rots: every row macro's action
cell carrying the class the stylesheet targets, and the rule still being there.

A new row macro that forgets the class is the way this comes back.
"""

import logging
import pathlib
import re
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "templates"
STYLESHEET = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "static" / "style.css"

#: Every row macro that renders a cell of action buttons.
ROW_MACROS_WITH_ACTIONS = ["family_row.html", "function_row.html", "job_row.html", "sample_row.html"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.mark.parametrize("macro", ROW_MACROS_WITH_ACTIONS)
def test_every_row_macro_marks_its_action_cell(macro):
    """`table.table td.buttons` is what the stylesheet keeps on one line."""
    markup = (TEMPLATES / "table" / macro).read_text()

    cells_with_buttons = [
        line for line in markup.splitlines()
        if "<td" in line and "class=" in line and "buttons" in line
    ]
    assert cells_with_buttons, f"{macro} has no cell marked class=\"buttons\""


def test_the_stylesheet_keeps_headings_and_action_cells_on_one_line():
    css = STYLESHEET.read_text()

    assert re.search(r"table\.table thead th\s*\{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"table\.table td\.buttons\s*\{[^}]*white-space:\s*nowrap", css)


def test_the_stylesheet_keeps_a_value_and_its_filter_icon_on_one_line():
    """A result page's id and family cells hold two links, the value and the icon that
    filters by it; wrapped onto two 21.6px lines they are closer than the 24px targets of
    WCAG 2.5.8. tests/testAccessibility.py checks the cells carry the classes."""
    css = STYLESHEET.read_text()

    assert re.search(r"table\.table td\.id,\s*table\.table td\.family\s*\{[^}]*white-space:\s*nowrap", css)


def test_the_rule_stays_scoped_to_the_application_tables():
    """`nowrap` on every <th> in the document would reach anything a future page
    renders - the manual's markdown tables among them, whose headings are prose and
    should wrap. The selector carries `table.table`, the Bootstrap class only the
    application's own tables have."""
    css = STYLESHEET.read_text()

    unscoped = re.findall(r"^\s*(?:table\s+)?thead th\s*\{", css, re.MULTILINE)
    assert unscoped == [], f"unscoped heading rule(s): {unscoped}"


def test_a_rendered_sample_table_carries_the_class(client, as_role):
    """The end of the chain: the class reaches the page, not just the template."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)

    assert 'class="buttons"' in page
    assert 'class="table table-hover"' in page, "the stylesheet's selector needs this"


# --- tables wider than the screen -------------------------------------------------
#
# What #52 keeps on one line makes a data table as wide as those cells, 1,064px on the
# samples page, and that widened the whole page on a phone or a tablet. A data table now
# sits in a `.table-scroll` box that scrolls it on its own below 1200px. The browser side
# is tests/testTableScrollBrowser.py, which CI does not run.

#: The tables that are not in a box, because they list the fields of one thing - a
#: sample, a function, a job, an account - and break a long value instead of scrolling.
#: A table missing from here and from a box widens the page again.
TABLES_THAT_WRAP = {
    "admin_server.html": 1,
    "import_complete.html": 1,
    "result_unique_blocks.html": 1,
    "settings.html": 1,
    "single_query_sample.html": 2,
    "statistics.html": 1,
    "table/column_table.html": 3,
    "table/function_details.html": 3,
    "table/matching_statistics_table.html": 1,
}


def tables_outside_a_box():
    """{template: number of its Bootstrap tables whose line before is not a .table-scroll}"""
    found = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        lines = path.read_text().splitlines()
        for number, line in enumerate(lines):
            if re.search(r'<table class="table\b', line):
                before = next((lines[i] for i in range(number - 1, -1, -1) if lines[i].strip()), "")
                if 'class="table-scroll' not in before:
                    name = path.relative_to(TEMPLATES).as_posix()
                    found[name] = found.get(name, 0) + 1
    return found


def test_every_data_table_sits_in_a_box_that_scrolls_it():
    assert tables_outside_a_box() == TABLES_THAT_WRAP


def test_the_box_scrolls_below_1200px_and_holds_what_bootstrap_hides():
    """`position: relative` is what makes the box clip Bootstrap's .visually-hidden, which
    is absolutely positioned: without it the box scrolled and the page was still 922px."""
    css = STYLESHEET.read_text()

    box = re.search(r"\n\.table-scroll\s*\{([^}]*)\}", css)
    assert box and re.search(r"position:\s*relative", box.group(1))
    assert re.search(r"@media \(max-width: 1199\.98px\)\s*\{\s*\.table-scroll\s*\{\s*overflow-x:\s*auto;", css)


def test_a_rendered_table_is_in_its_box(client, as_role):
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)

    assert re.search(r'<div class="table-scroll">\s*<table class="table table-hover" id="sample-table">', page)


@pytest.mark.parametrize("template", ["table/sample_row.html", "users.html"])
def test_the_rows_with_a_menu_queue_what_keeps_it_out_of_the_box(template):
    """A menu in a row of a box that scrolls is clipped by the box, unless Popper places it
    with position: fixed - which static/table_scroll.js asks for."""
    assert "later({{ url_for('static', filename='table_scroll.js')|tojson }})" in (TEMPLATES / template).read_text()


def test_a_table_outside_a_box_breaks_a_long_value():
    """The tables in TABLES_THAT_WRAP scroll nowhere, so a long token - the API token on the
    settings page - has to break in its cell, or it widens the page. `anywhere`, because
    only that lets the table's narrowest width give up the token's."""
    css = STYLESHEET.read_text()

    assert re.search(r"table\.table:not\(\.table-scroll > table\) :is\(td, th\)\s*\{\s*overflow-wrap:\s*anywhere;", css)


def test_on_a_wide_screen_a_data_table_keeps_its_headings_in_view():
    """Up to 250 rows a page, told apart by their headings. Only from 1200px, where the box
    does not scroll: a box that scrolls is what a sticky heading sticks to."""
    css = STYLESHEET.read_text()

    assert re.search(
        r"@media \(min-width: 1200px\)\s*\{\s*\.table-scroll > \.table > thead > tr > th\s*\{\s*position:\s*sticky;\s*top:\s*0;",
        css,
    )


def test_the_cross_compare_matrix_is_centred_by_its_margins():
    """`justify-content: center` on the pane pushed the start of a matrix wider than the
    pane out of the reach of its scrollbar; auto margins fall to 0 instead."""
    template = (TEMPLATES / "result_cross.html").read_text()
    css = STYLESHEET.read_text()

    panes = re.findall(r'<div class="([^"]*cross-pane-body[^"]*)"', template)
    assert len(panes) == 2
    assert all("justify-content-center" not in classes and "d-flex" not in classes for classes in panes), panes
    assert re.search(r"\.cross-pane-body > table\s*\{\s*margin-inline:\s*auto;", css)


def test_a_box_that_scrolls_fades_at_the_edge_with_more_to_come():
    """The scroll cue: only where scroll-state queries are supported, and only shown, never
    laid out, so that showing it moves nothing."""
    css = STYLESHEET.read_text()

    cue = css.split("@supports (container-type: scroll-state) {", 1)[1]
    assert re.search(r"@container scroll-state\(scrollable: inline-end\)\s*\{\s*:is\(\.table-scroll, \.cross-pane-body\)::after\s*\{\s*opacity:\s*1;", cue)
    fade = re.search(r":is\(\.table-scroll, \.cross-pane-body\)::after\s*\{([^}]*)\}", cue).group(1)
    assert "pointer-events: none" in fade and "position: sticky" in fade and "opacity: 0" in fade


#: result template -> the rows its function matches are counted from; that table is laid
#: out only when it comes near the window
DEFERRED_TABLES = {
    "result_compare_all.html": "function_rows",
    "result_compare_family.html": "function_rows",
    "result_compare_sample.html": "function_rows",
    "result_compare_vs.html": "function_rows",
}


@pytest.mark.parametrize("template", sorted(DEFERRED_TABLES))
def test_a_result_s_function_matches_wait_until_they_come_near(template):
    """Held at their number of rows until then, so that the page is about as long as it
    will be; they are the last box on the page, and the only one held."""
    text = (TEMPLATES / template).read_text()
    boxes = re.findall(r'<div class="table-scroll[^"]*"[^>]*>', text)

    held = [box for box in boxes if "table-deferred" in box]
    assert held == [boxes[-1]], boxes
    rows = DEFERRED_TABLES[template]
    assert held[0] == f'<div class="table-scroll table-deferred" style="contain-intrinsic-block-size: auto calc({{{{ {rows}|length }}}} * (2.35rem + 1px) + 3rem)">'


def test_the_deferral_is_held_to_where_the_box_scrolls():
    css = STYLESHEET.read_text()

    assert re.search(
        r"@media \(max-width: 1199\.98px\)\s*\{\s*\.table-deferred\s*\{\s*content-visibility:\s*auto;\s*\}",
        css,
    )


if __name__ == "__main__":
    unittest.main()
