#!/usr/bin/python
"""What the explore pages ask the backend for, and what they still show for it.

Issue #77 is "searching for samples is slow". Two of the three backend calls behind
`/explore/samples` have nothing to do with the query: the view downloaded the whole
job queue to annotate at most 25 rows, and the whole family table to fill a
type-ahead in a modal nobody had opened. A queue and a family table both grow
monotonically with instance use, so both cost more on exactly the instances where
the complaint comes from.

Issue #76 is the neighbouring one: `/explore/search` fans out to all three
collections, and the function search is the slow one - ~30 seconds on a large
database, worst when it finds nothing. That cost is mcrit's to fix; what is fixable
here is who is charged for it.

These tests are call counts, and they are paired deliberately with assertions on what
the page still renders. Narrowing a fetch is only a fix while the annotation it fed
survives - a listing that quietly stops showing which samples have matching jobs is a
regression, not an improvement, and a search that quietly stops looking at functions
is one too.
"""

import copy
import json
import logging
import re

import pytest
from mcrit.queue.LocalQueue import Job

from mcritweb.views.explore import (
    FAMILY_NAME_SUGGESTIONS,
    SAMPLE_ROW_JOB_METHODS,
    sample_row_job_collection,
)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: `sample_row` renders this when a sample has matching jobs, with the count after it.
JOB_BADGE = re.compile(r'id="sample_(-?\d+)_analyze".*?</button>', re.DOTALL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def calls_to(fake_mcrit, name):
    return [(args, kwargs) for called, args, kwargs in fake_mcrit.calls if called == name]


#: The methods a job badge can ever count, taken from the installed mcrit rather than
#: from mcritweb: `sample_row` asks for `matching_only=True` and gets an answer only
#: where `Job.has_sample_id` has one, which is the matching methods that carry a sample
#: id as an argument. `combineMatchesToCross` is matching but is not one of them.
MATCHING_SAMPLE_METHODS = sorted(
    set(Job({}, None).method_types["matching"]) & set(Job({}, None).method_types["sample_id"])
)


def badges(response):
    """sample_id -> the number in its job badge, for every row that has one."""
    found = {}
    for match in JOB_BADGE.finditer(response.get_data(as_text=True)):
        if "color:green" in match.group(0):
            found[int(match.group(1))] = int(re.search(r"&nbsp;(\d+)", match.group(0)).group(1))
    return found


def rows_on(response):
    """The sample ids the page rendered a row for, badge or no badge."""
    return [int(sample_id) for sample_id in re.findall(r'id="sample_(-?\d+)_analyze"', response.get_data(as_text=True))]


def expected_badges(queue, page_sample_ids):
    """sample_id -> the badge count `sample_row` owes each row, read off the raw queue.

    Written out here rather than taken from `JobCollection`, which is what the view
    builds and the macro asks: an expectation computed by the code under test cannot
    disagree with it. The rule is the one the listing implements - it keeps the jobs
    whose *own* sample id (their first argument) is on the page, and each of those then
    badges every sample it names, which for a `...Vs` job is two of them.
    """
    counts = {}
    for document in queue:
        method = document["payload"]["method"]
        if method not in MATCHING_SAMPLE_METHODS:
            continue
        arguments = json.loads(document["payload"]["params"])
        named = [int(arguments["0"])]
        if method == "getMatchesForSampleVs":
            named.append(int(arguments["1"]))
        if named[0] not in page_sample_ids:
            continue
        for sample_id in named:
            if sample_id in page_sample_ids:
                counts[sample_id] = counts.get(sample_id, 0) + 1
    return counts


def requeued(document, number, sample_id):
    """A captured queue document moved to another position in the queue.

    The shape comes from the corpus rather than from a literal here, so a job that
    the deserialiser stops accepting fails this the way it fails everything else.
    Only the two fields the ordering rests on are rewritten: the submission counter
    and the first argument, which is what `Job.sample_id` reads.
    """
    moved = copy.deepcopy(document)
    moved["number"] = number
    moved["_id"] = {"$oid": f"{number:024x}"}
    params = json.loads(moved["payload"]["params"])
    params["0"] = sample_id
    moved["payload"]["params"] = json.dumps(params)
    return Job(moved, None)


class QueueByMethod:
    """A backend that answers `getQueueData(method=...)` from a prepared queue.

    Deliberately not the corpus client: the captured queue holds exactly one job per
    matching method, which cannot distinguish any ordering from any other.
    """

    def __init__(self, jobs_by_method):
        self.jobs_by_method = jobs_by_method

    def getQueueData(self, method=None, **kwargs):
        return list(self.jobs_by_method[method])


# --- the queue is fetched by method, not wholesale --------------------------------

@pytest.mark.parametrize("path", ["/explore/samples", "/explore/samples?query=citadel"])
def test_a_listing_asks_the_queue_only_for_the_methods_its_rows_can_show(client, as_role, fake_mcrit, path):
    """An unqualified `getQueueData()` is the whole queue: `McritClient` omits `limit`
    from the query string when it is 0, and mcrit reads a missing limit as "all". The
    two methods named here are the only ones `sample_row` can reach - `has_sample_id`
    answers for nothing else - so the rest was fetched and dropped."""
    as_role("visitor")
    fake_mcrit.calls.clear()

    client.get(path)

    methods = sorted(kwargs.get("method") for _args, kwargs in calls_to(fake_mcrit, "getQueueData"))
    assert methods == ["getMatchesForSample", "getMatchesForSampleVs"], (
        f"{path} asked the queue for {methods}"
    )


def test_the_named_methods_are_the_ones_a_row_can_show():
    """`SAMPLE_ROW_JOB_METHODS` is a hand-copy of an intersection that lives in mcrit,
    and the two are tied together by nothing but this. Today the queue's
    `getMatchesForSampleVsGroup` jobs are dropped either way, because `Job` does not
    know the method at all and answers `sample_id` with None - so if mcrit ever teaches
    `Job` about it, the badge would silently stop counting it and the row would go
    blank, with no other test to say so. A version bump should fail here instead."""
    assert sorted(SAMPLE_ROW_JOB_METHODS) == MATCHING_SAMPLE_METHODS, (
        "the installed mcrit's matching methods that carry a sample id have changed"
    )


def test_a_listing_with_no_rows_asks_the_queue_for_nothing(client, as_role, fake_mcrit):
    """`/explore/families/1` is the example because the offline fake makes it one: it
    models search as a substring match, not mcrit's `field:value` parser, so the
    `family_id:1` query the view builds matches no sample. A page with no rows has
    nothing to annotate either way."""
    as_role("visitor")
    fake_mcrit.calls.clear()

    response = client.get("/explore/families/1")

    assert response.status_code == 200
    assert calls_to(fake_mcrit, "getQueueData") == []


def test_the_listing_still_shows_which_samples_have_matching_jobs(client, as_role, fake_mcrit):
    """The counterweight to the test above: the narrowed fetch has to reach every job
    the whole-queue fetch reached. The expectation is read off the captured queue, so a
    regenerated fixture moves it rather than breaking it."""
    as_role("visitor")

    response = client.get("/explore/samples")

    # against the whole corpus rather than against the rows that came back: an
    # expectation read off the page under test would shrink with it, and a listing that
    # lost the annotated row would agree with itself
    expected = expected_badges(fake_mcrit._queue, sorted(fake_mcrit._samples))
    assert expected, "the captured queue annotates no sample in the corpus - nothing is being asserted"
    assert set(expected) <= set(rows_on(response)), "a sample the queue annotates is not on this page"
    assert badges(response) == expected


def test_fetching_the_queue_per_method_still_answers_newest_first(corpus_mcrit):
    """One request per method returns each method's jobs newest-first, so simply
    concatenating the answers is newest-first only *within* a method - the whole-queue
    order the single request used to answer in is gone. `sample_row_job_collection`
    sorts it back, and this is the test the page-level ones cannot be: the captured
    queue holds exactly one job per matching method, so `[0]` of it is the same job
    whatever the order.

    Driven at the function rather than through a page because nothing in a rendered
    listing depends on the order today - `sample_row.html` re-filters by `method=` and
    takes `[0]` of that - so the guard has to sit where the ordering is decided.
    """
    on_page = corpus_mcrit.getSampleById(0)
    captured = {
        method: next(doc for doc in corpus_mcrit._queue if doc["payload"]["method"] == method)
        for method in SAMPLE_ROW_JOB_METHODS
    }
    # one old job and one new one per method, interleaved across the methods, each
    # method answered newest-first the way the backend answers it
    jobs_by_method = {
        method: [requeued(captured[method], 100 + offset, on_page.sample_id),
                 requeued(captured[method], offset, on_page.sample_id)]
        for offset, method in enumerate(SAMPLE_ROW_JOB_METHODS)
    }
    concatenated = [job.number for method in SAMPLE_ROW_JOB_METHODS for job in jobs_by_method[method]]
    assert concatenated != sorted(concatenated, reverse=True), (
        "the prepared queue cannot tell a concatenation from a newest-first merge"
    )

    jobs = sample_row_job_collection(QueueByMethod(jobs_by_method), [on_page]).getJobs()

    numbers = [job.number for job in jobs]
    assert numbers == sorted(numbers, reverse=True), f"the merged queue came back as {numbers}"
    assert sorted(numbers) == sorted(concatenated), "the merge lost or duplicated a job"


def test_the_listing_still_links_the_last_matching_job(client, as_role, fake_mcrit):
    """The dropdown's "Last 1:N Job" is the other thing the queue feeds. It re-filters
    by `method=` and takes `[0]`, so what this can show is that the link is still
    rendered and still points at a real job; the ordering the `[0]` rests on is pinned
    by `test_fetching_the_queue_per_method_still_answers_newest_first` instead, since
    the captured queue holds only one job of this method."""
    as_role("visitor")
    job_id = next(
        job["_id"]["$oid"] for job in fake_mcrit._queue
        if job["payload"]["method"] == "getMatchesForSample"
    )

    response = client.get("/explore/samples")

    assert f"/data/result/{job_id}" in response.get_data(as_text=True)


def test_a_failed_queue_read_costs_the_annotations_not_the_page(client, as_role, fake_mcrit):
    """`McritClient` answers None when the backend errors, and `JobCollection(None)`
    used to take the request down with a TypeError. The rows are the page; the job
    badges are decoration on them."""
    as_role("visitor")
    fake_mcrit.getQueueData = lambda *args, **kwargs: None

    response = client.get("/explore/samples")

    assert response.status_code == 200
    assert badges(response) == {}
    assert "job queue failed" in response.get_data(as_text=True)


def test_half_a_queue_read_is_no_queue_read(client, as_role, fake_mcrit):
    """The listing asks for two methods. If only one answers, keeping it renders
    badges that count some of a sample's matching jobs and not others, and the
    "Last 1:N Job" link comes from only one of the two - so the half-answer is not a
    smaller true result, it is a wrong one, shown with nothing to say it is wrong.

    Reported by Codex on the PR for issue #77.
    """
    as_role("visitor")
    real_get_queue_data = fake_mcrit.getQueueData

    def one_method_fails(*args, **kwargs):
        if kwargs.get("method") == "getMatchesForSampleVs":
            return None
        return real_get_queue_data(*args, **kwargs)

    fake_mcrit.getQueueData = one_method_fails

    response = client.get("/explore/samples")

    assert response.status_code == 200
    assert badges(response) == {}, "kept the half of the queue that answered"
    assert "job queue failed" in response.get_data(as_text=True)


# --- the single sample page narrows by sample id ----------------------------------

def test_the_single_sample_page_forwards_its_sample_id_as_a_filter(client, as_role, fake_mcrit):
    """`filter=sample_id` was passed as an int, and `McritClient.getQueueData` forwards
    a `filter` only when `isinstance(filter, str)` - so the argument was dropped and
    the whole queue came back anyway."""
    as_role("visitor")
    fake_mcrit.calls.clear()

    client.get("/explore/samples/0")

    assert [kwargs.get("filter") for _args, kwargs in calls_to(fake_mcrit, "getQueueData")] == ["0"]


def test_the_single_sample_page_still_lists_every_job_of_the_sample(client, as_role, fake_mcrit):
    """mcrit matches `filter` as a substring of the job's `method(args...)` string,
    which is a superset of the jobs whose own `sample_id` is this one - `Job.sample_id`
    is read from those very arguments - so `filterToSampleIds` still lands exactly.

    The expectation is what the unnarrowed fetch would have found: every job in the
    whole captured queue that `filterToSampleIds` keeps for this sample. Nothing in it
    is written down here, so regenerating the fixture moves the target rather than
    breaking the test."""
    as_role("visitor")
    of_this_sample = [job for job in (Job(document, None) for document in fake_mcrit._queue) if job.sample_id == 0]

    page = client.get("/explore/samples/0").get_data(as_text=True)

    assert of_this_sample, "the captured queue holds no job for sample 0"
    assert f'Sample has <a href="#jobs">{len(of_this_sample)} Job' in page
    for job in of_this_sample:
        assert job.job_id in page, f"{job.method} vanished from the job table"


def test_the_single_sample_page_survives_a_failed_function_search(client, as_role, fake_mcrit):
    """The job table is read outside the branch that fills it, so a failed function
    search reached an unbound `job_collection` - a 500 on the page whose job it was to
    report the failure. Pre-existing; fixed while narrowing the fetch around it."""
    as_role("visitor")
    fake_mcrit.search_functions = lambda *args, **kwargs: None

    response = client.get("/explore/samples/0")

    assert response.status_code == 200
    # #113 replaced "Ups, search ... failed!" with wording naming the backend; what
    # matters here is that the page renders *and* reports, not which sentence it uses.
    assert "the backend did not answer" in response.get_data(as_text=True)


def test_the_single_sample_page_survives_a_failed_queue_read(client, as_role, fake_mcrit):
    """`JobCollection(None)` is a TypeError one attribute access later."""
    as_role("visitor")
    fake_mcrit.getQueueData = lambda *args, **kwargs: None

    response = client.get("/explore/samples/0")

    assert response.status_code == 200
    assert "job queue failed" in response.get_data(as_text=True)


# --- the fake's fidelity, which the tests above rest on ---------------------------

def test_the_fake_applies_the_queue_filter_after_paging_like_mcrit_does(corpus_mcrit):
    """`QueueRemoteCalls.getQueueData` filters the *already paged* slice, so a `filter`
    combined with a `limit` silently returns fewer rows than the limit asked for
    instead of the first `limit` matches. Nothing may lean on `filter` for paging, and
    the fake has to make that visible rather than hide it.

    `addBinarySample` is the demonstration because the captured queue's submissions are
    its oldest jobs, so none of them is on the newest-first first page - which is what
    makes an empty answer to a filter that matches plenty the visible symptom. Both of
    those premises are asserted rather than assumed, so a regenerated fixture that no
    longer has them says so."""
    limit = 5
    first_page = corpus_mcrit.getQueueData(limit=limit)
    unpaged = corpus_mcrit.getQueueData(filter="addBinarySample")
    paged = corpus_mcrit.getQueueData(filter="addBinarySample", limit=limit)

    assert len(unpaged) > limit, "the captured queue has too few submissions to page past"
    assert not any(job.method == "addBinarySample" for job in first_page), (
        "the captured queue now opens with a submission - filter-after-paging would find it"
    )
    assert paged == [], "the fake paged the matches instead of matching the page"


# --- the family type-ahead is fetched, not embedded -------------------------------

@pytest.mark.parametrize("path", ["/explore/families", "/explore/samples", "/explore/families/1", "/explore/samples/0"])
def test_a_page_with_an_edit_modal_does_not_download_every_family(client, as_role, fake_mcrit, path):
    """`getFamilies()` is one storage lookup per family on mcrit's side
    (`MinHashIndex.getFamilies` walks `getFamilyIds()` calling `getFamily`), and these
    pages made that call to fill a type-ahead in a modal that is usually never opened."""
    as_role("visitor")
    fake_mcrit.calls.clear()

    response = client.get(path)

    assert response.status_code == 200
    assert calls_to(fake_mcrit, "getFamilies") == [], f"{path} still downloads every family"


def test_the_type_ahead_answers_a_bounded_number_of_names(client, as_role, fake_mcrit):
    """The names arrive as the widget's own `{label, value}` items, escaped:
    `search_families` answers entry dicts, and a view that forwarded those would fill
    the datalist with objects. Which names is read off the corpus, so a regenerated
    fixture moves the expectation with it. The escaping is #168's, and
    testScriptEscaping.py is where a name that *is* markup gets pinned."""
    as_role("visitor")
    matching = [family.family_name for family in fake_mcrit._families.values() if "cit" in family.family_name.lower()]

    response = client.get("/explore/familyNames?q=cit")

    assert response.status_code == 200
    assert 0 < len(matching) <= FAMILY_NAME_SUGGESTIONS, "the prefix no longer picks a testable slice of the corpus"
    assert set(response.json) == {"suggestions"}
    assert sorted(item["label"] for item in response.json["suggestions"]) == sorted(matching)
    assert all(item["label"] == item["value"] for item in response.json["suggestions"])
    assert [kwargs.get("limit") for _args, kwargs in calls_to(fake_mcrit, "search_families")] == [10]


def test_the_type_ahead_survives_a_backend_that_cannot_answer(client, as_role, fake_mcrit):
    """`search_families` answers None when the backend errors. The field it feeds is a
    free-text input, so the modal is still usable without suggestions."""
    as_role("visitor")
    fake_mcrit.search_families = lambda *args, **kwargs: None

    response = client.get("/explore/familyNames?q=cit")

    assert response.status_code == 200
    assert response.json == {"suggestions": []}


# --- the unified search does not run the slow collection unasked ------------------

def test_an_unqualified_search_leaves_the_function_scan_alone(client, as_role, fake_mcrit):
    """Issue #76. This is also what every pagination click on the family or sample
    table used to re-run."""
    as_role("visitor")
    fake_mcrit.calls.clear()

    client.get("/explore/search?query=citadel")

    searched = sorted(name for name, _args, _kwargs in fake_mcrit.calls if name.startswith("search_"))
    assert searched == ["search_families", "search_samples"]


def test_an_unqualified_search_says_that_it_skipped_the_functions(client, as_role, fake_mcrit):
    """The omission must not read as "there are no such functions". The offered link
    has to carry the collections already searched, or taking it would drop them."""
    as_role("visitor")

    page = client.get("/explore/search?query=citadel").get_data(as_text=True)

    assert "Search functions too" in page
    assert "type=family,sample,function" in page


def test_asking_for_functions_still_searches_them(client, as_role, fake_mcrit):
    as_role("visitor")
    fake_mcrit.calls.clear()

    client.get("/explore/search?query=citadel&type=family,sample,function")

    searched = sorted(name for name, _args, _kwargs in fake_mcrit.calls if name.startswith("search_"))
    assert searched == ["search_families", "search_functions", "search_samples"]


# --- what a sample search costs in total ------------------------------------------

#: Every backend call a sample search is allowed to make, and what bounds it. The
#: tests above are open-world - each says one call is *gone*, none says no other has
#: arrived - so a view that grows a `getStatus()` or a per-row lookup passes all of
#: them. This is the closed world, and it is the answer to issue #77 written down:
#: three calls for a listing, two for the unified search, neither growing with the
#: corpus, the page, or the age of the instance.
SEARCH_INVENTORY = {
    "/explore/samples": [
        ("search_samples", {"limit": 25}),
        ("getQueueData", {"method": "getMatchesForSample"}),
        ("getQueueData", {"method": "getMatchesForSampleVs"}),
    ],
    "/explore/samples?query=citadel": [
        ("search_samples", {"limit": 25}),
        ("getQueueData", {"method": "getMatchesForSample"}),
        ("getQueueData", {"method": "getMatchesForSampleVs"}),
    ],
    "/explore/search?query=citadel": [
        ("search_families", {"limit": 25}),
        ("search_samples", {"limit": 25}),
    ],
}


def inventory(fake_mcrit):
    """(call, what narrows it) for every backend call the request made, in order."""
    return [
        (name, {key: value for key, value in kwargs.items() if key in ("method", "filter", "limit") and value})
        for name, _args, kwargs in fake_mcrit.calls
    ]


@pytest.mark.parametrize("path", sorted(SEARCH_INVENTORY))
def test_a_sample_search_makes_these_backend_calls_and_no_others(client, as_role, fake_mcrit, path):
    """The two queue reads are the cost that is left, and they are narrowed rather
    than bounded - `limit` is absent because the badge is a count of *all* of a
    sample's matching jobs, so a truncated read would undercount it silently.

    Bounding them is not mcritweb's to do. mcrit's `/jobs` narrows by `payload.method`
    (indexed by `mongoqueue.MongoQueue`), by `state`, and by `filter` - and `filter` is
    a python-side substring test over one page's worth of already-fetched jobs
    (`QueueRemoteCalls.getQueueData` filters what `get_jobs` returned), so it takes one
    substring and cannot be combined with a limit. There is no "jobs for these sample
    ids" query, and mcrit could not answer one today either: a job's sample id is the
    first entry of `payload.params`, which is stored as a JSON *string*, so mongo can
    neither index nor match on it. See the report on issue #77.
    """
    as_role("visitor")
    fake_mcrit.calls.clear()

    response = client.get(path)

    assert response.status_code == 200
    assert inventory(fake_mcrit) == SEARCH_INVENTORY[path]
