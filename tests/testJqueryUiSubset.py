#!/usr/bin/python
"""jQuery UI cut down to the sortable widget, the one part of it a page calls.

The cross compare result page loads it for its drag-and-drop sample list. The full build is
529 KB of every widget and effect, and running it was that page's longest task in
Lighthouse's phone runs; scripts/subset_jquery_ui.py keeps the sortable widget and the
modules it is built on. These tests keep the committed cut what the script writes from the
vendored full build, and hold the pages to calling no widget the cut leaves out.
"""

import importlib.util
import logging
import pathlib
import re

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "mcritweb" / "static"
TEMPLATES = ROOT / "mcritweb" / "templates"
CUT = STATIC / "jquery-ui-sortable.js"
WHOLE = STATIC / "jquery-ui.js"
WIDGET = re.compile(r'\$\.widget\( "ui\.(\w+)"')
#: jQuery UI widgets whose name Bootstrap's bundle defines as a jQuery plugin too: a call
#: to one of them reaches Bootstrap's, which is queued after the page's own libraries
#: (base.html, ADR-0015)
BOOTSTRAP_NAMES = {"button", "tooltip"}
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("subset_jquery_ui", ROOT / "scripts" / "subset_jquery_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_cut_is_what_the_script_generates(script):
    assert CUT.read_text() == script.cut(WHOLE.read_text()), "run scripts/subset_jquery_ui.py"


@pytest.mark.parametrize("variable", ["widgetsMouse", "widgetsSortable"])
def test_the_script_refuses_a_build_whose_module_it_cannot_find_the_end_of(script, variable):
    """The end of a module is the first statement in the first column after its value is
    assigned; without that statement's semicolon the cut would run on into what follows."""
    text = WHOLE.read_text()
    start = text.index(f"\nvar {variable} = ")
    end = text.index("\n} );\n", start)
    with pytest.raises(ValueError):
        script.cut(text[:end] + "\n} )\n" + text[end + len("\n} );\n"):])


def test_it_holds_the_sortable_widget_and_what_it_is_built_on():
    cut = CUT.read_text()
    assert WIDGET.findall(cut) == ["mouse", "sortable"]
    for definition in ("$.widget = function", "$.expr.pseudos", "$.fn.scrollParent = ", "$.ui.ie = "):
        assert definition in cut, definition


def test_it_keeps_the_licence_of_the_build_and_of_every_module():
    cut = CUT.read_text()
    assert cut.startswith("/*! jQuery UI - v") and "Licensed MIT */" in cut[:500]
    assert cut.count("Released under the MIT license") == 5


def test_the_cut_is_a_fraction_of_the_whole_build():
    assert CUT.stat().st_size < WHOLE.stat().st_size * 0.2


def code(path):
    text = path.read_text()
    return HTML_COMMENT.sub("", JINJA_COMMENT.sub("", text)) if path.suffix == ".html" else text


def left_out():
    """Every widget of the full build that the cut does not have, and its datepicker, which
    is a plain plugin: a call to one of them would find nothing on the cross compare, and on
    any other page jQuery UI is not loaded at all."""
    return (set(WIDGET.findall(WHOLE.read_text())) - set(WIDGET.findall(CUT.read_text())) - BOOTSTRAP_NAMES) | {"datepicker"}


def calls(text, names):
    return re.findall(r"\.(%s)\(" % "|".join(sorted(names)), text)


def test_no_page_calls_a_widget_the_cut_leaves_out():
    names = left_out()
    sources = [*TEMPLATES.rglob("*.html"), *(path for path in STATIC.rglob("*.js") if not path.name.startswith("jquery-ui"))]
    callers = {f"{path.relative_to(ROOT)}: .{name}(" for path in sources for name in calls(code(path), names)}
    assert callers == set()


def test_the_check_would_catch_a_call():
    names = left_out()
    assert calls('$("#samples").draggable(); $(dialog).dialog({modal: true}); $("#tabs").tabs()', names) == ["draggable", "dialog", "tabs"]
    assert calls('$("#sortable").sortable(); $(el).tooltip("show"); $(el).button("toggle")', names) == []
