#!/usr/bin/python
"""How `analyze.query` names the file it keeps under `instance/temp/uploads/`.

The route is `@visitor_required` and writes every upload to that folder with mode
"wb" and no existence check, so the name is the only thing standing between two
users' uploads. It used to be a sha256, and for a `.smda` upload that hash was read
out of the uploaded report's own `sha256` field - a value the uploader types into a
JSON file. Any visitor could therefore name their upload after another user's query
and overwrite it, permanently, since nothing ever restores it.

The file is not write-only, and that is what makes the naming a two-sided problem.
It exists so that a query can later be promoted to a stored sample (issue #9, on
`fix/9-promote-a-query-to-a-sample`), which reads it back and resubmits the same
bytes. So a name has to satisfy two things at once:

  * no part of it may come from the request, or the bug above is still there; and
  * the promote path must be able to derive it from what it has, which is a job.

A digest of the uploaded bytes satisfies only the first. A query's report records the
sample's *declared* sha256, and its job descriptor records a hash of the canonicalised
report - neither is a hash of the bytes that were posted, so there is no path from a
job back to that name, and every `.smda` query becomes unpromotable. The job id
satisfies both: the backend issues it when the job is queued, so nobody can choose it,
and the promote route is holding it already.
`test_the_stored_file_is_reachable_from_the_job_id_alone` is the test for that second
half, and it is the one this module was missing when the naming was changed the first
time.

The trade is stated in `test_the_same_bytes_queried_twice_are_stored_twice`: keying by
job loses the deduplication a content hash gave the two binary branches.

Not addressed here, because naming does not answer it: the folder is still never
pruned. `QUERY_UPLOAD_LIMITS` is what bounds it today.
"""

import hashlib
import io
import json
import logging
import os

import pytest

from mcritweb.views.utility import query_upload_path

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: What B queried: an ordinary binary. Its digest is the name the old scheme filed it
#: under, and therefore the name an attacker had to declare to land on top of it.
VICTIM_BINARY = b"MZ this is the payload a contributor queried\x00" * 4
VICTIM_SHA256 = hashlib.sha256(VICTIM_BINARY).hexdigest()


@pytest.fixture
def fake_mcrit(fake_mcrit):
    """A backend that issues a fresh job id per query, as the real one does here.

    All three query branches pass `force_recalculation=True`, and `QueueRemoteCalls`
    skips its descriptor cache entirely in that case - so every query is queued as a
    new job with a new id. The shared fake answers one constant id, which would let a
    test about two users' files colliding read as passing whichever way it went.
    """
    fake_mcrit.issued = []

    def _queue(name):
        def _call(*args, **kwargs):
            fake_mcrit._record(name, *args, **kwargs)
            # an ObjectId, which is what MongoQueue answers - the shape matters now
            # that only a real job id shape is allowed to become a filename
            job_id = "%024x" % len(fake_mcrit.issued)
            fake_mcrit.issued.append(job_id)
            return job_id
        return _call

    for method in ("requestMatchesForSmdaReport", "requestMatchesForMappedBinary",
                   "requestMatchesForUnmappedBinary"):
        setattr(fake_mcrit, method, _queue(method))
    return fake_mcrit


def smda_report(sha256, family="attacker.family"):
    """The smallest dict `SmdaReport.fromDict` accepts. `sha256` is a plain field of
    the report - smda writes there the digest of the file it disassembled, and nothing
    downstream of an upload recomputes it."""
    return {
        "architecture": "intel", "base_addr": 4194304, "binary_size": 64, "bitness": 64,
        "code_areas": [], "code_sections": [], "confidence_threshold": 0.5,
        "disassembly_errors": {}, "execution_time": 0.1, "identified_alignment": 16,
        "metadata": {
            "family": family, "filename": "report.exe", "version": "1.0",
            "component": "", "is_library": False,
        },
        "message": "", "oep": 4194304, "sha256": sha256, "smda_version": "4.4.4",
        "statistics": {}, "status": "ok", "timestamp": "2026-08-07T12-00-00", "xcfg": {},
    }


def stored(app):
    """name -> bytes, for everything the route has kept so far."""
    directory = os.path.join(app.instance_path, "temp", "uploads")
    return {name: open(os.path.join(directory, name), "rb").read()
            for name in sorted(os.listdir(directory))}


