#!/usr/bin/python
"""What a page does when the backend fails, call site by call site (#43).

The backend here is the real McritClient for the one method under test, answering
through a stub session, so `handle_response` decides what the view gets back exactly as
it does in production. Every other method is served by the captured corpus.

Three answers, one per kind of failure:
- a page that needs the answer, or that says "not found" on a None, gets a 502 page for
  a failure on the backend's side (`with_server_errors`), and a 404 page naming what is
  missing where it cannot carry on without it (`require_result`);
- a page that can do without part of an answer still renders, without that part;
- a request that never completes gets a 503 page, and a bare 502 or 504 on `/api`.
"""

import io
import json
import logging
import re
from pathlib import Path

import pytest
import requests
from fixtureData import job_id_of
from mcrit.client.McritClient import McritClient

import mcritweb

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

SERVER_FAILED = "The MCRIT server could not handle the request"
NOTHING_RETURNED = "MCRIT server returned no data"
UNREACHABLE = "No connection to the MCRIT server"
TIMED_OUT = "The MCRIT server did not respond in time"

JOB_ID = job_id_of("matches_for_sample")
CROSS_JOB_ID = job_id_of("cross_compare")


class StubSession:
    """The session a McritClient sends through, answering every request alike."""

    def __init__(self, status=200, payload=None, raises=None):
        self.status = status
        self.payload = payload
        self.raises = raises
        self.urls = []

    def _answer(self, url, *args, **kwargs):
        self.urls.append(url)
        if self.raises is not None:
            raise self.raises
        response = requests.models.Response()
        response.status_code = self.status
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(self.payload).encode()
        return response

    get = post = put = delete = _answer


def failed(status):
    """What mcrit sends for a failure: `{"status": "failed", "data": {"message": ...}}`."""
    return StubSession(status=status, payload={"status": "failed", "data": {"message": f"failed with {status}"}})


def half_real(corpus, method, session, **extra):
    """A McritClient whose `method` runs the real client code against `session`, and
    whose every other method is the corpus's. `extra` adds methods the corpus lacks."""
    namespace = {"raw_variant": lambda self: corpus.raw_variant()}
    for name in dir(type(corpus)):
        if name.startswith("_") or name in ("raw_variant", method) or not hasattr(McritClient, name):
            continue
        namespace[name] = lambda self, *args, _name=name, **kwargs: getattr(corpus, _name)(*args, **kwargs)
    for name, implementation in extra.items():
        namespace[name] = lambda self, *args, _impl=implementation, **kwargs: _impl(*args, **kwargs)
    backend = type("HalfRealClient", (McritClient,), namespace)(mcrit_server="http://mcrit.test")
    backend._session = session
    return backend


@pytest.fixture
def backend(request, corpus_mcrit):
    """(method, session[, extra]) from the test's parameters, as a half-real client.
    A test without parameters sets its own factory and gets the corpus here."""
    if not hasattr(request, "param"):
        return corpus_mcrit
    method, session, *rest = request.param
    return half_real(corpus_mcrit, method, session, **(rest[0] if rest else {}))


@pytest.fixture
def fake_mcrit(backend):
    return backend


def a_function_id():
    from fixtureData import CorpusMcritClient
    return sorted(CorpusMcritClient()._functions)[0]


def two_functions_with_a_graph():
    """Two functions of a reference sample, which keep their control flow graph."""
    from fixtureData import CorpusMcritClient
    pool = next(iter(CorpusMcritClient()._functions_by_sample.values()))
    with_graph = [function_id for function_id, entry in sorted(pool.items()) if entry.xcfg]
    return with_graph[0], with_graph[1]


def page(response):
    return response.get_data(as_text=True)


# --- the client copy -------------------------------------------------------------

def test_with_server_errors_raises_on_a_copy_and_leaves_the_shared_client_alone():
    """get_client() hands every view of a request the same instance, and the call
    sites that degrade on None must keep getting None from it."""
    from mcritweb.backend_errors import with_server_errors

    shared = McritClient(mcrit_server="http://mcrit.test")
    shared._session = failed(500)

    raising = with_server_errors(shared)

    assert raising is not shared and raising.raise_server_errors
    assert shared.raise_server_errors is False
    assert shared.getSampleById(1) is None
    with pytest.raises(Exception, match="failed with 500"):
        raising.getSampleById(1)


