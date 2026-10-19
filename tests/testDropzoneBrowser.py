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

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SMDA_REPORT = '{\n "base_addr": 4194304,\n "bitness": 32,\n "metadata": {\n  "family": "dropfamily",\n  "version": "1.0"\n }\n}\n'


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def session_cookie_value(app, user_id):
    from flask.sessions import SecureCookieSessionInterface

    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    return serializer.dumps({"user_id": user_id})


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
            page.wait_for_load_state("load")
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
            page.wait_for_load_state("load")
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


if __name__ == "__main__":
    unittest.main()
