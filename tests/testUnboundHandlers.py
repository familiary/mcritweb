#!/usr/bin/python
"""Fallback request paths must keep their required values in scope (#201), and the API's
function-ID list has to be told from a malformed body the way the backend tells them apart."""

import logging
from types import SimpleNamespace

import pytest
from fixtureData import RawResponse

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(recording_mcrit):
    return recording_mcrit


@pytest.mark.parametrize(
    ("body", "expected_ids", "backend_status"),
    [(b"not ids", [], 400), (b"1, 2", [1, 2], 200)],
)
def test_functions_post_forwards_label_flag_on_both_paths(
        client, make_user, fake_mcrit, body, expected_ids, backend_status):
    make_user("visitor")

    def get_functions_by_ids(ids, **kwargs):
        fake_mcrit._record("getFunctionsByIds", ids, **kwargs)
        return RawResponse(backend_status, {})

    fake_mcrit.getFunctionsByIds = get_functions_by_ids

    response = client.post(
        "/api/functions?with_label_only=true",
        headers={"apitoken": "apitoken-visitor"},
        data=body,
    )

    assert response.status_code == backend_status
    assert [call for call in fake_mcrit.calls if call[0] == "getFunctionsByIds"] == [
        ("getFunctionsByIds", (expected_ids,), {"with_label_only": True})
    ]


def test_hash_list_redirect_does_not_search_the_whole_corpus(client, as_role, fake_mcrit):
    as_role("visitor")
    sample_hash = "a" * 64
    fake_mcrit.getSampleBySha256 = lambda value: SimpleNamespace(sample_id=7) if value == sample_hash else None
    fake_mcrit.search_samples = lambda *args, **kwargs: pytest.fail("unneeded sample search")

    response = client.post("/analyze/cross_compare_from_hash_list", data={"hashlist": sample_hash})

    assert response.status_code == 302
    assert "/analyze/cross_compare" in response.headers["Location"]
    assert "samples=7" in response.headers["Location"]


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_an_id_list_reaches_the_backend_whatever_its_content_type(client, make_user, fake_mcrit, content_type):
    """`curl --data "1,2"` sends a form content type, and Flask parses a form body into
    `request.form`, leaving `request.data` empty - so a valid list used to be taken for a
    malformed one. The backend reads the raw body; the passthrough has to as well."""
    make_user("visitor")

    def get_functions_by_ids(ids, **kwargs):
        fake_mcrit._record("getFunctionsByIds", ids, **kwargs)
        return RawResponse(200, {})

    fake_mcrit.getFunctionsByIds = get_functions_by_ids

    response = client.post("/api/functions", headers={"apitoken": "apitoken-visitor"}, data=b"1,2", content_type=content_type)

    assert response.status_code == 200
    assert [call for call in fake_mcrit.calls if call[0] == "getFunctionsByIds"] == [
        ("getFunctionsByIds", ([1, 2],), {"with_label_only": False})
    ]
