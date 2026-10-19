#!/usr/bin/python
"""What the job tables say about how long a job took.

A job that waits on dependencies - a cross compare waits on one child per sample -
only starts once the last of them is done, so `Job.duration`, finished_at minus
started_at of the parent, times the assembly of its result and nothing of the work
it is assembled from. `data.total_duration` is the created_at -> finished_at span
that does cover the children, counted up to the latest time the backend wrote on the
job or its children while it is still running; these tests pin it against the captured
cross compare job and over every timestamp shape a job document can carry. See issue
#46.

mcrit stamps its jobs with the backend's local time, so this server's clock never goes
into the subtraction: `server_clock` sets it two hours off to show that.
"""

import logging
import re
import unittest
from datetime import UTC, datetime

import pytest
from fixtureData import CorpusMcritClient, job_id_of, load
from mcrit.queue.LocalQueue import Job

from mcritweb.views import data
from mcritweb.views.data import dependency_progress, total_duration

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


#: The captured cross compare was queued at 10:46:10.490, as the backend's clock read.
#: A backend at UTC+2 stamps that local time and the REST API sends it with a "Z", so
#: by this server's UTC clock the job was queued two hours in the future.
SERVER_NOW = datetime(2026, 8, 6, 8, 47, 40, tzinfo=UTC)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def server_clock(monkeypatch):
    """This server's clock, two hours behind the backend's stamps (see SERVER_NOW).
    Measured against it, a running job's total came out negative and the row was
    hidden."""
    monkeypatch.setattr(data, "utc_now", lambda: SERVER_NOW)
    return SERVER_NOW


def job_table_row(page, label):
    """The value the job table shows for a label, or None if it has no such row.

    The rows are label/value cell pairs:
    <td valign="middle">Duration: </td><td valign="middle"> 0:00:00 </td>

    The label cell may carry markup of its own - a row whose meaning needs explaining
    wraps it in a `hint--right` span - so the label is matched inside the cell rather
    than as the whole of it.
    """
    match = re.search(
        r"<td valign=\"middle\"[^>]*>(?:<[^>]+>)?" + re.escape(label) + r": (?:</[^>]+>)?\s*</td>\s*<td valign=\"middle\"[^>]*>\s*(.*?)\s*</td>",
        page,
        re.S,
    )
    return match.group(1).strip() if match else None


def test_cross_job_page_reports_the_time_its_dependencies_took(client, as_role):
    """A cross compare does not start until every child has finished, so Job.duration
    - finished_at minus started_at of the parent alone - times the assembly of the
    matrix and nothing of the matching that fills it (issue #46). The captured job was
    created at 10:46:10.490 and finished at 10:46:18.572, eight seconds later, and
    reported 0:00:00."""
    as_role("visitor")
    response = client.get(f"/data/jobs/{job_id_of('cross_compare')}")

    assert response.status_code == 200
    page = response.data.decode()
    assert job_table_row(page, "Duration") == "0:00:00"
    assert job_table_row(page, "Total (since queued)") == "0:00:08"


