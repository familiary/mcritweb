#!/usr/bin/python
"""The function page lays its graph out in a worker, driven in a browser.

dagre's layout of a large function took seconds on the page's main thread, which froze
the page. main.js now measures the blocks on the page, has `static/trace_CFG/layout_worker.js`
lay them out and draws the answer. These tests hold the drawing to the one the page makes
without a worker, and check that a worker that fails, or never starts, does not leave the
page without a graph.

On the offline harness of testFunctionVsBrowser.py; like it, this skips where playwright
is not installed, CI among them.
"""

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page

# a function with nested loops and one without, both from the captured corpus
FUNCTIONS = [harness.FUNCTION_A, harness.FUNCTION_B]

DRAWING = """() => JSON.stringify([
    [...document.querySelectorAll('#graphContainer g.node')].map(n => [n.__data__, n.getAttribute('transform'),
        ...['x', 'y', 'width', 'height'].map(a => n.querySelector('rect').getAttribute(a))]),
    [...document.querySelectorAll('#graphContainer g.edgePath path')].map(p => [p.parentNode.__data__, p.getAttribute('d')]),
    document.querySelector('#graphContainer g').getAttribute('transform'),
])"""


def scroll_to_the_graph(page):
    """The page loads its graph viewer once the graph comes into view, and the graph starts
    below the first screen."""
    page.evaluate("document.getElementById('xcfg_top').scrollIntoView()")


def _open(page, live_server, function_id, errors):
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{live_server}/explore/functions/{function_id}")
    scroll_to_the_graph(page)
    page.wait_for_selector("#graphContainer g.node", state="attached")
    # the controls are enabled once everything after the drawing has run
    page.wait_for_function("!document.getElementById('showCycles').disabled")


def _workers(page):
    started = []
    page.on("worker", lambda worker: started.append(worker.url))
    return started


@pytest.mark.parametrize("function_id", FUNCTIONS)
def test_the_graph_is_laid_out_in_the_worker_as_on_the_page(browser_page, live_server, function_id):
    """Every block and edge where, and as large as, the page's own layout puts them, and
    the same fit."""
    errors = []
    started = _workers(browser_page)
    _open(browser_page, live_server, function_id, errors)
    in_worker = json.loads(browser_page.evaluate(DRAWING))
    assert [url.rsplit("/", 1)[-1] for url in started] == ["layout_worker.js"]

    # the same page in a browser without workers lays the graph out itself, as before
    browser_page.add_init_script("delete window.Worker;")
    _open(browser_page, live_server, function_id, errors)
    on_the_page = json.loads(browser_page.evaluate(DRAWING))

    assert in_worker[0] and in_worker == on_the_page
    assert errors == []


def test_the_drawing_is_the_worker_s_layout(browser_page, live_server):
    """A worker that moves every block 100 px to the right moves the drawing with it, so
    the page draws what the worker answered rather than laying the graph out itself."""
    errors = []
    _open(browser_page, live_server, harness.FUNCTION_A, errors)
    own = [node[1] for node in json.loads(browser_page.evaluate(DRAWING))[0]]

    def shifted(route):
        response = route.fetch()
        body = response.text().replace(
            "self.postMessage({ graph: answer });",
            "answer.nodes.forEach(function (node) { if ('x' in node.value) { node.value.x += 100; } });"
            " self.postMessage({ graph: answer });")
        assert "node.value.x += 100" in body
        route.fulfill(response=response, body=body)
    browser_page.route("**/static/trace_CFG/layout_worker.js", shifted)
    _open(browser_page, live_server, harness.FUNCTION_A, errors)
    drawn = [node[1] for node in json.loads(browser_page.evaluate(DRAWING))[0]]

    def x(transform):
        return float(transform.split("(")[1].split(",")[0])
    assert len(drawn) == len(own) > 0
    assert all(abs(x(b) - x(a) - 100) < 0.01 for a, b in zip(own, drawn))
    assert errors == []


def test_a_layout_the_worker_cannot_make_gives_the_button_back(browser_page, live_server):
    errors = []
    browser_page.route("**/static/trace_CFG/layout_worker.js", lambda route: route.fulfill(
        content_type="application/javascript",
        body="self.onmessage = function () { self.postMessage({ error: 'injected layout failure' }); };"))
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.goto(f"{live_server}/explore/functions/{harness.FUNCTION_A}")
    scroll_to_the_graph(browser_page)
    browser_page.wait_for_selector("#cfgDeferred:not(.hidden)")

    assert "could not be laid out" in browser_page.inner_text("#cfgDeferredNote")
    assert browser_page.is_enabled("#drawGraph")
    assert browser_page.locator("#graphContainer g.node").count() == 0
    assert browser_page.locator("#text_code p").count() > 0
    # what the measuring renderer set up on the empty graph is gone
    assert browser_page.evaluate("Object.keys(document.getElementById('graphContainer')).filter(k => k.includes('zoom'))") == []
    assert errors == []


def test_a_worker_that_does_not_load_leaves_the_layout_to_the_page(browser_page, live_server):
    errors = []
    browser_page.route("**/static/trace_CFG/layout_worker.js", lambda route: route.fulfill(status=404, body=""))
    _open(browser_page, live_server, harness.FUNCTION_A, errors)

    assert browser_page.locator("#graphContainer g.node").count() > 0
    assert errors == []


def test_a_large_graph_shows_its_code_first_and_follows_without_a_click(browser_page, live_server):
    """Above CFG_CODE_FIRST_BLOCKS (lowered to 3 here) the code panel comes first, and the
    graph is laid out in the background and drawn when ready, without anyone asking; focus
    stays where the reader has it."""
    errors, held, lookups = [], [], []

    def rewrite(route):
        response = route.fetch()
        body = response.text().replace("var CFG_CODE_FIRST_BLOCKS = 250;", "var CFG_CODE_FIRST_BLOCKS = 3;")
        assert "CFG_CODE_FIRST_BLOCKS = 3;" in body
        route.fulfill(response=response, body=body)
    browser_page.route("**/static/trace_CFG/main.js", rewrite)
    browser_page.route("**/explore/findLoops/", lambda route: held.append(route))
    browser_page.on("request", lambda request: lookups.append(request.url) if "getPicBlockMatches" in request.url else None)
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.goto(f"{live_server}/explore/functions/{harness.FUNCTION_A}")
    scroll_to_the_graph(browser_page)
    browser_page.wait_for_function("document.querySelectorAll('#text_code p').length > 0")
    browser_page.wait_for_function("document.getElementById('drawGraph').disabled")

    assert browser_page.locator("#graphContainer g.node").count() == 0
    assert browser_page.is_visible("#cfgDeferred")
    assert browser_page.inner_text("#cfgDeferredNote").startswith("Laying out ")
    assert browser_page.evaluate("document.activeElement.id") != "cfgDeferredNote"
    # the code panel's lookups have all been answered; the loop request is still held
    browser_page.wait_for_function("document.querySelectorAll('#text_code p').length > 0")
    browser_page.wait_for_timeout(1000)
    lookups_before = len(lookups)

    held[0].continue_()
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    browser_page.wait_for_function("!document.getElementById('showCycles').disabled")
    browser_page.wait_for_load_state("networkidle")

    assert not browser_page.is_visible("#cfgDeferred")
    assert browser_page.locator("#graphContainer g.node").count() == browser_page.locator("#text_code p").count()
    assert browser_page.evaluate("document.activeElement.id") != "showCycles"
    assert len(lookups) == lookups_before, "drawing the graph built the code panel a second time"
    assert errors == []