def query_binary(client, content, filename="sample.exe", options="unmapped", **fields):
    """The 'unmapped' branch: a plain file, and nothing about it declared."""
    data = {"options": options, "file": (io.BytesIO(content), filename)}
    data.update(fields)
    return client.post("/analyze/query", data=data, content_type="multipart/form-data")


def query_smda(client, report, filename="report.smda"):
    """The '.smda' branch. The form reveals base_addr for a report, and the view parses
    it as hex before it ever looks at the file."""
    body = json.dumps(report).encode()
    return client.post(
        "/analyze/query",
        data={"options": "smda", "base_addr": "0x400000",
              "file": (io.BytesIO(body), filename)},
        content_type="multipart/form-data",
    )


# --- the bug ------------------------------------------------------------------------


def test_a_visitor_upload_cannot_destroy_another_users_stored_query(app, client, as_role, fake_mcrit):
    """The disclosure, end to end. A contributor queries a binary; a visitor then
    uploads a report *declaring* that binary's digest. With the name taken from the
    declared field the second write landed on the first file - "wb", no existence
    check - and the stored query was gone for good."""
    as_role("contributor", username="victim")
    assert query_binary(client, VICTIM_BINARY).status_code == 202
    victim_job = fake_mcrit.issued[-1]
    after_victim = stored(app)
    assert list(after_victim.values()) == [VICTIM_BINARY], "the query was never stored"

    as_role("visitor", username="attacker")
    assert query_smda(client, smda_report(VICTIM_SHA256)).status_code == 202

    assert stored(app)[victim_job] == VICTIM_BINARY, \
        "a visitor's upload overwrote another user's stored query"


def test_no_part_of_the_stored_name_comes_from_the_request(app, client, as_role):
    """Neither the digest the uploader declared nor a digest of what they posted names
    a file, so no amount of guessing reserves a path for a later collision."""
    as_role("visitor")
    report = smda_report(VICTIM_SHA256)
    body = json.dumps(report).encode()
    assert query_smda(client, report).status_code == 202

    names = set(stored(app))
    assert VICTIM_SHA256 not in names, "the declared digest still names the file"
    assert hashlib.sha256(body).hexdigest() not in names, "the posted bytes still name it"


@pytest.mark.parametrize("job_id", [
    # walking out of the folder
    "../../../instance/mcritweb.sqlite",
    "..\\..\\secret_key",
    "a/b", "a.b", "", "..", None, 42,
    # `QueueRemoteCalls.submitPayloadQueue` returns `str(self.queue.put(...))`, and
    # `MongoQueue.put` answers None when an insert goes unacknowledged - so a failing
    # backend hands out the string "None", which is not a job id but is one name, the
    # same for everybody. That is the collision shape again, in miniature.
    "None", "none",
    # Windows device names. `open("uploads/NUL", "wb")` writes to the null device and
    # stores nothing at all, silently - not reachable from a backend-issued id, but
    # this project is developed on Windows and the folder is not a safe place to find
    # out. Reserved with or without an extension, and case does not matter.
    "NUL", "nul", "CON", "AUX", "PRN", "COM1", "LPT1", "NUL.bin",
    # right alphabet, wrong shape
    "0123456789abcdef0123456", "0123456789abcdef012345678", "deadbeef",
    "0123456789abcdef0123456g",
])
def test_a_job_id_that_is_not_one_names_no_path(app, job_id):
    """The name is a job id off the wire or out of a URL, so it becomes part of a path
    only once it is one: an ObjectId as MongoQueue answers, or the uuid4 LocalQueue
    answers. Anything else is refused rather than sanitised, because a job id that does
    not look like a job id is a backend that is not working, not a name to repair."""
    assert query_upload_path(app, job_id) is None


@pytest.mark.parametrize("job_id", [
    "0123456789abcdef01234567",              # MongoQueue: str(ObjectId)
    "6a7464faf8b8d2c6f836649a",              # one out of tests/fixtures
    "0123456789ABCDEF01234567",              # pymongo lower-cases, but do not assume it
    "3f2504e0-4f89-41d3-9a0c-0305e82c3301",  # LocalQueue: str(uuid.uuid4())
])
def test_a_real_job_id_names_a_path_inside_the_uploads_folder(app, job_id):
    """The other half, so the guard cannot be satisfied by refusing everything. Both
    queue implementations have to keep working - a refused id means the query is
    quietly not promotable."""
    upload_path = query_upload_path(app, job_id)
    assert upload_path is not None, "a job id the backend really issues was refused"
    assert os.path.dirname(upload_path) == os.path.join(app.instance_path, "temp", "uploads")
    assert os.path.basename(upload_path) == job_id