def test_cross_result_page_reports_the_total_too(client, as_role):
    """The same table heads the result page, and the same number belongs there."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}")

    assert response.status_code == 200
    assert job_table_row(response.data.decode(), "Total (since queued)") == "0:00:08"


def test_a_job_without_dependencies_reports_only_its_own_duration(client, as_role):
    """Nothing waited on, nothing to add - the row stays off the page."""
    as_role("visitor")
    response = client.get(f"/data/jobs/{job_id_of('matches_for_sample')}")

    assert response.status_code == 200
    page = response.data.decode()
    assert job_table_row(page, "Duration") == "0:00:03"
    assert job_table_row(page, "Total (since queued)") is None


class CorpusStillRunningCross(CorpusMcritClient):
    """The captured corpus, with the cross compare rewound to before a worker took it.

    Every job in the corpus ran to completion, so the state the issue is about has to
    be put back by hand. Only the parent is rewound - its five children keep the
    timestamps they were captured with, which is a moment a real queue does pass
    through: the last child has finished, at 10:46:18.545, and the parent has not been
    picked up yet. A job in that state has no result either, so `getResultForJob` is
    rewound too.
    """

    def getJobData(self, job_id, *args, **kwargs):
        if job_id == job_id_of("cross_compare"):
            self._record("getJobData", job_id, *args, **kwargs)
            return Job(dict(load("cross_compare.job"), started_at=None, finished_at=None, locked_at=None, locked_by=None, progress=0), None)
        return super().getJobData(job_id, *args, **kwargs)

    def getResultForJob(self, job_id, *args, **kwargs):
        if job_id == job_id_of("cross_compare"):
            self._record("getResultForJob", job_id, *args, **kwargs)
            return None
        return super().getResultForJob(job_id, *args, **kwargs)


class CorpusStillRunningCrossChildGone(CorpusStillRunningCross):
    """The same, with one of the five children deleted from the queue."""

    GONE = "6a7465f2f8b8d2c6f83664cc"

    def __init__(self):
        super().__init__()
        self._queue = [job for job in self._queue if job["_id"]["$oid"] != self.GONE]
        self._queue_by_id.pop(self.GONE)


RUNNING_TOTAL = '<span data-elapsed-seconds="8">0:00:08</span> and counting'


class TestWhileTheDependenciesAreStillRunning:
    """The half of issue #46 the page could not answer at all: until the last child is
    done the parent has no duration, and the progress it does have is its own, which
    is 0 no matter how much of the work is finished."""

    @pytest.fixture
    def fake_mcrit(self):
        return CorpusStillRunningCross()

    def test_the_job_page_reports_how_long_the_job_has_been_going(self, client, as_role, server_clock):
        as_role("visitor")
        response = client.get(f"/data/jobs/{job_id_of('cross_compare')}")

        assert response.status_code == 200
        page = response.data.decode()
        assert job_table_row(page, "Duration") == "This job hasn't finished yet"
        # `Job.progress` is the parent's own counter and reads 0 for the whole time the
        # children run, which is the other half of what #46 calls meaningless. The row
        # reports the dependencies instead: this fixture's five children are all done,
        # so the parent is waiting on nothing and says so.
        assert job_table_row(page, "Progress") == (
            '100.00% <span class="text-muted">(across 5 dependencies)</span>')
        # queued at 10:46:10, the last child finished at 10:46:18, both by the backend's
        # clock - whatever this server's reads
        assert job_table_row(page, "Total (since queued)") == RUNNING_TOTAL

    def test_the_progress_cell_says_it_shows_the_dependencies(self, client, as_role):
        """A script that updates the progress cell from the parent's own counter (the
        polling of #214) has to leave this number alone, or the 100% drops to 0%."""
        as_role("visitor")
        page = client.get(f"/data/jobs/{job_id_of('cross_compare')}").data.decode()

        assert re.search(r'<td valign="middle" data-progress-of="dependencies">\s*100\.00%', page)

    def test_the_in_progress_page_reports_it_too(self, client, as_role, server_clock):
        """`/data/result` of an unfinished job renders job_in_progress.html, which
        switches the duration row off - so this row is the only elapsed time on it."""
        as_role("visitor")
        response = client.get(f"/data/result/{job_id_of('cross_compare')}")

        assert response.status_code == 200
        page = response.data.decode()
        assert job_table_row(page, "Duration") is None
        assert job_table_row(page, "Total (since queued)") == RUNNING_TOTAL


class TestWhileTheDependenciesAreStillRunningOneGone:
    @pytest.fixture
    def fake_mcrit(self):
        return CorpusStillRunningCrossChildGone()

    def test_the_progress_counts_the_dependencies_it_averages(self, client, as_role):
        """The average leaves a deleted child out, so the count beside it does too; the
        page says separately how many are gone."""
        as_role("visitor")
        page = client.get(f"/data/jobs/{job_id_of('cross_compare')}").data.decode()

        assert job_table_row(page, "Progress") == (
            '100.00% <span class="text-muted">(across 4 dependencies)</span>')
        assert re.search(r"1 of this job's 5 sub-jobs\s+is no longer in the system", page)


# --- data.total_duration, over the timestamp shapes a Job can carry ----------------

def job_with(created_at, finished_at, dependencies=("6a7465f2f8b8d2c6f83664c8",), attempts_left=3, terminated=False):
    return Job(
        {
            "created_at": created_at,
            "finished_at": finished_at,
            "all_dependencies": list(dependencies),
            # only read for a job with no finished_at, to tell "still running" from
            # "will never finish"
            "attempts_left": attempts_left,
            "terminated": terminated,
        },
        None,
    )


@pytest.mark.parametrize(
    "created_at, finished_at",
    [
        # what the REST API sends, and what LocalQueue.Job normalizes it to
        ({"$date": "2026-08-06T10:46:10.490Z"}, {"$date": "2026-08-06T10:46:18.572Z"}),
        ("2026-08-06T10:46:10.490Z", "2026-08-06T10:46:18.572Z"),
        ("2026-08-06 10:46:10.490000", "2026-08-06 10:46:18.572000"),
        (datetime(2026, 8, 6, 10, 46, 10, 490000), datetime(2026, 8, 6, 10, 46, 18, 572000)),
    ],
)
def test_total_duration_reads_every_timestamp_shape(created_at, finished_at):
    assert total_duration(job_with(created_at, finished_at)).total_seconds() == pytest.approx(8, abs=1)


@pytest.mark.parametrize(
    "created_at",
    [
        {"$date": "2026-08-06T10:46:10.490Z"},
        "2026-08-06T10:46:10.490Z",
        "2026-08-06 10:46:10.490000",
        datetime(2026, 8, 6, 10, 46, 10, 490000),
    ],
)
def test_total_duration_of_a_running_job_counts_to_the_backend_s_latest_stamp(created_at, server_clock):
    """The state issue #46 is actually about. While the children run the parent has
    no finished_at and no duration, so elapsed time is the only number there is - up
    to the latest stamp on the job or a child, here a running child's last progress
    update, 90 seconds in."""
    running_child = Job({"created_at": "2026-08-06T10:46:10.480Z", "started_at": "2026-08-06T10:46:10.565Z",
                         "finished_at": None, "locked_at": {"$date": "2026-08-06T10:47:40.100Z"}}, None)
    assert str(total_duration(job_with(created_at, None), [running_child])) == "0:01:30"


