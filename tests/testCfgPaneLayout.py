#!/usr/bin/python
"""The panes of the control flow graph viewer: their size, and what is shown in them.

trace_CFG/cfg_style.css is vendored and floats the panes side by side at 50% each at every
width, as tall as the window less the 106px above them in `vh`; on a phone that was two
147px slivers. style.css stacks them below 768px and sizes them in `svh`. The browser side is
tests/testCfgPaneLayoutBrowser.py, which CI does not run.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"
STYLESHEET = ROOT / "static" / "style.css"
PANES = r"main #xcfg_left,\s*main #xcfg_right,\s*main #xcfg_text_right\s*\{"


def test_the_panes_stack_on_a_phone():
    css = STYLESHEET.read_text()

    narrow = re.search(r"@media \(max-width: 767\.98px\)\s*\{\s*" + PANES + r"([^}]*)\}", css)
    assert narrow, "nothing stacks the panes below 768px"
    assert re.search(r"float:\s*none", narrow.group(1)) and re.search(r"width:\s*100%", narrow.group(1))


def test_the_panes_are_as_tall_as_the_window_with_its_bars_shown():
    """100vh is the window without a phone browser's bars, taller than what shows while
    they are; svh is the window with them, and does not change as they come and go."""
    css = STYLESHEET.read_text()

    assert re.search(r"\n" + PANES + r"\s*height:\s*calc\(100svh - 106px\);", css)
    assert re.search(r"#xcfg_combined\s*\{[^}]*height:\s*calc\(100svh - 106px\);", css)
    assert "100vh" not in css


def test_a_graph_note_sits_inside_its_pane():
    """The "Loading" note and the combined graph's error sat 400px down a pane that is the
    window's height less 106px, which on a landscape phone is 284px."""
    templates = (ROOT / "templates" / "single_function.html").read_text() + (ROOT / "templates" / "result_compare_function_vs.html").read_text()
    assert "margin-top:400px" not in templates.replace(" ", "")
    for note in ["loading", "loading_a", "loading_b", "loading_c", "combined_error"]:
        assert re.search(rf'<div id="{note}" class="hidden graph-note[ "]', templates), note

    rule = re.search(r"\.graph-note\s*\{([^}]*)\}", STYLESHEET.read_text())
    assert rule and re.search(r"margin-top:\s*min\(400px, calc\(50svh - 73px\)\)", rule.group(1))
