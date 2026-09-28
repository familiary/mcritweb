#!/usr/bin/python
"""The job overview page has to survive a dependency that is no longer there.

`data.job_by_id` already knows that `getJobData` answers None for a job the backend
does not have - three lines above the crash it renders `job_invalid.html` for exactly
that. It just does not apply the same care to the job's children:

    child_jobs = sorted([client.getJobData(id) for id in job_info.all_dependencies],
                        key=lambda x: x.number)

One deleted dependency puts a None in that list and `.number` takes the whole page down
with a 500. This is reachable straight from the UI: `/data/jobs/category_<method>/delete`
deletes every job of a method in one go, which orphans the dependencies of any cross
compare that combined them.

The captured corpus happens to demonstrate it without any help - its cross compare job
lists five dependencies and the capture did not include them - which is why
testResultPages.py now renders the job page for every report rather than only one.
"""

import json
import logging
import re
import unittest

import pytest
from mcrit.queue.LocalQueue import Job

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def job_data(job_id, number, method="getMatchesForSample", dependencies=(), params="{}"):
    """The wire shape LocalQueue.Job wraps, trimmed to what the overview page reads."""
    return {
        "_id": job_id,
        "number": number,
        # params is a JSON *string* of {index: value}, the way the queue stores it
        "payload": {"method": method, "params": params, "file_params": "{}", "descriptor": None},
        "all_dependencies": list(dependencies),
        "created_at": {"$date": "2026-01-01T00:00:00.000Z"},
        "started_at": {"$date": "2026-01-01T00:00:01.000Z"},
        "finished_at": {"$date": "2026-01-01T00:00:02.000Z"},
        "last_error": None,
        "terminated": False,
        "attempts_left": 3,
        "progress": 1,
        "result": "some-result-id",
    }


class JobsWithHoles:
    """A backend that knows the parent job and only the children it is told about."""

    def __init__(self, parent, children):
        self._jobs = {parent["_id"]: parent}
        self._jobs.update({child["_id"]: child for child in children})

    def getJobData(self, job_id, *args, **kwargs):
        entry = self._jobs.get(job_id)
        return Job(entry, None) if entry else None

    def getSampleById(self, *args, **kwargs):
        return None

    def getFamily(self, *args, **kwargs):
        return None


@pytest.fixture
def overview(app, client, as_role):
    """Return a callable that installs a backend and fetches a job overview page."""
    def _overview(parent, children):
        backend = JobsWithHoles(parent, children)
        app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
        as_role("visitor")
        return client.get(f"/data/jobs/{parent['_id']}")
    return _overview


def test_a_job_whose_dependencies_are_all_gone_still_renders(overview):
    parent = job_data("parent", 10, "combineMatchesToCross", ["gone-a", "gone-b"])
    response = overview(parent, [])
    assert response.status_code == 200, "a deleted dependency should not 500 the page"


def test_the_page_says_that_children_are_missing(overview):
    """Silently rendering a shorter list would misreport the job: the overview would
    claim a cross compare combined nothing."""
    parent = job_data("parent", 10, "combineMatchesToCross", ["gone-a", "gone-b"])
    response = overview(parent, [])
    assert b"2 of this job's 2 sub-jobs" in response.data, response.data[-2000:]
    assert response.data.count(b"no longer in the system") == 1, "said once. `in` is as true of two copies as of one, and a resolution that keeps both sides of this block has happened twice in the integration merges."


def test_the_children_that_remain_are_still_listed_and_ordered(overview):
    """Ids chosen so that only sorting by job number gives this order - dependency
    order and alphabetical order both put aaa-job first."""
    parent = job_data("parent", 10, "combineMatchesToCross", ["gone", "aaa-job", "zzz-job"])
    # a 1vN job carries its sample id as argument 0; the overview reads it to build the
    # sample lookup, so the children need a real one
    children = [job_data("aaa-job", 9, params='{"0": 7}'), job_data("zzz-job", 4, params='{"0": 8}')]
    response = overview(parent, children)

    assert response.status_code == 200
    body = response.data
    assert b"aaa-job" in body and b"zzz-job" in body, "surviving children were dropped"
    assert body.index(b"zzz-job") < body.index(b"aaa-job"), "children lost their sort order"


def test_a_job_without_dependencies_is_unaffected(overview):
    parent = job_data("parent", 10, "getMatchesForSample", [])
    response = overview(parent, [])

    assert response.status_code == 200
    assert b"no longer" not in response.data


# --- polling a running job, issue #183 --------------------------------------------
#
# A running job's overview used to meta-refresh itself, and every tick re-ran the whole
# page: getJobData for the job and each of its sub-jobs, and a getSampleById per sample
# they name - 28 backend calls a tick for a cross compare over 13 samples, measured,
# nearly all of them for things the previous tick already had. The page now polls
# data.job_status, which reads the job alone, and reloads when the answer differs from
# what it rendered.


