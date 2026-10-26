#!/usr/bin/python
"""Drives a real browser over the result pages whose score tooltips changed.

AGENTS.md asks for the affected pages to be exercised when a template changes, and
the rest of the suite reads rendered HTML as text - which cannot see the two things
that only exist once a browser has the page. The hover text is an *attribute*, so
`&#10;` has to survive parsing as a newline; and the tooltip itself is drawn by
`hint.css` from `content: attr(data-hint)`, so nothing about it is in the markup.

Skipped rather than failed where playwright or its Chromium build is missing, so the
offline suite still runs on a machine that has neither.
"""

import logging
import re
import threading
import unittest

import pytest
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed").sync_playwright

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The score tooltip, as the DOM has it - `&#10;` is a newline by the time it is read
#: back off the attribute, which is the half the offline test cannot check.
SCORE_TOOLTIP = re.compile(r"Bytes:\s*(-?\d+\.\d+)\s*/\s*(\d+\.\d+)\s*\nPercent:\s*(-?\d+\.\d+)%")

#: Both halves of the fraction and the percentage are rendered to two decimals.
TWO_DECIMALS = 0.005 + 1e-6

#: The four templates that render a score tooltip, reached the way `data.result`
#: dispatches to them - the same triples `testResultPages.RESULT_PAGES` documents.
WALK = [
    ("matches_for_sample", "", "result_compare_all.html"),
    ("matches_for_query", "", "result_compare_all.html"),
    ("matches_for_sample_vs", "", "result_compare_vs.html"),
    ("matches_for_sample", "?famid=1", "result_compare_family.html"),
    ("matches_for_sample_vs", "?samid=3", "result_compare_sample.html"),
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Serve the captured reports, so the pages have real scores to render."""
    return corpus_mcrit


@pytest.fixture
def live_server(app, make_user):
    """The app on a real socket - a browser cannot drive a Flask test client."""
    make_user(role="visitor", username="walker")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture
def browser_page(live_server):
    """A logged-in page, and the console errors it collected along the way."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # playwright raises its own Error subclass
            pytest.skip(f"chromium is not available to playwright: {error}")
        try:
            page = browser.new_page()
            console_errors = []
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.goto(f"{live_server}/login")
            page.fill("#username", "walker")
            page.fill("#inputPassword", "password")
            page.click("button[type=submit]")
            page.wait_for_load_state()
            yield page, console_errors
        finally:
            browser.close()


def test_score_tooltips_hold_up_in_a_browser(live_server, browser_page):
    """Every score tooltip on every affected page states a fraction that is its own
    percentage, and the cell beside it is that percentage rounded (issue #7).
    """
    page, console_errors = browser_page
    inconsistent = []
    unrounded = []
    seen = 0

    for report, query, template in WALK:
        page.goto(f"{live_server}/data/result/{job_id_of(report)}{query}")
        page.wait_for_load_state()
        cells = page.eval_on_selector_all(
            "span[data-hint]",
            "nodes => nodes.map(node => [node.getAttribute('data-hint'), node.textContent.trim()])",
        )
        for hint, shown in cells:
            match = SCORE_TOOLTIP.search(hint)
            if match is None:
                continue
            seen += 1
            numerator, divisor, percent = (float(group) for group in match.groups())
            where = f"{template}{query} {hint.splitlines()[0]}"
            if abs(100.0 * numerator / divisor - percent) > TWO_DECIMALS:
                inconsistent.append((where, numerator, divisor, percent))
            if abs(int(shown) - percent) > 0.5 + TWO_DECIMALS:
                unrounded.append((where, percent, shown))

    assert seen, "the walk found no score tooltip on any page"
    assert not inconsistent, f"hover text states a fraction that is not its percentage: {inconsistent}"
    assert not unrounded, f"score cell is not its percentage rounded: {unrounded}"
    assert not console_errors, f"the walk logged console errors: {console_errors}"


def test_the_tooltip_css_draws_the_corrected_text(live_server, browser_page):
    """The numbers above are only in an attribute. `hint.css` is what turns one into
    visible text, so read back what the browser actually composes on hover - if the
    attribute were malformed this is where it would show up empty or truncated.
    """
    page, _ = browser_page
    page.goto(f"{live_server}/data/result/{job_id_of('matches_for_sample')}")
    cell = page.locator("span[data-hint*='Frequency Weighted Score (Library Excluded)']").first
    cell.hover()

    drawn = cell.evaluate("node => getComputedStyle(node, ':after').content")
    assert "Bytes:" in drawn and "Percent:" in drawn, f"hint.css drew {drawn!r}"

    numerator, divisor, percent = (float(group) for group in SCORE_TOOLTIP.search(cell.get_attribute("data-hint")).groups())
    assert f"{numerator:.2f}" in drawn and f"{divisor:.2f}" in drawn
    assert abs(100.0 * numerator / divisor - percent) <= TWO_DECIMALS


#: The pages with the tooltips that ran off the screen: the cross compare result (the job
#: table's "Total (since queued)", whose hint is the longest there is, the order buttons
#: and the matrix squares, in a pane that scrolls) and a result page of score cells.
OVERFLOW_WALK = [
    ("cross_compare", ""),
    ("matches_for_sample", ""),
]

#: Takes every hint.css tooltip off the page, so that what is left is the page's own width.
WITHOUT_TOOLTIPS = "[data-hint]::before, [data-hint]::after { display: none !important; }"

#: The rectangle the window and the element's scrolling ancestors leave visible to its
#: tooltip. A fixed box is clipped by none of them, unless one is its containing block.
VISIBLE_TO_TOOLTIP = """(element) => {
    const fixed = getComputedStyle(element, '::after').position === 'fixed';
    let escapes = fixed;
    let box = {left: 0, top: 0, right: document.documentElement.clientWidth, bottom: document.documentElement.clientHeight};
    for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (escapes && (style.transform !== 'none' || style.filter !== 'none' || style.contain !== 'none')) {
            escapes = false;
        }
        if (node !== element && !escapes && (style.overflowX !== 'visible' || style.overflowY !== 'visible')) {
            const r = node.getBoundingClientRect();
            box = {left: Math.max(box.left, r.left), top: Math.max(box.top, r.top), right: Math.min(box.right, r.right), bottom: Math.min(box.bottom, r.bottom)};
        }
    }
    return box;
}"""


def tooltip_box(page, element):
    """Where the browser drew `element`'s ::after - the box hint.css shows the tooltip in -
    as (left, top, right, bottom), or None when it drew none. No script API reaches the
    layout of a pseudo-element, so this asks through the DevTools protocol."""
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.getDocument", {"depth": 0})
        element.evaluate("node => { window.mcritProbe = node; }")
        handle = cdp.send("Runtime.evaluate", {"expression": "window.mcritProbe"})
        node = cdp.send("DOM.requestNode", {"objectId": handle["result"]["objectId"]})
        described = cdp.send("DOM.describeNode", {"nodeId": node["nodeId"], "depth": 1})
        after = [pseudo for pseudo in described["node"].get("pseudoElements", []) if pseudo.get("pseudoType") == "after"]
        if not after:
            return None
        try:
            quad = cdp.send("DOM.getBoxModel", {"backendNodeId": after[0]["backendNodeId"]})["model"]["border"]
        except Exception:  # noqa: BLE001 - playwright raises its own Error: the box is not laid out
            return None
    finally:
        cdp.detach()
    return min(quad[0::2]), min(quad[1::2]), max(quad[0::2]), max(quad[1::2])


@pytest.mark.parametrize("width", [390, 768, 1024])
@pytest.mark.parametrize("report,query", OVERFLOW_WALK)
def test_a_tooltip_nobody_is_looking_at_takes_no_room(live_server, browser_page, report, query, width):
    """hint.css hides a tooltip it is not showing, but leaves it laid out: the cross compare
    result was 1120px wide in a window of 390, 768 or 1024, for the "Total (since queued)"
    hint written out on one line, and every square of a cross compare matrix carried two
    such boxes, which was most of the time a large matrix took to draw. An idle tooltip has
    no box now, so the page is as wide as it is without any."""
    page, console_errors = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server}/data/result/{job_id_of(report)}{query}")
    page.wait_for_load_state()
    width_with = page.evaluate("document.documentElement.scrollWidth")
    drawn = page.eval_on_selector_all(
        "[data-hint]",
        "nodes => nodes.filter(node => getComputedStyle(node, '::after').display !== 'none').map(node => node.getAttribute('data-hint').slice(0, 40))",
    )
    page.add_style_tag(content=WITHOUT_TOOLTIPS)
    width_without = page.evaluate("document.documentElement.scrollWidth")

    assert drawn == [], f"tooltips laid out while nothing points at them: {drawn}"
    assert width_with == width_without, f"the tooltips nobody is looking at widen the page from {width_without}px to {width_with}px"
    assert not console_errors, console_errors


