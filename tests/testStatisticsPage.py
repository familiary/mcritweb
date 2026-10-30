#!/usr/bin/python
"""The Statistics page against the status answers of current backends.

`STATUS_1_13` and `STATUS_1_16` are the testbed backend's `/status?with_pichash=True`
answers under mcrit 1.13.0 (smda 4.9.0) and, after upgrading the same corpus, mcrit 1.16.0
(smda 4.11.0), captured as they came. 1.16 adds the stale and unrehashable PicHash
counts, and reports the four samples from smda 1.5.12 as due for Recalculate PicHashes.
`STATUS_ALL_DUE` has 1.16's keys with made-up values, chosen so every maintenance note shows.
"""

import logging
import re

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

STATUS_1_13 = {"status": {
    "db_state": 126, "db_timestamp": "2026-09-23T20:30:12", "storage_type": "mongodb", "num_bands": 20,
    "num_samples": 66, "num_families": 16, "num_functions": 26436, "num_pichashes": 10426,
    "smda_version": "4.9.0", "escaper_fingerprint": "110c9d152232a2e6",
    "escaper_fingerprints": {"intel": "110c9d152232a2e6", "aarch64": "3ec83d52504cff67", "arm": "unavailable",
                             "cil": "d9a8588e0ec32ad7", "dalvik": "9ab8f754c35aba3f"},
    "inline_xcfg_remaining": False, "minhash_compatibility_threshold": "4.4.5",
    "num_samples_with_stale_minhashes": 0, "num_samples_with_stale_picblockhashes": 0,
}}

STATUS_1_16 = {"status": {
    "db_state": 126, "db_timestamp": "2026-09-23T20:30:12", "storage_type": "mongodb", "num_bands": 20,
    "num_samples": 66, "num_families": 16, "num_functions": 26436, "num_pichashes": 10426,
    "smda_version": "4.11.0", "escaper_fingerprint": "110c9d152232a2e6",
    "escaper_fingerprints": {"intel": "110c9d152232a2e6", "aarch64": "3ec83d52504cff67", "arm": "unavailable",
                             "cil": "d9a8588e0ec32ad7", "dalvik": "9ab8f754c35aba3f"},
    "inline_xcfg_remaining": False, "minhash_compatibility_threshold": "4.4.5",
    "num_samples_with_stale_minhashes": 0, "num_samples_with_stale_picblockhashes": 0,
    "num_samples_with_stale_pichashes": 4, "num_samples_pichash_unrehashable": 0,
}}

STATUS_ALL_DUE = {"status": {
    **STATUS_1_16["status"],
    "num_functions": 12172614,
    "inline_xcfg_remaining": True,
    "num_samples_with_stale_minhashes": 3, "num_samples_with_stale_picblockhashes": 2,
    "num_samples_with_stale_pichashes": 1500, "num_samples_pichash_unrehashable": 4,
}}


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def answering(app, corpus_mcrit, status, version):
    corpus_mcrit.getStatus = lambda *args, **kwargs: status
    corpus_mcrit.getVersion = lambda *args, **kwargs: version
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: corpus_mcrit


def cell_after(page, label):
    match = re.search(re.escape(label) + r"\s*</th>\s*<td[^>]*>([^<]*)</td>", " ".join(page.split()))
    return match.group(1).strip() if match else None


def statistics(client, as_role):
    as_role("visitor")
    response = client.get("/explore/statistics")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_the_page_shows_all_three_versions(client, as_role, app, corpus_mcrit):
    answering(app, corpus_mcrit, STATUS_1_16, {"version": "1.16.0"})
    app.config["MCRITWEB_VERSION"] = "1.6.0"

    page = statistics(client, as_role)

    assert cell_after(page, "MCRITweb") == "1.6.0"
    assert cell_after(page, "MCRIT backend") == "1.16.0"
    assert cell_after(page, "SMDA (backend)") == "4.11.0"
    assert cell_after(page, "mcrit client library") not in (None, "")


def test_a_version_the_backend_does_not_give_says_unknown(client, as_role, app, corpus_mcrit):
    """getVersion answers None for a refusal; the page still renders its status."""
    answering(app, corpus_mcrit, STATUS_1_13, None)

    page = statistics(client, as_role)

    assert cell_after(page, "MCRIT backend") == "unknown"
    assert cell_after(page, "Functions") == "26,436"


