"""Every admin UI action is available to an admin token, with the same safeguards."""

import ast
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import testApiParity
from testApiParity import ANSWER, headers

from mcritweb import db
from mcritweb.db import ServerInfo, UserColumnSettings, UserFilters, UserInfo, is_legacy_apitoken
from mcritweb.views import api

wire = testApiParity.wire

MAINTENANCE = [
    ("rebuild_index", "GET"), ("rebuild_picblockhash_index", "GET"),
    ("recalculate_pichashes", "GET"), ("recalculate_minhashes", "GET"),
    ("repair_minhashes", "POST"), ("recompute_family_stats", "POST"),
    ("respawn", "POST"),
    ('complete_minhashes', 'GET'), ('rebuild_function_range_index', 'GET'),
    ('rebuild_band_df_index', 'GET'), ('band_df_cutoff_coverage', 'GET'),
    ('delete_orphaned_queue_files', 'POST'),
]
LOCAL = [
    ("GET", "admin/users", {}),
    ("PUT", "admin/users/2/role", {"role": "contributor"}),
    ("DELETE", "admin/users/2", {}),
    ("POST", "admin/users/regenerate_legacy_apitokens", {}),
    ("GET", "admin/server", {}),
    ("PUT", "admin/server", {"mcrit_server_url": "https://backend.example:8000"}),
]


@pytest.fixture
def admin_users(make_user):
    root = make_user("admin")
    target = make_user("visitor")
    assert root == 1 and target == 2
    return root, target


@pytest.mark.parametrize("path,backend_method", MAINTENANCE)
def test_admins_can_schedule_every_ui_maintenance_action(client, admin_users, wire, path, backend_method):
    response = client.post(f"/api/{path}", headers=headers("admin"), json={"reset_server": "RESET"} if path == "respawn" else None)
    route = next(route for route in api.ROUTES if route.method == 'POST' and re.fullmatch(route.path, path))
    if not api.route_available(route):
        assert response.status_code == 501
        assert wire.calls == []
        return
    assert response.status_code == 200, response.data
    assert response.data == ANSWER
    assert wire.calls[-1].method == backend_method
    assert urlsplit(wire.calls[-1].url).path == f"/{path}"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path,backend_method", MAINTENANCE)
@pytest.mark.parametrize("role", ["pending", "visitor", "contributor"])
def test_non_admins_cannot_schedule_maintenance(client, make_user, wire, app, path, backend_method, role):
    make_user(role)
    probes = []
    app.config["MCRIT_SERVER_PROBE"] = lambda: probes.append(True) or True
    response = client.post(f"/api/{path}", headers=headers(role), json={"reset_server": "RESET"})
    assert response.status_code == 403
    assert wire.calls == [] and probes == []


@pytest.mark.parametrize("method,path,body", LOCAL)
@pytest.mark.parametrize("role", ["pending", "visitor", "contributor"])
def test_local_administration_is_admin_only(client, make_user, wire, method, path, body, role):
    make_user(role)
    response = client.open(f"/api/{path}", method=method, headers=headers(role), json=body)
    assert response.status_code == 403
    assert wire.calls == []


@pytest.mark.parametrize("path,backend_method", MAINTENANCE)
def test_maintenance_cannot_be_started_by_get(client, admin_users, wire, path, backend_method):
    assert client.get(f"/api/{path}", headers=headers("admin")).status_code == 405
    assert wire.calls == []


@pytest.mark.parametrize("method,path,body", LOCAL)
def test_local_admin_routes_work_without_a_backend_or_a_session(app, client, admin_users, wire, method, path, body):
    app.config["MCRIT_SERVER_PROBE"] = lambda: False
    response = client.open(f"/api/{path}", method=method, headers=headers("admin"), json=body)
    assert response.status_code == (204 if method == "DELETE" else 200), response.data
    assert wire.calls == []
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers


@pytest.mark.parametrize("method,path,body", LOCAL)
def test_browser_admin_session_cannot_authorize_an_api_action(client, as_role, wire, method, path, body):
    as_role("admin")
    response = client.open(f"/api/{path}", method=method, json=body)
    assert response.status_code == 403
    assert wire.calls == []


