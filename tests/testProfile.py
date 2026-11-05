#!/usr/bin/python
"""`/profile`, the signed-in user's own page.

Five panes: the account and what its role may do, the jobs and the submissions the MCRIT
backend recorded under the username, the query uploads this instance kept for the account,
and the API token behind a button. The page reads SQLite first and the backend second, so
it renders with the backend down as far as SQLite carries it, and a transport failure on
the backend call is the app's 503 page rather than a warning that looks like an empty tab.

The jobs come from the captured corpus with a username on the queue documents, as mcrit
1.10+ records it: the first tests fed MagicMock jobs, on which every attribute exists and
`duration` is a float, so a 500 on every real finished job (a timedelta has no round) and
a "Sample #N" branch no real submission reaches both passed.
"""

import datetime
import logging
import os
import re

import pytest
import requests
from routePolicy import ADMIN, CONTRIBUTOR, LOGGED_IN, ROLE_ORDER, VISITOR

from mcritweb.db import UserInfo, get_db, remember_query_filename, utc_now
from mcritweb.views import authentication
from mcritweb.views.authentication import API_ROLES, KNOWN_ROLES, format_file_size, format_relative_time, get_role_capabilities
from mcritweb.views.utility import query_upload_path

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: who the captured queue is attributed to in the tests below
REQUESTER = "analyst1"
#: what the captured queue holds: 17 jobs, 7 of them submissions
QUEUE_SIZE, SUBMISSIONS = 17, 7


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def as_requester(as_role, fake_mcrit):
    """Signed in as the user the captured queue's jobs were requested by."""
    def _as_requester(role="contributor"):
        fake_mcrit.record_requester(REQUESTER)
        return as_role(role, username=REQUESTER)
    return _as_requester


def pane(html, name):
    """The markup of one tab pane."""
    start = html.index(f'id="pane-{name}"')
    end = html.find('<div class="tab-pane', start)
    return html[start:end if end != -1 else html.index('{{ pagination_js() }}') if '{{ pagination_js() }}' in html else len(html)]


def rows_of(html, name):
    return re.findall(r'<tr>\s*<td><span class="font-monospace">([0-9a-f]{24})</span></td>', pane(html, name))


