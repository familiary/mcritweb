"""API/UI parity through the real McritClient and requests' wire encoding (#345).

The transport is replaced below requests, so these tests catch client methods that
ignore raw_responses, encode false incorrectly, or splice metadata into the URL.
No analysis server or corpus is needed for this suite.
"""

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from mcrit.client.McritClient import McritClient
from smda.common.SmdaReport import SmdaReport

from mcritweb.views import api
from mcritweb.views.client import default_client_factory

JOB = "0123456789abcdef01234567"
SHA = "ab" * 32
ANSWER = b'{"status":"successful","data":{"id":2,"score":1.50}}'
BINARY = b'MZ\x00binary\xff'

# Independent examples of the supported public contract. Every table entry must
# have one, and each example actually runs its argument builder and client method.
CASES = [
    ("GET", "status", {}),
    ("GET", "version", {}),
    ("GET", "samples", {}),
    ("POST", "samples", {"report": True}),
    ("POST", "samples/binary", {"data": b"MZ\x00sample"}),
    ("POST", "samples/ids", {"data": "1,2"}),
    ("GET", "samples/1", {}),
    ("PUT", "samples/1", {"json": {"family_name": "family", "version": "2", "is_library": False}}),
    ("DELETE", "samples/1", {}),
    ("GET", f"samples/sha256/{SHA}", {}),
    ("GET", "samples/1/functions", {}),
    ("GET", "samples/1/smda", {}),
    ("GET", "families", {}),
    ("POST", "families/ids", {"data": "1,2"}),
    ("GET", "families/1", {}),
    ("PUT", "families/1", {"json": {"family_name": "family", "is_library": False}}),
    ("DELETE", "families/1", {}),
    ("GET", "functions", {}),
    ("POST", "functions", {"data": "1,2"}),
    ("GET", "functions/1", {}),
    ("PUT", "functions/1", {"json": {"function_name": "function"}}),
    ("POST", "query", {"report": True}),
    ("POST", "query/function", {"report": True}),
    ("GET", "query/pichash/0123456789abcdef/summary", {}),
    ("GET", "query/picblockhash/0123456789abcdef/summary", {}),
    ("POST", "query/binary", {"data": b"MZsample"}),
    ("POST", "query/binary/mapped/4194304", {"data": b"mapped"}),
    ("GET", "jobs", {}),
    ("GET", "jobs/stats", {}),
    ("GET", f"jobs/{JOB}", {}),
    ("DELETE", f"jobs/{JOB}", {}),
    ("GET", f"jobs/{JOB}/result", {}),
    ("GET", f"results/{JOB}", {}),
    ("GET", f"results/{JOB}/job", {}),
    ("GET", "matches/sample/1", {}),
    ("GET", "matches/sample/1/2", {}),
    ("GET", "matches/sample/cross/1,2", {}),
    ("GET", "matches/function/1/2", {}),
    ("GET", "matches/function/1", {}),
    ("GET", "uniqueblocks/samples/1,2", {}),
    ("GET", "uniqueblocks/family/1", {}),
    ("GET", "search/families", {}),
    ("GET", "search/samples", {}),
    ("GET", "search/functions", {}),
    ("POST", "import", {"json": {"config": {}, "content": {}}}),
    ("GET", "export", {}),
    ("GET", "export/1,2", {}),
    ("GET", "samples/1/binary", {}),
    ("GET", "jobs/count", {}),
    ("DELETE", "jobs", {"query_string": {"confirm": "DELETE"}}),
    ("POST", "uniqueblocks/samples/1,2", {"json": {"exclude_picblockhashes": [1, 2]}}),
    ("POST", "uniqueblocks/family/1", {"json": {"exclude_picblockhashes": [1, 2]}}),
    ("POST", "samples/1/tags", {"json": {"tags": ["reviewed"]}}),
    ("DELETE", "samples/1/tags", {"json": {"tags": ["reviewed"]}}),
    ("GET", "tags", {"query_string": {"entity": "sample"}}),
    ("POST", f"jobs/{JOB}/rerun", {}),
]
CONTRIBUTOR_CASES = [case for case in CASES if case[0] in ("PUT", "DELETE") or case[1].endswith('/tags')
                     or case[1] in ("samples", "samples/binary", "import", "export", "export/1,2", "samples/1/smda", "samples/1/binary")]
# Where the wire path is not the API path. McritClient.getMatchFunctionVs always
# writes a second id, and the single-id path has always been forwarded through it.
WIRE_PATHS = {"matches/function/1": "/matches/function/1/None"}
MUTATIONS = [case for case in CONTRIBUTOR_CASES if case[0] != "GET"]


