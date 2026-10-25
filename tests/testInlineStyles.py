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