def keep_upload(app, user_id, filename="query.exe", job_id="fedcba9876543210fedcba98", with_file=True):
    """What analyze.query leaves behind for a query: the row naming the upload and its
    uploader, and the file under the job id (the corpus client queues no queries)."""
    with app.app_context():
        remember_query_filename(job_id, filename, user_id)
        if with_file:
            path = query_upload_path(app, job_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as upload:
                upload.write(b"MZ" + filename.encode())
    return job_id


# --- who may read it, and what the backend's state does to it ---------------------------------

def test_a_signed_out_reader_is_sent_to_login(client, make_user):
    make_user(role="visitor")
    response = client.get("/profile")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_every_role_has_a_profile(client, as_role, role):
    as_role(role)
    response = client.get("/profile")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"Profile: {role}user" in html and f'<span class="badge bg-secondary align-middle fs-6">{role}</span>' in html


def test_the_page_renders_with_the_backend_down(client, as_role, app, fake_mcrit):
    """A SQLite page first: the reachability probe is not applied, so the account, the
    capabilities and the uploads render whatever the backend is doing. The queue counts
    are what the backend answers, and with it down they are not there."""
    app.config["MCRIT_SERVER_PROBE"] = lambda: False
    as_role("contributor")
    assert client.get("/profile").status_code == 200


@pytest.mark.parametrize("failure", [requests.ConnectionError("refused"), requests.Timeout("too slow")], ids=["connection", "timeout"])
def test_a_transport_failure_on_the_queue_is_the_apps_503_page(client, as_role, fake_mcrit, monkeypatch, failure):
    """AGENTS.md: a broad except around a backend call re-raises BACKEND_FAILURES. The
    first version caught everything and drew a "queue unreachable" tab, and its test
    raised the builtin ConnectionError, which is not what `requests` raises."""
    as_role("contributor")

    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(fake_mcrit, "getQueueCount", fail, raising=False)
    response = client.get("/profile")
    assert response.status_code == 503
    assert "MCRIT" in response.get_data(as_text=True)


def test_a_queue_that_cannot_be_read_is_said_so(client, as_role, fake_mcrit, monkeypatch):
    """The client answers None for a failed read; that is not an empty queue, and the
    page says which it is, as data.jobs does, instead of claiming no jobs."""
    as_role("contributor")
    monkeypatch.setattr(fake_mcrit, "getQueueCount", lambda *args, **kwargs: None, raising=False)
    html = client.get("/profile").get_data(as_text=True)
    assert "job queue failed" in html and "could not be read" in html
    assert "No job was recorded" not in html


def test_a_user_without_jobs_sees_the_empty_states(client, as_role):
    as_role("visitor")
    html = client.get("/profile").get_data(as_text=True)
    assert "No job was recorded under this username yet" in html
    assert "No submission was recorded under this username" in html
    assert "No query upload is kept for this account" in html
    assert "Jobs (0)" in html and "Submissions (0)" in html and "Query uploads (0)" in html


# --- the account --------------------------------------------------------------------------------

def test_the_account_pane_shows_the_row_with_machine_readable_times(client, as_role, app):
    """The <time> elements carry ISO datetimes, not the displayed text, which is not a
    valid datetime value."""
    user_id = as_role("contributor")
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.last_login = utc_now() - datetime.timedelta(hours=3)
        user_info.saveToDb()
        registered = user_info.registered
        last_login = user_info.last_login
    html = client.get("/profile").get_data(as_text=True)
    account = pane(html, "overview")
    assert f"<dd class=\"col-sm-7\">{user_id}</dd>" in account
    assert f'<time datetime="{registered.isoformat()}">{registered.strftime("%Y-%m-%d %H:%M UTC")}</time> <span class="text-muted">(just now)</span>' in account
    assert f'<time datetime="{last_login.isoformat()}">' in account and "(3 hours ago)" in account


def test_an_account_that_never_logged_in_says_so(client, as_role):
    as_role("visitor")
    assert "no login recorded" in pane(client.get("/profile").get_data(as_text=True), "overview")


@pytest.mark.parametrize("age, words", [
    (datetime.timedelta(seconds=30), "just now"),
    (datetime.timedelta(minutes=1), "1 minute ago"),
    (datetime.timedelta(minutes=5), "5 minutes ago"),
    (datetime.timedelta(hours=3), "3 hours ago"),
    (datetime.timedelta(days=2), "2 days ago"),
    (datetime.timedelta(days=45), "1 month ago"),
    (datetime.timedelta(days=362), "12 months ago"),
    (datetime.timedelta(days=365), "1 year ago"),
    (datetime.timedelta(days=800), "2 years ago"),
], ids=["seconds", "minute", "minutes", "hours", "days", "month", "under-a-year", "year", "years"])
def test_relative_times(age, words):
    """360 to 364 days used to read "0 years ago": the months branch stopped at 12 and
    the years were days // 365."""
    now = datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.UTC)
    assert format_relative_time(now - age, now=now) == words


@pytest.mark.parametrize("size, words", [(500, "500 B"), (2048, "2.0 KiB"), (5 * 1024 * 1024, "5.0 MiB")], ids=["bytes", "kibibytes", "mebibytes"])
def test_file_sizes(size, words):
    assert format_file_size(size) == words


# --- what the role may do -------------------------------------------------------------------------

#: each role's own level in the route policy, to compare with a page's
LEVEL_OF = {"pending": LOGGED_IN, "visitor": VISITOR, "contributor": CONTRIBUTOR, "admin": ADMIN}


def may(role, level):
    """Whether the route policy admits `role` to a page of `level`; the API is not
    session-based, so for it (None) the answer is API_ROLES."""
    return role in API_ROLES if level is None else ROLE_ORDER.index(LEVEL_OF[role]) >= ROLE_ORDER.index(level)


