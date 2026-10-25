#!/usr/bin/python
"""No page of MCRITweb can be framed by another site, or tells one where it was.

A page inside another site's frame can be dressed up so that a logged-in user clicks
something they cannot see. SameSite=Lax keeps the session cookie out of a cross-site
frame in current browsers, so such a frame would mostly show a logged-out page; the
headers say it outright, for every response and every browser.

And a link off one of our pages sent the site it led to a Referer: the instance's
address in current browsers, the whole URL - job id, sample hash, search terms - in an
older one. Referrer-Policy keeps it within MCRITweb (#284).

The same Content-Security-Policy says what else a page may do: no plugin, no `<base>`
that re-points every relative link, no form posted to another site, and nothing fetched,
drawn or run as a worker from anywhere but here. An injection that got past autoescaping
could otherwise do any of those without running a script of its own.
"""

import pytest

#: What a page may load and send apart from scripts, directive by directive. Every page
#: was walked in Chromium under this policy, with the upload, the live search and the
#: CFG worker exercised, without a violation.
RESOURCE_DIRECTIVES = {
    "frame-ancestors": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'none'"],
    "form-action": ["'self'"],
    "connect-src": ["'self'"],
    "font-src": ["'self'"],
    # Bootstrap's icons are data: SVGs, and Dropzone draws its file previews as data: URLs
    "img-src": ["'self'", "data:", "blob:"],
    "worker-src": ["'self'"],
    "media-src": ["'none'"],
    "manifest-src": ["'self'"],
}


def directives(policy):
    """A Content-Security-Policy as {directive: [sources]}."""
    parsed = {}
    for directive in policy.split(";"):
        name, *sources = directive.split()
        parsed[name] = sources
    return parsed


def assert_resource_policy(headers):
    policy = directives(headers["Content-Security-Policy"])
    for name, sources in RESOURCE_DIRECTIVES.items():
        assert policy.get(name) == sources, f"{name} is {policy.get(name)}, not {sources}"


@pytest.mark.parametrize("path", ["/login", "/", "/static/autocomplete.js", "/no/such/page"])
def test_every_response_refuses_framing_by_other_sites(client, path):
    headers = client.get(path).headers

    assert_resource_policy(headers)
    assert headers["X-Frame-Options"] == "SAMEORIGIN"


def test_a_logged_in_page_refuses_it_too(client, as_role):
    as_role("visitor")
    headers = client.get("/settings").headers

    assert_resource_policy(headers)
    assert headers["X-Frame-Options"] == "SAMEORIGIN"



@pytest.mark.parametrize("path", ["/login", "/", "/static/autocomplete.js", "/no/such/page"])
def test_every_response_keeps_the_referrer_within_mcritweb(client, path):
    assert client.get(path).headers["Referrer-Policy"] == "same-origin"


def test_a_logged_in_page_keeps_it_within_mcritweb_too(client, as_role):
    as_role("visitor")
    assert client.get("/settings").headers["Referrer-Policy"] == "same-origin"


def test_a_view_that_sets_its_own_policy_keeps_it(app, client):
    """The hook only fills in what a response does not already say."""
    @app.route("/own-policy")
    def own_policy():
        return "x", 200, {"Content-Security-Policy": "default-src 'none'"}

    assert client.get("/own-policy").headers["Content-Security-Policy"] == "default-src 'none'"
