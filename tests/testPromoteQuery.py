#!/usr/bin/python
"""Promoting a query to a stored sample - issue #9.

A query is matched without being stored: the backend answers with `sample_id: -1`,
no family and no version, and nothing about it stays in the corpus. Promotion turns
one into a real sample without asking the analyst to find the file and upload it a
second time.

Where the bytes come from is the whole story, so it is worth writing down. The
backend keeps a query's input in GridFS behind a job reference and exposes no route
that reads it back - `application_routes.py` has `/jobs/{id}`, `/jobs/{id}/result`
and `/results/{id}`, and nothing for a job's *input* - and `McritClient` has no verb
that turns a query job into a sample. The only copy that can be resubmitted is the
one `analyze.query` writes into `instance/temp/uploads/`, under the id of the job it
queued. Neither of the two hashes in play names that file: the query's report records
the sha256 the queried *sample* declares, and the job descriptor holds the hash of
whatever `QueueRemoteCalls` serialised - the canonicalised report JSON, for an .smda
query. Both still matter here, the first to ask the corpus whether the sample is
already stored and the second to check that the file found is the one the job ran on,
but a promotion locates the file with neither.

That copy is local to a single host and is written by the web upload alone:
`api.api_router` forwards a query straight to the backend, so a query from the API or
the IDA plugin never leaves one. Promotion is therefore offered exactly when the file
is there, and says so plainly when it is not, instead of presenting a button that
fails.
"""

import builtins
import copy
import hashlib
import io
import json
import logging
import pathlib
import unittest

import pytest
from fixtureData import job_id_of, load
from mcrit.libs.utility import encode_two_complement
from mcrit.storage.SampleEntry import SampleEntry
from smda.common.SmdaReport import SmdaReport

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The synthetic query jobs below all live under this id, so a test names one place.
QUERY_JOB_ID = "0123456789abcdef01234567"

#: What the backend answers when a promotion queues work.
NEW_JOB_ID = "fedcba9876543210fedcba98"

#: Bytes small enough to keep in a fixture and real enough to hash.
QUERIED_BYTES = b"MZ" + bytes(range(256)) * 4

