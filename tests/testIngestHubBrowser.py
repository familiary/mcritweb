#!/usr/bin/python
"""The ingest page's three zones, driven in a browser.

The forms are Dropzone's: a drop fills the zone, the Submit button posts the file with
the form's fields, the import zone posts at once with the CSRF header Flask-Dropzone adds
through dropzone.config, and the status script moves on to the import report. None of that
is visible to a test that reads the HTML as text, which is how the first version shipped a
query tab whose submit ended in a 403 for visitors and an import tab whose drop was refused
with a 400 and showed nothing. CSRF protection is on in this module, so the import's header
is what makes the request pass.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import json
import threading

import pytest
from conftest import FAKE_JOB_ID, FAKE_MINHASH_HASH, FAKE_SHINGLER_HASH
from werkzeug.serving import make_server

from mcritweb.csrf import ENABLED_KEY

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

EXPORT = {
    "config": {"version": "1.5.3", "shingler": FAKE_SHINGLER_HASH, "minhash": FAKE_MINHASH_HASH},
    "content": {"is_compressed": False, "num_samples": 1, "num_functions": 1, "num_families": 1},
    "family_mapping": {"1": "test.family"},
    "sample_entries": {"ab" * 32: {"sample_id": 1, "family_id": 1}},
    "function_entries": {"ab" * 32: {"1": {"function_id": 1, "sample_id": 1}}},
}


@pytest.fixture
def app(app):
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
def open_as(app, live_server, make_user):
    """A page, signed in as a role, handed to `use` with the errors the page threw and
    the requests it made."""

    def _open(role, path, use):
        user_id = make_user(role=role)
        cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except sync_api.Error as error:
                pytest.skip(f"no Chromium for playwright to drive: {error}")
            try:
                context = browser.new_context()
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request))
                page.goto(live_server + path)
                page.wait_for_load_state("networkidle")
                return use(page, errors, requests)
            finally:
                browser.close()

    return _open


def csrf_header(request):
    """The CSRF token a request carried, under either name csrf.py accepts: Flask-Dropzone
    sends X-CSRF-Token, the project's own scripts X-CSRFToken."""
    headers = {name.lower(): value for name, value in request.all_headers().items()}
    return headers.get("x-csrftoken") or headers.get("x-csrf-token")


def drop(page, name, content):
    """What a drop on the zone does: Dropzone reads the file from its hidden input."""
    page.wait_for_selector("#myDropzone .dz-message")
    page.set_input_files("input.dz-hidden-input", {"name": name, "mimeType": "application/octet-stream", "buffer": content})


@pytest.mark.parametrize("role, tab", [("visitor", "query"), ("contributor", "query"), ("contributor", "submit"), ("visitor", "submit"),
                                       ("contributor", "import"), ("visitor", "import")])
def test_no_tab_throws_a_script_error(open_as, role, tab):
    """The upload macro's head script was loaded on the tabs that show a visitor a notice
    instead of the zone, and threw "Dropzone is not defined" on every load."""
    errors = open_as(role, f"/data/ingest/{tab}", lambda page, errors, requests: list(errors))
    assert errors == []


def test_a_visitor_drops_a_file_on_the_query_tab_and_lands_on_the_job(open_as):
    """The form posted to data.submit_or_query, a contributor route, so the visitor's
    submit was a 403 and nothing happened. It posts to analyze.query."""

    def use(page, errors, requests):
        drop(page, "query.exe", b"MZquery")
        page.wait_for_selector("#submit-dropzone:not([disabled])")
        page.click("#submit-dropzone")
        page.wait_for_url(f"**/data/jobs/{FAKE_JOB_ID}**")
        # the macro asks data.request_filename_info about every dropped file, on the query
        # page too; the post that matters is the form's
        posts = [(request.url, csrf_header(request)) for request in requests
                 if request.method == "POST" and "request_filename_info" not in request.url]
        return page.url, posts, list(errors)

    url, posts, errors = open_as("visitor", "/data/ingest", use)
    assert f"/data/jobs/{FAKE_JOB_ID}" in url
    assert [url_posted.rsplit("/", 2)[-2:] for url_posted, _ in posts] == [["analyze", "query"]]
    assert all(token for _, token in posts)
    assert errors == []


def test_a_contributor_drops_a_file_on_the_submit_tab_and_lands_on_the_job(open_as):
    def use(page, errors, requests):
        drop(page, "sample.exe", b"MZsample")
        page.wait_for_selector("#submit-dropzone:not([disabled])")
        page.fill("#family", "win.family")
        page.click("#submit-dropzone")
        page.wait_for_url(f"**/data/jobs/{FAKE_JOB_ID}**")
        posts = [request.url for request in requests if request.method == "POST" and "request_filename_info" not in request.url]
        return page.url, posts, list(errors)

    url, posts, errors = open_as("contributor", "/data/ingest/submit", use)
    assert f"/data/jobs/{FAKE_JOB_ID}" in url
    assert [url_posted.rsplit("/", 2)[-2:] for url_posted in posts] == [["data", "submit"]]
    assert errors == []


def test_a_contributor_drops_an_export_on_the_import_tab_and_reaches_the_report(open_as):
    """The zone was rendered without dropzone.config: the upload went out without the
    CSRF header, was refused with a 400, and nothing showed or moved on. With the
    import page's config and script the header is there, the status box says what is
    happening, and the page moves on to the import report."""

    def use(page, errors, requests):
        shown = []
        page.expose_function("statusShown", lambda text: shown.append(text))
        page.evaluate("""() => new MutationObserver(() => {
            const box = document.getElementById('import-status');
            if (box && box.style.display !== 'none') { window.statusShown(box.textContent.trim()); }
        }).observe(document.getElementById('import-status'), {attributes: true, childList: true, subtree: true})""")
        drop(page, "export.json", json.dumps(EXPORT).encode())
        page.wait_for_url("**/data/import_complete")
        posts = [(request.url, csrf_header(request)) for request in requests if request.method == "POST"]
        return page.url, posts, page.content(), shown, list(errors)

    url, posts, content, shown, errors = open_as("contributor", "/data/ingest/import", use)
    assert url.endswith("/data/import_complete")
    assert shown and any("Uploading" in text or "Upload" in text for text in shown), shown
    assert len(posts) == 1 and posts[0][0].endswith("/data/import") and posts[0][1]
    assert "Import completed" in content or "num_samples" in content
    assert errors == []
