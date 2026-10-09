#!/usr/bin/python
"""Routes answer rather than crash when the request is not what the UI would send.

All three cases here were 500s found while building the route/role test matrix and
the result-page fixtures: a URL typed by hand, a link scanner, or a page acted on
after someone else changed the data behind it. None needed a malicious caller - the
last one just needs two admins with the user list open at the same time.

Issues #94, #95, #96.
"""

import io
import json
import logging
import unittest

import pytest
from fixtureData import job_id_of

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The h1 of result_corrupted.html. Asserting on the template name would pass
#: whatever the page said, since the name appears nowhere in the rendered output.
CORRUPTED_MARKER = b"are corrupted"


# --- #94: start_cross_compare ----------------------------------------------------

@pytest.mark.parametrize("query", ["", "?samples=", "?samples=abc", "?samples=1,,2", "?samples=-1"])
def test_cross_compare_without_usable_samples_redirects(client, as_role, query):
    """job_id was only bound inside `if selected != ''`, so a bare request fell
    through to a redirect naming an unbound local. `?samples=abc` raised from
    int() on the same line."""
    as_role("visitor")
    response = client.get(f"/analyze/start_cross_compare{query}")
    assert response.status_code == 302
    assert "/analyze/cross_compare" in response.headers["Location"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", b"select at least one sample"),
        ("?samples=", b"select at least one sample"),
        ("?samples=,1,2", b"not a list of sample ids"),
        ("?samples=1,2,", b"not a list of sample ids"),
        ("?samples=abc", b"not a list of sample ids"),
    ],
)
def test_a_malformed_sample_list_says_so_rather_than_blaming_the_selection(client, as_role, query, expected):
    """Both cases used to flash "select at least one sample", which is the wrong advice
    for a list that was sent but unparseable - and it is what hid the leading comma
    `cross_compare.html` had been sending since the initial commit. The leading and
    trailing forms are exactly what that bug produced when one half of the selection
    was empty."""
    as_role("visitor")
    response = client.get(f"/analyze/start_cross_compare{query}", follow_redirects=True)
    assert expected in response.data


class TestCrossCompareStillWorks:
    """The strict fake raises on requestMatchesCross and the permissive one answers
    None, which is not a job id the redirect can be built from. Teach it one."""

    @pytest.fixture
    def fake_mcrit(self, recording_mcrit):
        def _request_matches_cross(*args, **kwargs):
            recording_mcrit._record("requestMatchesCross", *args, **kwargs)
            return "0123456789abcdef01234567"
        recording_mcrit.requestMatchesCross = _request_matches_cross
        return recording_mcrit

    def test_cross_compare_with_samples_still_queues_a_job(self, client, as_role, fake_mcrit):
        """The guard must not swallow the working case."""
        as_role("visitor")
        response = client.get("/analyze/start_cross_compare?samples=1,2")

        assert response.status_code == 302
        assert "/data/jobs/" in response.headers["Location"]
        queued = [args for name, args, _ in fake_mcrit.calls if name == "requestMatchesCross"]
        assert queued, "no job was queued"
        assert queued[0][0] == [1, 2], "the selected samples did not reach the backend"


# --- #95: change_user_role -------------------------------------------------------

def test_changing_the_role_of_a_deleted_user_reports_it(client, as_role):
    """UserInfo.fromDb answers None for an unknown id, and the view assigned
    straight through it. Reachable by acting on a stale user list."""
    as_role("admin")
    response = client.post("/admin/change_user_role/9999/visitor/all")
    assert response.status_code == 302
    assert "/admin/users" in response.headers["Location"]


def test_an_unknown_role_is_refused(client, as_role, make_user):
    """Any string used to land in user.role. The account then failed every
    decorator, so it could reach nothing and no page said why."""
    as_role("admin")
    user_id = make_user(role="visitor", username="target")
    response = client.post(f"/admin/change_user_role/{user_id}/superuser/all")

    assert response.status_code == 302
    from mcritweb.db import UserInfo
    with client.application.app_context():
        assert UserInfo.fromDb(user_id=user_id).role == "visitor", "the role was written anyway"


