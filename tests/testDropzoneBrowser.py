#!/usr/bin/python
"""The drop overlay's Dropzone, loaded on the first drag (issue #63), in a browser.

It has to be the project's patched `static/dropzone.js`: stock Dropzone sends no head of
the file, and the submit form is pre-filled from that head. The browser tests need
playwright with a chromium build; without either they skip, as in CI.
"""

import json
import logging
import os
import threading
import unittest

import pytest
from conftest import session_cookie_for

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SMDA_REPORT = '{\n "base_addr": 4194304,\n "bitness": 32,\n "metadata": {\n  "family": "dropfamily",\n  "version": "1.0"\n }\n}\n'


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def session_cookie_value(app, user_id):
    return session_cookie_for(app, user_id)


@pytest.fixture
def live_url(app):
    """The app on a real socket. Playwright needs one; the test client is not a server."""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


def open_samples_page(playwright, app, live_url, user_id):
    try:
        browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001 - a missing browser is a skip, not a failure
        pytest.skip(f"playwright has no chromium installed: {exc}")
    context = browser.new_context()
    context.add_cookies([{"name": "session", "value": session_cookie_value(app, user_id), "url": live_url}])
    return browser, context.new_page()


def test_a_failed_load_is_tried_again_and_waiting_callers_are_queued_once(app, live_url, make_user):
    """A failed fetch of the script used to leave `mcritDropzoneLoading` set, so the
    overlay never opened again on that page; and every dragover while the script was
    on its way queued the same callback again."""
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("contributor")
    fetches = []
    held = []

    def serve_dropzone(route):
        fetches.append(route.request.url)
        if len(fetches) == 1:
            route.fulfill(status=404, body="")
        else:
            held.append(route)

    with sync_api.sync_playwright() as playwright:
        browser, page = open_samples_page(playwright, app, live_url, user_id)
        try:
            page.route("**/static/dropzone.js", serve_dropzone)
            page.goto(live_url + "/explore/samples")
            # the page's scripts load after its first paint, which can come after load
            page.evaluate("() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })")
            page.evaluate("() => { window.calls = 0; window.count = () => window.calls++; mcritLoadDropzone(window.count); }")
            page.wait_for_function("() => window.mcritDropzoneLoading === null", timeout=10000)
            assert not page.evaluate("() => window.mcritDropzoneReady")

            page.evaluate("() => { for (let i = 0; i < 20; i++) { mcritLoadDropzone(window.count); } }")
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(50)
            assert held, "the second call did not fetch the script again"
            assert page.evaluate("() => window.mcritDropzoneLoading.length") == 1
            held[0].continue_()
            page.wait_for_function("() => window.mcritDropzoneReady === true", timeout=10000)
            assert page.evaluate("() => window.calls") == 1
            assert page.evaluate("() => document.querySelectorAll('link[href$=\"dropzone.min.css\"]').length") == 1
        finally:
            browser.close()
    assert len(fetches) == 2


def test_the_lazily_fetched_stylesheet_leaves_the_zones_own_look_alone(app, live_url, make_user):
    """Dropzone's stock `.dropzone` rule (a thin solid grey border on white) is as specific
    as the macro's `dropzone.style()`; appended at the end of the head it came last and won,
    so the overlay lost its dashed outline and its fill the moment it was fetched."""
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("contributor")

    with sync_api.sync_playwright() as playwright:
        browser, page = open_samples_page(playwright, app, live_url, user_id)
        try:
            page.goto(live_url + "/explore/samples")
            # the page's scripts load after its first paint, which can come after load
            page.evaluate("() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })")
            page.evaluate("() => new Promise((resolve) => mcritLoadDropzone(resolve))")
            page.wait_for_function("() => [...document.styleSheets].some((sheet) => (sheet.href || '').endsWith('dropzone.min.css'))")
            zone = page.evaluate(
                "() => { const style = getComputedStyle(document.getElementById('myDropzone'));"
                " return [style.borderTopStyle, style.borderTopWidth, style.backgroundColor, style.borderTopLeftRadius]; }"
            )
        finally:
            browser.close()

    assert zone == ["dashed", "2px", "rgb(240, 240, 240)", "25px"], zone