#: An entry to stand in for what `addReport` gives back - a real one out of the
#: corpus, because the view reads `.sample_id` off it to build a redirect.
PROMOTED_SAMPLE = SampleEntry.fromDict(next(iter(load("samples").values())))


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The captured corpus, taught the two write verbs a promotion reaches for.

    `CorpusMcritClient` raises NotImplementedError for anything nobody taught it,
    which is the right default everywhere else. Here the writes are exactly what these
    tests assert on, so they have to be recorded rather than raised.
    """
    def _add_binary_sample(binary, **kwargs):
        corpus_mcrit._record("addBinarySample", binary, **kwargs)
        return NEW_JOB_ID

    def _add_report(smda_report, *args, **kwargs):
        corpus_mcrit._record("addReport", smda_report, *args, **kwargs)
        return PROMOTED_SAMPLE, NEW_JOB_ID

    corpus_mcrit.addBinarySample = _add_binary_sample
    corpus_mcrit.addReport = _add_report

    # the three verbs `analyze.query` queues through, so a test can run a real upload
    # in and then promote what it left behind. They answer the one job id the synthetic
    # jobs here live under, which is what a query does: the id names the new job, and
    # the upload is filed under it.
    def _queue_query(name):
        def _call(*args, **kwargs):
            corpus_mcrit._record(name, *args, **kwargs)
            return QUERY_JOB_ID
        return _call

    for verb in ("requestMatchesForUnmappedBinary", "requestMatchesForMappedBinary",
                 "requestMatchesForSmdaReport"):
        setattr(corpus_mcrit, verb, _queue_query(verb))
    return corpus_mcrit


@pytest.fixture
def uploads(app):
    """The directory `analyze.query` keeps its copy of an uploaded query in."""
    return pathlib.Path(app.instance_path) / "temp" / "uploads"


def canonical_report_sha256(report_bytes):
    """The hash `QueueRemoteCalls` writes into the descriptor of an .smda query.

    Not the file on disk and not the sample the report describes, but the report JSON
    as `McritClient` posted it and `to_binary` canonicalised it. Restated here rather
    than imported, so that a promotion hashing the wrong thing cannot agree with a
    test hashing it the same wrong way.
    """
    report = SmdaReport.fromDict(json.loads(report_bytes))
    return hashlib.sha256(json.dumps(report.toDict(), sort_keys=True).encode("ascii")).hexdigest()


def register_query(backend, method="getMatchesForUnmappedBinary", digest=None,
                   base_address=31850496, bitness=32, job_id=QUERY_JOB_ID, queried_report=None):
    """Put a query job, and the report it produced, into the backend.

    The captured query job is the template, and the report is where the sample's
    sha256 comes from - `analyze.query` names its stored copy by that, and it is the
    only value that means the same thing for all three upload kinds. The descriptor
    carries a *different* hash for an .smda query: `QueueRemoteCalls` hashes the
    canonicalised JSON it serialised, not the sample it describes. This fixture
    reproduces both, so a promotion that looked the file *up* by the descriptor hash
    would find nothing for an .smda upload, while one that measures the file it found
    against that hash has something real to measure it against.

    `queried_report` is the .smda report the job actually ran on, which is what its
    descriptor hash is taken over - deliberately a separate argument from whatever a
    test then writes into the uploads folder, because the two being the same file is
    what a promotion has to establish rather than assume. Left out, the descriptor
    matches nothing, which is what a test about an unusable stored file wants. A
    binary query needs none of it: there the payload is the upload itself, so the
    descriptor holds `digest` like everything else.
    """
    if digest is None:
        digest = hashlib.sha256(QUERIED_BYTES).hexdigest()
    document = copy.deepcopy(load("matches_for_query.job"))
    document["_id"] = {"$oid": job_id}
    params = {"band_matches_required": 2, "0": None, "1": base_address}
    document["payload"]["method"] = method
    document["payload"]["params"] = json.dumps(params)
    if method != "getMatchesForSmdaReport":
        descriptor_hash = digest
    elif queried_report is not None:
        descriptor_hash = canonical_report_sha256(queried_report)
    else:
        descriptor_hash = hashlib.sha256(job_id.encode()).hexdigest()
    document["payload"]["descriptor"] = json.dumps([method, params, {"0": descriptor_hash}])

    report = copy.deepcopy(load("matches_for_query.result"))
    report["info"]["sample"]["sha256"] = digest
    # `info.sample` is a `SampleEntry.toDict()`, which writes the base address through
    # `encode_two_complement` - so anything a 64-bit dump maps above 0x7fffffffffffffff
    # arrives negative, and a fixture that skipped this would only ever test the half
    # of the address space where the encoding happens to be the identity
    report["info"]["sample"]["base_addr"] = encode_two_complement(base_address)
    report["info"]["sample"]["bitness"] = bitness

    backend._jobs[job_id] = (document, report)
    return digest


def smda_upload(sha256, family="", version=""):
    """An SMDA report as the .smda branch of `analyze.query` stored it: the uploaded
    JSON itself, under the sha256 the *report* claims, not the hash of the JSON."""
    return json.dumps({
        "architecture": "intel", "abi": "cdecl", "base_addr": 4194304,
        "binary_size": 2371584, "bitness": 32, "code_areas": [], "code_sections": [],
        "confidence_threshold": 0.5, "disassembly_errors": {}, "execution_time": 1.0,
        "identified_alignment": 16,
        "metadata": {
            "binweight": 1234, "component": "", "family": family, "filename": "query.exe",
            "is_library": False, "is_buffer": False, "language": "C", "version": version,
        },
        "message": "", "oep": 4198400, "sha256": sha256, "smda_version": "4.4.4",
        "statistics": {}, "status": "ok", "timestamp": "2026-08-07T12-00-00", "xcfg": {},
    }).encode()


def store_upload(uploads, content, job_id=QUERY_JOB_ID):
    """Put a file where `analyze.query` leaves one: under the id of the job it queued.

    Written once rather than at every call site, because it is the one thing these
    tests and that route have to agree about - and agreeing with each other is not the
    same as agreeing with it, which is what `test_a_query_uploaded_through_the_web_can
    _be_promoted` at the bottom is for.
    """
    (uploads / job_id).write_bytes(content)


def promote(client, job_id=QUERY_JOB_ID, **fields):
    """POST the promotion form the result page renders."""
    data = {"family": "test.family", "version": "1.0"}
    data.update(fields)
    return client.post(f"/data/promote_query/{job_id}", data=data)


def calls_to(backend, method):
    return [call for call in backend.calls if call[0] == method]


def wrote_nothing(backend):
    """No sample reached the corpus, by either of the two routes into it."""
    return not calls_to(backend, "addBinarySample") and not calls_to(backend, "addReport")


# --- what the result page offers ---------------------------------------------------

def test_the_query_result_page_offers_promotion_while_the_upload_is_here(client, as_role, uploads):
    """The captured query report, with its uploaded file still in place. This is the
    only state in which the button is honest, so it is the only state that shows it."""
    as_role("contributor")
    store_upload(uploads, QUERIED_BYTES, job_id_of("matches_for_query"))

    page = client.get(f"/data/result/{job_id_of('matches_for_query')}").get_data(as_text=True)
    assert f'action="/data/promote_query/{job_id_of("matches_for_query")}"' in page


def test_the_query_result_page_explains_itself_when_the_upload_is_gone(client, as_role):
    """A query from the API, from the IDA plugin, or from another host of the same
    deployment leaves no local copy. Offering a button that cannot work would be the
    worse answer, so the page says why instead."""
    as_role("contributor")

    page = client.get(f"/data/result/{job_id_of('matches_for_query')}").get_data(as_text=True)
    assert "/data/promote_query/" not in page
    assert "no longer available on this server" in page


def test_a_visitor_is_not_offered_promotion(client, as_role, uploads):
    """Promotion adds to the corpus, which is contributor work - `data.submit` draws
    the same line. The route enforces it; the page must not dangle it either."""
    as_role("visitor")
    store_upload(uploads, QUERIED_BYTES, job_id_of("matches_for_query"))

    page = client.get(f"/data/result/{job_id_of('matches_for_query')}").get_data(as_text=True)
    assert "/data/promote_query/" not in page


def test_a_sample_result_page_offers_no_promotion(client, as_role):
    """`getMatchesForSample` matched something the corpus already holds. There is
    nothing to promote, and the same template renders both."""
    as_role("contributor")

    page = client.get(f"/data/result/{job_id_of('matches_for_sample')}").get_data(as_text=True)
    assert "/data/promote_query/" not in page
    assert "no longer available on this server" not in page


# --- what a promotion submits ------------------------------------------------------

def test_promotion_resubmits_the_bytes_that_were_queried(client, as_role, fake_mcrit, uploads):
    """The point of the feature: the corpus gets the same file that was matched, with
    the family and version the analyst decided on afterwards."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)

    assert response.status_code == 302
    _, args, kwargs = calls_to(fake_mcrit, "addBinarySample")[0]
    assert args[0] == QUERIED_BYTES
    assert kwargs["family"] == "test.family"
    assert kwargs["version"] == "1.0"
    assert kwargs["is_dump"] is False


