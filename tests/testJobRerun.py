#!/usr/bin/python
"""Rerunning a job from its job page - issue #55.

A job records the request that produced it: `payload["params"]` holds the arguments
the backend was called with, keyed by position ("0", "1", ...) for the positional
ones and by name for the rest. That is enough to submit the same request again - but
only for the methods where "the same request" is still a well-defined thing to ask
for, which is why these tests spend as much effort on the jobs that must *not* grow
a button as on the ones that must.

Three methods are rerunnable and each is pinned below:

  getMatchesForSample      params {"0": sample_id}
  getMatchesForSampleVs    params {"0": sample_id, "1": other_sample_id}
  combineMatchesToCross    params {"0": {sample_id: child_job_id}} - the sample ids
                           are there, but which comparison ran and with which
                           matching parameters is only on the children it combined

Everything else is excluded on purpose, and the exclusions are asserted rather than
described: a query job's binary lives in the backend's GridFS and cannot be resent
from a job record at all, a unique-blocks request has no force_recalculation, so
repeating it would hand back the very job being looked at, and the collection jobs
(addBinarySample, deleteSample, ...) are not analyses to repeat. Submitting a
request that differs from the one on screen is worse than offering no button: the
user believes they reran what they are looking at.

The rerun forces a recalculation. Without that flag mcrit's QueueRemoteCalls
answers from its descriptor cache and returns the *same job id*, so "Rerun" would
redirect back to the page it was clicked on and change nothing.
"""

import logging
import unittest

import pytest
from fixtureData import altered_job, inject_job, job_id_of, load

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: What the fake backend answers when a rerun is submitted. Deliberately not any of
#: the captured job ids, so "the page moved to a new job" is observable.
NEW_JOB_ID = "0123456789abcdef01234567"

#: The client calls a rerun is allowed to make.
QUEUEING_METHODS = ("requestMatchesForSample", "requestMatchesForSampleVs", "requestMatchesCross")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus, plus the request* methods a rerun submits through.

    The corpus client is the strict fake underneath, and the captured instance never
    had a rerun submitted against it, so it has no request* methods at all. These
    record the call and answer a job id, which is what the real client returns.
    """
    for method in QUEUEING_METHODS:
        def _queue(*args, _method=method, **kwargs):
            corpus_mcrit._record(_method, *args, **kwargs)
            return NEW_JOB_ID
        setattr(corpus_mcrit, method, _queue)
    return corpus_mcrit


def queued_calls(fake):
    return [call for call in fake.calls if call[0] in QUEUEING_METHODS]


def rerun_button_for(page, job_id):
    """Is the page offering a rerun of this job, as a POST?"""
    return f'data-post="/data/jobs/{job_id}/rerun"' in page


# --- what the job page offers ----------------------------------------------------

@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs", "cross_compare"])
def test_the_job_page_offers_a_rerun_for_a_reconstructible_job(client, as_role, report):
    as_role("visitor")
    job_id = job_id_of(report)
    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert rerun_button_for(page, job_id), f"{report} was not offered a rerun"


@pytest.mark.parametrize("report", ["matches_for_query", "unique_blocks"])
def test_the_job_page_offers_no_rerun_where_the_request_cannot_be_resent(client, as_role, report):
    """A query job's binary is only in the backend's GridFS - the job records a
    reference to it, not the bytes - and unique blocks has no force_recalculation,
    so repeating it would return the same finished job under the same id.
    """
    as_role("visitor")
    job_id = job_id_of(report)
    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert not rerun_button_for(page, job_id), f"{report} was offered a rerun it cannot honour"


def test_a_collection_job_is_not_offered_a_rerun(client, as_role, fake_mcrit):
    """addBinarySample and its siblings change the collection rather than analyse
    it. Resubmitting one is not a repeat of an analysis, and for the deletions it
    would be destructive."""
    as_role("visitor")
    job_id = next(entry["_id"]["$oid"] for entry in fake_mcrit._queue if entry["payload"]["method"] == "addBinarySample")
    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert not rerun_button_for(page, job_id)


def test_the_rerun_control_is_a_button_and_not_a_link(client, as_role):
    """An <a href> to a writing route is fired by middle-click and by prefetch,
    neither of which runs the click handler. See post_action.js and issue #84."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert f'href="/data/jobs/{job_id}/rerun"' not in page
    assert rerun_button_for(page, job_id)


