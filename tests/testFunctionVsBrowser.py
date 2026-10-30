#!/usr/bin/python
"""The function comparison page's graphs, driven in a browser.

Issue #69 asks for the FunctionVs visualisation to be fixed. All of it is script:
the two CFGs are fetched, laid out by dagre and annotated by
`static/trace_CFG/main_duo.js` after the page has loaded, so nothing about it is
visible to a test that renders the template and reads the HTML back. The markup
assertions in testResultPages.py are what CI keeps; these are the tests that watch
the page behave - the loop boundaries getting drawn, and below them the hover, the
tooltip and the edge click, none of which used to survive being used at all.

The harness is the offline one the rest of the suite uses - `CorpusMcritClient`
behind MCRIT_CLIENT_FACTORY, no backend and no network - served on a loopback port
so Chromium can load it. Loop detection is `mcritweb.views.cfg_explorer_detector`,
which is ours and needs no backend either, so what the page draws is driven by real
loops in real captured control flow graphs.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import json
import logging
import os
import re
import threading

import pytest
from cfgColors import CFG_FILLS, LIGHT_CFG_COLORS, blend, contrast, dark_value, hex_to_rgb, parse_rgb
from conftest import session_cookie_for
from werkzeug.serving import make_server

from mcritweb.db import UserInfo

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The page's own timeout, for a load and for anything a script does after it.
#: Generous, because it is only ever paid in full when a test is about to fail.
SCRIPT_TIMEOUT_MS = 20000

#: Two functions from the captured corpus, one per reference sample, both of which
#: the loop detector finds several loops in - including nested ones in B, so the
#: depth shading has something to distinguish. Only the reference pool keeps its
#: control flow graph, so the panels cannot be built from any other function.
FUNCTION_A = 84
FUNCTION_B = 943


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def live_server(app):
    """The app under test on a loopback port, for the seconds a test needs it.

    `client` is a WSGI stub with no socket behind it, so a browser cannot reach it.
    Port 0 lets the OS pick, so concurrent runs do not collide.
    """
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
    """A Chromium page already logged in as a visitor, on `live_server`.

    The session cookie is signed with the app's own session interface rather than
    driven through the login form: this module is about what the comparison page's
    scripts do, and going through the form would make every test here fail for a
    change to the login markup instead.
    """
    user_id = make_user(role="visitor")
    cookie = session_cookie_for(app, user_id)

    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself (a distro package, a
            # pre-provisioned image) is named through MCRITWEB_CHROMIUM
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


@pytest.fixture
def comparison_page(browser_page, live_server):
    """The function-vs page with both CFGs laid out and annotated.

    Each panel is two requests deep - the dot graph, then loop detection - and only
    draws once both have answered, so waiting for blocks in *both* containers is
    what says the page is finished.
    """
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    return browser_page


def scroll_to_the_graphs(page):
    """The graphs are drawn once they come into view (see the deferred-drawing tests)."""
    page.evaluate("document.getElementById('xcfg_container').scrollIntoView()")


# --- drawing the graphs when they come into view ------------------------------------

#: Lighthouse's two presets. At both, the graphs start below the window, or show only
#: their top edge in its last quarter.
VIEWPORTS = [{"width": 412, "height": 823}, {"width": 1350, "height": 940}]

#: an IntersectionObserver that never reports, so only the controls can start the drawing
SILENT_OBSERVER = "window.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };"


def graph_requests(page):
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/explore/fetchDotGraph/" in request.url else None)
    return requests


def drawn_blocks(page):
    return page.evaluate("[...['a', 'b']].map(p => document.querySelectorAll('#graphContainer_' + p + ' g.node').length)")


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["mobile", "desktop"])
def test_the_graphs_are_drawn_when_they_come_into_view(browser_page, live_server, viewport):
    """Laying out and drawing two large graphs held the main thread for seconds while the
    page loaded, for graphs below the first screen. Now nothing is fetched or drawn until
    they are scrolled to."""
    browser_page.set_viewport_size(viewport)
    requests = graph_requests(browser_page)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")
    assert requests == [] and drawn_blocks(browser_page) == [0, 0]

    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert len(requests) == 2


@pytest.mark.parametrize("use", ["click", "focus"])
def test_using_a_control_above_the_graphs_draws_them(browser_page, live_server, thrown, use):
    """The controls can show above the graphs before the graphs come into view. Using one,
    by pointer or keyboard, is a request for the graphs too. A control does what it did
    while the graphs were still loading, before this change: the highlight buttons only
    take effect once there is a graph."""
    browser_page.add_init_script(SILENT_OBSERVER)
    browser_page.set_viewport_size(VIEWPORTS[1])
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")
    assert drawn_blocks(browser_page) == [0, 0]

    if use == "click":
        browser_page.click("#resetView")
    else:
        browser_page.focus("#syncGraphs")
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert thrown == []


@pytest.mark.parametrize("button,label", [("#showLoops", "Hide Loops"), ("#showCycles", "Hide Cycles")])
def test_the_display_controls_used_before_the_graphs_are_drawn_hold_once_they_are(browser_page, live_server, thrown, button, label):
    """Show Loops, Show Cycles and the two checkboxes were bound as the first graph was
    drawn: a click before then did nothing, or left a checkbox saying the opposite of
    what the graphs showed. Until the graphs come into view is now the whole time before
    scrolling, so the graphs' answers are held here until the controls have been used."""
    browser_page.add_init_script(SILENT_OBSERVER)
    browser_page.set_viewport_size(VIEWPORTS[1])
    held = []
    browser_page.route("**/explore/fetchDotGraph/*", lambda route: held.append(route))
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")

    browser_page.click(button)
    browser_page.uncheck("#loopBgFill")
    browser_page.check("#enableTooltip")
    for _ in range(100):
        if len(held) == 2:
            break
        browser_page.wait_for_timeout(50)
    assert len(held) == 2 and drawn_blocks(browser_page) == [0, 0]
    for route in held:
        route.continue_()
    browser_page.wait_for_function("document.querySelectorAll('g.bgFill').length == 2")

    assert browser_page.get_attribute(button, "value") == label
    for panel in ("a", "b"):
        assert browser_page.locator(f'#graphContainer_{panel} g.node[fill="white"]').count(), (
            "panel " + panel + " has nothing highlighted"
        )
    assert browser_page.eval_on_selector_all(
        "g.bgFill", "groups => groups.map(group => getComputedStyle(group).display)"
    ) == ["none", "none"]
    hover_a_block(browser_page, "a")
    assert not tooltip_state(browser_page, "a")["hidden"], "the tooltip stayed off"
    assert thrown == []


def displays(page):
    """How the two views are shown; the side-by-side container is 0 px tall, its panes float."""
    return page.evaluate("Object.fromEntries(['xcfg_container', 'xcfg_combined'].map(id => [id, getComputedStyle(document.getElementById(id)).display]))")


