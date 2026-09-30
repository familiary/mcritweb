"""The user who asked for a job is shown where the job is (fkie-cad/mcritweb#37).

The backend records it on the job document (mcrit's `username` on put()); mcrit's
Job exposes it as .username. A backend that predates the field, or a job from
before it existed, must not break the page - the column shows a dash and the job
page simply has no such row.
"""
import pytest
from fixtureData import CorpusMcritClient
from mcrit.queue.LocalQueue import Job


class OwnedJob(Job):
    @property
    def username(self):
        return self._data.get("username")


class JobWithoutOwnerField(Job):
    """mcrit before the field existed: the attribute is not there at all."""

    @property
    def username(self):
        raise AttributeError("username")


class OwnerAwareCorpus(CorpusMcritClient):
    job_class = OwnedJob

    def getQueueData(self, *args, **kwargs):
        self._record("getQueueData", *args, **kwargs)
        return [self.job_class(dict(entry, username=owner), None) for entry, owner in zip(self._queue, ("alice", None, "bob"))]

    def getJobData(self, job_id, *args, **kwargs):
        job = super().getJobData(job_id, *args, **kwargs)
        if job is None:
            return None
        return self.job_class(dict(job._data, username="alice"), None)


class OwnerlessCorpus(OwnerAwareCorpus):
    job_class = JobWithoutOwnerField


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return OwnerAwareCorpus()


def _job_id(fake):
    return next(iter(fake._jobs))


def test_the_jobs_page_names_who_asked(client, as_role, fake_mcrit):
    as_role("visitor")
    page = client.get("/data/jobs").data.decode()
    assert "<th scope=\"col\">User</th>" in page
    assert "alice" in page and "bob" in page


def test_a_job_without_a_user_shows_a_dash_not_none(client, as_role, fake_mcrit):
    as_role("visitor")
    page = client.get("/data/jobs").data.decode()
    assert "None" not in page.split("<tbody>")[-1]


def test_the_job_page_has_a_requested_by_row(client, as_role, fake_mcrit):
    as_role("visitor")
    page = client.get(f"/data/jobs/{_job_id(fake_mcrit)}").data.decode()
    assert "Requested by:" in page
    assert "alice" in page


@pytest.mark.parametrize("fake_mcrit", [OwnerlessCorpus()])
def test_an_older_backend_without_the_field_still_renders(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get("/data/jobs")
    assert response.status_code == 200
    page = response.data.decode()
    assert "<th scope=\"col\">User</th>" in page
    response = client.get(f"/data/jobs/{_job_id(fake_mcrit)}")
    assert response.status_code == 200
    assert "Requested by:" not in response.data.decode()
