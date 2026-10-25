#!/usr/bin/python
"""Data tables wider than the screen, in a browser.

A data table is as wide as the cells issue #52 keeps on one line, up to 1,064px on the
samples page, and nothing held it: on a phone or a tablet the whole page scrolled sideways,
the heading, the filters and the pagination with it. Each data table now scrolls in a
`.table-scroll` box of its own below 1200px (static/style.css). What only a browser can
show is checked here: that no page is wider than the window, that the box does not cut off
the menus in a table's rows, and that none of it moves anything once the page is painted.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing; tests/testTableLayout.py checks the markup in CI.
"""

import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: the widths the layout is checked at: a small phone, a phone, a tablet, a small laptop
NARROW_WIDTHS = [320, 390, 768, 1024]

#: pages whose data tables were wider than a phone (up to 1,064px at 390px)
LIST_PAGES = [
    "/",
    "/explore/samples",
    "/explore/families",
    "/explore/functions",
    "/explore/search?query=citadel&type=sample",
    "/analyze/compare",
    "/analyze/compare_versus",
    "/analyze/cross_compare",
    "/analyze/unique_blocks",
    "/data/jobs",
]

#: as tests/testDropzoneBrowser.py counts them, keeping what moved for the failure message
COUNT_LAYOUT_SHIFTS = """
window.mcritShifted = 0;
window.mcritMoved = [];
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
            window.mcritShifted += entry.value;
            for (const source of entry.sources || []) {
                const node = source.node;
                const name = node ? node.nodeName + (node.id ? "#" + node.id : "") + " " + (node.textContent || "").trim().slice(0, 30) : "?";
                window.mcritMoved.push([name, Math.round(source.previousRect.x), Math.round(source.previousRect.y), "->", Math.round(source.currentRect.x), Math.round(source.currentRect.y)]);
            }
        }
    }
}).observe({type: "layout-shift", buffered: true});
"""

WAIT_FOR_THE_QUEUE = "() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })"

#: two frames on, so that a shift the queued scripts caused has been reported
SHIFTED = "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve([window.mcritShifted, window.mcritMoved]))))"

PAGE_WIDTH = "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"


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
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture
def open_page(app, browser, make_user):
    """open_page(url, width, role="contributor", height=800) -> a signed-in page at that size,
    loaded, its queued scripts run, counting layout shifts from the start.

    The icon fonts are refused. Whether one arrives before the first paint or after it
    depends on the test's server, which shares its process with the test, keeping up under
    a parallel run; one that comes after moves every cell and link with an icon in it, on
    master as much as here, and that would be counted against the tables. Refused by the
    browser itself (a route would wait on this same busy process), every icon is laid out
    in its fallback from the start and stays so, and what is counted is the page's own
    doing."""
    cookies = {}
    contexts = []

    def _open(url, width, role="contributor", height=800):
        if role not in cookies:
            cookies[role] = session_cookie_for(app, make_user(role=role, username=f"{role}-browser"))
        context = browser.new_context(viewport={"width": width, "height": height})
        contexts.append(context)
        context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookies[role], "domain": "127.0.0.1", "path": "/"}])
        context.add_init_script(COUNT_LAYOUT_SHIFTS)
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setBlockedURLs", {"urls": ["*/static/webfonts/*"]})
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        page.goto(url)
        page.evaluate(WAIT_FOR_THE_QUEUE)
        return page

    yield _open
    for context in contexts:
        context.close()


@pytest.mark.parametrize("path", LIST_PAGES)
def test_a_wide_table_scrolls_in_its_box_and_the_page_stays_the_width_of_the_window(open_page, live_server, path):
    """At 390px the samples page was 1,064px wide. The table still is - its cells are no
    narrower - but it scrolls in its own box, and nothing moves once the page is painted."""
    for width in NARROW_WIDTHS:
        page = open_page(live_server + path, width)
        scroll_width, client_width = page.evaluate(PAGE_WIDTH)
        assert scroll_width <= client_width, f"{path} is {scroll_width}px wide in a {client_width}px window"
        shifted, moved = page.evaluate(SHIFTED)
        assert shifted == 0, f"{path} shifted at {width}px: {moved}"
        assert page.errors == []


def test_the_table_keeps_its_width_and_scrolls_rather_than_being_squeezed(open_page, live_server):
    page = open_page(live_server + "/explore/samples", 390)
    box = page.evaluate(
        "() => { const box = document.querySelector('.table-scroll'); const table = box.querySelector('table');"
        " return [box.scrollWidth, box.clientWidth, Math.round(table.getBoundingClientRect().width), getComputedStyle(box).overflowX]; }"
    )
    assert box[0] > box[1] and box[2] > box[1], f"the samples table no longer overflows its box at 390px: {box}"
    assert box[3] == "auto"


def test_on_a_wide_screen_the_box_does_not_scroll(open_page, live_server):
    """At 1200px and up the tables fit, and a box that scrolls would clip the hover tooltips
    of the first rows and keep the headings from sticking."""
    page = open_page(live_server + "/explore/samples", 1280)
    assert page.evaluate("() => getComputedStyle(document.querySelector('.table-scroll')).overflowX") == "visible"


def menu_items_hit(page, toggle):
    """Open the dropdown of `toggle` and say, for its first and last item, whether a click at
    the item's centre lands on the item - the box around the table does not clip it."""
    page.click(toggle)
    menu = page.evaluate_handle(f"() => document.querySelector({toggle!r}).parentElement.querySelector('.dropdown-menu')")
    page.wait_for_function("(menu) => menu.classList.contains('show')", arg=menu)
    return page.evaluate(
        "(menu) => { const items = menu.querySelectorAll('.dropdown-item');"
        " return [items[0], items[items.length - 1]].map((item) => { const r = item.getBoundingClientRect();"
        " const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
        " return !!hit && item.contains(hit); }); }",
        menu,
    )


@pytest.mark.parametrize("width", [390, 1280])
def test_the_menu_of_the_last_row_of_a_short_table_is_not_cut_off(open_page, live_server, width):
    """The search for "citadel" lists three samples. The Analyze menu of the last one opens
    below the table, and a box that scrolls sideways clips at its bottom too: the menu's
    items could not be clicked, and the box grew a scrollbar to reach them."""
    page = open_page(live_server + "/explore/search?query=citadel&type=sample", width)
    toggles = page.locator(".table-scroll [data-bs-toggle='dropdown']")
    assert toggles.count() >= 2
    last = toggles.nth(toggles.count() - 1).get_attribute("id")
    page.evaluate(f"() => document.getElementById({last!r}).scrollIntoView({{block: 'center'}})")

    assert menu_items_hit(page, f"#{last}") == [True, True]
    box = page.evaluate(f"() => {{ const box = document.getElementById({last!r}).closest('.table-scroll'); return [box.scrollHeight, box.clientHeight]; }}")
    assert box[0] == box[1], f"the open menu made the box scroll: {box}"
    assert page.errors == []


def test_the_actions_menu_of_a_user_is_not_cut_off(open_page, live_server, make_user):
    """The same on the users page, whose tables list a few accounts each."""
    make_user(role="visitor", username="someone")
    page = open_page(live_server + "/admin/users/", 390, role="admin")
    toggle = page.locator("#pills-all.active .table-scroll [data-bs-toggle='dropdown']").last
    toggle.evaluate("(button) => { button.id = 'last-user-actions'; button.scrollIntoView({block: 'center'}); }")

    assert menu_items_hit(page, "#last-user-actions") == [True, True]