@pytest.mark.parametrize("then_side_by_side", [False, True], ids=["combined", "taken-back"])
def test_combined_pressed_before_the_graphs_are_drawn_is_shown_once_they_are(browser_page, live_server, thrown, then_side_by_side):
    """Combined is offered once both graphs are drawn. Pressing it before then started the
    drawing and was lost, and before scrolling it is still disabled. It is now acted on
    once it is there, unless Side by side was pressed after it."""
    browser_page.add_init_script(SILENT_OBSERVER)
    browser_page.set_viewport_size(VIEWPORTS[1])
    held = []
    browser_page.route("**/explore/fetchDotGraph/*", lambda route: held.append(route))
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")

    # a disabled radio gets the pointerdown of a press, and no click
    browser_page.locator("#viewCombined").click(force=True)
    if then_side_by_side:
        browser_page.click("#viewSideBySide")
    for _ in range(100):
        if len(held) == 2:
            break
        browser_page.wait_for_timeout(50)
    assert len(held) == 2 and browser_page.is_disabled("#viewCombined")
    for route in held:
        route.continue_()
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")

    if then_side_by_side:
        # the replay is a timer set when the option was enabled; one set now runs after it
        browser_page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")
        assert not browser_page.is_checked("#viewCombined")
        assert displays(browser_page) == {"xcfg_container": "block", "xcfg_combined": "none"}
    else:
        browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
        assert browser_page.is_checked("#viewCombined")
        assert displays(browser_page) == {"xcfg_container": "none", "xcfg_combined": "block"}
    assert thrown == []


#: the scripts of the graph viewer, which the page loads only once the graphs are wanted
VIEWER = ["trace_CFG/lib/d3.min.js", "trace_CFG/lib/graphlib.min.js", "trace_CFG/lib/graphlib-dot.min.js",
          "trace_CFG/lib/dagre-d3.min.js", "trace_CFG/lib/convexhull.js", "trace_CFG/main_duo.js",
          "trace_CFG/fnManip.js", "trace_CFG/loopCollapser.js", "trace_CFG/loopify_dagre.js", "function_compare.js"]


def viewer_requests(page):
    requests = []
    page.on("request", lambda request: requests.append(request.url.split("/static/", 1)[1])
            if "/static/" in request.url and request.url.split("/static/", 1)[1] in VIEWER else None)
    return requests


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["mobile", "desktop"])
def test_the_viewer_is_loaded_only_once_the_graphs_are_wanted(browser_page, live_server, thrown, viewport):
    """The viewer is 698 KB of script, 178 KB gzipped, that only the graphs use, and evaluating it held
    the main thread while the page loaded. Nothing of it is requested until the graphs come
    into view, and then each script once, and dagre-d3 once more by each of the two layout
    workers (layout_worker.js imports it; the browser has it in its cache by then)."""
    browser_page.set_viewport_size(viewport)
    requests = viewer_requests(browser_page)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")
    assert requests == []

    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert sorted(requests) == sorted(VIEWER + ["trace_CFG/lib/dagre-d3.min.js"] * 2)
    assert thrown == []


def hold_the_viewer(page):
    """Holds main_duo.js until released, so that what is pressed meanwhile is pressed
    before the viewer is in. Returns a function that lets it through."""
    held = []
    page.route("**/static/trace_CFG/main_duo.js", lambda route: held.append(route))

    def release():
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1, "main_duo.js was never requested"
        held[0].continue_()
    return release


@pytest.mark.parametrize("button,label", [("#showLoops", "Hide Loops"), ("#showCycles", "Hide Cycles")])
def test_what_is_pressed_before_the_viewer_is_in_holds_once_it_is(browser_page, live_server, thrown, button, label):
    """The viewer arrives some time after the first press above the graphs asks for it. The
    buttons pressed meanwhile are replayed once it is in, and the checkboxes it reads."""
    browser_page.add_init_script(SILENT_OBSERVER)
    browser_page.set_viewport_size(VIEWPORTS[1])
    release = hold_the_viewer(browser_page)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")

    browser_page.click(button)
    browser_page.uncheck("#loopBgFill")
    browser_page.check("#enableTooltip")
    assert browser_page.evaluate("typeof bindDisplayControls") == "undefined", "the viewer was in before the presses"
    release()
    browser_page.wait_for_function("document.querySelectorAll('g.bgFill').length == 2")

    assert browser_page.get_attribute(button, "value") == label
    for panel in ("a", "b"):
        assert browser_page.locator(f'#graphContainer_{panel} g.node[fill="white"]').count(), (
            "panel " + panel + " has nothing highlighted"
        )
    assert browser_page.eval_on_selector_all(
        "g.bgFill", "groups => groups.map(group => getComputedStyle(group).display)"
    ) == ["none", "none"]
    hover_a_block(browser_page, "a")
    assert not tooltip_state(browser_page, "a")["hidden"], "the tooltip stayed off"
    assert thrown == []


@pytest.mark.parametrize("then_side_by_side", [False, True], ids=["combined", "taken-back"])
def test_combined_pressed_before_the_viewer_is_in_is_shown_once_the_graphs_are(browser_page, live_server, thrown, then_side_by_side):
    """The presses are replayed in the order they were made: Combined and then Side by side
    ends side by side."""
    browser_page.add_init_script(SILENT_OBSERVER)
    browser_page.set_viewport_size(VIEWPORTS[1])
    release = hold_the_viewer(browser_page)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")

    browser_page.locator("#viewCombined").click(force=True)
    if then_side_by_side:
        browser_page.click("#viewSideBySide")
    assert browser_page.evaluate("typeof FunctionCompare") == "undefined", "the viewer was in before the press"
    release()
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")
    if then_side_by_side:
        # the replay is a timer set when the option was enabled; one set now runs after it
        browser_page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")
        assert not browser_page.is_checked("#viewCombined")
        assert displays(browser_page) == {"xcfg_container": "block", "xcfg_combined": "none"}
    else:
        browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
        assert browser_page.is_checked("#viewCombined")
        assert displays(browser_page) == {"xcfg_container": "none", "xcfg_combined": "block"}
    assert thrown == []


@pytest.mark.parametrize("script", ["trace_CFG/lib/d3.min.js", "trace_CFG/main_duo.js", "function_compare.js"])
def test_a_viewer_script_that_does_not_load_says_so(browser_page, live_server, script):
    """Without it the graphs cannot be drawn, and the page used to stay blank and silent."""
    browser_page.route("**/static/" + script, lambda route: route.fulfill(status=404, body="gone"))
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    assert browser_page.is_hidden("#viewer_error")
    scroll_to_the_graphs(browser_page)
    browser_page.wait_for_selector("#viewer_error", state="visible")
    assert "could not be loaded" in browser_page.inner_text("#viewer_error")
    assert drawn_blocks(browser_page) == [0, 0]


def test_without_a_script_the_drawing_can_do_without_the_graphs_are_still_drawn(browser_page, live_server):
    """loopCollapser.js is not needed to draw. A page that loaded the viewer from plain
    script tags drew both graphs without it, and this one does too, under the message."""
    browser_page.route("**/static/trace_CFG/loopCollapser.js", lambda route: route.fulfill(status=404, body="gone"))
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert browser_page.is_visible("#viewer_error")


#: a block of each panel by its node id, as main_duo.js binds it to the block's group
NODE_BY_ID = "([panel, id]) => [...document.querySelectorAll('#graphContainer_' + panel + ' g.node')].find(node => node.__data__ === id)"


