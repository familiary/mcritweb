#!/usr/bin/python
"""A job method should be called the same thing everywhere it is named.

The queue identifies a job by its RPC entry point (`getMatchesForSample`). Four
different answers to "what is this job called" were reaching the user:

  * the job list wrote a human label, e.g. "Match 1vN"
  * the job overview printed `job_info.parameters`, e.g. `getMatchesForSample(0, 2)`
  * two result pages printed `job_info.method`, e.g. `getMatchesForSample`
  * four result pages printed `matching_result.method`, and `linkhunt.html` plus
    `result_maintenance.html` printed `job_info.job_parameters` - neither attribute
    exists, so six headings rendered as nothing at all

`test_no_template_reads_an_attribute_that_does_not_exist` is the ratchet for that last
class of bug, and it is the one that found the sixth site: five had been fixed by hand
and `linkhunt.html` was still reading `job_parameters`. A Jinja Undefined prints as the
empty string, so nothing else in the suite - or in a browser - says a word about it.

See issue #39.
"""

import collections
import copy
import inspect
import json
import logging
import pathlib
import re
import unittest

import pytest
from fixtureData import job_id_of
from mcrit.queue.LocalQueue import Job
from mcrit.storage.MatchingResult import MatchingResult
from mcrit.Worker import Worker

from mcritweb.jobnames import JOB_METHOD_NAMES, job_method_name

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATE_ROOT = pathlib.Path(__file__).parent.parent / "mcritweb" / "templates"

def every_queued_method():
    """Every method the backend can put a job in the queue for.

    Taken from the `@Remote` decorator, which sets `remote = True` on the function it
    wraps, rather than from `Job.method_types["all"]`. That list is hand-maintained in
    mcrit and is **incomplete**: it omits `getMatchesForSampleVsGroup` (queued by
    `MinHashIndex.getMatchesCross` when `sample_group_only` is set), `doDbCleanup`, and
    the two `recalculate*` methods the admin maintenance page submits. A ratchet built
    on it therefore ratchets against the wrong list and passes while jobs go unnamed -
    which is how `getMatchesForSampleVsGroup` was missing from the table below.
    """
    return sorted(
        name for name, function in inspect.getmembers(Worker, predicate=inspect.isfunction)
        if getattr(function, "remote", False)
    )


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


# --- the lookup itself ---------------------------------------------------------------


def test_every_job_method_the_backend_can_produce_has_a_name():
    """A ratchet. When mcrit grows a job type, this fails and someone picks a label,
    rather than the new type quietly showing up as a raw RPC name in the interface."""
    unnamed = [method for method in every_queued_method() if method not in JOB_METHOD_NAMES]
    assert unnamed == [], f"no display name for {unnamed}"


def test_every_method_in_the_captured_queue_has_a_name():
    """A second ratchet, grounded in real captured data rather than in a declaration.

    The queue fixture holds five `getMatchesForSampleVsGroup` jobs - the children of the
    captured cross compare - so this method is not hypothetical: mcritweb submits cross
    compares that produce it, and its headings were rendering as the raw RPC name. The
    check above should have caught that and did not, because it ratcheted against a list
    mcrit maintains by hand. This one cannot drift from reality the same way: it reads
    what the backend actually put in the queue.
    """
    queue = json.loads((pathlib.Path(__file__).parent / "fixtures" / "queue.json").read_text())
    documents = queue if isinstance(queue, list) else queue.get("data", [])
    methods = {document.get("payload", {}).get("method") for document in documents}
    methods.discard(None)

    assert methods, "the queue fixture holds no jobs, so this proves nothing"
    assert [method for method in sorted(methods) if method not in JOB_METHOD_NAMES] == []


def test_the_ratchet_is_wider_than_the_queue_own_list():
    """Guards the guard. If mcrit ever fills `method_types["all"]` in, this test starts
    failing and can simply go - but until then, an assertion that the two agree would be
    the assertion that let the gap through."""
    queue_own_list = Job({"payload": {}}, None).method_types["all"]
    missing_from_the_queue_list = [m for m in every_queued_method() if m not in queue_own_list]

    assert "getMatchesForSampleVsGroup" in missing_from_the_queue_list
    assert set(missing_from_the_queue_list) < set(every_queued_method())


