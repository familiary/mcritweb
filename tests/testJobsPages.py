#!/usr/bin/python
"""The job listings, and how often they ask the backend for the same thing.

Both listings build a lookup of the families and samples their jobs name. The
sample loop has always skipped ids it already holds; the family loop did not, so a
page of jobs all belonging to one family fetched that family once per job. See
issue #68.
"""

import copy
import json
import logging
import unittest

import pytest
from fixtureData import FIXTURES, job_id_of
from mcrit.queue.LocalQueue import Job

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def unique_blocks_jobs(count, family_ids):
    """`count` finished getUniqueBlocks jobs, each naming one of `family_ids`.

    getUniqueBlocks is the job kind the listings resolve a family for, and the
    captured queue happens to hold exactly one of them - not enough to show what
    happens when several jobs share a family.
    """
    template = json.loads((FIXTURES / "unique_blocks.job.json").read_text())
    jobs = []
    for index in range(count):
        entry = copy.deepcopy(template)
        entry["_id"] = {"$oid": f"{index:024d}"}
        entry["number"] = index
        family_id = family_ids[index % len(family_ids)]
        entry["payload"]["params"] = json.dumps({"family_id": family_id, "0": [0, 1, 2]})
        entry["payload"]["descriptor"] = json.dumps(["getUniqueBlocks", {"0": [0, 1, 2], "family_id": family_id}, {}])
        jobs.append(Job(entry, None))
    return jobs


def calls_to(backend, name):
    return [args for called, args, _kwargs in backend.calls if called == name]


def test_the_jobs_page_asks_for_each_family_once(client, as_role, corpus_mcrit, monkeypatch):
    jobs = unique_blocks_jobs(8, family_ids=[1])
    monkeypatch.setattr(corpus_mcrit, "getQueueData", lambda *args, **kwargs: jobs)
    as_role("visitor")
    corpus_mcrit.calls.clear()

    response = client.get("/data/jobs")

    assert response.status_code == 200
    assert calls_to(corpus_mcrit, "getFamily") == [(1,)], calls_to(corpus_mcrit, "getFamily")


def test_the_jobs_page_still_resolves_every_distinct_family(client, as_role, corpus_mcrit, monkeypatch):
    jobs = unique_blocks_jobs(9, family_ids=[1, 2, 3])
    monkeypatch.setattr(corpus_mcrit, "getQueueData", lambda *args, **kwargs: jobs)
    as_role("visitor")
    corpus_mcrit.calls.clear()

    response = client.get("/data/jobs")

    assert response.status_code == 200
    assert sorted(calls_to(corpus_mcrit, "getFamily")) == [(1,), (2,), (3,)]


def test_a_job_page_asks_for_each_child_jobs_family_once(client, as_role, corpus_mcrit, monkeypatch):
    """The same loop, over a job's dependencies rather than over the queue."""
    children = unique_blocks_jobs(8, family_ids=[1])
    parent = json.loads((FIXTURES / "unique_blocks.job.json").read_text())
    parent["all_dependencies"] = [child.job_id for child in children]
    parent["finished_at"] = None
    by_id = {child.job_id: child for child in children}
    parent_job = Job(parent, None)
    monkeypatch.setattr(
        corpus_mcrit, "getJobData",
        lambda job_id, *args, **kwargs: by_id.get(job_id, parent_job if job_id == parent_job.job_id else None),
    )
    as_role("visitor")
    corpus_mcrit.calls.clear()

    response = client.get(f"/data/jobs/{parent_job.job_id}")

    assert response.status_code == 200
    assert calls_to(corpus_mcrit, "getFamily") == [(1,)], calls_to(corpus_mcrit, "getFamily")


def test_a_job_page_still_renders_from_the_captured_queue(client, as_role):
    as_role("visitor")
    assert client.get(f"/data/jobs/{job_id_of('unique_blocks')}").status_code == 200


if __name__ == "__main__":
    unittest.main()
