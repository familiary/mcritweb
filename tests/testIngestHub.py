#!/usr/bin/python
"""E2E Test Suite for Ingestion Hub and Authorization Gating (Tiers 1-4).

Covers:
- Tier 1: Core feature coverage for `/data/ingest` and `/data/submit_or_query`:
  * Default tab is `query`
  * `/data/ingest/submit` displays submission form for contributor/admin
  * `/data/ingest/submit` displays informative notice/read-only mode for visitor
  * `/data/ingest/import` displays import form
  * POST `/data/submit_or_query` with `form_type="query_form"` succeeds for visitor (HTTP 202)
  * POST `/data/submit_or_query` with `form_type="submit_form"` succeeds for contributor (HTTP 202)
  * POST `/data/submit_or_query` with `form_type="submit_form"` fails for visitor (HTTP 403)
  * POST `/data/request_filename_info` succeeds for visitor (HTTP 200 JSON)
- Tier 2: Boundary & Corner Cases:
  * Unauthenticated access redirects to login
  * Pending role rejected with HTTP 403 Forbidden
  * Invalid tab names fall back gracefully to query
  * CSRF enforcement on state-modifying requests (HTTP 400 when token missing)
  * Missing file or invalid form_type returns HTTP 400 Bad Request
  * Malformed JSON in filename info auto-detection returns fallback 200
  * Dumped sample without base address rejected with HTTP 400
- Tier 3: Combinatorial & Cross-Feature Coverage:
  * SMDA report upload handling across both query and submit workflows
  * Memory dump parameters (bitness, base address) across query and submit
  * Legacy route backward compatibility (/analyze/query, /data/submit, /data/import)
- Tier 4: Real-World End-to-End Scenarios:
  * Visitor uploads binary for similarity query, verifying job queued and file persisted
  * Contributor auto-detects parameters and submits sample to corpus
  * Contributor imports database export bundle and verifies import completion
"""

import io
import json
import pathlib

from conftest import FAKE_MINHASH_HASH, FAKE_SHINGLER_HASH

from mcritweb.csrf import ENABLED_KEY
from mcritweb.views.utility import query_upload_path


def _smda_report_dict(sha256="11" * 32, family="sample.family"):
    """Smallest valid dictionary accepted as an SmdaReport."""
    return {
        "architecture": "intel",
        "base_addr": 4194304,
        "binary_size": 64,
        "bitness": 32,
        "code_areas": [],
        "code_sections": [],
        "confidence_threshold": 0.5,
        "disassembly_errors": {},
        "execution_time": 0.1,
        "identified_alignment": 16,
        "metadata": {
            "family": family,
            "filename": "sample.smda",
            "version": "1.0",
            "component": "",
            "is_library": False,
        },
        "message": "",
        "oep": 4194304,
        "sha256": sha256,
        "smda_version": "4.4.4",
        "statistics": {},
        "status": "ok",
        "timestamp": "2026-11-05T12-00-00",
        "xcfg": {},
    }


def _export_bundle_dict():
    """Valid dictionary shaped as an MCRIT database export bundle."""
    sha256 = "ab" * 32
    return {
        "config": {
            "version": "1.5.3",
            "shingler": FAKE_SHINGLER_HASH,
            "minhash": FAKE_MINHASH_HASH,
        },
        "content": {"is_compressed": False, "num_samples": 1, "num_functions": 1, "num_families": 1},
        "family_mapping": {"1": "test.family"},
        "sample_entries": {sha256: {"sample_id": 1, "family_id": 1}},
        "function_entries": {sha256: {"1": {"function_id": 1, "sample_id": 1}}},
    }


# =============================================================================
# Tier 1: Core Feature Coverage
# =============================================================================

def test_ingest_get_defaults_to_query_tab(client, as_role):
    """GET /data/ingest should render the unified ingestion hub defaulting to the query tab."""
    as_role("visitor")
    response = client.get("/data/ingest")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "submit_or_query" in html or "dropzone" in html
    # Query tab is selected/active by default
    assert "query" in html.lower()


def test_ingest_get_submit_tab_displays_form_for_contributor_and_admin(client, as_role):
    """GET /data/ingest/submit displays corpus submission fields for contributors and admins."""
    for role in ("contributor", "admin"):
        as_role(role)
        response = client.get("/data/ingest/submit")
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'name="family"' in html or 'id="family"' in html
        assert 'name="version"' in html or 'id="version"' in html


