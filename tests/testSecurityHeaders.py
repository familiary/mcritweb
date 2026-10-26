#!/usr/bin/python
"""The browser features no page uses are refused, and HTTPS stays HTTPS.

Permissions-Policy: nothing MCRITweb does needs a camera, a microphone, the location,
payments or USB, so no page of it - and no script injected into one - gets to ask the
user for them. The clipboard, which the copy buttons write to, stays allowed.

Strict-Transport-Security and Cross-Origin-Opener-Policy go out only on a request that
came over HTTPS, directly or through a proxy MCRITweb trusts (TRUSTED_PROXY_COUNT). A
browser ignores HSTS on a plain-HTTP response anyway, and Chromium logs an error for the
COOP header on every plain-HTTP page that is not on the loopback address, so sending them
there would only put an error in every console of a deployment served that way, which
docker-mcrit's default site is.

The HSTS value is the one docker-mcrit's TLS site already adds in NGINX. Both reach the
browser then, the app's first, and browsers act on the first only: any other default
would change what that deployment tells its users.
"""

import pytest
from testAppConfig import build_app

FEATURES = ["camera", "microphone", "geolocation", "payment", "usb"]
DOCKER_MCRIT_HSTS = "max-age=31536000; includeSubDomains"


def features(policy):
    """A Permissions-Policy as {feature: allowlist}."""
    return dict(item.strip().split("=", 1) for item in policy.split(","))


@pytest.mark.parametrize("path", ["/login", "/", "/static/autocomplete.js", "/no/such/page"])
def test_every_response_refuses_the_features_no_page_uses(client, path):
    policy = features(client.get(path).headers["Permissions-Policy"])

    assert {feature: policy.get(feature) for feature in FEATURES} == {feature: "()" for feature in FEATURES}


def test_the_copy_buttons_keep_the_clipboard(client):
    """navigator.clipboard.writeText is governed by clipboard-write."""
    policy = features(client.get("/login").headers["Permissions-Policy"])

    assert "clipboard-write" not in policy


def test_plain_http_gets_neither_hsts_nor_coop(client):
    headers = client.get("/login").headers

    assert "Strict-Transport-Security" not in headers
    assert "Cross-Origin-Opener-Policy" not in headers


def test_https_gets_both(client):
    headers = client.get("/login", base_url="https://localhost").headers

    assert headers["Strict-Transport-Security"] == DOCKER_MCRIT_HSTS
    # a site that opens MCRITweb gets no handle on its window; windows it opens keep theirs
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"


def test_https_through_a_trusted_proxy_counts(tmp_path):
    """docker-mcrit terminates TLS in NGINX, which says so in X-Forwarded-Proto."""
    client = build_app(tmp_path, TRUSTED_PROXY_COUNT=1).test_client()
    headers = client.get("/login", headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}).headers

    assert headers["Strict-Transport-Security"] == DOCKER_MCRIT_HSTS
    assert "Cross-Origin-Opener-Policy" in headers


def test_a_forwarded_proto_nobody_trusts_does_not(tmp_path):
    """Without TRUSTED_PROXY_COUNT the header is the client's own say-so."""
    client = build_app(tmp_path).test_client()
    headers = client.get("/login", headers={"X-Forwarded-Proto": "https"}).headers

    assert "Strict-Transport-Security" not in headers


@pytest.mark.parametrize("value", ["max-age=300", None])
def test_an_operator_can_shorten_or_drop_hsts(tmp_path, value):
    """The guide's advice is to start short and lengthen; None leaves it to the proxy."""
    client = build_app(tmp_path, STRICT_TRANSPORT_SECURITY=value).test_client()
    headers = client.get("/login", base_url="https://localhost").headers

    assert headers.get("Strict-Transport-Security") == value
