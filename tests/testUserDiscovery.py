#!/usr/bin/python
"""`/explore/users`, the directory of accounts, and `/profile/<username>`, another
account's page: who may open them, and what each viewer is shown.

The policy: a signed-in visitor, contributor or admin may see of another account its
username, its role, its submissions ledger (the addBinarySample jobs the backend recorded
under the username) and their count. The account id, the registration date, the last
login, the query uploads, the API token tab, the settings button, the capability table
and the full job list are the holder's and an admin's. A pending viewer has its own
profile and nothing else. The directory lists the non-pending accounts, 25 a page, with
username, role and submission count; an admin also sees the pending ones, the registration
date and the last login.

The first version rendered the uploads pane of every account for every viewer, passed
the last login and the id to peers, let a pending account browse everyone's profile, told
pending accounts apart from unknown ones by the status code, and fetched the whole queue
on every directory view. The boundary is enforced in the view, which hands the template
only what the viewer may see; the tests here read what reaches the page, per role.
"""

import logging
import os
import re

import pytest

from mcritweb.db import ServerInfo, UserInfo, get_db, remember_query_filename, utc_now
from mcritweb.views.utility import query_upload_path

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: the username the captured queue is attributed to; it holds 17 jobs, 7 of them submissions
REQUESTER, QUEUE_SIZE, SUBMISSIONS = "analyst1", 17, 7
UPLOAD_NAME = "secret_query.exe"
PEERS = ["visitor", "contributor"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def target(app, make_user, fake_mcrit):
    """A contributor the captured queue's jobs belong to, with a logged login and one
    query upload kept: everything a profile can show."""
    user_id = make_user(role="contributor", username=REQUESTER)
    fake_mcrit.record_requester(REQUESTER)
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.last_login = utc_now()
        user_info.saveToDb()
        remember_query_filename("fedcba9876543210fedcba98", UPLOAD_NAME, user_id)
        path = query_upload_path(app, "fedcba9876543210fedcba98")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as upload:
            upload.write(b"MZquery")
    return user_id


def set_operation_mode(app, mode):
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = mode
        server.saveToDb()


def rows(html):
    """(username, cells) per directory row."""
    return [(name, re.findall(r"<td>(.*?)</td>", row, re.DOTALL))
            for name, row in re.findall(r'<tr>\s*<td><a href="/profile/[^"]+">([^<]+)</a></td>(.*?)</tr>', html, re.DOTALL)]


def header_cells(html):
    return [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in re.findall(r'<th scope="col"[^>]*>(.*?)</th>', html, re.DOTALL)]


# --- the directory: who may open it ----------------------------------------------------------

@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_a_visitor_and_up_can_open_the_directory(client, as_role, target, role):
    as_role(role)
    response = client.get("/explore/users")
    assert response.status_code == 200
    assert f'<a href="/profile/{REQUESTER}">{REQUESTER}</a>' in response.get_data(as_text=True)


def test_a_pending_account_is_refused(client, as_role):
    as_role("pending")
    assert client.get("/explore/users").status_code == 403


def test_a_signed_out_reader_is_sent_to_login(client, make_user):
    make_user(role="visitor")
    response = client.get("/explore/users")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")


def test_single_user_mode_has_no_directory(client, as_role, app):
    set_operation_mode(app, "single")
    as_role("visitor")
    response = client.get("/explore/users")
    assert response.status_code == 302 and response.headers["Location"].endswith("/")


# --- the directory: what it shows --------------------------------------------------------------

@pytest.mark.parametrize("role", PEERS)
def test_a_peer_sees_names_roles_and_counts_and_no_pending_account(client, as_role, make_user, target, role):
    make_user(role="pending", username="applicant")
    as_role(role)
    html = client.get("/explore/users").get_data(as_text=True)
    assert header_cells(html) == ["Username", "Role", "Submissions recorded under this username by the backend"]
    listed = dict(rows(html))
    assert "applicant" not in listed
    assert listed[REQUESTER] == ["contributor", str(SUBMISSIONS)]
    assert "Registered" not in html and "Last login" not in html


def test_an_admin_sees_the_pending_accounts_and_the_dates(client, as_role, make_user, target):
    """The registration date and the last login are on master's admin page only; the
    directory shows them to the admin, not to the rest."""
    make_user(role="pending", username="applicant")
    as_role("admin")
    html = client.get("/explore/users").get_data(as_text=True)
    assert header_cells(html) == ["Username", "Role", "Registered", "Last login", "Submissions recorded under this username by the backend"]
    listed = dict(rows(html))
    assert listed["applicant"][0] == "pending"
    assert listed[REQUESTER][0] == "contributor" and re.fullmatch(r"\d{4}-\d\d-\d\d", listed[REQUESTER][2]) and listed[REQUESTER][3] == str(SUBMISSIONS)


def test_the_counts_cost_one_queue_count_per_listed_account_and_never_the_whole_queue(client, as_role, make_user, target, fake_mcrit):
    """The first version fetched every job document on every view, by any visitor, to
    count submissions in Python. A count is one getQueueCount per account on the page."""
    for index in range(3):
        make_user(role="visitor", username=f"reader{index}")
    as_role("visitor")
    fake_mcrit.calls.clear()
    assert client.get("/explore/users").status_code == 200
    queue_calls = [call for call in fake_mcrit.calls if call[0] in ("getQueueCount", "getQueueData")]
    # every queue request names one username and the submissions, so none can be the whole queue
    assert all(call[2].get("method") == "addBinarySample" and call[2].get("username") for call in queue_calls), queue_calls
    counts = [call for call in queue_calls if call[0] == "getQueueCount"]
    # the target, three readers and the viewer: one count each
    assert len(counts) == 5 <= 25


def test_the_directory_is_paged_and_the_counts_are_for_the_page_only(client, as_role, make_user, fake_mcrit):
    """30 accounts: 25 on the first page, 5 on the second, and the queue is asked for
    the accounts on the page, not for every account."""
    for index in range(29):
        make_user(role="visitor", username=f"reader{index:02d}")
    as_role("visitor", username="zz_viewer")
    fake_mcrit.calls.clear()
    first = client.get("/explore/users").get_data(as_text=True)
    assert len(rows(first)) == 25 and len([call for call in fake_mcrit.calls if call[0] == "getQueueCount"]) == 25
    fake_mcrit.calls.clear()
    second = client.get("/explore/users?p=2").get_data(as_text=True)
    assert [name for name, _ in rows(second)] == ["reader24", "reader25", "reader26", "reader27", "reader28", "zz_viewer"][-5:]
    assert len([call for call in fake_mcrit.calls if call[0] == "getQueueCount"]) == 5
    assert 'href="/explore/users?p=2"' in first


def test_a_count_the_backend_will_not_give_is_a_dash_not_a_zero(client, as_role, target, fake_mcrit, monkeypatch):
    as_role("visitor")
    monkeypatch.setattr(fake_mcrit, "getQueueCount", lambda *args, **kwargs: None, raising=False)
    html = client.get("/explore/users").get_data(as_text=True)
    assert dict(rows(html))[REQUESTER][1] == '<span class="text-muted">-</span>'


def test_the_directory_says_what_the_counts_are_and_they_move_with_the_name(client, as_role, app, make_user, target):
    """ADR-0007: the backend records the username of the request, not an owner. The
    page says so, and the count follows the name: a renamed account counts 0, and the
    next holder of the freed name inherits the old holder's count."""
    as_role("visitor")
    html = client.get("/explore/users").get_data(as_text=True)
    assert "as the MCRIT backend recorded them under the username" in html
    assert "a renamed account's earlier submissions stay under the old name" in html
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=target)
        user_info.username = "renamed"
        user_info.saveToDb()
    listed = dict(rows(client.get("/explore/users").get_data(as_text=True)))
    assert listed["renamed"] == ["contributor", "0"]
    make_user(role="visitor", username=REQUESTER)
    listed = dict(rows(client.get("/explore/users").get_data(as_text=True)))
    assert listed[REQUESTER] == ["visitor", str(SUBMISSIONS)] and listed["renamed"] == ["contributor", "0"]


