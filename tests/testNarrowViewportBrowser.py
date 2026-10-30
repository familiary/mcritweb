#!/usr/bin/python
"""What a phone or tablet does not download, in a browser.

The Fraunhofer logo in the navbar is `d-none d-lg-block`: below 992px it has no box. It was
requested on every page view there all the same, at low priority but among the first
requests, 1.7 KB in the light theme and 2.8 KB in the dark. base.html loads it lazily now,
so the browser asks for it only at the widths that show it. testPageAssets.py checks the
markup in CI; this checks what Chromium makes of it.

The icon font's preload is the same story on the pages whose solid icons are all in the
navbar, which collapses below 992px: there it is preloaded only from 992px up
(testIconPreload.py).

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LOGO = "fkie_190x52.gif"
FONT = "fa-solid-900.woff2"
#: the pages that draw solid icons in the navbar only (icons_only_in_the_navbar)
NAVBAR_ICONS_ONLY = ["/help", "/analyze/query", "/data/submit", "/data/import", "/data/export"]
#: what the page loaded of the solid face, and how its request was started
SOLID_FACE = """() => ({
    loaded: [...document.fonts].some((face) => face.status === "loaded" && face.weight === "900"),
    initiators: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("fa-solid-900")).map((entry) => entry.initiatorType),
})"""


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
def open_at(app, live_server, make_user):
    """open_at(width, path): the page at that viewport width, signed in as a contributor
    (the upload pages need one), and the static files it asked for once its queued scripts
    have run."""
    cookie = session_cookie_for(app, make_user(role="contributor"))
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            def _open_at(width, path):
                context = browser.new_context(viewport={"width": width, "height": 844})
                context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                requested = []
                page.on("request", lambda request: requested.append(request.url.split("?")[0].rsplit("/", 1)[-1]))
                page.goto(live_server + path)
                page.wait_for_function("() => window.bootstrap !== undefined")
                page.wait_for_timeout(500)
                return page, requested
            yield _open_at
        finally:
            browser.close()


@pytest.mark.parametrize("width", [390, 820])
def test_a_narrow_page_does_not_download_the_hidden_logo(open_at, width):
    page, requested = open_at(width, "/explore/samples")
    assert page.evaluate("document.querySelector('.right-logo img').getBoundingClientRect().width") == 0
    assert LOGO not in requested, requested


@pytest.mark.parametrize("width", [1100, 1350])
def test_a_wide_page_shows_the_logo(open_at, width):
    page, requested = open_at(width, "/explore/samples")
    assert LOGO in requested
    page.wait_for_function("() => document.querySelector('.right-logo img').complete")
    assert page.evaluate("document.querySelector('.right-logo img').naturalWidth") == 187


@pytest.mark.parametrize("path", NAVBAR_ICONS_ONLY)
def test_a_phone_does_not_fetch_the_icon_font_for_a_menu_it_has_not_opened(open_at, path):
    page, requested = open_at(390, path)
    page.evaluate("() => document.fonts.ready")
    assert FONT not in requested, requested
    assert page.evaluate(SOLID_FACE) == {"loaded": False, "initiators": []}


@pytest.mark.parametrize("path", NAVBAR_ICONS_ONLY)
def test_a_desktop_still_preloads_the_font_for_the_navbar_s_help_icon(open_at, path):
    page, requested = open_at(1350, path)
    page.evaluate("() => document.fonts.ready")
    assert page.evaluate(SOLID_FACE) == {"loaded": True, "initiators": ["link"]}


@pytest.mark.parametrize("path", ["/explore/samples", "/data/jobs"])
def test_a_phone_still_preloads_the_font_on_a_page_with_icons_in_its_content(open_at, path):
    page, requested = open_at(390, path)
    page.evaluate("() => document.fonts.ready")
    assert page.evaluate(SOLID_FACE) == {"loaded": True, "initiators": ["link"]}


def test_the_font_arrives_when_the_menu_is_opened(open_at):
    """The other side of not preloading it: the navbar's icons are drawn once the font is
    there, which is a request after the menu opens."""
    page, requested = open_at(390, "/data/export")
    page.click("button.navbar-toggler")
    page.wait_for_function("() => [...document.fonts].some((face) => face.status === 'loaded' && face.weight === '900')")
    assert requested.count(FONT) == 1