def test_a_job_that_has_not_finished_is_not_offered_a_rerun(client, as_role, fake_mcrit):
    """Nothing to repeat yet: forcing a recalculation of a job that is still queued
    or running only queues the same work twice."""
    as_role("visitor")
    running = altered_job("matches_for_sample", "aaaaaaaaaaaaaaaaaaaaaaa1")
    running["finished_at"] = None
    running["progress"] = 0.5
    job_id = inject_job(fake_mcrit, running)

    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert not rerun_button_for(page, job_id)


@pytest.mark.parametrize(
    "state, fields",
    [
        ("queued", {"finished_at": None, "started_at": None, "progress": 0}),
        ("in progress", {"finished_at": None, "progress": 0.5}),
    ],
    ids=["queued", "in progress"],
)
def test_posting_a_rerun_for_an_unfinished_job_queues_nothing(client, as_role, fake_mcrit, state, fields):
    """Withholding the button only hides the door. The POST is reachable without it -
    by hand, or from a page rendered a moment before the job started - and forcing a
    recalculation then queues the same work alongside the run already in progress.

    So the terminal-state check belongs in the route, not only in what decides whether
    to draw the button. Found by Codex review of the first version, which had it in
    is_rerunnable() alone.
    """
    as_role("visitor")
    unfinished = altered_job("matches_for_sample", "aaaaaaaaaaaaaaaaaaaaaaa2")
    unfinished.update(fields)
    job_id = inject_job(fake_mcrit, unfinished)

    response = client.post(f"/data/jobs/{job_id}/rerun", follow_redirects=True)

    assert response.status_code == 200
    assert queued_calls(fake_mcrit) == [], f"a {state} job was rerun anyway"
    assert b"has not finished yet" in response.data


def test_a_failed_job_is_offered_a_rerun(client, as_role, fake_mcrit):
    """The case the button is most wanted for - a job that fell over and has to be
    tried again."""
    as_role("visitor")
    failed = altered_job("matches_for_sample", "aaaaaaaaaaaaaaaaaaaaaaa2")
    failed["finished_at"] = None
    failed["attempts_left"] = 0
    failed["last_error"] = "something went wrong"
    job_id = inject_job(fake_mcrit, failed)

    page = client.get(f"/data/jobs/{job_id}").get_data(as_text=True)

    assert rerun_button_for(page, job_id)


# --- what a rerun submits --------------------------------------------------------

def test_rerunning_a_1vsn_job_repeats_its_request(client, as_role, fake_mcrit):
    """The captured job is getMatchesForSample(0) with band_matches_required=2."""
    as_role("visitor")
    response = client.post(f"/data/jobs/{job_id_of('matches_for_sample')}/rerun")

    assert queued_calls(fake_mcrit) == [
        ("requestMatchesForSample", (0,), {"band_matches_required": 2, "force_recalculation": True})
    ]
    assert response.headers["Location"].startswith(f"/data/jobs/{NEW_JOB_ID}")


def test_rerunning_a_1vs1_job_repeats_both_sample_ids(client, as_role, fake_mcrit):
    """Both positions matter and they are not interchangeable: getMatchesForSampleVs
    renders sample_id as the report's own sample and other_sample_id as the one it
    is compared against, so a swap would silently produce the mirrored report."""
    as_role("visitor")
    client.post(f"/data/jobs/{job_id_of('matches_for_sample_vs')}/rerun")

    assert queued_calls(fake_mcrit) == [
        ("requestMatchesForSampleVs", (1, 3), {"band_matches_required": 2, "force_recalculation": True})
    ]