def test_the_promoted_sample_is_reached_through_its_job(client, as_role, fake_mcrit, uploads):
    """`addBinarySample` only queues disassembly, so there is no sample id yet - the
    same hop `data.submit` makes."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)
    assert f"/data/jobs/{NEW_JOB_ID}" in response.headers["Location"]


def test_a_mapped_query_is_promoted_as_the_dump_it_was(client, as_role, fake_mcrit, uploads):
    """A dump has to be resubmitted with the base address and bitness it was queried
    under. The backend defaults bitness to 32 when it is not told, so a 64-bit dump
    promoted without it would be disassembled differently than the report on screen."""
    as_role("contributor")
    register_query(fake_mcrit, method="getMatchesForMappedBinary",
                   base_address=0x140000000, bitness=64)
    store_upload(uploads, QUERIED_BYTES)

    promote(client)

    _, _, kwargs = calls_to(fake_mcrit, "addBinarySample")[0]
    assert kwargs["is_dump"] is True
    assert kwargs["base_addr"] == 0x140000000
    assert kwargs["bitness"] == 64


def test_a_kernel_mode_dump_is_promoted_at_the_address_it_was_mapped_to(client, as_role, fake_mcrit, uploads):
    """The base address a dump is resubmitted with is read out of a `SampleEntry`, so
    it has to be decoded the way one stores it. Above 0x7fffffffffffffff - which is
    every Windows kernel-mode dump - `encode_two_complement` has made it negative, and
    a promotion reading it raw either refuses the dump or, worse, maps it nowhere."""
    as_role("contributor")
    base_address = 0xFFFFF80000000000
    register_query(fake_mcrit, method="getMatchesForMappedBinary",
                   base_address=base_address, bitness=64)
    store_upload(uploads, QUERIED_BYTES)

    promote(client)

    submitted = calls_to(fake_mcrit, "addBinarySample")
    assert submitted, "a kernel-mode dump could not be promoted"
    _, _, kwargs = submitted[0]
    assert kwargs["base_addr"] == base_address


def test_an_smda_query_is_promoted_through_its_report(client, as_role, fake_mcrit, uploads):
    """An .smda upload is not a binary, so it goes back the way `data.submit` sends
    one - as a report. Family and version have nowhere else to go than into it."""
    as_role("contributor")
    digest = "ab" * 32
    queried = smda_upload(digest)
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest, queried_report=queried)
    store_upload(uploads, queried)

    response = promote(client)

    assert not calls_to(fake_mcrit, "addBinarySample")
    _, args, _ = calls_to(fake_mcrit, "addReport")[0]
    assert args[0].sha256 == digest
    assert args[0].family == "test.family"
    assert args[0].version == "1.0"
    assert f"/explore/samples/{PROMOTED_SAMPLE.sample_id}" in response.headers["Location"]


def test_an_smda_query_is_found_without_being_named_by_either_of_its_hashes(client, as_role, fake_mcrit, uploads):
    """An .smda query carries two different hashes and is named by neither.

    The report declares the sha256 of the *sample* it describes; the job descriptor
    holds the hash of what `QueueRemoteCalls` serialised, which for this method is the
    canonicalised report JSON. Naming the file by the first is what let one visitor
    overwrite another user's stored query, and naming it by a digest of the upload -
    the obvious correction - is a value the promote path cannot reconstruct from
    either, so every .smda query would have become unpromotable. It is named by the
    job. This asserts the two hashes really do differ here, so a scheme that quietly
    fell back to one of them could not pass by coincidence, and that promotion works
    anyway."""
    as_role("contributor")
    digest = "ab" * 32
    queried = smda_upload(digest)
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest, queried_report=queried)
    descriptor = json.loads(fake_mcrit._jobs[QUERY_JOB_ID][0]["payload"]["descriptor"])
    assert descriptor[2]["0"] != digest, "the fixture no longer reproduces the two hashes"
    assert hashlib.sha256(queried).hexdigest() not in (digest, descriptor[2]["0"]), \
        "the fixture no longer distinguishes the upload from the hashes it carries"
    store_upload(uploads, queried)

    promote(client)

    assert calls_to(fake_mcrit, "addReport"), "the upload was not found under its job id"


def test_an_smda_report_keeps_its_own_family_when_none_is_typed(client, as_role, fake_mcrit, uploads):
    """An empty form field means "say nothing", not "erase what the report carries"."""
    as_role("contributor")
    digest = "ab" * 32
    queried = smda_upload(digest, family="win.citadel", version="2019")
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest, queried_report=queried)
    store_upload(uploads, queried)

    promote(client, family="", version="")

    _, args, _ = calls_to(fake_mcrit, "addReport")[0]
    assert args[0].family == "win.citadel"
    assert args[0].version == "2019"


# --- what a promotion refuses ------------------------------------------------------

def test_promoting_a_query_that_is_already_in_the_corpus_creates_nothing(client, as_role, fake_mcrit, uploads):
    """Promoting twice must not make two samples. The corpus is checked by hash before
    anything is submitted, and the second attempt lands on the sample that exists."""
    as_role("contributor")
    known = next(iter(fake_mcrit._samples.values()))
    register_query(fake_mcrit, digest=known.sha256)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)

    assert wrote_nothing(fake_mcrit)
    assert f"/explore/samples/{known.sample_id}" in response.headers["Location"]


def test_the_corpus_is_checked_even_once_the_local_copy_is_gone(client, as_role, fake_mcrit):
    """"Already promoted" is the true answer whether or not the upload survived, so it
    is answered first. Reporting a missing file for a sample that is plainly in the
    database would send the analyst looking for it again."""
    as_role("contributor")
    known = next(iter(fake_mcrit._samples.values()))
    register_query(fake_mcrit, digest=known.sha256)

    response = promote(client)

    assert wrote_nothing(fake_mcrit)
    assert f"/explore/samples/{known.sample_id}" in response.headers["Location"]


def test_promotion_without_the_local_copy_writes_nothing(client, as_role, fake_mcrit):
    """The gate the template shows must also hold in the route: a hand-made POST for a
    query whose bytes are gone has to be refused, not guessed at."""
    as_role("contributor")
    register_query(fake_mcrit)

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_promotion_of_a_job_that_is_not_a_query_writes_nothing(client, as_role, fake_mcrit, uploads):
    """Every other job id in the system is one POST away, and none of them is a query,
    so none of them may promote anything.

    Arranged in the one state where nothing further along would stop it anyway: a
    `getMatchesForSample` job whose sample has since been deleted, so "already in
    database" is not the answer, with a file still lying in the uploads folder under
    that sample's hash. All that is left between the POST and
    `QUERY_UPLOAD_KINDS[job_info.method]` - which has no key for this method, and so
    raises rather than refuses - is the check this test is here for. Asserting where
    the refusal lands, and not only that the corpus stayed empty, is what keeps the
    two ways of writing nothing apart.
    """
    as_role("contributor")
    job_id = job_id_of("matches_for_sample")
    queried_sha256 = load("matches_for_sample.result")["info"]["sample"]["sha256"]
    deleted = next(sample for sample in fake_mcrit._samples.values() if sample.sha256 == queried_sha256)
    del fake_mcrit._samples[deleted.sample_id]
    store_upload(uploads, QUERIED_BYTES, job_id)

    response = promote(client, job_id=job_id)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)
    assert response.headers["Location"].endswith(f"/data/result/{job_id}"), response.headers["Location"]


def test_promotion_of_a_job_nobody_knows_writes_nothing(client, as_role, fake_mcrit):
    as_role("contributor")

    response = promote(client, job_id="ffffffffffffffffffffffff")

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_a_local_copy_that_no_longer_matches_the_query_is_not_submitted(client, as_role, fake_mcrit, uploads):
    """The file is named by the hash of the query's input, so a file whose contents
    hash to something else is not that input. Promoting it would put a different
    sample in the corpus under a report that never described it."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, b"something else entirely")

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


