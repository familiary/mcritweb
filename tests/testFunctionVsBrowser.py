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
from werkzeug.serving import make_server

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
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})

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
    for panel in ("a", "b"):
        browser_page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")
    return browser_page


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
    the window it lives in and the text ran off the edge and was clipped."""
    comparison_page.check("#enableTooltip")
    comparison_page.locator("#xcfg_container").scroll_into_view_if_needed()

    count = blocks_of(comparison_page, panel).count()
    for index in range(min(count, 6)):
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