def test_ingest_get_submit_tab_displays_read_only_notice_for_visitor(client, as_role):
    """GET /data/ingest/submit displays an informational notice or disabled form for visitors."""
    as_role("visitor")
    response = client.get("/data/ingest/submit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Must indicate submission is restricted to contributors or provide informational banner
    notice_found = (
        "contributor" in html.lower()
        or "permission" in html.lower()
        or "restricted" in html.lower()
        or "disabled" in html.lower()
    )
    assert notice_found, "Expected visitor to receive informational restriction on submit tab"


def test_ingest_get_import_tab_displays_form_for_contributor(client, as_role):
    """GET /data/ingest/import displays the export bundle import dropzone for contributors."""
    as_role("contributor")
    response = client.get("/data/ingest/import")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "import" in html.lower()
    assert "dropzone" in html.lower() or "upload" in html.lower()


def test_submit_or_query_query_form_succeeds_for_visitor(client, as_role, fake_mcrit):
    """POST /data/submit_or_query in query mode succeeds for visitor with HTTP 202."""
    as_role("visitor")
    payload = {
        "form_type": "query_form",
        "options": "unmapped",
        "file": (io.BytesIO(b"MZtestquerybinarycontent"), "sample_query.exe"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")

    assert response.status_code == 202
    assert "/data/jobs/" in response.get_data(as_text=True)
    forwarded = [call for call in fake_mcrit.calls if call[0] == "requestMatchesForUnmappedBinary"]
    assert len(forwarded) == 1


def test_submit_or_query_submit_form_succeeds_for_contributor(client, as_role, fake_mcrit):
    """POST /data/submit_or_query in submit mode succeeds for contributor with HTTP 202."""
    as_role("contributor")
    payload = {
        "form_type": "submit_form",
        "options": "unmapped",
        "family": "win.testmalware",
        "version": "1.0",
        "file": (io.BytesIO(b"MZtestsubmitbinarycontent"), "sample_submit.exe"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")

    assert response.status_code == 202
    assert "/data/jobs/" in response.get_data(as_text=True)
    forwarded = [call for call in fake_mcrit.calls if call[0] == "addBinarySample"]
    assert len(forwarded) == 1


def test_submit_or_query_submit_form_fails_for_visitor(client, as_role, fake_mcrit):
    """POST /data/submit_or_query in submit mode is rejected for visitor with HTTP 403."""
    as_role("visitor")
    payload = {
        "form_type": "submit_form",
        "options": "unmapped",
        "family": "win.forbiddenmalware",
        "version": "1.0",
        "file": (io.BytesIO(b"MZtestsubmitbinarycontent"), "forbidden.exe"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")

    assert response.status_code == 403
    forwarded = [call for call in fake_mcrit.calls if call[0] == "addBinarySample"]
    assert len(forwarded) == 0, "addBinarySample must not be called when visitor attempts corpus submission"


def test_request_filename_info_succeeds_for_visitor(client, as_role):
    """POST /data/request_filename_info succeeds for visitor with HTTP 200 and parsed parameters."""
    as_role("visitor")
    body = {
        "filename": "sample_dump_0x00400000.bin",
        "file_header": "MZ\x90\x00",
    }
    response = client.post(
        "/data/request_filename_info",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data["dump"] is True
    assert data["base_addr"] == "0x400000"
    assert data["bitness"] == 32


# =============================================================================
# Tier 2: Boundary & Corner Cases
# =============================================================================

def test_ingest_requires_login_unauthenticated_redirects(client):
    """Unauthenticated access to /data/ingest redirects to /login."""
    response = client.get("/data/ingest")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_ingest_rejects_pending_role(client, as_role):
    """Pending user role is rejected with HTTP 403 on /data/ingest."""
    as_role("pending")
    response = client.get("/data/ingest")
    assert response.status_code == 403


def test_ingest_invalid_tab_handles_gracefully(client, as_role):
    """An unknown tab name on /data/ingest/<tab> falls back or redirects to query without 500."""
    as_role("visitor")
    response = client.get("/data/ingest/nonexistent_tab_foo")
    # Must be either a graceful 200 (falling back to default query tab) or a 302 redirect to /data/ingest
    assert response.status_code in (200, 302)
    if response.status_code == 200:
        html = response.get_data(as_text=True)
        assert "query" in html.lower()


def test_submit_or_query_missing_csrf_returns_400(client, app, as_role):
    """With CSRF protection active, a POST without token returns HTTP 400 Bad Request."""
    app.config[ENABLED_KEY] = True
    as_role("contributor")

    payload = {
        "form_type": "query_form",
        "options": "unmapped",
        "file": (io.BytesIO(b"MZtestbinary"), "sample.exe"),
    }
    # No csrf_token provided
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")
    assert response.status_code == 400


def test_submit_or_query_missing_file_returns_400(client, as_role):
    """POST /data/submit_or_query without a file returns HTTP 400 Bad Request."""
    as_role("contributor")
    payload = {
        "form_type": "query_form",
        "options": "unmapped",
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")
    assert response.status_code == 400


def test_submit_or_query_invalid_form_type_returns_400(client, as_role):
    """POST /data/submit_or_query with an invalid form_type returns HTTP 400 Bad Request."""
    as_role("contributor")
    payload = {
        "form_type": "unexpected_custom_form",
        "options": "unmapped",
        "file": (io.BytesIO(b"MZtestbinary"), "sample.exe"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")
    assert response.status_code == 400


def test_request_filename_info_malformed_json_returns_fallback_200(client, as_role):
    """POST /data/request_filename_info with invalid JSON does not crash and returns fallback."""
    as_role("visitor")
    response = client.post(
        "/data/request_filename_info",
        data=b"this-is-not-valid-json",
        content_type="application/json",
    )
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data.get("dump") is False


def test_submit_or_query_dumped_without_base_address_returns_400(client, as_role):
    """POST /data/submit_or_query for a memory dump without a valid base address returns HTTP 400."""
    as_role("contributor")
    payload = {
        "form_type": "submit_form",
        "options": "dumped",
        "family": "testfam",
        "version": "1.0",
        "bitness": "32",
        "base_addr": "invalid_base_addr_hex",
        "file": (io.BytesIO(b"MZrawdumpbytes"), "dump.raw"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")
    assert response.status_code == 400


# =============================================================================
# Tier 3: Combinatorial & Cross-Feature Coverage
# =============================================================================

def test_submit_or_query_smda_report_handling(client, as_role, fake_mcrit):
    """SMDA report uploads are properly dispatched for both query and submit workflows."""
    report_data = json.dumps(_smda_report_dict(sha256="cc" * 32, family="smda.family")).encode("utf-8")

    # 1. Query with SMDA report as visitor
    as_role("visitor")
    query_payload = {
        "form_type": "query_form",
        "options": "smda",
        "file": (io.BytesIO(report_data), "report.smda"),
    }
    res_query = client.post("/data/submit_or_query", data=query_payload, content_type="multipart/form-data")
    assert res_query.status_code == 202
    assert any(c[0] == "requestMatchesForSmdaReport" for c in fake_mcrit.calls)

    # 2. Submit with SMDA report as contributor
    as_role("contributor")
    submit_payload = {
        "form_type": "submit_form",
        "options": "smda",
        "family": "smda.family",
        "version": "1.0",
        "file": (io.BytesIO(report_data), "report.smda"),
    }
    res_submit = client.post("/data/submit_or_query", data=submit_payload, content_type="multipart/form-data")
    assert res_submit.status_code == 202
    assert any(c[0] == "addReport" for c in fake_mcrit.calls)


def test_submit_or_query_mapped_dump_parameters(client, as_role, fake_mcrit):
    """Memory dump parameters (base address, bitness) correctly pass to backend."""
    dump_bytes = b"MZmemorydumpbytesatbase"

    # Query mapped binary
    as_role("visitor")
    query_payload = {
        "form_type": "query_form",
        "options": "dumped",
        "base_addr": "0x400000",
        "file": (io.BytesIO(dump_bytes), "dump_0x400000_32bit.raw"),
    }
    res_query = client.post("/data/submit_or_query", data=query_payload, content_type="multipart/form-data")
    assert res_query.status_code == 202
    forwarded_query = [c for c in fake_mcrit.calls if c[0] == "requestMatchesForMappedBinary"]
    assert len(forwarded_query) == 1
    assert forwarded_query[0][1][1] == 0x400000

    # Submit mapped binary
    as_role("contributor")
    submit_payload = {
        "form_type": "submit_form",
        "options": "dumped",
        "family": "dumped.family",
        "version": "1.0",
        "bitness": "32",
        "base_addr": "0x400000",
        "file": (io.BytesIO(dump_bytes), "dump_0x400000_32bit.raw"),
    }
    res_submit = client.post("/data/submit_or_query", data=submit_payload, content_type="multipart/form-data")
    assert res_submit.status_code == 202
    forwarded_submit = [c for c in fake_mcrit.calls if c[0] == "addBinarySample"]
    assert len(forwarded_submit) == 1
    assert forwarded_submit[0][2].get("base_addr") == 0x400000
    assert forwarded_submit[0][2].get("bitness") == 32


def test_legacy_routes_remain_functional(client, as_role):
    """Legacy routes (/analyze/query, /data/submit, /data/import) remain functional."""
    # /analyze/query accessible to visitor
    as_role("visitor")
    res_query = client.get("/analyze/query")
    assert res_query.status_code == 200

    # /data/submit accessible to contributor
    as_role("contributor")
    res_submit = client.get("/data/submit")
    assert res_submit.status_code == 200

    # /data/import accessible to contributor
    res_import = client.get("/data/import")
    assert res_import.status_code == 200


# =============================================================================
# Tier 4: Real-World End-to-End Scenarios
# =============================================================================

def test_e2e_visitor_query_persists_upload_file(client, app, as_role, fake_mcrit):
    """End-to-End: Visitor queries binary, job is queued, and upload is filed under job id."""
    as_role("visitor", username="researcher_alice")
    binary_content = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00"

    payload = {
        "form_type": "query_form",
        "options": "unmapped",
        "file": (io.BytesIO(binary_content), "malware_artifact.bin"),
    }
    response = client.post("/data/submit_or_query", data=payload, content_type="multipart/form-data")
    assert response.status_code == 202

    # Extract job id from response URL
    job_url = response.get_data(as_text=True)
    assert "/data/jobs/" in job_url
    job_id = job_url.split("/data/jobs/")[1].split("?")[0]

    # Verify upload was stored in instance temp directory under the job id
    stored_path = query_upload_path(app, job_id)
    assert stored_path is not None
    assert pathlib.Path(stored_path).exists()
    assert pathlib.Path(stored_path).read_bytes() == binary_content


def test_e2e_contributor_submit_sample_with_auto_detected_filename(client, as_role, fake_mcrit):
    """End-to-End: Contributor auto-detects filename parameters, then submits sample."""
    as_role("contributor", username="contributor_bob")

    # Step 1: Detect filename info
    detect_resp = client.post(
        "/data/request_filename_info",
        data=json.dumps({"filename": "trojan_dump_0x0000000100000000.raw", "file_header": "MZ"}),
        content_type="application/json",
    )
    assert detect_resp.status_code == 200
    meta = json.loads(detect_resp.get_data(as_text=True))
    assert meta["dump"] is True
    assert meta["base_addr"] == "0x100000000"
    assert meta["bitness"] == 64

    # Step 2: Submit with detected parameters
    submit_payload = {
        "form_type": "submit_form",
        "options": "dumped",
        "family": "trojan.apt28",
        "version": "2.1",
        "base_addr": meta["base_addr"],
        "bitness": str(meta["bitness"]),
        "file": (io.BytesIO(b"MZdumpbytes64bit"), "trojan_dump_0x0000000100000000.raw"),
    }
    submit_resp = client.post("/data/submit_or_query", data=submit_payload, content_type="multipart/form-data")
    assert submit_resp.status_code == 202
    assert "/data/jobs/" in submit_resp.get_data(as_text=True)


def test_e2e_contributor_imports_database_export_bundle(client, as_role, fake_mcrit):
    """End-to-End: Contributor uploads database export bundle and verifies completion report."""
    as_role("contributor", username="admin_charlie")
    export_json = json.dumps(_export_bundle_dict()).encode("utf-8")

    response = client.post(
        "/data/import",
        data={"file": (io.BytesIO(export_json), "mcrit_corpus_export.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    # Verify report is carried to import_complete
    complete_resp = client.get("/data/import_complete")
    assert complete_resp.status_code == 200
    html = complete_resp.get_data(as_text=True)
    assert "Import completed" in html or "num_samples_imported" in html
