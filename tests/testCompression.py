#!/usr/bin/python
"""gzip for the deployments that have no compressing proxy (issue #63)."""

import gzip
import json
import logging
import pathlib
import re
import unittest

import pytest

from mcritweb import compression

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_a_page_is_gzipped(client, as_role):
    """A page reflects input (the search term) next to the CSRF token, which BREACH
    could read out of a compressed response if the token were the same in each one.
    It is masked afresh on every render (testCsrf.py), so the page is compressed."""
    as_role("visitor")
    page = client.get("/explore/samples?query=abc", headers={"Accept-Encoding": "gzip, deflate"})
    assert page.status_code == 200 and page.mimetype == "text/html"
    assert page.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in page.headers.get("Vary", "")
    assert b"abc" in gzip.decompress(page.get_data())


def test_the_settings_page_is_not_gzipped(client, as_role):
    """It shows the user's API token, a secret that is the same in every response."""
    as_role("contributor")
    page = client.get("/settings", headers={"Accept-Encoding": "gzip, deflate"})
    assert page.status_code == 200 and page.mimetype == "text/html"
    assert page.headers.get("Content-Encoding") is None
    assert b"apitoken" in page.get_data()


#: every view that renders a template printing a stored token, and those templates
SECRET_VIEWS = {"mcritweb.views.authentication.settings": "settings.html",
                "mcritweb.views.administration.server": "admin_server.html",
                "mcritweb.views.administration.change_server": "admin_server.html"}
#: names with "token" in them that a template uses and that are not stored secrets: the
#: visitor's own ?token= input, a registration token proposed afresh on every render, and
#: whether one is asked for, and the form field's own name, which the form passes to the
#: macros that mark it invalid. Its value is only what the visitor typed or ?token= carried.
NOT_SECRETS = {"register.html": {"query_token", "proposed_registration_token", "is_registration_token_required",
                                 "registrationToken"},
               # #254: who still holds a legacy token, and the button that rotates them
               "users.html": {"legacy_token_user_ids", "admin.regenerate_legacy_apitokens"}}
#: a {{ }} or {% %} block, wherever a token can be printed from: directly, through a macro,
#: a subscript or a variable set first. What it finds are names with "token" in them; the
#: views hand templates the user and server records, whose fields are printed by name.
TEMPLATE_BLOCK = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
TOKEN_NAME = re.compile(r"[\w.]*token[\w.]*", re.IGNORECASE)


def token_names(template):
    """The names with "token" in them that a template's blocks use. A quoted string with a
    space in it is text, such as the "<your API token>" of a curl example, not a name."""
    names = set()
    for block in TEMPLATE_BLOCK.findall(template):
        block = STRING.sub(lambda string: "" if re.search(r"\s", string.group()) else string.group(), block)
        names.update(TOKEN_NAME.findall(block))
    return names


def test_the_token_scan_finds_a_token_however_it_is_printed():
    for printed in ("{{user_info.apitoken}}", "{{ clipboard_btn(user_info.apitoken) }}", "{{ s['token'] }}",
                    "{{ (s.token) }}", "{{- s.token }}", "{{ '' ~ s.token }}", "{{ s.Token }}",
                    "{% set t = s.token %}{{ t }}", "{{ f('a', s.token, 'b c') }}", "{{ 'it\\'s' ~ s.token ~ 'x y' }}"):
        assert token_names(printed), printed
    assert token_names("{{ clipboard_btn('curl -H \"apitoken: <your API token>\" ' + api_path) }}") == set()


def test_the_pages_that_print_a_token_are_marked_as_showing_a_secret(app):
    """A page that shows a secret other than the CSRF token has to be marked. `app`
    imports the views, which is when they mark themselves."""
    assert compression._UNCOMPRESSED_VIEWS == set(SECRET_VIEWS)
    templates = pathlib.Path(__file__).resolve().parent.parent / "mcritweb" / "templates"
    printing = {}
    for path in templates.rglob("*.html"):
        # csrf_token() is masked afresh on every render, so it is no such secret
        names = token_names(path.read_text()) - {"csrf_token"} - NOT_SECRETS.get(path.name, set())
        if names:
            printing[str(path.relative_to(templates))] = names
    assert set(printing) == set(SECRET_VIEWS.values()), printing


@pytest.mark.parametrize("path", ["/admin/server"])
def test_the_server_page_is_not_gzipped(client, as_role, path):
    """It shows the registration token and the backend's token."""
    as_role("admin")
    page = client.get(path, headers={"Accept-Encoding": "gzip, deflate"})
    assert page.status_code == 200 and page.mimetype == "text/html"
    assert page.headers.get("Content-Encoding") is None


def test_json_is_gzipped_for_a_client_that_accepts_it(app):
    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip, deflate"}):
        body = json.dumps({"rows": list(range(1000))})
        response = compression.compress(app.response_class(body, mimetype="application/json"))
        assert response.headers["Content-Encoding"] == "gzip"
        assert "Accept-Encoding" in response.headers.get("Vary", "")
        assert int(response.headers["Content-Length"]) == len(response.get_data()) < len(body)
        assert gzip.decompress(response.get_data()).decode() == body


