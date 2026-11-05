import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Callable
from urllib.parse import quote

import requests
from flask import Blueprint, Response, abort, current_app, g, request
from mcrit.client.McritClient import McritClient
from mcrit.queue.LocalQueue import Job
from werkzeug.exceptions import HTTPException

from mcritweb import backend_errors
from mcritweb.views import api_accounts, api_admin, api_workflows
from mcritweb.views.admin_actions import clear_reset_state
from mcritweb.views.analyze import MAX_SELECTED_SAMPLES
from mcritweb.views.authentication import token_required
from mcritweb.views.client import get_client
from mcritweb.views.params import parse_base_addr, parse_integer_query_param
from mcritweb.views.utility import read_smda_report

bp = Blueprint('api', __name__, url_prefix='/api')
ROLE_PERMISSIONS = {
    'user': ('pending', 'visitor', 'contributor', 'admin'),
    'visitor': ('visitor', 'contributor', 'admin'),
    'contributor': ('contributor', 'admin'),
    'admin': ('admin',),
}


@bp.after_request
def private_api_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.errorhandler(HTTPException)
def api_error(error):
    if error.code is None:
        # abort(Response(...)) preserves the backend's status and JSON bytes.
        return error.get_response()
    return {'status': 'failed', 'data': {'message': error.description}}, error.code


@bp.errorhandler(backend_errors.McritServerError)
def api_backend_failed(error):
    current_app.logger.warning('MCRIT backend failed an API workflow: %s', error)
    return {'status': 'failed', 'data': {'message': 'The MCRIT backend failed the request.'}}, 502


@bp.errorhandler(backend_errors.NoResultFromBackend)
def api_no_result(error):
    return {'status': 'failed', 'data': {'message': str(error)}}, 404


@bp.errorhandler(requests.RequestException)
def backend_unreachable(error):
    """A backend call that never completed, answered as a gateway: 502, or 504 for a
    timeout. Body-less, as handle_raw_response is for a failure without a JSON body: a caller
    of the API cannot read the page the app-wide handler renders (#43)."""
    current_app.logger.warning("MCRIT backend call failed on the API: %r", error)
    return Response(status=backend_errors.status_for(error))

def exceeds_query_upload_limit():
    """The per-role cap `analyze.query` puts on what may be queried (#19). A token
    carries the same authority as its owner's browser, so the API applies it too.

    The declared Content-Length is checked, so an oversized body is refused without
    being read. The server hands the app no more than that many bytes, so a body that
    claims less cannot be larger. A chunked body declares nothing, and only reading it
    would tell its size, so a capped role is refused one. McritClient always declares a
    length: it sends bytes through `requests`. (Reading it up to the cap would need
    `request.max_content_length` set per request, which Flask allows only from 3.1;
    requirements.txt allows 3.0.)"""
    role_limit = current_app.config.get('QUERY_UPLOAD_LIMITS', {}).get(g.api_user.role)
    if role_limit is None:
        return False
    if request.content_length is None:
        return True
    return request.content_length > role_limit

def int_list(x):
    """Comma-separated ints, parsed the way the backend's /jobs parses them: an item
    that isn't one is dropped."""
    ids = []
    for item in x.split(","):
        try:
            ids.append(int(item))
        except ValueError:
            pass
    return ids

def handle_raw_response(response):
    # the backend's JSON goes out as the bytes it sent, error answers included; of its
    # headers only the JSON content type crosses over. A success that is not JSON (a
    # proxy's error page, an empty answer) is a bad gateway, an error keeps its status.
    content_type = response.headers.get("Content-Type", "")
    if content_type.split(";")[0].strip().lower() == "application/json" and response.content:
        return Response(response=response.content, status=response.status_code, content_type=content_type)
    if response.status_code in [200, 202]:
        current_app.logger.warning("api passthrough: backend answered %d with %r, %d bytes", response.status_code, content_type, len(response.content))
        return Response(status=502)
    return Response(status=response.status_code)


# Argument builders return (positional arguments, keyword arguments). The route
# table below owns both dispatch and access policy; adding a path cannot omit its gate.
def no_arguments(match, client):
    return (), {}


def path_arguments(match, client):
    try:
        return tuple(int(value) if value is not None and key.endswith("_id") and key not in ("job_id", "result_id")
                     else value.lower() if isinstance(value, str) and key in ('job_id', 'result_id', 'sample_sha256') else value
                     for key, value in match.groupdict().items()), {}
    except ValueError:
        abort(400)


def boolean(value):
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
        return value.lower() in ("true", "1")
    abort(400, "Expected a boolean (true, false, 1 or 0).")


