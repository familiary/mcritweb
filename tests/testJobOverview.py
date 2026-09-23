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

import logging
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


if __name__ == "__main__":
    unittest.main()
