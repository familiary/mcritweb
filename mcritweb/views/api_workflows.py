"""MCRITweb analysis workflows as data and downloads, sharing the UI algorithms."""

import copy
import inspect
import json
from collections.abc import Mapping
from pathlib import Path

from flask import Response, abort, current_app, g, request, send_file
from mcrit.storage.MatchingResult import MatchingResult

from mcritweb.backend_errors import require_result, with_server_errors
from mcritweb.db import remember_query_filename
from mcritweb.views import api_admin, data, explore
from mcritweb.views.client import get_client
from mcritweb.views.functiondiff import get_combined_dot_graph, get_function_diff
from mcritweb.views.pagination import Pagination
from mcritweb.views.params import parse_integer_query_param
from mcritweb.views.query_promotion import PromotionError, promote
from mcritweb.views.utility import query_upload_path


def ok(value):
    return {'status': 'successful', 'data': value}


def serialize(value):
    if isinstance(value, Mapping):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    if callable(getattr(value, 'toDict', None)):
        return serialize(value.toDict())
    return value


def client():
    return with_server_errors(get_client())


def identifier(value):
    try:
        result = int(value)
    except (ValueError, TypeError):
        abort(400, 'Malformed numeric identifier.')
    if not -(2**63) <= result <= 2**63 - 1:
        abort(400, 'Numeric identifiers must fit in signed 64 bits.')
    return result


def job_result(job_id):
    backend = client()
    job = require_result(with_server_errors(backend).getJobData(job_id.lower()), 'the job')
    if not isinstance(job.job_id, str) or not data.JOB_ID_PATTERN.fullmatch(job.job_id) or job.job_id.lower() != job_id.lower():
        abort(502, 'The backend returned a different or invalid job identity.')
    try:
        method = job.method
    except (ValueError, TypeError, AttributeError):
        abort(409, 'The job has unreadable parameters.')
    result = backend.getResultForJob(job.job_id)
    if result is None:
        abort(409, 'The job has no completed result.')
    return job, method, result


def matching(job_id):
    job, method, result = job_result(job_id)
    if method not in ('getMatchesForSample', 'getMatchesForSampleVs', 'getMatchesForSampleVsGroup',
                       'getMatchesForUnmappedBinary', 'getMatchesForMappedBinary', 'getMatchesForSmdaReport'):
        abort(400, 'This operation requires a matching result.')
    try:
        return job, MatchingResult.fromDict(copy.deepcopy(result))
    except (ValueError, KeyError, TypeError, AttributeError):
        abort(502, 'The backend returned an unreadable matching report.')


def filtered_result(match):
    job, result = matching(match['job_id'])
    result = data.result_matches_for_sample_or_query(job, result, as_api=True)
    samples = result.filtered_sample_matches
    functions = result.filtered_function_matches
    page = Pagination(request, len(functions), limit=100, query_param='funp', limit_param='funl')
    sample_page = Pagination(request, len(samples), limit=10, query_param='samp', limit_param='sampl')
    return ok({'job_id': job.job_id, 'sample': result.reference_sample_entry.toDict(), 'filters': result.filter_values,
               'samples': [sample.toDict() for sample in samples[sample_page.start_index:sample_page.start_index + sample_page.limit]],
               'functions': [function.toDict() for function in functions[page.start_index:page.start_index + page.limit]],
               'num_samples': len(samples), 'num_functions': len(functions), 'function_start': page.start_index, 'sample_start': sample_page.start_index})


def linkhunt(match):
    job, result = matching(match['job_id'])
    clusters, links, filters = data.linkhunt_for_sample_or_query(job, result, as_api=True)
    cluster_page = Pagination(request, len(clusters), limit=10, query_param='clup', limit_param='clul')
    link_page = Pagination(request, len(links), limit=100, query_param='funp', limit_param='funl')
    def link_data(link):
        return dict(link.toDict(), matched_family=link.matched_family, matched_link_score=link.matched_link_score,
                    matched_unique=link.matched_unique, matched_offset=link.matched_offset)
    selected_clusters = []
    for cluster in clusters[cluster_page.start_index:cluster_page.start_index + cluster_page.limit]:
        selected_clusters.append({key: [link_data(link) for link in value] if key == 'links' else serialize(value) for key, value in cluster.items()})
    return ok({'job_id': job.job_id, 'filters': filters, 'clusters': selected_clusters,
               'links': [link_data(link) for link in links[link_page.start_index:link_page.start_index + link_page.limit]],
               'num_clusters': len(clusters), 'num_links': len(links)})