def test_a_dropped_file_sends_its_head_for_the_pre_fill(app, live_url, make_user):
    """The lazily loaded Dropzone must be the patched build: stock 5.9.2 sets no
    `file.upload.header`, so `file_header` went missing from the pre-fill request and
    bitness, base address, family and version stayed empty."""
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("contributor")
    posted = []

    def answer_filename_info(route):
        posted.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body="{}")

    with sync_api.sync_playwright() as playwright:
        browser, page = open_samples_page(playwright, app, live_url, user_id)
        try:
            page.route("**/data/request_filename_info", answer_filename_info)
            page.route("**/data/submit_or_query", lambda route: route.abort())
            page.goto(live_url + "/explore/samples")
            # the page's scripts load after its first paint, which can come after load
            page.evaluate("() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })")
            page.evaluate("() => mcritLoadDropzone(() => {})")
            page.wait_for_function("() => window.mcritDropzoneReady === true", timeout=10000)
            page.evaluate(
                "(report) => Dropzone.forElement('#myDropzone').addFile(new File([report], 'sample.smda'))",
                SMDA_REPORT,
            )
            for _ in range(100):
                if posted:
                    break
                page.wait_for_timeout(50)
        finally:
            browser.close()

    assert posted, "the drop sent no pre-fill request"
    assert posted[0].get("file_header") == SMDA_REPORT
    assert posted[0].get("file_metadata", "").startswith('"metadata"')


#: the page's layout shifts from its first paint on, as Lighthouse adds them up
COUNT_LAYOUT_SHIFTS = """
window.mcritShifted = 0;
new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) { window.mcritShifted += entry.value; }
    }
}).observe({type: "layout-shift", buffered: true});
"""

WAIT_FOR_THE_QUEUE = "() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })"


@pytest.mark.parametrize("path", ["/analyze/query", "/data/submit", "/data/import"])
def test_an_upload_page_fetches_dropzone_after_its_first_paint_and_attaches_it_once(app, live_url, make_user, path):
    """The drop zone is these pages' content, and Dropzone was a tag in the body: the
    first paint shared the network with its 77 KB. Queued now, it is requested after the
    paint, and dropzone_attach.js attaches the zone with the page's options before
    auto-discovery could attach it with none. The zone is styled as a drop zone from the
    first paint, so attaching it moves nothing."""
    sync_api = pytest.importorskip("playwright.sync_api")
    user_id = make_user("contributor")
    errors = []

    with sync_api.sync_playwright() as playwright:
        browser, page = open_samples_page(playwright, app, live_url, user_id)
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(COUNT_LAYOUT_SHIFTS)
            page.goto(live_url + path)
            page.evaluate(WAIT_FOR_THE_QUEUE)
            fetched_at, painted_at = page.evaluate(
                "() => [performance.getEntriesByType('resource').find((entry) => entry.name.endsWith('/static/dropzone.js')).startTime,"
                " performance.getEntriesByName('first-contentful-paint')[0].startTime]"
            )
            attached = page.evaluate("() => [Dropzone.instances.length, Dropzone.forElement('#myDropzone').options.maxFilesize, Dropzone.autoDiscover]")
            # two frames on, so that a shift the attach caused has been reported
            shifted = page.evaluate("() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(window.mcritShifted))))")
            if path == "/data/import":
                # the import page's own handlers, from its options' init
                page.evaluate("() => Dropzone.forElement('#myDropzone').emit('sending', {name: 'export.json'})")
                status = page.inner_text("#import-status-text")
            else:
                status = None
                assert page.evaluate("() => Dropzone.forElement('#myDropzone').options.autoProcessQueue") is False
        finally:
            browser.close()

    assert fetched_at > painted_at, f"dropzone.js was requested at {fetched_at:.0f} ms, before the first paint at {painted_at:.0f} ms"
    # one zone, with the page's 100,000,000 MB cap rather than Dropzone's default of 256
    assert attached == [1, 100000000, False], attached
    assert shifted == 0, f"the page shifted by {shifted}"
    assert status in (None, "Uploading export.json ..."), status
    assert errors == []


if __name__ == "__main__":
    unittest.main()