#: capability name -> the route policy level of a page that needs it
CAPABILITY_LEVELS = {
    "Explore and match": VISITOR,
    "Query uploads": VISITOR,
    "Sample submissions": CONTRIBUTOR,
    "Family and function edits": CONTRIBUTOR,
    "Import and export": CONTRIBUTOR,
    "REST API": None,
    "Server and user administration": ADMIN,
}


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_a_cell_is_restricted_exactly_where_the_route_policy_refuses_the_role(app, role):
    """The matrix is derived from what enforces it. The first version wrote the cells by
    hand: two were untrue, and a later rewording of the API row left the template not
    recognising its own strings, so every role read "REST API Access: Restricted"."""
    with app.test_request_context("/profile"):
        rows = {row["name"]: row[role] for row in get_role_capabilities()}
    assert set(rows) == set(CAPABILITY_LEVELS)
    for name, level in CAPABILITY_LEVELS.items():
        assert (rows[name]["kind"] != "restricted") == may(role, level), f"{role} / {name}: {rows[name]}"
        assert rows[name]["kind"] in ("allowed", "limited", "restricted")


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_every_cell_renders_as_a_badge_the_template_knows(client, as_role, role):
    """Each cell is one of three kinds, and the template draws each kind: a cell it does
    not recognise fell through to "Restricted"."""
    as_role(role)
    table = pane(client.get("/profile").get_data(as_text=True), "overview")
    badges = re.findall(r'<th scope="row">([^<]+)</th>\s*<td>\s*<span class="badge ([^"]+)">([^<]+)</span>', table)
    assert sorted(name for name, _, _ in badges) == sorted(CAPABILITY_LEVELS)
    for name, classes, label in badges:
        assert (label != "Restricted") == may(role, CAPABILITY_LEVELS[name]), (name, label)
        assert (classes == "bg-secondary") == (label == "Restricted"), (name, classes)


def test_the_api_row_says_what_the_api_holds_to_contributors(app):
    """api.py's CONTRIBUTOR_ONLY holds adding a sample to contributors; everything else a
    visitor's token may do. The row reads that list rather than restating it."""
    with app.test_request_context("/profile"):
        rows = {row["name"]: row for row in get_role_capabilities()}["REST API"]
    assert rows["visitor"] == {"kind": "limited", "label": "Read and query"}
    assert rows["contributor"] == rows["admin"] == {"kind": "allowed", "label": "Read, query and submit"}
    assert rows["pending"]["kind"] == "restricted"


def test_the_query_cap_is_read_from_the_configuration(app, client, as_role):
    """The cap was the literal "1 MiB" while QUERY_UPLOAD_LIMITS is a setting."""
    as_role("visitor")
    assert "Up to 1.0 MiB" in client.get("/profile").get_data(as_text=True)
    app.config["QUERY_UPLOAD_LIMITS"] = {"visitor": 512 * 1024, "contributor": 0}
    with app.test_request_context("/profile"):
        rows = {row["name"]: row for row in get_role_capabilities()}["Query uploads"]
    assert rows["visitor"] == {"kind": "limited", "label": "Up to 512.0 KiB"}
    assert rows["contributor"]["kind"] == "restricted"
    assert rows["admin"] == {"kind": "allowed", "label": "Uncapped"}


def test_the_hand_written_matrix_is_gone():
    assert not hasattr(authentication, "ROLE_CAPABILITIES")


# --- the jobs -------------------------------------------------------------------------------------

def test_the_jobs_pane_lists_the_jobs_under_the_username_and_names_what_that_is(client, as_requester):
    """The jobs are the backend's, keyed by the username it recorded (ADR-0007):
    the page says so in words, so nobody reads the list as an account's history."""
    as_requester()
    html = client.get("/profile").get_data(as_text=True)
    assert f"Jobs ({QUEUE_SIZE})" in html
    assert len(rows_of(html, "jobs")) == QUEUE_SIZE
    assert "recorded under the username <strong>analyst1</strong>" in html
    assert "A renamed account's earlier jobs stay under the old name." in html


def test_a_real_finished_job_renders_its_duration(client, as_requester):
    """`job.duration` is a timedelta; `|round` on it was a 500 on every finished job."""
    as_requester()
    jobs = pane(client.get("/profile").get_data(as_text=True), "jobs")
    assert re.search(r"<td>\d+\.\d\ds</td>", jobs)
    assert '<time datetime="2026-08-06T10:46:34.679Z">2026-08-06 10:46:34</time>' in jobs