# --- the half the first attempt at this fix broke -------------------------------------


@pytest.mark.parametrize("upload", ["unmapped", "dumped", "smda"])
def test_the_stored_file_is_reachable_from_the_job_id_alone(app, client, as_role, fake_mcrit, upload):
    """What promoting a query needs, for every kind of query alike.

    The promote route has a job id and the job's report; it has no way to hash the
    bytes it is looking for, since finding them is the point. Naming the file by a
    digest of the upload therefore made every `.smda` query permanently unpromotable
    on a clean system - the button never renders, and the route reports the file as
    gone. This is that requirement written down, so the next change to the naming has
    to keep it."""
    as_role("visitor")
    if upload == "smda":
        report = smda_report("cd" * 32)
        expected = json.dumps(report).encode()
        assert query_smda(client, report).status_code == 202
    else:
        expected = VICTIM_BINARY
        assert query_binary(client, expected, options=upload, base_addr="0x400000").status_code == 202

    job_id = fake_mcrit.issued[-1]
    upload_path = query_upload_path(app, job_id)
    assert os.path.isfile(upload_path), f"a {upload} query left nothing at its job id"
    assert open(upload_path, "rb").read() == expected


def test_the_name_is_the_id_the_backend_issued(app, client, as_role, fake_mcrit):
    """Stated directly, because it is the whole of the security property: the name is
    a value the request never supplied."""
    as_role("visitor")
    assert query_smda(client, smda_report("00" * 32)).status_code == 202

    assert list(stored(app)) == [fake_mcrit.issued[-1]]


# --- what moving the write after the request changed ----------------------------------


def test_nothing_is_stored_when_the_backend_refuses_the_query(app, client, as_role, fake_mcrit):
    """There is no job to promote, so there is nothing to keep. The file used to be
    written before the request was made, which left one behind for every upload the
    backend could not parse."""
    fake_mcrit.requestMatchesForUnmappedBinary = lambda *args, **kwargs: None
    as_role("visitor")

    assert query_binary(client, VICTIM_BINARY).status_code == 400
    assert stored(app) == {}


def test_a_write_that_fails_does_not_cost_the_submitter_the_job(app, client, as_role, fake_mcrit, monkeypatch):
    """The file is written after the job is queued, so by the time a disk fills up or a
    permission is wrong, the job is already running.

    There is no errorhandler in `mcritweb`, so an OSError out of this write would leave
    the submitter with a 500 and no job URL for work the backend is doing anyway. The
    copy is a convenience for promoting the query later; losing it costs a button, and
    the promote path already says so plainly. Losing the job link costs the query.
    """
    def _refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("mcritweb.views.analyze.open", _refuse, raising=False)
    as_role("visitor")

    response = query_binary(client, VICTIM_BINARY)

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    assert fake_mcrit.issued[-1] in response.get_data(as_text=True), \
        "the submitter was not told where the queued job is"
    assert stored(app) == {}


def test_the_same_bytes_queried_twice_are_stored_twice(app, client, as_role, fake_mcrit):
    """The trade this naming makes, so it is on the record rather than discovered.

    A content hash gave the two binary branches deduplication: the same file queried
    by two people cost one copy. A job id does not - `force_recalculation=True` means
    each query is its own job, so each gets its own file. Both copies are correct and
    each belongs to exactly one job, which is what the promote path wants; what it
    costs is disk, in a folder that is already never pruned."""
    as_role("visitor", username="first")
    assert query_binary(client, VICTIM_BINARY).status_code == 202
    as_role("visitor", username="second")
    assert query_binary(client, VICTIM_BINARY).status_code == 202

    files = stored(app)
    assert sorted(files) == sorted(fake_mcrit.issued)
    assert len(files) == 2, "two queries, two jobs, two files"
    assert set(files.values()) == {VICTIM_BINARY}