@pytest.fixture
def wire(app, monkeypatch):
    sent = SimpleNamespace(calls=[], answers={})

    def send(session, prepared, **kwargs):
        sent.calls.append(prepared)
        key = prepared.method, urlsplit(prepared.url).path.rstrip('/') or '/'
        body = ANSWER
        if key == ("GET", "/families/1") and parse_qs(urlsplit(prepared.url).query).get("with_samples") == ["true"]:
            body = b'{"status":"successful","data":{"samples":{"1":{}}}}'
        code, body = sent.answers.get(key, (200, body))
        response = requests.Response()
        response.status_code = code
        response._content = body
        response.headers["Content-Type"] = "application/json"
        if key == ('GET', '/samples/1/binary') and key not in sent.answers:
            response.headers['Content-Type'] = 'application/octet-stream'
            response._content = BINARY
        response.headers["Set-Cookie"] = "private-backend-cookie=1"
        response.request = prepared
        response.url = prepared.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    app.config["MCRIT_CLIENT_FACTORY"] = default_client_factory
    return sent


def options(body):
    body = body.copy()
    if body.pop("report", False):
        report = SmdaReport()
        report.sha256 = SHA
        body["json"] = report.toDict()
    return body


def headers(role="contributor"):
    return {"apitoken": f"apitoken-{role}"}


@pytest.mark.parametrize("method,path,body", CASES)
def test_every_route_forwards_the_backend_answer(client, make_user, wire, method, path, body):
    make_user("contributor")
    route = next(route for route in api.ROUTES if route.method == method and re.fullmatch(route.path, path))
    if not api.route_available(route):
        assert client.open(f"/api/{path}", method=method, headers=headers(), **options(body)).status_code == 501
        return
    response = client.open(f"/api/{path}", method=method, headers=headers(), **options(body))
    assert response.status_code == 200, response.data
    assert response.data == (BINARY if route.binary_response else ANSWER)
    assert response.mimetype == ("application/octet-stream" if route.binary_response else "application/json")
    assert "Set-Cookie" not in response.headers
    assert wire.calls[-1].method == method
    assert urlsplit(wire.calls[-1].url).path.rstrip("/") == WIRE_PATHS.get(path, f"/{path}")
    assert wire.calls[-1].headers["username"] == "contributoruser"


def test_every_registered_route_has_a_wire_example():
    for route in api.ROUTES:
        if route.role == "admin" or route.local_handler is not None:
            # Local workflows and account actions have functional fixture tests.
            continue
        assert any(method == route.method and re.fullmatch(route.path, path) for method, path, _ in CASES), route


@pytest.mark.parametrize("method,path,body", MUTATIONS)
@pytest.mark.parametrize("role", ["pending", "visitor", "contributor", "admin"])
def test_mutations_use_the_ui_role(client, make_user, wire, app, method, path, body, role):
    make_user(role)
    probes = []
    app.config["MCRIT_SERVER_PROBE"] = lambda: probes.append(True) or True
    response = client.open(f"/api/{path}", method=method, headers=headers(role), **options(body))
    route = next(route for route in api.ROUTES if route.method == method and re.fullmatch(route.path, path))
    expected = (200 if api.route_available(route) else 501) if role in ("contributor", "admin") else 403
    assert response.status_code == expected
    if response.status_code == 403:
        assert wire.calls == []
        assert probes == []


@pytest.mark.parametrize("path", ["samples/1/smda", "samples/1/binary"], ids=["smda", "binary"])
@pytest.mark.parametrize("role", ["pending", "visitor"])
def test_sample_downloads_need_a_contributor_as_the_corpus_export_does(client, make_user, wire, path, role):
    """Below contributor, the UI offers no way to download what a sample was
    submitted as: corpus export is contributor_required. The token must not be one."""
    make_user(role)
    response = client.get(f"/api/{path}", headers=headers(role))
    assert response.status_code == 403
    assert wire.calls == []


def test_the_single_function_match_path_is_forwarded_and_queues_nothing(client, make_user, wire):
    """GET matches/function/<id> has always been forwarded to the backend as
    getMatchFunctionVs(id, None). It must not become a whole-sample matching job."""
    make_user("visitor")
    response = client.get("/api/matches/function/1", headers=headers("visitor"))
    assert response.status_code == 200 and response.data == ANSWER
    assert [(call.method, urlsplit(call.url).path) for call in wire.calls] == [("GET", "/matches/function/1/None")]


