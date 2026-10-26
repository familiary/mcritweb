#!/usr/bin/python
"""Every page works under the Content-Security-Policy, driven in a browser.

testScriptNonces.py and testFramingHeaders.py pin what the policy says and that every
script element carries the response's nonce. Whether that is enough is a question only a
browser answers: the scripts MCRITweb runs are mostly not in the page at all but added
by its own scripts - base.html's later() queue, the CFG viewer's libraries, Dropzone
fetched on the first drag, the layout worker and what it imports, the live search's
swapped-in rows - and each of those has to pass the policy too. So these tests walk every
page as each role and signed out, use what scripts the pages have, and fail on any
`securitypolicyviolation`, on a page error, and on a console message saying the policy
blocked something. The CFG viewer falls back to laying a graph out on the page when its
worker fails, which would hide a blocked worker; it says so in the console, and that
fails these tests as well.

The harness is the offline one of tests/testFunctionVsBrowser.py: `CorpusMcritClient`
behind the app, served on a loopback port. `playwright` is not a dependency of this
project and CI does not install it, so this module skips there rather than failing.
"""

import os
import re
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from testJobOverview import PARENT_JOB_ID, CountingJobs, job_data, running
from testScriptNonces import PAGES
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 30000

#: Collects what the policy refused, from before the page's first script runs.
RECORD_VIOLATIONS = """
window.cspViolations = [];
document.addEventListener("securitypolicyviolation", function (event) {
  window.cspViolations.push(event.violatedDirective + " " + event.blockedURI + " " + (event.sample || "")
    + " at " + event.sourceFile + ":" + event.lineNumber);
});
"""

#: The pages a signed-out caller reaches, and one that sends them to /login.
SIGNED_OUT_PAGES = ["/login", "/register", "/help", "/explore/samples"]

#: Two functions of the captured corpus with control flow graphs, as testFunctionVsBrowser.py has them.
FUNCTION_A = 84
FUNCTION_B = 943


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


class Watched:
    """A page of a context signed in as `role` (or signed out), and what went wrong on it."""

    def __init__(self, browser, app, make_user, role=None):
        # the copy buttons write to the clipboard; granted, so that they do not prompt
        self.context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        if role:
            self.context.add_cookies([{
                "name": app.config["SESSION_COOKIE_NAME"],
                "value": session_cookie_for(app, make_user(role=role)),
                "domain": "127.0.0.1",
                "path": "/",
                "secure": app.config["SESSION_COOKIE_SECURE"],
            }])
        self.context.add_init_script(RECORD_VIOLATIONS)
        self.problems = []
        self.page = self.new_page()

    def new_page(self):
        page = self.context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        page.on("pageerror", lambda error: self.problems.append(f"{page.url}: page error {error}"))
        page.on("console", self._console)
        # the admin page's copy icon and the YARA copy confirm with alert(), the
        # maintenance forms ask first with confirm(); dismissed, so nothing is scheduled
        page.on("dialog", lambda dialog: dialog.dismiss())
        return page

    def _console(self, message):
        text = message.text
        if "Content Security Policy" in text or "layout worker failed" in text:
            self.problems.append(f"console {message.type}: {text}")

    def open(self, url):
        """Load `url`; once a page of ours is in, wait for its queued scripts to have run."""
        response = self.page.goto(url)
        if response.status == 200 and "text/html" in response.headers.get("content-type", ""):
            self.page.wait_for_function("() => window.bootstrap !== undefined")
        return response

    def violations(self):
        return self.page.evaluate("window.cspViolations || []")

    def assert_clean(self):
        assert self.violations() == [], f"the policy refused something on {self.page.url}"
        assert self.problems == []


# --- every page, as each role and signed out -------------------------------------------

@pytest.mark.parametrize("role", ["visitor", "contributor", "admin", None])
def test_no_page_breaks_the_policy(browser, app, live_server, make_user, role):
    """Pages a role may not see answer with a 403 or a redirect, which is walked too."""
    if role is None:
        # with no user at all, every page sends the caller to registration instead
        make_user(role="admin", username="someoneelse")
    watched = Watched(browser, app, make_user, role)
    refused = {}
    for path in PAGES if role else SIGNED_OUT_PAGES:
        watched.open(live_server + path)
        if watched.violations():
            refused[path] = watched.violations()
        watched.page.close()
        watched.page = watched.new_page()
    assert refused == {}
    assert watched.problems == []