@pytest.mark.parametrize("recorded", [None, "0x140000000", True, 2 ** 64])
def test_a_dump_whose_report_records_no_base_address_is_not_submitted(client, as_role, fake_mcrit, uploads, recorded):
    """`McritClient.addBinarySample` formats the base address with `0x{base_addr:x}`,
    so it is not a place to pass on whatever the report happened to carry. A report
    that records no address in the range a dump can be mapped at - an older backend,
    or a result rewritten by hand - leaves nothing to resubmit it under, and that has
    to become a message rather than an exception or some other address."""
    as_role("contributor")
    register_query(fake_mcrit, method="getMatchesForMappedBinary", bitness=64)
    fake_mcrit._jobs[QUERY_JOB_ID][1]["info"]["sample"]["base_addr"] = recorded
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_an_smda_upload_that_describes_another_sample_is_not_submitted(client, as_role, fake_mcrit, uploads):
    """The same check on the .smda path, where the stored file is a report rather than
    the sample, so what it has to be measured against is the report the job ran on."""
    as_role("contributor")
    digest = "ab" * 32
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest,
                   queried_report=smda_upload(digest))
    store_upload(uploads, smda_upload("cd" * 32))

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_an_smda_upload_forged_to_claim_the_queried_hash_is_not_submitted(client, as_role, fake_mcrit, uploads):
    """The report a promotion resubmits may not be checked against a field of its own.

    `analyze.query` files an .smda upload under the sha256 the report *claims*, and
    every role that may run a query may therefore write any name in the uploads folder.
    So a visitor can read a contributor's queried hash off the result page - the same
    page is visitor-readable - re-upload a report of their own declaring that hash, and
    overwrite the contributor's stored copy. Promotion then carries the visitor's
    report, filename, family and version into the corpus, across the line
    `data.submit` draws. Comparing the report's `sha256` against the name it chose
    itself agrees with anything; the hash `QueueRemoteCalls` recorded for the payload
    the job actually ran on does not.
    """
    as_role("contributor")
    digest = "ab" * 32
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest,
                   queried_report=smda_upload(digest))
    planted = json.loads(smda_upload(digest))
    planted["metadata"]["filename"] = "PLANTED.exe"
    planted["metadata"]["family"] = "totally.benign"
    planted["binary_size"] = 1337
    assert planted["sha256"] == digest, "the forgery is only interesting under the queried name"
    store_upload(uploads, json.dumps(planted).encode())

    response = promote(client, family="", version="")

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit), "the corpus got " + repr(
        [(call[1][0].filename, call[1][0].family, call[1][0].binary_size)
         for call in calls_to(fake_mcrit, "addReport")])


