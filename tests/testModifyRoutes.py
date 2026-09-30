#!/usr/bin/python
"""The modify routes wait for the change, not for a fixed time - issue #189.

mcrit queues a sample or family modification as a job and answers before the worker
has run it (`SampleResource.on_put`, `FamilyResource.on_put`), with no job id to
follow. `explore.modifySample` and `explore.modifyFamily` covered that with a
`time.sleep` - 0.3s, or 1.3s before returning to a cross compare - held for its full
length whether the worker had already run the job or had not reached it at all. They
now read the change back and stop as soon as it shows, never waiting longer than the
old 0.3s.

The fake backend below applies a modification only after a given number of reads, the
way the worker applies it only after its next poll, and applies it the way
`MongoDbStorage.modifySample` / `modifyFamily` do. The view's clock is replaced, so
these tests count what the request waited without waiting themselves.
"""

import logging

import pytest
import requests
from conftest import FakeMcritClient
from fixtureData import RawResponse
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.SampleEntry import SampleEntry

from mcritweb.views import explore

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SAMPLE_ID = 3
SED_FAMILY_ID = 3
GZIP_FAMILY_ID = 2
EMPTY_FAMILY_ID = 5
CROSS_COMPARE_JOB_ID = "6ab3b4131eeeb027e04b57ae"
NOT_CONFIRMED = "not confirmed it yet"


class QueueingBackend(FakeMcritClient):
    """A backend whose modifications land `lag` reads after they were requested.

    `lag=None` is a worker that never gets there within the request, as when it is
    busy with a long matching job. `rejects` answers the way the client does for
    anything but a 200/202, and `read_error` is raised by every read after the
    modification - a ConnectionError, as the client raises when the backend has gone away.
    `read_status` is what a raw read answers once the modification has been applied, as
    a backend does whose GET fails although the PUT went through.
    """

    def __init__(self, lag=0):
        super().__init__()
        self.lag = lag
        self.rejects = False
        self.read_error = None
        self.read_status = None
        self.sample = SampleEntry(None, sample_id=SAMPLE_ID, family_id=0)
        self.sample.family = ""
        self.sample.version = ""
        self.sample.is_library = False
        self.families = {
            0: FamilyEntry(family_name="", family_id=0, num_samples=7, num_functions=791, num_library_samples=1),
            GZIP_FAMILY_ID: FamilyEntry(family_name="gzip", family_id=GZIP_FAMILY_ID, num_samples=1, num_functions=217),
            SED_FAMILY_ID: FamilyEntry(family_name="sed", family_id=SED_FAMILY_ID, num_samples=1, num_functions=333),
            EMPTY_FAMILY_ID: FamilyEntry(family_name="empty", family_id=EMPTY_FAMILY_ID),
        }
        self.pending = None

    def _read(self):
        if self.pending is None:
            return
        if self.read_error is not None:
            raise self.read_error
        if self.lag == 0:
            self.pending()
            self.pending = None
        elif self.lag is not None:
            self.lag -= 1

    def getSampleById(self, sample_id, *args, **kwargs):
        self._record("getSampleById", sample_id, *args, **kwargs)
        self._read()
        return self.sample if sample_id == SAMPLE_ID else None

    def raw_variant(self):
        """This backend in raw mode. Unlike FakeMcritClient's copy, it shares `lag` and
        `pending` with the ordinary client, so a raw read and a parsed one see one worker."""
        backend = self

        class RawView:
            def getFamily(self, family_id, *args, **kwargs):
                return backend.getFamily(family_id, *args, _raw=True, **kwargs)

        return RawView()

    def getFamily(self, family_id, *args, _raw=False, **kwargs):
        """In raw mode, as FamilyResource.on_get answers: 404 for a family without a row."""
        self._record("getFamily", family_id, *args, **kwargs)
        self._read()
        family = self.families.get(family_id)
        if not _raw:
            return family
        if self.pending is None and self.read_status is not None:
            return RawResponse(self.read_status)
        return RawResponse(200, family.toDict()) if family is not None else RawResponse(404)

    def getJobData(self, job_id, *args, **kwargs):
        self._record("getJobData", job_id, *args, **kwargs)
        return {"job_id": job_id} if job_id == CROSS_COMPARE_JOB_ID else None

    def modifySample(self, sample_id, **kwargs):
        self._record("modifySample", sample_id, **kwargs)
        if self.rejects:
            return None
        def apply():
            for field, key in [("family", "family_name"), ("version", "version"), ("is_library", "is_library")]:
                if kwargs.get(key) is not None:
                    setattr(self.sample, field, kwargs[key])
        self.pending = apply
        return {"message": "Sample modified."}

    def modifyFamily(self, family_id, family_name=None, is_library=None):
        self._record("modifyFamily", family_id, family_name=family_name, is_library=is_library)
        if self.rejects:
            return None
        self.pending = lambda: self._modify_family(family_id, family_name, is_library)
        return {"message": "Family modified."}

    def _modify_family(self, family_id, family_name, is_library):
        """What MongoDbStorage.modifyFamily does to the family rows."""
        family = self.families[family_id]
        if is_library is not None:
            family.num_library_samples = family.num_samples if is_library else 0
        if family_name is not None:
            target = next((entry for entry in self.families.values() if entry.family_name == family_name), None)
            if target is None:
                target = FamilyEntry(family_name=family_name, family_id=max(self.families) + 1)
                self.families[target.family_id] = target
            target.num_samples += family.num_samples
            target.num_functions += family.num_functions
            target.num_library_samples += family.num_library_samples
            if family_id == 0:
                family.num_samples = family.num_functions = family.num_library_samples = 0
            else:
                del self.families[family_id]


