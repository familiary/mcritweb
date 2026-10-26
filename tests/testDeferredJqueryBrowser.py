#!/usr/bin/python
"""What loading jQuery after the first paint does to the pages, in a browser (issue #63,
ADR-0018, ADR-0021).

testParseTimeScripts.py reads the scripts and finds no `$` that runs before the queue
base.html loads after the first paint has run. This loads the pages and looks: every
page the accessibility checks render, in Chromium, against the captured corpus. A `$`
reached before jQuery has run is a "$ is not defined" page error, so the first test is
that no page logs an error at all. The others check what the ready handlers set up, once they have run:
Bootstrap owns `$.fn.tooltip` (jQuery UI is queued now too, and runs before it), the
clipboard buttons have Bootstrap tooltips, the cross compare's sample list is a jQuery
UI sortable, the settings lists are SortableJS lists, a row click navigates, a file
dragged over a page opens the drop overlay, the upload page's form reacts, and the
delete confirmation on the jobs page opens.

The harness is the offline one testBrowser.py uses. `playwright` is not a dependency
and CI does not install it, so this module skips there; testParseTimeScripts.py is
what CI keeps.
"""

import logging
import os
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, ANONYMOUS_PAGES, CONTRIBUTOR_PAGES, VISITOR_PAGES
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SCRIPT_TIMEOUT_MS = 10000

#: resolves once every ready handler queued before it has run: whenReady keeps them in
#: the order they were queued until the queued scripts have run, and jQuery runs them in
#: that order
AFTER_THE_READY_HANDLERS = """() => new Promise((resolve, reject) => {
    whenReady(() => resolve(true));
    setTimeout(() => reject(new Error("the queued scripts did not run within 10 s")), 10000);
})"""

#: how long after DOMContentLoaded the queue started: the start of jquery.js's request
QUEUE_DELAY = """() => {
    const loaded = performance.getEntriesByType("navigation")[0].domContentLoadedEventStart;
    const jquery = performance.getEntriesByType("resource").find(entry => entry.name.split("?")[0].endsWith("/static/jquery.js"));
    return jquery ? jquery.startTime - loaded : null;
}"""

#: the lists settings.html makes sortable, both halves of each table's pair
SETTINGS_LISTS = [f"{table}_{half}" for table in (
    "family_table", "samples_table", "functions_table", "result_family_table",
    "result_function_unfiltered_table", "result_function_sample_filtered_table",
    "result_function_function_filtered_table") for half in ("active", "available")]


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
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def open_as(app, browser, make_user):
    """open_as(role) -> a page whose errors are collected in `page.errors`, signed in as
    `role` (or no one, for "anonymous") through a session cookie rather than the form."""
    def _open_as(role):
        user_id = make_user(role="admin" if role == "anonymous" else role)
        context = browser.new_context()
        if role != "anonymous":
            cookie = session_cookie_for(app, user_id)
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
        page = context.new_page()
        page.set_default_timeout(SCRIPT_TIMEOUT_MS)
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(f"pageerror: {error}"))
        page.on("console", lambda message: page.errors.append(f"console: {message.text}") if message.type == "error" else None)
        return page
    return _open_as


def settled(page, selector):
    """Wait for a modal to finish opening: Bootstrap ignores a hide asked for while its
    show transition still runs, so a close clicked any earlier is lost."""
    page.wait_for_function("""selector => {
        const modal = bootstrap.Modal.getInstance(document.querySelector(selector));
        return modal !== null && modal._isShown && !modal._isTransitioning;
    }""", arg=selector)


def ready(page, url):
    page.goto(url)
    assert page.evaluate(AFTER_THE_READY_HANDLERS)
    return page


def page_cases():
    return ([("anonymous", path) for path in ANONYMOUS_PAGES]
            + [("visitor", path) for path in VISITOR_PAGES]
            + [("contributor", path) for path in CONTRIBUTOR_PAGES]
            + [("admin", path) for path in ADMIN_PAGES])


@pytest.mark.parametrize(("role", "path"), page_cases())
def test_a_page_loads_without_an_error_and_bootstrap_owns_its_plugins(open_as, live_server, role, path):
    page = ready(open_as(role), live_server + path)

    state = page.evaluate("""() => ({
        tooltipIsBootstraps: jQuery.fn.tooltip.Constructor === bootstrap.Tooltip,
        modalIsBootstraps: jQuery.fn.modal.Constructor === bootstrap.Modal,
        tooltipsWithoutInstance: Array.from(document.querySelectorAll('[data-toggle="tooltip"]'))
            .filter(element => !bootstrap.Tooltip.getInstance(element)).length,
    })""")
    assert page.errors == []
    assert state == {"tooltipIsBootstraps": True, "modalIsBootstraps": True, "tooltipsWithoutInstance": 0}


