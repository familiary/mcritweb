#!/usr/bin/python
"""Page structure that only exists once a browser has run the page's scripts.

The markup checks are in testTableSemantics.py and the other structure tests; what is here
is what a script builds or changes: the cross compare panes filled on first click, and the
other script-driven pieces of the page's semantics.

The harness is the offline one of tests/testLiveSearchBrowser.py: `CorpusMcritClient`
behind the app, served on a loopback port. `playwright` is not a dependency of this
project and CI does not install it, so this module skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 20000

#: resolves once base.html's queue has run (ADR-0021)
QUEUE_HAS_RUN = "() => new Promise(resolve => whenReady(() => resolve(true)))"


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
def page(app, live_server, make_user):
    """A Chromium page signed in as a contributor."""
    user_id = make_user(role="contributor")
    cookie = session_cookie_for(app, user_id)
    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie,
                                  "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.errors = []
            page.on("pageerror", lambda error: page.errors.append(str(error)))
            yield page
        finally:
            browser.close()


def open_page(page, url):
    page.goto(url)
    page.evaluate(QUEUE_HAS_RUN)


def test_a_cross_compare_pane_filled_on_first_click_keeps_the_table_s_name(page, live_server):
    """The panes other than the first are built by cloning the rendered table without its
    children, so the caption has to be copied over or those tables have no name."""
    open_page(page, f"{live_server}/data/result/{job_id_of('cross_compare')}")

    page.locator("#pills-score_weighted-tab").click()
    page.wait_for_selector("#pills-score_weighted table")

    captions = page.locator("#pills-tabContent table > caption").all_inner_texts()
    assert captions == ["Cross comparison matrix"] * 2
    assert page.errors == []


def test_the_drawn_function_graph_is_not_read_as_its_code_run_together(page, live_server):
    """The code panel beside the graph is the text of the same blocks."""
    open_page(page, f"{live_server}/explore/functions/84")
    page.evaluate("document.getElementById('xcfg_top').scrollIntoView()")
    page.wait_for_selector("#graphContainer g.node", state="attached")
    page.wait_for_function("document.querySelectorAll('#text_code p').length > 0")

    tree = page.locator("#xcfg_container").aria_snapshot()

    assert "- img" not in tree
    assert 'region "Code of function 84"' in tree
    assert page.errors == []


def test_each_drawn_comparison_graph_is_one_named_image(page, live_server):
    open_page(page, f"{live_server}/data/matches/function/84/943")
    page.evaluate("document.getElementById('xcfg_container').scrollIntoView()")
    for panel in ("a", "b"):
        page.wait_for_selector(f"#graphContainer_{panel} g.node", state="attached")

    tree = page.locator("#xcfg_container").aria_snapshot()

    images = [line.strip() for line in tree.splitlines() if line.strip().startswith("- img")]
    assert images == ['- img "Control flow graph of function A, 84"', '- img "Control flow graph of function B, 943"']
    assert "push ebp" not in tree
    assert page.errors == []


def test_the_page_loading_status_is_there_before_it_is_written_to(page, live_server):
    """The overlay used to be created holding its "Loading" text in a status role, which a
    screen reader often does not read: a live region is read when it changes."""
    open_page(page, f"{live_server}/explore/samples")
    status = page.locator("#mcritweb-page-loading-status")
    assert status.get_attribute("role") == "status"
    assert status.text_content() == ""

    page.evaluate("mcritwebPageLoading.show()")
    page.wait_for_selector("#mcritweb-page-loading")

    assert status.text_content() == "Loading, please wait."
    assert page.locator("#mcritweb-page-loading").get_attribute("role") is None
    page.evaluate("mcritwebPageLoading.hide()")
    assert status.text_content() == ""
    assert page.errors == []


def test_an_upload_under_way_is_announced(page, live_server):
    """The upload button's spinner was the only sign, a status role hidden from assistive
    technology by its own aria-hidden."""
    held = []
    page.route("**/data/request_filename_info", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
    page.route("**/data/submit_or_query", lambda route: held.append(route))
    open_page(page, f"{live_server}/explore/samples")
    page.evaluate("() => new Promise(resolve => mcritLoadDropzone(resolve))")
    assert page.locator("#submit-status").text_content() == ""

    page.evaluate("Dropzone.forElement('#myDropzone').addFile(new File(['MZ'], 'sample.bin'))")
    page.wait_for_function("Dropzone.forElement('#myDropzone').getQueuedFiles().length === 1")
    # the overlay's form is in a closed modal; its Submit starts the upload
    page.evaluate("document.getElementById('submit-dropzone').click()")
    page.wait_for_function("document.getElementById('submit-status').textContent !== ''")

    assert page.locator("#submit-status").text_content() == "Uploading, please wait."
    assert page.locator("#submit-status").get_attribute("role") == "status"
    for route in held:
        route.abort()


def static_texts(page):
    """The text nodes of the page's accessibility tree, as Chromium builds it."""
    session = page.context.new_cdp_session(page)
    nodes = session.send("Accessibility.getFullAXTree")["nodes"]
    return [node.get("name", {}).get("value", "") for node in nodes
            if node.get("role", {}).get("value") == "StaticText" and not node.get("ignored")]


def test_decorative_generated_text_is_not_in_the_accessibility_tree(page, live_server):
    """A family's matches: the breadcrumb has two items, and the diagram its placeholder."""
    open_page(page, f"{live_server}/data/result/{job_id_of('matches_for_sample')}?famid=1")
    assert page.locator("nav .breadcrumb-item").count() == 2
    assert page.locator(".mcrit-diagram").count() == 1

    texts = static_texts(page)

    assert "rendering diagram…" not in texts
    assert "/" not in texts
    assert "All Matches" in texts, "the tree was read"
    # and both are still drawn
    drawn = page.evaluate("""() => [".breadcrumb-item + .breadcrumb-item", ".mcrit-diagram"].map(
        (selector) => getComputedStyle(document.querySelector(selector), "::before").content)""")
    assert drawn == ['"/" / ""', '"rendering diagram\u2026" / ""']


