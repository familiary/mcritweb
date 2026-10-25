#!/usr/bin/python
"""The upload form beside the drop zone, in a browser: what it does with what was typed.

The submit and query pages carry the form (`templates/table/submit_or_query_dropzone.html`,
driven by `static/upload_form.js`), and so does the drop overlay of every other page.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

SMDA_REPORT = '{\n "base_addr": 4194304,\n "bitness": 32,\n "metadata": {\n  "family": "dropfamily",\n  "version": "1.0"\n }\n}\n'

WAIT_FOR_THE_QUEUE = ("() => new Promise((resolve, reject) => { whenReady(() => resolve(true));"
                      " setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })")


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
def open_page(app, live_server, make_user):
    """open_page(path, role) -> a page on `path`, signed in, once its queued scripts have run."""
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open(path, role="contributor"):
                user_id = make_user(role=role)
                context = browser.new_context()
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": session_cookie_for(app, user_id),
                                      "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                page.errors = []
                page.on("pageerror", lambda error: page.errors.append(str(error)))
                page.goto(live_server + path)
                page.evaluate(WAIT_FOR_THE_QUEUE)
                return page
            yield _open
        finally:
            browser.close()


def add_file(page, name, content="MZ"):
    """Add a file to the zone the way a drop does, and wait for the pre-fill to be applied."""
    with page.expect_response("**/data/request_filename_info"):
        page.evaluate("([name, content]) => Dropzone.forElement('#myDropzone').addFile(new File([content], name))",
                      [name, content])
    # fill_form runs in the request's load handler, once the body has been read
    page.wait_for_timeout(200)


def test_adding_a_file_keeps_the_family_and_version_typed_first(open_page):
    """The page focuses the family field, so typing first is the natural order. The probe
    answers `{"dump": false}` for an ordinary filename, and the form wrote its missing
    family and version into the fields regardless, which emptied both."""
    page = open_page("/data/submit")
    page.fill("#family", "win.testfam")
    page.fill("#version", "1.0")
    add_file(page, "plain_sample.bin")

    assert [page.input_value("#family"), page.input_value("#version")] == ["win.testfam", "1.0"]
    assert page.errors == []


def test_a_report_still_fills_the_fields_and_the_next_file_takes_that_back(open_page):
    """What a file filled in belongs to that file: the next file that has no value of its
    own clears it, while a value the user typed over it stays."""
    page = open_page("/data/submit")
    add_file(page, "sample.smda", SMDA_REPORT)
    assert [page.input_value("#family"), page.input_value("#version")] == ["dropfamily", "1.0"]

    page.fill("#version", "2.0")
    add_file(page, "plain_sample.bin")
    assert [page.input_value("#family"), page.input_value("#version")] == ["", "2.0"]
    assert page.errors == []


def submit_requests(page):
    """Hold every upload to /data/submit, and return the list it is recorded in."""
    held = []
    page.route("**/data/submit", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    return held


def test_a_dump_without_its_bitness_and_address_is_held_back_in_the_browser(open_page):
    """The click handler sent the queue without the browser's own check of the fields, so a
    dump went up without the two fields it needs, and the server's refusal came back after
    the whole file had been sent."""
    page = open_page("/data/submit")
    held = submit_requests(page)
    page.fill("#family", "win.testfam")
    add_file(page, "plain_sample.bin")
    page.check("#dumped")
    page.click("#submit-dropzone")
    page.wait_for_timeout(300)

    assert held == [], "the upload was sent"
    assert page.evaluate("() => document.activeElement.id") in ("base_addr", "bitness32")
    assert page.evaluate("() => document.getElementById('base_addr').validity.valueMissing")
    assert page.evaluate("() => document.getElementById('bitness32').validity.valueMissing")
    page.fill("#base_addr", "not an address")
    assert page.evaluate("() => document.getElementById('base_addr').validity.patternMismatch")
    page.fill("#base_addr", "0x400000")
    assert page.evaluate("() => document.getElementById('base_addr').checkValidity()")
    assert page.input_value("#family") == "win.testfam"
    assert page.errors == []


def test_an_unmapped_file_does_not_need_the_dump_fields(open_page):
    page = open_page("/data/submit")
    page.check("#dumped")
    page.check("#unmapped")
    assert page.evaluate("() => document.getElementById('dropzone-additional-fields-form').checkValidity()")


def test_without_a_file_the_button_says_so(open_page):
    """It used to be disabled until a file was added, with nothing to say why."""
    page = open_page("/data/submit")
    held = submit_requests(page)
    assert page.is_enabled("#submit-dropzone")
    page.click("#submit-dropzone")

    assert page.inner_text("#upload-feedback") == "Choose a file first."
    assert page.evaluate("() => document.activeElement.classList.contains('dz-button')")
    assert held == []


def test_a_refused_upload_keeps_the_file_and_the_fields_and_can_go_again(open_page):
    """The page reloaded on any refusal to show the server's flash: the file was gone, the
    family and version empty, "Dumped" unchecked. The reason now appears beside the button,
    and the same file goes again with the next press."""
    page = open_page("/data/submit")
    held = submit_requests(page)
    page.fill("#family", "win.testfam")
    page.fill("#version", "1.0")
    add_file(page, "plain_sample.bin")
    page.check("#dumped")
    page.check("#bitness32")
    page.fill("#base_addr", "0x400000")

    page.click("#submit-dropzone")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1
    assert held[0].request.headers.get("x-requested-with") == "XMLHttpRequest"
    held[0].fulfill(status=400, content_type="text/plain; charset=utf-8", body="Refused for the test.")
    page.wait_for_function("() => document.getElementById('upload-feedback').textContent !== ''")

    assert page.inner_text("#upload-feedback") == "Refused for the test."
    assert [page.input_value("#family"), page.input_value("#version"), page.input_value("#base_addr")] == ["win.testfam", "1.0", "0x400000"]
    assert page.is_checked("#dumped") and page.is_checked("#bitness32")
    assert page.evaluate("() => Dropzone.forElement('#myDropzone').getQueuedFiles().length") == 1

    page.click("#submit-dropzone")
    for _ in range(100):
        if len(held) == 2:
            break
        page.wait_for_timeout(50)
    assert len(held) == 2, "the file was not sent again"
    assert b"plain_sample.bin" in (held[1].request.post_data_buffer or b"")
    held[1].fulfill(status=400, content_type="text/html", body="<html>gateway</html>")
    page.wait_for_function("() => document.getElementById('upload-feedback').textContent.indexOf('400') >= 0")
    assert "gateway" not in page.inner_text("#upload-feedback")
    assert page.errors == []
