#!/usr/bin/python
"""Decorative generated text is not read out.

Text a stylesheet writes with `content:` is in the accessibility tree like any other: the
match diagram's "rendering diagram..." placeholder was read on every result page, though
the diagram covers it once loaded, and the breadcrumb was read with a "slash" between its
items. `content: "text" / ""` gives such text an empty alternative. That form is newer
than `content` itself, so it is written as a second declaration, which a browser without
it drops, keeping the plain one and the look it had. tests/testPageStructureBrowser.py
reads the accessibility tree.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "mcritweb" / "static"


def declarations(css, selector):
    """The `content` values of every rule for exactly `selector`, in the order they come."""
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if selector in [part.strip() for part in selectors.split(",")]:
            found += [value.strip() for value in re.findall(r"content:\s*([^;]+)(?:;|$)", body)]
    return found


def test_the_diagram_placeholder_is_not_read_out():
    css = (STATIC / "style.css").read_text()

    assert declarations(css, ".mcrit-diagram::before") == ['"rendering diagram\\2026"', '"rendering diagram\\2026" / ""']


def test_the_breadcrumb_divider_is_not_read_out():
    """Bootstrap draws it from `var(--bs-breadcrumb-divider, "/")`. The alternative is
    written out without the variable: a declaration holding one is only checked once
    the variable is put in, so a browser without the alternative would drop the divider
    rather than this declaration."""
    css = (STATIC / "style.css").read_text()
    bootstrap = (STATIC / "css" / "bootstrap-subset.css").read_text()

    assert declarations(bootstrap, ".breadcrumb-item + .breadcrumb-item::before") == ['var(--bs-breadcrumb-divider,"/")']
    assert declarations(css, ".breadcrumb-item + .breadcrumb-item::before") == ['"/" / ""']
