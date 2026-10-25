#!/usr/bin/python
"""A browser keeps and sends back the `__Host-session` cookie, signed in through the form.

testAppConfig.py pins the name and the attributes the prefix demands. Whether a browser
then accepts the cookie is the part that decides whether anyone can log in, and only a
browser answers it: one that refuses a `__Host-` cookie drops it without a word and the
user lands on the login page again. The app here is built as a deployment's is, so its
cookie gets the name create_app gives it; the shared `app` fixture keeps the plain name
for the other browser tests (see conftest.py). Chromium treats the loopback address as a
secure origin, so the Secure cookie travels over the test server's plain HTTP as it
would over a deployment's HTTPS.

`playwright` is not a dependency of this project and CI does not install it, so this
module skips there rather than failing.
"""

import os
import threading

import pytest
from fixtureData import CorpusMcritClient
from testAppConfig import build_app
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from mcritweb.db import UserInfo

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

PASSWORD = "a long enough password"


@pytest.fixture
def live_server(tmp_path):
    corpus = CorpusMcritClient()
    app = build_app(tmp_path, MCRIT_CLIENT_FACTORY=lambda **kwargs: corpus.raw_variant() if kwargs.get("raw_responses") else corpus)
    assert app.config["SESSION_COOKIE_NAME"] == "__Host-session"
    with app.app_context():
        user = UserInfo()
        user.username = "cookieuser"
        user.password = generate_password_hash(PASSWORD)
        user.role = "visitor"
        user.saveToDb()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def test_a_user_who_signs_in_stays_signed_in(live_server):
    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(live_server + "/login")
            page.fill("input[name=username]", "cookieuser")
            page.fill("input[name=inputPassword]", PASSWORD)
            with page.expect_navigation():
                page.click("button[type=submit]")

            cookies = {cookie["name"]: cookie for cookie in context.cookies()}
            assert "__Host-session" in cookies, f"the browser kept {sorted(cookies)}"
            assert cookies["__Host-session"]["secure"] and cookies["__Host-session"]["path"] == "/"
            page.goto(live_server + "/settings")
            assert page.url == live_server + "/settings", "the next page sent the user to log in again"
            assert "cookieuser" in page.content()
        finally:
            browser.close()
