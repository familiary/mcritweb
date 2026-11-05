"""Account and preference API. Caller identity always comes from g.api_user."""

import sqlite3
import uuid

from flask import Response, abort, current_app, g, request
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import ServerInfo, UserColumnSettings, UserFilters, UserInfo, generate_apitoken, is_legacy_apitoken, utc_now
from mcritweb.views import api_admin, authentication
from mcritweb.views.account_actions import AccountActionError, change_password, rename

FILTER_BOOLEANS = ('filter_unique_only', 'filter_exclude_own_family', 'filter_exclude_library', 'filter_exclude_pic')
FILTER_INTEGERS = ('filter_direct_min_score', 'filter_direct_nonlib_min_score', 'filter_frequency_min_score',
                   'filter_frequency_nonlib_min_score', 'filter_function_min_score', 'filter_function_max_score', 'filter_max_num_families')


def ok(data):
    return {'status': 'successful', 'data': data}


def text_fields(allowed, required=()):
    fields = api_admin.body_fields(allowed)
    if any(name not in fields for name in required) or not all(isinstance(value, str) for value in fields.values()):
        abort(400, 'Supply the required string fields.')
    return fields


def require_json_body():
    """Refuse anything but an application/json body on a route that needs no token.

    /api is exempt from CSRF because a cross-site request cannot supply an apitoken
    header. auth/token and auth/register take no token, so that argument does not
    cover them: an HTML form on any site can post urlencoded or multipart fields to
    them, and on a fresh instance that is enough to configure it with the form's
    author as admin. A browser sends application/json cross-site only after a CORS
    preflight, which this app never grants.
    """
    if request.mimetype != 'application/json':
        abort(415, 'Send this request as application/json.')


def credentials(allowed, required):
    if request.content_length is None or request.content_length > 65536:
        abort(413, 'Credential requests must declare at most 64 KiB.')
    fields = text_fields(allowed, required)
    if any(len(value) > 4096 for value in fields.values()):
        abort(400, 'Credential fields are too long.')
    return fields


def login():
    require_json_body()
    fields = credentials(('username', 'password'), ('username', 'password'))
    username, password = fields['username'], fields['password']
    if authentication._throttled(username):
        abort(429, authentication.TOO_MANY_ATTEMPTS)
    user = UserInfo.fromDb(username=username)
    if user is None or not password:
        authentication._spend_a_password_check(password)
        valid = False
    else:
        valid = check_password_hash(user.password, password)
    if not valid:
        db.record_failed_login(request.remote_addr, username)
        abort(401, authentication.LOGIN_FAILED)
    verified_password = user.password
    rehashed = authentication._rehash_if_stale(user, password)
    user.last_login = utc_now()
    user.saveLogin(verified_password=verified_password if rehashed else None)
    fresh = UserInfo.fromDb(user_id=user.user_id)
    if fresh is None or fresh.password != user.password:
        abort(401, authentication.LOGIN_FAILED)
    user = fresh
    db.clear_login_failures(request.remote_addr, username)
    # Password proof also lets a user recover from a deployment's legacy-token cutover.
    if is_legacy_apitoken(user.apitoken) and not current_app.config.get('ACCEPT_LEGACY_APITOKENS', True):
        rotate_user_token(user)
    return ok(dict(api_admin.user_data(user), apitoken=user.apitoken))


def register():
    require_json_body()
    fields = credentials(('username', 'password', 'password_confirm', 'registration_token', 'mcrit_server_url',
                          'mcrit_server_token', 'operation_mode', 'registration_token_new'), ('username', 'password', 'password_confirm'))
    username, password = fields['username'], fields['password']
    if authentication._throttled(authentication.registration_token_attempt(username)):
        abort(429, authentication.TOO_MANY_ATTEMPTS)
    if not db.is_well_formed_username(username) or db.is_reserved_username(username):
        abort(400, 'Username has invalid format or is reserved.')
    if not password or password != fields['password_confirm']:
        abort(400, 'Supply a nonempty password and matching confirmation.')
    first = g.first_user
    server = db.get_server_info()
    if not first:
        if server.operation_mode == 'single':
            abort(403, 'Registration is disabled in single-user mode.')
        if server.registration_token and fields.get('registration_token') != server.registration_token:
            db.record_failed_login(request.remote_addr, authentication.registration_token_attempt(username))
            abort(400, 'Invalid registration token provided.')
        if any(name in fields for name in ('mcrit_server_url', 'mcrit_server_token', 'operation_mode', 'registration_token_new')):
            abort(400, 'Connection and operation settings are only accepted during first-user setup.')
    else:
        url = fields.get('mcrit_server_url', '')
        if not api_admin.valid_server_url(url) or fields.get('operation_mode') not in ('single', 'multi'):
            abort(400, 'First-user setup requires an HTTP(S) backend URL and single or multi operation_mode.')
        server = ServerInfo()
        server.url = url
        server.operation_mode = fields['operation_mode']
        server.server_token = fields.get('mcrit_server_token', '')
        server.registration_token = fields.get('registration_token_new', '')
        server.server_uuid = str(uuid.uuid4())
        server.server_version = current_app.config['MCRITWEB_VERSION']
    user = UserInfo()
    user.username = username
    user.password = generate_password_hash(password)
    user.role = 'admin' if first else 'pending'
    user.apitoken = generate_apitoken()
    database = db.get_db()
    if first:
        database.execute('BEGIN IMMEDIATE;')
        if not db.is_first_user():
            database.rollback()
            abort(409, authentication.FIRST_USER_TAKEN)
        server.saveToDb(commit=False)
    try:
        user.saveToDb()
    except sqlite3.IntegrityError:
        database.rollback()
        # Match the browser's generic response for taken and newly-created names.
        if first:
            abort(409, authentication.FIRST_USER_TAKEN)
    return ok({'message': 'Instance configured.' if first else authentication.REGISTRATION_SUBMITTED}), 201 if first else 202