def test_the_graphs_are_drawn_from_the_block_data_fetched_with_the_viewer(browser_page, live_server, thrown):
    """The colours and matches come with the viewer, from match_functions_diff, once the
    graphs are wanted. What the graphs show is what that answers, even when it comes in
    after the viewer's scripts: a colour and a match planted in the answer are the ones
    drawn."""
    requests = []
    browser_page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/diff") else None)
    planted = {}

    def plant(route):
        response = route.fetch()
        browser_page.wait_for_timeout(700)
        answer = response.json()
        planted["a"], planted["b"] = sorted(answer["node_colors"]["a"])[0], sorted(answer["node_colors"]["b"])[-1]
        answer["node_colors"]["a"][planted["a"]] = "rgb(1, 2, 3)"
        answer["node_matches"]["a"][planted["a"]] = [planted["b"]]
        route.fulfill(response=response, body=json.dumps(answer))
    browser_page.route("**/data/matches/function/*/*/diff", plant)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    browser_page.wait_for_load_state("networkidle")
    assert requests == []

    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert requests == [f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}/diff"]
    assert browser_page.evaluate(f"args => ({NODE_BY_ID})(args).querySelector('rect').style.fill", ["a", planted["a"]]) == "rgb(1, 2, 3)"
    browser_page.evaluate(f"args => ({NODE_BY_ID})(args).dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}}))", ["a", planted["a"]])
    assert browser_page.evaluate(f"args => ({NODE_BY_ID})(args).classList.contains('matched')", ["b", planted["b"]])
    assert thrown == []


def test_block_data_that_does_not_load_says_so_and_the_graphs_are_still_drawn(browser_page, live_server):
    browser_page.route("**/data/matches/function/*/*/diff", lambda route: route.fulfill(status=500, body="down"))
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert browser_page.is_visible("#viewer_error")


def test_the_viewer_scripts_run_in_order_however_they_arrive(browser_page, live_server, thrown):
    """Each script builds on the ones before it. Added by a script, they would run as each
    arrives, so d3 arriving last would break every one after it."""
    def late(route):
        browser_page.wait_for_timeout(700)
        route.continue_()
    browser_page.route("**/static/trace_CFG/lib/d3.min.js", late)
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    scroll_to_the_graphs(browser_page)
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    assert thrown == []


def test_without_intersection_observer_the_graphs_are_drawn_on_load(browser_page, live_server):
    browser_page.add_init_script("delete window.IntersectionObserver;")
    browser_page.set_viewport_size(VIEWPORTS[0])
    browser_page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")


def detected_loops(page, function_id):
    """The loops the server reports for one function, asked for as the page asks.

    Same two endpoints in the same order, including the `\\l` substitution made
    before posting - so this is what the panel was handed, not a re-derivation of
    it. Issued from inside the page because both routes need the session, and
    playwright's request context does not carry the context's cookies.
    """
    return json.loads(page.evaluate(
        """async function(functionId) {
            const dot = await (await fetch('/explore/fetchDotGraph/' + functionId)).text();
            const response = await fetch('/explore/findLoops/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'text/plain',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
                },
                body: dot.replace(/\\\\l/g, "\\n"),
            });
            return await response.text();
        }""",
        function_id,
    ))


def boundary_geometry(page, panel):
    """Where each of a panel's loop boundaries is, in screen coordinates."""
    return page.evaluate(
        """panel => {
            const group = document.getElementById('bgFill_' + panel);
            if (!group) { return null; }
            const inner = document.querySelector('#graphContainer_' + panel + ' g');
            return {
                behind_the_graph: inner.firstElementChild === group,
                paths: Array.from(group.querySelectorAll('path')).map(path => {
                    const box = path.getBoundingClientRect();
                    return {left: box.left, top: box.top, right: box.right, bottom: box.bottom,
                            fill: path.getAttribute('fill')};
                }),
            };
        }""",
        panel,
    )


def block_geometry(page, panel, block_names):
    """Where each named block of a panel was rendered, in screen coordinates.

    The rendered blocks are looked up through the page's own per-panel registry,
    which is how every other consumer on the page finds them; a block the renderer
    never produced comes back as null rather than throwing.
    """
    return page.evaluate(
        """([panel, names]) => names.map(name => {
            const nodes = window.cfgPanels[panel].nodes;
            if (!Object.prototype.hasOwnProperty.call(nodes, name)) { return null; }
            const box = nodes[name].node().getBoundingClientRect();
            return {left: box.left, top: box.top, right: box.right, bottom: box.bottom};
        })""",
        [panel, block_names],
    )


def encloses(outer, inner):
    return (outer["left"] <= inner["left"] and outer["top"] <= inner["top"]
            and outer["right"] >= inner["right"] and outer["bottom"] >= inner["bottom"])


@pytest.mark.parametrize("panel,function_id", [("a", FUNCTION_A), ("b", FUNCTION_B)])
def test_every_detected_loop_gets_a_boundary_behind_its_panel(comparison_page, panel, function_id):
    """Issue #69's "fix loop visualization". `Show Loop Boundaries` used to toggle a
    `#bgFill` that nothing on this page ever created, because loopify_dagre draws it
    against a rewritten layout this page does not render. Both panels now draw their
    own, one per loop, underneath the blocks rather than over them."""
    loops = detected_loops(comparison_page, function_id)
    assert loops, f"function {function_id} has no loops to draw"

    geometry = boundary_geometry(comparison_page, panel)
    assert geometry is not None, f"panel {panel} drew no loop boundaries at all"
    assert geometry["behind_the_graph"], "the boundaries are painted over the blocks, not behind them"
    assert len(geometry["paths"]) == len(loops)
    for path in geometry["paths"]:
        assert path["right"] > path["left"] and path["bottom"] > path["top"], "a boundary has no area"


@pytest.mark.parametrize("panel,function_id", [("a", FUNCTION_A), ("b", FUNCTION_B)])
def test_each_boundary_encloses_the_blocks_of_one_loop(comparison_page, panel, function_id):
    """Drawing the right number of shapes says nothing about where they are. Every
    loop has to have a boundary that covers all of its blocks - a hull built from
    the wrong panel's layout, or from the blocks of some other loop, would still
    count correctly here and cover nothing."""
    loops = detected_loops(comparison_page, function_id)
    paths = boundary_geometry(comparison_page, panel)["paths"]

    for loop in loops:
        blocks = [block for block in block_geometry(comparison_page, panel, loop["nodes"]) if block]
        assert blocks, f"none of loop {loop['backedge']}'s blocks were rendered"
        assert any(all(encloses(path, block) for block in blocks) for path in paths), (
            f"no boundary in panel {panel} covers loop {loop['backedge']}"
        )


def test_the_checkbox_takes_the_boundaries_off_and_puts_them_back(comparison_page):
    """The control shipped disabled, with a title saying it had nothing to toggle.
    It is a live checkbox again, and it has to reach both panels - the one-graph
    page toggles a single `#bgFill` by id, which would leave a panel behind here."""
    checkbox = comparison_page.locator("#loopBgFill")
    assert checkbox.is_enabled(), "the loop boundary control is still disabled"
    assert checkbox.is_checked(), "boundaries are meant to start visible"

    def displays():
        return comparison_page.eval_on_selector_all(
            "g.bgFill", "groups => groups.map(group => getComputedStyle(group).display)"
        )

    assert len(displays()) == 2, "both panels should have a boundary group"
    assert "none" not in displays()

    checkbox.uncheck()
    assert displays() == ["none", "none"]

    # a panel draws its boundaries only once its own two requests have answered,
    # which can be well after the reader touched the control - so a panel finishing
    # late must not put back what was just taken off
    comparison_page.evaluate("() => renderLoopBoundaries('b')")
    assert displays() == ["none", "none"]

    checkbox.check()
    assert "none" not in displays()
    assert len(displays()) == 2, "re-drawing a panel left it with two boundary groups"


# --- the layout in a worker ------------------------------------------------------------------

