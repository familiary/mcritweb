#!/usr/bin/python
"""Bootstrap's stylesheet cut to the rules that can match something mcritweb renders.

base.html loads it on every page, render-blocking, so every byte is waited for before the
first paint. scripts/subset_bootstrap.py drops a selector that needs a class appearing
nowhere in mcritweb's templates, views, manual or scripts, and a rule once none of its
selectors is left; these tests keep the committed subset what the script writes, and hold
the script to that rule.
"""

import logging
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import subset_bootstrap  # noqa: E402

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

STATIC = ROOT / "mcritweb" / "static"
SUBSET = STATIC / "css" / "bootstrap-subset.css"
WHOLE = STATIC / "bootstrap-5.0.2-dist" / "css" / "bootstrap.min.css"


def selectors_in(css):
    """Every selector of every style rule, read with a regex rather than the script's own
    parser: a prelude is what stands between a brace (or the start) and the next `{`, and
    one that starts with `@` is an at-rule's."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = set()
    for prelude in re.findall(r"(?:^|[{}])\s*([^{}@\s][^{}]*)\{", css):
        found.update(selector.strip() for selector in prelude.split(","))
    return found


def test_the_committed_subset_is_what_the_script_generates():
    words, prefixes = subset_bootstrap.used_names()
    assert SUBSET.read_text() == subset_bootstrap.subset(WHOLE.read_text(), words, prefixes), "run scripts/subset_bootstrap.py"


def test_base_loads_the_subset_and_not_the_whole_build():
    base = (ROOT / "mcritweb" / "templates" / "base.html").read_text()
    assert "filename='css/bootstrap-subset.css'" in base
    assert "bootstrap.min.css" not in re.sub(r"\{#.*?#\}", "", base, flags=re.S)


def test_the_subset_is_a_fraction_of_the_whole_build():
    assert SUBSET.stat().st_size < WHOLE.stat().st_size * 0.4


def test_the_subset_keeps_the_licence_after_the_charset():
    css = SUBSET.read_text()
    assert css.startswith('@charset "UTF-8";\n/*!')
    assert "Bootstrap v5.0.2" in css[:200] and "Licensed under MIT" in css[:400]


def test_every_class_a_template_names_keeps_the_rules_bootstrap_has_for_it():
    """The plain case, with no script involved: a class written into a template's class
    attribute. For each one, the selector that is that class alone is still there wherever
    the whole build has it."""
    named = set()
    for path in (ROOT / "mcritweb" / "templates").rglob("*.html"):
        for attribute in re.findall(r'class="([^"{]*)"', path.read_text()):
            named.update(attribute.split())
    whole, subset = selectors_in(WHOLE.read_text()), selectors_in(SUBSET.read_text())
    missing = sorted("." + name for name in named if "." + name in whole and "." + name not in subset)
    assert missing == [], missing[:10]


def test_the_classes_a_script_or_a_built_name_adds_keep_their_rules():
    """No template writes these out: `bg-danger` is `bg-{{ variant }}` in a row's badge,
    Bootstrap's bundle builds the tooltip's `bs-tooltip-top` from a prefix and adds
    `collapsing` while the navbar opens."""
    subset = selectors_in(SUBSET.read_text())
    for selector in (".bg-danger", ".bs-tooltip-top", ".collapsing"):
        assert selector in subset, selector


# --- the selector rule -------------------------------------------------------------

def test_a_rule_is_dropped_only_when_every_selector_needs_an_unused_class():
    css = '.used{a:1}.unused{b:2}.used,.unused{c:3}.unused .used{d:4}div{e:5}'
    assert subset_bootstrap.subset(css, {"used"}, set()) == ".used{a:1}\n.used{c:3}\ndiv{e:5}\n"


def test_a_class_inside_not_or_an_attribute_selector_is_not_required():
    css = '.btn:not(.disabled){a:1}[class*=col-]{b:2}.x[data-y=".z"]{c:3}'
    assert subset_bootstrap.subset(css, {"btn", "x"}, set()) == css.replace("}", "}\n")


def test_a_class_built_at_run_time_keeps_its_whole_prefix():
    words, prefixes = set(), set(subset_bootstrap.PREFIX.findall('<span class="bg-{{ level }}"> "btn-" + kind'))
    assert prefixes == {"bg-", "btn-"}
    assert subset_bootstrap.subset(".bg-danger{a:1}.btn-primary{b:2}.text-danger{c:3}", words, prefixes) == (
        ".bg-danger{a:1}\n.btn-primary{b:2}\n")


def test_a_brace_or_a_comma_inside_a_rule_does_not_split_it():
    """Bootstrap 5.0.2 has neither, a later build may: a brace in a string, in a body or
    in an attribute selector, and a selector list inside `:not()`."""
    css = '.used{content:"}"}.used[title="{;"]{b:1}.used:not(.a,.b){c:1}.unused:not(.used,.b){d:1}'
    assert subset_bootstrap.subset(css, {"used"}, set()) == (
        '.used{content:"}"}\n.used[title="{;"]{b:1}\n.used:not(.a,.b){c:1}\n')


def test_media_blocks_and_keyframes_survive_whole_or_go():
    css = "@media (min-width:576px){.used{a:1}.unused{b:2}}@media print{.unused{c:3}}@keyframes spin{0%{x:1}to{x:2}}"
    assert subset_bootstrap.subset(css, {"used"}, set()) == (
        "@media (min-width:576px){.used{a:1}}\n@keyframes spin{0%{x:1}to{x:2}}\n")


def test_a_comment_never_reads_as_a_class():
    """The licence names getbootstrap.com, which would read as a class `.com` in the
    selector the comment sits in front of."""
    css = '@charset "UTF-8";/*! see getbootstrap.com */:root{--a:1}'
    assert subset_bootstrap.subset(css, set(), set()) == '@charset "UTF-8";\n/*! see getbootstrap.com */\n:root{--a:1}\n'
