#!/usr/bin/python
"""Exports are passed on as the backend's bytes, not parsed and serialised again (#202).

`json.dumps(client.getExportData(...))` held the whole export twice, as a dict and as the
string made from it. Asked in raw mode, the client hands over the backend's response,
and the export is its body minus the envelope mcrit's export routes put around it,
`{"status": "successful", "data": ...}`. An mcrit client that ignores `raw_responses`
for exports, as 1.9.0's does, still answers the parsed export, which is serialised as
before.
"""

import json
import logging

import flask
import pytest
from conftest import FakeMcritClient
from mcrit.storage.SampleEntry import SampleEntry

import mcritweb.views.data as data_views

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: key order the backend writes, which the sorted re-serialisation of the old path loses
EXPORTED = {"content": {"is_compressed": True, "num_samples": 2, "num_families": 1, "num_functions": 0}, "sample_entries": {"3": {"sample_id": 3}, "12": {"sample_id": 12}}}
EXPORTED_BYTES = json.dumps(EXPORTED).encode()


class BackendResponse:
    """Enough of a requests.Response for `fetch_export`."""

    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


class PassingThroughMcrit(FakeMcritClient):
    """Answers `getExportData` in raw mode with the backend's response, as a client that
    honours `raw_responses` for every method does; `answer` replaces it for a test."""

    answer = None

    def getSamplesByFamilyId(self, family_id):
        self._record("getSamplesByFamilyId", family_id)
        return {sample_id: SampleEntry(None, sample_id=sample_id, family_id=1) for sample_id in (3, 12)} if int(family_id) == 1 else None

    def getExportData(self, sample_ids=None, compress_data=True):
        self._record("getExportData", sample_ids, raw=self.raw)
        if not self.raw:
            return EXPORTED
        if self.answer is not None:
            return self.answer
        return BackendResponse(200, b'{"status": "successful", "data": ' + EXPORTED_BYTES + b"}")


@pytest.fixture
def fake_mcrit():
    return PassingThroughMcrit()


def flashes(client):
    with client.session_transaction() as session:
        return [message for _category, message in session.get("_flashes", [])]


def exports_requested(fake_mcrit):
    return [(args, kwargs) for name, args, kwargs in fake_mcrit.calls if name == "getExportData"]


@pytest.mark.parametrize(
    "request_export, sample_ids",
    [
        (lambda client: client.post("/data/export", data={"samples": ""}), None),
        (lambda client: client.post("/data/export", data={"samples": "3, 12"}), [3, 12]),
        (lambda client: client.get("/data/specific_export/family/1"), [3, 12]),
    ],
    ids=["all", "listed", "family"],
)
def test_the_export_is_the_backends_bytes(client, as_role, fake_mcrit, request_export, sample_ids):
    as_role("contributor")

    response = request_export(client)

    assert response.status_code == 200
    assert response.data == EXPORTED_BYTES
    assert response.headers["Content-Length"] == str(len(EXPORTED_BYTES))
    assert exports_requested(fake_mcrit) == [((sample_ids,), {"raw": True})]


def test_a_large_export_is_passed_on_whole(client, as_role, fake_mcrit, monkeypatch):
    monkeypatch.setattr(data_views, "EXPORT_CHUNK_SIZE", 7)
    as_role("contributor")

    response = client.get("/data/specific_export/family/1")

    assert response.data == EXPORTED_BYTES
    assert response.headers["Content-Length"] == str(len(EXPORTED_BYTES))


@pytest.mark.parametrize(
    "answer",
    [
        BackendResponse(200, b'{"status": "failed", "data": {"message": "Export exceeded assigned maximum memory limit and was cancelled."}}'),
        BackendResponse(500, b""),
        BackendResponse(200, b"not json at all"),
        None,
    ],
    ids=["failed-envelope", "server-error", "unknown-body", "no-response"],
)
def test_an_export_the_backend_did_not_make_is_reported_not_downloaded(client, as_role, fake_mcrit, answer):
    fake_mcrit.answer = answer if answer is not None else BackendResponse(502, b"")
    as_role("contributor")

    family = client.get("/data/specific_export/family/1")

    assert family.status_code == 302 and "/data/export" in family.headers["Location"]
    assert any('family "1"' in message for message in flashes(client))

    listed = client.post("/data/export", data={"samples": "3"})

    assert listed.status_code == 200 and "Content-disposition" not in listed.headers
    assert "MCRIT did not export the samples" in listed.get_data(as_text=True)


def test_a_client_that_ignores_raw_mode_exports_as_before(app, client, as_role, fake_mcrit, monkeypatch):
    """1.9.0's client answers the parsed export even in raw mode, and the file is the
    same `flask.json.dumps` of it that it always was."""
    monkeypatch.setattr(PassingThroughMcrit, "getExportData", lambda self, sample_ids=None, compress_data=True: EXPORTED)
    as_role("contributor")

    response = client.get("/data/specific_export/family/1")

    with app.app_context():
        assert response.data == flask.json.dumps(EXPORTED).encode("utf-8")
    assert response.headers["Content-Length"] == str(len(response.data))
