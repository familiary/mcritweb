#!/usr/bin/python
"""A vendor-prefixed selector in the project's CSS stands in a rule of its own.

A selector list is invalid as a whole when one selector in it is, and the browser drops the
whole rule. A pseudo-element or pseudo-class with another engine's prefix is such a selector:
Chromium drops `.form-check-input:focus, .form-range::-moz-range-thumb` entirely, the check
box's focus ring with it. Selectors Level 4 makes one exception for compatibility (its appendix
B): a `::-webkit-` pseudo-element that is not a function parses as valid everywhere, and matches
nothing where it is unknown; Firefox follows that since version 63. A list holding one is kept
today, but only by that quirk, and a `-moz-` twin written beside it the way the others are
written would take the whole rule down. So none is grouped.
"""

import re

import themeCss
from testTheme import VENDORED_STYLESHEETS
from themeCss import STATIC, TEMPLATES

PREFIXED = re.compile(r"::?-(?:webkit|moz|ms|o)-[\w-]+", re.I)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def stylesheets():
    """name -> CSS of every stylesheet the project writes: static/ less the vendored ones, the
    dark palette included, and the <style> blocks of the templates."""
    sheets = {path.name: path.read_text(encoding="utf-8") for path in sorted(STATIC.glob("*.css")) if path.name not in VENDORED_STYLESHEETS}
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, block in enumerate(re.findall(r"<style\b[^>]*>(.*?)</style\s*>", path.read_text(encoding="utf-8"), re.S | re.I)):
            sheets[f"{path.relative_to(TEMPLATES).as_posix()} <style> {number + 1}"] = JINJA.sub(" ", block)
    return sheets


def grouped_prefixed_selectors(css):
    """The selector lists of a stylesheet that hold a vendor-prefixed selector and anything else."""
    found = []
    for rule in themeCss.parse_css(css):
        selectors = themeCss.selectors_of(rule)
        if len(selectors) > 1 and any(PREFIXED.search(selector) for selector in selectors):
            found.append(", ".join(selectors))
    return found


def test_the_scanner_finds_a_grouped_prefixed_selector_and_leaves_a_lone_one_alone():
    css = """
      .a:focus, .b::-moz-range-thumb { color: red; }
      @media (forced-colors: active) { .c, .d:-webkit-autofill { color: red; } }
      .e::-webkit-slider-thumb { color: red; }
      .f, .g::placeholder { color: red; }
    """

    assert grouped_prefixed_selectors(css) == [".a:focus, .b::-moz-range-thumb", ".c, .d:-webkit-autofill"]


def test_no_project_rule_groups_a_vendor_prefixed_selector_with_another():
    sheets = stylesheets()
    grouped = {name: found for name, css in sheets.items() if (found := grouped_prefixed_selectors(css))}
    prefixed = sum(len(PREFIXED.findall(themeCss.strip_comments(css))) for css in sheets.values())

    assert grouped == {}, f"a vendor-prefixed selector grouped with others; give it a rule of its own: {grouped}"
    assert {"style.css", "theme-dark.css"} <= set(sheets) and prefixed >= 8, f"the scan found {prefixed} prefixed selectors in {sorted(sheets)}: it has stopped looking"