def test_the_cross_compare_keeps_both_libraries(open_as, live_server):
    """jQuery UI makes the sample list sortable and Bootstrap keeps the tooltips: the
    clipboard buttons of a pane filled on its first showing get Bootstrap's too."""
    page = ready(open_as("visitor"), f"{live_server}/data/result/{job_id_of('cross_compare')}")

    assert page.evaluate("() => jQuery('#sortable').hasClass('ui-sortable') && !!jQuery('#sortable').sortable('instance')")
    lazy = page.locator("#pills-tab button:not(.active)").first
    target = lazy.get_attribute("data-bs-target")
    lazy.click()
    page.wait_for_selector(f"{target} table", state="attached")
    assert page.evaluate(f"""() => Array.from(document.querySelectorAll('{target} [data-toggle="tooltip"]'))
        .every(element => bootstrap.Tooltip.getInstance(element) !== null)""")
    assert page.evaluate(f"() => document.querySelectorAll('{target} [data-toggle=\"tooltip\"]').length") > 0
    assert page.errors == []


def test_a_sample_dragged_in_the_list_moves(open_as, live_server):
    """What jQuery UI is loaded for, done with the mouse: the first sample dragged below the
    second swaps the two."""
    page = ready(open_as("visitor"), f"{live_server}/data/result/{job_id_of('cross_compare')}")
    order = """() => Array.from(document.querySelectorAll('#sortable li'), item => item.textContent.split(' ')[0])"""
    before = page.evaluate(order)
    assert len(before) >= 2
    first, second = (page.locator("#sortable li").nth(index).bounding_box() for index in (0, 1))
    x, y = first["x"] + 10, first["y"] + first["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x, y + (second["y"] + second["height"] - y) * step / 10 + 2)
    page.mouse.up()
    assert page.evaluate(order) == [before[1], before[0], *before[2:]]
    assert page.errors == []


def test_the_list_items_keep_jquery_uis_touch_action(open_as, live_server):
    """The page no longer links jquery-ui.css for the one rule of it that applies here, and
    style.css carries that rule: every item jQuery UI made a handle has it."""
    page = ready(open_as("visitor"), f"{live_server}/data/result/{job_id_of('cross_compare')}")
    assert not page.evaluate("() => [...document.styleSheets].some(sheet => (sheet.href || '').includes('jquery-ui'))")
    assert page.evaluate("""() => [...document.querySelectorAll('#sortable li')]
        .every(item => item.classList.contains('ui-sortable-handle') && getComputedStyle(item).touchAction === 'none')""")
    assert page.errors == []


def test_the_settings_lists_are_sortable(open_as, live_server):
    page = ready(open_as("visitor"), f"{live_server}/settings")

    unsorted = page.evaluate("ids => ids.filter(id => !Sortable.get(document.getElementById(id)))", SETTINGS_LISTS)
    assert unsorted == []
    assert page.errors == []


def test_a_row_click_navigates(open_as, live_server):
    page = ready(open_as("visitor"), f"{live_server}/explore/samples")
    sample_id = page.locator("tr.sample-row th.sample-id").first.inner_text().strip()

    with page.expect_navigation():
        page.locator("tr.sample-row th.sample-id").first.click()
    assert page.url.endswith(f"/explore/samples/{sample_id}")
    assert page.errors == []


def test_a_dragged_file_opens_the_drop_overlay(open_as, live_server):
    page = ready(open_as("visitor"), f"{live_server}/explore/samples")

    # Chromium leaves effectAllowed at "none" on a DataTransfer made outside a real
    # drag, so the event carries a stand-in with the one field base.html's handler reads
    page.evaluate("""() => {
        const event = new Event("dragover", {bubbles: true});
        Object.defineProperty(event, "dataTransfer", {value: {effectAllowed: "copy", dropEffect: "none"}});
        window.dispatchEvent(event);
    }""")
    page.wait_for_selector("#submitFileModal.show")
    settled(page, "#submitFileModal")
    assert page.evaluate("() => !!Dropzone.forElement('#myDropzone')")
    # inserted after DOMContentLoaded, Dropzone finds jQuery there and attaches its plugin
    assert page.evaluate("() => typeof jQuery.fn.dropzone") == "function"
    page.click("#close-submit-modal")
    page.wait_for_selector("#submitFileModal", state="hidden")
    assert page.errors == []


def test_the_upload_page_wires_its_form(open_as, live_server):
    """The page whose content the drop zone is: Dropzone is queued, dropzone_attach.js
    attaches the form with the page's options, and mcritInitDropzone, waiting in
    whenReady, has to find it attached once."""
    page = ready(open_as("contributor"), f"{live_server}/analyze/query")

    assert page.evaluate("() => !!jQuery._data(document.getElementById('submit-dropzone'), 'events').click")
    assert page.evaluate("() => Dropzone.instances.length") == 1
    assert page.evaluate("() => Dropzone.forElement('#myDropzone').options.autoProcessQueue") is False
    # queued after jQuery, Dropzone finds it and attaches its plugin, as on the overlay
    assert page.evaluate("() => typeof jQuery.fn.dropzone") == "function"
    page.check("#dumped")
    page.wait_for_selector("#extendedFields", state="visible")
    assert page.errors == []


def test_the_job_delete_confirmation_opens(open_as, live_server):
    page = ready(open_as("visitor"), f"{live_server}/data/jobs?state=finished")

    page.locator(".confirm-delete").first.click()
    page.wait_for_selector("#jobDeleteModal.show")
    settled(page, "#jobDeleteModal")
    page.locator("#jobDeleteModal .modal-footer [data-bs-dismiss=modal]").click()
    page.wait_for_selector("#jobDeleteModal", state="hidden")
    assert page.errors == []


# --- the time before jQuery has run ----------------------------------------------------------
#
# The queue loads once the page has been painted, and the page is on screen before it has
# run. These hold jquery.js back, so that every queued script behind it waits too, click
# something in that window, and then let it through.

@pytest.fixture
def held_back(open_as):
    """held_back(role) -> (page, release): a page on which every request for jquery.js
    waits until release() is called."""
    def _held_back(role):
        page = open_as(role)
        waiting = []
        released = []

        def hold(route):
            # one that reaches the handler after release() goes on at once. Unrouting in
            # release() instead lost a request the browser had caught but not yet handed
            # over: called as the queue started, it drained nothing, and jquery.js then
            # never loaded
            if released:
                route.continue_()
            else:
                waiting.append(route)
        page.route("**/static/jquery.js*", hold)

        def release(settle=True):
            released.append(True)
            while waiting:
                waiting.pop().continue_()
            if settle:
                page.wait_for_function("() => window.jQuery !== undefined")
                assert page.evaluate(AFTER_THE_READY_HANDLERS)
        return page, release
    return _held_back


def test_an_early_pagination_click_keeps_the_pickers_state(held_back, live_server):
    """fetchState is what a pagination or sort link adds to its URL, from onclick=. It
    used to read the rematch checkbox through jQuery, so a click before jQuery had run
    threw, and the browser followed the link's plain href instead: the selection, the
    rematch flag and the MinHash setting were gone from the next page."""
    page, _ = held_back("visitor")
    page.goto(f"{live_server}/analyze/compare?selected=0", wait_until="commit")
    page.wait_for_selector("ul.pagination li.active a.page-link")
    assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the click"
    page.check("#force_rematch")
    page.evaluate("""() => {
        const slider = document.getElementById("minhashBandRange");
        slider.value = "1";
        slider.dispatchEvent(new Event("input"));
    }""")

    with page.expect_navigation(wait_until="commit"):
        page.click("ul.pagination li.active a.page-link")
    assert "selected=0" in page.url and "rematch=true" in page.url and "minhashBandRange=1" in page.url, page.url
    assert page.errors == []


@pytest.mark.parametrize("picker", ["cross_compare", "unique_blocks"])
def test_an_early_remove_click_drops_the_sample(held_back, live_server, picker):
    """deleteSample, behind the x of a selected sample, found its row through jQuery
    and did nothing when clicked before jQuery had run."""
    page, _ = held_back("visitor")
    page.goto(f"{live_server}/analyze/{picker}?samples=0,1", wait_until="commit")
    page.wait_for_selector("button[aria-label='Remove sample']")

    with page.expect_navigation(wait_until="commit"):
        page.locator("button[aria-label='Remove sample']").first.click()
    assert "samples=1" in page.url and "samples=0" not in page.url, page.url
    assert page.errors == []

def test_an_early_analyze_click_does_not_turn_the_next_row_click_into_a_menu(held_back, live_server):
    """The Analyze button's onclick= sets a flag that the row's click handler takes back
    and opens the menu for. Clicked before jQuery had run, there was no row handler to
    take it back, and the next row click after load opened a menu instead of the sample."""
    page, release = held_back("visitor")
    page.goto(f"{live_server}/explore/samples", wait_until="commit")
    page.wait_for_selector("tr.sample-row button[id$='_analyze']")
    page.locator("tr.sample-row button[id$='_analyze']").first.click()
    release()
    sample_id = page.locator("tr.sample-row th.sample-id").first.inner_text().strip()

    with page.expect_navigation(timeout=5000):
        page.locator("tr.sample-row th.sample-id").first.click()
    assert page.url.endswith(f"/explore/samples/{sample_id}")
    assert page.errors == []


def test_an_early_switch_to_smda_still_shows_the_extra_fields(held_back, live_server):
    """The upload form's radios slide its extra fields in and out with jQuery, so before
    jQuery has run a toggle leaves them alone - it used to throw - and the ready handler
    is what puts the fields in the state the checked radio asks for. Dumped and SMDA
    both ask for them."""
    page, release = held_back("contributor")
    page.goto(f"{live_server}/analyze/query", wait_until="commit")
    page.wait_for_selector("#smda")
    page.check("#smda")
    release()

    page.wait_for_selector("#extendedFields", state="visible", timeout=5000)
    assert page.errors == []


def test_an_early_click_on_a_navbar_menu_does_not_reload_the_page(held_back, live_server):
    """Bootstrap opens the navbar's menus, and it runs after jQuery. Analyze and Data linked
    a signed-in user to "", so a click before Bootstrap had run reloaded the page; like
    Explore and the user menu they link to "#" now."""
    page, release = held_back("visitor")
    page.goto(f"{live_server}/", wait_until="commit")
    page.wait_for_selector("#navbarDropdownData")
    assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the click"
    page.evaluate("() => { window.stillThisPage = true; }")
    for toggle in ("#navbarDropdownAnalyze", "#navbarDropdownData"):
        page.click(toggle)
    release()

    assert page.evaluate("() => window.stillThisPage") is True, "a click on a menu reloaded the page"
    assert page.errors == []


#: picker -> (where it starts, its search box, what the URL has to carry after a search
#: besides the query, the query's name, what the search drops). The rematch box is ticked
#: and the MinHash slider moved before the search, where the page has them.
SEARCHES = {
    "cross_compare": ("/analyze/cross_compare?samples=0,1", "#Search",
                      {"samples": "0,1", "rematch": "true", "onlySelected": "false", "minhashBandRange": "1"}, "query", "cache"),
    # the 1 vs N search has never carried the selection, only the settings
    "compare": ("/analyze/compare?selected=0", "#Search",
                {"rematch": "true", "minhashBandRange": "1"}, "query", "selected"),
    "compare_versus a": ("/analyze/compare_versus?selected_a=0&selected_b=1", "#Search_a",
                         {"selected_b": "1", "rematch": "true", "minhashBandRange": "1"}, "query_a", "selected_a"),
    "compare_versus b": ("/analyze/compare_versus?selected_a=0&selected_b=1", "#Search_b",
                         {"selected_a": "0", "rematch": "true", "minhashBandRange": "1"}, "query_b", "selected_b"),
    "unique_blocks": ("/analyze/unique_blocks?samples=0,1", "#Search", {"samples": "0,1"}, "query", None),
}


@pytest.mark.parametrize("when", ["before jQuery", "after load"])
@pytest.mark.parametrize("picker", sorted(SEARCHES))
def test_a_search_keeps_the_pickers_state(held_back, live_server, picker, when):
    """The search box is the one each picker opens with focus in, so Enter is the first
    thing a user can do there. Bound from whenReady, the search was a plain form submission
    until jQuery had run, which kept the query and dropped everything else in the URL. It
    is bound to document while the page parses now - and only there. Bound twice as well,
    it would hand pagination_js_helper the same URL twice, which Chromium folds into one
    navigation, so the calls are counted too; on the 1 vs N page, which submits the form
    itself, it would add its fields twice."""
    start, box, carried, query, dropped = SEARCHES[picker]
    page, release = held_back("visitor")
    page.goto(live_server + start, wait_until="commit")
    page.wait_for_selector(box)
    if when == "after load":
        release()
    else:
        assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the search"
    if page.locator("#force_rematch").count():
        page.check("#force_rematch")
    if page.locator("#minhashBandRange").count():
        page.evaluate("""() => {
            const slider = document.getElementById("minhashBandRange");
            slider.value = "1";
            slider.dispatchEvent(new Event("input"));
        }""")
    page.fill(box, "mcrit")
    # counted in sessionStorage, which outlives the navigation the call starts
    page.evaluate("""() => {
        const helper = window.pagination_js_helper;
        sessionStorage.setItem("helperCalls", "0");
        window.pagination_js_helper = function () {
            sessionStorage.setItem("helperCalls", String(Number(sessionStorage.getItem("helperCalls")) + 1));
            return helper.apply(this, arguments);
        };
    }""")
    navigations = []
    page.on("request", lambda request: navigations.append(request.url) if request.is_navigation_request() and request.frame == page.main_frame else None)

    with page.expect_navigation(wait_until="commit"):
        page.press(box, "Enter")

    assert len(navigations) == 1, navigations
    assert page.evaluate("() => sessionStorage.getItem('helperCalls')") == ("0" if picker == "compare" else "1")
    parameters = parse_qs(urlsplit(page.url).query)
    assert parameters.pop(query) == ["mcrit"], page.url
    for name, value in carried.items():
        assert parameters.get(name) == [value], f"{name} in {page.url}"
    assert dropped is None or dropped not in parameters, page.url
    assert page.errors == []


# --- the queue -------------------------------------------------------------------------------

#: what the samples page queues: base.html's scripts, the upload form's for the drop overlay,
#: the search as you type (#293), and the family type-ahead, which the page and the
#: overlay's form each include
QUEUED_ON_THE_SAMPLES_PAGE = ["jquery.js", "post_action.js", "page_loading.js", "autocomplete.js", "upload_form.js",
                              "bootstrap-5.0.2-dist/js/bootstrap.bundle.min.js", "live_search.js", "family_names.js"]


#: the scripts in the order they run: a script's load event fires once it has run
RUN_ORDER = """
window.mcritRunOrder = [];
document.addEventListener("load", event => {
    if (event.target.tagName === "SCRIPT") {
        window.mcritRunOrder.push(event.target.src.split("?")[0].split("/static/")[1]);
    }
}, true);
"""


def test_the_queue_is_fetched_once_the_page_has_been_painted(open_as, live_server):
    """What the queue is for: its scripts are not fetched alongside the stylesheets the
    page is painted with, but once it has been painted, and they run in the order they were
    queued. Not the order their requests start in: the browser need not start them in the
    order they were added, and a test run under load saw it start one ahead of another."""
    page = open_as("visitor")
    page.add_init_script(RUN_ORDER)
    ready(page, f"{live_server}/explore/samples")
    timings = page.evaluate("""() => ({
        paint: performance.getEntriesByName("first-contentful-paint")[0].startTime,
        scripts: performance.getEntriesByType("resource")
            .filter(entry => entry.initiatorType === "script")
            .map(entry => [entry.name.split("?")[0].split("/static/")[1], entry.startTime]),
        ran: window.mcritRunOrder,
    })""")
    assert sorted(name for name, _ in timings["scripts"]) == sorted(QUEUED_ON_THE_SAMPLES_PAGE)
    assert timings["ran"] == QUEUED_ON_THE_SAMPLES_PAGE
    assert all(start > timings["paint"] for _, start in timings["scripts"]), timings
    assert page.errors == []


def test_the_csrf_token_is_there_before_the_queue_has_run(held_back, live_server):
    """The CFG viewers ask for csrfToken() from window.onload, which can come before the
    queue has run when nothing else was left to load. It used to come from post_action.js."""
    page, release = held_back("visitor")
    page.goto(f"{live_server}/explore/samples", wait_until="domcontentloaded")
    assert page.evaluate("() => window.jQuery") is None, "the queue ran before the check"
    token = page.get_attribute('meta[name="csrf-token"]', "content")
    assert token and page.evaluate("() => csrfToken()") == token
    release()
    assert page.errors == []


def test_a_press_on_a_navbar_menu_before_bootstrap_is_in_opens_it_once_it_is(held_back, live_server):
    page, release = held_back("visitor")
    page.goto(f"{live_server}/", wait_until="commit")
    page.wait_for_selector("#navbarDropdownExplore")
    assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the click"
    page.click("#navbarDropdownExplore")
    release()

    page.wait_for_selector('[aria-labelledby="navbarDropdownExplore"].show')
    assert page.get_attribute("#navbarDropdownExplore", "aria-expanded") == "true"
    assert page.errors == []


def test_a_press_on_a_post_button_before_post_action_is_in_posts_once_it_is(held_back, live_server):
    """The settings page's new-token button POSTs through post_action.js, which is queued
    behind jQuery. Pressed before it was in, the press did nothing."""
    page, release = held_back("visitor")
    page.goto(f"{live_server}/settings", wait_until="commit")
    button = "button[data-post$='/regenerate_apitoken']"
    page.wait_for_selector(button)
    assert page.evaluate("() => window.postTo") is None, "post_action.js ran before the click"
    page.click(button)

    # not waited for here: the replayed press navigates away while the page would be asked
    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/regenerate_apitoken")) as posted:
        release(settle=False)
    assert "csrf_token=" in (posted.value.post_data or "")
    page.wait_for_load_state()
    assert page.errors == []


def test_a_page_that_is_not_visible_loads_the_queue_without_painting(open_as, live_server):
    """A tab opened in the background paints no frame, so the queue does not wait for one.
    The page here reports no paint and runs no animation frame either, so only the hidden
    page's own branch starts the queue before the three-second backstop."""
    page = open_as("visitor")
    page.add_init_script("""
        Object.defineProperty(document, "visibilityState", {get: () => "hidden"});
        delete window.PerformanceObserver;
        window.requestAnimationFrame = () => 0;
    """)
    page.goto(f"{live_server}/explore/samples")
    # polled on a timer: the page's requestAnimationFrame never calls back here
    page.wait_for_function("() => window.bootstrap !== undefined && typeof Autocomplete !== 'undefined'", polling=100)
    assert page.evaluate(QUEUE_DELAY) < 1500
    assert page.evaluate(AFTER_THE_READY_HANDLERS)
    assert page.errors == []


def test_without_paint_timing_an_animation_frame_starts_the_queue(open_as, live_server):
    page = open_as("visitor")
    page.add_init_script("delete window.PerformanceObserver;")
    page = ready(page, f"{live_server}/explore/samples")
    assert page.evaluate(QUEUE_DELAY) < 1500
    assert page.errors == []


def test_a_page_that_reports_no_paint_loads_the_queue_after_three_seconds(open_as, live_server):
    page = open_as("visitor")
    page.add_init_script("""
        window.PerformanceObserver = class {
            static get supportedEntryTypes() { return ["paint"]; }
            observe() {}
            disconnect() {}
        };
    """)
    page = ready(page, f"{live_server}/explore/samples")
    assert 2500 < page.evaluate(QUEUE_DELAY) < 6000
    assert page.errors == []


def test_the_queue_is_loaded_once(open_as, live_server):
    """The paint, the animation frame and the three-second backstop can all try to start it."""
    page = ready(open_as("visitor"), f"{live_server}/explore/samples")
    page.wait_for_timeout(3500)
    assert page.evaluate("() => performance.getEntriesByType('resource').filter(entry => entry.name.split('?')[0].endsWith('/static/jquery.js')).length") == 1
    assert page.errors == []


def test_when_ready_after_the_queue_has_run_runs_at_once(open_as, live_server):
    page = ready(open_as("visitor"), f"{live_server}/explore/samples")
    assert page.evaluate("() => new Promise(resolve => { whenReady(() => resolve(typeof jQuery.fn.modal)); setTimeout(() => resolve('never'), 2000); })") == "function"
    assert page.errors == []


@pytest.mark.parametrize(("role", "path"), [("contributor", "/analyze/query"), ("visitor", "/explore/samples")])
def test_the_upload_form_is_set_without_a_slide_at_load(open_as, live_server, role, path):
    """The form's ready handler runs after the first paint now: a slide there would move
    the upload page's fields under the reader, and on every other page animate the closed
    drop overlay's form for nobody - the overlay built on the first drag, before it is
    shown. The fields end where the checked radios ask."""
    page = open_as(role)
    # counts every jQuery animation from the moment jQuery arrives: slideDown and slideUp
    # go through .animate()
    page.add_init_script("""
        window.animations = 0;
        Object.defineProperty(window, "jQuery", {configurable: true, set(value) {
            Object.defineProperty(window, "jQuery", {configurable: true, writable: true, value: value});
            const animate = value.fn.animate;
            value.fn.animate = function () { window.animations += this.length; return animate.apply(this, arguments); };
        }});
    """)
    ready(page, live_server + path)
    if path != "/analyze/query":
        page.evaluate("() => new Promise((resolve) => mcritLoadDropzone(resolve))")
        # its ready handler, queued once it was in
        page.evaluate(AFTER_THE_READY_HANDLERS)
    assert page.evaluate("() => window.animations") == 0
    extended = page.evaluate("() => getComputedStyle(document.getElementById('extendedFields')).display")
    asked = page.evaluate("() => document.getElementById('dumped').checked || document.getElementById('smda').checked")
    assert (extended != "none") == asked
    assert page.errors == []


def test_a_copy_pressed_before_jquery_is_in_copies_and_confirms_once_it_is(held_back, live_server):
    """copy_to_clipboard is an onclick= handler. It copied, and then threw "$ is not defined"
    before showing its "Copied!" tooltip, when pressed before the scripts were in."""
    page, release = held_back("visitor")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server)
    page.goto(f"{live_server}/explore/samples/0", wait_until="commit")
    button = page.locator("button[data-clipboard-value]").first
    button.wait_for()
    assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the press"
    button.click()
    assert page.evaluate("() => navigator.clipboard.readText()") == button.get_attribute("data-clipboard-value")
    release()

    page.wait_for_selector(".tooltip.show")
    assert "Copied!" in page.inner_text(".tooltip.show")
    assert page.errors == []


