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


if __name__ == "__main__":
    unittest.main()