def running(job):
    """The same job, not finished yet and with all of its sub-jobs still unfinished."""
    job = dict(job, finished_at=None, progress=0.25, result=None)
    job["unfinished_dependencies"] = list(job["all_dependencies"])
    return job


class CountingJobs(JobsWithHoles):
    """JobsWithHoles, recording every call a view makes."""

    def __init__(self, parent, children):
        super().__init__(parent, children)
        self.calls = []

    def getJobData(self, job_id, *args, **kwargs):
        self.calls.append(("getJobData", job_id))
        return super().getJobData(job_id)

    def getSampleById(self, sample_id, *args, **kwargs):
        self.calls.append(("getSampleById", sample_id))
        return None

    def getFamily(self, family_id, *args, **kwargs):
        self.calls.append(("getFamily", family_id))
        return None


@pytest.fixture
def cross_compare_backend(app):
    """A running cross compare over three samples, one of its sub-jobs finished."""
    children = [job_data(f"child-{sample_id}", sample_id, params=f'{{"0": {sample_id}}}') for sample_id in (7, 8, 9)]
    children[1] = running(children[1])
    children[2] = running(children[2])
    parent = running(job_data("parent", 10, "combineMatchesToCross", [child["_id"] for child in children]))
    parent["unfinished_dependencies"] = ["child-8", "child-9"]
    parent["started_at"] = None
    backend = CountingJobs(parent, children)
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    return backend


def test_the_status_of_a_running_job_is_read_off_the_job_alone(client, as_role, cross_compare_backend):
    as_role("visitor")
    response = client.get("/data/jobs/parent/status")

    assert response.status_code == 200
    assert response.get_json() == {
        "started": False,
        "finished": False,
        "failed": False,
        "progress": 0.25,
        "unfinished_sub_jobs": 2,
    }
    assert cross_compare_backend.calls == [("getJobData", "parent")], "a tick fetched more than the job it polls"


def test_the_status_of_an_unknown_job_is_a_404(client, as_role, cross_compare_backend):
    as_role("visitor")
    response = client.get("/data/jobs/no-such-job/status")

    assert response.status_code == 404
    assert response.is_json


def test_a_running_job_polls_rather_than_reloading_on_every_tick(client, as_role, cross_compare_backend):
    as_role("visitor")
    body = client.get("/data/jobs/parent?refresh=3").get_data(as_text=True)

    assert '"/data/jobs/parent/status"' in body, "the page does not poll the status endpoint"
    # without scripting, the meta refresh is still what brings the page up to date
    assert re.search(r'<noscript>\s*<meta http-equiv="refresh" content="3">\s*</noscript>', body)
    assert body.count('http-equiv="refresh"') == 1, "a meta refresh outside <noscript> would still reload every tick"


def test_the_page_compares_against_what_the_endpoint_answers(client, as_role, cross_compare_backend):
    """The page reloads when the endpoint's answer differs from the state it rendered,
    so the two have to agree on an unchanged job - or every tick reloads after all."""
    as_role("visitor")
    body = client.get("/data/jobs/parent?refresh=3").get_data(as_text=True)
    rendered = json.loads(re.search(r"const shown = (\{.*?\});", body).group(1))

    assert rendered == client.get("/data/jobs/parent/status").get_json()


def test_a_finished_job_neither_polls_nor_refreshes(client, as_role, app):
    backend = CountingJobs(job_data("done", 3, "getMatchesForSample", [], params='{"0": 7}'), [])
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")
    body = client.get("/data/jobs/done?refresh=3").get_data(as_text=True)

    assert "/data/jobs/done/status" not in body
    assert 'http-equiv="refresh"' not in body


def test_a_page_that_will_not_poll_does_not_build_the_poll_state(client, as_role, app, monkeypatch):
    """The state reads a private part of mcrit's Job, so only a page that emits the
    polling script may take that risk - not every finished job's page."""
    import mcritweb.views.data as data_views

    def unexpected(job_info):
        raise AssertionError("built the poll state for a page that does not poll")

    monkeypatch.setattr(data_views, "job_overview_state", unexpected)
    backend = CountingJobs(job_data("done", 3, "getMatchesForSample", [], params='{"0": 7}'), [])
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")

    assert client.get("/data/jobs/done?refresh=3").status_code == 200
    assert client.get("/data/jobs/done").status_code == 200


def test_the_poll_state_survives_a_job_without_its_raw_document():
    """`unfinished_dependencies` has no property on mcrit's Job, so it is read from the
    raw document; a Job that stops exposing that must cost the count, not the page."""
    from types import SimpleNamespace

    from mcritweb.views.data import job_overview_state

    job = SimpleNamespace(started_at="2026-01-01", finished_at=None, is_failed=False, progress=0.5)
    assert job_overview_state(job)["unfinished_sub_jobs"] == 0


if __name__ == "__main__":
    unittest.main()