def test_a_press_on_an_element_with_a_tooltip_is_not_held(held_back, live_server):
    """A tooltip acts on hover and focus. The copy button marked up as Bootstrap 5 marks a
    tooltip, data-bs-toggle="tooltip", still copies on the press itself."""
    page, release = held_back("visitor")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server)
    page.goto(f"{live_server}/explore/samples/0", wait_until="commit")
    button = page.locator("button[data-clipboard-value]").first
    button.wait_for()
    button.evaluate("button => button.setAttribute('data-bs-toggle', 'tooltip')")
    assert page.evaluate("() => window.jQuery") is None, "jQuery ran before the press"
    button.click()
    assert page.evaluate("() => navigator.clipboard.readText()") == button.get_attribute("data-clipboard-value")
    assert page.evaluate("() => mcritScripts.pressed.length") == 0
    release()
    assert page.errors == []


def test_the_slider_label_follows_the_slider_moved_before_the_scripts_are_in(held_back, live_server):
    """The label beside the upload form's minhash slider follows it before the upload
    form's script is in, and after: its handler stays in the template, which is all it
    needs."""
    page, release = held_back("contributor")
    page.goto(f"{live_server}/analyze/query", wait_until="commit")
    page.wait_for_selector("#minhashBandRange_dropzone")
    page.eval_on_selector("#minhashBandRange_dropzone", "range => { range.value = 0; range.dispatchEvent(new Event('input')); }")
    assert page.inner_text("#minhashBandRange-currentSelection_dropzone").strip() == "Off"
    release()

    page.eval_on_selector("#minhashBandRange_dropzone", "range => { range.value = 3; range.dispatchEvent(new Event('input')); }")
    assert page.inner_text("#minhashBandRange-currentSelection_dropzone").strip() == "Complete"
    assert page.errors == []


def test_a_page_that_includes_the_family_type_ahead_twice_fetches_it_once(open_as, live_server):
    """A listing includes js/ac_family_names.html for its edit modals, and the drop
    overlay's form, built on the first drag, names it again. static/family_names.js is
    fetched once, and it attaches to the overlay's family field when it is focused."""
    page = open_as("contributor")
    requested = []
    page.on("request", lambda request: requested.append(request.url) if request.url.split("?")[0].endswith("/static/family_names.js") else None)
    ready(page, f"{live_server}/explore/samples")
    page.evaluate("() => new Promise((resolve) => mcritLoadDropzone(() => { $('#submitFileModal').modal('show'); resolve(); }))")
    settled(page, "#submitFileModal")
    page.check("#submit_form")
    page.wait_for_selector("#family", state="visible")
    page.focus("#family")
    assert len(requested) == 1, requested
    assert page.eval_on_selector("#family", "field => field.dataset.familyAutocomplete") == "attached"
    assert page.errors == []
