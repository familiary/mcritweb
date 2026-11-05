#!/usr/bin/python
"""Tests for Navigation Modernization & UI Presentation (Milestone 3).

Verifies:
- Tier 1: Core Navigation Structure across Roles (anonymous, pending, visitor, contributor, admin)
- Tier 2: Single-User vs Multi-User Dynamic Link Gating
- Tier 3: Accessibility Landmarks, Skip-to-Content Link, and ARIA Attributes
- Tier 4: Active Section and Page Indicator State Tracking
- Tier 5: Strict Security Invariants (visitor cannot see contributor submit link, pending cannot trigger dropdowns)
"""

import re

import pytest

from mcritweb.db import ServerInfo


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Provide the captured corpus backend client so pages render their content."""
    return corpus_mcrit


# =============================================================================
# Tier 1: Role-Based Navigation Structure
# =============================================================================

def test_anonymous_navigation_renders_minimal_public_interface(client, make_user):
    """Unauthenticated users see only brand logos, login, and documentation."""
    make_user(role="visitor")
    response = client.get("/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Brand and documentation links
    assert 'href="/"' in html or 'href="http://localhost/"' in html
    assert 'href="/login"' in html
    assert 'href="/help"' in html

    # Protected navigation dropdowns must NOT be present
    assert 'id="navbarDropdownExplore"' not in html
    assert 'id="navbarDropdownAnalyze"' not in html
    assert 'id="navbarDropdownData"' not in html
    assert 'id="navbarDropdownUser"' not in html


def test_visitor_navigation_structure(client, as_role):
    """Visitor sees Explore, Analyze, and Data (Ingestion & Jobs only, no Submit/Import/Export)."""
    as_role("visitor")
    response = client.get("/explore/samples")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Explore dropdown links
    assert 'href="/explore/search"' in html
    assert 'href="/explore/families"' in html
    assert 'href="/explore/samples"' in html
    assert 'href="/explore/functions"' in html
    assert 'href="/explore/statistics"' in html
    assert 'href="/explore/users"' in html
    assert "Researchers &amp; Users" in html or "Researchers & Users" in html

    # Analyze dropdown links
    assert 'href="/analyze/compare"' in html
    assert 'href="/analyze/compare_versus"' in html
    assert 'href="/analyze/cross_compare"' in html
    assert 'href="/analyze/unique_blocks"' in html
    assert 'href="/analyze/query"' in html
    assert "Query by Upload" in html

    # Data dropdown links
    assert 'href="/data/ingest"' in html
    assert "Ingestion &amp; Submission" in html or "Ingestion & Submission" in html
    assert 'href="/data/jobs"' in html
    assert "Jobs/Results" in html

    # Contributor/Admin only write links MUST NOT be present
    assert 'href="/data/submit"' not in html
    assert 'href="/data/import"' not in html
    assert 'href="/data/export"' not in html


def test_contributor_navigation_structure(client, as_role):
    """Contributor gains direct links for Submit Binary, Import Data, and Export Data."""
    as_role("contributor")
    response = client.get("/data/jobs", follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Data dropdown includes ingestion hub and contributor quick links
    assert 'href="/data/ingest"' in html
    assert 'href="/data/submit"' in html
    assert "Submit Binary" in html
    assert 'href="/data/import"' in html
    assert "Import Data" in html
    assert 'href="/data/export"' in html
    assert "Export Data" in html
    assert 'href="/data/jobs"' in html

    # Administrative links must NOT be present
    assert 'href="/admin/server"' not in html
    assert 'href="/admin/users' not in html


def test_admin_navigation_structure(client, as_role):
    """Admin sees User Management and Server configuration in the user dropdown."""
    as_role("admin")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Admin controls in user dropdown
    assert 'href="/admin/server"' in html
    assert "Server" in html
    assert 'href="/admin/users' in html
    assert "User Management" in html

    # Personal profile and session links
    assert 'href="/profile"' in html
    assert "View profile" in html
    assert 'href="/settings"' in html
    assert "Settings" in html
    assert 'href="/logout"' in html
    assert "Logout" in html


# =============================================================================
# Tier 2: Single-User vs Multi-User Operating Modes
# =============================================================================

def test_single_user_mode_omits_user_directory_links(client, as_role, app):
    """In single-user mode, community user discovery and user management links are hidden."""
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = "single"
        server.saveToDb()

    # Visitor check
    as_role("visitor")
    resp_visitor = client.get("/explore/samples")
    assert resp_visitor.status_code == 200
    html_visitor = resp_visitor.get_data(as_text=True)
    assert 'href="/explore/users"' not in html_visitor

    # Admin check
    as_role("admin")
    resp_admin = client.get("/")
    assert resp_admin.status_code == 200
    html_admin = resp_admin.get_data(as_text=True)
    assert 'href="/explore/users"' not in html_admin
    assert 'href="/admin/users' not in html_admin


def test_multi_user_mode_includes_user_directory_links(client, as_role, app):
    """In multi-user mode, Researchers & Users appears under Explore and User Management under Admin."""
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = "multi"
        server.saveToDb()

    # Visitor check
    as_role("visitor")
    resp_visitor = client.get("/explore/samples")
    assert resp_visitor.status_code == 200
    html_visitor = resp_visitor.get_data(as_text=True)
    assert 'href="/explore/users"' in html_visitor

    # Admin check
    as_role("admin")
    resp_admin = client.get("/")
    assert resp_admin.status_code == 200
    html_admin = resp_admin.get_data(as_text=True)
    assert 'href="/explore/users"' in html_admin
    assert 'href="/admin/users' in html_admin


# =============================================================================
# Tier 3: Accessibility Landmarks, Skip Link, and Mobile Toggle
# =============================================================================

def test_navbar_has_accessible_landmark_name(client, as_role):
    """Top navbar must declare aria-label="Main navigation" to distinguish landmarks."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert re.search(r'<nav\b[^>]*\baria-label="Main navigation"', html)