@pytest.mark.parametrize("width", [390, 768, 1280])
@pytest.mark.parametrize("report,query", OVERFLOW_WALK)
def test_a_tooltip_shows_whole_inside_the_window(live_server, browser_page, report, query, width):
    """A tooltip ran past the edge of the window, or was cut off by the scrolling pane it
    sits in: "Total (since queued)" ended at "...this job was queue" on a phone, and on
    every width the matrix squares of the first rows had the top of their tooltip cut off by
    the pane. Each tooltip on the page is hovered in turn and has to be drawn in full."""
    page, _ = browser_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server}/data/result/{job_id_of(report)}{query}")
    page.wait_for_load_state()
    hints = page.locator("[data-hint]:visible")
    assert hints.count(), "the page has no tooltip to hover"
    cut = []
    for index in range(hints.count()):
        hint = hints.nth(index)
        hint.scroll_into_view_if_needed()
        hint.hover()
        box = tooltip_box(page, hint)
        if box is None:
            cut.append((index, hint.get_attribute("data-hint")[:40], "not drawn"))
            continue
        visible = hint.evaluate(VISIBLE_TO_TOOLTIP)
        left, top, right, bottom = box
        if left < visible["left"] - 0.5 or top < visible["top"] - 0.5 or right > visible["right"] + 0.5 or bottom > visible["bottom"] + 0.5:
            cut.append((index, hint.get_attribute("data-hint")[:40], [round(edge) for edge in box], visible))
    assert not cut, f"{len(cut)} of {hints.count()} tooltips are not drawn whole: {cut[:5]}"


