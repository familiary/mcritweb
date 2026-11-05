"""Token-authenticated local administration handlers (dispatched by api.ROUTES).

They use the same operations as the browser. No backend probe is needed to repair
connection settings or manage users, and no password hash or token is serialized.
"""

from urllib.parse import urlsplit

from flask import Response, abort, g, request

from mcritweb import db
from mcritweb.db import UserInfo, is_legacy_apitoken
from mcritweb.views.admin_actions import change_role, delete_account, rotate_legacy_tokens, update_connection


def json_object():
    try:
        body = request.get_json(silent=True)
    except RecursionError:
        abort(400, "JSON nesting is too deep.")
    if not isinstance(body, dict):
        abort(400, "Supply a JSON object.")
    return body


def body_fields(allowed, max_bytes=65536):
    if (request.content_length is None and request.environ.get('wsgi.input_terminated')) \
            or (request.content_length is not None and request.content_length > max_bytes):
        abort(413, f'This request must declare at most {max_bytes} bytes.')
    if not request.is_json and any(len(request.form.getlist(name)) > 1 for name in request.form):
        abort(400, 'Repeated form fields are not supported; use JSON arrays for lists.')
    body = json_object() if request.is_json else request.form.to_dict()
    if not isinstance(body, dict) or not body or set(body) - set(allowed):
        abort(400, "Supply an object containing supported fields.")
    return body


def user_data(user):
    return {"user_id": user.user_id, "username": user.username, "role": user.role,
            "registered": str(user.registered), "last_login": str(user.last_login),
            "legacy_apitoken": is_legacy_apitoken(user.apitoken)}


def list_users(match):
    # The UI's users list is hidden in single-user mode as well.
    if g.operation_mode == "single":
        abort(403, "User listing requires multi-user mode.")
    return {"status": "successful", "data": [user_data(user) for user in db.get_all_user_info()]}


def user_role(match):
    fields = body_fields(("role",))
    role = fields.get("role")
    if not isinstance(role, str):
        abort(400)
    try:
        change_role(int(match["user_id"]), role)
    except PermissionError as error:
        abort(403, str(error))
    except ValueError as error:
        abort(400, str(error))
    except LookupError as error:
        abort(404, str(error))
    return {"status": "successful", "data": user_data(UserInfo.fromDb(user_id=int(match["user_id"])))}


def delete_user(match):
    try:
        delete_account(int(match["user_id"]))
    except PermissionError as error:
        abort(403, str(error))
    return Response(status=204)


def regenerate_legacy_apitokens(match):
    return {"status": "successful", "data": {"rotated": rotate_legacy_tokens()}}


def server_data():
    server = db.get_server_info()
    return {"mcrit_server_url": server.url, "operation_mode": server.operation_mode,
            "server_uuid": server.server_uuid, "server_version": server.server_version,
            "server_token_configured": bool(server.server_token)}


def get_server(match):
    return {"status": "successful", "data": server_data()}


def valid_server_url(url):
    try:
        parsed = urlsplit(url)
        valid_url = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        parsed.port  # also validate a malformed/out-of-range port
    except ValueError:
        valid_url = False
    return bool(valid_url)


def change_server(match):
    fields = body_fields(("mcrit_server_url", "mcrit_server_token"))
    if not all(isinstance(value, str) for value in fields.values()):
        abort(400, "Server settings must be strings.")
    server = db.get_server_info()
    url = fields.get("mcrit_server_url", server.url)
    if not valid_server_url(url):
        abort(400, "Supply an HTTP(S) backend URL without credentials, query or fragment.")
    update_connection(url, fields.get("mcrit_server_token", server.server_token))
    return {"status": "successful", "data": server_data()}
