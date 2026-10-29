#!/usr/bin/python
"""The upload pages' drop zone, in a browser: how wide it is does not depend on Dropzone.

The zone sits in a centred flex column, and a flex item there is as wide as its content
unless it is told otherwise. Its content is Dropzone's message, which Dropzone puts in
when it attaches, so the zone was 44px wide until then and 239px after, short of the
600px it is allowed. Measured with Dropzone's script held back and with it in, at a
desktop and at a phone width.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import threading

import pytest
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

ZONE_WIDTH = "() => document.getElementById('myDropzone').getBoundingClientRect().width"
WAIT_FOR_THE_QUEUE = "() => new Promise((resolve, reject) => { whenReady(() => resolve(true)); setTimeout(() => reject(new Error('the queued scripts did not run')), 10000); })"


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


@pytest.mark.parametrize("viewport_width, expected", [(1280, 600), (390, None)])
@pytest.mark.parametrize("path", ["/analyze/query", "/data/submit", "/data/import"])
def test_the_drop_zone_is_as_wide_before_dropzone_has_filled_it_as_after(app, live_server, make_user, path, viewport_width, expected):
    user_id = make_user(role="contributor")
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": user_id})
    widths = {}
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            for held_back in (True, False):
                context = browser.new_context(viewport={"width": viewport_width, "height": 900})
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                if held_back:
                    # the pages load the minified build, and a versioned URL ends in a query
                    page.route("**/static/dropzone-patched.min.js*", lambda route: route.abort())
                page.goto(live_server + path)
                if held_back:
                    # once the queue has run without it, nothing has filled the zone
                    page.evaluate(WAIT_FOR_THE_QUEUE)
                    assert page.evaluate("() => typeof Dropzone === 'undefined' && !document.querySelector('#myDropzone .dz-message')"), f"{path} has Dropzone although its script was held back"
                else:
                    page.wait_for_selector("#myDropzone .dz-message")
                widths[held_back] = page.evaluate(ZONE_WIDTH)
                column = page.evaluate("() => document.getElementById('myDropzone').parentElement.getBoundingClientRect().width")
                context.close()
        finally:
            browser.close()

    assert widths[True] == widths[False], f"{path} is {widths[True]:.0f}px wide before Dropzone has filled it and {widths[False]:.0f}px after"
    assert widths[False] == (expected if expected is not None else column), f"{path} is {widths[False]:.0f}px wide in a {column:.0f}px column"
