#!/usr/bin/python
"""Text that follows the reader's text size.

Android and ChromeOS let a reader set a text size for the whole system, and a page opts in
to it with `<meta name="text-scale" content="scale">`: the browser then scales the root
font size, and everything sized in rem or em with it. What is sized in px stays as it was,
so the opt-in is only as good as the stylesheets: the tooltips were 12px, the cross compare
matrix 13px and 10px, the login fields 16px. And a box with a px maximum clips what grows
in it, as the copy buttons did their icon at twice the size.

tests/testTextScaleBrowser.py checks the same at a doubled root size in a browser.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
STYLESHEET = STATIC / "style.css"
BASE = TEMPLATES / "base.html"

#: the stylesheets that are ours rather than vendored (hint.css is vendored, and overridden
#: from style.css)
PROJECT_STYLESHEETS = ["style.css", "theme-dark.css", "navbar.css", "signin.css"]

PX_FONT_SIZE = re.compile(r"font-size:\s*[\d.]+px")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_every_page_opts_in_to_the_system_text_size(client, as_role):
    """Right after the viewport, where the guide puts it. Chrome and Edge read it; the
    other browsers ignore it and need nothing else."""
    head = BASE.read_text().split("<title>", 1)[0]
    assert head.count('<meta name="text-scale" content="scale">') == 1
    assert head.index('<meta name="viewport"') < head.index('<meta name="text-scale"') < head.index('<meta name="color-scheme"')

    as_role("visitor")
    assert '<meta name="text-scale" content="scale">' in client.get("/explore/samples").get_data(as_text=True)


@pytest.mark.parametrize("name", PROJECT_STYLESHEETS)
def test_no_project_stylesheet_sets_a_font_size_in_px(name):
    assert PX_FONT_SIZE.findall((STATIC / name).read_text()) == []


def test_no_template_sets_a_font_size_in_px():
    found = [
        f"{path.relative_to(TEMPLATES).as_posix()}:{number}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if PX_FONT_SIZE.search(line)
    ]
    assert found == []


@pytest.mark.parametrize("name", PROJECT_STYLESHEETS)
def test_no_project_stylesheet_sets_the_root_font_size(name):
    """The size the browser scales is the root's: one set here would pin it."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / name).read_text(), flags=re.S)
    for selectors, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css):
        if {selector.strip() for selector in selectors.split(",")} & {"html", ":root"}:
            assert "font-size" not in body, f"{name}: {selectors.strip()} sets the root font size"


def test_the_tooltips_follow_the_text_size():
    """hint.css is vendored and sets 12px on a 12px line; style.css loads before it, so the
    override needs the extra :root to win, as theme-dark.css's does."""
    rule = re.search(r":root \[data-hint\]::after\s*\{([^}]*)\}", STYLESHEET.read_text())
    assert rule
    assert re.search(r"font-size:\s*0\.75rem", rule.group(1))
    assert re.search(r"line-height:\s*1;", rule.group(1))


def test_the_copy_button_grows_with_its_icon():
    """24px was a maximum, and the icon of a doubled text size is 28px."""
    rule = re.search(r"\.btn-clipboard\s*\{([^}]*)\}", STYLESHEET.read_text()).group(1)
    assert "max-height" not in rule and "max-width" not in rule
    assert re.search(r"min-height:\s*24px", rule) and re.search(r"min-width:\s*24px", rule)