def test_a_running_job_without_its_dependencies_has_no_total():
    """The parent's own stamps alone would give the time between its children's
    creation and its own, a few milliseconds."""
    assert total_duration(job_with("2026-08-06T10:46:10.490Z", None)) is None


@pytest.mark.parametrize(
    "job",
    [
        pytest.param(job_with("2026-08-06T10:46:10.490Z", "2026-08-06T10:46:18.572Z", dependencies=()), id="no dependencies"),
        pytest.param(job_with("2026-08-06T10:46:10.490Z", None, dependencies=()), id="running, nothing waited on"),
        pytest.param(job_with("2026-08-06T10:46:10.490Z", None, attempts_left=0), id="out of attempts"),
        pytest.param(job_with("2026-08-06T10:46:10.490Z", None, terminated=True), id="terminated"),
        pytest.param(
            Job({"all_dependencies": ["6a7465f2f8b8d2c6f83664c8"], "created_at": "2026-08-06T10:46:10.490Z", "finished_at": None}, None),
            id="running, no attempts_left field",
        ),
        pytest.param(job_with(None, "2026-08-06T10:46:18.572Z"), id="never created"),
        pytest.param(job_with("who knows", "2026-08-06T10:46:18.572Z"), id="unparsable"),
        pytest.param(job_with("2026-08-06T10:46", "2026-08-06T10:46:18.572Z"), id="truncated"),
        pytest.param(job_with("2026-08-06T10:46:18.572Z", "2026-08-06T10:46:10.490Z"), id="finished before created"),
        pytest.param(
            job_with(datetime(2026, 8, 6, 10, 46, 10, tzinfo=UTC), datetime(2026, 8, 6, 10, 46, 18)),
            id="one aware one naive",
        ),
        pytest.param(Job({"created_at": "2026-08-06T10:46:10.490Z", "finished_at": "2026-08-06T10:46:18.572Z"}, None), id="no dependency field"),
        pytest.param(Job({"all_dependencies": ["6a7465f2f8b8d2c6f83664c8"], "finished_at": "2026-08-06T10:46:18.572Z"}, None), id="no created_at field"),
        pytest.param(Job({"all_dependencies": ["6a7465f2f8b8d2c6f83664c8"], "created_at": "2026-08-06T10:46:10.490Z"}, None), id="no finished_at field"),
    ],
)
def test_total_duration_has_nothing_to_show(job):
    """Every shape that cannot produce a number renders as no row rather than a 500."""
    assert total_duration(job, []) is None


def test_latest_job_timestamp_reads_every_field_and_shape():
    jobs = [
        Job({"created_at": datetime(2026, 8, 6, 10, 46, 10), "finished_at": None}, None),
        Job({"created_at": "2026-08-06T10:46:10.480Z", "started_at": {"$date": "2026-08-06T10:46:11.000Z"},
             "locked_at": {"$date": "2026-08-06T10:46:30.900Z"}, "finished_at": None}, None),
    ]
    assert data.latest_job_timestamp(jobs) == datetime(2026, 8, 6, 10, 46, 30)
    assert data.latest_job_timestamp([]) is None


class _Child:
    """The two attributes `dependency_progress` reads, and nothing else."""

    def __init__(self, progress=0.0, finished=False):
        self.progress = progress
        self.finished_at = "2026-08-06T10:46:18.000Z" if finished else None


def test_dependency_progress_averages_the_children():
    assert dependency_progress([_Child(0.0), _Child(0.5), _Child(1.0)]) == pytest.approx(0.5)


def test_a_finished_child_counts_as_done_whatever_its_counter_says():
    """A worker that finishes without ticking progress to 1.0 must not hold the parent
    below 100% forever."""
    assert dependency_progress([_Child(0.0, finished=True)]) == 1.0


def test_a_deleted_dependency_is_skipped_not_counted_as_zero():
    """Counting it as zero would make the number go *backwards* when a dependency is
    cleaned up, which is worse than the 0% this row replaces."""
    assert dependency_progress([_Child(1.0, finished=True), None]) == 1.0


def test_no_knowable_children_means_no_number():
    assert dependency_progress([]) is None
    assert dependency_progress([None]) is None
    assert dependency_progress(None) is None


def test_a_counter_outside_zero_to_one_is_clamped():
    """The counter is written by a worker; the row must not render -300% or 4200%."""
    assert dependency_progress([_Child(5.0)]) == 1.0
    assert dependency_progress([_Child(-2.0)]) == 0.0


if __name__ == "__main__":
    unittest.main()