def test_with_server_errors_returns_a_raw_client_and_a_fake_as_they_are(corpus_mcrit):
    from mcritweb.backend_errors import with_server_errors

    raw = McritClient(mcrit_server="http://mcrit.test", raw_responses=True)

    assert with_server_errors(raw) is raw
    assert with_server_errors(corpus_mcrit) is corpus_mcrit


# --- single-object pages: a failure is not "not found" ------------------------------

SINGLE_OBJECT_PAGES = [
    ("/explore/samples/1", "getSampleById"),
    ("/explore/families/1", "getFamily"),
    (f"/explore/functions/{a_function_id()}", "getFunctionById"),
    (f"/data/jobs/{JOB_ID}", "getJobData"),
    (f"/data/result/{JOB_ID}", "getJobData"),
    (f"/data/linkhunt/{JOB_ID}", "getJobData"),
    (f"/data/result/{JOB_ID}/download", "getJobData"),
    (f"/data/matches/function/{a_function_id()}/{a_function_id()}", "getMatchFunctionVs"),
    (f"/analyze/compare_function/{a_function_id()}", "getFunctionById"),
]


@pytest.mark.parametrize(
    "path, backend",
    [(path, (method, failed(500))) for path, method in SINGLE_OBJECT_PAGES],
    indirect=["backend"], ids=[path for path, _ in SINGLE_OBJECT_PAGES],
)
def test_a_single_object_page_reports_a_server_failure_as_one(client, as_role, path, backend):
    """Before, the 500 arrived as None and the page said the id did not exist."""
    as_role("admin")

    response = client.get(path)

    assert response.status_code == 502
    assert SERVER_FAILED in page(response)
    assert "failed with 500" in page(response)


@pytest.mark.parametrize(
    "path, backend",
    [(path, (method, failed(404))) for path, method in SINGLE_OBJECT_PAGES],
    indirect=["backend"], ids=[path for path, _ in SINGLE_OBJECT_PAGES],
)
def test_a_single_object_page_still_says_not_found_for_a_404(client, as_role, path, backend):
    """A guard: an unknown id keeps the page's own answer, as before."""
    as_role("admin")

    response = client.get(path)

    assert response.status_code in (200, 302)
    assert SERVER_FAILED not in page(response)


# --- call sites that cannot carry on without the answer ------------------------------

def samples_of_family(family_id):
    from fixtureData import CorpusMcritClient
    corpus = CorpusMcritClient()
    return {sample_id: entry for sample_id, entry in corpus._samples.items() if entry.family_id == family_id}


#: (what the test does, the client method it needs an answer from, what the page names,
#: methods the corpus does not serve)
REQUIRED = [
    ("GET /analyze/compare/1", "requestMatchesForSample", "a job for this comparison", {}),
    ("GET /analyze/compare/0/1", "requestMatchesForSampleVs", "a job for this comparison", {}),
    ("GET /analyze/start_cross_compare?samples=0,1", "requestMatchesCross", "a job for this cross comparison", {}),
    ("GET /analyze/blocks/sample/1", "requestUniqueBlocksForSamples", "a job for the unique blocks of this sample", {}),
    ("GET /analyze/blocks/family/1", "requestUniqueBlocksForFamily", "a job for the unique blocks of this family",
     {"getSamplesByFamilyId": lambda family_id: samples_of_family(family_id)}),
    ("GET /explore/getPicBlockMatches/0x1", "getMatchesForPicBlockHash", "the matches of this block", {}),
    ("POST /data/submit", "addBinarySample", "a job for the submitted sample", {}),
    ("POST /explore/modifyFamily", "deleteFamily", "a job for the family deletion", {}),
    ("POST /explore/modifySample", "deleteSample", "a job for the sample deletion", {}),
    (f"GET /data/linkhunt/{JOB_ID}", "getFunctionsBySampleId", "the functions of the reference sample", {}),
    ("GET /explore/fetchCombinedDotGraph/{}/{}".format(*two_functions_with_a_graph()), "getSampleById", "the sample of the first function", {}),
]