#: every block and edge as drawn: the node's id, its translate and its rect's size, and each
#: edge's path, per panel
DRAWING = """(panel) => JSON.stringify([
    [...document.querySelectorAll('#graphContainer_' + panel + ' g.node')].map(n => [n.__data__, n.getAttribute('transform'),
        ...['x', 'y', 'width', 'height'].map(a => n.querySelector('rect').getAttribute(a))]),
    [...document.querySelectorAll('#graphContainer_' + panel + ' g.edgePath path')].map(p => [p.parentNode.__data__, p.getAttribute('d')]),
])"""


#: the three graphs: the two functions side by side, and the combined one
GRAPHS = ("a", "b", "c")


def drawn_graphs(page, panels):
    for panel in panels:
        page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    return {panel: page.evaluate(DRAWING, panel) for panel in panels}


def layout_workers(page):
    started = []
    page.on("worker", lambda worker: started.append(worker.url))
    return started


def open_and_draw(page, live_server, panels=("a", "b")):
    """The page with its graphs drawn, the combined one too when it is asked for."""
    page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    scroll_to_the_graphs(page)
    drawing = drawn_graphs(page, [p for p in panels if p != "c"])
    if "c" in panels:
        page.wait_for_function("!document.getElementById('viewCombined').disabled")
        page.click("#viewCombined")
        drawing.update(drawn_graphs(page, ["c"]))
    return drawing


def test_each_graph_is_laid_out_in_a_worker(browser_page, live_server, thrown):
    workers = layout_workers(browser_page)
    open_and_draw(browser_page, live_server)
    assert [url.rsplit("/", 1)[1] for url in workers] == ["layout_worker.js"] * 2

    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")
    browser_page.click("#viewCombined")
    browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    assert len(workers) == 3
    assert thrown == []


def test_the_drawing_is_the_worker_s_layout(browser_page, live_server, thrown):
    """A worker that moves every block 1000 to the right: the blocks of all three graphs are
    drawn there, so it is the worker's layout that is drawn, not one the page made itself."""
    def shifted(route):
        response = route.fetch()
        body = response.text().replace(
            "var answer = dagreD3.json.encode(laidOut);",
            "laidOut.eachNode(function (id, value) { value.x += 1000; }); var answer = dagreD3.json.encode(laidOut);")
        assert "value.x += 1000" in body
        route.fulfill(response=response, body=body)
    browser_page.route("**/static/trace_CFG/layout_worker.js", shifted)
    drawing = open_and_draw(browser_page, live_server, GRAPHS)
    for panel in GRAPHS:
        xs = browser_page.evaluate(f"[...document.querySelectorAll('#graphContainer_{panel} g.node')]"
                                   ".map(n => parseFloat(n.getAttribute('transform').split('(')[1]))")
        assert min(xs) >= 1000, (panel, drawing[panel][:200])
    assert thrown == []


def test_the_worker_s_drawing_is_the_one_the_page_made_itself(browser_page, live_server):
    """Sizes as well as positions, for all three graphs: the renderer writes the sizes it
    measures into the graph it is given, and a block drawn from a value that has one is drawn
    at that size. One page lays the graphs out in workers, the other, without the worker's
    script, on the page."""
    workers = layout_workers(browser_page)
    in_worker = open_and_draw(browser_page, live_server, GRAPHS)
    assert len(workers) == 3

    on_page = browser_page.context.new_page()
    refused = []

    def missing(route):
        refused.append(route.request.url)
        route.fulfill(status=404, body="")
    on_page.route("**/static/trace_CFG/layout_worker.js", missing)
    assert open_and_draw(on_page, live_server, GRAPHS) == in_worker
    assert len(refused) == 3


def combined_fit(page):
    """The combined graph's scale, and whether the graph is inside its pane."""
    return page.evaluate("""() => { const svg = document.getElementById('graphContainer_c'), g = svg.querySelector('g');
        const pane = svg.getBoundingClientRect(), graph = g.getBoundingClientRect();
        return {scale: d3.transform(g.getAttribute('transform')).scale[0],
                inside: graph.width > 0 && graph.width <= pane.width + 1 && graph.height <= pane.height + 1}; }""")


@pytest.mark.parametrize("comes_back", ["fetched", "laid-out"])
def test_a_combined_graph_that_comes_back_while_side_by_side_is_shown_is_drawn_when_combined_is(
        browser_page, live_server, thrown, comes_back):
    """The blocks are measured, and the graph fitted, from what its pane shows. The combined
    graph came back while the reader had gone back to side by side, was measured and fitted in a
    hidden pane, and was drawn at scale(-Infinity), off the pane once it was shown. The worker
    adds its layout to the time it takes, so it is now measured and drawn only once its pane is
    shown, whether its dot graph or its layout comes back while it is not."""
    held = []

    def until_held():
        for _ in range(200):
            if held:
                return
            browser_page.wait_for_timeout(50)
        raise AssertionError("nothing was held")
    if comes_back == "fetched":
        browser_page.route("**/explore/fetchCombinedDotGraph/**", lambda route: held.append(route))
    else:
        # a worker that, its layout made, waits for the test before it answers
        def waiting(route):
            response = route.fetch()
            body = response.text().replace(
                "self.postMessage({ graph: answer });",
                "fetch('/static/layout-answer-released').then(function () { self.postMessage({ graph: answer }); });")
            assert "layout-answer-released" in body
            route.fulfill(response=response, body=body)
    reference = open_and_draw(browser_page, live_server)
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")
    if comes_back == "laid-out":
        # the two graphs side by side are drawn; only the combined one's answer is held
        closed = []
        browser_page.on("worker", lambda worker: worker.on("close", lambda _: closed.append(worker.url)))
        browser_page.route("**/static/trace_CFG/layout_worker.js", waiting)
        browser_page.route("**/static/layout-answer-released", lambda route: held.append(route))
        browser_page.click("#viewCombined")
        until_held()
        browser_page.click("#viewSideBySide")
        held[0].fulfill(status=200, body="")
        # the page ends the worker when its answer comes, before it goes on with it
        for _ in range(200):
            if closed:
                break
            browser_page.wait_for_timeout(50)
        assert closed, "the worker's answer did not come"
    else:
        browser_page.click("#viewCombined")
        until_held()
        browser_page.click("#viewSideBySide")
        # the graph is parsed, and its next step waits for the pane, in the same task
        browser_page.evaluate("""() => { const parse = graphlibDot.parse;
            graphlibDot.parse = function () { window.mcritCombinedParsed = true; return parse.apply(this, arguments); }; }""")
        held[0].continue_()
        browser_page.wait_for_function("window.mcritCombinedParsed === true")
    assert browser_page.evaluate("document.querySelectorAll('#graphContainer_c g.node').length") == 0
    assert drawn_graphs(browser_page, ["a", "b"]) == reference

    browser_page.click("#viewCombined")
    browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    fit = combined_fit(browser_page)
    assert 0 < fit["scale"] < float("inf") and fit["inside"], fit
    # and laid out from blocks measured in the shown pane: drawn as on a page that showed it throughout
    shown_throughout = browser_page.context.new_page()
    try:
        assert drawn_graphs(browser_page, ["c"]) == {"c": open_and_draw(shown_throughout, live_server, GRAPHS)["c"]}
    finally:
        shown_throughout.close()
    assert thrown == []