def test_skip_to_main_content_link_and_landmark_target(client, as_role):
    """Skip-to-content link exists in header and targets #main-content inside verbatim main."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Skip link in header
    skip_match = re.search(r'<a\b[^>]*href="#main-content"[^>]*>([^<]+)</a>', html)
    assert skip_match, "Skip to main content link missing"
    assert "visually-hidden-focusable" in skip_match.group(0)
    assert "Skip to main content" in skip_match.group(1)

    # Target element immediately inside verbatim <main class="content">
    assert '<main class="content">' in html
    assert '<div id="main-content" tabindex="-1"></div>' in html


def test_responsive_mobile_toggler_attributes(client, as_role):
    """Navbar toggler declares correct Bootstrap 5 collapse attributes and target."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    toggler_match = re.search(r'<button class="navbar-toggler\b[^"]*"[^>]*>', html)
    assert toggler_match, "Navbar toggler button not found"
    toggler_tag = toggler_match.group(0)

    assert 'data-bs-toggle="collapse"' in toggler_tag
    assert 'data-bs-target="#navbarSupportedContent"' in toggler_tag
    assert 'aria-controls="navbarSupportedContent"' in toggler_tag
    assert 'aria-expanded="false"' in toggler_tag
    assert 'aria-label="Toggle navigation"' in toggler_tag

    # Collapsible target must exist on the page
    assert re.search(r'<div class="[^"]*\bcollapse\b[^"]*" id="navbarSupportedContent"', html)


def test_dropdown_toggle_classes_and_attributes(client, as_role):
    """Authenticated dropdown menus declare dropdown-toggle and disclosure ARIA attributes."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    dropdown_ids = ["navbarDropdownExplore", "navbarDropdownAnalyze", "navbarDropdownData", "navbarDropdownUser"]
    for dropdown_id in dropdown_ids:
        pattern = rf'<a\b[^>]*id="{dropdown_id}"[^>]*>'
        match = re.search(pattern, html)
        assert match, f"Dropdown trigger #{dropdown_id} not found"
        tag = match.group(0)
        assert "dropdown-toggle" in tag, f"#{dropdown_id} missing dropdown-toggle class"
        assert 'data-bs-toggle="dropdown"' in tag, f"#{dropdown_id} missing data-bs-toggle"
        assert 'aria-haspopup="true"' in tag, f"#{dropdown_id} missing aria-haspopup"
        assert 'aria-expanded="false"' in tag, f"#{dropdown_id} missing aria-expanded"


def test_navbar_icons_decorative_aria_hidden(client, as_role):
    """Decorative Font Awesome icons in the navbar declare aria-hidden="true"."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Extract navbar block
    nav_match = re.search(r'<nav class="navbar\b[^>]*>(.*?)</nav>', html, re.DOTALL)
    assert nav_match, "Navbar block not found"
    nav_content = nav_match.group(1)

    icons = re.findall(r'<i\b[^>]*>', nav_content)
    assert len(icons) > 0, "Expected navbar icons"
    for icon in icons:
        assert 'aria-hidden="true"' in icon, f"Icon {icon} missing aria-hidden='true'"


# =============================================================================
# Tier 4: Active State Indication
# =============================================================================

def test_active_state_on_explore_users(client, as_role):
    """Visiting /explore/users marks the Explore toggle active and Users link aria-current='page'."""
    as_role("visitor")
    response = client.get("/explore/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Explore toggle is active
    explore_match = re.search(r'<a\b[^>]*id="navbarDropdownExplore"[^>]*>', html)
    assert explore_match
    assert "active" in explore_match.group(0)
    assert 'aria-current="true"' in explore_match.group(0)

    # Users item is active with aria-current="page"
    users_match = re.search(r'<a class="dropdown-item[^"]*" href="/explore/users"[^>]*>', html)
    assert users_match
    assert "active" in users_match.group(0)
    assert 'aria-current="page"' in users_match.group(0)

    # Other toggles must not be active
    analyze_match = re.search(r'<a\b[^>]*id="navbarDropdownAnalyze"[^>]*>', html)
    assert "active" not in analyze_match.group(0)
    data_match = re.search(r'<a\b[^>]*id="navbarDropdownData"[^>]*>', html)
    assert "active" not in data_match.group(0)


def test_active_state_on_data_ingest(client, as_role):
    """Visiting /data/ingest marks the Data toggle active and Ingestion item aria-current='page'."""
    as_role("visitor")
    response = client.get("/data/ingest")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Data toggle is active
    data_match = re.search(r'<a\b[^>]*id="navbarDropdownData"[^>]*>', html)
    assert data_match
    assert "active" in data_match.group(0)
    assert 'aria-current="true"' in data_match.group(0)

    # Ingestion item is active with aria-current="page"
    ingest_match = re.search(r'<a class="dropdown-item[^"]*" href="/data/ingest"[^>]*>', html)
    assert ingest_match
    assert "active" in ingest_match.group(0)
    assert 'aria-current="page"' in ingest_match.group(0)


def test_active_state_on_analyze_query(client, as_role):
    """Visiting /analyze/query marks the Analyze toggle active and Query item aria-current='page'."""
    as_role("visitor")
    response = client.get("/analyze/query")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Analyze toggle is active
    analyze_match = re.search(r'<a\b[^>]*id="navbarDropdownAnalyze"[^>]*>', html)
    assert analyze_match
    assert "active" in analyze_match.group(0)
    assert 'aria-current="true"' in analyze_match.group(0)

    # Query item is active with aria-current="page"
    query_match = re.search(r'<a class="dropdown-item[^"]*" href="/analyze/query"[^>]*>', html)
    assert query_match
    assert "active" in query_match.group(0)
    assert 'aria-current="page"' in query_match.group(0)


def test_active_state_on_help_documentation(client, as_role):
    """Visiting /help marks the Documentation nav-link active with aria-current='page'."""
    as_role("visitor")
    response = client.get("/help")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    help_match = re.search(r'<a class="nav-link[^"]*" href="/help"[^>]*>', html)
    assert help_match
    assert "active" in help_match.group(0)
    assert 'aria-current="page"' in help_match.group(0)


# =============================================================================
# Tier 5: Adversarial & Role Boundary Edge Cases
# =============================================================================

def test_pending_user_navigation_disabled_dropdowns(client, as_role):
    """Pending users have disabled dropdown toggles with tabindex='-1', aria-disabled='true', and no data-bs-toggle."""
    as_role("pending")
    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # No stray divider at beginning of navbar-nav
    assert not re.search(r'<ul class="navbar-nav[^"]*">\s*<li><hr class="dropdown-divider"></li>', html)

    for dropdown_id in ["navbarDropdownExplore", "navbarDropdownAnalyze", "navbarDropdownData"]:
        match = re.search(rf'<a\b[^>]*id="{dropdown_id}"[^>]*>', html)
        assert match, f"#{dropdown_id} not found"
        tag = match.group(0)
        assert "disabled" in tag, f"#{dropdown_id} missing disabled class"
        assert 'tabindex="-1"' in tag, f"#{dropdown_id} missing tabindex='-1'"
        assert 'aria-disabled="true"' in tag, f"#{dropdown_id} missing aria-disabled='true'"
        assert 'data-bs-toggle="dropdown"' not in tag, f"#{dropdown_id} must not toggle dropdown for pending role"


@pytest.mark.parametrize("path", ["/", "/explore/samples", "/explore/families", "/data/jobs", "/data/ingest"])
def test_visitor_never_sees_submit_binary_link(client, as_role, path):
    """A visitor must never receive a link to /data/submit on any rendered page."""
    as_role("visitor")
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'href="/data/submit"' not in html, f"Page {path} offered a visitor a link to /data/submit"
