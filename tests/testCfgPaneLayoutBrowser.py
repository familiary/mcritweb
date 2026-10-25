#!/usr/bin/python
"""The panes of the control flow graph viewer on a phone, in a browser.

trace_CFG/cfg_style.css floats the graph and the code (or the two graphs) side by side at
50% each, at every width, and makes them as tall as the window less the 106px above them,
in `vh`. On a phone that was two 147px slivers: the graph a speck, the code cut after twenty
characters. On the offline harness of testFunctionVsBrowser.py; like it, this skips where
playwright is not installed, CI among them, and tests/testCfgPaneLayout.py checks the
stylesheet in CI.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page

#: page -> (its path, its two panes)
PAGES = {
    "function": (f"/explore/functions/{harness.FUNCTION_A}", ["#xcfg_left", "#xcfg_text_right"]),
    "comparison": (f"/data/matches/function/{harness.FUNCTION_A}/{harness.FUNCTION_B}", ["#xcfg_left", "#xcfg_right"]),
}

PANES = "(ids) => ids.map((id) => { const box = document.querySelector(id).getBoundingClientRect(); return [box.left, box.top, box.width, box.height]; })"

PAGE_WIDTH = "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"


@pytest.mark.parametrize("name", sorted(PAGES))
def test_on_a_phone_the_panes_stack_and_take_the_whole_width(browser_page, live_server, name):
    path, panes = PAGES[name]
    browser_page.set_viewport_size({"width": 390, "height": 844})
    browser_page.goto(live_server + path)

    (left_a, top_a, width_a, height_a), (left_b, top_b, width_b, height_b) = browser_page.evaluate(PANES, panes)
    scroll_width, client_width = browser_page.evaluate(PAGE_WIDTH)
    room = browser_page.evaluate("() => document.getElementById('xcfg_container').clientWidth")
    assert scroll_width <= client_width
    assert min(width_a, width_b) >= room - 1, f"panes {width_a}px and {width_b}px wide in a {room}px column"
    assert top_b >= top_a + height_a - 1, "the second pane is not below the first"
    # most of the window each, so that a pane and the controls above it fit on one screen
    assert abs(height_a - 0.7 * 844) <= 1 and abs(height_b - 0.7 * 844) <= 1, (height_a, height_b)


@pytest.mark.parametrize("name", sorted(PAGES))
def test_on_a_wide_screen_the_panes_stay_side_by_side(browser_page, live_server, name):
    path, panes = PAGES[name]
    browser_page.set_viewport_size({"width": 1280, "height": 800})
    browser_page.goto(live_server + path)

    (left_a, top_a, width_a, height_a), (left_b, top_b, width_b, height_b) = browser_page.evaluate(PANES, panes)
    assert top_a == top_b and left_b >= left_a + width_a - 1
    assert abs(width_a - width_b) <= 1
    assert height_a == height_b == 800 - 106

