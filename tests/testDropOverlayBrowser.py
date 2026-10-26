#!/usr/bin/python
"""The drop overlay built on the first drag, in Chromium.

The overlay's dialog and form are fetched from data.drop_overlay on the first drag that
carries a file, while Dropzone is (testDropOverlay.py has the page's side). The drags here
are the browser's own, through the DevTools protocol, so that what the browser does with
a file nobody takes - open it in place of the page - is what the page has to prevent.
`playwright` is not a dependency and CI does not install it, so this module skips there.
"""

import json
import logging
import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

from mcritweb.csrf import ENABLED_KEY, FIELD_NAME, unmask

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SMDA_REPORT = '{\n "base_addr": 4194304,\n "bitness": 32,\n "metadata": {\n  "family": "dropfamily",\n  "version": "1.0"\n }\n}\n'

WAIT_FOR_THE_QUEUE = "() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })"

#: the page's layout shifts from its first paint on, as Lighthouse adds them up
COUNT_LAYOUT_SHIFTS = """
window.mcritShifted = 0;
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) { window.mcritShifted += entry.value; }
    }
}).observe({type: "layout-shift", buffered: true});
"""

TWO_FRAMES_ON = "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(window.mcritShifted))))"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def app(app):
    # the upload is checked for its token as in production
    app.config[ENABLED_KEY] = True
    return app


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
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            yield browser
        finally:
            browser.close()


class Visit:
    """A signed-in page, the requests it made by path, its errors, and a file to drag."""

    def __init__(self, app, browser, live_server, user_id, tmp_path):
        self.app = app
        self.live_server = live_server
        self.context = browser.new_context()
        self.cookie = session_cookie_for(app, user_id)
        self.context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": self.cookie, "url": live_server}])
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(f"pageerror: {error}"))
        self.page.on("console", lambda message: self.errors.append(f"console: {message.text}") if message.type == "error" else None)
        self.requested = []
        self.page.on("request", lambda request: self.requested.append(request.url.replace(live_server, "").split("?")[0]))
        self.cdp = self.context.new_cdp_session(self.page)
        self.file = tmp_path / "sample.smda"
        self.file.write_text(SMDA_REPORT)

    def open(self, path):
        self.page.goto(self.live_server + path)
        assert self.page.evaluate(WAIT_FOR_THE_QUEUE)
        return self.page

    def drag(self, *kinds, x=400, y=300):
        """The browser's own drag of the file, as a file manager starts one: copy only."""
        for kind in kinds:
            self.cdp.send("Input.dispatchDragEvent", {"type": kind, "x": x, "y": y,
                                                      "data": {"items": [], "files": [str(self.file)], "dragOperationsMask": 1}})

    def count(self, path):
        return self.requested.count(path)

    def session_token(self):
        """The token the browser's session holds now: the server minted it after sign-in."""
        cookie = next(cookie["value"] for cookie in self.context.cookies() if cookie["name"] == self.app.config["SESSION_COOKIE_NAME"])
        session = self.app.session_interface.get_signing_serializer(self.app).loads(cookie)
        return session[FIELD_NAME].encode()


@pytest.fixture
def visit(app, browser, live_server, make_user, tmp_path):
    def _visit(role="contributor"):
        return Visit(app, browser, live_server, make_user(role), tmp_path)
    return _visit


def shown(page):
    """Wait for the dialog to finish opening: Bootstrap ignores a hide asked for while
    its show transition still runs."""
    page.wait_for_function("""() => {
        const modal = bootstrap.Modal.getInstance(document.getElementById("submitFileModal"));
        return modal !== null && modal._isShown && !modal._isTransitioning;
    }""")


def hidden(page):
    page.wait_for_function("""() => {
        const modal = bootstrap.Modal.getInstance(document.getElementById("submitFileModal"));
        return modal !== null && !modal._isShown && !modal._isTransitioning;
    }""")


