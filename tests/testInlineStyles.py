#!/usr/bin/python
"""Styling that is said once, in a class, and not in an attribute on every cell.

`style="text-align: right;"` stood on 94 cells and `style="color: var(--status-yes);"`
on 33 icons, each a separate place to change a column's alignment or a colour, and an
inline colour is one the dark sheet can only outweigh with !important. They are classes
now (Bootstrap's text-end and text-center, and style.css's status-yes and status-no);
what pins them is that they do not come back, and that the classes exist.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

#: the alignments and colours that were repeated; a data-driven `text-align: {{ align }}`
#: is not one of them
LITERAL_STYLES = [
    re.compile(r'style="[^"]*text-align:\s*(?:right|center)\s*;'),
    re.compile(r'style="[^"]*color:\s*var\(--status-(?:yes|no)\)'),
    re.compile(r'style="[^"]*justify-content:\s*space-between'),
]


def _lines(pattern):
    return [
        f"{path.relative_to(TEMPLATES).as_posix()}:{number}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]


@pytest.mark.parametrize("pattern", LITERAL_STYLES, ids=lambda pattern: pattern.pattern[:40])
def test_no_template_repeats_a_literal_style_that_a_class_says(pattern):
    assert _lines(pattern) == []


@pytest.mark.parametrize("name", ["text-end", "text-center"])
def test_the_alignment_classes_are_in_the_bootstrap_subset(name):
    """ADR 0020 cuts Bootstrap to the rules in use: a class the templates start to use
    has to be in the cut, or the cells go back to the left."""
    subset = (STATIC / "css" / "bootstrap-subset.css").read_text()
    assert re.search(rf"\.{name}\s*\{{[^}}]*text-align", subset)


@pytest.mark.parametrize("name", ["yes", "no"])
def test_the_status_classes_take_their_colour_from_the_theme_token(name):
    """The token is what theme-dark.css redefines; a class that named a colour would not
    follow it."""
    css = (STATIC / "style.css").read_text()
    assert re.search(rf"\.status-{name}\s*\{{\s*color:\s*var\(--status-{name}\);\s*\}}", css)


def test_the_settings_columns_are_spread_by_their_class():
    """A .row is a flex box already: the class says the one thing the inline style added."""
    css = (STATIC / "style.css").read_text()
    assert re.search(r"\.settings-columns\s*\{\s*justify-content:\s*space-between;\s*\}", css)
    assert (TEMPLATES / "settings.html").read_text().count('class="row settings-columns"') == 14