@pytest.mark.parametrize("query", ["start=wat", "limit=1.5", "start=", "limit=ten"], ids=["word-start", "fraction-limit", "empty-start", "word-limit"])
def test_malformed_queue_integers_are_refused_rather_than_defaulted(client, make_user, wire, query):
    """A malformed start or limit used to become 0, so a typo silently read a
    different page of the queue than the one asked for."""
    make_user("visitor")
    response = client.get(f"/api/jobs?{query}", headers=headers("visitor"))
    assert response.status_code == 400 and response.is_json
    assert wire.calls == []


@pytest.mark.parametrize("method,path,body", [case for case in CASES if case not in CONTRIBUTOR_CASES])
def test_visitor_operations_need_only_a_visitor_token(client, make_user, wire, method, path, body):
    make_user("visitor")
    response = client.open(f"/api/{path}", method=method, headers=headers("visitor"), **options(body))
    route = next(route for route in api.ROUTES if route.method == method and re.fullmatch(route.path, path))
    assert response.status_code == (200 if api.route_available(route) else 501)


@pytest.mark.parametrize("method,path,body", [
    ("POST", "status", {}), ("DELETE", "status", {}),
    ("GET", "samples/binary", {}), ("DELETE", "uniqueblocks/samples/1", {}),
    ("POST", "matches/sample/cross/1,2", {}), ("PUT", f"jobs/{JOB}", {}),
    ("GET", "query", {}), ("DELETE", f"jobs/{JOB}/result", {}),
])
def test_wrong_method_cannot_run_a_different_operation(client, make_user, wire, method, path, body):
    make_user("admin")
    response = client.open(f"/api/{path}", method=method, headers=headers("admin"), **body)
    assert response.status_code == 405
    assert wire.calls == []
    assert "Allow" in response.headers


@pytest.mark.parametrize("method,path", [
    ("GET", "status\n"), ("POST", "samples/binary/extra"), ("DELETE", "samples/1/extra"),
    ("DELETE", "jobs/abc"), ("DELETE", f"jobs/{JOB}\n"), ("PUT", "families/1/extra"),
])
def test_route_patterns_are_full_matches(client, make_user, wire, method, path):
    make_user("admin")
    response = client.open(f"/api/{path.replace(chr(10), '%0A')}", method=method, headers=headers("admin"))
    assert response.status_code in (404, 405, 501)
    assert wire.calls == []


@pytest.mark.parametrize("path,expected", [
    ("samples/1", {"family_name": "", "version": "2", "is_library": "False"}),
    ("families/1", {"family_name": "", "is_library": "False"}),
])
@pytest.mark.parametrize("form", [False, True])
def test_edits_accept_json_or_forms_and_preserve_false_and_empty(client, make_user, wire, path, expected, form):
    make_user("contributor")
    payload = {key: False if key == "is_library" else value for key, value in expected.items()}
    response = client.put(f"/api/{path}", headers=headers(), **{"data" if form else "json": payload})
    assert response.status_code == 200
    sent = wire.calls[-1]
    if sent.headers["Content-Type"].startswith("application/json"):
        assert json.loads(sent.body) == payload
    else:
        assert {key: values[0] for key, values in parse_qs(sent.body, keep_blank_values=True).items()} == expected


