#!/usr/bin/python
"""The single function page's deferred graph (#267), driven in a browser.

Above `CFG_DEFERRED_LAYOUT_BLOCKS` blocks the page shows a function's code at once and
lays its control flow graph out only when asked, because dagre's layout freezes the
page for seconds. All of that is script, so it is watched here in Chromium, on the
offline harness of testFunctionVsBrowser.py. The captured functions are far below the
real threshold; the tests that need a deferred graph lower it by rewriting main.js on
its way to the page, which leaves the code under test otherwise as served.

Like testFunctionVsBrowser.py this skips where playwright is not installed, CI among them.
"""

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

# the offline app, its loopback server and a logged-in page, as testFunctionVsBrowser.py has them
fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page
FUNCTION_A = harness.FUNCTION_A

THRESHOLD_RE = re.compile(r"var CFG_DEFERRED_LAYOUT_BLOCKS = \d+;")
# where a failing layout is injected: the start of showGraph(), and a line it runs after it has
# built the code panel - which it skips when the panel came first
SHOW_GRAPH_START = "function showGraph(isTraceSupplied) {"
AFTER_CODE_PANEL = "var bBox = svg.node().getBBox();"
CONTROLS = "#showCycles, #showLoops, #loopBgFill, #enableTooltip"


def _with_threshold(page, blocks=None, fail_at=None):
    """Serve main.js with the deferral threshold at `blocks`, and with a layout that throws
    at the line `fail_at` if given."""
    def rewrite(route):
        response = route.fetch()
        body = response.text()
        if blocks is not None:
            body, count = THRESHOLD_RE.subn(f"var CFG_DEFERRED_LAYOUT_BLOCKS = {blocks};", body)
            assert count == 1, "main.js no longer declares CFG_DEFERRED_LAYOUT_BLOCKS as expected"
        if fail_at:
            assert body.count(fail_at) == 1, "main.js no longer has the line the failure is injected at"
            failure = 'throw new Error("injected layout failure");'
            body = body.replace(fail_at, f"{fail_at} {failure}" if fail_at.endswith("{") else f"{failure} {fail_at}")
        route.fulfill(response=response, body=body)
    page.route("**/static/trace_CFG/main.js", rewrite)


def _count_lookups(page):
    """The block match lookups the code panel sends, one per block with a picblockhash."""
    lookups = []
    page.on("request", lambda request: lookups.append(request.url) if "getPicBlockMatches" in request.url else None)
    return lookups


def _state(page):
    return page.evaluate("""(controls) => ({
        prompt: !document.getElementById('cfgDeferred').classList.contains('hidden'),
        nodes: document.querySelectorAll('#graphContainer g.node').length,
        code: document.querySelectorAll('#text_code p').length,
        disabled: [...document.querySelectorAll(controls)].map(e => e.disabled),
        note: document.getElementById('cfgDeferredNote').textContent,
        focused: document.activeElement && document.activeElement.id,
    })""", CONTROLS)