@pytest.mark.parametrize("failure", ["http-500", "not-a-graph"])
def test_a_combined_load_that_fails_says_so_and_the_next_press_loads_it_again(browser_page, live_server, thrown, failure):
    """A failed fetch says so; an answer that is not a dot graph, such as the login page of a
    lapsed session, fails to parse and now says so too. Either way the note goes and the next
    press of Combined fetches the graph again; the parse failure had left the note up, and
    Combined doing nothing, once the layout moved to the worker. The message goes when the next
    load starts: the note, out of the pane's flow, stands where it stands."""
    answered = []

    def first_fails(route):
        answered.append(route.request.url)
        if len(answered) > 1:
            route.continue_()
        elif failure == "http-500":
            route.fulfill(status=500, body="down")
        else:
            route.fulfill(status=200, content_type="text/html", body="<html><body>Log in</body></html>")
    browser_page.route("**/explore/fetchCombinedDotGraph/**", first_fails)
    open_and_draw(browser_page, live_server)
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")
    browser_page.click("#viewCombined")
    browser_page.wait_for_function("document.getElementById('loading_c').classList.contains('hidden')")
    assert browser_page.is_visible("#combined_error")
    assert len(answered) == 1

    browser_page.click("#viewSideBySide")
    browser_page.click("#viewCombined")
    assert not browser_page.is_visible("#combined_error")
    browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    assert not browser_page.is_visible("#combined_error")
    assert len(answered) == 2
    if failure == "not-a-graph":
        assert thrown, "the parse of the login page did not fail"
    else:
        # Chromium logs the failed fetch itself
        assert [error for error in thrown if not error.startswith("Failed to load resource")] == []


def test_the_combined_graph_is_drawn_where_its_pane_showed_it_loading(browser_page, live_server, thrown):
    """The "Loading" note stood in the pane's flow, 400 px down, and the graph moved up by as much
    when it went. Drawn seconds after the click now that the layout is in a worker, the move
    counted as a layout shift (0.26 for 549/2356 on the live corpus); the note is now out of the
    flow, and the graph is drawn where its pane was while the note showed."""
    open_and_draw(browser_page, live_server)
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")

    def late(route):
        response = route.fetch()
        body = response.text().replace("self.postMessage({ graph: answer });",
                                       "setTimeout(function () { self.postMessage({ graph: answer }); }, 1000);")
        route.fulfill(response=response, body=body)
    browser_page.route("**/static/trace_CFG/layout_worker.js", late)
    top = "document.getElementById('graphContainer_c').getBoundingClientRect().top"
    browser_page.click("#viewCombined")
    browser_page.wait_for_selector("#loading_c", state="visible")
    while_loading = browser_page.evaluate(top)
    browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    browser_page.wait_for_function("document.getElementById('loading_c').classList.contains('hidden')")
    assert browser_page.evaluate(top) == while_loading
    assert thrown == []


@pytest.mark.parametrize("worker", ["missing", "failing"])
def test_without_a_working_worker_the_page_lays_the_graphs_out_itself(browser_page, live_server, thrown, worker):
    if worker == "missing":
        browser_page.route("**/static/trace_CFG/layout_worker.js", lambda route: route.fulfill(status=404, body=""))
    else:
        browser_page.route("**/static/trace_CFG/layout_worker.js", lambda route: route.fulfill(
            content_type="application/javascript",
            body="self.onmessage = function () { self.postMessage({ error: 'injected layout failure' }); };"))
    drawing = open_and_draw(browser_page, live_server)
    assert all(drawing[panel] != "[[],[]]" for panel in ("a", "b"))
    browser_page.wait_for_function("!document.getElementById('viewCombined').disabled")
    browser_page.click("#viewCombined")
    browser_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    # Chromium logs the missing worker script itself as an error
    assert [error for error in thrown if not error.startswith("Failed to load resource")] == []


# --- the hover, tooltip and edge-click handlers ---------------------------------
#
# `main_duo.js` binds all three inside `showGraph(graph_id, ...)`, which runs once
# per panel. Everything below is about the fact that it used to bind them without
# saying *which* panel: the selections were unscoped and the handlers reached for
# ids the one-graph template has and this one does not. The helpers here therefore
# always name a panel, so a fix that reaches only one of the two is a failure.

#: One api name out of the captured control flow graph of FUNCTION_A, and a
#: replacement for it that carries markup. Import names are read out of the
#: analysed binary by smda (`toDotGraph(with_api=True)`) and land in the block
#: label verbatim, so they are attacker-controlled for anyone who can get a sample
#: submitted. The payload avoids double quotes because the whole label is a
#: double-quoted dot string, and quotes its handler because an unquoted HTML
#: attribute value may not contain `=`.
API_NAME = re.compile(r"[\w.]+\.dll![\w@?]+")
API_NAME_WITH_MARKUP = "evil.dll!<img src=x onerror='window.__xssFired = true'>"


@pytest.fixture
def thrown(browser_page):
    """Everything the page threw, and everything it logged as an error.

    An exception out of a d3 event handler does not fail navigation and does not
    stop the next handler, so a page that throws on every hover still looks fine to
    a test that only asserts on the DOM. Chromium reports it as `pageerror`.
    """
    errors = []
    browser_page.on("pageerror", lambda error: errors.append(str(error)))
    browser_page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    return errors


def blocks_of(page, panel):
    return page.locator("#graphContainer_" + panel + " g.node.enter")


def hover_a_block(page, panel, index=0):
    """Hover one rendered block and hand back its lines, in order."""
    block = blocks_of(page, panel).nth(index)
    block.hover()
    return block.evaluate(
        "node => Array.from(node.querySelectorAll('text tspan')).map(line => line.textContent)"
    )


def a_clickable_edge_point(page, panel):
    """A viewport point that lies on one of a panel's rendered edges.

    An edge is a stroked path, so the centre of its bounding box is usually not on
    it and clicking there hits the canvas instead; and the CFGs sit below the fold,
    so the point has to be scrolled to before it can be hit at all. This walks the
    panel's edges until it finds one with a point Chromium agrees is the edge.
    """
    page.locator("#xcfg_container").scroll_into_view_if_needed()
    return page.evaluate(
        """panel => {
            const paths = document.querySelectorAll(
                '#graphContainer_' + panel + ' g.edgePath.enter path');
            for (const path of paths) {
                const length = path.getTotalLength();
                if (!length) { continue; }
                const matrix = path.getScreenCTM();
                for (const fraction of [0.5, 0.25, 0.75, 0.1, 0.9]) {
                    const point = path.getPointAtLength(length * fraction);
                    const x = point.x * matrix.a + point.y * matrix.c + matrix.e;
                    const y = point.x * matrix.b + point.y * matrix.d + matrix.f;
                    if (x < 2 || y < 2 || x > innerWidth - 2 || y > innerHeight - 2) { continue; }
                    if (document.elementFromPoint(x, y) === path) { return {x: x, y: y}; }
                }
            }
            return null;
        }""",
        panel,
    )


TRANSLATE = re.compile(r"translate\(\s*([-\d.e]+)\s*,\s*([-\d.e]+)\s*\)")


def block_positions(page, panel):
    """Where every block of a panel currently sits, in the graph's own coordinates."""
    transforms = page.eval_on_selector_all(
        "#graphContainer_" + panel + " g.node.enter",
        "blocks => blocks.map(block => block.getAttribute('transform'))",
    )
    return [tuple(float(number) for number in TRANSLATE.search(transform).groups())
            for transform in transforms]