def test_an_unknown_method_is_shown_as_itself():
    """More use than a placeholder: a backend newer than this table is still readable."""
    assert job_method_name("getSomethingInvented") == "getSomethingInvented"


def test_a_job_with_no_method_still_has_something_to_show():
    assert job_method_name(None) == "Unknown job"
    assert job_method_name("") == "Unknown job"


# --- the templates -------------------------------------------------------------------


def _attributes_used(variable):
    used = collections.defaultdict(list)
    pattern = re.compile(rf"\b{variable}\.([A-Za-z_][A-Za-z0-9_]*)")
    for path in TEMPLATE_ROOT.rglob("*.html"):
        for match in pattern.finditer(path.read_text()):
            used[match.group(1)].append(path.name)
    return used


def _attributes_of(cls):
    return set(getattr(cls, "__annotations__", {})) | {n for n in dir(cls) if not n.startswith("__")}


@pytest.mark.parametrize(
    "variable,cls",
    [("matching_result", MatchingResult), ("job_info", Job), ("job", Job)],
)
def test_no_template_reads_an_attribute_that_does_not_exist(variable, cls):
    """Jinja renders a missing attribute as the empty string, so this kind of typo is
    invisible in a browser and in every render test that only checks a status code."""
    known = _attributes_of(cls)
    missing = {name: files for name, files in _attributes_used(variable).items() if name not in known}
    assert missing == {}, f"{variable}.<attr> not on {cls.__name__}: {missing}"


# --- the pages agree with each other --------------------------------------------------


def _heading(response):
    match = re.search(rb"<h1>([^<]*)</h1>", response.data)
    return match.group(1).decode().strip() if match else None


@pytest.mark.parametrize(
    "report,expected",
    [
        ("matches_for_sample", "Match 1vN"),
        ("matches_for_sample_vs", "Match 1v1"),
        ("matches_for_query", "Match Binary (mapped)"),
        ("cross_compare", "CrossCompare"),
    ],
)
def test_the_result_page_names_the_job(client, as_role, report, expected):
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of(report)}")
    assert response.status_code == 200
    assert _heading(response) == f"Results for Job: {expected}"


@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs", "matches_for_query"])
def test_the_job_page_and_the_result_page_agree(client, as_role, report):
    as_role("visitor")
    job_id = job_id_of(report)
    on_the_job_page = _heading(client.get(f"/data/jobs/{job_id}"))
    on_the_result_page = _heading(client.get(f"/data/result/{job_id}"))

    assert on_the_job_page.startswith("Job overview: ")
    assert on_the_result_page.startswith("Results for Job: ")
    assert on_the_job_page.removeprefix("Job overview: ") == on_the_result_page.removeprefix("Results for Job: ")


@pytest.mark.parametrize(
    "report,method,expected",
    [("matches_for_sample", "getMatchesForSample", b"Match 1vN"),
     ("matches_for_sample_vs", "getMatchesForSampleVs", b"Match 1v1")],
)
def test_the_job_list_calls_it_the_same_thing(client, as_role, report, method, expected):
    """The list is where these names came from, so this is really a check that the
    extraction did not change its wording. The list shows one job type per tab - and
    the fake narrows by method the way mcrit does - so each name is looked for on the
    tab its job is listed on."""
    as_role("visitor")
    response = client.get(f"/data/jobs?active={method}")
    assert response.status_code == 200
    assert expected in response.data


def test_the_full_task_string_is_still_on_the_page(client, as_role):
    """The heading is a name now, not a call signature - but the arguments are useful
    and must not have been thrown away. job_column_table still shows them."""
    as_role("visitor")
    response = client.get(f"/data/jobs/{job_id_of('matches_for_sample')}")
    assert b"getMatchesForSample(0, 2)" in response.data


if __name__ == "__main__":
    unittest.main()


