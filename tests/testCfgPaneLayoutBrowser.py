#!/usr/bin/python
"""The panes of the control flow graph viewer on a phone and in a short window, in a browser.

trace_CFG/cfg_style.css floats the graph and the code (or the two graphs) side by side at
50% each, at every width, and makes them as tall as the window less the 106px above them,
in `vh`. On a phone that was two 147px slivers: the graph a speck, the code cut after twenty
characters. And the "Loading" and error notes sat 400px down a pane that can be shorter
than that. On the offline harness of testFunctionVsBrowser.py; like it, this skips where
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


def note_inside_its_pane(page, note, pane):
    return page.evaluate(
        "([note, pane]) => { const n = document.querySelector(note), p = document.querySelector(pane);"
        " n.classList.remove('hidden'); p.style.display = p.style.display === 'none' ? 'block' : p.style.display;"
        " const a = n.getBoundingClientRect(), b = p.getBoundingClientRect();"
        " return [a.top >= b.top && a.bottom <= b.bottom && a.height > 0, a.top - b.top, a.bottom, b.bottom]; }",
        [note, pane],
    )


@pytest.mark.parametrize("name, note, pane", [
    ("function", "#loading", "#xcfg_left"),
    ("comparison", "#loading_c", "#xcfg_combined"),
    ("comparison", "#combined_error", "#xcfg_combined"),
])
def test_on_a_short_window_the_graph_notes_show_inside_their_pane(browser_page, live_server, name, note, pane):
    """A landscape phone's pane is 284px tall, and the note was 400px down it, hidden by
    the pane's overflow: neither "Loading. Please Wait!" nor the combined graph's error
    ever showed there."""
    browser_page.set_viewport_size({"width": 844, "height": 390})
    browser_page.goto(live_server + PAGES[name][0])

    inside, offset, note_bottom, pane_bottom = note_inside_its_pane(browser_page, note, pane)
    assert inside, f"{note} is {offset}px down its pane, its bottom at {note_bottom} below the pane's {pane_bottom}"
