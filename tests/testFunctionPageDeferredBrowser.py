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
CONTROLS = "#showCycles, #showLoops, #loopBgFill, #enableTooltip"


def _with_threshold(page, blocks):
    """Serve main.js with the deferral threshold at `blocks`."""
    def rewrite(route):
        response = route.fetch()
        body, count = THRESHOLD_RE.subn(f"var CFG_DEFERRED_LAYOUT_BLOCKS = {blocks};", response.text())
        assert count == 1, "main.js no longer declares CFG_DEFERRED_LAYOUT_BLOCKS as expected"
        route.fulfill(response=response, body=body)
    page.route("**/static/trace_CFG/main.js", rewrite)


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
    browser_page.on("request", lambda request: loop_requests.append(request.url) if "findLoops" in request.url else None)
    _open(browser_page, live_server, errors)
    browser_page.wait_for_timeout(300)
    before = _state(browser_page)
    assert before["prompt"] and before["nodes"] == 0 and before["code"] > 3
    assert before["disabled"] == [True] * 4
    assert browser_page.inner_text("#cfgDeferredBlocks") == str(before["code"])
    assert loop_requests == []

    browser_page.click("#drawGraph")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    after = _state(browser_page)
    assert not after["prompt"]
    assert after["nodes"] == after["code"] == before["code"]  # one code panel, not two
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
    assert "could not be laid out" in state["note"]
    assert state["disabled"] == [True] * 4
    assert errors == []