def test_admin_reads_never_expose_tokens_or_password_hashes(app, client, admin_users, wire):
    with app.app_context():
        server = ServerInfo.fromDb()
        server.server_token = "SECRET-SERVER-TOKEN"
        server.registration_token = "SECRET-REGISTRATION-TOKEN"
        server.saveToDb()
        passwords = [user.password for user in db.get_all_user_info()]
    for path in ("admin/users", "admin/server"):
        response = client.get(f"/api/{path}", headers=headers("admin"))
        assert response.status_code == 200
        for secret in ["SECRET-SERVER-TOKEN", "SECRET-REGISTRATION-TOKEN", "apitoken-admin", "apitoken-visitor", *passwords]:
            assert secret.encode() not in response.data


@pytest.mark.parametrize("method,path,payload", [
    ("DELETE", "admin/users/1", None),
    ("PUT", "admin/users/1/role", {"role": "visitor"}),
])
def test_the_root_account_cannot_be_deleted_or_demoted(app, client, admin_users, wire, method, path, payload):
    assert client.open(f"/api/{path}", method=method, headers=headers("admin"), json=payload).status_code == 403
    with app.app_context():
        assert UserInfo.fromDb(user_id=1).role == "admin"


@pytest.mark.parametrize("payload", [{"role": "superadmin"}, {"role": []}, {"role": None}, {"username": "x"}, [], None])
def test_invalid_role_updates_do_not_change_the_account(app, client, admin_users, wire, payload):
    response = client.put("/api/admin/users/2/role", headers=headers("admin"), json=payload)
    assert response.status_code == 400
    with app.app_context():
        assert UserInfo.fromDb(user_id=2).role == "visitor"


def test_changing_a_missing_user_returns_not_found(client, admin_users, wire):
    assert client.put("/api/admin/users/999/role", headers=headers("admin"), json={"role": "visitor"}).status_code == 404


def test_deleted_users_lose_tokens_and_all_preferences(app, client, admin_users, wire):
    with app.app_context():
        filters = UserFilters.fromDict(2, {})
        filters.saveToDb()
        UserColumnSettings(2).saveToDb()
    assert client.delete("/api/admin/users/2", headers=headers("admin")).status_code == 204
    assert client.get("/api/status", headers=headers("visitor")).status_code == 403
    with app.app_context():
        for table in ("user_filters", "user_column_settings"):
            assert db.get_db().execute(f"SELECT 1 FROM {table} WHERE user_id = 2").fetchone() is None
        assert UserInfo.fromDb(user_id=2) is None


def test_role_changes_take_effect_on_the_next_token_request(app, client, admin_users, wire):
    assert client.put("/api/admin/users/2/role", headers=headers("admin"), json={"role": "admin"}).status_code == 200
    assert client.get("/api/admin/server", headers=headers("visitor")).status_code == 200
    assert client.put("/api/admin/users/2/role", headers=headers("admin"), json={"role": "visitor"}).status_code == 200
    assert client.get("/api/admin/server", headers=headers("visitor")).status_code == 403


def test_rotating_legacy_tokens_revokes_only_the_old_tokens(app, client, admin_users, wire):
    old_token = "a" * 32
    with app.app_context():
        user = UserInfo.fromDb(user_id=2)
        user.apitoken = old_token
        user.saveToDb()
    response = client.post("/api/admin/users/regenerate_legacy_apitokens", headers=headers("admin"))
    assert response.status_code == 200 and response.json["data"]["rotated"] == 1
    assert client.get("/api/status", headers={"apitoken": old_token}).status_code == 403
    with app.app_context():
        user = UserInfo.fromDb(user_id=2)
        assert not is_legacy_apitoken(user.apitoken)
    assert client.get("/api/status", headers={"apitoken": user.apitoken}).status_code == 200
    assert client.get("/api/admin/server", headers=headers("admin")).status_code == 200


def test_listing_users_obeys_single_user_mode(app, client, admin_users, wire):
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = "single"
        server.saveToDb()
    assert client.get("/api/admin/users", headers=headers("admin")).status_code == 403


def test_server_updates_preserve_omitted_values_and_invalidate_probe(app, client, admin_users, wire, monkeypatch):
    cleared = []
    monkeypatch.setattr("mcritweb.views.admin_actions.forget_server_probe", lambda: cleared.append(True))
    with app.app_context():
        server = ServerInfo.fromDb()
        server.server_token = "OLD-TOKEN"
        server.saveToDb()
    response = client.put("/api/admin/server", headers=headers("admin"), json={"mcrit_server_url": "https://new.example:8000"})
    assert response.status_code == 200 and cleared == [True]
    assert b"OLD-TOKEN" not in response.data
    with app.app_context():
        server = ServerInfo.fromDb()
        assert server.server_token == "OLD-TOKEN"
        assert server.url == "https://new.example:8000"
    assert client.put("/api/admin/server", headers=headers("admin"), json={"mcrit_server_token": ""}).status_code == 200
    with app.app_context():
        assert ServerInfo.fromDb().server_token == ""