def test_the_directory_prints_no_secret(client, as_role, target):
    as_role("admin")
    html = client.get("/explore/users").get_data(as_text=True)
    assert "apitoken-" not in html and "pbkdf2" not in html and "scrypt" not in html


# --- another account's profile: who may open which -------------------------------------------------

@pytest.mark.parametrize("viewer", ["visitor", "contributor", "admin"])
@pytest.mark.parametrize("kind, status", [("visitor", 200), ("contributor", 200), ("admin", 200), ("unknown", 404)],
                         ids=["visitor-target", "contributor-target", "admin-target", "unknown"])
def test_a_visitor_and_up_can_open_the_profile_of_a_non_pending_account(client, as_role, make_user, viewer, kind, status):
    if kind != "unknown":
        make_user(role=kind, username="other")
    as_role(viewer)
    assert client.get("/profile/other").status_code == status


@pytest.mark.parametrize("viewer, status", [("visitor", 404), ("contributor", 404), ("admin", 200)])
def test_a_pending_account_is_unknown_to_everyone_but_an_admin(client, as_role, make_user, viewer, status):
    """It answered 403 for a pending target and 404 for an unknown name, which told the
    pending names the directory hides apart from names that do not exist."""
    make_user(role="pending", username="applicant")
    as_role(viewer)
    assert client.get("/profile/applicant").status_code == status


