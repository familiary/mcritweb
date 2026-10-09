#!/usr/bin/python
"""CSRF protection - issue #83.

Issue #84 made every writing route POST-only, which stops a prefetch or an `<img>`
tag from firing a write. It does not stop a form on an attacker's page from posting
to us with the victim's session cookie attached, because the browser attaches that
cookie to any request it makes. These tests cover what does: a token bound to the
session, required on every unsafe method.

The rest of the suite runs with `WTF_CSRF_ENABLED` off, so that a 400 from a missing
token can never be mistaken for the 403 an authorization test is asserting. This
module switches it back on, which is why the `app` fixture is overridden here.
"""

import logging
import os
import re

import pytest

from mcritweb.csrf import ENABLED_KEY, FIELD_NAME

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: login_required, POST-only, and needs no request body - so a rejection is always
#: about the token and never about what was or was not submitted alongside it.
TARGET = "/admin/reset_column_settings"

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
TEMPLATE_ROOT = os.path.join(PACKAGE_ROOT, "templates")
STATIC_ROOT = os.path.join(PACKAGE_ROOT, "static")

FORM_TAG = re.compile(r"<form[^>]*\bmethod\s*=\s*['\"]post['\"]", re.IGNORECASE)


@pytest.fixture
def app(app):
    """The standard test app with protection switched back on."""
    app.config[ENABLED_KEY] = True
    return app


def session_token(client):
    """The token this client's session will be checked against."""
    with client.session_transaction() as test_session:
        return test_session[FIELD_NAME]


def prime_session(client):
    """Fetch a page so the session exists and has a token, as a browser would."""
    client.get("/")
    return session_token(client)


# --- the check itself ------------------------------------------------------------

def test_a_post_without_a_token_is_rejected(client, as_role):
    as_role("admin")
    assert client.post(TARGET).status_code == 400


def test_a_post_with_the_session_token_is_accepted(client, as_role):
    as_role("admin")
    token = prime_session(client)
    response = client.post(TARGET, data={FIELD_NAME: token})
    assert response.status_code == 302, "a valid token should reach the view and redirect"


def test_a_token_from_another_session_is_rejected(client, as_role):
    """The whole point: an attacker can serve a token, just not *this* one."""
    as_role("admin")
    prime_session(client)
    assert client.post(TARGET, data={FIELD_NAME: "a" * 64}).status_code == 400


def test_an_empty_token_is_rejected(client, as_role):
    as_role("admin")
    prime_session(client)
    assert client.post(TARGET, data={FIELD_NAME: ""}).status_code == 400


def test_a_session_without_a_token_cannot_be_talked_into_one(client, as_role):
    """A caller that has never been issued a token cannot supply one either.

    Without this, submitting any value against an absent session entry would pass
    on `None == None`, and every forged request would succeed on a fresh session.
    """
    as_role("admin")
    assert client.post(TARGET, data={FIELD_NAME: "b" * 64}).status_code == 400


@pytest.mark.parametrize("header", ["X-CSRFToken", "X-CSRF-Token"])
def test_the_token_is_accepted_as_a_header(client, as_role, header):
    """How the two dropzones and the filename-info XHR send it - they post JSON or a
    file body, neither of which carries a form field. Flask-Dropzone spells the
    header `X-CSRF-Token`; the hand-written XHR uses flask-wtf's `X-CSRFToken`."""
    as_role("admin")
    token = prime_session(client)
    assert client.post(TARGET, headers={header: token}).status_code == 302


def test_a_get_needs_no_token(client, as_role):
    as_role("admin")
    assert client.get("/settings").status_code == 200


def test_the_token_is_stable_across_requests(client, as_role):
    """A token that changed per request would invalidate any page already open."""
    as_role("admin")
    first = prime_session(client)
    client.get("/settings")
    assert session_token(client) == first


def test_a_post_to_an_unrouted_path_still_gives_a_404(client, as_role):
    """A missing token must not turn a 404 into a confusing 400."""
    as_role("admin")
    assert client.post("/no/such/path").status_code == 404


def test_the_api_blueprint_is_exempt(client, make_user):
    """It authenticates by `apitoken` header, which a cross-site request cannot
    supply, so there is nothing to forge and nothing to protect.

    As in testApiTokens, a request that dies downstream is a request that got
    through: the router hands the fake backend's return value to code that wants a
    real `requests.Response`. Reaching that failure means CSRF let it past.
    """
    make_user(role="contributor")
    try:
        response = client.post("/api/samples", headers={"apitoken": "apitoken-contributor"}, json={})
    except Exception:
        return
    # the router answers a body that is not a report with a 400 of its own; the
    # refusal this test is about is the CSRF one, so it looks for that
    assert b"CSRF token is missing or invalid" not in response.data, "the API must not require a browser token"


# --- what the pages emit ---------------------------------------------------------

def test_the_base_layout_exposes_the_token_to_scripts(client, as_role):
    """post_action.js reads this meta tag; without it every data-post control 400s."""
    as_role("admin")
    page = client.get("/settings").get_data(as_text=True)
    assert f'<meta name="csrf-token" content="{session_token(client)}">' in page


@pytest.mark.parametrize("path", ["/data/import", "/data/submit"])
def test_the_dropzones_send_the_token(client, as_role, path):
    """Uploads go out as XHR, so no hidden input reaches them. Flask-Dropzone emits
    the header itself once DROPZONE_ENABLE_CSRF is on and it can find our protector
    under `app.extensions["csrf"]` - break either and the upload silently 400s."""
    as_role("admin")
    page = client.get(path).get_data(as_text=True)
    assert f'headers: {{"X-CSRF-Token": "{session_token(client)}"}}' in page


@pytest.mark.parametrize("script", ["trace_CFG/main.js", "trace_CFG/main_duo.js"])
def test_the_cfg_explorer_sends_the_token_to_findloops(script):
    """`explore.findLoops` is a POST that computes rather than writes, but it is
    still an unsafe method and still gets checked. The vendored CFGExplorer calls
    it through d3.xhr from five places; each needs the header or the control-flow
    graph silently fails to render its loops."""
    path = os.path.join(STATIC_ROOT, script)
    with open(path, encoding="utf-8") as source_file:
        source = source_file.read()
    call_sites = source.count('findLoops/")')
    assert call_sites, "the call sites moved - this lint is no longer watching anything"
    assert source.count("X-CSRFToken") == call_sites


def template_files():
    for directory, _, filenames in os.walk(TEMPLATE_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                yield os.path.join(directory, filename)


def post_forms():
    """Every `method=post` form in the template tree, as (path, form source)."""
    for path in sorted(template_files()):
        with open(path, encoding="utf-8") as template:
            source = template.read()
        for match in FORM_TAG.finditer(source):
            end = source.find("</form>", match.start())
            relative = os.path.relpath(path, TEMPLATE_ROOT)
            yield pytest.param(source[match.start():end], id=f"{relative}:{source.count(chr(10), 0, match.start()) + 1}")


@pytest.mark.parametrize("form_source", post_forms())
def test_every_post_form_carries_the_token(form_source):
    """A lint, not a render: a form added without a token fails here rather than in
    a browser, and names the file and line it is missing from."""
    assert f"{FIELD_NAME}()" in form_source