def test_jobs_of_another_username_are_not_listed(client, as_role, fake_mcrit):
    """Only the username the backend recorded selects: without the selector the page
    would list the whole queue under whoever is looking."""
    fake_mcrit.record_requester("someone_else")
    fake_mcrit.record_requester(REQUESTER, method="getUniqueBlocks")
    as_role("contributor", username=REQUESTER)
    html = client.get("/profile").get_data(as_text=True)
    assert "Jobs (1)" in html and len(rows_of(html, "jobs")) == 1
    assert [call for call in fake_mcrit.calls if call[0] == "getQueueData"][0][2]["username"] == REQUESTER


def test_the_jobs_are_paged_and_the_count_is_what_the_pages_hold(client, as_requester):
    """The whole queue for the user came as one table, and "Total Jobs" counted the
    submissions the table left out. Paged as data.jobs pages, 25 a page, the count is
    getQueueCount for the same selection, and the page links carry the tab."""
    as_requester()
    first = client.get("/profile?tab=jobs&jlimit=10").get_data(as_text=True)
    second = client.get("/profile?tab=jobs&jlimit=10&jp=2").get_data(as_text=True)
    assert len(rows_of(first, "jobs")) == 10 and len(rows_of(second, "jobs")) == QUEUE_SIZE - 10
    assert rows_of(first, "jobs") + rows_of(second, "jobs") == rows_of(client.get("/profile").get_data(as_text=True), "jobs")
    assert 'href="/profile?tab=jobs&amp;jlimit=10&amp;jp=2"' in pane(first, "jobs")


def test_a_finished_job_links_its_result_and_any_other_its_job_page(client, as_requester):
    """As the jobs page links them: data.result renders every finished job's type."""
    as_requester()
    jobs = pane(client.get("/profile").get_data(as_text=True), "jobs")
    finished = jobs.count('<span class="badge bg-success">Finished</span>')
    assert 0 < finished <= QUEUE_SIZE
    assert jobs.count('class="btn btn-sm btn-primary">Result</a>') == finished
    assert jobs.count('class="btn btn-sm btn-outline-secondary">Job</a>') == QUEUE_SIZE - finished


# --- the submissions --------------------------------------------------------------------------------

def test_the_submissions_pane_lists_the_addbinarysample_jobs(client, as_requester):
    as_requester()
    html = client.get("/profile").get_data(as_text=True)
    assert f"Submissions ({SUBMISSIONS})" in html
    submissions = pane(html, "submissions")
    assert len(rows_of(html, "submissions")) == SUBMISSIONS
    assert "80ba47f1632518238b3ce9c792340f15d955014e6b441eff2d9b137ee4b81a66_dump7_0x002f0000" in submissions
    assert submissions.count('<span class="badge bg-success">Indexed</span>') == SUBMISSIONS


def test_a_finished_submission_links_the_sample_its_result_names(client, as_requester, fake_mcrit, monkeypatch):
    """`Job.sample_id` is never set for addBinarySample; the sample is in the job's
    result, {"sample_info": <SampleEntry>}. One result lookup per finished submission on
    the shown page, and a result the backend no longer has is a link to the job."""
    as_requester()
    submission_ids = rows_of(client.get("/profile").get_data(as_text=True), "submissions")
    answered, looked_up = submission_ids[0], []

    def result_for(job_id, *args, **kwargs):
        looked_up.append(job_id)
        return {"sample_info": {"sample_id": 42}} if job_id == answered else None
    monkeypatch.setattr(fake_mcrit, "getResultForJob", result_for, raising=False)
    html = client.get("/profile?tab=submissions&slimit=10").get_data(as_text=True)
    submissions = pane(html, "submissions")
    assert 'href="/explore/samples/42" class="btn btn-sm btn-primary">Sample 42</a>' in submissions
    assert submissions.count('class="btn btn-sm btn-outline-secondary">Job</a>') == SUBMISSIONS - 1
    assert sorted(looked_up) == sorted(submission_ids)


def test_the_submissions_are_paged_on_their_own(client, as_requester, fake_mcrit):
    """Each ledger pages by itself, so a page of one does not move the other; and the
    result lookups are made for the shown page only."""
    as_requester()
    fake_mcrit.calls.clear()
    html = client.get("/profile?tab=submissions&slimit=10&jlimit=10&jp=2").get_data(as_text=True)
    assert len(rows_of(html, "submissions")) == SUBMISSIONS
    assert len(rows_of(html, "jobs")) == QUEUE_SIZE - 10
    assert len([call for call in fake_mcrit.calls if call[0] == "getResultForJob"]) == SUBMISSIONS


