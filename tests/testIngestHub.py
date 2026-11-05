#!/usr/bin/python
"""Tests for the Ingestion Hub (/data/ingest).

Verifies:
- Unauthenticated access redirects to /login.
- Default view displays the Query Sample tab.
- Submitting and importing tabs render appropriately based on user role.
- Visitors see an informative notice when viewing submission or import tabs.
- Contributors and administrators see the respective upload dropzones.
- Invalid tab parameters fall back gracefully to query.
"""



def test_anonymous_access_redirects_to_login(client):
    response = client.get("/data/ingest")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_visitor_can_access_ingest_query(client, as_role):
    as_role("visitor")
    response = client.get("/data/ingest")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Ingestion Hub" in html
    assert "Query Sample" in html
    assert "pane-query" in html


def test_visitor_sees_contributor_notice_on_submit_tab(client, as_role):
    as_role("visitor")
    response = client.get("/data/ingest/submit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Contributor Role Required" in html
    assert "Binary sample submissions to the permanent corpus require contributor or administrator privileges." in html


def test_contributor_sees_submit_dropzone(client, as_role):
    as_role("contributor")
    response = client.get("/data/ingest/submit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Submit a new binary sample" in html


def test_visitor_sees_contributor_notice_on_import_tab(client, as_role):
    as_role("visitor")
    response = client.get("/data/ingest/import")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Contributor Role Required" in html
    assert "Importing corpus archives requires contributor or administrator privileges." in html


def test_admin_sees_import_dropzone(client, as_role):
    as_role("admin")
    response = client.get("/data/ingest/import")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Import a complete or partial MCRIT corpus archive package" in html


def test_invalid_tab_defaults_to_query(client, as_role):
    as_role("visitor")
    response = client.get("/data/ingest/nonexistent_tab")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "pane-query" in html
