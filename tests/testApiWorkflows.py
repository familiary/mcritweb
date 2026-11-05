"""API analysis actions return the same results as the manual UI workflows."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtureData import job_id_of, load
from flask import template_rendered
from mcrit.storage.MatchingResult import MatchingResult

from mcritweb.views import api_workflows


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def token(role='visitor'):
    return {'apitoken': f'apitoken-{role}'}


def capture(app):
    contexts = []
    def record(sender, template, context, **extra):
        contexts.append(context)
    template_rendered.connect(record, app)
    return contexts, record


def test_filtered_api_results_use_the_ui_filters(app, client, as_role):
    as_role('visitor')
    contexts, record = capture(app)
    job = job_id_of('matches_for_sample')
    query = {'filter_function_min_score': '80', 'filter_button_action': 'filter'}
    try:
        assert client.get(f'/data/result/{job}', query_string=query).status_code == 200
    finally:
        template_rendered.disconnect(record, app)
    expected = next(context['matching_result'] for context in contexts if 'matching_result' in context)
    response = client.get(f'/api/jobs/{job}/filtered', headers=token(), query_string=query)
    assert response.status_code == 200, response.data
    assert response.json['data']['num_functions'] == len(expected.filtered_function_matches)
    assert response.json['data']['num_samples'] == len(expected.filtered_sample_matches)
    assert all(function['matches'][3] >= 80 for function in response.json['data']['functions'])
    assert 'Set-Cookie' not in response.headers


def test_default_result_filters_belong_to_the_api_token_owner(client, make_user):
    make_user('visitor')
    job = job_id_of('matches_for_sample')
    assert client.put('/api/me/filters', headers=token(), json={'filter_function_min_score': 99}).status_code == 200
    response = client.get(f'/api/jobs/{job}/filtered', headers=token())
    assert response.status_code == 200
    assert response.json['data']['filters']['filter_function_min_score'] == 99
    assert all(function['matches'][3] >= 99 for function in response.json['data']['functions'])


def test_explicit_false_result_filters_are_not_replaced_by_saved_defaults(client, make_user):
    make_user('visitor')
    client.put('/api/me/filters', headers=token(), json={'filter_exclude_library': True})
    response = client.get(f'/api/jobs/{job_id_of("matches_for_sample")}/filtered?filter_exclude_library=false', headers=token())
    assert response.status_code == 200
    assert response.json['data']['filters']['filter_exclude_library'] is False
    response = client.get(f'/api/jobs/{job_id_of("matches_for_sample")}/linkhunt?filter_strongest_per_family=false', headers=token())
    assert response.status_code == 200
    assert response.json['data']['filters']['filter_strongest_per_family'] is False


def test_linkhunt_api_uses_the_same_clustering_and_filter_parameters(app, client, as_role):
    as_role('visitor')
    job = job_id_of('matches_for_sample')
    contexts, record = capture(app)
    try:
        assert client.get(f'/data/linkhunt/{job}').status_code == 200
    finally:
        template_rendered.disconnect(record, app)
    expected = next(context for context in contexts if 'lhr' in context)
    response = client.get(f'/api/jobs/{job}/linkhunt', headers=token())
    assert response.status_code == 200, response.data
    assert response.json['data']['num_links'] == len(expected['lhr'])
    assert response.json['data']['num_clusters'] == len(expected['lc'])
    assert response.json['data']['filters']['filter_min_score'] == 65


def test_yara_api_uses_the_same_rule_generator(app, client, as_role):
    as_role('visitor')
    job = job_id_of('unique_blocks')
    contexts, record = capture(app)
    try:
        assert client.get(f'/data/result/{job}').status_code == 200
    finally:
        template_rendered.disconnect(record, app)
    expected = next(context for context in contexts if 'yara_rule' in context)
    response = client.get(f'/api/jobs/{job}/yara', headers=token())
    assert response.status_code == 200, response.data
    assert response.json['data']['rule'] == expected['yara_rule']
    assert response.json['data']['parameters'] == expected['yara_params']


def test_cross_compare_api_applies_the_ui_ordering(client, make_user):
    make_user('visitor')
    job = job_id_of('cross_compare')
    response = client.get(f'/api/jobs/{job}/cross?order=sample_id', headers=token())
    assert response.status_code == 200, response.data
    for samples in response.json['data']['samples'].values():
        ids = [sample['sample_id'] for sample in samples]
        assert ids == sorted(ids)
    bad = client.get(f'/api/jobs/{job}/cross?custom=0,0,0', headers=token())
    assert bad.status_code == 409 and bad.is_json


def test_result_and_diagram_downloads_work_without_a_browser_session(client, make_user):
    make_user('visitor')
    job = job_id_of('matches_for_sample')
    report = client.get(f'/api/jobs/{job}/download', headers=token())
    assert report.status_code == 200
    assert report.json == load('matches_for_sample.result')
    assert 'attachment' in report.headers['Content-Disposition']
    image = client.get(f'/api/jobs/{job}/diagram', headers=token())
    assert image.status_code == 200, image.data[:200]
    assert image.mimetype == 'image/png' and image.data.startswith(b'\x89PNG')


def matched_report():
    return MatchingResult.fromDict(load('matches_for_sample.result'))


DIAGRAM_SCOPES = [
    ('famid', lambda report: next(sample.family_id for sample in report.sample_matches)),
    ('samid', lambda report: next(sample.sample_id for sample in report.sample_matches)),
    ('funid', lambda report: next(iter(report.function_id_to_family_ids_matched))),
]


@pytest.mark.parametrize('scope', ['famid', 'samid', 'funid'], ids=['family', 'sample', 'function'])
def test_a_diagram_scope_the_report_did_not_match_is_refused_without_a_file(app, client, make_user, scope):
    """The diagram cache is unbounded, so the browser's diagram route draws only a
    family, sample or function the report matched (#145). The API writes into the
    same cache and must apply the same gate, or every id is a new file there."""
    make_user('visitor')
    job = job_id_of('matches_for_sample')
    response = client.get(f'/api/jobs/{job}/diagram', headers=token(), query_string={scope: 10**12})
    assert response.status_code == 404 and response.is_json
    diagrams = Path(app.instance_path) / 'cache' / 'diagrams'
    assert not diagrams.exists() or list(diagrams.iterdir()) == []


@pytest.mark.parametrize('scope,matched', DIAGRAM_SCOPES, ids=['family', 'sample', 'function'])
def test_a_diagram_scope_the_report_matched_is_rendered(app, client, make_user, scope, matched):
    """The gate above refuses only what the result page could never link to."""
    make_user('visitor')
    job = job_id_of('matches_for_sample')
    value = matched(matched_report())
    response = client.get(f'/api/jobs/{job}/diagram', headers=token(), query_string={scope: value})
    assert response.status_code == 200 and response.data.startswith(b'\x89PNG')
    assert (Path(app.instance_path) / 'cache' / 'diagrams' / f'{job}-{scope}_{value}.png').is_file()


def test_cfg_and_diff_endpoints_return_data(client, make_user, fake_mcrit):
    make_user('visitor')
    ids = [entry.function_id for entry in fake_mcrit._functions.values() if entry.xcfg]
    response = client.get(f'/api/functions/{ids[0]}/graph', headers=token())
    assert response.status_code == 200 and response.mimetype == 'text/plain'

    assert b'digraph' in response.data
    response = client.get(f'/api/functions/{ids[0]}/diff/{ids[1]}', headers=token())
    assert response.status_code == 200 and response.is_json
    assert 'node_colors' in response.json['data']
    response = client.get(f'/api/functions/{ids[0]}/diff/{ids[1]}?format=dot', headers=token())
    assert response.status_code == 200 and response.mimetype == 'text/plain'


def test_upload_metadata_uses_the_ui_probe_without_returning_html(client, make_user):
    make_user('contributor')
    response = client.post('/api/uploads/metadata', headers=token('contributor'), json={'filename': 'sample.exe', 'file_header': ''})
    assert response.status_code == 200 and response.is_json
    assert response.json == {'dump': False}


def test_loop_analysis_has_the_same_resource_limit_as_the_ui(app, client, make_user):
    make_user('visitor')
    response = client.post('/api/graphs/loops', headers=token(), data='digraph { }', content_type='text/plain')
    assert response.status_code == 200 and response.is_json
    limit = app.config['FIND_LOOPS_MAX_BYTES']
    response = client.post('/api/graphs/loops', headers=token(), data=b' ' * (limit + 1))
    assert response.status_code == 200
    assert response.json == []
    assert response.headers['X-Loop-Analysis'].startswith('skipped; max-bytes=')


@pytest.mark.parametrize('method,path', [
    ('GET', f'jobs/{job_id_of("matches_for_sample")}/filtered'),
    ('GET', f'jobs/{job_id_of("matches_for_sample")}/linkhunt'),
    ('GET', f'jobs/{job_id_of("unique_blocks")}/yara'),
    ('GET', f'jobs/{job_id_of("cross_compare")}/cross'),
    ('GET', 'functions/1/graph'), ('GET', 'functions/1/diff/2'),
    ('POST', 'functions/1/analyze'), ('POST', 'graphs/loops'),
    ('POST', 'selections/samples/by-sha256'),
])
def test_pending_accounts_cannot_run_analyses(client, make_user, fake_mcrit, method, path):
    make_user('pending')
    fake_mcrit.calls.clear()
    response = client.open('/api/' + path, method=method, headers=token('pending'), json={})
    assert response.status_code == 403
    assert fake_mcrit.calls == []


def test_incompatible_analysis_jobs_are_reported_as_json(client, make_user):
    make_user('visitor')
    assert client.get(f'/api/jobs/{job_id_of("unique_blocks")}/filtered', headers=token()).status_code == 400
    assert client.get(f'/api/jobs/{job_id_of("matches_for_sample")}/yara', headers=token()).status_code == 400
    response = client.get('/api/jobs/0123456789abcdef01234567/filtered', headers=token())
    assert response.status_code == 404 and response.is_json


def test_backend_job_ids_cannot_name_a_file_outside_the_diagram_cache(client, make_user, fake_mcrit):
    make_user('visitor')
    fake_mcrit.getJobData = lambda job_id: SimpleNamespace(job_id='../../outside')
    response = client.get(f'/api/jobs/{job_id_of("matches_for_sample")}/diagram', headers=token())
    assert response.status_code == 502 and response.is_json


@pytest.mark.parametrize('path,method', [
    ('functions/' + '9' * 5000 + '/graph', 'GET'),
    ('functions/' + '9' * 100 + '/diff/1', 'GET'),
    ('functions/' + '9' * 100 + '/analyze', 'POST'),
    (f'jobs/{job_id_of("matches_for_sample")}/diagram?famid=' + '9' * 200, 'GET'),
])
def test_numeric_ids_cannot_overflow_parsing_or_cache_filenames(client, make_user, path, method):
    make_user('visitor')
    response = client.open('/api/' + path, method=method, headers=token())
    assert response.status_code == 400 and response.is_json


def test_hash_selection_body_is_bounded_before_json_parsing(client, make_user):
    make_user('visitor')
    response = client.post('/api/selections/samples/by-sha256', headers=token(), data=b' ' * 65537, content_type='application/json')
    assert response.status_code == 413


def test_hash_lists_return_the_same_selection_information_without_partial_submission(client, make_user, fake_mcrit):
    make_user('visitor')
    sample = next(iter(fake_mcrit._samples.values()))
    missing = 'ef' * 32
    response = client.post('/api/selections/samples/by-sha256', headers=token(), json={'sha256': [sample.sha256.upper(), missing, 'invalid']})
    assert response.status_code == 200
    assert response.json['data']['sample_ids'] == [sample.sample_id]
    assert response.json['data']['missing'] == [missing]
    assert response.json['data']['invalid'] == ['invalid']
    assert not any(name.startswith('request') for name, _, _ in fake_mcrit.calls)


def test_capabilities_report_available_actions_at_the_callers_role(client, make_user):
    make_user('visitor')
    response = client.get('/api/capabilities', headers=token())
    assert response.status_code == 200
    assert all(route['role'] in ('visitor', 'user') for route in response.json['data']['routes'])
    assert any(route['path'] == 'me/password' for route in response.json['data']['routes'])
    assert any(route['path'] == 'tags' for route in response.json['data']['routes'])


def test_retained_api_query_inputs_are_named_only_by_validated_job_ids(app, client, make_user, monkeypatch):
    # Exercise the storage helper with an actual request context and response; the
    # HTTP transport tests cover dispatch calling it after a successful query.
    import requests
    make_user('visitor')
    body = b'MZquery'
    response = requests.Response()
    response.status_code = 200
    response.headers['Content-Type'] = 'application/json'
    response._content = b'{"status":"successful","data":"0123456789abcdef01234567"}'
    with app.test_request_context('/api/query/binary?filename=query.exe', method='POST', data=body):
        api_workflows.retain_query_input(response, 'requestMatchesForUnmappedBinary')
    assert (Path(app.instance_path) / 'temp' / 'uploads' / '0123456789abcdef01234567').read_bytes() == body
    response._content = b'{"status":"successful","data":"../../outside"}'
    with app.test_request_context('/api/query/binary', method='POST', data=body):
        api_workflows.retain_query_input(response, 'requestMatchesForUnmappedBinary')
    assert not (Path(app.instance_path) / 'outside').exists()
