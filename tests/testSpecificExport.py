#!/usr/bin/python
"""The per-sample and per-family export buttons, `/data/specific_export/<type>/<id>`.

The backend reads an empty selection as "every sample". The sample branch looked the
sample up first and passed on the id only if the backend knew it, so an unknown id
exported the whole corpus; the family branch did the same for a family without
samples, and raised on one the backend does not know. Asking for the one sample id
instead is a request less (issue #207), and the backend leaves out an id it does not
know, so the export itself says whether there was a sample.
"""

import json
import logging
import unittest

import pytest
from conftest import FakeMcritClient
from mcrit.storage.SampleEntry import SampleEntry

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

KNOWN_SAMPLE_IDS = (3, 12)
#: family 1 holds both samples; family 0 exists but is empty, as mcrit's family 0 can be
FAMILY_SAMPLES = {1: KNOWN_SAMPLE_IDS, 0: ()}


class ExportingMcrit(FakeMcritClient):
    """Answers `getExportData` the way MinHashIndex.getExportData selects: an empty or
    missing selection exports every sample, and an id it does not know is left out."""

    def getSamplesByFamilyId(self, family_id):
        self._record("getSamplesByFamilyId", family_id)
        # the real client puts the id into the URL, so a string of digits works there too
        family_id = int(family_id)
        if family_id not in FAMILY_SAMPLES:
            return None
        return {sample_id: SampleEntry(None, sample_id=sample_id, family_id=family_id) for sample_id in FAMILY_SAMPLES[family_id]}

    def getExportData(self, sample_ids=None, compress_data=True):
        self._record("getExportData", sample_ids)
        exported = [sample_id for sample_id in KNOWN_SAMPLE_IDS if not sample_ids or sample_id in sample_ids]
        return {
            "content": {"is_compressed": compress_data, "num_families": 1, "num_samples": len(exported), "num_functions": 0},
            "sample_entries": {str(sample_id): {"sample_id": sample_id} for sample_id in exported},
        }


@pytest.fixture
def fake_mcrit():
    return ExportingMcrit()


def _exports_requested(fake_mcrit):
    return [args for name, args, _ in fake_mcrit.calls if name == "getExportData"]


def _flashes(client):
    with client.session_transaction() as session:
        return [message for _category, message in session.get("_flashes", [])]


def test_a_sample_is_exported_with_one_request(client, as_role, fake_mcrit):
    as_role("contributor")
    response = client.get("/data/specific_export/samples/12")
    assert response.status_code == 200
    assert response.headers["Content-disposition"] == "attachment; filename=export_samples.json"
    assert json.loads(response.data)["content"]["num_samples"] == 1
    assert [name for name, _, _ in fake_mcrit.calls] == ["getExportData"]


def test_an_unknown_sample_is_reported_not_swapped_for_every_sample(client, as_role, fake_mcrit):
    as_role("contributor")
    response = client.get("/data/specific_export/samples/9999")
    assert response.status_code == 302
    assert "/data/export" in response.headers["Location"]
    assert _exports_requested(fake_mcrit) == [([9999],)]
    assert any('sample "9999"' in message for message in _flashes(client))


@pytest.mark.parametrize(
    "item_id",
    ["abc", "-1", "1,2", "\uff11\uff12", "9" * 5000],
    ids=["letters", "negative", "list", "fullwidth-digits", "5000-digits"],
)
@pytest.mark.parametrize("export_type", ["samples", "family"])
def test_an_id_the_route_refuses_never_reaches_the_backend(client, as_role, fake_mcrit, export_type, item_id):
    as_role("contributor")
    response = client.get(f"/data/specific_export/{export_type}/{item_id}")
    assert response.status_code == 302
    assert fake_mcrit.calls == []


def test_a_family_is_exported_with_its_samples(client, as_role, fake_mcrit):
    as_role("contributor")
    response = client.get("/data/specific_export/family/1")
    assert response.status_code == 200
    assert response.headers["Content-disposition"] == "attachment; filename=export_family_1.json"
    assert _exports_requested(fake_mcrit) == [([3, 12],)]


@pytest.mark.parametrize("family_id", [0, 99])
def test_an_empty_or_unknown_family_is_reported_not_swapped_for_every_sample(client, as_role, fake_mcrit, family_id):
    """Family 0 is never deleted when it empties, so it is the empty family a real
    instance is most likely to have; an unknown family used to raise on None."""
    as_role("contributor")
    response = client.get(f"/data/specific_export/family/{family_id}")
    assert response.status_code == 302
    assert "/data/export" in response.headers["Location"]
    assert _exports_requested(fake_mcrit) == []
    assert any(f'family "{family_id}"' in message for message in _flashes(client))


if __name__ == "__main__":
    unittest.main()
