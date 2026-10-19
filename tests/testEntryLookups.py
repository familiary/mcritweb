#!/usr/bin/python
"""How often a page asks the backend for one sample or family entry. Issue #191.

Each entry a page names used to cost a `getSampleById` (or `getFamily`) round trip, one
after the other, and several pages paid for the same one twice: the sample page fetched
its own sample again for the job table, the selection pages fetched samples the search
table beside them had just delivered, and the index fetched samples its "latest samples"
list had just delivered. `client.get_sample_entries` and `get_family_entries` keep what a
request has fetched, so each id costs at most one lookup, and `remember_samples` hands
them what the page already holds. What is still missing goes to the backend in one
`getSamplesByIds` / `getFamiliesByIds` request per call, however many ids it names. A
backend without those routes answers the batch with nothing, and then each id is asked
for on its own, as before.

The call counts here are paired with assertions on what the pages still show: a lookup
that stops happening is only a fix while the entry it fed is still rendered.
"""

import copy
import json
import logging
import re
from collections import Counter

import pytest
from fixtureData import job_id_of, load
from mcrit.queue.LocalQueue import Job

import mcritweb.views.data as data_views
from mcritweb.views.client import get_family_entries, get_sample_entries, remember_samples

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


#: the batch read that answers for each single lookup
BATCHED = {"getSampleById": "getSamplesByIds", "getFamily": "getFamiliesByIds"}


def lookups(fake_mcrit, name="getSampleById"):
    """id -> how often the request asked for it, on its own or as part of a batch."""
    asked = Counter(args[0] for called, args, kwargs in fake_mcrit.calls if called == name)
    for called, args, kwargs in fake_mcrit.calls:
        if called == BATCHED[name]:
            asked.update(args[0])
    return asked


def batches(fake_mcrit, name="getSamplesByIds"):
    """The id list of every batch request, in order."""
    return [list(args[0]) for called, args, kwargs in fake_mcrit.calls if called == name]


# --- the helper -----------------------------------------------------------------


def test_each_id_is_asked_for_once_per_request(app, fake_mcrit):
    with app.test_request_context():
        first = get_sample_entries([3, 5, 3])
        second = get_sample_entries([5, 7])

    assert lookups(fake_mcrit) == {3: 1, 5: 1, 7: 1}
    assert batches(fake_mcrit) == [[3, 5], [7]], "the misses of one call are one request"
    assert "getSampleById" not in [called for called, *_ in fake_mcrit.calls]
    assert list(first) == [3, 5] and first[3].sample_id == 3
    assert second[5] is first[5]


def test_an_id_the_backend_does_not_have_is_answered_none_and_not_asked_for_again(app, fake_mcrit):
    """An empty answer can't be told apart from a backend without the batch route, so
    the id is asked for once more on its own - and then not again in this request."""
    with app.test_request_context():
        assert get_sample_entries([99, 99]) == {99: None}
        assert get_sample_entries([99]) == {99: None}

    assert batches(fake_mcrit) == [[99]]
    assert lookups(fake_mcrit) == {99: 2}


def test_a_partial_answer_is_not_asked_for_again(app, fake_mcrit):
    """Some of the ids answered means the backend has the route: the rest are gone."""
    with app.test_request_context():
        assert get_sample_entries([3, 99]) == {3: fake_mcrit._samples[3], 99: None}

    assert lookups(fake_mcrit) == {3: 1, 99: 1}
    assert "getSampleById" not in [called for called, *_ in fake_mcrit.calls]


def test_a_remembered_sample_is_not_asked_for(app, fake_mcrit):
    with app.test_request_context():
        known = fake_mcrit._samples[4]
        remember_samples([known])
        assert get_sample_entries([4]) == {4: known}

    assert lookups(fake_mcrit) == {}


def test_families_are_asked_for_once_per_request(app, fake_mcrit):
    with app.test_request_context():
        families = get_family_entries([1, 1, 2])

    assert lookups(fake_mcrit, "getFamily") == {1: 1, 2: 1}
    assert batches(fake_mcrit, "getFamiliesByIds") == [[1, 2]]
    assert families[1].family_id == 1