# --- the Task line of a job's page (issue #40) --------------------------------------
#
# `Job.parameters` flattens the positional arguments and the named options into one
# anonymous call, `getMatchesForMappedBinary(None, 31850496, 2)`, and that is what the
# job overview and the query result page printed as the task. `job_task` reads the
# queue's record itself and says what the job was run on, in the words its page uses.

from mcritweb.jobnames import job_task  # noqa: E402


def fixture_job(report):
    return Job(json.loads((pathlib.Path(__file__).parent / "fixtures" / f"{report}.job.json").read_text()), None)


class JobTaskTest(unittest.TestCase):
    def test_a_query_job_names_the_binary_and_its_base_address(self):
        job = fixture_job("matches_for_query")
        self.assertEqual("getMatchesForMappedBinary(None, 31850496, 2)", job.parameters)
        self.assertEqual(f"Match Binary (mapped) | {job.sha256[:8]} | mapped at 0x1e60000 | MinHash matching: Standard", job_task(job))

    def test_the_matching_jobs_name_their_samples(self):
        self.assertEqual("Match 1vN | sample 0 | MinHash matching: Standard", job_task(fixture_job("matches_for_sample")))
        self.assertEqual("Match 1v1 | sample 1 vs. sample 3 | MinHash matching: Standard", job_task(fixture_job("matches_for_sample_vs")))
        self.assertEqual("UniqueBlocks | samples 0–2 (3) | family: 1", job_task(fixture_job("unique_blocks")))
        cross = job_task(fixture_job("cross_compare"))
        self.assertTrue(cross.startswith("CrossCompare | "), cross)
        self.assertRegex(cross, r"CrossCompare \| \d+ samples")

    def test_a_unique_blocks_job_folds_its_samples_into_ranges(self):
        """Issue #300: the task line listed every sample id, up to the 250 a selection may
        name or a whole family's."""
        def unique_blocks(sample_ids):
            return job_task(Job({"payload": {"method": "getUniqueBlocks", "params": json.dumps({"0": sample_ids})}}, None))

        self.assertEqual("UniqueBlocks | samples 14–100 (87)", unique_blocks(list(range(14, 101))))
        self.assertEqual("UniqueBlocks | samples 14–17, 20, 21, 30 (7)", unique_blocks([30, 14, 15, 16, 17, 20, 21]))
        self.assertEqual("UniqueBlocks | samples 3, 4, 9", unique_blocks([3, 4, 9]))
        self.assertEqual("UniqueBlocks | samples 1, 2", unique_blocks([2, 1, 2]))
        self.assertEqual("UniqueBlocks | sample 16", unique_blocks([16]))
        self.assertEqual("UniqueBlocks | no samples", unique_blocks([]))
        # five ranges or single ids are listed, a sixth gives just the count
        self.assertEqual("UniqueBlocks | samples 1–3, 5, 7, 9, 11–13 (9)", unique_blocks([1, 2, 3, 5, 7, 9, 11, 12, 13]))
        self.assertEqual("UniqueBlocks | 10 samples", unique_blocks([1, 2, 3, 5, 7, 9, 11, 12, 13, 15]))
        self.assertEqual("UniqueBlocks | 250 samples", unique_blocks(list(range(1, 500, 2))))
        # mcrit's server stores the ids as ints, but a record that does not still gets a line
        self.assertEqual("UniqueBlocks | 2 samples", unique_blocks(["1", True]))

    def test_options_read_by_their_names_and_flags_only_when_set(self):
        job = Job({"payload": {"method": "getMatchesForSample", "params": json.dumps({"0": 4, "force_recalculation": True, "minhash_threshold": 70, "sample_group_only": False})}}, None)
        self.assertEqual("Match 1vN | sample 4 | forced recalculation | MinHash threshold: 70", job_task(job))

    def test_an_unknown_or_unreadable_job_still_gets_its_name(self):
        self.assertEqual("Recalculate PicHashes", job_task(Job({"payload": {"method": "recalculatePicHashes", "params": "{}"}}, None)))
        self.assertEqual("someNewMethod", job_task(Job({"payload": {"method": "someNewMethod", "params": "not json"}}, None)))
        self.assertEqual("Unknown job", job_task(Job({}, None)))