def test_rerunning_a_cross_compare_rebuilds_it_from_its_children(client, as_role, fake_mcrit):
    """The combineMatchesToCross job holds {sample_id: child_job_id} and nothing
    else. `sample_group_only` is recoverable only as the choice between the two
    child methods - the captured job's children are getMatchesForSampleVsGroup, so
    it was a group-only comparison - and the matching parameters likewise only exist
    on the children."""
    as_role("visitor")
    client.post(f"/data/jobs/{job_id_of('cross_compare')}/rerun")

    assert queued_calls(fake_mcrit) == [
        ("requestMatchesCross", ([0, 2, 4, 6, 1],),
         {"band_matches_required": 2, "sample_group_only": True, "force_recalculation": True})
    ]


def test_a_rerun_forces_a_recalculation(client, as_role, fake_mcrit):
    """The point of the whole feature. mcrit hashes method plus parameters into a
    descriptor and returns the cached job for a repeat, so a rerun that did not ask
    for a recalculation would hand back the job it was started from and look like a
    reload that did nothing."""
    as_role("visitor")
    client.post(f"/data/jobs/{job_id_of('matches_for_sample')}/rerun")

    _name, _args, kwargs = queued_calls(fake_mcrit)[0]
    assert kwargs["force_recalculation"] is True


def test_a_rerun_lands_on_the_new_job_and_polls_it(client, as_role):
    as_role("visitor")
    response = client.post(f"/data/jobs/{job_id_of('matches_for_sample')}/rerun")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/data/jobs/{NEW_JOB_ID}?refresh=3"


# --- what a rerun refuses to submit ----------------------------------------------

@pytest.mark.parametrize("report", ["matches_for_query", "unique_blocks"])
def test_posting_a_rerun_for_an_excluded_method_queues_nothing(client, as_role, fake_mcrit, report):
    """The button is not rendered for these, but the route is the thing that has to
    hold: a POST can be made without one."""
    as_role("visitor")
    job_id = job_id_of(report)
    response = client.post(f"/data/jobs/{job_id}/rerun")

    assert queued_calls(fake_mcrit) == []
    assert response.headers["Location"] == f"/data/jobs/{job_id}"


def test_a_cross_compare_missing_a_child_is_not_rerun(client, as_role, fake_mcrit):
    """Its matching parameters live on the children, so one that has been deleted
    from the queue takes them with it. Guessing the rest would submit a comparison
    the user did not ask for."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    child_job_id = load("cross_compare.job")["all_dependencies"][0]
    fake_mcrit._queued_by_id.pop(child_job_id)

    page = client.get(f"/data/jobs/{job_id}")
    response = client.post(f"/data/jobs/{job_id}/rerun")

    # the page has to survive that at all: it resolves every dependency, and
    # getJobData answers None for one that is gone, which used to be an
    # AttributeError on `.number` - a 500 for the exact job this button is about
    assert page.status_code == 200
    assert not rerun_button_for(page.get_data(as_text=True), job_id)
    assert queued_calls(fake_mcrit) == []
    assert response.headers["Location"] == f"/data/jobs/{job_id}"


def test_a_cross_compare_whose_children_disagree_is_not_rerun(client, as_role, fake_mcrit):
    """One request produced all of them, so they cannot legitimately differ. If they
    do, the job on screen is not describable as a single requestMatchesCross call
    and no rerun can reproduce it."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    child_job_id = load("cross_compare.job")["all_dependencies"][0]
    fake_mcrit._queued_by_id[child_job_id]["payload"]["params"] = '{"band_matches_required": 1, "0": 0, "1": [2, 4, 6, 1]}'

    response = client.post(f"/data/jobs/{job_id}/rerun")

    assert queued_calls(fake_mcrit) == []
    assert response.headers["Location"] == f"/data/jobs/{job_id}"


