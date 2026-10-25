#!/usr/bin/python
"""Chromium draws the stylesheets served without their comments as it draws the files.

A scanner that cut one character too many (a string, a url(), a rule after a comment) would
change a style that no test of the text sees. This loads pages twice, once with what the
server sends and once with the unstripped files put in its place, and compares the computed
style of every element, with its ::before and ::after, in the light and the dark theme.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import pathlib
import threading

import pytest
from conftest import session_cookie_for
from werkzeug.serving import make_server

from mcritweb import static_cache
from mcritweb.db import UserInfo

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

STATIC = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "static"
PAGES = ["/explore/samples", "/settings", "/analyze/query"]

#: every element of the page, named by its position, with all its computed properties, custom ones included, in name order
COMPUTED = """() => {
    const styles = new Map();
    const describe = (style) => Array.from(style).sort().map((name) => name + ":" + style.getPropertyValue(name)).join(";");
    document.querySelectorAll("*").forEach((element, index) => {
        if (["SCRIPT", "STYLE", "LINK", "META", "TITLE"].includes(element.tagName)) return;
        styles.set(index + ":" + element.tagName + "#" + element.id, [
            describe(getComputedStyle(element)),
            describe(getComputedStyle(element, "::before")),
            describe(getComputedStyle(element, "::after")),
        ].join("|"));
    });
    return Object.fromEntries(styles);
}"""


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


def computed_styles(browser, app, live_server, user_id, path, unstripped):
    """The computed style of the page's elements, in a context of its own so that nothing
    comes from a cache; with `unstripped`, the project's stylesheets are the files."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": session_cookie_for(app, user_id), "domain": "127.0.0.1", "path": "/"}])
        served = []
        if unstripped:
            for name in static_cache.STRIPPED_STYLESHEETS:
                context.route(f"**/static/{name}?*", lambda route, request, name=name: route.fulfill(status=200, content_type="text/css", body=(STATIC / name).read_bytes()))
        page = context.new_page()
        page.on("response", lambda response: served.append(response.url.split("?")[0].rsplit("/", 1)[-1]))
        page.goto(f"{live_server}{path}")
        page.wait_for_function("() => window.bootstrap !== undefined")
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(500)
        return page.evaluate(COMPUTED), served
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("path", PAGES)
def test_the_stripped_stylesheets_compute_the_same_style(app, live_server, make_user, path, theme):
    user_id = make_user(role="contributor")
    with app.app_context():
        user = UserInfo.fromDb(user_id=user_id)
        user.theme = theme
        user.saveToDb()
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            stripped, served = computed_styles(browser, app, live_server, user_id, path, unstripped=False)
            full, _ = computed_styles(browser, app, live_server, user_id, path, unstripped=True)
        finally:
            browser.close()
    assert {"style.css", "navbar.css"} <= set(served) and (theme == "light" or "theme-dark.css" in served), served
    assert len(stripped) > 50
    assert stripped.keys() == full.keys()
    different = [key for key in stripped if stripped[key] != full[key]]
    assert different == [], [(key, [pair for pair in zip(stripped[key].split(";"), full[key].split(";")) if pair[0] != pair[1]][:6]) for key in different[:3]]