def without_batch_route(fake_mcrit, monkeypatch, name):
    """What the client answers a backend older than mcrit 1.12: its 404 becomes {}."""
    def not_found(entry_ids, *args, **kwargs):
        fake_mcrit._record(name, list(entry_ids), *args, **kwargs)
        return {}
    monkeypatch.setattr(fake_mcrit, name, not_found, raising=False)


def test_a_backend_without_the_sample_batch_is_asked_per_id(app, fake_mcrit, monkeypatch):
    """mcrit 1.9.0 answers `POST /samples/ids` with a 404. Every entry used to map to
    None then, and pages showed their samples as missing."""
    without_batch_route(fake_mcrit, monkeypatch, "getSamplesByIds")
    with app.test_request_context():
        entries = get_sample_entries([3, 5, 99])
        again = get_sample_entries([3, 5])

    assert entries == {3: fake_mcrit._samples[3], 5: fake_mcrit._samples[5], 99: None}
    assert again == {3: fake_mcrit._samples[3], 5: fake_mcrit._samples[5]}
    singles = [args[0] for called, args, kwargs in fake_mcrit.calls if called == "getSampleById"]
    assert singles == [3, 5, 99], "each id on its own, once"


def test_a_backend_without_the_family_batch_is_asked_per_id_without_sample_lists(app, fake_mcrit, monkeypatch):
    without_batch_route(fake_mcrit, monkeypatch, "getFamiliesByIds")
    with app.test_request_context():
        families = get_family_entries([1, 2])

    assert families == {1: fake_mcrit._families[1], 2: fake_mcrit._families[2]}
    singles = [(args[0], kwargs) for called, args, kwargs in fake_mcrit.calls if called == "getFamily"]
    assert singles == [(1, {"with_samples": False}), (2, {"with_samples": False})]


def test_a_failed_batch_and_failed_single_lookups_answer_none_for_each_id(app, fake_mcrit, monkeypatch):
    """The client answers {} and None when requests fail, which read as "none of
    these" - the same None a failed single lookup always answered."""
    without_batch_route(fake_mcrit, monkeypatch, "getSamplesByIds")
    monkeypatch.setattr(fake_mcrit, "getSampleById", lambda sample_id, *args, **kwargs: None, raising=False)
    with app.test_request_context():
        assert get_sample_entries([3, 5]) == {3: None, 5: None}


def test_nothing_is_kept_beyond_the_request(app, fake_mcrit):
    with app.test_request_context():
        get_sample_entries([3])
    with app.test_request_context():
        get_sample_entries([3])

    assert lookups(fake_mcrit) == {3: 2}


# --- the pages ------------------------------------------------------------------


def test_the_sample_page_does_not_fetch_its_own_sample_again(client, as_role, fake_mcrit):
    """Every job the page lists names the page's sample - that is the filter."""
    as_role("visitor")
    response = client.get("/explore/samples/0")

    assert response.status_code == 200
    asked = lookups(fake_mcrit)
    assert asked[0] == 1, "the page's own sample was fetched again for its job table"
    # the other samples its jobs in the captured queue name: the unique blocks job over
    # 0, 1 and 2 - each asked for once
    assert asked == {0: 1, 1: 1, 2: 1}


@pytest.mark.parametrize("path", ["/analyze/cross_compare", "/analyze/unique_blocks"])
def test_a_selection_listed_in_the_search_table_is_not_looked_up_again(client, as_role, fake_mcrit, path):
    as_role("visitor")
    response = client.get(f"{path}?samples=0,1,2")

    assert response.status_code == 200
    assert lookups(fake_mcrit) == {}, "the search on the same page already delivered these"
    body = response.get_data(as_text=True)
    for sample_id in (0, 1, 2):
        assert fake_mcrit._samples[sample_id].sha256[:8] in body


@pytest.mark.parametrize("path", ["/analyze/cross_compare", "/analyze/unique_blocks"])
def test_a_selection_off_the_search_page_is_looked_up_once_and_shown(client, as_role, fake_mcrit, path):
    """The search shows ten rows, sample_id ascending, so 11 is not among them."""
    as_role("visitor")
    response = client.get(f"{path}?samples=0,11")

    assert response.status_code == 200
    assert lookups(fake_mcrit) == {11: 1}
    assert fake_mcrit._samples[11].sha256[:8] in response.get_data(as_text=True)