@pytest.mark.parametrize("descriptor", ["not json", '"a string"', '["m", {}]', '["m", {}, {}]'])
def test_a_query_whose_job_records_no_payload_hash_is_not_promoted(client, as_role, fake_mcrit, uploads, descriptor):
    """The hash the stored file is measured against comes out of the job descriptor,
    which is backend data of a shape this app does not get to decide. A job that
    carries no usable one leaves nothing to check the file against, so the promotion
    stops there - and a descriptor shaped unexpectedly has to stop it the same way,
    rather than raise on the way past."""
    as_role("contributor")
    register_query(fake_mcrit)
    fake_mcrit._jobs[QUERY_JOB_ID][0]["payload"]["descriptor"] = descriptor
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


@pytest.mark.parametrize("payload", [b"not json at all", b'["a", "list"]', b"{}"])
def test_an_unreadable_smda_upload_is_reported_rather_than_a_500(client, as_role, fake_mcrit, uploads, payload):
    """Whatever sits in the uploads directory is an uploaded file, which is to say
    attacker-controlled. Anything it does to the parser has to become a message."""
    as_role("contributor")
    digest = "ab" * 32
    register_query(fake_mcrit, method="getMatchesForSmdaReport", digest=digest)
    store_upload(uploads, payload)

    response = promote(client)

    assert response.status_code < 500
    assert wrote_nothing(fake_mcrit)


