#!/usr/bin/python
"""Issue #193: a samples-table row asked `JobCollection.getJobsForSample` four times.

`getJobsForSample` (mcrit.queue.JobCollection) re-filters the whole job list on
every call - each `{% if %}`, `| length` and `[0]` in `sample_row.html` was its own
full scan. The macro actually needs only two distinct answers per row (badge count
via `matching_only=True`, and the "Last 1:N Job" link via
`method="getMatchesForSample", finished_only=True`), each asked twice: once in an
`{% if %}` and again to use the result. `single_sample.html` did the same for the
one row it renders.

This pins the count down to exactly one call per (sample, query) pair - two calls
per row on `/explore/samples` - rather than pinning it to a flat "one call", since
the two queries use different arguments and are not the same lookup.
"""

import logging
from collections import Counter

import pytest
from mcrit.queue.JobCollection import JobCollection

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def call_signature(sample_id, method=None, matching_only=False, finished_only=False):
    return (sample_id, method, matching_only, finished_only)


@pytest.fixture
def counted_calls(monkeypatch):
    """Every (sample_id, method, matching_only, finished_only) `getJobsForSample`
    was called with, recorded around the real method so rendering is unaffected."""
    calls = []
    original = JobCollection.getJobsForSample

    def counting(self, sample_id, method=None, matching_only=False, finished_only=False):
        calls.append(call_signature(sample_id, method, matching_only, finished_only))
        return original(self, sample_id, method=method, matching_only=matching_only, finished_only=finished_only)

    monkeypatch.setattr(JobCollection, "getJobsForSample", counting)
    return calls


def test_the_samples_listing_asks_each_query_once_per_row(client, as_role, fake_mcrit, counted_calls):
    as_role("visitor")

    response = client.get("/explore/samples")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    sample_ids = sorted({int(sample_id) for sample_id in fake_mcrit._samples})
    assert sample_ids, "the corpus has no samples to render rows for"
    for sample_id in sample_ids:
        assert f'id="sample_{sample_id}_analyze"' in page, f"sample {sample_id} did not render a row"

    counts = Counter(counted_calls)
    repeated = {signature: n for signature, n in counts.items() if n > 1}
    assert not repeated, f"a row asked the same query more than once: {repeated}"
    # two distinct queries (the badge count and the last-matches link), one call
    # each, for every row the page rendered
    assert len(counted_calls) == 2 * len(sample_ids), (
        f"expected {2 * len(sample_ids)} calls (2 per row), got {len(counted_calls)}: {counts}"
    )


def test_a_row_with_matching_jobs_still_shows_its_badge_and_link(client, as_role, fake_mcrit, counted_calls):
    """Sample 0 has a finished `getMatchesForSample` job in the fixture queue, which is
    exactly the case that used to double each call: both `{% if %}` branches are true,
    so the macro reads the result a second time to render it. Correctness, not just
    the call count, has to survive the merge."""
    as_role("visitor")

    response = client.get("/explore/samples")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="sample_0_analyze"' in page
    assert "Last 1:N Job" in page

    counts = Counter(counted_calls)
    for signature, n in counts.items():
        if signature[0] == 0:
            assert n == 1, f"sample 0's query {signature} ran {n} times"


def test_the_single_sample_page_asks_each_query_once(client, as_role, fake_mcrit, counted_calls):
    as_role("visitor")

    response = client.get("/explore/samples/0")

    assert response.status_code == 200
    assert "Last 1:N Job" in response.get_data(as_text=True)

    counts = Counter(counted_calls)
    repeated = {signature: n for signature, n in counts.items() if n > 1}
    assert not repeated, f"the single sample page asked the same query more than once: {repeated}"
    assert len(counted_calls) == 2, f"expected exactly 2 calls, got {len(counted_calls)}: {counts}"