def perform(client, action):
    verb, path = action.split(" ", 1)
    if verb == "GET":
        return client.get(path)
    if path == "/data/submit":
        data = {"family": "test.family", "version": "1.0", "options": "unmapped", "form_type": "submit_form",
                "file": (io.BytesIO(b"MZ not in the corpus"), "sample.exe")}
        return client.post("/data/submit_or_query", data=data, content_type="multipart/form-data")
    if path == "/explore/modifyFamily":
        return client.post(path, data={"family_id": "1", "family_delete": "on"})
    return client.post(path, data={"sample_id": "1", "sample_delete": "on"})


@pytest.mark.parametrize(
    "action, what, backend",
    [(action, what, (method, failed(404), extra)) for action, method, what, extra in REQUIRED],
    indirect=["backend"], ids=[action for action, *_ in REQUIRED],
)
def test_a_refused_request_gets_a_page_naming_what_is_missing(client, as_role, action, what, backend):
    """Before, a None here was a BuildError or a TypeError: a 500 with a stack trace."""
    as_role("admin")

    response = perform(client, action)

    assert response.status_code == 404
    assert NOTHING_RETURNED in page(response)
    assert f"did not return {what}" in page(response)


@pytest.mark.parametrize(
    "action, backend",
    [(action, (method, failed(500), extra)) for action, method, _, extra in REQUIRED],
    indirect=["backend"], ids=[action for action, *_ in REQUIRED],
)
def test_a_server_failure_there_gets_the_server_failure_page(client, as_role, action, backend):
    as_role("admin")

    response = perform(client, action)

    assert response.status_code == 502
    assert SERVER_FAILED in page(response)