@pytest.mark.parametrize("path,payload", [
    ("samples/1", []), ("samples/1", ["x"]), ("families/1", None),
    ("families/1", {}), ("samples/1", {"family_name": 1}),
    ("samples/1", {"is_library": []}), ("families/1", {"is_library": "maybe"}),
    ("samples/1", {"version": None}), ("samples/1", {"unknown": "x"}),
    ("functions/1", {"function_name": "x\n"}), ("functions/1", {"function_name": "x" * 257}),
])
def test_malformed_edits_are_rejected_before_the_backend(client, make_user, wire, path, payload):
    make_user("contributor")
    response = client.put(f"/api/{path}", headers=headers(), data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    assert wire.calls == []


def test_binary_metadata_survives_url_encoding(client, make_user, wire):
    make_user("contributor")
    metadata = {"filename": "C++_%41.exe", "family": "R&D&is_dump=1", "version": "100% + 2"}
    response = client.post("/api/samples/binary", headers=headers(), query_string=metadata, data=b"MZ\x00bytes")
    assert response.status_code == 200
    assert wire.calls[-1].body == b"MZ\x00bytes"
    assert parse_qs(urlsplit(wire.calls[-1].url).query) == {key: [value] for key, value in metadata.items()}


@pytest.mark.parametrize("query", [
    {"is_dump": "true"}, {"is_dump": "true", "base_addr": "0x400000"},
    {"is_dump": "true", "base_addr": "0x400000", "bitness": "16"},
    {"base_addr": "0x10000000000000000"}, {"base_addr": "wat"}, {"is_dump": "maybe"},
])
def test_binary_metadata_is_validated_before_submission(client, make_user, wire, query):
    make_user("contributor")
    response = client.post("/api/samples/binary", headers=headers(), query_string=query, data=b"MZ")
    assert response.status_code == 400
    assert wire.calls == []


def test_binary_dump_parameters_are_forwarded(client, make_user, wire):
    make_user("contributor")
    response = client.post("/api/samples/binary?is_dump=true&base_addr=0x400000&bitness=64", headers=headers(), data=b"dump")
    assert response.status_code == 200
    assert parse_qs(urlsplit(wire.calls[-1].url).query) == {"is_dump": ["1"], "base_addr": ["0x400000"], "bitness": ["64"]}


@pytest.mark.parametrize("sha256", ["not-a-hash", "ab" * 31, "a" * 64 + "\n"])
def test_report_submission_uses_the_ui_hash_validation(client, make_user, wire, sha256):
    make_user("contributor")
    report = SmdaReport()
    report.sha256 = sha256
    response = client.post("/api/samples", headers=headers(), json=report.toDict())
    assert response.status_code == 400
    assert wire.calls == []


def test_report_submission_normalizes_hash_case_as_the_ui_does(client, make_user, wire):
    make_user("contributor")
    report = SmdaReport()
    report.sha256 = SHA.upper()
    response = client.post("/api/samples", headers=headers(), json=report.toDict())
    assert response.status_code == 200
    assert json.loads(wire.calls[-1].body)["sha256"] == SHA


@pytest.mark.parametrize("selection", ["1,wat", "1,,2", "-1,2", "1,2\n", ",1,2", "1" * 5000])
def test_invalid_unique_selections_do_not_reach_the_backend(client, make_user, wire, selection):
    make_user("visitor")
    response = client.get(f"/api/uniqueblocks/samples/{selection.replace(chr(10), '%0A')}", headers=headers("visitor"))
    assert response.status_code in (400, 404)
    assert wire.calls == []


def test_unique_samples_are_capped_before_any_backend_lookups(client, make_user, wire):
    make_user("visitor")
    selection = ",".join(map(str, range(api.MAX_SELECTED_SAMPLES + 1)))
    response = client.get(f"/api/uniqueblocks/samples/{selection}", headers=headers("visitor"))
    assert response.status_code == 400
    assert wire.calls == []


def test_the_unique_sample_cap_allows_exactly_the_ui_limit(client, make_user, wire):
    make_user("visitor")
    selection = ",".join(map(str, range(api.MAX_SELECTED_SAMPLES)))
    response = client.get(f"/api/uniqueblocks/samples/{selection}", headers=headers("visitor"))
    assert response.status_code == 200
    assert len(wire.calls) == api.MAX_SELECTED_SAMPLES + 1


def test_unique_samples_are_normalized_before_lookup_and_submission(client, make_user, wire):
    make_user("visitor")
    response = client.get("/api/uniqueblocks/samples/2,1,2?covers_required=2&min_instructions=5", headers=headers("visitor"))
    assert response.status_code == 200
    assert [urlsplit(call.url).path for call in wire.calls] == ["/samples/1", "/samples/2", "/uniqueblocks/samples/1,2"]
    assert parse_qs(urlsplit(wire.calls[-1].url).query) == {"covers_required": ["2"], "min_instructions": ["5"]}


@pytest.mark.parametrize("status", [404, 500])
def test_a_missing_sample_or_backend_failure_does_not_queue_unique_blocks(client, make_user, wire, status):
    make_user("visitor")
    wire.answers["GET", "/samples/2"] = status, b'{"status":"failed","data":{"message":"unavailable"}}'
    response = client.get("/api/uniqueblocks/samples/1,2", headers=headers("visitor"))
    assert response.status_code == status
    assert all("uniqueblocks" not in call.url for call in wire.calls)


@pytest.mark.parametrize("body", [b'not-json', b'null', b'{"status":"failed","data":null}'])
def test_a_broken_lookup_does_not_queue_unique_blocks(client, make_user, wire, body):
    make_user("visitor")
    wire.answers["GET", "/samples/1"] = 200, body
    response = client.get("/api/uniqueblocks/samples/1", headers=headers("visitor"))
    assert response.status_code == 502
    assert len(wire.calls) == 1


def test_empty_families_cannot_queue_unique_blocks(client, make_user, wire):
    make_user("visitor")
    wire.answers["GET", "/families/1"] = 200, b'{"status":"successful","data":{"samples":{}}}'
    response = client.get("/api/uniqueblocks/family/1", headers=headers("visitor"))
    assert response.status_code == 400
    assert len(wire.calls) == 1


@pytest.mark.parametrize("path", ["matches/sample/1", "matches/sample/1/2", "matches/sample/cross/2,1,2"])
def test_matching_parameters_are_forwarded_and_false_stays_false(client, make_user, wire, path):
    make_user("visitor")
    response = client.get(f"/api/{path}?minhash_threshold=40&pichash_size=5&band_matches_required=2&force_recalculation=false&sample_group_only=false", headers=headers("visitor"))
    assert response.status_code == 200
    query = parse_qs(urlsplit(wire.calls[-1].url).query)
    assert query["minhash_score"] == ["40"]
    assert query["pichash_size"] == ["5"]
    assert query["band_matches_required"] == ["2"]
    assert query["force_recalculation"] == ["False"]
    assert "sample_group_only" not in query


@pytest.mark.parametrize("path", ["search/families", "search/samples", "search/functions"])
def test_search_forwards_terms_and_cursor_options(client, make_user, wire, path):
    make_user("visitor")
    query = {"query": 'family:"R&D"', "cursor": "opaque+cursor", "sort_by": "num_samples", "is_ascending": "false", "limit": "17"}
    response = client.get(f"/api/{path}", query_string=query, headers=headers("visitor"))
    assert response.status_code == 200
    assert parse_qs(urlsplit(wire.calls[-1].url).query) == {key: ["False" if key == "is_ascending" else value] for key, value in query.items()}


@pytest.mark.parametrize("method,path,body", MUTATIONS)
@pytest.mark.parametrize("status", [202, 400, 404, 409, 500])
def test_mutation_status_and_error_bytes_are_preserved(client, make_user, wire, method, path, body, status):
    make_user("contributor")
    route = next(route for route in api.ROUTES if route.method == method and re.fullmatch(route.path, path))
    if not api.route_available(route):
        pytest.skip('This installed MCRIT client has no such operation.')
    wire.answers[method, f"/{path}"] = status, ANSWER
    response = client.open(f"/api/{path}", method=method, headers=headers(), **options(body))
    assert response.status_code == status
    assert response.data == ANSWER


@pytest.mark.parametrize("path", ["unlisted-operation", "config", "users"])
def test_unlisted_operations_stay_unavailable(client, make_user, wire, path):
    make_user("admin")
    assert client.post(f"/api/{path}", headers=headers("admin")).status_code == 501
    assert wire.calls == []


def test_ui_client_calls_have_an_api_route_or_a_documented_exception():
    # Include all view helpers, chained with_server_errors(client) calls, and both
    # search naming conventions. A new McritClient operation cannot silently be UI-only.
    methods = {name for name in dir(McritClient) if not name.startswith("_")}
    called = set()
    for path in Path("mcritweb/views").glob("*.py"):
        if path.name == "api.py":
            continue
        tree = ast.parse(path.read_text())
        called.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in methods)
        called.update(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in methods)
    routed = {route.client_method for route in api.ROUTES}
    covered = routed | set(api.UI_ONLY_CLIENT_METHODS) | {name for name, target in api.CLIENT_METHOD_ALIASES.items() if target in routed}
    assert called <= covered, f"Declare an API route or explain a UI-only exception for: {called - covered}"
    assert all(api.UI_ONLY_CLIENT_METHODS.values())