@pytest.mark.parametrize("path", ["/", "/explore/samples"])
def test_the_first_drag_builds_the_overlay_once_and_shows_it(visit, path):
    """The overlay was in the page; it is built on the first drag now, once however many
    dragover events the held file fires, with the options and the token it had."""
    visit = visit()
    page = visit.open(path)
    assert page.evaluate("() => document.getElementById('submitFileModal')") is None, "the page carries the overlay"

    visit.drag("dragEnter", *["dragOver"] * 10)
    shown(page)
    visit.drag(*["dragOver"] * 5, "dragCancel")

    assert visit.count("/data/drop_overlay") == 1
    assert visit.count("/static/dropzone-patched.min.js") == 1
    zone = page.evaluate("""() => { const options = Dropzone.forElement('#myDropzone').options;
        return [Dropzone.instances.length, options.maxFilesize, options.autoProcessQueue, options.paramName, options.headers['X-CSRF-Token'],
                document.querySelectorAll('#submitFileModal').length, document.querySelectorAll('link[href*="/dropzone.min.css"]').length]; }""")
    assert zone[:4] == [1, 100000000, False, "file"], zone
    assert unmask(zone[4], len(visit.session_token())) == visit.session_token()
    assert zone[5:] == [1, 1], zone
    # the zone's own look, from the style that came with it; solid while a file is held
    # over it, which is Dropzone's dz-drag-hover
    style = page.evaluate("""() => { const zone = document.getElementById('myDropzone'); zone.classList.remove('dz-drag-hover');
        const style = getComputedStyle(zone); return [style.borderTopStyle, style.borderTopWidth, style.borderTopLeftRadius, style.maxWidth]; }""")
    assert style == ["dashed", "2px", "25px", "600px"], style
    assert visit.errors == []


def test_a_file_dropped_before_the_overlay_is_in_is_kept_and_uploaded_with_the_form(visit, live_server):
    """The drop came before anything took it: the browser opened the file in place of
    the page, or (with nothing to open it with) dropped it, and the overlay came up empty.
    The page takes it while the overlay is on its way and hands it to the zone, which
    pre-fills the form from it and uploads it with the fields and the token."""
    visit = visit()
    page = visit.open("/explore/samples")
    held = []
    page.route("**/static/dropzone-patched.min.js*", lambda route: held.append(route))
    filled = []

    def answer_filename_info(route):
        filled.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"smda": True, "family": "dropfamily", "version": "1.0", "bitness": 32, "base_addr": "0x400000"}))

    page.route("**/data/request_filename_info", answer_filename_info)
    uploads = []

    def take_the_upload(route):
        uploads.append(route.request)
        route.fulfill(status=200, content_type="text/plain", body=live_server + "/explore/samples")

    page.route("**/data/submit_or_query", take_the_upload)

    visit.drag("dragEnter", "dragOver", "dragOver", "drop")
    page.wait_for_timeout(200)
    assert held, "Dropzone was not asked for"
    assert page.url == live_server + "/explore/samples"
    held[0].continue_()
    shown(page)
    page.wait_for_timeout(200)
    assert page.evaluate("() => Dropzone.forElement('#myDropzone').files.map((file) => file.name)") == ["sample.smda"], "the file was lost"
    page.wait_for_function("() => document.getElementById('smda').checked && document.getElementById('family').value === 'dropfamily'")
    assert filled and filled[0]["file_header"] == SMDA_REPORT

    page.check("#submit_form")
    page.wait_for_selector("#family", state="visible")
    with page.expect_navigation():
        page.click("#submit-dropzone")

    assert len(uploads) == 1
    upload = uploads[0]
    body = upload.post_data_buffer.decode("latin-1")
    for name, value in (("form_type", "submit_form"), ("family", "dropfamily"), ("version", "1.0"), ("options", "smda"), ("minhashBandRange", "2")):
        assert f'name="{name}"\r\n\r\n{value}\r\n' in body, (name, value)
    assert 'name="file"; filename="sample.smda"' in body
    assert unmask(upload.headers["x-csrf-token"], len(visit.session_token())) == visit.session_token()
    assert visit.errors == []