def _open(page, live_server, errors):
    page.on("pageerror", lambda error: errors.append(str(error)))
    # script errors only: Chromium also logs the failed requests some tests force on purpose
    page.on("console", lambda message: errors.append("console: " + message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
    page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    page.wait_for_function("document.querySelectorAll('#text_code p').length > 0")


def test_a_small_graph_is_drawn_on_load(browser_page, live_server):
    errors = []
    _open(browser_page, live_server, errors)
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    state = _state(browser_page)
    assert not state["prompt"]
    assert state["nodes"] == state["code"] > 0
    assert state["disabled"] == [False] * 4
    assert errors == []


def test_a_large_graph_waits_for_the_click(browser_page, live_server):
    errors, loop_requests = [], []
    _with_threshold(browser_page, 3)
    lookups = _count_lookups(browser_page)
    browser_page.on("request", lambda request: loop_requests.append(request.url) if "findLoops" in request.url else None)
    _open(browser_page, live_server, errors)
    browser_page.wait_for_load_state("networkidle")
    before = _state(browser_page)
    lookups_before = len(lookups)
    assert lookups_before > 0, "the code panel sent no block lookups, so a second panel would go unnoticed"
    assert before["prompt"] and before["nodes"] == 0 and before["code"] > 3
    assert before["disabled"] == [True] * 4
    assert browser_page.inner_text("#cfgDeferredBlocks") == str(before["code"])
    assert loop_requests == []

    browser_page.click("#drawGraph")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    after = _state(browser_page)
    assert not after["prompt"]
    browser_page.wait_for_load_state("networkidle")
    assert after["nodes"] == after["code"] == before["code"]
    assert len(lookups) == lookups_before, "drawing the graph built the code panel a second time"
    assert after["disabled"] == [False] * 4
    assert after["focused"] == "showCycles"
    assert len(loop_requests) == 1
    assert errors == []


def test_a_failed_loop_request_gives_the_button_back(browser_page, live_server):
    errors, attempts = [], []
    _with_threshold(browser_page, 3)

    def fail_once(route):
        attempts.append(1)
        if len(attempts) == 1:
            route.fulfill(status=500, body="boom")
        else:
            route.continue_()
    browser_page.route("**/findLoops/", fail_once)
    _open(browser_page, live_server, errors)

    browser_page.click("#drawGraph")
    browser_page.wait_for_function("!document.getElementById('drawGraph').disabled")
    failed = _state(browser_page)
    assert failed["prompt"] and failed["nodes"] == 0
    assert "not drawn" in failed["note"] and "HTTP 500" in failed["note"]

    browser_page.click("#drawGraph")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    assert not _state(browser_page)["prompt"]
    assert errors == []


def test_an_answer_that_is_not_loop_data_is_reported_for_a_small_graph_too(browser_page, live_server):
    """Drawn on load, a graph used to leave an empty pane and no code when findLoops did not
    answer with loop data - a login page once the session has expired, say."""
    errors = []
    browser_page.route("**/findLoops/", lambda route: route.fulfill(status=200, body="<!doctype html><p>log in</p>"))
    _open(browser_page, live_server, errors)
    browser_page.wait_for_function("!document.getElementById('cfgDeferred').classList.contains('hidden')")
    state = _state(browser_page)
    assert state["nodes"] == 0 and state["code"] > 0
    assert "did not answer with loop data" in state["note"]
    assert state["disabled"] == [True] * 4
    assert errors == []


@pytest.mark.parametrize("deferred", [False, True])
def test_a_layout_that_fails_after_the_code_panel_keeps_one_panel(browser_page, live_server, deferred):
    """On load, showGraph() builds the code panel near its end, and a layout failing after that
    must not build a second one. After a click the panel is already there, and the note gets
    the focus back from the prompt the layout had hidden."""
    errors = []
    if deferred:
        _with_threshold(browser_page, 3, fail_at=SHOW_GRAPH_START)
    else:
        _with_threshold(browser_page, fail_at=AFTER_CODE_PANEL)
    lookups = _count_lookups(browser_page)
    _open(browser_page, live_server, errors)
    if deferred:
        browser_page.wait_for_load_state("networkidle")
        lookups_before = len(lookups)
        browser_page.click("#drawGraph")
    browser_page.wait_for_function("document.getElementById('cfgDeferredNote').textContent.includes('not drawn')")
    browser_page.wait_for_load_state("networkidle")
    state = _state(browser_page)
    assert state["prompt"] and state["nodes"] == 0
    assert "could not be laid out" in state["note"]
    assert state["disabled"] == [True] * 4
    assert len({p for p in lookups}) == len(lookups), "a block was looked up twice"
    if deferred:
        assert len(lookups) == lookups_before
        assert state["focused"] == "cfgDeferredNote"
    assert errors == []
