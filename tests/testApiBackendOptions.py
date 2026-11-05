"""Backend operations and controls omitted by the old fixed API router."""

import copy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import testApiParity
from fixtureData import load
from mcrit.client.McritClient import McritClient
from testApiParity import ANSWER, JOB, headers
from testPromoteQuery import NEW_JOB_ID, register_query

wire = testApiParity.wire


def test_python_clients_wire_threshold_name_is_forwarded(client, make_user, wire):
    make_user('visitor')
    response = client.get('/api/matches/sample/1?minhash_score=35&force_recalculation=false', headers=headers('visitor'))
    assert response.status_code == 200
    query = parse_qs(urlsplit(wire.calls[-1].url).query)
    assert query['minhash_score'] == ['35']
    assert query['force_recalculation'] == ['False']
    assert client.get('/api/matches/sample/1?minhash_score=35&minhash_threshold=50', headers=headers('visitor')).status_code == 400


@pytest.mark.parametrize('path', ['matches/sample/1', 'query/binary', 'query/binary/mapped/4194304'])
def test_new_matching_options_are_forwarded_without_dropping_them(client, make_user, wire, path):
    make_user('visitor')
    method = 'GET' if path.startswith('matches') else 'POST'
    response = client.open('/api/' + path, method=method, headers=headers('visitor'), data=b'MZ' if method == 'POST' else None,
                           query_string={'band_df_cutoff': 20, 'shortlist_size': 12, 'preset': 'balanced'})
    call = McritClient.requestMatchesForSample
    if 'shortlist_size' not in inspect.signature(call).parameters:
        assert response.status_code == 501
        return
    assert response.status_code == 200
    query = parse_qs(urlsplit(wire.calls[-1].url).query)
    assert {name: query[name] for name in ('band_df_cutoff', 'shortlist_size', 'preset')} == {'band_df_cutoff': ['20'], 'shortlist_size': ['12'], 'preset': ['balanced']}


@pytest.mark.parametrize('query', ['band_df_cutoff=-1', 'shortlist_size=bad', 'force_recalculation=maybe', 'preset=bad%26query=1'])
def test_malformed_matching_controls_do_not_submit_jobs(client, make_user, wire, query):
    make_user('visitor')
    assert client.get('/api/matches/sample/1?' + query, headers=headers('visitor')).status_code == 400
    assert wire.calls == []


def test_family_actor_updates_are_typed_and_preserve_an_empty_clear(client, make_user, wire):
    make_user('contributor')
    response = client.put('/api/families/1', headers=headers(), json={'actors': ['group one', 'group.two']})
    if 'actors' not in inspect.signature(McritClient.modifyFamily).parameters:
        assert response.status_code == 501
        return
    assert response.status_code == 200
    assert json.loads(wire.calls[-1].body)['actors'] == ['group one', 'group.two']
    assert client.put('/api/families/1', headers=headers(), json={'actors': []}).status_code == 200
    assert json.loads(wire.calls[-1].body)['actors'] == []
    before = len(wire.calls)
    assert client.put('/api/families/1', headers=headers(), json={'actors': ['bad\nname']}).status_code == 400
    assert len(wire.calls) == before


@pytest.mark.parametrize('query', ['created_before=not-a-date', 'created_before=2026-02-31', 'finished_before=2026-01-01Tbad', 'method='])
def test_invalid_queue_cleanup_selectors_never_become_an_unfiltered_delete(client, make_user, wire, query):
    make_user('contributor')
    assert client.delete('/api/jobs?' + query, headers=headers()).status_code == 400
    assert wire.calls == []


def test_whole_queue_cleanup_requires_explicit_confirmation(client, make_user, wire):
    make_user('contributor')
    assert client.delete('/api/jobs', headers=headers()).status_code == 400
    assert wire.calls == []
    assert client.delete('/api/jobs?confirm=DELETE', headers=headers()).status_code == 200
    assert wire.calls[-1].method == 'DELETE'
    assert client.delete('/api/jobs?created_before=2026-01-02&method=getMatchesForSample', headers=headers()).status_code == 200
    query = parse_qs(urlsplit(wire.calls[-1].url).query)
    assert query['created_before'] == ['2026-01-02T00:00:00']
    assert query['method'] == ['getMatchesForSample']


def test_queue_counts_forward_the_same_selectors_without_pagination(client, make_user, wire):
    make_user('visitor')
    response = client.get('/api/jobs/count?sample_ids=1,2&job_ids=' + JOB + '&username=another&state=finished', headers=headers('visitor'))
    if not hasattr(McritClient, 'getQueueCount'):
        assert response.status_code == 501
        return
    assert response.status_code == 200
    query = parse_qs(urlsplit(wire.calls[-1].url).query)
    assert query['sample_ids'] == ['1,2'] and query['job_ids'] == [JOB]
    assert query['username'] == ['another'] and query['state'] == ['finished']
    assert wire.calls[-1].headers['username'] == 'visitoruser'


def test_binary_downloads_preserve_errors_without_forwarding_html(client, make_user, wire):
    make_user('visitor')
    if not hasattr(McritClient, 'getSampleBinary'):
        assert client.get('/api/samples/1/binary', headers=headers('visitor')).status_code == 501
        return
    wire.answers['GET', '/samples/1/binary'] = 403, ANSWER
    response = client.get('/api/samples/1/binary', headers=headers('visitor'))
    assert response.status_code == 403 and response.data == ANSWER
    wire.answers['GET', '/samples/1/binary'] = 200, b'<html>unexpected</html>'
    assert client.get('/api/samples/1/binary', headers=headers('visitor')).status_code == 502