@pytest.mark.parametrize("path", ["/", "/explore/samples"])
def test_the_family_type_ahead_works_in_the_built_form(visit, path):
    """`/` has no edit modal, and so no type-ahead until the overlay is built, which
    fetches it; the listing has one queued for its modals, which the overlay's field gets
    on its first focus. Either way it is fetched once and suggests what the server answers."""
    visit = visit()
    page = visit.open(path)
    assert page.evaluate("() => document.getElementById('family')") is None, "the page carries the overlay's form"
    page.route("**/explore/familyNames?q=*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"suggestions": [{"label": "citadel", "value": "citadel"}]})))
    visit.drag("dragEnter", "dragOver", "dragOver")
    shown(page)
    visit.drag("dragCancel")

    page.check("#submit_form")
    page.wait_for_selector("#family", state="visible")
    page.click("#family")
    page.keyboard.type("cit")
    page.wait_for_selector("#submitFileModal .dropdown-menu.show .dropdown-item")
    assert page.inner_text("#submitFileModal .dropdown-menu.show .dropdown-item") == "citadel"
    page.click("#submitFileModal .dropdown-menu.show .dropdown-item")
    assert page.input_value("#family") == "citadel"
    assert visit.count("/static/family_names.js") == 1
    assert visit.errors == []


def test_a_second_drag_reuses_the_form_and_escape_and_the_close_button_close_it(visit):
    """Built once: what was chosen in the form stays, as it did when the form was in the
    page, and leaving the window and coming back changes nothing."""
    visit = visit()
    page = visit.open("/data/export")
    visit.drag("dragEnter", "dragOver", "dragOver")
    shown(page)
    visit.drag("dragCancel")
    page.check("#dumped")
    page.wait_for_selector("#extendedFields", state="visible")
    page.evaluate("() => { document.getElementById('dropzone-additional-fields-form').built = 'once'; }")

    page.keyboard.press("Escape")
    hidden(page)
    visit.drag("dragEnter", "dragOver", "dragOver")
    shown(page)
    # out of the window and back, the dialog still open
    visit.drag("dragCancel", "dragEnter", "dragOver", "dragCancel")
    assert page.evaluate("() => document.getElementById('submitFileModal').classList.contains('show')")
    page.click("#close-submit-modal")
    hidden(page)
    visit.drag("dragEnter", "dragOver")
    shown(page)
    visit.drag("dragCancel")

    assert page.evaluate("() => document.getElementById('dropzone-additional-fields-form').built") == "once"
    assert page.is_checked("#dumped")
    assert visit.count("/data/drop_overlay") == 1 and visit.count("/static/dropzone-patched.min.js") == 1
    assert page.evaluate("() => [Dropzone.instances.length, document.querySelectorAll('#myDropzone').length]") == [1, 1]
    assert visit.errors == []


def test_building_the_overlay_moves_nothing_and_takes_no_focus(visit):
    """It goes in at the end of the content, hidden, so the page does not move; and its
    family field's autofocus does not take the focus from where the reader has it."""
    visit = visit()
    visit.page.add_init_script(COUNT_LAYOUT_SHIFTS)
    page = visit.open("/explore/samples")
    assert page.evaluate("() => document.getElementById('dropzone-additional-fields-form')") is None, "the page carries the overlay's form"
    page.focus("#sample-search, input, a")
    focused = page.evaluate("() => document.activeElement.outerHTML")
    page.evaluate("() => new Promise((resolve) => mcritLoadDropzone(resolve))")
    assert page.evaluate("() => !!document.getElementById('dropzone-additional-fields-form')")
    assert page.evaluate(TWO_FRAMES_ON) == 0
    assert page.evaluate("() => document.activeElement.outerHTML") == focused
    assert visit.errors == []


def test_a_session_that_has_ended_builds_nothing_and_the_next_drag_tries_again(visit, live_server):
    """Signed out in another tab, the overlay's request lands on the login page, which is
    not the overlay: nothing is put in the page, and once signed in again a drag builds it."""
    visit = visit()
    page = visit.open("/explore/samples")
    visit.context.clear_cookies()
    page.evaluate("() => new Promise((resolve) => { mcritLoadDropzone(() => {}); const wait = () => window.mcritDropzoneLoading === null ? resolve() : setTimeout(wait, 20); wait(); })")
    assert page.evaluate("() => [!!document.getElementById('submitFileModal'), !!window.mcritDropzoneReady]") == [False, False]

    visit.context.add_cookies([{"name": visit.app.config["SESSION_COOKIE_NAME"], "value": visit.cookie, "url": live_server}])
    visit.drag("dragEnter", "dragOver", "dragOver")
    shown(page)
    visit.drag("dragCancel")
    assert visit.count("/data/drop_overlay") == 2 and visit.count("/static/dropzone-patched.min.js") == 1
    assert visit.errors == []