@pytest.mark.parametrize("body", [
    {"mcrit_server_url": "file:///etc/passwd"}, {"mcrit_server_url": "https://user:password@example.org"},
    {"mcrit_server_url": "https://example.org:bad"}, {"mcrit_server_token": 123},
    {"operation_mode": "single"}, {"mcrit_server_url": "https://example.org?extra=value"},
])
def test_malformed_server_changes_do_not_write(app, client, admin_users, wire, body):
    assert client.put("/api/admin/server", headers=headers("admin"), json=body).status_code == 400
    with app.app_context():
        assert ServerInfo.fromDb().url == "http://127.0.0.1:8000"


@pytest.mark.parametrize("payload", [None, {}, {"reset_server": "reset"}, {"reset_server": True}, {"reset_server": "RESET", "extra": 1}])
def test_reset_requires_the_same_explicit_confirmation_as_the_ui(client, admin_users, wire, payload):
    assert client.post("/api/respawn", headers=headers("admin"), json=payload).status_code == 400
    assert wire.calls == []


@pytest.mark.parametrize("code,body,cleared", [
    (200, b'{"status":"successful","data":{}}', True),
    (500, b'{"status":"failed","data":{}}', False),
    (200, b'{"status":"failed","data":{}}', False),
    (200, b'not-json', False),
])
def test_only_a_successful_backend_reset_clears_local_state(client, admin_users, wire, monkeypatch, code, body, cleared):
    cleanups = []
    monkeypatch.setattr(api, "clear_reset_state", lambda: cleanups.append(True))
    wire.answers["POST", "/respawn"] = code, body
    client.post("/api/respawn", headers=headers("admin"), json={"reset_server": "RESET"})
    assert bool(cleanups) == cleared


@pytest.mark.parametrize("path", ["export", "export/1,2"])
@pytest.mark.parametrize("role", ["visitor", "contributor", "admin"])
def test_import_and_export_use_the_ui_contributor_role(client, make_user, wire, path, role):
    make_user(role)
    response = client.get(f"/api/{path}?compress=false", headers=headers(role))
    assert response.status_code == (403 if role == "visitor" else 200)
    if role != "visitor":
        assert "compress" not in parse_qs(urlsplit(wire.calls[-1].url).query)
    response = client.post("/api/import", headers=headers(role), json={"config": {}, "content": {}})
    assert response.status_code == (403 if role == "visitor" else 200)


def test_every_admin_ui_action_has_an_api_counterpart():
    tree = ast.parse(Path("mcritweb/views/administration.py").read_text())
    actions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
               and any(isinstance(decorator, ast.Name) and decorator.id == "admin_required" for decorator in node.decorator_list)}
    assert actions == set(api.ADMIN_UI_PARITY)
    for method, path in api.ADMIN_UI_PARITY.values():
        assert any(route.method == method and route.role == "admin" and re.fullmatch(route.path, path) for route in api.ROUTES)
    for route in api.ROUTES:
        if route.role == "admin":
            assert any(method == route.method and re.fullmatch(route.path, path) for method, path in api.ADMIN_UI_PARITY.values()) \
                or any(route.method == 'POST' and re.fullmatch(route.path, path) for path, _ in MAINTENANCE), route


@pytest.mark.parametrize("query,expected", [
    ("", None),
    ("?band_df_cutoff=500", ["500"]),
], ids=["configured cutoff", "another cutoff"])
def test_the_band_df_cutoff_coverage_forwards_the_cutoff_it_was_given(client, admin_users, wire, query, expected):
    response = client.post(f"/api/band_df_cutoff_coverage{query}", headers=headers("admin"))

    assert response.status_code == 200, response.data
    assert parse_qs(urlsplit(wire.calls[-1].url).query).get("band_df_cutoff") == expected


@pytest.mark.parametrize("value", ["many", "-1", "1.5", str(2**63)], ids=["word", "negative", "fraction", "too large"])
def test_a_malformed_band_df_cutoff_is_refused_before_the_backend(client, admin_users, wire, value):
    """As the matching options' band_df_cutoff is: it was forwarded as typed."""
    response = client.post(f"/api/band_df_cutoff_coverage?band_df_cutoff={value}", headers=headers("admin"))

    assert response.status_code == 400
    assert wire.calls == []