def test_the_job_page_prints_the_readable_task(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/jobs/{job_id_of('matches_for_query')}").get_data(as_text=True)
    assert "Match Binary (mapped) |" in page
    assert "mapped at 0x1e60000" in page
    assert 'title="getMatchesForMappedBinary(None, 31850496, 2)"' in page
    assert ">getMatchesForMappedBinary(None, 31850496, 2)<" not in page


def test_a_unique_blocks_job_page_keeps_every_sample_in_the_title(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/jobs/{job_id_of('unique_blocks')}").get_data(as_text=True)
    assert 'title="getUniqueBlocks([0, 1, 2], 1)">UniqueBlocks | samples 0–2 (3) | family: 1<' in page


def sample_set_job(job_id, sample_ids):
    """The captured unique blocks job as requestUniqueBlocksForSamples queues one: the
    sample ids alone, no family."""
    document = json.loads((pathlib.Path(__file__).parent / "fixtures" / "unique_blocks.job.json").read_text())
    document["_id"] = {"$oid": job_id}
    document["payload"]["params"] = json.dumps({"0": sample_ids})
    document["payload"]["descriptor"] = json.dumps(["getUniqueBlocks", {"0": sample_ids}, {}])
    return document


def test_the_jobs_list_names_a_unique_blocks_jobs_samples_like_its_task_row(client, as_role, corpus_mcrit, monkeypatch):
    """The list printed the count and then every id; it prints the Task row's line now,
    and the line's title holds the ids it used to print."""
    job = Job(sample_set_job("6a74660af8b8d2c6f8366401", [0, 1, 2, 3, 4, 7, 9]), None)
    monkeypatch.setattr(corpus_mcrit, "getQueueData", lambda *args, **kwargs: [job])
    as_role("visitor")
    page = client.get("/data/jobs?active=getUniqueBlocks").get_data(as_text=True)

    assert 'UniqueBlocks | <span title="0, 1, 2, 3, 4, 7, 9">samples 0–4, 7, 9 (7)</span>' in page
    assert page.count("0, 1, 2, 3, 4, 7, 9") == 1, "the ids are still listed outside the title"


#: The sample set job the result page tests below are served under.
SAMPLE_SET_JOB_ID = "6a74660af8b8d2c6f8366402"


def serve_sample_set_result(corpus_mcrit, sample_ids):
    """The captured report as a sample set job's over `sample_ids`, with a statistics row
    for each of them, as mcrit writes one per requested sample."""
    result = copy.deepcopy(corpus_mcrit._jobs[job_id_of("unique_blocks")][1])
    by_sample_id = result["statistics"]["by_sample_id"]
    for sample_id in sample_ids:
        by_sample_id[str(sample_id)] = dict(by_sample_id["0"], sample_id=sample_id)
    corpus_mcrit._jobs[SAMPLE_SET_JOB_ID] = (sample_set_job(SAMPLE_SET_JOB_ID, sample_ids), result)


def requested_samples(client):
    """What the result page's Requested Samples row unfolds into, whitespace collapsed."""
    page = client.get(f"/data/result/{SAMPLE_SET_JOB_ID}").get_data(as_text=True)
    row = page[page.index("Requested Samples"):]
    row = re.sub(r"\s+", " ", row[:row.index("</tr>")])
    return row[row.index("<details>"):row.index("</details>")]


def test_a_unique_blocks_result_page_folds_its_requested_samples(client, as_role, corpus_mcrit):
    """The Requested Samples row linked every id; it gives the Task row's line now, and
    opens into every sample, still linked. 999 is a sample deleted since."""
    serve_sample_set_result(corpus_mcrit, [0, 1, 2, 3, 4, 999])
    as_role("visitor")
    unfolded = requested_samples(client)

    assert unfolded.startswith("<details><summary>samples 0–4, 999 (6)</summary>"), unfolded
    assert re.findall(r'/explore/samples/(\d+)"', unfolded) == ["0", "1", "2", "3", "4", "999"]