# --- what the pages' scripts do ------------------------------------------------------

@pytest.fixture
def admin(browser, app, live_server, make_user):
    return Watched(browser, app, make_user, "admin")


def test_a_file_dropped_on_the_submit_page_prefills_the_form(admin, live_server):
    """Dropzone is queued with later(); the drop sends the head of the file to
    request_filename_info, a same-origin fetch."""
    page = admin.page
    admin.open(live_server + "/data/submit")
    page.wait_for_function("() => document.getElementById('myDropzone').dropzone !== undefined")
    dropped = page.evaluate_handle(
        "() => { const data = new DataTransfer(); data.items.add(new File(['MZ' + '\\0'.repeat(2000)], 'sample.bin')); return data; }")
    with page.expect_response(lambda response: "request_filename_info" in response.url):
        page.dispatch_event("#myDropzone", "drop", {"dataTransfer": dropped})
    page.wait_for_selector(".dz-preview")
    admin.assert_clean()


def test_the_drop_overlay_fetches_dropzone_on_demand(admin, live_server):
    """upload_form.js adds Dropzone's script itself, on the first drag over a page."""
    page = admin.page
    admin.open(live_server + "/explore/samples")
    # what the overlay's dragover listener does with a file held over the page
    page.evaluate("() => mcritLoadDropzone(() => $('#submitFileModal').modal('show'))")
    page.wait_for_selector("#submitFileModal.show")
    page.wait_for_function("() => window.mcritDropzoneReady === true")
    page.set_input_files("input.dz-hidden-input", {"name": "sample.bin", "mimeType": "application/octet-stream", "buffer": b"MZ" * 100})
    page.wait_for_selector("#submitFileModal .dz-preview")
    admin.assert_clean()


@pytest.mark.parametrize("path, dialog", [("/explore/samples", "#editSampleModal"), ("/explore/families", "#editFamilyModal")])
def test_the_rows_a_live_search_swaps_in_bring_their_script_with_the_pages_nonce(admin, live_server, path, dialog):
    """The fetched page's scripts carry that response's nonce, which this page's policy
    does not name; live_search.js gives the ones it re-creates this page's own."""
    page = admin.page
    admin.open(live_server + path)
    page.fill("input[name=query]", "citadel")
    page.wait_for_function("() => new URL(location.href).searchParams.get('query') === 'citadel'")
    nonces = page.evaluate("""() => {
        const region = document.getElementById(document.querySelector('form[data-live-search]').dataset.liveSearch);
        return [document.querySelector('script[nonce]').nonce,
                [...region.childNodes].filter(node => node.nodeName === 'SCRIPT').map(script => script.nonce)];
    }""")
    page_nonce, swapped_in = nonces
    assert swapped_in, f"the rows swapped into {path} brought no script - not what this test means to cover"
    assert swapped_in == [page_nonce] * len(swapped_in)
    page.locator(f"[data-bs-target='{dialog}']").first.click()
    page.wait_for_selector(f"{dialog}.show")
    admin.assert_clean()


def test_the_function_page_draws_its_graph_with_the_worker(admin, live_server):
    page = admin.page
    admin.open(live_server + f"/explore/functions/{FUNCTION_A}")
    page.evaluate("document.getElementById('xcfg_container').scrollIntoView()")
    page.wait_for_selector("#graphContainer g.node", state="attached")
    page.wait_for_function("() => !document.getElementById('showLoops').disabled")
    page.click("#showLoops")
    page.click("#showCycles")
    page.check("#enableTooltip")
    admin.assert_clean()


def test_the_function_comparison_draws_both_graphs_and_the_combined_one(admin, live_server):
    page = admin.page
    admin.open(live_server + f"/data/matches/function/{FUNCTION_A}/{FUNCTION_B}")
    page.evaluate("document.getElementById('xcfg_container').scrollIntoView()")
    for pane in ("a", "b"):
        page.wait_for_selector(f"#graphContainer_{pane} g.node", state="attached")
    page.wait_for_function("() => !document.getElementById('viewCombined').disabled")
    page.click("#viewCombined")
    page.wait_for_selector("#graphContainer_c g.node", state="attached")
    page.locator("[data-bs-target='#apiUsageModal']").first.click()
    page.wait_for_selector("#apiUsageModal.show")
    admin.assert_clean()