class Clock:
    """Stands in for the `time` module inside the view: sleeping advances it."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(explore, "time", clock)
    return clock


@pytest.fixture
def logged(caplog):
    """caplog, with the module-wide `logging.disable` above lifted for the test."""
    logging.disable(logging.NOTSET)
    caplog.set_level(logging.WARNING)
    yield caplog
    logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit():
    return QueueingBackend(lag=0)


def flashes(client):
    with client.session_transaction() as test_session:
        return test_session.get("_flashes", [])


def modify_sample(client, **form):
    return client.post("/explore/modifySample", data={"sample_id": str(SAMPLE_ID), **form})


def modify_family(client, family_id=SED_FAMILY_ID, **form):
    return client.post("/explore/modifyFamily", data={"family_id": str(family_id), **form})


def assert_gave_up(clock, client, kind):
    """The request waited the full budget, and said the change is not confirmed."""
    assert sum(clock.sleeps) == pytest.approx(explore.MODIFICATION_TIMEOUT)
    [(category, message)] = flashes(client)
    assert category == "info"
    assert f"The {kind} modification was sent" in message and NOT_CONFIRMED in message


def test_the_wait_is_never_longer_than_the_fixed_delay_it_replaced():
    assert explore.MODIFICATION_TIMEOUT <= 0.3


class TestModifySample:

    def test_an_applied_change_is_answered_without_sleeping(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")

        response = modify_sample(client, sample_version="1.0")

        assert response.status_code == 302
        assert response.location.endswith("/explore/samples")
        assert clock.sleeps == []
        assert fake_mcrit.sample.version == "1.0"
        assert flashes(client) == [("success", "Sample modified.")]

    def test_returning_to_a_cross_compare_is_not_delayed_by_a_second(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")

        response = modify_sample(client, sample_family_name="sed", redirection_job_id=CROSS_COMPARE_JOB_ID)

        assert response.status_code == 302
        assert response.location.endswith(f"/data/result/{CROSS_COMPARE_JOB_ID}")
        assert clock.sleeps == []
        assert fake_mcrit.sample.family == "sed"
        assert flashes(client) == [("success", "Sample modified.")]

    def test_the_request_waits_only_until_the_worker_has_run_the_job(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.lag = 2

        response = modify_sample(client, sample_is_library="on")

        assert response.status_code == 302
        assert clock.sleeps == [explore.MODIFICATION_POLL_INTERVAL] * 2
        assert fake_mcrit.sample.is_library is True
        assert flashes(client) == [("success", "Sample modified.")]

    def test_a_change_not_yet_applied_is_reported_as_unconfirmed(self, client, as_role, clock, fake_mcrit):
        """A busy worker: the request gives up after the timeout and says so, rather than
        announcing a change the page it lands on does not show."""
        as_role("contributor")
        fake_mcrit.lag = None

        response = modify_sample(client, sample_version="1.0", redirection_job_id=CROSS_COMPARE_JOB_ID)

        assert response.status_code == 302
        assert response.location.endswith(f"/data/result/{CROSS_COMPARE_JOB_ID}")
        assert_gave_up(clock, client, "sample")

    def test_a_failing_read_back_is_reported_as_unconfirmed_not_as_an_error_page(self, client, as_role, clock, fake_mcrit, logged):
        """By then the modification has been sent; a failed check must not turn into a 500."""
        as_role("contributor")
        fake_mcrit.read_error = requests.ConnectionError("backend went away")

        response = modify_sample(client, sample_version="1.0")

        assert response.status_code == 302
        assert clock.sleeps == []
        [(category, message)] = flashes(client)
        assert category == "info" and NOT_CONFIRMED in message
        assert f"modification of sample {SAMPLE_ID}" in logged.text

    def test_a_programming_error_in_the_read_back_is_not_swallowed(self, client, as_role, clock, fake_mcrit):
        """Only a failed request reads as "not confirmed"; a bug has to surface."""
        as_role("contributor")
        fake_mcrit.read_error = TypeError("a bug in the check")

        with pytest.raises(TypeError):
            modify_sample(client, sample_version="1.0")

    def test_a_change_not_accepted_is_reported_and_not_waited_for(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.rejects = True

        response = modify_sample(client, sample_family_name="-invalid-")

        assert response.status_code == 302
        assert clock.sleeps == []
        assert [call[0] for call in fake_mcrit.calls] == ["getSampleById", "modifySample"]
        assert flashes(client) == [("error", "MCRIT did not accept the sample modification.")]

    def test_an_unchanged_form_queues_nothing(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")

        response = modify_sample(client, sample_family_name="", sample_version="")

        assert response.status_code == 302
        assert clock.sleeps == []
        assert "modifySample" not in [call[0] for call in fake_mcrit.calls]
        assert flashes(client) == [("info", "Nothing to change.")]


class TestModifyFamily:

    def test_a_rename_is_seen_as_the_family_id_going_away(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")

        response = modify_family(client, family_new_name="sed189")

        assert response.status_code == 302
        assert response.location.endswith("/explore/families")
        assert clock.sleeps == []
        assert SED_FAMILY_ID not in fake_mcrit.families
        assert [entry.family_name for entry in fake_mcrit.families.values()].count("sed189") == 1
        assert flashes(client) == [("success", "Family modified.")]

    def test_a_rename_into_an_existing_family_merges_and_is_seen(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")

        response = modify_family(client, family_new_name="gzip")

        assert response.status_code == 302
        assert clock.sleeps == []
        assert SED_FAMILY_ID not in fake_mcrit.families
        assert fake_mcrit.families[GZIP_FAMILY_ID].num_samples == 2
        assert flashes(client) == [("success", "Family modified.")]

    def test_a_rename_waits_only_until_the_worker_has_run_the_job(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.lag = 3

        response = modify_family(client, family_new_name="sed189")

        assert response.status_code == 302
        assert clock.sleeps == [explore.MODIFICATION_POLL_INTERVAL] * 3
        assert SED_FAMILY_ID not in fake_mcrit.families
        assert flashes(client) == [("success", "Family modified.")]

    def test_a_library_flag_waits_only_until_the_worker_has_run_the_job(self, client, as_role, clock, fake_mcrit):
        """The form sends the current name along; unchanged, it is not part of the request."""
        as_role("contributor")
        fake_mcrit.lag = 3

        response = modify_family(client, family_new_name="sed", family_is_library="on")

        assert response.status_code == 302
        [modify_call] = [call for call in fake_mcrit.calls if call[0] == "modifyFamily"]
        assert modify_call[2] == {"family_name": None, "is_library": True}
        assert clock.sleeps == [explore.MODIFICATION_POLL_INTERVAL] * 3
        assert fake_mcrit.families[SED_FAMILY_ID].is_library is True
        assert flashes(client) == [("success", "Family modified.")]

    def test_renaming_family_0_is_seen_as_its_samples_moving_out(self, client, as_role, clock, fake_mcrit):
        """MongoDbStorage.modifyFamily keeps family 0's row, name and all, and zeroes its
        counters - so its id never goes away and its name never changes."""
        as_role("contributor")
        fake_mcrit.lag = 2

        response = modify_family(client, family_id=0, family_new_name="unsorted")

        assert response.status_code == 302
        assert clock.sleeps == [explore.MODIFICATION_POLL_INTERVAL] * 2
        assert fake_mcrit.families[0].family_name == ""
        assert fake_mcrit.families[0].num_samples == 0
        assert flashes(client) == [("success", "Family modified.")]

    def test_renaming_family_0_not_yet_applied_is_reported_as_unconfirmed(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.lag = None

        response = modify_family(client, family_id=0, family_new_name="unsorted")

        assert response.status_code == 302
        assert_gave_up(clock, client, "family")

    def test_a_library_flag_on_a_family_without_samples_is_not_waited_for(self, client, as_role, clock, fake_mcrit):
        """num_library_samples is set to num_samples, so an empty family never reads as
        a library; waiting for it to would always end as "not confirmed"."""
        as_role("contributor")
        fake_mcrit.lag = None

        response = modify_family(client, family_id=EMPTY_FAMILY_ID, family_new_name="empty", family_is_library="on")

        assert response.status_code == 302
        assert clock.sleeps == []
        assert flashes(client) == [("success", "Family modified.")]

    def test_a_change_not_yet_applied_is_reported_as_unconfirmed(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.lag = None

        response = modify_family(client, family_new_name="sed189")

        assert response.status_code == 302
        assert_gave_up(clock, client, "family")

    def test_a_failing_read_back_is_reported_as_unconfirmed_not_as_an_error_page(self, client, as_role, clock, fake_mcrit, logged):
        as_role("contributor")
        fake_mcrit.read_error = requests.ConnectionError("backend went away")

        response = modify_family(client, family_new_name="sed189")

        assert response.status_code == 302
        [(category, message)] = flashes(client)
        assert category == "info" and NOT_CONFIRMED in message
        assert f"modification of family {SED_FAMILY_ID}" in logged.text

    def test_a_rename_whose_read_back_fails_is_not_taken_for_the_family_going_away(self, client, as_role, clock, fake_mcrit):
        """getFamily answers None for a 500 as for a deleted row; only a 404 is the rename."""
        as_role("contributor")
        fake_mcrit.read_status = 500

        response = modify_family(client, family_new_name="sed189")

        assert response.status_code == 302
        assert_gave_up(clock, client, "family")

    def test_a_change_not_accepted_is_reported_and_not_waited_for(self, client, as_role, clock, fake_mcrit):
        as_role("contributor")
        fake_mcrit.rejects = True

        response = modify_family(client, family_new_name="-invalid-")

        assert response.status_code == 302
        assert clock.sleeps == []
        assert flashes(client) == [("error", "MCRIT did not accept the family modification.")]