@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_every_known_role_still_applies(client, as_role, make_user, role):
    as_role("admin")
    user_id = make_user(role="visitor", username="target")
    assert client.post(f"/admin/change_user_role/{user_id}/{role}/all").status_code == 302

    from mcritweb.db import UserInfo
    with client.application.app_context():
        assert UserInfo.fromDb(user_id=user_id).role == role


# --- #96: a function deleted since the job ran -----------------------------------

class TestMissingFunctions:
    """These need the captured corpus, so the fake backend is overridden for them."""

    @pytest.fixture
    def fake_mcrit(self, corpus_mcrit):
        return corpus_mcrit

    def test_a_missing_function_renders_the_corrupted_page(self, client, as_role, fake_mcrit):
        """getFunctionsByIds returns only what the backend still has, and the view
        indexed the result directly. A sample deleted after the job finished left
        ids behind that resolve to nothing, and the report 500'd instead of saying
        so - while the cross-compare path had handled the same case for years.

        This drives the 1-vs-1 report, which is the path issue #96 was reported
        against. The other two call sites share `assign_matched_offsets`, whose
        contract is covered directly below."""
        as_role("visitor")
        job_id = job_id_of("matches_for_sample_vs")
        assert client.get(f"/data/result/{job_id}").status_code == 200, "the report does not render even intact"

        requested = [
            function_id
            for name, args, _ in fake_mcrit.calls
            if name == "getFunctionsByIds"
            for function_id in args[0]
        ]
        assert requested, "this path no longer looks up matched functions - the test is watching nothing"
        fake_mcrit._functions.pop(int(requested[0]))

        response = client.get(f"/data/result/{job_id}")
        assert response.status_code == 200
        assert CORRUPTED_MARKER in response.data


class FunctionEntry:
    def __init__(self, offset):
        self.offset = offset


class FunctionMatch:
    def __init__(self, matched_function_id):
        self.matched_function_id = matched_function_id
        self.matched_offset = None


class LookupClient:
    """Answers only for the ids it was given, as getFunctionsByIds does."""

    def __init__(self, offsets_by_id):
        self._offsets_by_id = offsets_by_id

    def getFunctionsByIds(self, function_ids, *args, **kwargs):
        return {fid: FunctionEntry(self._offsets_by_id[fid]) for fid in function_ids if fid in self._offsets_by_id}


def test_offsets_are_assigned_when_every_function_is_present():
    from mcritweb.views.data import assign_matched_offsets

    matches = [FunctionMatch(1), FunctionMatch(2)]
    assert assign_matched_offsets(LookupClient({1: 0x1000, 2: 0x2000}), matches) is True
    assert [match.matched_offset for match in matches] == [0x1000, 0x2000]


def test_a_missing_function_is_reported_rather_than_raising():
    from mcritweb.views.data import assign_matched_offsets

    matches = [FunctionMatch(1), FunctionMatch(2)]
    assert assign_matched_offsets(LookupClient({1: 0x1000}), matches) is False
    assert matches[0].matched_offset == 0x1000, "the entries that survive are still assigned"


def test_a_backend_answering_nothing_is_reported_rather_than_raising():
    """`or {}` in the helper - a None answer used to be an AttributeError one line on."""
    from mcritweb.views.data import assign_matched_offsets

    class SilentClient:
        def getFunctionsByIds(self, function_ids, *args, **kwargs):
            return None

    assert assign_matched_offsets(SilentClient(), [FunctionMatch(1)]) is False


# --- input the UI never sends: answered, not a 500 -----------------------------------
# Found in a security review as robustness findings. None of them leaked anything or
# wrote state; each was an unhandled exception and a traceback in the log.

#: JSON nested deeper than json.loads can follow raises RecursionError, not ValueError
DEEP_JSON = b"[" * 100000


def _upload(content, name, **fields):
    return {"file": (io.BytesIO(content), name), **fields}


