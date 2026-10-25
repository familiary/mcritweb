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