#: Whether an element's tooltip is on screen.
SHOWN = "(node) => { const after = getComputedStyle(node, '::after'); return after.display !== 'none' && after.visibility === 'visible'; }"

#: The tooltip of the focused element, if it has one: its text and whether it is on screen.
FOCUSED_HINT = """() => {
    const node = document.activeElement;
    if (!node || !node.matches('[data-hint]')) return null;
    const after = getComputedStyle(node, '::after');
    return [node.getAttribute('data-hint'), after.display !== 'none' && after.visibility === 'visible'];
}"""


def tab_to_a_hint(page, limit=150):
    """Press Tab until the focus is on an element with a tooltip; its tooltip, as
    FOCUSED_HINT has it, or None when the walk never lands on one."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        focused = page.evaluate(FOCUSED_HINT)
        if focused is not None:
            return focused
    return None


def focused_description(page):
    """The accessible description the browser gives the focused element."""
    cdp = page.context.new_cdp_session(page)
    try:
        handle = cdp.send("Runtime.evaluate", {"expression": "document.activeElement"})
        nodes = cdp.send("Accessibility.getPartialAXTree", {"objectId": handle["result"]["objectId"], "fetchRelatives": False})["nodes"]
    finally:
        cdp.detach()
    return " ".join(((nodes[0].get("description") or {}).get("value") or "").split())


def test_a_hint_takes_the_keyboard_focus_and_shows_its_tooltip(live_server, browser_page):
    """hint.css shows a tooltip on :focus as well as on :hover, but no element with one
    could take the focus, so 200 presses of Tab never showed one: what "Total (since
    queued)" measures was there for a mouse only. The label takes the focus now, its
    tooltip shows, and a screen reader is given the same text as its description."""
    page, console_errors = browser_page
    page.goto(f"{live_server}/data/result/{job_id_of('cross_compare')}")
    page.wait_for_load_state()

    focused = tab_to_a_hint(page)
    assert focused is not None, "Tab never reached an element with a tooltip"
    hint, shown = focused
    assert hint.startswith("Measured from when this job was queued"), f"the first tooltip Tab reaches is {hint!r}"
    assert shown, "the focused label's tooltip is not on screen"
    assert focused_description(page) == " ".join(hint.split())
    assert not console_errors, console_errors


def test_escape_puts_the_tooltip_away(live_server, browser_page):
    """A tooltip covers what is beside it, and hint.css kept it up for as long as the
    pointer or the focus stayed on its element, Escape or not (WCAG 1.4.13). Escape hides
    it now, until the focus or the pointer moves on to another element."""
    page, _ = browser_page
    page.goto(f"{live_server}/data/result/{job_id_of('cross_compare')}")
    page.wait_for_load_state()
    page.evaluate("() => new Promise((resolve) => whenReady(() => resolve(true)))")

    assert tab_to_a_hint(page) is not None, "Tab never reached an element with a tooltip"
    page.keyboard.press("Escape")
    assert page.evaluate(FOCUSED_HINT)[1] is False, "Escape left the focused label's tooltip on screen"
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(FOCUSED_HINT)[1] is True, "the tooltip stayed away after the focus left and came back"

    page.goto(f"{live_server}/data/result/{job_id_of('matches_for_sample')}")
    page.wait_for_load_state()
    page.evaluate("() => new Promise((resolve) => whenReady(() => resolve(true)))")
    cells = page.locator("td span[data-hint]")
    cells.nth(0).hover()
    assert cells.nth(0).evaluate(SHOWN)
    page.keyboard.press("Escape")
    assert not cells.nth(0).evaluate(SHOWN), "Escape left the hovered cell's tooltip on screen"
    box = cells.nth(0).bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + 1)
    assert not cells.nth(0).evaluate(SHOWN), "the tooltip came back while the pointer stayed on its cell"
    cells.nth(1).hover()
    assert cells.nth(1).evaluate(SHOWN), "the next cell's tooltip does not show once the pointer has moved on"


def test_the_pointer_can_rest_on_a_tooltip(live_server, browser_page):
    """WCAG 1.4.13 asks that the pointer can move onto a tooltip without it closing, so
    that it can be read under a magnifier. hint.css's let the pointer through to whatever
    was under it, and closed as soon as it left the element."""
    page, _ = browser_page
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{live_server}/data/result/{job_id_of('matches_for_sample')}")
    page.wait_for_load_state()
    cell = page.locator("td span[data-hint]").first
    cell.hover()
    left, top, right, bottom = tooltip_box(page, cell)
    # up out of the cell into the tooltip, which sits on top of it, and then across it
    cell_box = cell.bounding_box()
    page.mouse.move(cell_box["x"] + cell_box["width"] / 2, bottom - 4, steps=5)
    page.mouse.move((left + right) / 2, (top + bottom) / 2, steps=5)
    page.wait_for_timeout(400)
    assert cell.evaluate("node => node.matches(':hover')"), "the pointer on the tooltip is not on its element"
    assert cell.evaluate(SHOWN), "the tooltip closed when the pointer moved onto it"


if __name__ == "__main__":
    unittest.main()