@pytest.mark.parametrize("kind", ["visitor", "contributor", "admin", "pending"])
def test_a_pending_viewer_has_its_own_profile_and_nobody_elses(client, as_role, make_user, kind):
    """The route was @login_required with the pending check on the target, so a pending
    account could browse every profile while every other page refused it."""
    make_user(role=kind, username="other")
    as_role("pending")
    assert client.get("/profile/other").status_code == 403
    assert client.get("/profile").status_code == 200


def test_ones_own_name_leads_to_ones_own_page(client, as_role):
    as_role("contributor", username="me")
    response = client.get("/profile/me")
    assert response.status_code == 302 and response.headers["Location"].endswith("/profile")


def test_a_signed_out_reader_is_sent_to_login_from_a_profile(client, make_user):
    make_user(role="visitor", username="other")
    response = client.get("/profile/other")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")


# --- another account's profile: what reaches the page -------------------------------------------------

#: what a peer must not be shown, as it appears in the holder's own page
PRIVATE = {
    "user id": "<dt class=\"col-sm-5\">User ID</dt>",
    "registered": "<dt class=\"col-sm-5\">Registered</dt>",
    "last login": "<dt class=\"col-sm-5\">Last login</dt>",
    "uploads pane": 'id="pane-uploads"',
    "uploads tab": 'id="tab-uploads"',
    "upload filename": UPLOAD_NAME,
    "uploads count": "Query uploads (",
    "jobs tab": 'id="tab-jobs"',
    "jobs pane": 'id="pane-jobs"',
    "overview pane": 'id="pane-overview"',
    "developer tab": 'id="tab-developer"',
    "settings button": 'class="btn btn-outline-secondary btn-sm">Settings</a>',
    "settings link": "Change username, password or preferences",
    "capability table": "What this account may do",
    "token": "apitoken-contributor",
}


@pytest.mark.parametrize("field", sorted(PRIVATE), ids=sorted(PRIVATE))
@pytest.mark.parametrize("viewer", PEERS)
def test_a_peer_is_shown_none_of_the_holders_private_fields(client, as_role, target, viewer, field):
    """Each of these was in the page for any signed-in viewer: the uploads pane with
    the file names, the last login, the id, the jobs, the viewer's own settings
    button and the capability table. The view hands the template only the name, the
    role and the submissions."""
    as_role(viewer)
    html = client.get(f"/profile/{REQUESTER}").get_data(as_text=True)
    assert PRIVATE[field] not in html


@pytest.mark.parametrize("viewer", PEERS)
def test_a_peer_is_shown_the_name_the_role_and_the_submissions(client, as_role, target, viewer):
    as_role(viewer)
    html = client.get(f"/profile/{REQUESTER}").get_data(as_text=True)
    assert f"Profile: {REQUESTER} <span class=\"badge bg-secondary align-middle fs-6\">contributor</span>" in html
    assert f"Submissions ({SUBMISSIONS})" in html and 'id="pane-submissions"' in html
    assert html.count('<span class="badge bg-success">Indexed</span>') == SUBMISSIONS
    assert "as the MCRIT backend recorded them under the username" in html
    assert 'href="/explore/users"' in html
    assert re.findall(r'<div class="tab-pane fade show active" id="pane-(\w+)"', html) == ["submissions"]


@pytest.mark.parametrize("viewer", PEERS)
def test_a_peer_cannot_ask_for_a_private_tab_by_url(client, as_role, target, viewer):
    as_role(viewer)
    for tab in ("jobs", "uploads", "developer", "overview"):
        html = client.get(f"/profile/{REQUESTER}?tab={tab}").get_data(as_text=True)
        assert re.findall(r'<div class="tab-pane fade show active" id="pane-(\w+)"', html) == ["submissions"], tab