def displaced(before, after, tolerance=1.0):
    """Which blocks are somewhere else now.

    A tolerance rather than equality because a d3 transition interpolates its way
    back to the transform it started from and lands a few billionths of a pixel
    off it, which is settled for any purpose but `==`.
    """
    return [index for index, (was, is_now) in enumerate(zip(before, after))
            if abs(was[0] - is_now[0]) > tolerance or abs(was[1] - is_now[1]) > tolerance]


def tooltip_state(page, panel):
    return page.evaluate(
        """panel => {
            const tooltip = document.getElementById('tooltip_' + panel);
            const value = document.getElementById('value_' + panel);
            return {
                hidden: tooltip.classList.contains('hidden'),
                text: value.textContent,
                children: value.children.length,
                html: value.innerHTML,
            };
        }""",
        panel,
    )


@pytest.mark.parametrize("panel", ["a", "b"])
def test_hovering_a_block_throws_nothing(comparison_page, thrown, panel):
    """The linked-highlight fallback selected `#text_code p`, the paragraphs of the
    code panel the single-function page has. This template has no `#text_code`, and
    d3 3.4.11 answers a miss with a selection - an array holding one empty group -
    rather than with nothing, so the length test passed and `.node()` was called on
    a plain Array. Every hover of every block threw."""
    lines = hover_a_block(comparison_page, panel)
    assert lines, "panel " + panel + " rendered a block with no text to hover"
    assert thrown == []


def test_the_tooltip_control_shows_the_hovered_block_in_that_panel(comparison_page, thrown):
    """`Enable Tooltip` targeted `#tooltip` and `#value`. This page has one of each
    per panel - `#tooltip_a`/`#value_a` and `#tooltip_b`/`#value_b` - so the control
    toggled a flag that then wrote to nothing: a dead checkbox. A fix that reaches
    only one of the two panels is no better, so both are hovered here."""
    comparison_page.check("#enableTooltip")

    for panel, other in (("a", "b"), ("b", "a")):
        lines = hover_a_block(comparison_page, panel)
        state = tooltip_state(comparison_page, panel)
        assert not state["hidden"], "the tooltip stayed hidden while hovering panel " + panel
        for line in lines:
            assert line in state["text"], "panel " + panel + " tooltip is missing " + repr(line)
        assert tooltip_state(comparison_page, other)["hidden"], (
            "hovering panel " + panel + " opened panel " + other + "'s tooltip"
        )

    comparison_page.mouse.move(0, 0)
    assert tooltip_state(comparison_page, "b")["hidden"], "the tooltip never closes again"
    assert thrown == []


def test_the_tooltip_stays_off_until_the_control_is_switched_on(comparison_page, thrown):
    """The checkbox has to still mean something once it works."""
    hover_a_block(comparison_page, "a")
    assert tooltip_state(comparison_page, "a")["hidden"]
    assert thrown == []


@pytest.mark.parametrize("panel", ["a", "b"])
def test_clicking_an_edge_throws_nothing_and_moves_its_two_blocks(comparison_page, thrown, panel):
    """The edge handlers were bound to `g.edgePath.enter` unscoped - so the second
    panel to render rebound the first panel's edges to its own graph - and looked
    the clicked edge up in the global `g`, which the one-graph page assigns and this
    page never does. Clicking any edge threw on a null `g` before it did anything.
    The two incident blocks are meant to jump apart and settle back."""
    point = a_clickable_edge_point(comparison_page, panel)
    assert point, "no edge of panel " + panel + " could be pointed at"

    before = block_positions(comparison_page, panel)
    comparison_page.mouse.click(point["x"], point["y"])
    comparison_page.wait_for_timeout(250)
    assert thrown == []
    assert len(displaced(before, block_positions(comparison_page, panel))) == 2, (
        "an edge click is meant to move the two blocks it joins, and only those"
    )

    # and it has to leave the graph as it found it
    comparison_page.wait_for_timeout(2000)
    assert displaced(before, block_positions(comparison_page, panel)) == []


@pytest.fixture
def page_with_a_dangerous_api_name(browser_page, live_server):
    """The comparison page with markup planted in one of function A's api names.

    Served by rewriting the dot graph on its way to the browser rather than by
    doctoring the fixture, so the corpus stays the corpus and the substitution is
    visible right here. Loop detection still sees the rewritten graph, which is
    what the page posts to it.
    """
    replaced = []

    def plant(route):
        response = route.fetch()
        dot_graph, count = API_NAME.subn(API_NAME_WITH_MARKUP, response.text(), count=1)
        replaced.append(count)
        route.fulfill(response=response, body=dot_graph)

    browser_page.route("**/explore/fetchDotGraph/" + str(FUNCTION_A), plant)
    browser_page.goto(live_server + "/data/matches/function/%d/%d" % (FUNCTION_A, FUNCTION_B))
    scroll_to_the_graphs(browser_page)
    browser_page.wait_for_selector("#graphContainer_a g.node", state="attached")
    assert replaced == [1], "no api name in function A's dot graph to plant markup in"
    return browser_page


def test_an_api_name_carrying_markup_is_shown_as_text(page_with_a_dangerous_api_name, thrown):
    """The tooltip assigned the block's text into `innerHTML`. That text is the dot
    graph's node label, which carries the import names smda read out of the analysed
    binary - so whoever can get a sample submitted chooses part of it. It could not
    fire while the hover threw first; fixing the hover or the tooltip arms it, which
    is why it is closed in the same change. The same sink is still present in
    `static/trace_CFG/main.js`, held shut there by a different throw."""
    page = page_with_a_dangerous_api_name
    page.check("#enableTooltip")

    index = page.evaluate(
        """() => Array.from(document.querySelectorAll('#graphContainer_a g.node.enter'))
               .findIndex(block => block.textContent.includes('__xssFired'))"""
    )
    assert index >= 0, "the planted api name was not rendered into any block"

    hover_a_block(page, "a", index)
    state = tooltip_state(page, "a")

    assert not state["hidden"], "the tooltip did not open, so nothing was proven"
    assert API_NAME_WITH_MARKUP in state["text"], (
        "the api name did not survive as text: " + repr(state["text"])
    )
    assert state["children"] == 0, "the api name built elements: " + repr(state["html"])
    assert page.evaluate("() => window.__xssFired") is None, "the planted markup ran"
    assert thrown == []


def visible_count(page, panel, selector):
    return page.evaluate(
        """([panel, selector]) => Array.from(
               document.querySelectorAll('#graphContainer_' + panel + ' ' + selector)
           ).filter(element => getComputedStyle(element).display !== 'none').length""",
        [panel, selector],
    )


def test_escape_html_neutralises_markup_in_the_span_the_taint_highlighters_build(
        comparison_page):
    """The three remaining innerHTML sinks in this file cannot become `.text()` calls -
    the markup is the highlight. Their untrusted half goes through `escapeHtml` instead,
    and this builds the exact string they build to show that nothing survives it.

    `tests/testScriptEscaping.py` is the lint that keeps every one of the three routed
    through the helper; this is the check that the helper does what the lint assumes."""
    result = comparison_page.evaluate(
        """payload => {
            const line = "<span style = 'background-color: red ; color: white; '>"
                + escapeHtml(payload) + "</span>";
            const host = document.createElement('div');
            host.innerHTML = line;
            return {
                elements: host.querySelectorAll('*').length,
                images: host.querySelectorAll('img').length,
                text: host.textContent,
            };
        }""",
        API_NAME_WITH_MARKUP,
    )
    assert result["images"] == 0, "the payload built an element"
    assert result["elements"] == 1, "only the span the highlighter authored may be built"
    assert result["text"] == API_NAME_WITH_MARKUP, "the line did not survive as text"