def query_boolean(name, default=False):
    return boolean(request.args[name]) if name in request.args else default


def query_options(match, client, **defaults):
    args, _ = path_arguments(match, client)
    options = {}
    for name, default in defaults.items():
        if isinstance(default, bool):
            options[name] = query_boolean(name, default)
        elif isinstance(default, int):
            value = parse_integer_query_param(request, name)
            if name in request.args and value is None:
                abort(400, f"{name} must be an integer.")
            options[name] = default if value is None else value
        else:
            options[name] = request.args.get(name, default)
    return args, options


def band_df_cutoff_arguments(match, client):
    """The cutoff to evaluate, checked as the matching options' band_df_cutoff is;
    absent, the backend evaluates its configured one."""
    args, _ = path_arguments(match, client)
    value = parse_integer_query_param(request, "band_df_cutoff")
    if "band_df_cutoff" in request.args and (value is None or not 0 <= value <= 2**63 - 1):
        abort(400, "band_df_cutoff must be a nonnegative 63-bit integer.")
    return args, {"band_df_cutoff": value}


def listing(match, client):
    return (request.args.get("start", 0, type=int), request.args.get("limit", 0, type=int)), {}


def queue_arguments(match, client):
    _, options = query_options(match, client, start=0, limit=0, method=None, filter=None, state=None, ascending=False)
    options["sample_ids"] = int_list(request.args["sample_ids"]) if "sample_ids" in request.args else None
    options["job_ids"] = [item.strip() for item in request.args["job_ids"].split(",") if item.strip()] if "job_ids" in request.args else None
    if 'username' in request.args:
        options['username'] = request.args['username']
    return (), options


def queue_count_arguments(match, client):
    _, options = queue_arguments(match, client)
    for name in ("start", "limit", "ascending"):
        options.pop(name)
    if "username" in request.args:
        options["username"] = request.args["username"]
    return (), options


def delete_queue_arguments(match, client):
    options = {}
    if "method" in request.args:
        if not request.args["method"].strip():
            abort(400, "An empty method cannot select jobs to delete.")
        options["method"] = request.args["method"]
    for name in ("created_before", "finished_before"):
        if name in request.args:
            value = request.args[name]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?", value):
                abort(400, f"{name} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS.")
            try:
                options[name] = datetime.fromisoformat(value)
            except ValueError:
                abort(400)
    if not options and request.args.get("confirm") != "DELETE":
        abort(400, "Deleting the whole queue requires confirm=DELETE.")
    return (), options


def batch_arguments(match, client, collection):
    body = request.get_data()
    pattern = rb"-?\d+(?:\s*,\s*-?\d+)*" if collection == "samples" else rb"\d+(?:\s*,\s*\d+)*"
    if not re.fullmatch(pattern, body):
        # Preserve the original functions endpoint's empty-list fallback.
        if collection == "functions":
            return ([],), {"with_label_only": query_boolean("with_label_only")}
        abort(400)
    try:
        ids = [int(value) for value in body.split(b",")]
    except ValueError:
        abort(400)
    return (ids,), {"with_label_only": query_boolean("with_label_only")} if collection == "functions" else {}


