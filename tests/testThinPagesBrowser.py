#!/usr/bin/python
"""A thinned page is drawn as the whole page is, in Chromium.

testThinPages.py shows that the thinned page has the same tags, attributes and text, white
space read as a browser reads it. This looks at the pages as the browser lays them out: the
box of every element and the text on show are the same with the pass off and on, and no
page logs an error from a script it thinned.

`playwright` is not a dependency of this project and CI does not install it, so this module
skips there rather than failing.
"""

import os
import threading

import pytest
from conftest import session_cookie_for
from fixtureData import job_id_of
from werkzeug.serving import make_server

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

TIMEOUT_MS = 20000

#: resolves once base.html's queue has run (ADR-0021)
QUEUE_HAS_RUN = "() => new Promise(resolve => whenReady(() => resolve(true)))"

#: the text on show, and where each element is and how big
LAYOUT = """() => ({
    title: document.title,
    text: document.body.innerText,
    boxes: Array.from(document.querySelectorAll("body *")).map(element => {
        const box = element.getBoundingClientRect();
        return [element.tagName, element.id, ...[box.x, box.y, box.width, box.height].map(value => Math.round(value * 10) / 10)];
    }),
})"""

PAGES = [
    f"/data/result/{job_id_of('matches_for_sample')}",
    f"/data/result/{job_id_of('matches_for_sample')}?famid=1",
    f"/data/result/{job_id_of('cross_compare')}",
    f"/data/result/{job_id_of('unique_blocks')}",
    f"/data/linkhunt/{job_id_of('matches_for_sample')}",
    "/explore/samples",
    "/explore/functions/84",
    "/data/matches/function/84/943",
    "/data/jobs?active=getMatchesForSample",
    "/settings",
    "/help",
    "/analyze/compare",
    "/data/submit",
]


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
def page(app, live_server, make_user):
    user_id = make_user(role="contributor")
    cookie = session_cookie_for(app, user_id)
    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": app.config["SESSION_COOKIE_NAME"], "value": cookie,
                                  "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.errors = []
            page.on("pageerror", lambda error: page.errors.append(str(error)))
            page.on("console", lambda message: page.errors.append(message.text) if message.type == "error" else None)
            yield page
        finally:
            browser.close()


def looked_at(page, url):
    page.goto(url)
    page.evaluate(QUEUE_HAS_RUN)
    page.evaluate("document.fonts.ready")
    return page.evaluate(LAYOUT)


@pytest.mark.parametrize("path", PAGES)
def test_a_thinned_page_is_laid_out_as_the_whole_page_is(app, page, live_server, path):
    looked_at(page, live_server + path)
    thinned = looked_at(page, live_server + path)
    app.config["MCRITWEB_THIN_PAGES"] = False
    whole = looked_at(page, live_server + path)

    assert thinned["title"] == whole["title"]
    assert thinned["text"] == whole["text"]
    assert len(thinned["boxes"]) == len(whole["boxes"]) > 20
    moved = [(a, b) for a, b in zip(thinned["boxes"], whole["boxes"]) if a != b]
    assert moved == [], moved[:3]
    assert page.errors == []