#: the page's layout shifts from its first paint on, as Lighthouse adds them up
COUNT_LAYOUT_SHIFTS = """
window.mcritShifted = 0;
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) { window.mcritShifted += entry.value; }
}).observe({type: "layout-shift", buffered: true});
"""


@pytest.mark.parametrize("path", ["/", "/settings"])
def test_the_first_tab_shows_the_skip_link_and_enter_moves_past_the_navbar(page, live_server, path):
    open_page(page, f"{live_server}{path}")
    page.evaluate(COUNT_LAYOUT_SHIFTS)
    page.wait_for_timeout(200)
    page.evaluate("window.mcritShifted = 0")
    skip = page.locator("a.skip-link")
    clip = "() => getComputedStyle(document.querySelector('a.skip-link')).clipPath"
    assert page.evaluate(clip) == "inset(50%)"
    hidden = skip.bounding_box()
    main_before = page.locator("main").bounding_box()

    page.keyboard.press("Tab")

    assert page.evaluate("document.activeElement.className") == "skip-link"
    assert page.evaluate(clip) == "none"
    shown = skip.bounding_box()
    assert (shown["x"], shown["y"]) == (hidden["x"], hidden["y"])
    assert shown["width"] > 100 and shown["height"] > 20 and shown["x"] < 20 and shown["y"] < 20
    assert page.locator("main").bounding_box() == main_before
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "content"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('main') !== null")
    assert page.evaluate("window.mcritShifted") == 0
    assert page.errors == []


CLOSED_PANELS = ["collapse-minhash", "collapse-blocks"]
OPEN_PANELS = ["collapse-info", "collapse-cfg"]


def panel_state(page, panel):
    return page.evaluate("""(id) => {
        const panel = document.getElementById(id);
        const button = document.querySelector(`[data-bs-target="#${id}"]`);
        return {hidden: panel.getAttribute("hidden"), open: panel.classList.contains("show"),
                height: panel.getBoundingClientRect().height, expanded: button.getAttribute("aria-expanded")};
    }""", panel)


def shifted_on_load(page, url):
    """The layout shifts of loading `url`, added up as Lighthouse does, once its queue has run."""
    page.add_init_script(COUNT_LAYOUT_SHIFTS)
    open_page(page, url)
    page.wait_for_timeout(500)
    return page.evaluate("window.mcritShifted")


def test_find_in_page_can_search_the_closed_panels_of_a_function_page(page, live_server):
    """A closed panel was display: none, which the browser's search skips. Marked, it is
    displayed again with its content hidden, so it must still take no room: the page
    shifts no more than it does where the browser cannot mark the panels."""
    shifted = shifted_on_load(page, f"{live_server}/explore/functions/84")

    for panel in CLOSED_PANELS:
        assert panel_state(page, panel) == {"hidden": "until-found", "open": False, "height": 0, "expanded": "false"}
    for panel in OPEN_PANELS:
        assert panel_state(page, panel)["hidden"] is None
    assert page.errors == []
    unmarked = page.context.new_page()
    unmarked.add_init_script("delete HTMLElement.prototype.onbeforematch")
    assert shifted == pytest.approx(shifted_on_load(unmarked, f"{live_server}/explore/functions/84"), abs=1e-4)
    assert panel_state(unmarked, CLOSED_PANELS[0])["hidden"] is None


def test_a_search_landing_in_a_closed_panel_opens_it_as_its_button_would(page, live_server):
    open_page(page, f"{live_server}/explore/functions/84")

    # what the browser fires on the panel before it shows a match inside
    page.evaluate("document.getElementById('collapse-blocks').dispatchEvent(new Event('beforematch', {bubbles: true}))")
    page.wait_for_function("document.getElementById('collapse-blocks').classList.contains('show')")

    state = panel_state(page, "collapse-blocks")
    assert state["hidden"] is None and state["expanded"] == "true" and state["height"] > 0
    assert panel_state(page, "collapse-minhash")["hidden"] == "until-found"
    assert page.errors == []


def test_a_panel_closed_with_its_button_is_searchable_again(page, live_server):
    open_page(page, f"{live_server}/explore/functions/84")
    button = page.locator("[data-bs-target='#collapse-minhash']")

    button.click()
    page.wait_for_function("document.getElementById('collapse-minhash').classList.contains('show')")
    page.wait_for_function("!document.getElementById('collapse-minhash').classList.contains('collapsing')")
    opened = panel_state(page, "collapse-minhash")
    button.click()
    page.wait_for_function("document.getElementById('collapse-minhash').getAttribute('hidden') === 'until-found'")

    assert opened["hidden"] is None and opened["height"] > 0
    assert panel_state(page, "collapse-minhash") == {"hidden": "until-found", "open": False, "height": 0, "expanded": "false"}


def test_without_until_found_the_panels_are_as_they_were(page, live_server):
    """Safari has no hidden="until-found": there the panels stay Bootstrap's alone."""
    page.add_init_script("delete HTMLElement.prototype.onbeforematch")
    open_page(page, f"{live_server}/explore/functions/84")

    for panel in CLOSED_PANELS:
        assert panel_state(page, panel) == {"hidden": None, "open": False, "height": 0, "expanded": "false"}
        assert page.evaluate("(id) => getComputedStyle(document.getElementById(id)).display", panel) == "none"
    assert page.errors == []