def test_unique_blocks_support_optional_cover_controls_and_negative_sets(client, make_user, wire):
    make_user('visitor')
    supported = 'exclude_picblockhashes' in inspect.signature(McritClient.requestUniqueBlocksForSamples).parameters
    response = client.post('/api/uniqueblocks/samples/1?gentle_length_score=true&max_blocks_per_function=2&demote_generic_blocks=false',
                           headers=headers('visitor'), json={'exclude_picblockhashes': ['0xff', 12, 12]})
    assert response.status_code == (200 if supported else 501)
    if supported:
        body = json.loads(wire.calls[-1].body)
        assert body['exclude_picblockhashes'] == [12, 255]
        query = parse_qs(urlsplit(wire.calls[-1].url).query)
        assert query['max_blocks_per_function'] == ['2']
        assert [value.lower() for value in query['gentle_length_score']] == ['true']


@pytest.mark.parametrize('body', [{'exclude_picblockhashes': [True]}, {'exclude_picblockhashes': [-1]}, {'exclude_picblockhashes': [2**64]}])
def test_bad_negative_sets_are_rejected_without_submission(client, make_user, wire, body):
    make_user('visitor')
    response = client.post('/api/uniqueblocks/samples/1', headers=headers('visitor'), json=body)
    supported = 'exclude_picblockhashes' in inspect.signature(McritClient.requestUniqueBlocksForSamples).parameters
    assert response.status_code == (400 if supported else 501)
    assert not any(call.method == 'POST' for call in wire.calls)


def test_api_query_submission_can_be_promoted_without_uploading_it_again(app, client, make_user, wire):
    make_user('contributor')
    content = b'MZquery-input-bytes'
    backend = SimpleNamespace(_jobs={})
    digest = register_query(backend, digest=hashlib.sha256(content).hexdigest(), job_id=JOB)
    document, report = backend._jobs[JOB]
    wire.answers['POST', '/query/binary'] = 200, json.dumps({'status': 'successful', 'data': JOB}).encode()
    wire.answers['GET', f'/jobs/{JOB}'] = 200, json.dumps({'status': 'successful', 'data': document}).encode()
    wire.answers['GET', f'/jobs/{JOB}/result'] = 200, json.dumps({'status': 'successful', 'data': report}).encode()
    wire.answers['GET', '/samples/sha256/' + digest] = 404, b'{"status":"failed","data":null}'
    wire.answers['POST', '/samples/binary'] = 202, json.dumps({'status': 'successful', 'data': NEW_JOB_ID}).encode()
    response = client.post('/api/query/binary?filename=unseen.exe', headers=headers(), data=content)
    assert response.status_code == 200
    assert (Path(app.instance_path) / 'temp' / 'uploads' / JOB).read_bytes() == content
    response = client.post(f'/api/jobs/{JOB}/promote', headers=headers(), json={'family': 'api_family', 'version': '2'})
    assert response.status_code == 200, response.data
    assert response.json['data']['job_id'] == NEW_JOB_ID
    submitted = [call for call in wire.calls if call.method == 'POST' and urlsplit(call.url).path == '/samples/binary']
    assert len(submitted) == 1 and submitted[0].body == content
    assert parse_qs(urlsplit(submitted[0].url).query)['family'] == ['api_family']


@pytest.mark.parametrize('owner,expected', [('visitoruser', 200), ('another_user', 403), (None, 403)])
def test_query_reruns_cannot_bypass_a_roles_upload_cap(app, client, make_user, owner, expected):
    make_user('visitor')
    document = copy.deepcopy(load('matches_for_query.job'))
    document['_id'] = JOB
    document['username'] = owner
    submitted = []
    def raw(payload):
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'application/json'
        response._content = json.dumps({'status': 'successful', 'data': payload}).encode()
        return response
    class Rerunner:
        def getJobData(self, job_id):
            return raw(document)
        def requestJobRerun(self, job_id):
            submitted.append(job_id)
            return raw(NEW_JOB_ID)
    app.config['MCRIT_CLIENT_FACTORY'] = lambda **kwargs: Rerunner()
    response = client.post(f'/api/jobs/{JOB}/rerun', headers=headers('visitor'))
    assert response.status_code == expected
    assert submitted == ([JOB] if expected == 200 else [])


def test_tag_capability_and_contributor_boundary(client, make_user, wire):
    make_user('visitor')
    assert client.post('/api/samples/1/tags', headers=headers('visitor'), json={'tags': ['reviewed']}).status_code == 403
    make_user('contributor')
    response = client.post('/api/samples/1/tags', headers=headers(), json={'tags': [' Reviewed ', 'reviewed', 'source:vt']})
    assert response.status_code == (200 if hasattr(McritClient, 'addTags') else 501)
    if response.status_code == 200:
        assert json.loads(wire.calls[-1].body) == {'tags': ['reviewed', 'source:vt']}
        before = len(wire.calls)
        assert client.post('/api/samples/1/tags', headers=headers(), json={'tags': ['x'] * 101}).status_code == 400
        assert len(wire.calls) == before