@pytest.mark.parametrize("field", ["family", "version"])
def test_metadata_that_could_inject_backend_parameters_is_refused(client, as_role, fake_mcrit, uploads, field):
    """`McritClient.addBinarySample` builds its request by pasting these into a query
    string without percent-encoding, so a value carrying '&' would append parameters
    of its own to the backend call - `is_dump`, or another `family`. Nothing shaped
    like that gets that far."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client, **{field: "evil&is_dump=1&family=other"})

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


@pytest.mark.parametrize("field", ["family", "version"])
def test_metadata_that_the_backend_query_string_would_alter_is_refused(client, as_role, fake_mcrit, uploads, field):
    """The allowlist is justified by what survives that query string unchanged, so it
    may not hold a character that does not. `requests` counts '+' as safe and leaves
    it in the URL it prepares, and the backend is Falcon, which decodes a query string
    with `unquote_plus` - so a family typed as "win.a+b" would be stored as "win.a b",
    silently, and only on the binary path: the .smda path posts JSON and keeps it."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client, **{field: "win.a+b"})

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_metadata_with_a_space_is_still_accepted(client, as_role, fake_mcrit, uploads):
    """A space is spelled '%20' by `requests` and read back as a space, so it does
    reach the backend as typed - the allowlist is narrowed to what round-trips, not to
    whatever is easiest to refuse."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    promote(client, family="win.a b", version="1.0 rc")

    submitted = calls_to(fake_mcrit, "addBinarySample")
    assert submitted, "a family that reaches the backend unchanged was refused"
    _, _, kwargs = submitted[0]
    assert kwargs["family"] == "win.a b"
    assert kwargs["version"] == "1.0 rc"


def test_a_report_without_a_usable_hash_promotes_nothing(client, as_role, fake_mcrit, uploads, monkeypatch):
    """A report is uploaded, so the sha256 it declares is a string an uploader chose.

    It used to be the filename, and then it had to be checked to be a hash before it
    could become part of a path. It is not the filename any more - the job id is - so
    this now holds a narrower statement: whatever the report declares, it stays out of
    the path entirely, and only the corpus is asked about it.

    Asserting that nothing was submitted would not be enough. A path built from that
    field would traverse, the file would be read, and the promotion would then be
    rejected further down because the bytes do not hash to the declared "digest" -
    leaving an arbitrary file read behind a passing test, and on the .smda branch an
    arbitrary file parsed as JSON. So this asserts on which file was opened.
    """
    as_role("contributor")
    # a target outside the uploads folder, reached by exactly as many steps as it
    # takes - a fixed "../../../../etc/passwd" only traverses on a filesystem where
    # the instance path happens to sit four levels down, so it would pass anywhere
    # else for no reason at all
    outside = uploads.parent.parent / "outside.txt"
    outside.write_bytes(b"not an upload")
    register_query(fake_mcrit, digest="../../" + outside.name)
    store_upload(uploads, QUERIED_BYTES)

    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda file, *args, **kwargs: (opened.append(file), real_open(file, *args, **kwargs))[1])

    response = promote(client)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)
    assert not [path for path in opened if outside.name in str(path)], f"read outside the uploads folder: {opened}"
    # and the file it did open is the one the job names, not one the report asked for
    assert [path for path in opened if str(path).endswith(QUERY_JOB_ID)], \
        f"the upload was not looked for under its job id: {opened}"


@pytest.mark.parametrize("job_id", ["None", "NUL", "job-1"])
def test_a_job_whose_id_could_not_have_named_a_file_promotes_nothing(client, as_role, fake_mcrit, uploads, job_id):
    """The shape check belongs to the path, so the route has to be standing behind it
    rather than beside it.

    A job id reaches here off the wire and out of a URL, and only two shapes are ever
    issued: an ObjectId, or a uuid4. "None" is what `QueueRemoteCalls` hands back when
    `MongoQueue.put` returns None on an unacknowledged insert - one name for every such
    failure - and "NUL" is a Windows device rather than a file. Neither can name an
    upload, so neither may be promoted from one, whatever happens to be lying at that
    name in a folder every query writes into.
    """
    as_role("contributor")
    register_query(fake_mcrit, job_id=job_id)
    store_upload(uploads, QUERIED_BYTES, job_id)

    response = promote(client, job_id=job_id)

    assert response.status_code == 302
    assert wrote_nothing(fake_mcrit)


def test_promotion_is_not_reachable_by_get(client, as_role, fake_mcrit, uploads):
    """Issue #84: a write a browser performs on plain navigation is reachable from any
    page the victim visits, and carries no CSRF token to check."""
    as_role("contributor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = client.get(f"/data/promote_query/{QUERY_JOB_ID}")

    assert response.status_code == 405
    assert wrote_nothing(fake_mcrit)


def test_promotion_needs_a_contributor(client, as_role, fake_mcrit, uploads):
    """The same floor `data.submit` stands on, enforced in the route rather than only
    in the template that hides the form."""
    as_role("visitor")
    register_query(fake_mcrit)
    store_upload(uploads, QUERIED_BYTES)

    response = promote(client)

    assert response.status_code == 403
    assert wrote_nothing(fake_mcrit)


# --- the two halves, joined --------------------------------------------------------
#
# Every test above places the stored upload itself, which is the right shape for
# asking what promotion does with a file that is there. It cannot answer whether the
# file is ever there: that depends on `analyze.query` and this module on how it names
# what it wrote, and two tests agreeing with each other about a name is not evidence
# that the two *routes* agree. They did not - a query upload was filed under the
# sha256 an .smda report declares about itself, which any visitor could set to another
# user's digest, and correcting that to a digest of the uploaded bytes would have left
# nothing here able to find the file at all. Both halves run in these.


QUERY_BASE_ADDRESS = "0x1e64000"


def run_query(client, content, options="unmapped", filename="query.exe"):
    """Upload through `analyze.query`, the way the query dropzone does."""
    data = {"options": options, "file": (io.BytesIO(content), filename)}
    if options in ("dumped", "smda"):
        data["base_addr"] = QUERY_BASE_ADDRESS
    return client.post("/analyze/query", data=data, content_type="multipart/form-data")


@pytest.mark.parametrize("options, method", [
    ("unmapped", "getMatchesForUnmappedBinary"),
    ("dumped", "getMatchesForMappedBinary"),
    ("smda", "getMatchesForSmdaReport"),
])
def test_a_query_uploaded_through_the_web_can_be_promoted(client, as_role, fake_mcrit, options, method):
    """A query goes in through the upload form; its result page then offers promotion,
    and promoting it reaches the corpus with the bytes that were queried.

    This is the whole feature on a clean instance, and the only test that fails if the
    two routes disagree about where the file lives. The .smda case is the one that was
    broken: its report declares a sha256 that is neither a digest of the upload nor the
    hash in its job descriptor, so a naming scheme built on either leaves the promote
    path looking somewhere the file is not - permanently, on every host."""
    as_role("contributor")
    if options == "smda":
        content = smda_upload(hashlib.sha256(QUERIED_BYTES).hexdigest())
        register_query(fake_mcrit, method=method, queried_report=content)
    else:
        content = QUERIED_BYTES
        register_query(fake_mcrit, method=method)

    assert run_query(client, content, options=options).status_code == 202

    page = client.get(f"/data/result/{QUERY_JOB_ID}").get_data(as_text=True)
    assert f'action="/data/promote_query/{QUERY_JOB_ID}"' in page,         "the result page did not offer to promote a query this instance stored"

    assert promote(client).status_code == 302
    assert not wrote_nothing(fake_mcrit), "the promotion reached the corpus with nothing"


def test_a_query_this_instance_never_saw_is_still_not_promotable(client, as_role, fake_mcrit):
    """The other side of the same statement, so the test above cannot be satisfied by
    offering promotion unconditionally. A query from the API or the IDA plugin, or one
    that reached another host of the same deployment, leaves no local file."""
    as_role("contributor")
    register_query(fake_mcrit)

    page = client.get(f"/data/result/{QUERY_JOB_ID}").get_data(as_text=True)
    assert "/data/promote_query/" not in page
    assert "no longer available on this server" in page

    assert promote(client).status_code == 302
    assert wrote_nothing(fake_mcrit)


if __name__ == "__main__":
    unittest.main()
