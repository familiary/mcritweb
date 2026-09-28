#!/usr/bin/python
"""What the job tables say about how long a job took.

A job that waits on dependencies - a cross compare waits on one child per sample -
only starts once the last of them is done, so `Job.duration`, finished_at minus
started_at of the parent, times the assembly of its result and nothing of the work
it is assembled from. `data.total_duration` is the created_at -> finished_at span
that does cover the children, counted against the clock while the job is still
running; these tests pin it against the captured cross compare job and over every
timestamp shape a job document can carry. See issue #46.

The clock is `data.utc_now`, and every test that reaches it freezes it, so nothing
here depends on when it runs.
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


#: 90 seconds after the captured cross compare was queued (10:46:10.490).
NOW = datetime(2026, 8, 6, 10, 47, 40)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


@pytest.fixture
def frozen_clock(monkeypatch):
    """Stop the clock the elapsed-time row reads, so it is a number and not a race."""
    monkeypatch.setattr(data, "utc_now", lambda: NOW)
    return NOW


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
    through: the last child has finished and the parent has not been picked up yet.
    A job in that state has no result either, so `getResultForJob` is rewound too.
    """

    def getJobData(self, job_id, *args, **kwargs):
        if job_id == job_id_of("cross_compare"):
            self._record("getJobData", job_id, *args, **kwargs)
            return Job(dict(load("cross_compare.job"), started_at=None, finished_at=None, progress=0), None)
        return super().getJobData(job_id, *args, **kwargs)

    def getResultForJob(self, job_id, *args, **kwargs):
        if job_id == job_id_of("cross_compare"):
            self._record("getResultForJob", job_id, *args, **kwargs)
            return None
        return super().getResultForJob(job_id, *args, **kwargs)


class TestWhileTheDependenciesAreStillRunning:
    """The half of issue #46 the page could not answer at all: until the last child is
    done the parent has no duration, and the progress it does have is its own, which
    is 0 no matter how much of the work is finished."""

    @pytest.fixture
    def fake_mcrit(self):
        return CorpusStillRunningCross()

    def test_the_job_page_reports_how_long_the_job_has_been_going(self, client, as_role, frozen_clock):
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
        assert job_table_row(page, "Total (since queued)") == "0:01:30 and counting"

    def test_the_in_progress_page_reports_it_too(self, client, as_role, frozen_clock):
        """`/data/result` of an unfinished job renders job_in_progress.html, which
        switches the duration row off - so this row is the only elapsed time on it."""
        as_role("visitor")
        response = client.get(f"/data/result/{job_id_of('cross_compare')}")

        assert response.status_code == 200
        page = response.data.decode()
        assert job_table_row(page, "Duration") is None
        assert job_table_row(page, "Total (since queued)") == "0:01:30 and counting"


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
def test_total_duration_of_a_running_job_counts_against_the_clock(created_at, frozen_clock):
    """The state issue #46 is actually about. While the children run the parent has
    no finished_at and no duration, so elapsed time is the only number there is."""
    assert str(total_duration(job_with(created_at, None))) == "0:01:30"


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
    assert total_duration(job) is None



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
