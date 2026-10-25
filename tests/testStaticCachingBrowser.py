#!/usr/bin/python
"""The browser keeps the static files from one page to the next, in a browser.

Every /static/ file used to come with `Cache-Control: no-cache`, so the next page asked for
each of them again and waited a round trip for every 304, five stylesheets among them
before its first paint. mcritweb/static_cache.py versions the URLs and lets the browser keep
the files (testStaticCaching.py checks the headers); this checks that Chromium does, and
that no file is fetched twice because a page and a static file name it by different URLs.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

#: what the resource timing of the page says about each static file: its name and the
#: bytes that came over the network for it, 0 for one served from the browser's cache
STATIC_RESOURCES = """() => performance.getEntriesByType("resource")
    .filter((entry) => entry.name.includes("/static/"))
    .map((entry) => [entry.name.split("/static/")[1].split("?")[0], entry.transferSize])"""


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
def browser_page(app, live_server, make_user):
    user_id = make_user(role="contributor")
    cookie = session_cookie_for(app, user_id)
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.requested = []
            page.on("request", lambda request: page.requested.append(request.url) if "/static/" in request.url else None)
            page.warnings = []
            page.on("console", lambda message: page.warnings.append(message.text) if message.type in ("warning", "error") else None)
            yield page
        finally:
            browser.close()


def settled(page, url):
    """The page with its queued scripts run and its icon font in."""
    page.goto(url)
    page.wait_for_function("() => window.bootstrap !== undefined")
    page.evaluate("() => document.fonts.ready")
    return dict(page.evaluate(STATIC_RESOURCES))


def test_the_next_page_takes_its_static_files_from_the_cache(browser_page, live_server):
    first = settled(browser_page, f"{live_server}/explore/families")
    second = settled(browser_page, f"{live_server}/explore/samples")

    shared = sorted(set(first) & set(second))
    # the stylesheets, the font, the logos and the queued scripts
    assert len(shared) >= 12, shared
    assert "style.css" in shared and "webfonts/subset/fa-solid-900.woff2" in shared, shared
    fetched = [name for name in shared if second[name] != 0]
    assert fetched == [], f"/explore/samples asked the server again for {fetched}"


def test_the_preloaded_font_is_fetched_once(browser_page, live_server):
    """The preload and the stylesheet's @font-face have to ask for the same URL; a preload
    with a version would be a second download and a console warning that it went unused."""
    settled(browser_page, f"{live_server}/explore/samples")
    browser_page.wait_for_timeout(3500)
    fonts = [url for url in browser_page.requested if "fa-solid-900" in url]
    assert len(fonts) == 1, fonts
    assert [text for text in browser_page.warnings if "preload" in text] == []


def test_the_layout_worker_takes_dagre_from_the_request_the_page_made(browser_page, live_server):
    """The CFG page queues dagre-d3 and the layout worker imports it by a relative URL; with
    a version on the page's request and none on the worker's, the 49 KB came twice."""
    browser_page.goto(f"{live_server}/explore/functions/84")
    browser_page.evaluate("document.getElementById('xcfg_top').scrollIntoView()")
    browser_page.wait_for_selector("#graphContainer g.node", state="attached")
    browser_page.wait_for_function("!document.getElementById('showCycles').disabled")
    assert any("layout_worker.js" in url for url in browser_page.requested), "the graph was not laid out in the worker"
    dagre = [url for url in browser_page.requested if "dagre-d3.min.js" in url]
    assert len(dagre) >= 2 and len(set(dagre)) == 1, dagre