def test_a_refused_post_says_nothing_was_changed(client, as_role, corpus_mcrit, app):
    """A guard on the wording: a POST page must not leave the reader guessing."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, "deleteSample", failed(404))
    as_role("admin")

    response = perform(client, "POST /explore/modifySample")

    assert "Nothing was changed" in page(response)


# --- call sites that degrade: a failing backend still gets a page -------------------

def test_a_failed_sample_search_still_flashes_and_renders(client, as_role, corpus_mcrit, app):
    """The search adapter (#174) answers None for a failed search, and the list says so."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, "search_samples", failed(500))
    as_role("visitor")

    response = client.get("/explore/samples?query=sed", follow_redirects=True)

    assert response.status_code == 200
    assert "Could not search MCRIT" in page(response)
    assert SERVER_FAILED not in page(response)


def test_a_failed_read_of_the_dependencies_still_renders_the_cross_compare_overview(client, as_role, corpus_mcrit, app):
    """dependency_jobs: a failed chunk loses only its own ids, counted as missing."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, "getQueueData", failed(500))
    as_role("visitor")

    response = client.get(f"/data/jobs/{CROSS_JOB_ID}")

    assert response.status_code == 200
    assert "no longer in the system" in page(response)


@pytest.mark.parametrize("status", [500, 404], ids=["a failure", "a backend older than 1.12"])
def test_a_failed_batch_lookup_of_samples_falls_back_to_one_lookup_per_id(client, as_role, corpus_mcrit, app, status):
    """_entries_by_id (#191). A backend older than mcrit 1.12 has no /samples/ids and
    answers 404, which the client turns into None like any other failure; either way the
    selection is then looked up one sample at a time and still shows its names."""
    backend = half_real(corpus_mcrit, "getSamplesByIds", failed(status))
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    as_role("visitor")

    # a search that lists neither, so both are looked up rather than taken from its rows
    response = client.get("/analyze/unique_blocks?samples=0,1&query=zzzz-no-such-sample")

    assert response.status_code == 200
    assert any(url.endswith("/samples/ids") for url in backend._session.urls), "the batch route was not asked first"
    assert [call[1] for call in corpus_mcrit.calls if call[0] == "getSampleById"] == [(0,), (1,)]
    assert corpus_mcrit.getSampleById(1).getShortSha256() in page(response)
    assert "not confirmed by MCRIT" not in page(response)


@pytest.mark.parametrize("method", ["getSampleById", "getFamily"])
def test_a_failed_annotation_leaves_the_jobs_list_up(client, as_role, corpus_mcrit, app, method):
    """The jobs list names each job's sample and family; a label that fails stays blank."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, method, failed(500))
    as_role("visitor")

    response = client.get("/data/jobs?active=getMatchesForSample")

    assert response.status_code == 200
    assert SERVER_FAILED not in page(response)


def test_the_function_page_needs_its_sample(client, as_role, corpus_mcrit, app):
    """It links and describes the function through its sample throughout, and a None
    there was a 500 out of the template."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, "getSampleById", failed(404))
    as_role("visitor")

    response = client.get(f"/explore/functions/{a_function_id()}")

    assert response.status_code == 404
    assert "did not return the sample of this function" in page(response)


def test_a_family_read_back_in_raw_mode_is_untouched(client, as_role, corpus_mcrit):
    """family_read_back and sha256_second_opinion read the status themselves."""
    from mcritweb.backend_errors import with_server_errors

    raw = McritClient(mcrit_server="http://mcrit.test", raw_responses=True)
    raw._session = failed(500)

    assert with_server_errors(raw).getFamily(1).status_code == 500


# --- a request that never completes ----------------------------------------------

TRANSPORT = [
    (requests.exceptions.ConnectionError("refused"), UNREACHABLE, 502),
    (requests.exceptions.ReadTimeout("read timed out"), TIMED_OUT, 504),
    (requests.exceptions.ConnectTimeout("connect timed out"), TIMED_OUT, 504),
]


@pytest.mark.parametrize("error, message, _", TRANSPORT, ids=[type(e).__name__ for e, *_ in TRANSPORT])
@pytest.mark.parametrize("path, method", [
    ("/explore/samples/1", "getSampleById"),
    (f"/data/result/{JOB_ID}", "getJobData"),
    ("/explore/samples", "search_samples"),
], ids=["single object", "result", "list"])
def test_a_page_whose_backend_call_never_completes_says_so(client, as_role, corpus_mcrit, app, error, message, _, path, method):
    """Before, the requests exception left the view: a 500 with a stack trace."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, method, StubSession(raises=error))
    as_role("visitor")

    response = client.get(path)

    assert response.status_code == 503
    assert message in page(response)


def test_a_post_whose_backend_call_never_completes_does_not_claim_either_way(client, as_role, corpus_mcrit, app):
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(
        corpus_mcrit, "deleteSample", StubSession(raises=requests.exceptions.ReadTimeout("read timed out")))
    as_role("admin")

    response = perform(client, "POST /explore/modifySample")

    assert response.status_code == 503
    assert "Whether the request went through is unknown" in page(response)


def test_a_modify_whose_lookup_fails_does_not_blame_the_id(client, as_role, corpus_mcrit, app):
    """modifySample's broad except used to turn the failure into "No valid sample_id"."""
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: half_real(corpus_mcrit, "getSampleById", failed(500))
    as_role("admin")

    response = perform(client, "POST /explore/modifySample")

    assert response.status_code == 502
    assert "No valid sample_id" not in page(response)


@pytest.mark.parametrize("error, _, status", TRANSPORT, ids=[type(e).__name__ for e, *_ in TRANSPORT])
def test_the_api_answers_a_backend_that_never_answers_as_a_gateway(client, make_user, app, error, _, status):
    """A caller of /api cannot read a page: a bare 502, or 504 for a timeout."""
    class Unreachable:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise error
            return fail

    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: Unreachable()
    make_user(role="visitor")

    response = client.get("/api/status", headers={"apitoken": "apitoken-visitor"})

    assert response.status_code == status
    assert response.get_data() == b""


# --- the convention the pages rely on ------------------------------------------------

def test_every_require_result_wraps_a_call_made_with_server_errors():
    """require_result's page says "refused or not there"; that is only true of a None
    from a client that raises for a failure on the server's side."""
    views = Path(mcritweb.__file__).parent
    offenders = []
    for source in sorted(views.rglob("*.py")):
        text = source.read_text()
        for match in re.finditer(r"require_result\(\s*", text):
            if not text.startswith("with_server_errors(", match.end()):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{source.relative_to(views)}:{line}")
    offenders = [o for o in offenders if not o.startswith("backend_errors.py")]
    assert not offenders, f"require_result on a call that answers None for a server failure too: {offenders}"
