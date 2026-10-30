#!/usr/bin/python
"""The single function page loads its graph viewer when the graph is wanted, in a browser.

d3, graphlib, dagre-d3 and the CFG scripts are only used by the graph and the code beside
it, which start below the first screen at Lighthouse's phone and desktop sizes. The page
loads them, and fetches, lays out and draws the graph, once the graph comes into view or
the keyboard reaches the link above it. On the offline harness of testFunctionVsBrowser.py.

Like testFunctionVsBrowser.py this skips where playwright is not installed, CI among them.
"""

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

import testFunctionVsBrowser as harness  # noqa: E402

fake_mcrit = harness.fake_mcrit
live_server = harness.live_server
browser_page = harness.browser_page
FUNCTION_A = harness.FUNCTION_A

#: the viewer, in the order its scripts build on each other
VIEWER = ["trace_CFG/lib/d3.min.js", "trace_CFG/lib/graphlib.min.js", "trace_CFG/lib/graphlib-dot.min.js",
          "trace_CFG/lib/dagre-d3.min.js", "trace_CFG/lib/convexhull.js", "trace_CFG/main.js",
          "trace_CFG/fnManip.js", "trace_CFG/loopCollapser.js", "trace_CFG/loopify_dagre.js"]

#: Lighthouse's phone and desktop sizes
VIEWPORTS = [{"width": 412, "height": 823}, {"width": 1350, "height": 940}]

#: an IntersectionObserver that never reports, so that only the other trigger can draw
SILENT_OBSERVER = "window.IntersectionObserver = class { observe() {} disconnect() {} };"

CONTROLS = ["#showCycles", "#showLoops", "#loopBgFill", "#enableTooltip"]


def viewer_requests(page):
    requested = []
    page.on("request", lambda request: requested.append(request.url.split("?")[0].split("/static/", 1)[1])
            if "/static/" in request.url and request.url.split("?")[0].split("/static/", 1)[1] in VIEWER else None)
    return requested


def thrown_on(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


def scroll_to_the_graph(page):
    page.evaluate("document.getElementById('xcfg_top').scrollIntoView()")


def drawn(page):
    page.wait_for_selector("#graphContainer g.node", state="attached")
    page.wait_for_function("() => !document.getElementById('showCycles').disabled")


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["mobile", "desktop"])
def test_the_viewer_is_loaded_only_once_the_graph_is_wanted(browser_page, live_server, viewport):
    browser_page.set_viewport_size(viewport)
    requests, errors = viewer_requests(browser_page), thrown_on(browser_page)
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    browser_page.wait_for_load_state("networkidle")
    assert requests == []
    assert browser_page.locator("#text_code p").count() == 0

    scroll_to_the_graph(browser_page)
    drawn(browser_page)
    # each once, in order; the layout worker then loads its own dagre-d3 (layout_worker.js)
    assert requests[:len(VIEWER)] == VIEWER
    assert requests[len(VIEWER):] == ["trace_CFG/lib/dagre-d3.min.js"]
    assert browser_page.locator("#text_code p").count() > 0
    assert errors == []


def test_both_triggers_load_the_viewer_once(browser_page, live_server):
    """Scrolled to and then reached by the keyboard, the graph is still fetched and the
    viewer loaded only once."""
    requests, errors = viewer_requests(browser_page), thrown_on(browser_page)
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    scroll_to_the_graph(browser_page)
    drawn(browser_page)
    browser_page.focus("#xcfg_top a")
    browser_page.wait_for_timeout(500)
    assert requests.count("trace_CFG/main.js") == 1
    assert errors == []


def test_a_graph_only_peeking_in_at_the_bottom_waits(browser_page, live_server):
    """The observer watches the upper three quarters of the window. The window is sized
    around where the graph starts on this page, so that it starts in the bottom quarter,
    and then in the upper part."""
    browser_page.set_viewport_size(VIEWPORTS[1])
    requests = viewer_requests(browser_page)
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    browser_page.wait_for_load_state("networkidle")
    top = browser_page.evaluate("document.getElementById('xcfg_container').getBoundingClientRect().top")
    assert top > VIEWPORTS[1]["height"], "the graph is in the first screen; this test needs it below"

    browser_page.set_viewport_size({"width": VIEWPORTS[1]["width"], "height": int(top / 0.85)})
    browser_page.wait_for_timeout(500)
    assert requests == []
    browser_page.set_viewport_size({"width": VIEWPORTS[1]["width"], "height": int(top / 0.6)})
    drawn(browser_page)
    assert requests[:len(VIEWER)] == VIEWER


def test_the_controls_wait_for_the_graph(browser_page, live_server):
    """What they act on is not there before the graph is drawn."""
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    browser_page.wait_for_load_state("networkidle")
    assert [browser_page.is_disabled(control) for control in CONTROLS] == [True] * 4

    scroll_to_the_graph(browser_page)
    drawn(browser_page)
    assert [browser_page.is_enabled(control) for control in CONTROLS] == [True] * 4


def test_the_keyboard_reaching_the_link_above_the_graph_draws_it(browser_page, live_server):
    browser_page.add_init_script(SILENT_OBSERVER)
    errors = thrown_on(browser_page)
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    browser_page.wait_for_load_state("networkidle")
    assert browser_page.locator("#graphContainer g.node").count() == 0

    browser_page.focus("#xcfg_top a")
    drawn(browser_page)
    assert errors == []


@pytest.mark.parametrize("script", ["trace_CFG/lib/d3.min.js", "trace_CFG/main.js"])
def test_a_viewer_script_that_does_not_load_says_so(browser_page, live_server, script):
    browser_page.route("**/static/" + script + "*", lambda route: route.fulfill(status=404, body="gone"))
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    assert browser_page.is_hidden("#viewer_error")
    scroll_to_the_graph(browser_page)
    browser_page.wait_for_selector("#viewer_error", state="visible")
    assert "could not be loaded" in browser_page.inner_text("#viewer_error")
    assert browser_page.locator("#graphContainer g.node").count() == 0


def test_without_a_script_the_drawing_can_do_without_the_graph_is_still_drawn(browser_page, live_server):
    """loopCollapser.js is not needed to draw. A page that loaded the viewer from plain
    script tags drew the graph without it, and this one does too, under the message."""
    browser_page.route("**/static/trace_CFG/loopCollapser.js*", lambda route: route.fulfill(status=404, body="gone"))
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    scroll_to_the_graph(browser_page)
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    assert browser_page.is_visible("#viewer_error")


def test_the_viewer_scripts_run_in_order_however_they_arrive(browser_page, live_server):
    """Each script builds on the ones before it. Added by a script, they would run as each
    arrives, so d3 arriving last would break every one after it."""
    def late(route):
        browser_page.wait_for_timeout(700)
        route.continue_()
    browser_page.route("**/static/trace_CFG/lib/d3.min.js*", late)
    errors = thrown_on(browser_page)
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    scroll_to_the_graph(browser_page)
    drawn(browser_page)
    assert errors == []


def test_without_intersection_observer_the_graph_is_drawn_on_load(browser_page, live_server):
    browser_page.add_init_script("delete window.IntersectionObserver;")
    browser_page.goto(f"{live_server}/explore/functions/{FUNCTION_A}")
    drawn(browser_page)