def profile(match):
    return ok(api_admin.user_data(g.api_user))


def rename_user(match):
    fields = credentials(('username', 'password'), ('username', 'password'))
    try:
        rename(g.api_user, fields['username'], fields['password'])
    except AccountActionError as error:
        abort(error.status, str(error))
    return profile(match)


def password(match):
    fields = credentials(('current_password', 'password', 'password_confirm'), ('current_password', 'password', 'password_confirm'))
    try:
        change_password(g.api_user, fields['current_password'], fields['password'], fields['password_confirm'])
    except AccountActionError as error:
        abort(error.status, str(error))
    return ok({'apitoken': g.api_user.apitoken})


def rotate_user_token(user):
    token = generate_apitoken()
    database = db.get_db()
    changed = database.execute('UPDATE user SET apitoken = ? WHERE id = ? AND apitoken = ? AND password = ?',
                               (token, user.user_id, user.apitoken, user.password)).rowcount
    database.commit()
    if changed != 1:
        abort(403, 'The account credentials changed; authenticate again.')
    user.apitoken = token


def rotate_token(match):
    rotate_user_token(g.api_user)
    return ok({'apitoken': g.api_user.apitoken})


def logout(match):
    # A token has no browser session to clear. Revoke this credential instead.
    rotate_token(match)
    return Response(status=204)


def get_filters(match):
    filters = UserFilters.fromDb(g.api_user.user_id) or UserFilters.fromDict(g.api_user.user_id, {})
    result = filters.toDict()
    result.pop('user_id', None)
    for name in FILTER_BOOLEANS:
        result[name] = bool(result[name])
    return ok({name: result[name] for name in (*FILTER_BOOLEANS, *FILTER_INTEGERS)})


def set_filters(match):
    from mcritweb.views.api import boolean
    fields = api_admin.body_fields((*FILTER_BOOLEANS, *FILTER_INTEGERS))
    values = get_filters(match)['data']
    for name, value in fields.items():
        if name in FILTER_BOOLEANS:
            values[name] = boolean(value)
        else:
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError
                values[name] = int(value)
                if not -(2**63) <= values[name] <= 2**63 - 1:
                    raise ValueError
            except ValueError:
                abort(400, 'Filter bounds must be integers.')
    UserFilters.fromDict(g.api_user.user_id, values).saveToDb()
    return get_filters(match)


def get_columns(match):
    columns = UserColumnSettings.fromDb(g.api_user.user_id) or UserColumnSettings(g.api_user.user_id)
    return ok(columns.toUserColumnSettings())


def set_columns(match):
    fields = api_admin.body_fields(('column_settings',))
    changes = fields.get('column_settings')
    if not isinstance(changes, dict):
        abort(400, 'column_settings must be an object.')
    values = get_columns(match)['data']
    defaults = UserColumnSettings._default_settings
    for table, config in changes.items():
        if table not in defaults or not isinstance(config, dict) or set(config) - {'active', 'available'}:
            abort(400, 'Unknown table or column setting.')
        active = config.get('active')
        known = set(defaults[table]['active'])
        if not isinstance(active, list) or not all(isinstance(name, str) and name in known for name in active) or len(active) != len(set(active)):
            abort(400, 'Active columns must be a list of distinct, supported names.')
        available = config.get('available', [])
        if not isinstance(available, list) or not all(isinstance(name, str) and name in known for name in available) or set(available) & set(active):
            abort(400, 'Available columns must be supported names outside the active list.')
        values[table] = {'active': active}
    UserColumnSettings.fromDict(g.api_user.user_id, {'column_settings': values}).saveToDb()
    return get_columns(match)


def reset_columns(match):
    UserColumnSettings(g.api_user.user_id).saveToDb()
    return get_columns(match)
