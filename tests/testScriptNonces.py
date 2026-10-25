#!/usr/bin/python
"""Only the scripts MCRITweb wrote run on its pages.

Autoescaping keeps a family name or a search term from becoming markup, and #85 showed
that it can be got around. A `<script>` that slipped past it ran like one of ours: the
Content-Security-Policy said nothing about scripts. It now runs a script element only
when it carries the nonce of the response it came in, which is drawn afresh for every
response, so markup injected into a page cannot know it. `'strict-dynamic'` hands that
trust on to the scripts ours create - base.html's later() queue, the CFG viewer's
libraries, Dropzone fetched on the first drag - so those need no nonce of their own.

The `onclick=`-style attributes are left alone for now, by `script-src-attr
'unsafe-inline'`, until they are listeners: an injected one still runs, an injected
`<script>` does not. testContentSecurityPolicyBrowser.py walks the pages in Chromium
under the policy.

So every script element a template writes has to carry `nonce="{{ csp_nonce() }}"`,
and the lint below fails on one that does not - including one added on another branch,
which would otherwise only show up as a page whose script silently never ran. A
`type="application/json"` block is data, not a script, and the policy does not apply.
"""

import os
import re

import pytest
from fixtureData import job_id_of

PACKAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb")
TEMPLATE_ROOT = os.path.join(PACKAGE_ROOT, "templates")

SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
DATA_BLOCK = re.compile(r"""\btype\s*=\s*["']application/json["']""", re.IGNORECASE)
TEMPLATE_NONCE = 'nonce="{{ csp_nonce() }}"'
NONCE_ATTRIBUTE = re.compile(r"""\bnonce\s*=\s*["']([^"']*)["']""")
SCRIPT_NONCE = re.compile(r"'nonce-([^']+)'")


def template_files():
    for directory, _, filenames in os.walk(TEMPLATE_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                yield os.path.join(directory, filename)


def template_script_tags():
    """Every script element a template writes, as (path:line, opening tag)."""
    for path in sorted(template_files()):
        with open(path, encoding="utf-8") as template:
            source = template.read()
        for tag in SCRIPT_TAG.finditer(source):
            line = source.count("\n", 0, tag.start()) + 1
            yield f"{os.path.relpath(path, TEMPLATE_ROOT)}:{line}", tag.group()


def directives(policy):
    parsed = {}
    for directive in policy.split(";"):
        name, *sources = directive.split()
        parsed[name] = sources
    return parsed


def header_nonce(response):
    script_src = directives(response.headers["Content-Security-Policy"])["script-src"]
    nonces = [SCRIPT_NONCE.fullmatch(source).group(1) for source in script_src if SCRIPT_NONCE.fullmatch(source)]
    assert len(nonces) == 1, f"script-src names {len(nonces)} nonces: {script_src}"
    return nonces[0]


def scripts_without_the_nonce(page, nonce):
    """The script elements of a rendered page that do not carry `nonce`."""
    return [tag for tag in SCRIPT_TAG.findall(page)
            if not DATA_BLOCK.search(tag)
            and [value for value in NONCE_ATTRIBUTE.findall(tag)] != [nonce]]


def test_the_template_scan_still_sees_something():
    """A guard on the guard: a scan that matches nothing passes on any tree."""
    tags = list(template_script_tags())
    assert len(tags) > 40, f"only {len(tags)} script tags found - the scan is not watching the tree"


def test_every_script_a_template_writes_carries_the_nonce():
    """One assertion for the whole tree, as in testScriptEscaping.py: the healthy state
    is no offenders, and an empty parameter set would read like a pass."""
    offenders = [f"{where} {tag}" for where, tag in template_script_tags()
                 if not DATA_BLOCK.search(tag) and TEMPLATE_NONCE not in tag]
    assert not offenders, (
        "script elements without the page's nonce, which the Content-Security-Policy "
        "refuses to run: " + "; ".join(offenders) + f". Write <script {TEMPLATE_NONCE}>."
    )


def test_the_policy_runs_only_nonced_scripts(client):
    """No 'unsafe-inline' or 'self' for elements, which 'strict-dynamic' would ignore
    anyway, and no 'unsafe-eval'. The handler attributes are the one interim allowance."""
    policy = directives(client.get("/login").headers["Content-Security-Policy"])
    script_src = policy["script-src"]

    assert "'strict-dynamic'" in script_src
    assert "'report-sample'" in script_src
    header_nonce(client.get("/login"))
    for loose in ("'unsafe-inline'", "'unsafe-eval'", "'self'", "https:", "http:", "*"):
        assert loose not in script_src, f"script-src allows {loose}"
    assert policy["script-src-attr"] == ["'unsafe-inline'"]
    assert "script-src-elem" not in policy, "a script-src-elem would replace script-src for elements"


def test_every_response_gets_a_nonce_of_its_own(client):
    """A nonce repeated across responses is one an attacker can read off one page and
    write into another."""
    nonces = [header_nonce(client.get("/login")) for _ in range(3)]

    assert len(set(nonces)) == 3, nonces
    for nonce in nonces:
        # 16 random bytes, base64url: what CSP's nonce-source grammar accepts
        assert re.fullmatch(r"[A-Za-z0-9_-]{22,}", nonce), nonce


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The pages need real rows to render their tables, modals and their scripts."""
    return corpus_mcrit


#: Every kind of page, so every partial with a script is rendered at least once: the
#: drop overlay (every signed-in page), the upload and import zones, whose config
#: script flask-dropzone writes, the row and pagination partials, the CFG pages, the
#: result pages and the admin pages.
PAGES = [
    "/", "/explore/samples", "/explore/samples/0", "/explore/families", "/explore/families/1",
    "/explore/functions", "/explore/functions/84", "/explore/search?query=citadel&type=sample",
    "/explore/statistics", "/data/jobs?active=getMatchesForSample", "/settings", "/help",
    "/analyze/compare", "/analyze/compare_versus", "/analyze/cross_compare", "/analyze/unique_blocks",
    "/analyze/query", "/data/submit", "/data/import", "/data/export", "/data/matches/function/84/943",
    "/admin/users/", "/admin/server", "/logout",
] + [f"/data/result/{job_id_of(report)}" for report in
     ("matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks")
     ] + [f"/data/jobs/{job_id_of('cross_compare')}", f"/data/linkhunt/{job_id_of('matches_for_sample_vs')}"]


@pytest.mark.parametrize("path", PAGES)
def test_every_script_on_a_page_carries_that_responses_nonce(client, as_role, path):
    """Rendered rather than read, because not every script element is in a template:
    flask-dropzone's `dropzone.config()` writes one from Python."""
    as_role("admin")
    response = client.get(path)

    assert response.status_code == 200, f"{path} answered {response.status_code}"
    page = response.get_data(as_text=True)
    assert SCRIPT_TAG.search(page), f"{path} has no script at all - not the page this test means"
    assert not scripts_without_the_nonce(page, header_nonce(response))


@pytest.mark.parametrize("path", ["/login", "/register", "/help"])
def test_a_signed_out_page_carries_it_too(client, make_user, path):
    make_user(role="admin")
    response = client.get(path)

    page = response.get_data(as_text=True)
    assert SCRIPT_TAG.search(page)
    assert not scripts_without_the_nonce(page, header_nonce(response))