def test_the_cross_compare_tabs_fill_and_its_controls_navigate(admin, live_server):
    page = admin.page
    url = live_server + f"/data/result/{job_id_of('cross_compare')}"
    admin.open(url)
    for tab in page.locator("#pills-tab button").all():
        tab.click()
        page.wait_for_selector(".tab-pane.active.show span.score-swatch")
    # the last tab's pane was filled by cloning the first one's cells
    with page.expect_navigation():
        page.locator(".tab-pane.active a.score-swatch[href]").first.click()
    assert re.search(r"/data/result/[0-9a-f]{24}\?samid=\d+$", page.url), page.url
    admin.assert_clean()
    admin.open(url)
    with page.expect_navigation():
        page.click("text=Change order")
    admin.assert_clean()


def test_a_matrix_cell_leads_to_its_matches_before_the_queued_scripts_are_in(admin, live_server):
    """A cell with matches is a link, which needs no script at all, so a click does not
    wait for jQuery."""
    page = admin.page
    page.route("**/static/jquery.js", lambda route: None)
    # not "load": the held script can keep the page from ever reaching it
    page.goto(live_server + f"/data/result/{job_id_of('cross_compare')}", wait_until="domcontentloaded")
    with page.expect_navigation():
        page.locator("a.score-swatch[href]").first.click()
    assert re.search(r"/data/result/[0-9a-f]{24}\?samid=\d+$", page.url), page.url
    assert admin.violations() == [] and admin.problems == []


def test_the_modals_and_the_sortable_headers_work(admin, live_server):
    page = admin.page
    admin.open(live_server + "/explore/families")
    page.locator("[data-bs-target='#editFamilyModal']").first.click()
    page.wait_for_selector("#editFamilyModal.show")
    page.check("#family_delete")
    page.keyboard.press("Escape")
    with page.expect_navigation():
        page.locator("th a.table-sort").first.click()
    admin.assert_clean()
    admin.open(live_server + "/data/jobs?active=getMatchesForSample")
    page.locator(".confirm-delete").first.click()
    page.wait_for_selector("#jobDeleteModal.show")
    page.click("#jobDeleteModal >> text=Cancel")
    admin.assert_clean()


def test_the_copy_buttons_and_the_confirming_forms_work(admin, live_server):
    page = admin.page
    admin.open(live_server + "/explore/samples/0")
    page.locator(".btn-clipboard").first.click()
    page.wait_for_selector(".tooltip")
    admin.open(live_server + "/admin/server")
    page.locator("i.fa-copy").first.click()
    # its onsubmit asks first, and the dismissed confirm keeps the form from posting
    page.click("text=Recalculate PicHashes")
    admin.open(live_server + f"/data/result/{job_id_of('unique_blocks')}")
    page.click("#pills-yara-tab")
    page.click("button:has-text('Copy YARA rule')")
    admin.assert_clean()


def test_the_column_settings_drag_and_save(admin, live_server):
    """SortableJS is queued from the page's style block; saving posts a form the script builds."""
    page = admin.page
    admin.open(live_server + "/settings")
    page.click("#pills-column-settings-tab")
    page.wait_for_selector("#pills-column-settings.show")
    items = page.locator("#family_table_active .list-group-item")
    items.nth(0).drag_to(items.nth(2))
    with page.expect_navigation():
        page.click("#saveButton")
    admin.assert_clean()


def test_the_api_token_is_shown_and_copied(admin, live_server):
    """show_apitoken.js is queued with later() and fetches the token from the same origin."""
    page = admin.page
    admin.open(live_server + "/settings")
    page.click("button[data-show-apitoken]")
    page.wait_for_function("() => document.querySelector('#apitoken-value').textContent === 'apitoken-admin'")
    page.click("#apitoken-copy .btn-clipboard")
    page.wait_for_selector(".tooltip")
    admin.assert_clean()


def test_a_running_jobs_page_polls_its_status(browser, app, live_server, make_user):
    """The overview of an unfinished job asks data.job_status on a timer (#183)."""
    jobs = CountingJobs(running(job_data(PARENT_JOB_ID, 10, params='{"0": 7}')), [])
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: jobs
    watched = Watched(browser, app, make_user, "visitor")
    polls = []
    watched.page.on("request", lambda request: polls.append(request.url) if request.url.endswith("/status") else None)
    watched.open(live_server + f"/data/jobs/{PARENT_JOB_ID}?refresh=1")
    watched.page.wait_for_timeout(2500)
    assert polls, "the page did not poll"
    watched.assert_clean()