def report_arguments(match, client, query=False, matching=False, function=False):
    if query and exceeds_query_upload_limit():
        abort(403)
    report = read_smda_report(request.get_data())
    if report is None:
        abort(400)
    if not query:
        # data.submit applies this check before adding an SMDA report to the corpus.
        if not isinstance(report.sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", report.sha256):
            abort(400, "The SMDA report must carry a valid sha256.")
        report.sha256 = report.sha256.lower()
    options = matching_options() if matching else {}
    if function and "exclude_self_matches" in request.args:
        options["exclude_self_matches"] = query_boolean("exclude_self_matches")
    return (report,), options


def update_arguments(match, client, fields):
    options = api_admin.body_fields(fields)
    for name, value in options.items():
        if name == "is_library":
            options[name] = boolean(value)
        elif name == "actors":
            if isinstance(value, str):
                value = [actor.strip() for actor in value.split(",") if actor.strip()]
            if not isinstance(value, list) or len(value) > 64 or not all(isinstance(actor, str) and re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", actor) for actor in value):
                abort(400, "Actors must be a list of up to 64 names of 1-64 characters.")
            options[name] = value
        elif not isinstance(value, str):
            abort(400, "Metadata fields must be strings.")
    if "function_name" in options and not re.fullmatch(r"[ -~]{0,256}", options["function_name"]):
        abort(400, "Function names must be up to 256 printable ASCII characters.")
    return path_arguments(match, client)[0], options


def matching_options():
    options = {}
    for name in ("minhash_threshold", "pichash_size", "band_matches_required"):
        value = parse_integer_query_param(request, name)
        if name in request.args and value is None:
            abort(400, f"{name} must be an integer.")
        options[name] = value
    options["force_recalculation"] = query_boolean("force_recalculation")
    # McritClient writes minhash_score on the wire; direct callers also use the
    # Python argument name. Accept both, but never silently choose between them.
    if "minhash_score" in request.args:
        value = parse_integer_query_param(request, "minhash_score")
        if value is None or options["minhash_threshold"] not in (None, value):
            abort(400, "Conflicting or malformed MinHash thresholds.")
        options["minhash_threshold"] = value
    for name in ("band_df_cutoff", "shortlist_size"):
        if name in request.args:
            value = parse_integer_query_param(request, name)
            if value is None or not 0 <= value <= 2**63 - 1:
                abort(400, f"{name} must be a nonnegative 63-bit integer.")
            options[name] = value
    if "preset" in request.args:
        value = request.args["preset"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            abort(400, "Invalid matching preset.")
        options["preset"] = value
    return options


def matching_arguments(match, client):
    return path_arguments(match, client)[0], matching_options()


def single_function_arguments(match, client):
    return (*path_arguments(match, client)[0], None), {}


def binary_query_arguments(match, client):
    if exceeds_query_upload_limit():
        abort(403)
    options = matching_options()
    options.update(binary=request.get_data(), disassemble_locally=False)
    if match.groupdict().get("base_address") is not None:
        try:
            address = int(match["base_address"])
        except ValueError:
            abort(400)
        if address > 0xFFFFFFFFFFFFFFFF:
            abort(400)
        options["base_address"] = address
    return (), options


def binary_submission_arguments(match, client):
    options = {name: quote(request.args[name], safe="") for name in ("filename", "family", "version") if name in request.args}
    options["is_dump"] = query_boolean("is_dump")
    if "base_addr" in request.args:
        address = parse_base_addr(request.args["base_addr"])
        if address is None:
            abort(400, "Expected a hexadecimal base address fitting in 64 bits.")
        options["base_addr"] = address
    if "bitness" in request.args:
        if request.args["bitness"] not in ("32", "64"):
            abort(400, "Bitness must be 32 or 64.")
        options["bitness"] = int(request.args["bitness"])
    if options["is_dump"] and ("base_addr" not in options or "bitness" not in options):
        abort(400, "Memory dumps require base_addr and bitness.")
    binary = request.get_data()
    if not binary:
        abort(400, "Supply a binary as the request body.")
    return (binary,), options


def checked_response(response):
    forwarded = handle_raw_response(response)
    if forwarded.status_code != 200:
        abort(forwarded)
    try:
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("status") != "successful" or envelope.get("data") is None:
            abort(502)
    except (ValueError, RecursionError):
        abort(502)
    return response


def selected_samples(match):
    value = match["sample_ids"]
    if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", value):
        abort(400, "Expected a comma-separated list of sample ids.")
    # A sample set's order and duplicates must not defeat the backend's job cache.
    try:
        return sorted({int(item) for item in value.split(",")})
    except ValueError:
        abort(400)


def unique_options():
    options = {}
    for name, minimum in (("covers_required", 1), ("min_instructions", 0)):
        if name in request.args:
            value = parse_integer_query_param(request, name)
            if value is None or value < minimum:
                abort(400, f"{name} must be an integer of at least {minimum}.")
            options[name] = value
    for name in ("gentle_length_score", "demote_generic_blocks"):
        if name in request.args:
            options[name] = query_boolean(name)
    if "max_blocks_per_function" in request.args:
        value = parse_integer_query_param(request, "max_blocks_per_function")
        if value is None or not 0 <= value <= 2**63 - 1:
            abort(400)
        options["max_blocks_per_function"] = value
    if request.method == "POST":
        if exceeds_query_upload_limit():
            abort(403)
        fields = api_admin.body_fields(("exclude_picblockhashes",), max_bytes=8 * 1024 * 1024)
        hashes = fields.get("exclude_picblockhashes")
        if not isinstance(hashes, list) or len(hashes) > 100000:
            abort(400, "Supply at most 100000 excluded PicBlockHashes.")
        parsed = []
        for value in hashes:
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError
                number = int(value, 16) if isinstance(value, str) and value.lower().startswith("0x") else int(value)
                if not 0 <= number <= 0xffffffffffffffff:
                    raise ValueError
                parsed.append(number)
            except ValueError:
                abort(400, "Excluded PicBlockHashes must be unsigned 64-bit integers.")
        options["exclude_picblockhashes"] = sorted(set(parsed))
    return options


def unique_samples_arguments(match, client):
    ids = selected_samples(match)
    options = unique_options()
    if len(ids) > MAX_SELECTED_SAMPLES:
        abort(400, f"A unique blocks request can name at most {MAX_SELECTED_SAMPLES} samples.")
    for sample_id in ids:
        checked_response(client.isSampleId(sample_id))
    return (ids,), options


def unique_family_arguments(match, client):
    args, _ = path_arguments(match, client)
    options = unique_options()
    response = checked_response(client.getFamily(args[0], with_samples=True))
    try:
        samples = response.json()["data"]["samples"]
    except (ValueError, KeyError, TypeError):
        abort(502)
    if not samples:
        abort(400, "Cannot locate unique blocks for a family without samples.")
    return args, options


def cross_arguments(match, client):
    options = matching_options()
    options["sample_group_only"] = query_boolean("sample_group_only")
    return (selected_samples(match),), options


def search_arguments(match, client):
    return (request.args.get("query", ""),), {
        "cursor": request.args.get("cursor"), "is_ascending": query_boolean("is_ascending", True),
        "sort_by": request.args.get("sort_by"), "limit": request.args.get("limit", type=int),
    }


def hash_arguments(match, client):
    return (int(match["hash"], 16),), {"summary": bool(match["summary"])}


def import_arguments(match, client):
    return (api_admin.json_object(),), {}


def export_arguments(match, client):
    ids = selected_samples(match) if match.groupdict().get("sample_ids") is not None else None
    return (ids,), {"compress_data": query_boolean("compress", True)}


def reset_arguments(match, client):
    body = api_admin.body_fields(("reset_server",))
    if body.get("reset_server") != "RESET":
        abort(400, 'Confirm the server reset with reset_server="RESET".')
    return (), {}


def tags_arguments(match, client):
    entity = {'families': 'family', 'samples': 'sample', 'functions': 'function'}[match['collection']]
    fields = api_admin.body_fields(('tags',))
    tags = fields.get('tags')
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or len(tags) > 100:
        abort(400, 'Supply at most 100 tags.')
    normalized = []
    for tag in tags:
        if not isinstance(tag, str) or not re.fullmatch(r'[a-z0-9][a-z0-9 ._:\-]{0,63}', tag.strip().lower()):
            abort(400, 'Invalid tag.')
        normalized.append(tag.strip().lower())
    return (entity, int(match['entry_id']), list(dict.fromkeys(normalized))), {}


def tags_listing_arguments(match, client):
    entity = request.args.get('entity')
    if entity not in ('family', 'sample', 'function'):
        abort(400, 'Choose entity=family, sample or function.')
    return (entity,), {}


def rerun_arguments(match, client):
    response = checked_response(client.getJobData(match['job_id'].lower()))
    job = Job(response.json()['data'], None)
    try:
        allowed = job.method in ('getMatchesForSample', 'getMatchesForSampleVs', 'getMatchesForSampleVsGroup', 'combineMatchesToCross',
                                 'getMatchesForSmdaReport', 'getMatchesForMappedBinary', 'getMatchesForUnmappedBinary', 'getUniqueBlocks')
        if not allowed or not (job.is_finished or job.is_failed or job.is_terminated):
            abort(409, 'Only a completed, failed or terminated analysis may be rerun.')
        # A rerun resubmits a query's upload without the caller sending it, so for
        # someone else's upload it bypasses the role's upload cap. Whose upload it was
        # cannot be told: a job records only a username, and usernames are renamed and
        # reused (ADR-0007), so whoever holds a name now would pass an ownership test.
        if job.method in ('getMatchesForSmdaReport', 'getMatchesForMappedBinary', 'getMatchesForUnmappedBinary') \
                and current_app.config.get('QUERY_UPLOAD_LIMITS', {}).get(g.api_user.role) is not None:
            abort(403, 'A role with a query upload cap cannot rerun query jobs.')
    except (ValueError, TypeError, AttributeError, KeyError):
        abort(409, 'The job has unreadable parameters.')
    return (job.job_id,), {}


@dataclass(frozen=True)
class ApiRoute:
    path: str
    method: str
    client_method: str
    arguments: Callable = no_arguments
    role: str = field(kw_only=True)
    local_handler: Callable | None = None
    binary_response: bool = False
    required_parameters: tuple = ()

    def __post_init__(self):
        if self.role not in ROLE_PERMISSIONS or self.method not in ('GET', 'POST', 'PUT', 'DELETE'):
            raise ValueError('Every API operation needs a recognized method and role.')
        if bool(self.client_method) == bool(self.local_handler):
            raise ValueError('Every API operation must name exactly one backend method or local handler.')
        re.compile(self.path)


ROUTES = (
    ApiRoute(r"status", "GET", "getStatus", partial(query_options, with_pichash=True), role="visitor"),
    ApiRoute(r"version", "GET", "getVersion", role="visitor"),
    ApiRoute(r"samples", "GET", "getSamples", listing, role="visitor"),
    ApiRoute(r"samples", "POST", "addReport", report_arguments, role="contributor"),
    ApiRoute(r"samples/binary", "POST", "addBinarySample", binary_submission_arguments, role="contributor"),
    ApiRoute(r"samples/ids", "POST", "getSamplesByIds", partial(batch_arguments, collection="samples"), role="visitor"),
    ApiRoute(r"samples/(?P<sample_id>-?\d+)", "GET", "getSampleById", path_arguments, role="visitor"),
    ApiRoute(r"samples/(?P<sample_id>-?\d+)", "PUT", "modifySample",
             partial(update_arguments, fields=("family_name", "version", "component", "is_library")), role="contributor"),
    ApiRoute(r"samples/(?P<sample_id>-?\d+)", "DELETE", "deleteSample", path_arguments, role="contributor"),
    ApiRoute(r"samples/sha256/(?P<sample_sha256>[0-9a-fA-F]{64})", "GET", "getSampleBySha256", path_arguments, role="visitor"),
    ApiRoute(r"samples/(?P<sample_id>-?\d+)/functions", "GET", "getFunctionsBySampleId", path_arguments, role="visitor"),
    # Downloading what a sample was submitted as is the UI's corpus export, which is
    # contributor_required (data.export_view, data.specific_export).
    ApiRoute(r"samples/(?P<sample_id>-?\d+)/smda", "GET", "getSmdaReportForSample", path_arguments, role="contributor"),
    ApiRoute(r"samples/(?P<sample_id>-?\d+)/binary", "GET", "getSampleBinary", path_arguments, binary_response=True, role="contributor"),
    ApiRoute(r"families", "GET", "getFamilies", role="visitor"),
    ApiRoute(r"families/ids", "POST", "getFamiliesByIds", partial(batch_arguments, collection="families"), role="visitor"),
    ApiRoute(r"families/(?P<family_id>\d+)", "GET", "getFamily", partial(query_options, with_samples=False), role="visitor"),
    ApiRoute(r"families/(?P<family_id>\d+)", "PUT", "modifyFamily",
             partial(update_arguments, fields=("family_name", "is_library", "actors")), role="contributor"),
    ApiRoute(r"families/(?P<family_id>\d+)", "DELETE", "deleteFamily", partial(query_options, keep_samples=False), role="contributor"),
    ApiRoute(r"functions", "GET", "getFunctions", listing, role="visitor"),
    ApiRoute(r"functions", "POST", "getFunctionsByIds", partial(batch_arguments, collection="functions"), role="visitor"),
    ApiRoute(r"functions/(?P<function_id>-?\d+)", "GET", "getFunctionById", partial(query_options, with_xcfg=False), role="visitor"),
    ApiRoute(r"functions/(?P<function_id>-?\d+)", "PUT", "modifyFunction",
             partial(update_arguments, fields=("function_name",)), role="contributor"),
    ApiRoute(r"query", "POST", "requestMatchesForSmdaReport", partial(report_arguments, query=True, matching=True), role="visitor"),
    ApiRoute(r"query/function", "POST", "getMatchesForSmdaFunction", partial(report_arguments, query=True, matching=True, function=True), role="visitor"),
    ApiRoute(r"query/pichash/(?P<hash>[0-9a-fA-F]{16})(?P<summary>/summary)?", "GET", "getMatchesForPicHash", hash_arguments, role="visitor"),
    ApiRoute(r"query/picblockhash/(?P<hash>[0-9a-fA-F]{16})(?P<summary>/summary)?", "GET", "getMatchesForPicBlockHash", hash_arguments, role="visitor"),
    ApiRoute(r"query/binary", "POST", "requestMatchesForUnmappedBinary", binary_query_arguments, role="visitor"),
    ApiRoute(r"query/binary/mapped/(?P<base_address>\d+)", "POST", "requestMatchesForMappedBinary", binary_query_arguments, role="visitor"),
    ApiRoute(r"jobs", "GET", "getQueueData", queue_arguments, role="visitor"),
    ApiRoute(r"jobs/stats", "GET", "getQueueStatistics", partial(query_options, with_refresh=False), role="visitor"),
    ApiRoute(r"jobs/count", "GET", "getQueueCount", queue_count_arguments, role="visitor"),
    ApiRoute(r"jobs", "DELETE", "deleteQueueData", delete_queue_arguments, role="contributor"),
    ApiRoute(r"jobs/(?P<job_id>[0-9a-fA-F]+)", "GET", "getJobData", path_arguments, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[0-9a-fA-F]{24})", "DELETE", "deleteJob", path_arguments, role="contributor"),
    ApiRoute(r"jobs/(?P<job_id>[0-9a-fA-F]+)/result", "GET", "getResultForJob", partial(query_options, compact=False), role="visitor"),
    ApiRoute(r"results/(?P<result_id>[0-9a-fA-F]+)", "GET", "getResult", partial(query_options, compact=False), role="visitor"),
    ApiRoute(r"results/(?P<result_id>[0-9a-fA-F]+)/job", "GET", "getJobForResult", path_arguments, role="visitor"),
    ApiRoute(r"matches/sample/(?P<sample_id>\d+)", "GET", "requestMatchesForSample", matching_arguments, role="visitor"),
    ApiRoute(r"matches/sample/(?P<sample_id>\d+)/(?P<other_sample_id>\d+)", "GET", "requestMatchesForSampleVs", matching_arguments, role="visitor"),
    ApiRoute(r"matches/sample/cross/(?P<sample_ids>[^/]+)", "GET", "requestMatchesCross", cross_arguments, role="visitor"),
    ApiRoute(r"matches/function/(?P<function_id>\d+)/(?P<other_function_id>\d+)", "GET", "getMatchFunctionVs", path_arguments, role="visitor"),
    # The single-id form forwards as it always has, to getMatchFunctionVs(id, None).
    ApiRoute(r"matches/function/(?P<function_id>\d+)", "GET", "getMatchFunctionVs", single_function_arguments, role="visitor"),
    ApiRoute(r"uniqueblocks/samples/(?P<sample_ids>[^/]+)", "GET", "requestUniqueBlocksForSamples", unique_samples_arguments, role="visitor"),
    ApiRoute(r"uniqueblocks/family/(?P<family_id>\d+)", "GET", "requestUniqueBlocksForFamily", unique_family_arguments, role="visitor"),
    ApiRoute(r"uniqueblocks/samples/(?P<sample_ids>[^/]+)", "POST", "requestUniqueBlocksForSamples", unique_samples_arguments, required_parameters=('exclude_picblockhashes',), role="visitor"),
    ApiRoute(r"uniqueblocks/family/(?P<family_id>\d+)", "POST", "requestUniqueBlocksForFamily", unique_family_arguments, required_parameters=('exclude_picblockhashes',), role="visitor"),
    *(ApiRoute(f"search/{kind}", "GET", method, search_arguments, role="visitor") for kind, method in
      (("families", "search_families"), ("samples", "search_samples"), ("functions", "search_functions"))),
    ApiRoute(r"import", "POST", "addImportData", import_arguments, role="contributor"),
    ApiRoute(r"export", "GET", "getExportData", export_arguments, role="contributor"),
    ApiRoute(r"export/(?P<sample_ids>[^/]+)", "GET", "getExportData", export_arguments, role="contributor"),
    # Whole-corpus mutations use POST at the proxy, even when the backend's old
    # protocol uses GET. A read request must not reset or re-index a deployment.
    ApiRoute(r"respawn", "POST", "respawn", reset_arguments, role="admin"),
    ApiRoute(r"rebuild_index", "POST", "rebuildIndex", role="admin"),
    ApiRoute(r"rebuild_picblockhash_index", "POST", "rebuildPicBlockHashIndex", role="admin"),
    ApiRoute(r"recalculate_pichashes", "POST", "recalculatePicHashes", role="admin"),
    ApiRoute(r"recalculate_minhashes", "POST", "recalculateMinHashes", role="admin"),
    ApiRoute(r"repair_minhashes", "POST", "repairMinHashes", role="admin"),
    ApiRoute(r"recompute_family_stats", "POST", "recomputeFamilyStats", role="admin"),
    ApiRoute(r"complete_minhashes", "POST", "completeMinhashes", role="admin"),
    ApiRoute(r"rebuild_function_range_index", "POST", "rebuildFunctionRangeIndex", role="admin"),
    ApiRoute(r"rebuild_band_df_index", "POST", "rebuildBandDfIndex", role="admin"),
    ApiRoute(r"band_df_cutoff_coverage", "POST", "requestBandDfCutoffCoverage", band_df_cutoff_arguments, role="admin"),
    ApiRoute(r"delete_orphaned_queue_files", "POST", "deleteOrphanedQueueFiles", partial(query_options, dry_run=False), role="admin"),
    ApiRoute(r"admin/users", "GET", "", role="admin", local_handler=api_admin.list_users),
    ApiRoute(r"admin/users/(?P<user_id>[0-9]{1,18})/role", "PUT", "", role="admin", local_handler=api_admin.user_role),
    ApiRoute(r"admin/users/(?P<user_id>[0-9]{1,18})", "DELETE", "", role="admin", local_handler=api_admin.delete_user),
    ApiRoute(r"admin/users/regenerate_legacy_apitokens", "POST", "", role="admin", local_handler=api_admin.regenerate_legacy_apitokens),
    ApiRoute(r"admin/server", "GET", "", role="admin", local_handler=api_admin.get_server),
    ApiRoute(r"admin/server", "PUT", "", role="admin", local_handler=api_admin.change_server),
    ApiRoute(r"me", "GET", "", role="user", local_handler=api_accounts.profile),
    ApiRoute(r"me/username", "PUT", "", role="user", local_handler=api_accounts.rename_user),
    ApiRoute(r"me/password", "PUT", "", role="user", local_handler=api_accounts.password),
    ApiRoute(r"me/apitoken", "POST", "", role="user", local_handler=api_accounts.rotate_token),
    ApiRoute(r"auth/token", "DELETE", "", role="user", local_handler=api_accounts.logout),
    ApiRoute(r"me/filters", "GET", "", role="user", local_handler=api_accounts.get_filters),
    ApiRoute(r"me/filters", "PUT", "", role="user", local_handler=api_accounts.set_filters),
    ApiRoute(r"me/columns", "GET", "", role="user", local_handler=api_accounts.get_columns),
    ApiRoute(r"me/columns", "PUT", "", role="user", local_handler=api_accounts.set_columns),
    ApiRoute(r"me/columns", "DELETE", "", role="user", local_handler=api_accounts.reset_columns),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/filtered", "GET", "", local_handler=api_workflows.filtered_result, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/linkhunt", "GET", "", local_handler=api_workflows.linkhunt, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/yara", "GET", "", local_handler=api_workflows.yara, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/cross", "GET", "", local_handler=api_workflows.cross_result, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/diagram", "GET", "", local_handler=api_workflows.diagram, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/promote", "POST", "", role="contributor", local_handler=api_workflows.promote_query),
    ApiRoute(r"jobs/delete-selection", "POST", "", role="contributor", local_handler=api_workflows.delete_selection),
    ApiRoute(r"functions/(?P<function_id>-?\d+)/graph", "GET", "", local_handler=api_workflows.dot, role="visitor"),
    ApiRoute(r"functions/(?P<function_id>-?\d+)/analyze", "POST", "", local_handler=api_workflows.function_analysis, role="visitor"),
    ApiRoute(r"functions/(?P<function_id>-?\d+)/diff/(?P<other_function_id>-?\d+)", "GET", "", local_handler=api_workflows.diff, role="visitor"),
    ApiRoute(r"graphs/loops", "POST", "", local_handler=api_workflows.loops, role="visitor"),
    ApiRoute(r"selections/samples/by-sha256", "POST", "", local_handler=api_workflows.select_hashes, role="visitor"),
    ApiRoute(r"exports/(?P<kind>sample|family)/(?P<entry_id>[0-9]{1,18})", "GET", "", role="contributor", local_handler=api_workflows.specific_export),
    ApiRoute(r"(?P<collection>families|samples|functions)/(?P<entry_id>[0-9]{1,18})/tags", "POST", "addTags", tags_arguments, role='contributor'),
    ApiRoute(r"(?P<collection>families|samples|functions)/(?P<entry_id>[0-9]{1,18})/tags", "DELETE", "removeTags", tags_arguments, role='contributor'),
    ApiRoute(r"tags", "GET", "getTags", tags_listing_arguments, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/rerun", "POST", "requestJobRerun", rerun_arguments, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/download", "GET", "", local_handler=api_workflows.download, role="visitor"),
    ApiRoute(r"jobs/(?P<job_id>[a-fA-F0-9]{24})/status", "GET", "", local_handler=api_workflows.status, role="visitor"),
    ApiRoute(r"uploads/metadata", "POST", "", role="contributor", local_handler=api_workflows.upload_metadata),
    ApiRoute(r"capabilities", "GET", "", role="user", local_handler=api_workflows.capabilities),
)

# Every backend operation currently used by a view has an API counterpart.
# Any future exception must include its reason and pass the parity audit.
UI_ONLY_CLIENT_METHODS = {}

ADMIN_UI_PARITY = {
    "users": ("GET", "admin/users"),
    "change_user_role": ("PUT", "admin/users/2/role"),
    "delete_user": ("DELETE", "admin/users/2"),
    "regenerate_legacy_apitokens": ("POST", "admin/users/regenerate_legacy_apitokens"),
    "server": ("GET", "admin/server"),
    "change_server": ("PUT", "admin/server"),
    "reset_server": ("POST", "respawn"),
    "schedule_rebuild_index": ("POST", "rebuild_index"),
    "schedule_recalc_pichashes": ("POST", "recalculate_pichashes"),
    "schedule_recalc_minhashes": ("POST", "recalculate_minhashes"),
    "schedule_repair_minhashes": ("POST", "repair_minhashes"),
    "schedule_recompute_family_stats": ("POST", "recompute_family_stats"),
    "schedule_rebuild_picblockhash_index": ("POST", "rebuild_picblockhash_index"),
}

# These helpers use the same backend operation as their corresponding table entry.
CLIENT_METHOD_ALIASES = {
    "isSampleId": "getSampleById", "isFamilyId": "getFamily", "isFunctionId": "getFunctionById",
    "getSamplesByFamilyId": "getFamily",
    "searchFamilies": "search_families", "searchSamples": "search_samples", "searchFunctions": "search_functions",
    "getJobCount": "getQueueData", "awaitResult": "getResultForJob",
}

CLIENT_CONFIGURATION_METHODS = {
    'setApitoken': 'Client-side credential configuration; the API authenticates its apitoken header.',
    'setUsername': 'Client-side attribution configuration; the API derives identity from the authenticated account.',
}


def route_available(route):
    if route.local_handler is not None:
        return True
    method = getattr(McritClient, route.client_method, None)
    return callable(method) and all(name in inspect.signature(method).parameters for name in route.required_parameters)


def invoke(call, *args, **kwargs):
    owner = getattr(call, '__self__', None)
    if isinstance(owner, McritClient):
        try:
            inspect.signature(call).bind(*args, **kwargs)
        except TypeError:
            abort(501, 'The installed MCRIT client does not support these options.')
    return call(*args, **kwargs)


def dispatch(route, match):
    # The API uses the actual call's HTTP/transport result. The browser's reachability
    # decorator flashes into a session and redirects to HTML, which is not an API error.
    client = get_client(username=g.api_user.username, raw_responses=True)
    call = getattr(client, route.client_method, None)
    if not callable(call):
        abort(501)
    if isinstance(client, McritClient) and not all(name in inspect.signature(call).parameters for name in route.required_parameters):
        abort(501, 'The installed MCRIT client does not support this operation.')
    args, kwargs = route.arguments(match, client)
    response = invoke(call, *args, **kwargs)
    api_workflows.retain_query_input(response, route.client_method)
    if route.binary_response and response.status_code == 200:
        if response.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/octet-stream":
            return Response(status=502)
        return Response(response.content, mimetype="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="sample-{match["sample_id"]}.bin"'})
    if route.client_method == "respawn" and response.status_code == 200:
        try:
            successful = response.json().get("status") == "successful"
        except (ValueError, AttributeError):
            successful = False
        if successful:
            clear_reset_state()
    return handle_raw_response(response)


@bp.route('/<path:api_path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@token_required(allow_pending=True)
def api_router(api_path):
    api_path = api_path.rstrip("/")
    if any(len(request.args.getlist(name)) > 1 for name in request.args):
        abort(400, 'Repeated query parameters are not supported; use comma-separated selectors.')
    candidates = [(route, match) for route in ROUTES if (match := re.fullmatch(route.path, api_path))]
    method = "GET" if request.method == "HEAD" else request.method
    for route, match in candidates:
        if route.method != method:
            continue
        if g.api_user.role not in ROLE_PERMISSIONS[route.role]:
            abort(403)
        if route.local_handler is not None:
            return route.local_handler(match)
        return dispatch(route=route, match=match)
    public_methods = {'POST'} if api_path in ('auth/token', 'auth/register') else set()
    if candidates or public_methods or method in ("PUT", "DELETE"):
        allowed = sorted({route.method for route, _ in candidates} | public_methods)
        if "GET" in allowed:
            allowed.append("HEAD")
        return Response(status=405, headers={"Allow": ", ".join(allowed)})
    return Response(status=501)


@bp.route('/auth/token', methods=['POST'])
def login_api():
    return api_accounts.login()


@bp.route('/auth/register', methods=['POST'])
def register_api():
    return api_accounts.register()