def test_an_unknown_selection_is_dropped_from_a_cross_compare_after_one_lookup(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get("/analyze/cross_compare?samples=0,99")

    assert response.status_code == 302
    assert "samples=0&" in response.headers["Location"]
    assert lookups(fake_mcrit) == {99: 2}, "the batch, then the one lookup an empty answer costs"


def flashes(client):
    """(category, message) for everything flashed and not yet rendered."""
    with client.session_transaction() as session:
        return list(session.get("_flashes", []))


def failing_search(*args, **kwargs):
    return None


def test_a_failed_search_still_resolves_the_selection_in_order(client, as_role, fake_mcrit, monkeypatch):
    """The search now runs before the selection is looked up. When it fails there is
    nothing to take the selection from, so every selected sample is looked up, and the
    messages come in the order they did before: the unconfirmed sample, then the search."""
    monkeypatch.setattr(fake_mcrit, "search_samples", failing_search, raising=False)
    as_role("visitor")
    response = client.get("/analyze/unique_blocks?samples=0,99")

    assert response.status_code == 200
    assert lookups(fake_mcrit) == {0: 1, 99: 1}
    body = response.get_data(as_text=True)
    assert fake_mcrit._samples[0].sha256[:8] in body
    unconfirmed = body.index("MCRIT did not confirm sample id 99")
    search_failed = body.index("search for  in MCRIT&#39;s samples failed")
    assert unconfirmed < search_failed


def test_a_failed_search_does_not_add_a_message_to_the_unknown_id_redirect(client, as_role, fake_mcrit, monkeypatch):
    """cross_compare redirects away from an unknown id before it reports the search,
    as it did when the search ran after the lookup - the page it redirects to searches
    again and reports its own outcome."""
    monkeypatch.setattr(fake_mcrit, "search_samples", failing_search, raising=False)
    as_role("visitor")
    response = client.get("/analyze/cross_compare?samples=0,99")

    assert response.status_code == 302
    assert flashes(client) == [("warning", "Sample with Id 99 does not exist and was ignored")]


def test_a_deleted_sample_still_marks_a_cross_compare_corrupted(client, as_role, fake_mcrit, monkeypatch):
    """The cross result now looks up its whole sequence at once, through the helper;
    a sample that is gone still makes the report unrenderable rather than short."""
    ask = fake_mcrit.getSamplesByIds

    def without_sample_4(sample_ids, *args, **kwargs):
        return {sample_id: entry for sample_id, entry in ask(sample_ids, *args, **kwargs).items() if sample_id != 4}

    monkeypatch.setattr(fake_mcrit, "getSamplesByIds", without_sample_4, raising=False)
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('cross_compare')}")

    assert response.status_code == 200
    assert "not able to retrieve information for all samples" in response.get_data(as_text=True)
    assert set(lookups(fake_mcrit).values()) == {1}, "a sample of the sequence was asked for twice"


def test_the_job_page_resolves_its_samples_through_the_request_lookup(client, as_role, fake_mcrit, monkeypatch):
    """Two sub-jobs naming one sample cost one lookup, and a sample the request already
    knows costs none - the latter is what going through the helper adds."""
    parent_document = copy.deepcopy(load("cross_compare.job"))
    children = {job.job_id: job for job in (matching_job(3, 40), matching_job(3, 41), matching_job(5, 42))}
    parent_document["all_dependencies"] = list(children)
    parent = Job(parent_document, None)
    monkeypatch.setattr(fake_mcrit, "getJobData", lambda job_id, *args, **kwargs: parent if job_id == "parent" else None, raising=False)
    monkeypatch.setattr(fake_mcrit, "getQueueData", dependency_reader(fake_mcrit, children), raising=False)
    helper = data_views.get_sample_entries

    def remembering_first(sample_ids):
        remember_samples([fake_mcrit._samples[5]])
        return helper(sample_ids)

    monkeypatch.setattr(data_views, "get_sample_entries", remembering_first)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert lookups(fake_mcrit) == {3: 1}
    assert dependency_reads(fake_mcrit) == [list(children)], "the dependencies are one request"
    body = response.get_data(as_text=True)
    for sample_id in (3, 5):
        assert fake_mcrit._samples[sample_id].sha256[:8] in body