BAD_INPUT = [
    ("visitor", "get", "/analyze/cross_compare?samples=abc", {}),
    ("visitor", "get", "/analyze/cross_compare?cache=1,x", {}),
    ("contributor", "post", "/data/import", {"data": _upload(DEEP_JSON, "a.json")}),
    ("contributor", "post", "/data/request_filename_info", {"data": json.dumps({"filename": 5, "file_header": ""})}),
    ("contributor", "post", "/data/request_filename_info", {"data": json.dumps({"filename": None, "file_header": None})}),
    ("contributor", "post", "/data/request_filename_info", {"data": json.dumps({"filename": "a.smda", "file_header": '"base_addr": ' + "9" * 5000})}),
    ("contributor", "post", "/data/submit_or_query", {"data": {"form_type": "other"}}),
    ("visitor", "post", "/api/query/function", {"data": "[]", "headers": {"apitoken": "apitoken-visitor"}}),
    ("visitor", "post", "/api/query/function", {"data": "null", "headers": {"apitoken": "apitoken-visitor"}}),
    ("visitor", "post", "/api/query/function", {"data": DEEP_JSON, "headers": {"apitoken": "apitoken-visitor"}}),
    ("contributor", "post", "/api/samples", {"data": '{"a": 1}', "headers": {"apitoken": "apitoken-contributor"}}),
]


@pytest.mark.parametrize(("role", "method", "url", "kwargs"), BAD_INPUT,
                         ids=[f"{method} {url[:40]} {i}" for i, (_, method, url, _) in enumerate(BAD_INPUT)])
def test_input_the_ui_never_sends_is_answered_not_raised(client, as_role, role, method, url, kwargs):
    as_role(role)
    response = getattr(client, method)(url, **kwargs)
    assert response.status_code < 500


@pytest.mark.parametrize("url", ["/api/query/function", "/api/samples"])
def test_the_api_answers_a_body_that_is_not_a_report_with_400(client, as_role, url):
    as_role("contributor")
    response = client.post(url, data="[]", headers={"apitoken": "apitoken-contributor"})
    assert response.status_code == 400


class TestJobPageParameters:
    """On a job still running, refresh sets the page's auto-refresh."""

    @pytest.fixture
    def fake_mcrit(self, corpus_mcrit, monkeypatch):
        finished = corpus_mcrit.getJobData

        def running(job_id, *args, **kwargs):
            job = finished(job_id, *args, **kwargs)
            if job is not None:
                job._data = {**job._data, "finished_at": None}
            return job

        monkeypatch.setattr(corpus_mcrit, "getJobData", running)
        return corpus_mcrit

    @pytest.mark.parametrize("query", ["?refresh=abc", "?forward=x", "?refresh=1.5&forward="])
    def test_a_parameter_that_is_not_a_number_reads_as_absent(self, client, as_role, query):
        as_role("visitor")
        response = client.get(f"/data/jobs/{job_id_of('cross_compare')}{query}")
        assert response.status_code == 200
        assert b'http-equiv="refresh"' not in response.data

    def test_a_number_still_sets_the_refresh(self, client, as_role):
        as_role("visitor")
        response = client.get(f"/data/jobs/{job_id_of('cross_compare')}?refresh=3")
        assert response.status_code == 200
        assert b'<meta http-equiv="refresh" content="3">' in response.data


def test_a_hand_edited_sample_list_is_reported(client, as_role):
    as_role("visitor")
    response = client.get("/analyze/cross_compare?samples=1,x", follow_redirects=True)
    assert response.status_code == 200
    assert b"not a list of sample ids" in response.data


@pytest.mark.parametrize("raised", [ValueError, TypeError, KeyError, AttributeError])
def test_a_report_that_older_smda_cannot_read_is_none(monkeypatch, raised):
    """Current smda raises ValueError for anything that is not a report; the older ones
    mcrit still allows raise TypeError or KeyError for some of the same input."""
    from mcritweb.views import utility

    def refuse(_):
        raise raised("not a report")

    monkeypatch.setattr(utility.SmdaReport, "fromDict", refuse)
    assert utility.read_smda_report(b"{}") is None


if __name__ == "__main__":
    unittest.main()