def test_token_identity_wins_over_browser_session_and_username_header(client, as_role, make_user, wire):
    as_role("admin")
    make_user("visitor")
    response = client.get("/api/status", headers={**headers("visitor"), "username": "adminuser"})
    assert response.status_code == 200
    assert wire.calls[-1].headers["username"] == "visitoruser"
    assert client.post("/api/rebuild_index", headers=headers("visitor")).status_code == 403


@pytest.mark.parametrize("error,status", [(requests.ConnectionError("offline"), 502), (requests.Timeout("late"), 504)])
def test_transport_failures_are_http_errors_without_a_flash_session(client, make_user, wire, monkeypatch, error, status):
    make_user("visitor")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(requests.Session, "send", fail)
    response = client.get("/api/status", headers=headers("visitor"))
    assert response.status_code == status
    assert response.data == b""
    assert "Location" not in response.headers and "Set-Cookie" not in response.headers


@pytest.mark.parametrize("path", ["samples/1", "families/1", "functions/1", "admin/server", "admin/users/2/role"])
def test_deep_json_edits_are_bad_requests(client, make_user, wire, path):
    make_user("admin")
    response = client.put(f"/api/{path}", headers=headers("admin"), data=b"[" * 10000, content_type="application/json")
    assert response.status_code == 400
    assert wire.calls == []
