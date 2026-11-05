"""Self-service account API mirrors settings without using browser cookies."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from mcritweb import db
from mcritweb.db import ServerInfo, UserInfo
from mcritweb.views import api_accounts


def token(role='visitor'):
    return {'apitoken': f'apitoken-{role}'}


def test_password_authentication_returns_only_the_callers_token(client, make_user):
    make_user('visitor')
    response = client.post('/api/auth/token', json={'username': 'visitoruser', 'password': 'password'})
    assert response.status_code == 200
    assert response.json['data']['apitoken'] == 'apitoken-visitor'
    assert 'password' not in response.json['data']
    assert 'Set-Cookie' not in response.headers
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('username,password', [('visitoruser', 'wrong'), ('unknownuser', 'wrong'), ('visitoruser', '')])
def test_failed_credentials_do_not_disclose_accounts(client, make_user, username, password):
    make_user('visitor')
    response = client.post('/api/auth/token', json={'username': username, 'password': password})
    assert response.status_code == 401
    assert response.json['data']['message'] == 'Incorrect username or password.'
    assert 'Set-Cookie' not in response.headers


def test_api_password_guesses_share_the_browser_login_budget(app, client, make_user):
    make_user('visitor')
    for _ in range(db.LOGIN_ATTEMPT_LIMIT):
        assert client.post('/api/auth/token', json={'username': 'visitoruser', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/token', json={'username': 'visitoruser', 'password': 'password'}).status_code == 429
    assert b'Too many failed attempts' in client.post('/login', data={'username': 'visitoruser', 'inputPassword': 'password'}).data


@pytest.mark.parametrize('role', ['pending', 'visitor', 'contributor', 'admin'])
def test_every_signed_in_role_can_manage_its_own_preferences_without_a_cookie(client, make_user, role):
    make_user(role)
    response = client.get('/api/me', headers=token(role))
    assert response.status_code == 200 and response.json['data']['role'] == role
    response = client.put('/api/me/filters', headers=token(role), json={'filter_function_min_score': 75, 'filter_exclude_library': True})
    assert response.status_code == 200
    assert response.json['data']['filter_function_min_score'] == 75
    assert response.json['data']['filter_exclude_library'] is True
    response = client.put('/api/me/columns', headers=token(role), json={'column_settings': {'functions_table': {'active': ['function_name', 'function_id']}}})
    assert response.status_code == 200
    assert response.json['data']['functions_table']['active'] == ['function_name', 'function_id']
    assert 'Set-Cookie' not in response.headers
    assert client.delete('/api/me/columns', headers=token(role)).json['data']['functions_table']['active'][0] == 'function_id'


def test_partial_preferences_preserve_other_tables_and_bounds(client, make_user):
    make_user('visitor')
    original = client.get('/api/me/columns', headers=token()).json['data']
    updated = client.put('/api/me/columns', headers=token(), json={'column_settings': {'functions_table': {'active': []}}}).json['data']
    assert updated['family_table'] == original['family_table']
    assert updated['functions_table']['active'] == []
    client.put('/api/me/filters', headers=token(), json={'filter_function_min_score': 200})
    updated = client.put('/api/me/filters', headers=token(), json={'filter_exclude_pic': True}).json['data']
    assert updated['filter_function_min_score'] == 100
    assert updated['filter_exclude_pic'] is True


@pytest.mark.parametrize('path,payload', [
    ('filters', {'user_id': 1}), ('filters', {'role': 'admin'}),
    ('filters', {'filter_function_min_score': True}), ('filters', {'filter_max_num_families': 2**100}),
    ('columns', {'column_settings': {'unknown_table': {'active': []}}}),
    ('columns', {'column_settings': {'functions_table': {'active': ['password']}}}),
    ('columns', {'column_settings': {'functions_table': {'active': ['function_id', 'function_id']}}}),
    ('columns', {'column_settings': []}),
])
def test_malformed_or_foreign_preferences_cannot_write(app, client, make_user, path, payload):
    user_id = make_user('visitor')
    assert client.put(f'/api/me/{path}', headers=token(), json=payload).status_code == 400
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).role == 'visitor'


def test_a_browser_admin_session_cannot_choose_the_api_account(app, client, as_role, make_user):
    admin_id = as_role('admin')
    visitor_id = make_user('visitor')
    response = client.put('/api/me/filters', headers=token(), json={'filter_function_min_score': 88})
    assert response.status_code == 200
    with app.app_context():
        assert db.UserFilters.fromDb(visitor_id).filter_function_min_score == 88
        assert db.UserFilters.fromDb(admin_id) is None
    assert client.get('/api/me').status_code == 403


def test_pending_tokens_cannot_use_corpus_or_admin_operations(client, make_user):
    make_user('pending')
    assert client.get('/api/me/filters', headers=token('pending')).status_code == 200
    for method, path in [('GET', 'status'), ('POST', 'query'), ('GET', 'jobs'), ('GET', 'admin/server'), ('PUT', 'samples/1')]:
        assert client.open('/api/' + path, method=method, headers=token('pending'), json={}).status_code == 403


def test_rename_requires_the_current_password_and_preserves_the_token(client, make_user):
    make_user('visitor')
    assert client.put('/api/me/username', headers=token(), json={'username': 'renamed_user', 'password': 'wrong'}).status_code == 403
    response = client.put('/api/me/username', headers=token(), json={'username': 'renamed_user', 'password': 'password'})
    assert response.status_code == 200 and response.json['data']['username'] == 'renamed_user'
    assert client.get('/api/me', headers=token()).json['data']['username'] == 'renamed_user'


def test_password_changes_revoke_old_tokens_and_browser_sessions(app, client, make_user):
    user_id = make_user('contributor')
    browser = app.test_client()
    assert browser.post('/login', data={'username': 'contributoruser', 'inputPassword': 'password'}).status_code == 302
    response = client.put('/api/me/password', headers=token('contributor'), json={'current_password': 'password', 'password': 'new-password', 'password_confirm': 'new-password'})
    assert response.status_code == 200
    assert 'Set-Cookie' not in response.headers
    assert client.get('/api/me', headers=token('contributor')).status_code == 403
    assert client.get('/api/me', headers={'apitoken': response.json['data']['apitoken']}).status_code == 200
    assert '/login' in browser.get('/settings').headers['Location']
    with app.app_context():
        assert check_password_hash(UserInfo.fromDb(user_id=user_id).password, 'new-password')


def test_token_rotation_and_logout_revoke_the_previous_credential(client, make_user):
    make_user('visitor')
    response = client.post('/api/me/apitoken', headers=token())
    new_token = response.json['data']['apitoken']
    assert client.get('/api/me', headers=token()).status_code == 403
    assert client.delete('/api/auth/token', headers={'apitoken': new_token}).status_code == 204
    assert client.get('/api/me', headers={'apitoken': new_token}).status_code == 403
    response = client.post('/api/auth/token', json={'username': 'visitoruser', 'password': 'password'})
    assert response.status_code == 200 and response.json['data']['apitoken'] != new_token


def test_password_proof_recovers_from_legacy_token_retirement(app, client, make_user):
    user_id = make_user('visitor')
    with app.app_context():
        user = UserInfo.fromDb(user_id=user_id)
        user.apitoken = 'a' * 32
        user.saveToDb()
    app.config['ACCEPT_LEGACY_APITOKENS'] = False
    response = client.post('/api/auth/token', json={'username': 'visitoruser', 'password': 'password'})
    assert response.status_code == 200
    assert len(response.json['data']['apitoken']) == 64
    assert client.get('/api/me', headers={'apitoken': 'a' * 32}).status_code == 403


def test_registration_and_duplicate_names_have_the_same_pending_response(app, client, make_user):
    make_user('admin')
    payload = {'username': 'new_account', 'password': 'chosen-password', 'password_confirm': 'chosen-password'}
    first = client.post('/api/auth/register', json=payload)
    second = client.post('/api/auth/register', json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json == second.json
    with app.app_context():
        assert UserInfo.fromDb(username='new_account').role == 'pending'
    response = client.post('/api/auth/token', json={'username': 'new_account', 'password': 'chosen-password'})
    assert response.json['data']['role'] == 'pending'


def test_registration_obeys_single_user_mode_and_registration_token(app, client, make_user):
    make_user('admin')
    payload = {'username': 'new_account', 'password': 'password', 'password_confirm': 'password'}
    with app.app_context():
        server = ServerInfo.fromDb()
        server.registration_token = 'secret-registration'
        server.saveToDb()
    assert client.post('/api/auth/register', json=payload).status_code == 400
    assert client.post('/api/auth/register', json={**payload, 'registration_token': 'secret-registration'}).status_code == 202
    with app.app_context():
        server = ServerInfo.fromDb()
        server.operation_mode = 'single'
        server.saveToDb()
    assert client.post('/api/auth/register', json={**payload, 'registration_token': 'secret-registration'}).status_code == 403


def test_first_registration_configures_the_backend_atomically(app, client):
    payload = {'username': 'operator', 'password': 'password', 'password_confirm': 'password', 'mcrit_server_url': 'https://backend.example:8000',
               'operation_mode': 'multi', 'mcrit_server_token': 'SECRET', 'registration_token_new': 'REGISTER-SECRET'}
    response = client.post('/api/auth/register', json=payload)
    assert response.status_code == 201
    assert b'SECRET' not in response.data
    with app.app_context():
        assert UserInfo.fromDb(user_id=1).role == 'admin'
        assert ServerInfo.fromDb().server_token == 'SECRET'


def test_two_first_registrations_can_create_only_one_admin(app, monkeypatch):
    barrier = threading.Barrier(2)
    def hash_together(password):
        barrier.wait(timeout=10)
        return generate_password_hash(password)
    monkeypatch.setattr(api_accounts, 'generate_password_hash', hash_together)
    def register(username):
        return app.test_client().post('/api/auth/register', json={'username': username, 'password': 'password', 'password_confirm': 'password',
                                                                'mcrit_server_url': 'http://backend.example', 'operation_mode': 'multi'}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, ['operator_a', 'operator_b']))
    assert sorted(results) == [201, 409]
    with app.app_context():
        assert len(db.get_all_user_info()) == 1
        assert db.get_all_user_info()[0].role == 'admin'


@pytest.mark.parametrize('path', ['auth/token', 'auth/register', 'me/password', 'me/username'])
def test_credential_bodies_are_bounded_before_parsing(client, make_user, path):
    make_user('visitor')
    method = 'POST' if path.startswith('auth/') else 'PUT'
    response = client.open('/api/' + path, method=method, headers=token(), data=b'x' * 65537, content_type='application/json')
    assert response.status_code == 413


#: What an HTML form on another site can send without a CORS preflight: the three
#: enctypes a <form> supports.
CROSS_SITE_BODIES = [
    pytest.param(lambda fields: {'data': fields}, id='urlencoded'),
    pytest.param(lambda fields: {'data': fields, 'content_type': 'multipart/form-data'}, id='multipart'),
    pytest.param(lambda fields: {'data': json.dumps(fields), 'content_type': 'text/plain'}, id='text-plain'),
]


@pytest.mark.parametrize('encode', CROSS_SITE_BODIES)
def test_first_user_setup_cannot_be_posted_by_a_cross_site_form(app, client, encode):
    """/api is exempt from CSRF because a forged request cannot carry an apitoken, but
    auth/register takes none. On a fresh instance a form on any page could otherwise
    create the admin and point the instance at a backend of the form author's choice."""
    fields = {'username': 'attacker', 'password': 'password', 'password_confirm': 'password',
              'mcrit_server_url': 'http://backend.example', 'operation_mode': 'multi'}
    response = client.post('/api/auth/register', headers={'Origin': 'https://elsewhere.example'}, **encode(fields))
    assert response.status_code == 415 and response.is_json
    with app.app_context():
        assert db.get_all_user_info() == []
        assert db.get_server_info().url != 'http://backend.example'


@pytest.mark.parametrize('encode', CROSS_SITE_BODIES)
def test_password_authentication_cannot_be_posted_by_a_cross_site_form(client, make_user, encode):
    """auth/token takes no token either, so it is held to the same JSON-only rule."""
    make_user('visitor')
    response = client.post('/api/auth/token', headers={'Origin': 'https://elsewhere.example'},
                           **encode({'username': 'visitoruser', 'password': 'password'}))
    assert response.status_code == 415 and response.is_json
    assert b'apitoken-visitor' not in response.data
