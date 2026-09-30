#!/usr/bin/python
"""The API passthrough honours the role behind the token.

`token_required` used to check only that a token matched *some* user. Since the
router forwards to the backend's write endpoints, that made a 'pending' account's
token as powerful as an admin's, and turned the API into the cheapest way around
every role check in the web UI.

Roles now mirror the UI: reads and job submission at visitor, adding a report at
contributor, 'pending' refused outright.

These tests assert on the gate and nothing else. The router builds its client with
`raw_responses=True` and hands the result to `handle_raw_response`, which wants a
real `requests.Response`; the fakes return plain values, so anything that gets past
authorization dies downstream. That distinction is exactly what `_verdict` encodes -
a request that dies downstream is a request that was allowed through.
"""

import logging
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ALLOWED = "allowed"


def token_for(role):
    """make_user in conftest gives every account a predictable token."""
    return {"apitoken": f"apitoken-{role}"}


def _verdict(call):
    """403 when authorization refused, ALLOWED when the request got past it."""
    try:
        response = call()
    except Exception:
        return ALLOWED
    return response.status_code if response.status_code == 403 else ALLOWED


def test_a_request_without_a_token_is_refused(client, as_role):
    as_role("admin")
    assert _verdict(lambda: client.get("/api/version")) == 403


def test_an_unknown_token_is_refused(client, as_role):
    as_role("admin")
    assert _verdict(lambda: client.get("/api/version", headers={"apitoken": "not-a-token"})) == 403


def test_a_pending_token_is_refused(client, make_user):
    make_user("pending")
    assert _verdict(lambda: client.get("/api/version", headers=token_for("pending"))) == 403


@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_a_role_bearing_token_can_read(client, make_user, role):
    make_user(role)
    assert _verdict(lambda: client.get("/api/version", headers=token_for(role))) == ALLOWED


def test_a_visitor_token_cannot_add_a_report(client, make_user):
    """POST /api/samples reaches addReport, which data.submit puts behind
    contributor_required."""
    make_user("visitor")
    assert _verdict(lambda: client.post("/api/samples", headers=token_for("visitor"), json={})) == 403


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_a_contributor_token_may_add_a_report(client, make_user, role):
    make_user(role)
    assert _verdict(lambda: client.post("/api/samples", headers=token_for(role), json={})) == ALLOWED


def test_a_visitor_token_cannot_rename_a_function(client, make_user):
    """PUT /api/functions/<id> reaches modifyFunction, which explore.modifyFunction puts
    behind contributor_required (fkie-cad/mcritweb#72)."""
    make_user("visitor")
    assert _verdict(lambda: client.put("/api/functions/1", headers=token_for("visitor"), json={"function_name": "x"})) == 403


@pytest.mark.parametrize("role", ["contributor", "admin"])
def test_a_contributor_token_may_rename_a_function(client, make_user, role):
    make_user(role)
    assert _verdict(lambda: client.put("/api/functions/1", headers=token_for(role), json={"function_name": "x"})) == ALLOWED


def test_a_visitor_token_can_still_read_samples(client, make_user):
    """The same path by GET is a read, and stays visitor-level."""
    make_user("visitor")
    assert _verdict(lambda: client.get("/api/samples", headers=token_for("visitor"))) == ALLOWED


def test_a_visitor_token_can_still_submit_a_job(client, make_user):
    """Job submission is visitor-level in the UI (analyze.query), so it stays so here."""
    make_user("visitor")
    assert _verdict(lambda: client.get("/api/matches/sample/1", headers=token_for("visitor"))) == ALLOWED


# --- router branches are anchored ------------------------------------------------------
#
# `re.match` is not a full match, so an unanchored branch claimed every path *beginning*
# with its pattern: `status_whatever` dispatched to `getStatus()`. Harmless while every
# branch re-parses its own groups, but it belongs with the role tests, because the gate
# in `CONTRIBUTOR_ONLY` is itself a `re.match` - `samples$` - and an unanchored write
# branch added later would be reachable by a path the gate does not recognise.
#
# A path the router does not implement falls through to `Response(status=501)` without
# reaching the backend, so 501 here means "did not dispatch" and is distinguishable from
# the downstream death that `_verdict` treats as success.


def _dispatched(call):
    """False when the router fell through to its 501, True when a branch claimed the path."""
    try:
        return call().status_code != 501
    except Exception:
        # the branch ran and died in `handle_raw_response`, which is a dispatch
        return True


@pytest.mark.parametrize(
    "path",
    [
        "status_whatever",              # was: getStatus
        "matches/sample/1/2/3",         # was: requestMatchesForSampleVs(1, 2)
        "matches/function/1/2/3",       # was: getMatchFunctionVs(1, 2)
        "query/binary/nonsense",        # was: requestMatchesForUnmappedBinary
        "versionitis",                  # never matched - `version$` was already anchored
    ],
)
def test_an_unimplemented_path_does_not_dispatch(client, make_user, path):
    make_user("admin")
    assert not _dispatched(lambda: client.get(f"/api/{path}", headers=token_for("admin")))


@pytest.mark.parametrize("path", ["status", "version", "samples/1", "matches/sample/1", "matches/sample/1/2"])
def test_the_paths_the_router_does_implement_still_dispatch(client, make_user, path):
    make_user("admin")
    assert _dispatched(lambda: client.get(f"/api/{path}", headers=token_for("admin")))


@pytest.mark.parametrize("path", ["query/binary", "query/binary/mapped/4194304"])
def test_the_query_binary_pair_both_still_dispatch(client, make_user, path):
    """The outer branch deliberately covers the mapped sub-path, so it anchors on the
    optional suffix rather than on `query/binary` alone - anchoring it any tighter would
    have made the mapped variant unreachable."""
    make_user("admin")
    assert _dispatched(lambda: client.post(f"/api/{path}", headers=token_for("admin"), data=b"MZ"))


if __name__ == "__main__":
    unittest.main()