def yara(match):
    job, method, result = job_result(match['job_id'])
    if method != 'getUniqueBlocks':
        abort(400, 'This operation requires a unique-blocks result.')
    result = copy.deepcopy(result)
    result['unique_blocks'] = dict(sorted(result['unique_blocks'].items()))
    params = data.parse_yara_rule_params(request)
    rule, cover = data.build_yara_rule(job.job_id, result, params)
    return ok({'job_id': job.job_id, 'rule': rule, 'cover': serialize(cover), 'parameters': params, 'statistics': result['statistics']})


def cross_result(match):
    job, method, result = job_result(match['job_id'])
    if method != 'combineMatchesToCross':
        abort(400, 'This operation requires a cross-compare result.')
    return ok(data.result_matches_for_cross(job, result, as_api=True))


def diagram(match):
    job, result = matching(match['job_id'])
    selected = [(name, parse_integer_query_param(request, name)) for name in ('famid', 'samid', 'funid')]
    if any(name in request.args and value is None for name, value in selected):
        abort(400)
    for _, value in selected:
        if value is not None:
            identifier(value)
    family, sample, function = [value for _, value in selected]
    data.create_match_diagram(current_app, job.job_id, result, family, sample, function)
    suffix = next((f'-{name}_{value}' for name, value in selected if value is not None), '')
    path = Path(current_app.instance_path) / 'cache' / 'diagrams' / f'{job.job_id}{suffix}.png'
    return send_file(path, mimetype='image/png')


def dot(match):
    # The UI graph helper is already a data response; unwrap only its browser auth
    # and redirecting reachability decorators, after the table's token role gate.
    response = inspect.unwrap(explore.fetchDotGraph)(identifier(match['function_id']))
    return response if isinstance(response, Response) else Response(response, mimetype='text/plain')


def diff(match):
    left, right = identifier(match['function_id']), identifier(match['other_function_id'])
    if request.args.get('format') == 'dot':
        return Response(get_combined_dot_graph(left, right), mimetype='text/plain')
    return ok(serialize(get_function_diff(left, right)))


def loops(match):
    response = inspect.unwrap(explore.findLoops)()
    if isinstance(response, Response):
        response.mimetype = 'application/json'
        return response
    return Response(response or '[]', mimetype='application/json')


def promote_query(match):
    fields = api_admin.body_fields(('family', 'version')) if request.content_length else {}
    if not all(isinstance(value, str) for value in fields.values()):
        abort(400, 'Promotion metadata must be strings.')
    try:
        sample, new_job, existing = promote(client(), match['job_id'], fields.get('family', ''), fields.get('version', ''))
    except PromotionError as error:
        abort(error.status, str(error))
    return ok({'sample': sample.toDict() if sample is not None else None, 'job_id': new_job, 'already_stored': existing})


def retain_query_input(response, method):
    if method not in ('requestMatchesForUnmappedBinary', 'requestMatchesForMappedBinary', 'requestMatchesForSmdaReport') or response.status_code not in (200, 202):
        return
    try:
        if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get('status') != 'successful':
            return
        job_id = envelope.get('data')
        path = query_upload_path(current_app, job_id) if isinstance(job_id, str) else None
        if path is None:
            return
        content = request.get_data()
        data.write_atomically(current_app, str(Path(path).parent), Path(path).name, lambda target: target.write(content))
        if 'filename' in request.args:
            remember_query_filename(job_id, request.args['filename'])
    except (ValueError, TypeError, AttributeError, RecursionError, OSError) as error:
        current_app.logger.warning('Could not retain the API query input for promotion: %s', error)


def function_analysis(match):
    from mcritweb.views.api import checked_response, handle_raw_response, invoke, matching_options
    backend = get_client(username=g.api_user.username, raw_responses=True)
    response = checked_response(backend.getFunctionById(identifier(match['function_id'])))
    sample_id = response.json()['data'].get('sample_id')
    if type(sample_id) is not int:
        abort(502)
    response = invoke(backend.requestMatchesForSample, sample_id, **matching_options())
    return handle_raw_response(response)