# --- the query uploads --------------------------------------------------------------------------------

def test_the_uploads_pane_lists_the_accounts_own_uploads_with_what_is_on_disk(client, as_role, app):
    job_id = keep_upload(app, as_role("contributor"), "suspicious.exe")
    html = client.get("/profile").get_data(as_text=True)
    uploads = pane(html, "uploads")
    assert "Query uploads (1)" in html
    assert job_id in uploads and "suspicious.exe" in uploads and "<td>16 B</td>" in uploads
    assert re.search(r'<time datetime="\d{4}-\d\d-\d\dT[\d:.]+\+00:00">', uploads)
    assert f'data-post="/data/promote_query/{job_id}"' in uploads


def test_a_visitor_cannot_promote_an_upload(client, as_role, app):
    keep_upload(app, as_role("visitor"))
    assert "promote_query" not in pane(client.get("/profile").get_data(as_text=True), "uploads")


def test_an_upload_whose_file_is_gone_is_listed_without_a_size_or_a_promote(client, as_role, app):
    keep_upload(app, as_role("contributor"), "gone.exe", with_file=False)
    uploads = pane(client.get("/profile").get_data(as_text=True), "uploads")
    assert "gone.exe" in uploads and "file no longer kept" in uploads and "promote_query" not in uploads


def test_another_accounts_uploads_are_not_listed(client, as_role, app):
    """The one test the first version had that bit: with the owner filter gone, the
    secret name shows up here."""
    keep_upload(app, as_role("visitor", username="uploader"), "secret_payload.exe")
    as_role("visitor", username="innocent")
    html = client.get("/profile").get_data(as_text=True)
    assert "secret_payload.exe" not in html
    assert "No query upload is kept for this account" in html


def test_an_admin_is_shown_every_upload(client, as_role, app):
    keep_upload(app, as_role("visitor", username="uploader"), "someone_elses.exe")
    as_role("admin")
    html = client.get("/profile").get_data(as_text=True)
    assert "someone_elses.exe" in pane(html, "uploads")
    assert "every account's" in html


def test_a_renamed_account_still_sees_its_uploads(client, as_role, app):
    """ADR-0007: the username is renamed by its holder, so keyed on it the uploads
    would have vanished at the rename. They are keyed on the account's id."""
    user_id = as_role("visitor", username="before")
    keep_upload(app, user_id, "mine.exe")
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.username = "after"
        user_info.saveToDb()
    html = client.get("/profile").get_data(as_text=True)
    assert "Profile: after" in html and "mine.exe" in pane(html, "uploads")


def test_the_next_holder_of_a_freed_name_sees_nothing_of_the_old_one(client, as_role, app, make_user):
    """ADR-0007: a delete frees the name for the next registration, which must
    not inherit the old holder's uploads."""
    old_id = as_role("visitor", username="reused")
    keep_upload(app, old_id, "old_holders.exe")
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM user WHERE id = ?;", (old_id,))
        database.commit()
    new_id = make_user(role="visitor", username="reused")
    with client.session_transaction() as test_session:
        test_session["user_id"] = new_id
    html = client.get("/profile").get_data(as_text=True)
    assert "Profile: reused" in html
    assert "old_holders.exe" not in html and "No query upload is kept for this account" in html


# --- the API token -------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_the_token_is_not_in_the_page(client, as_role, role):
    """It was in the page four times, behind a d-none span and in the snippets. The page
    holds a button that fetches it; the snippets hold a placeholder until then."""
    as_role(role)
    html = client.get("/profile").get_data(as_text=True)
    assert f"apitoken-{role}" not in html
    if role in API_ROLES:
        assert 'data-show-apitoken="/admin/show_apitoken"' in html
        assert html.count('<span data-apitoken-slot>&lt;YOUR_API_TOKEN&gt;</span>') == 2
        assert 'src="/static/show_apitoken.js"' in html
    else:
        assert "show_apitoken" not in html and 'id="tab-developer"' not in html