@pytest.mark.parametrize(
    "params",
    [
        "not json at all",
        '["band_matches_required", 2]',
        '{"band_matches_required": 2}',
        '{"band_matches_required": 2, "0": "0; DROP"}',
        '{"band_matches_required": "2", "0": 0}',
        '{"band_matches_required": true, "0": 0}',
        '{"band_matches_required": 2, "0": 0, "exclude_self_matches": true}',
        '{"band_matches_required": 2, "0": 0, "1": 5}',
    ],
    ids=[
        "unparseable", "not-a-mapping", "no-sample-id", "sample-id-not-an-int",
        "parameter-not-an-int", "parameter-a-bool", "unknown-named-parameter", "unknown-positional-parameter",
    ],
)
def test_a_payload_that_does_not_describe_the_request_is_not_rerun(client, as_role, fake_mcrit, params):
    """`Job.arguments` raises on the first two of these, so the rerun has to parse
    the payload itself rather than lean on it. The rest would each submit a request
    that is not the one on screen - dropping a matching parameter that could not be
    read changes the comparison, and a sample id that is not an integer is not a
    sample id at all. Every one of them ends as "no rerun", never as a best guess.

    The last two are the forward-looking half of the same rule. A parameter this code
    does not know about - a positional one it would not pass on, or a named one like
    `exclude_self_matches` that a later mcrit might record - would be dropped from the
    resubmitted call and change the comparison. Withholding the button is how a
    backend that grows a parameter announces itself, instead of quietly running
    something else.

    Asserted through the route rather than the page: `job_by_id` reads
    `job_info.parameters` for its heading long before any of this, so the first two
    payloads take the whole page down today. That is issue #55's neighbour, not its
    business - what matters here is that no request reaches the backend.
    """
    as_role("visitor")
    job_id = inject_job(fake_mcrit, altered_job("matches_for_sample", "aaaaaaaaaaaaaaaaaaaaaaa3", params=params))

    response = client.post(f"/data/jobs/{job_id}/rerun")

    assert queued_calls(fake_mcrit) == []
    assert response.headers["Location"] == f"/data/jobs/{job_id}"


def test_a_cross_compare_built_from_an_unknown_child_method_is_not_rerun(client, as_role, fake_mcrit):
    """`sample_group_only` is read off the child method and nothing else, so a child
    this code does not recognise leaves that choice unknown - and it is not a caching
    hint but which comparison runs (#97)."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    child_job_id = load("cross_compare.job")["all_dependencies"][0]
    fake_mcrit._queued_by_id[child_job_id]["payload"]["method"] = "getMatchesForSampleVsSomethingNew"

    response = client.post(f"/data/jobs/{job_id}/rerun")

    assert queued_calls(fake_mcrit) == []
    assert response.headers["Location"] == f"/data/jobs/{job_id}"


def test_a_job_id_nobody_knows_is_reported_not_crashed(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.post("/data/jobs/ffffffffffffffffffffffff/rerun")

    assert response.status_code == 302
    assert queued_calls(fake_mcrit) == []


def test_a_backend_that_refuses_the_rerun_does_not_redirect_to_a_missing_job(client, as_role, fake_mcrit):
    """`requestMatchesForSample` answers None when the backend refuses - a sample
    deleted since the original run is the obvious way to get there. Redirecting on
    that builds /data/jobs/None."""
    as_role("visitor")
    fake_mcrit.requestMatchesForSample = lambda *args, **kwargs: None
    job_id = job_id_of("matches_for_sample")

    response = client.post(f"/data/jobs/{job_id}/rerun")

    assert response.headers["Location"] == f"/data/jobs/{job_id}"


# --- method safety ---------------------------------------------------------------

def test_a_rerun_cannot_be_fired_by_get(client, as_role):
    """It queues backend work, so anything that makes a browser fetch a URL must not
    be able to start it - issue #84."""
    as_role("visitor")
    assert client.get(f"/data/jobs/{job_id_of('matches_for_sample')}/rerun").status_code == 405


if __name__ == "__main__":
    unittest.main()