def select_hashes(match):
    fields = api_admin.body_fields(('sha256',))
    hashes = fields.get('sha256')
    if isinstance(hashes, str):
        hashes = hashes.splitlines()
    if not isinstance(hashes, list) or len(hashes) > 250 or not all(isinstance(value, str) for value in hashes):
        abort(400, 'Supply at most 250 SHA-256 lines.')
    import re
    backend = client()
    selected, missing, invalid = {}, [], []
    for value in dict.fromkeys(hashes):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-f0-9]{64}', value):
            invalid.append(value)
            continue
        sample = backend.getSampleBySha256(value)
        if sample is None:
            missing.append(value)
        else:
            selected[sample.sample_id] = sample
    return ok({'samples': [sample.toDict() for sample in selected.values()], 'sample_ids': sorted(selected), 'missing': missing, 'invalid': invalid})


def specific_export(match):
    from mcritweb.views.api import handle_raw_response
    backend = get_client(username=g.api_user.username, raw_responses=True)
    if match['kind'] == 'sample':
        ids = [int(match['entry_id'])]
    else:
        normal = client()
        if match['kind'] == 'family':
            samples = require_result(with_server_errors(normal).getSamplesByFamilyId(int(match['entry_id'])), 'the family samples')
            ids = [sample.sample_id for sample in (samples.values() if isinstance(samples, Mapping) else samples)]
        else:
            job, method, result = job_result(match['entry_id'])
            if method == 'combineMatchesToCross':
                ids = [int(value) for value in next(iter(result.values()))['clustered_sequence']]
            else:
                _, matching_result = matching(job.job_id)
                ids = sorted({matching_result.reference_sample_entry.sample_id, *(sample.sample_id for sample in matching_result.sample_matches)})
    if not ids:
        abort(400, 'There are no samples to export.')
    return handle_raw_response(backend.getExportData(ids))


def delete_selection(match):
    fields = api_admin.body_fields(('state', 'method', 'job_ids'))
    options = {}
    if 'state' in fields:
        if fields['state'] not in ('queued', 'in_progress', 'finished', 'failed', 'terminated'):
            abort(400)
        options['state'] = fields['state']
    if 'method' in fields:
        if not isinstance(fields['method'], str) or not fields['method']:
            abort(400)
        options['method'] = fields['method']
    if 'job_ids' in fields:
        ids = fields['job_ids']
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or not all(isinstance(value, str) and data.JOB_ID_PATTERN.fullmatch(value) for value in ids):
            abort(400)
        options['job_ids'] = sorted({value.lower() for value in ids})
    if not options:
        abort(400, 'Choose a job state, method, or explicit job ids.')
    backend = client()
    jobs = require_result(with_server_errors(backend).getQueueData(limit=10001, **options), 'the selected jobs')
    if len(jobs) > 10000:
        abort(413, 'At most 10000 jobs may be deleted in one selection.')
    deleted = []
    for job in jobs:
        require_result(with_server_errors(backend).deleteJob(job.job_id), 'the deleted job')
        deleted.append(job.job_id)
    return ok({'deleted': deleted, 'num_deleted': len(deleted)})


def download(match):
    job, _, result = job_result(match['job_id'])
    return Response(json.dumps(result), mimetype='application/json', headers={'Content-Disposition': f'attachment; filename="result-{job.job_id}.json"'})


def status(match):
    job = require_result(with_server_errors(client()).getJobData(match['job_id'].lower()), 'the job')
    return ok(data.job_overview_state(job))


def upload_metadata(match):
    # This UI helper already validates malformed JSON and returns data, not HTML.
    if request.content_length is None or request.content_length > 1024 * 1024:
        abort(413)
    response = current_app.make_response(inspect.unwrap(data.request_filename_info)())
    response.mimetype = 'application/json'
    return response


def capabilities(match):
    from mcritweb.views.api import ROUTES, route_available
    levels = {'user': 0, 'visitor': 1, 'contributor': 2, 'admin': 3}
    level = {'pending': 0, 'visitor': 1, 'contributor': 2, 'admin': 3}[g.api_user.role]
    return ok({'role': g.api_user.role, 'mcritweb_version': current_app.config['MCRITWEB_VERSION'],
               'routes': [{'path': route.path, 'method': route.method, 'role': route.role, 'available': route_available(route)}
                          for route in ROUTES if levels[route.role] <= level]})