def test_the_rotate_button_is_a_form_that_names_where_to_return(client, as_role):
    """A form rather than data-post, because it carries `next`: the route follows that
    field, not the Referer, which is optional and was matched by substring."""
    as_role("contributor")
    html = client.get("/profile").get_data(as_text=True)
    form = re.search(r'<form method="post" action="/admin/regenerate_apitoken"[^>]*>(.*?)</form>', html, re.DOTALL).group(1)
    assert 'name="csrf_token"' in form and '<input type="hidden" name="next" value="profile">' in form


@pytest.mark.parametrize("form, location", [({"next": "profile"}, "/profile#developer"), ({}, "/settings"), ({"next": "http://evil/"}, "/settings")],
                         ids=["profile", "absent", "unknown"])
def test_a_rotation_returns_to_the_page_named_by_the_form(client, as_role, app, form, location):
    user_id = as_role("contributor")
    response = client.post("/admin/regenerate_apitoken", data=form)
    assert response.status_code == 302 and response.headers["Location"].endswith(location)
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).apitoken != "apitoken-contributor"


def test_a_pre_1_5_0_token_is_marked_deprecated(client, as_role, app):
    user_id = as_role("contributor")
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.apitoken = "0123456789abcdef0123456789abcdef"
        user_info.saveToDb()
    html = client.get("/profile").get_data(as_text=True)
    assert "issued before MCRITweb 1.5.0" in html and "0123456789abcdef0123456789abcdef" not in html


# --- the tabs and the page's own code ------------------------------------------------------------------------

@pytest.mark.parametrize("query, shown", [("", "overview"), ("?tab=jobs", "jobs"), ("?tab=uploads", "uploads"), ("?tab=developer", "developer"), ("?tab=nothing", "overview")],
                         ids=["default", "jobs", "uploads", "developer", "unknown"])
def test_the_url_picks_the_pane(client, as_role, query, shown):
    as_role("contributor")
    html = client.get("/profile" + query).get_data(as_text=True)
    assert re.findall(r'<div class="tab-pane fade show active" id="pane-(\w+)"', html) == [shown]
    assert re.findall(r'<button class="nav-link active" id="tab-(\w+)"', html) == [shown]


def test_a_pending_account_has_no_developer_tab_and_is_not_sent_to_it(client, as_role):
    as_role("pending")
    html = client.get("/profile?tab=developer").get_data(as_text=True)
    assert 'id="tab-developer"' not in html
    assert re.findall(r'<div class="tab-pane fade show active" id="pane-(\w+)"', html) == ["overview"]


def test_the_page_carries_its_script_in_static_files(client, as_role):
    """150 lines of inline script, onclick= handlers, bootstrap.Tab at parse time and a
    second copy of clipboard_js() were on the page. The page's own block holds one
    inline script, the pagination widget's helper from master's macro, and loads its
    own code from static/profile.js and static/show_apitoken.js."""
    as_role("contributor")
    html = client.get("/profile").get_data(as_text=True)
    block = html[html.index("<h1"):html.index('id="submitFileModal"')]
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", block, re.DOTALL)
    assert len(inline) == 1 and "pagination_js_helper" in inline[0]
    assert "<style" not in block and "bootstrap.Tab" not in block and "copySnippet" not in block
    assert re.findall(r'onclick="(?!pagination_js_helper|copy_to_clipboard)', block) == []
    assert html.count("function copy_to_clipboard") == 1
    assert 'src="/static/profile.js"' in block and 'src="/static/show_apitoken.js"' in block


def test_no_literal_colour_is_written_outside_the_root_block():
    """The page's rules name their colours as custom properties on :root, so a theme can
    give the same names other values; the first version had twenty literal colours."""
    import pathlib
    css = pathlib.Path(__file__).resolve().parents[1] / "mcritweb" / "static" / "style.css"
    text = css.read_text()
    root = text[text.index(":root {"):text.index("}", text.index(":root {"))]
    profile_block = text[text.index("/* --- the profile page"):]
    assert re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", profile_block) is None
    for name in ("--surface-page", "--surface-subtle", "--text-body", "--accent", "--border-subtle"):
        assert f"{name}:" in root, name


def test_the_settings_page_links_to_the_profile(client, as_role):
    as_role("visitor")
    html = client.get("/settings").get_data(as_text=True)
    assert 'href="/profile"' in html and "Last Login:" in html
