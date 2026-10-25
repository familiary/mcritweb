#!/usr/bin/python
"""The note a function page shows when findLoops skipped its loop analysis, in a browser.

findLoops answers a graph past the bounds of its loop analysis, or a body over
FIND_LOOPS_MAX_BYTES, with no loops, a 200 and an X-Loop-Analysis header, so the page
still draws the graph. It used to draw it without loop highlighting and say nothing;
main.js (the single function page) and main_duo.js (the function comparison) now read
the header and say so above the graph. Only a browser runs those scripts, so the
markup tests cannot see the note; testCfgLoopDetection.py covers the header itself.

Loop detection is ours and needs no backend, so the bounds are lowered here, below
the size of two captured functions, rather than a function built past the real ones.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import logging
import os
import threading

import pytest
from werkzeug.serving import make_server

import mcritweb.views.cfg_explorer_detector as cfg_explorer_detector

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 20000

#: Two functions of the captured corpus that keep their control flow graph, with 27
#: and 41 blocks; a block bound of 30 skips the analysis of B alone.
FUNCTION_A = 84
FUNCTION_A_BLOCKS = 27
FUNCTION_B = 943
FUNCTION_B_BLOCKS = 41


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


@pytest.fixture
def browser_page(app, live_server, make_user):
    """A Chromium page logged in as a visitor, by a signed session cookie."""
    user_id = make_user(role="visitor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": cookie,
                "domain": "127.0.0.1",
                "path": "/",
            }])
            page = context.new_page()
            page.set_default_timeout(SCRIPT_TIMEOUT_MS)
            yield page
        finally:
            browser.close()


def open_function(page, live_server, function_id=FUNCTION_A):
    page.goto(f"{live_server}/explore/functions/{function_id}")
    page.wait_for_selector("#graphContainer g.node", state="attached")


def open_comparison(page, live_server):
    page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    for panel in ("a", "b"):
        page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")


#: Where each page draws its loop boundaries: loopify_dagre's single #bgFill on the
#: function page, a g.bgFill per panel (main_duo.js) on the comparison.
FUNCTION_LOOPS = "#graphContainer #bgFill path"


def panel_loops(panel):
    return f"#graphContainer_{panel} g.bgFill path"


def drawn_loops(page, selector):
    """How many loop boundaries a graph was drawn with."""
    return page.locator(selector).count()


@pytest.mark.parametrize("bound", ["blocks", "bytes"])
def test_the_function_page_says_its_loops_were_not_analysed(browser_page, live_server, app, monkeypatch, bound):
    if bound == "blocks":
        monkeypatch.setattr(cfg_explorer_detector, "MAX_BLOCKS", FUNCTION_A_BLOCKS - 1)
    else:
        app.config["FIND_LOOPS_MAX_BYTES"] = 1000
    open_function(browser_page, live_server)

    note = browser_page.locator("#loopAnalysisNote")
    note.wait_for(state="visible")
    assert note.inner_text() == (f"This function is too large for loop analysis ({FUNCTION_A_BLOCKS} blocks); "
                                 "loops are not highlighted.")
    assert note.get_attribute("role") == "status"
    assert browser_page.locator("#graphContainer g.node").count() == FUNCTION_A_BLOCKS, "the graph is not drawn"
    assert drawn_loops(browser_page, FUNCTION_LOOPS) == 0


def test_the_function_page_says_nothing_when_its_loops_were_analysed(browser_page, live_server):
    open_function(browser_page, live_server)
    browser_page.wait_for_selector(FUNCTION_LOOPS, state="attached")

    assert drawn_loops(browser_page, FUNCTION_LOOPS) > 0
    assert browser_page.locator("#loopAnalysisNote").inner_text() == ""


def test_the_comparison_says_which_functions_loops_were_not_analysed(browser_page, live_server, monkeypatch):
    monkeypatch.setattr(cfg_explorer_detector, "MAX_BLOCKS", 30)
    open_comparison(browser_page, live_server)

    note = browser_page.locator("#loopAnalysisNote_b")
    note.wait_for(state="visible")
    assert note.inner_text() == (f"Function B is too large for loop analysis ({FUNCTION_B_BLOCKS} blocks); "
                                 "its loops are not highlighted.")
    assert browser_page.locator("#graphContainer_b g.node.enter").count() == FUNCTION_B_BLOCKS, "B is not drawn"
    assert drawn_loops(browser_page, panel_loops("b")) == 0

    browser_page.wait_for_selector(panel_loops("a"), state="attached")
    assert drawn_loops(browser_page, panel_loops("a")) > 0, "A lost its loops too"
    assert browser_page.locator("#loopAnalysisNote_a").inner_text() == ""


def test_the_comparison_says_nothing_when_both_were_analysed(browser_page, live_server):
    open_comparison(browser_page, live_server)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(panel_loops(panel), state="attached")

    for panel in ("a", "b"):
        assert browser_page.locator(f"#loopAnalysisNote_{panel}").inner_text() == ""