def test_an_unreadable_mcritweb_version_says_unknown(client, as_role, app, corpus_mcrit):
    """MCRITWEB_VERSION is None when the version could not be read from the checkout."""
    answering(app, corpus_mcrit, STATUS_1_13, {"version": "1.13.0"})
    app.config["MCRITWEB_VERSION"] = None

    page = statistics(client, as_role)

    assert cell_after(page, "MCRITweb") == "unknown"


def test_a_backend_without_a_status_still_shows_the_versions(client, as_role, app, corpus_mcrit):
    answering(app, corpus_mcrit, None, {"version": "1.13.0"})

    page = statistics(client, as_role)

    assert "did not answer with its status" in page
    assert cell_after(page, "MCRIT backend") == "1.13.0"
    assert cell_after(page, "SMDA (backend)") == "not reported"
    assert "Maintenance" not in page


@pytest.mark.parametrize("status", [STATUS_1_13, STATUS_1_16], ids=["mcrit 1.13", "mcrit 1.16"])
def test_the_sections_carry_labels_not_key_names(client, as_role, app, corpus_mcrit, status):
    answering(app, corpus_mcrit, status, {"version": "1.13.0"})

    page = statistics(client, as_role)

    for heading in ("Versions", "Corpus", "Index", "Maintenance", "Escaper fingerprints"):
        assert f">{heading}</h2>" in page
    assert cell_after(page, "Unique PicHashes") == "10,426"
    assert cell_after(page, "MinHash compatibility threshold") == "4.4.5"
    assert cell_after(page, "dalvik") == "9ab8f754c35aba3f"
    assert cell_after(page, "Last change (UTC)") == "2026-09-23 20:30:12"
    # every key of these releases has a label, so nothing is left for Other
    assert ">Other</h2>" not in page
    assert "num_" not in page


def test_the_upgraded_testbed_is_told_to_recalculate_its_pichashes(client, as_role, app, corpus_mcrit):
    """The captured 1.16 answer: one note, for the four samples an old smda hashed."""
    answering(app, corpus_mcrit, STATUS_1_16, {"version": "1.16.0"})

    page = " ".join(statistics(client, as_role).split())

    assert page.count("fa-triangle-exclamation") == 1
    assert "4 samples have PicHashes from an smda older than the compatibility threshold." in page
    assert "Recalculate PicHashes" in page
    assert cell_after(page, "SMDA (backend)") == "4.11.0"
    assert cell_after(page, "MCRIT backend") == "1.16.0"


def test_a_healthy_backend_has_no_maintenance_notes(client, as_role, app, corpus_mcrit):
    answering(app, corpus_mcrit, STATUS_1_13, {"version": "1.13.0"})

    page = statistics(client, as_role)

    assert "reports nothing that needs a maintenance job" in page
    assert "fa-triangle-exclamation" not in page


def test_each_due_maintenance_job_gets_a_note(client, as_role, app, corpus_mcrit):
    answering(app, corpus_mcrit, STATUS_ALL_DUE, {"version": "1.16.0"})

    page = " ".join(statistics(client, as_role).split())

    assert page.count("fa-triangle-exclamation") == 5
    assert "3 samples were MinHashed" in page and "Repair stale MinHashes" in page
    assert "2 non-Intel samples" in page
    assert "1,500 samples have PicHashes" in page and "Recalculate PicHashes" in page
    assert "4 samples cannot have their PicHashes recalculated" in page
    assert "migrate_xcfg_split" in page
    assert cell_after(page, "Functions") == "12,172,614"


def test_a_key_the_page_does_not_know_is_listed_under_other(client, as_role, app, corpus_mcrit):
    future = {"status": {**STATUS_ALL_DUE["status"], "num_widgets": 1234567, "widget_mode": {"a": 1, "b": True}}}
    answering(app, corpus_mcrit, future, {"version": "1.17.0"})

    page = statistics(client, as_role)

    assert ">Other</h2>" in page
    assert cell_after(page, "num_widgets") == "1,234,567"
    assert cell_after(page, "widget_mode") == "a: 1, b: yes"


def test_what_the_backend_sends_is_escaped(client, as_role, app, corpus_mcrit):
    hostile = "<script>alert(1)</script>"
    status = {"status": {**STATUS_1_13["status"], "smda_version": hostile, hostile: hostile,
                         "escaper_fingerprints": {hostile: hostile}}}
    answering(app, corpus_mcrit, status, {"version": hostile})

    page = statistics(client, as_role)

    assert hostile not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