@pytest.mark.parametrize("accept_encoding", ["gzip;q=0", "identity", "deflate, br"])
def test_nothing_is_gzipped_for_a_client_that_refuses_it(client, accept_encoding):
    response = client.get("/static/style.css", headers={"Accept-Encoding": accept_encoding})
    assert response.status_code == 200 and response.headers.get("Content-Encoding") is None


def test_static_files_are_gzipped_too(client):
    response = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.data).startswith(client.get("/static/style.css").data[:64])
    assert response.headers.get("ETag", "").endswith('-gzip"')


def test_small_and_binary_responses_are_left_alone(client):
    tiny = client.get("/static/navbar.css", headers={"Accept-Encoding": "gzip"})
    assert tiny.status_code == 200 and tiny.headers.get("Content-Encoding") is None, "262 bytes are not worth a gzip header"
    image = client.get("/static/malpedia_small.png", headers={"Accept-Encoding": "gzip"})
    assert image.status_code == 200 and image.headers.get("Content-Encoding") is None


def test_a_conditional_request_still_gets_its_304(client):
    """send_file compares If-None-Match before the ETag is renamed, so a client that
    revalidates its gzipped copy has to be answered here."""
    first = client.get("/static/style.css", headers={"Accept-Encoding": "gzip"})
    again = client.get("/static/style.css", headers={
        "Accept-Encoding": "gzip",
        "If-None-Match": first.headers["ETag"],
        "If-Modified-Since": first.headers["Last-Modified"],
    })
    assert again.status_code == 304
    assert again.data == b""
    assert again.headers["ETag"] == first.headers["ETag"]
    changed = client.get("/static/style.css", headers={"Accept-Encoding": "gzip", "If-None-Match": '"something-else-gzip"'})
    assert changed.status_code == 200 and changed.headers["Content-Encoding"] == "gzip"


def test_a_plain_etag_still_gets_its_304(client):
    first = client.get("/static/style.css")
    again = client.get("/static/style.css", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def big_json(size):
    return json.dumps({"x": "a" * size})


def test_a_download_is_left_alone(app):
    with app.test_request_context("/data/export", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(4096), mimetype="application/json", headers={"Content-Disposition": "attachment; filename=export_samples.json"})
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_the_api_passthrough_is_left_alone(app):
    with app.test_request_context("/api/version", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(4096), mimetype="text/plain")
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_a_body_of_unknown_length_is_not_read(app):
    read = []

    def pieces():
        read.append(True)
        yield big_json(4096).encode()

    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(pieces(), mimetype="application/json")
        assert compression.compress(response).headers.get("Content-Encoding") is None
    assert read == []


def test_a_body_above_the_cap_is_left_alone(app):
    with app.test_request_context("/explore/samples", headers={"Accept-Encoding": "gzip"}):
        response = app.response_class(big_json(compression.MAX_SIZE), mimetype="application/json")
        assert compression.compress(response).headers.get("Content-Encoding") is None


def test_the_cap_covers_every_compressible_asset():
    static = pathlib.Path(compression.__file__).parent / "static"
    largest = max(path.stat().st_size for path in static.rglob("*") if path.suffix in (".js", ".css", ".svg", ".json"))
    assert largest <= compression.MAX_SIZE


def test_it_can_be_switched_off():
    """The switch is read once, when the app is built - a proxy that compresses is a
    deployment property, not something that changes per request."""
    from flask import Flask
    off = Flask("off")
    off.config["MCRITWEB_COMPRESS_RESPONSES"] = False
    compression.register(off)
    assert compression.compress not in off.after_request_funcs.get(None, [])
    on = Flask("on")
    compression.register(on)
    assert compression.compress in on.after_request_funcs.get(None, [])


def test_an_already_encoded_body_is_not_encoded_twice(app):
    from flask import Response
    with app.test_request_context("/", headers={"Accept-Encoding": "gzip"}):
        response = Response(b"x" * 4096, mimetype="text/plain", headers={"Content-Encoding": "br"})
        assert compression.compress(response) is response
        assert response.headers["Content-Encoding"] == "br"


if __name__ == "__main__":
    unittest.main()


def test_the_registration_form_does_not_print_the_stored_registration_token(app, client):
    """The form keeps what was typed after a failed attempt. The token field has to hold
    only that, never the token the server stores, or anyone could read it off /register."""
    from werkzeug.security import generate_password_hash

    from mcritweb.db import ServerInfo, UserInfo
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = "stored-registration-secret"
        server_info.saveToDb()
        user_info = UserInfo()
        user_info.username = "alice"
        user_info.password = generate_password_hash("pw-12345678")
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-alice"
        user_info.saveToDb()

    fresh = client.get("/register").get_data(as_text=True)
    typed = client.post("/register", data={
        "username": "bob", "inputPassword1": "pw-12345678", "inputPassword2": "pw-12345678",
        "registrationToken": "a-wrong-guess"}).get_data(as_text=True)

    assert 'name="registrationToken"' in fresh
    assert "stored-registration-secret" not in fresh
    assert "stored-registration-secret" not in typed
    assert 'value="a-wrong-guess"' in typed