def dependency_reader(fake_mcrit, jobs_by_id, extra=()):
    """A `getQueueData` answering `job_ids=` from `jobs_by_id`, plus `extra` - what a
    backend that ignores the selector would add to it."""
    def read(*args, job_ids=None, **kwargs):
        fake_mcrit._record("getQueueData", *args, job_ids=job_ids, **kwargs)
        return [jobs_by_id[job_id] for job_id in job_ids or [] if job_id in jobs_by_id] + list(extra)
    return read


def dependency_reads(fake_mcrit):
    return [kwargs.get("job_ids") for called, args, kwargs in fake_mcrit.calls if called == "getQueueData"]


def parent_with_dependencies(fake_mcrit, monkeypatch, job_ids):
    parent_document = copy.deepcopy(load("cross_compare.job"))
    parent_document["all_dependencies"] = list(job_ids)
    parent = Job(parent_document, None)
    monkeypatch.setattr(fake_mcrit, "getJobData", lambda job_id, *args, **kwargs: parent if job_id == "parent" else None, raising=False)


def test_a_dependency_that_is_gone_is_counted_as_missing(client, as_role, fake_mcrit, monkeypatch):
    present = matching_job(3, 40)
    parent_with_dependencies(fake_mcrit, monkeypatch, [present.job_id, "b" * 24, "c" * 24])
    monkeypatch.setattr(fake_mcrit, "getQueueData", dependency_reader(fake_mcrit, {present.job_id: present}), raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert re.search(r"2 of this job(&#39;|')s 3 sub-jobs\s+are no longer in the system", response.get_data(as_text=True))


def test_a_backend_that_ignores_the_selector_does_not_add_jobs(client, as_role, fake_mcrit, monkeypatch):
    """An older mcrit answers `job_ids=` with the whole queue; only the jobs asked for
    are dependencies."""
    present = matching_job(3, 40)
    parent_with_dependencies(fake_mcrit, monkeypatch, [present.job_id])
    stranger = matching_job(12, 99)
    monkeypatch.setattr(fake_mcrit, "getQueueData", dependency_reader(fake_mcrit, {present.job_id: present}, extra=[stranger]), raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert stranger.job_id not in response.get_data(as_text=True)
    assert lookups(fake_mcrit) == {3: 1}


def test_a_failed_dependency_read_counts_every_dependency_as_missing(client, as_role, fake_mcrit, monkeypatch):
    parent_with_dependencies(fake_mcrit, monkeypatch, ["a" * 24, "b" * 24])
    monkeypatch.setattr(fake_mcrit, "getQueueData", lambda *args, **kwargs: None, raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert re.search(r"2 of this job(&#39;|')s 2 sub-jobs\s+are no longer in the system", response.get_data(as_text=True))


def test_many_dependencies_are_read_in_requests_of_a_hundred(client, as_role, fake_mcrit, monkeypatch):
    """A cross compare of MAX_SELECTED_SAMPLES samples has 250 sub-jobs. Their ids in one
    query string make a request line of about 6,250 bytes, which gunicorn's default
    limit_request_line of 4,094 refuses; every dependency then read as missing."""
    children = {job.job_id: job for job in (matching_job(3, number) for number in range(1000, 1250))}
    parent_with_dependencies(fake_mcrit, monkeypatch, list(children))
    monkeypatch.setattr(fake_mcrit, "getQueueData", dependency_reader(fake_mcrit, children), raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    reads = dependency_reads(fake_mcrit)
    assert [len(read) for read in reads] == [100, 100, 50]
    assert [job_id for read in reads for job_id in read] == list(children)
    for read in reads:
        request_line = f"GET /jobs/?job_ids={','.join(read)} HTTP/1.1"
        assert len(request_line) < 4094
    body = response.get_data(as_text=True)
    assert "are no longer in the system" not in body
    assert lookups(fake_mcrit) == {3: 1}


def test_a_failed_read_loses_only_its_own_dependencies(client, as_role, fake_mcrit, monkeypatch):
    children = {job.job_id: job for job in (matching_job(3, number) for number in range(1000, 1150))}
    parent_with_dependencies(fake_mcrit, monkeypatch, list(children))
    read = dependency_reader(fake_mcrit, children)

    def second_read_fails(*args, job_ids=None, **kwargs):
        answer = read(*args, job_ids=job_ids, **kwargs)
        return None if len(dependency_reads(fake_mcrit)) == 2 else answer

    monkeypatch.setattr(fake_mcrit, "getQueueData", second_read_fails, raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert re.search(r"50 of this job(&#39;|')s 150 sub-jobs\s+are no longer in the system", response.get_data(as_text=True))


def test_a_backend_that_ignores_the_selector_is_read_once(client, as_role, fake_mcrit, monkeypatch):
    """An older mcrit answers each `job_ids=` read with the whole queue. The first such
    answer already holds every dependency there is, so there is no second read of it."""
    children = {job.job_id: job for job in (matching_job(3, number) for number in range(1000, 1250))}
    parent_with_dependencies(fake_mcrit, monkeypatch, list(children))
    stranger = matching_job(12, 99)

    def whole_queue(*args, job_ids=None, **kwargs):
        fake_mcrit._record("getQueueData", *args, job_ids=job_ids, **kwargs)
        return list(children.values()) + [stranger]

    monkeypatch.setattr(fake_mcrit, "getQueueData", whole_queue, raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert len(dependency_reads(fake_mcrit)) == 1
    body = response.get_data(as_text=True)
    assert "are no longer in the system" not in body
    assert stranger.job_id not in body


def unique_blocks_job(family_id, number):
    """A finished unique blocks job restricted to `family_id` - the one kind of sub-job
    that names a family - in the captured queue's own shape."""
    document = copy.deepcopy(next(entry for entry in load("queue") if entry["payload"]["method"] == "getUniqueBlocks"))
    document["_id"] = {"$oid": f"{number:024x}"}
    document["number"] = number
    document["payload"]["params"] = json.dumps({"family_id": family_id, "0": [0, 1, 2]})
    return Job(document, None)


def test_the_job_page_asks_for_its_families_in_one_request(client, as_role, fake_mcrit, monkeypatch):
    jobs = (unique_blocks_job(1, 40), unique_blocks_job(2, 41), unique_blocks_job(1, 42), matching_job(3, 43))
    children = {job.job_id: job for job in jobs}
    parent_with_dependencies(fake_mcrit, monkeypatch, list(children))
    monkeypatch.setattr(fake_mcrit, "getQueueData", dependency_reader(fake_mcrit, children), raising=False)
    as_role("visitor")
    response = client.get("/data/jobs/parent")

    assert response.status_code == 200
    assert batches(fake_mcrit, "getFamiliesByIds") == [[1, 2]]
    assert "getFamily" not in [called for called, *_ in fake_mcrit.calls]
    body = response.get_data(as_text=True)
    for family_id in (1, 2):
        assert fake_mcrit._families[family_id].family_name in body


def matching_job(sample_id, number):
    """A finished 1vN job for `sample_id`, in the captured queue's own shape."""
    document = copy.deepcopy(next(entry for entry in load("queue") if entry["payload"]["method"] == "getMatchesForSample"))
    document["_id"] = {"$oid": f"{number:024x}"}
    document["number"] = number
    document["payload"]["params"] = json.dumps({"band_matches_required": 2, "0": sample_id})
    return Job(document, None)


def test_the_index_asks_for_each_matched_sample_once(client, as_role, fake_mcrit, monkeypatch):
    """Two recent jobs for sample 3 and one for sample 12, which is also among the five
    latest samples the index lists anyway."""
    jobs = [matching_job(3, 30), matching_job(12, 31), matching_job(3, 32)]
    monkeypatch.setattr(fake_mcrit, "getQueueData", lambda *args, **kwargs: jobs, raising=False)
    as_role("visitor")
    response = client.get("/")

    assert response.status_code == 200
    assert lookups(fake_mcrit) == {3: 1}, "a latest sample was fetched again, or one sample twice"
    body = response.get_data(as_text=True)
    for sample_id in (3, 12):
        assert fake_mcrit._samples[sample_id].sha256[:8] in body