@pytest.mark.parametrize("panel,other", [("a", "b"), ("b", "a")])
def test_backspace_hides_the_hovered_block_and_its_edges_in_that_panel_only(
        comparison_page, thrown, panel, other):
    """CFGExplorer's declutter key. Here it read the global `g` for the block's edges -
    null on this page - but only *after* hiding the block, so it left the block gone,
    its edges dangling and no way to bring it back, and then threw. It also selected
    `g.edgePath.enter` unscoped, so it would have hidden edges in the other panel too."""
    comparison_page.locator("#xcfg_container").scroll_into_view_if_needed()
    blocks_before = visible_count(comparison_page, panel, "g.node.enter")
    edges_before = visible_count(comparison_page, panel, "g.edgePath.enter")
    other_blocks_before = visible_count(comparison_page, other, "g.node.enter")
    other_edges_before = visible_count(comparison_page, other, "g.edgePath.enter")

    hover_a_block(comparison_page, panel)
    comparison_page.keyboard.press("Backspace")
    comparison_page.wait_for_timeout(200)

    assert thrown == []
    assert visible_count(comparison_page, panel, "g.node.enter") == blocks_before - 1, (
        "the hovered block was not taken out of its panel"
    )
    assert visible_count(comparison_page, panel, "g.edgePath.enter") < edges_before, (
        "the block went but its edges are still drawn, pointing at nothing"
    )
    assert visible_count(comparison_page, other, "g.node.enter") == other_blocks_before
    assert visible_count(comparison_page, other, "g.edgePath.enter") == other_edges_before


def tooltip_metrics(page, panel):
    return page.evaluate(
        """panel => {
            const tooltip = document.getElementById('tooltip_' + panel);
            const paragraph = tooltip.querySelector('p');
            const container = document.getElementById(
                panel === 'a' ? 'xcfg_left' : 'xcfg_right');
            const style = getComputedStyle(paragraph);
            const box = tooltip.getBoundingClientRect();
            const frame = container.getBoundingClientRect();
            return {
                margin: [style.marginTop, style.marginRight,
                         style.marginBottom, style.marginLeft],
                font_size: parseFloat(style.fontSize),
                tooltip_font_size: parseFloat(getComputedStyle(tooltip).fontSize),
                overflow_right: box.right - frame.right,
                overflow_left: frame.left - box.left,
                // the text running out of its own box, and the box out of the window
                text_overflow: paragraph.scrollWidth - paragraph.clientWidth,
                overflow_bottom: box.bottom - document.documentElement.clientHeight,
                // how much of the panel is on screen, and where the box starts within it
                visible_height: Math.min(frame.bottom, document.documentElement.clientHeight) - Math.max(frame.top, 0),
                height: box.height,
                top_in_view: box.top - Math.max(frame.top, 0),
            };
        }""",
        panel,
    )


def test_the_tooltip_matches_the_styling_the_single_graph_page_gets(comparison_page):
    """`cfg_style.css` is vendored and styles `#tooltip p` by id, so it reaches neither
    of this page's two. The properties are set from the script instead, and they have to
    be the same ones: without `margin: 0` the paragraph keeps Bootstrap reboot's
    `margin-bottom: 1rem` and leaves 16px of blank space inside a 3px-padded box."""
    comparison_page.check("#enableTooltip")
    hover_a_block(comparison_page, "a")

    metrics = tooltip_metrics(comparison_page, "a")
    assert metrics["margin"] == ["0px", "0px", "0px", "0px"], (
        "the tooltip paragraph keeps the stylesheet's default margin"
    )
    assert metrics["font_size"] == pytest.approx(metrics["tooltip_font_size"] * 1.25, abs=0.2), (
        "the tooltip text is not the 1.25em the single-graph page renders it at"
    )


@pytest.mark.parametrize("panel", ["a", "b"])
def test_the_tooltip_stays_inside_its_panel(comparison_page, panel):
    """The width came from the block's bounding box in unscaled SVG units, with nothing
    clamping it to the panel, so a wide block produced a tooltip wider than the half of
    the window it lives in and the text ran off the edge and was clipped. Clamping that
    width to the panel kept the box in, but the box was still sized from the drawn block,
    so the instructions ran out of it; and a low block's tooltip ran below the window."""
    comparison_page.check("#enableTooltip")
    comparison_page.locator("#xcfg_container").scroll_into_view_if_needed()

    count = blocks_of(comparison_page, panel).count()
    for index in range(count):
        hover_a_block(comparison_page, panel, index)
        metrics = tooltip_metrics(comparison_page, panel)
        assert metrics["overflow_right"] <= 1, (
            f"block {index} of panel {panel} overflows its panel to the right by "
            f"{metrics['overflow_right']:.1f}px"
        )
        assert metrics["overflow_left"] <= 1, (
            f"block {index} of panel {panel} overflows its panel to the left by "
            f"{metrics['overflow_left']:.1f}px"
        )
        assert metrics["text_overflow"] <= 1, (
            f"the text of block {index} of panel {panel} runs out of its tooltip by "
            f"{metrics['text_overflow']}px"
        )
        if metrics["height"] <= metrics["visible_height"]:
            assert metrics["overflow_bottom"] <= 1, (
                f"block {index} of panel {panel} has its tooltip run below the window by "
                f"{metrics['overflow_bottom']:.1f}px"
            )
        else:
            # taller than the part of the panel on screen: it can only start at the top of
            # that part, since the panel clips it everywhere else
            assert abs(metrics["top_in_view"]) <= 1, (
                f"block {index} of panel {panel} has a tooltip taller than the visible panel "
                f"that does not start at its top ({metrics['top_in_view']:.1f}px off)"
            )


def test_the_combined_tooltip_stays_inside_its_pane(comparison_page):
    """The combined view shows B's instructions for a block that differs, in a tooltip
    that was put at the pointer with nothing holding it inside the pane."""
    comparison_page.check("#viewCombined")
    comparison_page.wait_for_selector("#graphContainer_c g.node", state="attached")
    comparison_page.locator("#xcfg_combined").scroll_into_view_if_needed()
    blocks = comparison_page.locator("#graphContainer_c g.node.enter")
    shown = 0
    for index in range(blocks.count()):
        blocks.nth(index).hover(force=True)
        state = comparison_page.evaluate(
            """() => {
                const tooltip = document.getElementById('tooltip_c');
                if (tooltip.classList.contains('hidden')) { return null; }
                const box = tooltip.getBoundingClientRect();
                const frame = document.getElementById('xcfg_combined').getBoundingClientRect();
                const paragraph = tooltip.querySelector('p');
                return {right: box.right - frame.right, left: frame.left - box.left,
                        text: paragraph.scrollWidth - paragraph.clientWidth};
            }"""
        )
        if state is None:
            continue
        shown += 1
        assert state["right"] <= 1 and state["left"] <= 1, f"combined block {index}: {state}"
        assert state["text"] <= 1, f"combined block {index}: the text runs out of its tooltip"
    # the captured pair has blocks that differ; a tooltip that stops appearing must not pass
    assert shown > 0, "no combined block showed a tooltip"