def test_a_peer_costs_the_backend_the_submissions_only(client, as_role, target, fake_mcrit):
    """No job listing and no job count for a page that does not show them."""
    as_role("visitor")
    fake_mcrit.calls.clear()
    client.get(f"/profile/{REQUESTER}")
    queue_calls = [call for call in fake_mcrit.calls if call[0] in ("getQueueCount", "getQueueData")]
    assert queue_calls and all(call[2].get("method") == "addBinarySample" and call[2].get("username") == REQUESTER for call in queue_calls), queue_calls


def test_an_admin_sees_the_holders_page_without_the_token_tab_and_the_settings_button(client, as_role, target):
    as_role("admin")
    html = client.get(f"/profile/{REQUESTER}").get_data(as_text=True)
    for field in ("user id", "registered", "last login", "uploads pane", "upload filename", "jobs tab", "overview pane", "capability table"):
        assert PRIVATE[field] in html, field
    for field in ("developer tab", "settings button", "settings link", "token"):
        assert PRIVATE[field] not in html, field
    assert f"Jobs ({QUEUE_SIZE})" in html and "Query uploads (1)" in html
    assert 'href="/explore/users"' in html and 'href="/analyze/query"' in html


def test_an_admin_viewing_another_account_sees_that_accounts_uploads_not_everyones(client, as_role, app, make_user, target):
    other = make_user(role="visitor", username="someone")
    with app.app_context():
        remember_query_filename("0123456789abcdef01234567", "someone_elses.exe", other)
    as_role("admin")
    html = client.get(f"/profile/{REQUESTER}").get_data(as_text=True)
    assert UPLOAD_NAME in html and "someone_elses.exe" not in html


def test_the_own_page_is_unchanged(client, as_role, target):
    """The holder keeps every pane, the settings button and the token tab."""
    with_login = as_role("contributor", username="holder")
    assert with_login
    html = client.get("/profile").get_data(as_text=True)
    for field in ("overview pane", "jobs tab", "uploads pane", "developer tab", "settings button", "capability table"):
        assert PRIVATE[field] in html, field


def test_the_uploads_of_a_freed_name_do_not_follow_it_to_the_peer_page(client, as_role, app, make_user, target):
    """The submissions are the backend's and follow the name (ADR-0007); the uploads
    are this instance's and follow the account, so a freed name's next holder shows
    the old holder's submissions and none of the old holder's uploads, to anyone."""
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM user WHERE id = ?;", (target,))
        database.commit()
    make_user(role="visitor", username=REQUESTER)
    as_role("admin")
    html = client.get(f"/profile/{REQUESTER}").get_data(as_text=True)
    assert f"Submissions ({SUBMISSIONS})" in html and UPLOAD_NAME not in html and "Query uploads (0)" in html


# --- the boundary is the view's, not the template's ----------------------------------------------------

@pytest.fixture
def rendered(app):
    """The context each template was rendered with, as Flask's template_rendered signal
    reports it; the boundary is checked on what reaches the template, not on the HTML."""
    from flask import template_rendered
    contexts = []

    def record(sender, template, context, **extra):
        contexts.append((template.name, context))
    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


@pytest.mark.parametrize("viewer", PEERS)
def test_the_view_hands_a_peer_no_private_field_whatever_the_template_does(client, as_role, target, rendered, viewer):
    """A template `if` is one layer; this is the other. For a peer the context holds
    the name and the role of the account and nothing else of it, no jobs, no uploads,
    no capabilities, so a later edit to profile.html cannot show what the view did not
    pass."""
    as_role(viewer)
    assert client.get(f"/profile/{REQUESTER}").status_code == 200
    context = next(context for name, context in rendered if name == "profile.html")
    assert context["account"] == {"username": REQUESTER, "role": "contributor"}
    assert context["jobs"] == [] and context["job_count"] == 0 and context["jobs_pagination"] is None
    assert context["query_uploads"] == [] and context["upload_count"] == 0
    assert context["capabilities"] is None and context["can_use_api"] is False and context["full_view"] is False
    assert context["tabs"] == ["submissions"]
    assert len(context["submissions"]) == SUBMISSIONS


def test_the_view_hands_an_admin_the_account_and_the_holders_uploads(client, as_role, target, rendered):
    as_role("admin")
    assert client.get(f"/profile/{REQUESTER}").status_code == 200
    context = next(context for name, context in rendered if name == "profile.html")
    assert set(context["account"]) == {"username", "role", "user_id", "registered", "registered_relative", "last_login", "last_login_relative"}
    assert [upload["filename"] for upload in context["query_uploads"]] == [UPLOAD_NAME]
    assert context["tabs"] == ["overview", "jobs", "submissions", "uploads"] and context["can_use_api"] is False