# --- the colour themes (#70) -----------------------------------------------------------
#
# The block colours reach the page as `var(--cfg-...)`, and what each paints is decided
# by the stylesheet in force. These load the page in each theme and read back what the
# browser computed: a name that resolves to the wrong value, a rule that loses a
# specificity tie, a colour a script writes inline over the theme - none of those can be
# seen in the markup.

def use_theme(app, theme):
    """Switch the account `browser_page` is logged in as to a theme."""
    with app.app_context():
        user = UserInfo.fromDb(username="visitoruser")
        user.theme = theme
        user.saveToDb()


def open_comparison(page, live_server):
    page.goto(f"{live_server}/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    for panel in ("a", "b"):
        page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")


#: what the browser computed for every block: its fill, how much of it shows, its text
PAINTED_BLOCKS = """() => Array.from(document.querySelectorAll('g.node.enter')).map(node => {
    const rect = getComputedStyle(node.querySelector('rect'));
    const text = getComputedStyle(node.querySelector('text'));
    return {fill: rect.fill, opacity: parseFloat(rect.fillOpacity), text: text.fill};
})"""


def canvas_of(page):
    return parse_rgb(page.evaluate("getComputedStyle(document.getElementById('xcfg_left')).backgroundColor"))


def test_in_the_light_theme_the_blocks_wear_the_colors_they_always_had(comparison_page):
    blocks = comparison_page.evaluate(PAINTED_BLOCKS)
    assert blocks
    light = {hex_to_rgb(value) for name, value in LIGHT_CFG_COLORS.items() if name in CFG_FILLS}
    for block in blocks:
        assert tuple(int(c) for c in parse_rgb(block["fill"])) in light, block
        assert parse_rgb(block["text"]) == (0, 0, 0)
    assert canvas_of(comparison_page) == (255, 255, 255)


def test_in_the_dark_theme_the_blocks_are_dark_tints_with_readable_text(app, browser_page, live_server):
    use_theme(app, "dark")
    open_comparison(browser_page, live_server)
    blocks = browser_page.evaluate(PAINTED_BLOCKS)
    assert blocks
    dark = {hex_to_rgb(dark_value(name)) for name in CFG_FILLS}
    canvas = canvas_of(browser_page)
    assert canvas == hex_to_rgb(dark_value("--cfg-canvas"))
    for block in blocks:
        fill = tuple(int(c) for c in parse_rgb(block["fill"]))
        # the name resolved to the dark value, not to the light one
        assert fill in dark, block
        assert contrast(fill, parse_rgb(block["text"])) >= 4.5, block


def test_in_the_dark_theme_a_hovered_or_matched_block_is_outlined_in_the_dark_colors(app, browser_page, live_server):
    """The vendored stylesheet draws these outlines, and the dark rule that restyles
    every block and edge would win over them on a tie - so each state is put on a
    block or an edge and the colour the browser computes is read back."""
    use_theme(app, "dark")
    open_comparison(browser_page, live_server)
    outlines = browser_page.evaluate(
        """() => {
            const block = document.querySelector('#graphContainer_a g.node.enter');
            const result = {};
            for (const state of ['', 'active', 'matched']) {
                block.classList.remove('active', 'matched');
                if (state) { block.classList.add(state); }
                result[state || 'plain'] = getComputedStyle(block.querySelector('rect')).stroke;
            }
            const edge = document.querySelector('#graphContainer_a g.edgePath.enter');
            for (const state of ['', 'backedge', 'active']) {
                edge.classList.remove('backedge', 'active');
                if (state) { edge.classList.add(state); }
                result['edge ' + (state || 'plain')] = getComputedStyle(edge).stroke;
            }
            return result;
        }"""
    )
    expect = {name: hex_to_rgb(dark_value(name)) for name in ("--cfg-highlight", "--cfg-matched", "--cfg-edge-both", "--cfg-backedge")}
    expect["--border-control"] = hex_to_rgb(dark_value("--border-control"))
    got = {state: tuple(int(c) for c in parse_rgb(stroke)) for state, stroke in outlines.items()}
    assert got["plain"] == expect["--border-control"]
    assert got["active"] == expect["--cfg-highlight"]
    assert got["matched"] == expect["--cfg-matched"]
    assert got["edge plain"] == expect["--cfg-edge-both"]
    # loopCollapser.js marks a loop's back edge on the function page; here it is done by hand
    assert got["edge backedge"] == expect["--cfg-backedge"]
    assert got["edge active"] == expect["--cfg-highlight"]


def test_in_the_dark_theme_cycle_colors_keep_the_text_readable(app, browser_page, live_server):
    """`Show Cycles` writes one of sixteen saturated colours into each block inline and
    puts fill="white" on the block's group. Orange and yellow ones leave white text at
    2:1; the dark theme lets them through at 55% over the canvas instead."""
    use_theme(app, "dark")
    open_comparison(browser_page, live_server)
    browser_page.click("#showCycles")
    coloured = browser_page.evaluate(
        """() => Array.from(document.querySelectorAll('g.node.enter')).filter(node => node.getAttribute('fill') === 'white').map(node => {
            const rect = getComputedStyle(node.querySelector('rect'));
            return {fill: rect.fill, opacity: parseFloat(rect.fillOpacity), text: getComputedStyle(node.querySelector('text')).fill};
        })"""
    )
    assert coloured, "the cycles painted nothing"
    canvas = canvas_of(browser_page)
    for block in coloured:
        shown = blend(parse_rgb(block["fill"]), block["opacity"], canvas)
        assert contrast(shown, parse_rgb(block["text"])) >= 4.5, block


def test_the_loop_label_is_the_colour_the_first_boundary_is_drawn_in(app, browser_page, live_server):
    """"Show Loop Boundaries" is labelled with the outermost loop's colour. In the dark
    theme the boundaries are translucent over the canvas, and the label has to be that
    mix - a label in the light colour would be a pale patch that matches nothing."""
    use_theme(app, "dark")
    open_comparison(browser_page, live_server)
    drawn = browser_page.evaluate(
        """() => {
            const group = document.getElementById('bgFill_a');
            const path = group.querySelector('path');
            const label = document.getElementById('loopBgFill').parentNode;
            return {fill: getComputedStyle(path).fill, opacity: parseFloat(getComputedStyle(group).opacity),
                    label: getComputedStyle(label).backgroundColor};
        }"""
    )
    shown = blend(parse_rgb(drawn["fill"]), drawn["opacity"], canvas_of(browser_page))
    # color-mix() computes to color(srgb 0.34 0.30 0.27): channels as fractions
    label = tuple(255 * c for c in parse_rgb(drawn["label"]))
    assert drawn["label"].startswith("color(srgb")
    assert all(abs(a - b) <= 1.5 for a, b in zip(shown, label)), (shown, label)


def test_in_the_dark_theme_the_tooltips_and_the_code_lines_are_dark(app, browser_page, live_server):
    """Both tooltips of this page are white before the theme gets to them - #tooltip_a
    and #tooltip_b because main_duo.js writes it inline."""
    use_theme(app, "dark")
    open_comparison(browser_page, live_server)
    browser_page.check("#enableTooltip")
    hover_a_block(browser_page, "a")
    background = browser_page.evaluate("getComputedStyle(document.getElementById('tooltip_a')).backgroundColor")
    text = browser_page.evaluate("getComputedStyle(document.querySelector('#tooltip_a p')).color")
    assert tuple(int(c) for c in parse_rgb(background)) == hex_to_rgb(dark_value("--cfg-tooltip-bg"))
    assert contrast(parse_rgb(background), parse_rgb(text)) >= 4.5
