import json
import re

from flask import Blueprint, Response, abort, current_app, g, request
from smda.common.SmdaReport import SmdaReport

from mcritweb.views.authentication import token_required
from mcritweb.views.client import get_client
from mcritweb.views.utility import get_username, mcrit_server_required

bp = Blueprint('api', __name__, url_prefix='/api')

# Paths that add to the corpus rather than read from it or queue a job against it.
# The web UI puts the same operation behind contributor_required (data.submit), and
# a token must not be the cheaper way in. Everything else the router dispatches is a
# read or a job submission, which the UI grants to visitors.
CONTRIBUTOR_ONLY = [
    (re.compile("samples$"), "POST"),   # addReport
]


def requires_contributor(api_path, method):
    return any(pattern.match(api_path) and method == verb for pattern, verb in CONTRIBUTOR_ONLY)

def nullable_int(x):
    try:
        casted = int(x)
        return casted
    except Exception:
        raise ValueError("Can't cast this to int")

def stringified_bool(x):
    if not isinstance(x, str):
        return x
    lowered = x.lower()
    if lowered in ['true', '1']:
        return True
    elif lowered in ['false', '0']:
        return False

def handle_raw_response(response):
    if response.status_code in [200, 202]:
        return Response(response=json.dumps(response.json()), status=response.status_code)
    return Response(status=response.status_code)


@bp.route('/<path:api_path>', methods=['GET','POST'])
@token_required
@mcrit_server_required
def api_router(api_path):
    api_path = api_path.rstrip("/")
    if requires_contributor(api_path, request.method) and g.api_user.role not in ('contributor', 'admin'):
        abort(403)
    username = get_username(request)
    client = get_client(username=username, raw_responses=True)
    current_app.logger.debug("api_router - %s - %s", api_path, username)
    if re.match(r"status$", api_path):
        return handle_raw_response(client.getStatus())
    # getFunctionsBySampleId
    elif re_match := re.match(r"samples/(?P<sample_id>\d+)/functions$", api_path):
        sample_id = int(re_match.group("sample_id"))
        return handle_raw_response(client.getFunctionsBySampleId(sample_id))
    # getSampleById, isSampleId
    elif re_match := re.match(r"samples/(?P<sample_id>\d+)$", api_path):
        sample_id = int(re_match.group("sample_id"))
        return handle_raw_response(client.getSampleById(sample_id))
    # getSampleBySha256
    elif re_match := re.match(r"samples/sha256/(?P<sample_sha256>[0-9a-fA-F]{64})$", api_path):
        sample_sha256 = re_match.group("sample_sha256")
        return handle_raw_response(client.getSampleBySha256(sample_sha256))
    # getSamples
    elif re_match := re.match(r"samples$", api_path):
        if request.method == "GET":
            forward_start = 0
            forward_limit = 0
            try:
                forward_start = int(request.args.get("start", 0))
                forward_limit = int(request.args.get("limit", 0))
            except Exception:
                pass
            return handle_raw_response(client.getSamples(forward_start, forward_limit))
        elif request.method == 'POST':
            smda_report_body = request.get_json(force=True)
            smda_report = SmdaReport.fromDict(smda_report_body)
            return handle_raw_response(client.addReport(smda_report))
    # getFamily, isFamilyId
    elif re_match := re.match(r"families/(?P<family_id>\d+)$", api_path):
        family_id = int(re_match.group("family_id"))
        forward_with_samples = request.args.get("with_samples", "").lower() in ["1", "true"]
        return handle_raw_response(client.getFamily(family_id, with_samples=forward_with_samples))
    # getFamilies
    elif re_match := re.match(r"families$", api_path):
        return handle_raw_response(client.getFamilies())
    # getFunctionById, isFunctionId
    elif re_match := re.match(r"functions/(?P<function_id>\d+)$", api_path):
        function_id = int(re_match.group("function_id"))
        forward_with_xcfg = request.args.get("with_xcfg", "").lower() in ["1", "true"]
        return handle_raw_response(client.getFunctionById(function_id, with_xcfg=forward_with_xcfg))
    # getFunctions
    elif re_match := re.match(r"functions$", api_path):
        if request.method == "GET":
            forward_start = 0
            forward_limit = 0
            try:
                forward_start = int(request.args.get("start", 0))
                forward_limit = int(request.args.get("limit", 0))
            except Exception:
                pass
            return handle_raw_response(client.getFunctions(forward_start, forward_limit))
        elif request.method == 'POST':
            if re.match(rb"^\d+(?:[\s]*,[\s]*\d+)*$", request.data):
                forward_with_label_only = request.args.get("with_label_only", "").lower() in ["1", "true"]
                target_function_ids = [int(function_id) for function_id in request.data.split(b",")]
                return handle_raw_response(client.getFunctionsByIds(target_function_ids, with_label_only=forward_with_label_only))
            return handle_raw_response(client.getFunctionsByIds([], with_label_only=forward_with_label_only))
    # getSamplesByIds, getFamiliesByIds: a comma-separated id list as the body, as for
    # functions; sample ids may be negative, for query samples
    elif re_match := re.match(r"(?P<collection>samples|families)/ids$", api_path):
        if request.method != "POST":
            return Response(status=405)
        id_list = request.get_data()
        id_pattern = rb"^-?\d+(?:[\s]*,[\s]*-?\d+)*$" if re_match.group("collection") == "samples" else rb"^\d+(?:[\s]*,[\s]*\d+)*$"
        if not re.match(id_pattern, id_list):
            # what the backend answers a body that isn't an id list; the client sends
            # none it could answer that way
            return Response(status=400)
        entry_ids = [int(entry_id) for entry_id in id_list.split(b",")]
        fetch_many = client.getSamplesByIds if re_match.group("collection") == "samples" else client.getFamiliesByIds
        return handle_raw_response(fetch_many(entry_ids))
    # getMatchesForSmdaFunction
    elif re_match := re.match(r"query/function$", api_path):
        smda_report_body = request.get_json(force=True)
        smda_report = SmdaReport.fromDict(smda_report_body)
        return handle_raw_response(client.getMatchesForSmdaFunction(smda_report))
    # getMatchesForPicHash
    elif re_match := re.match(r"query/pichash/(?P<pichash>[0-9a-fA-F]{16})(?P<as_summary>/summary)?$", api_path):
        pichash = int(re_match.group("pichash"), 16)
        forward_as_summary = True if re_match.group("as_summary") is not None else False
        return handle_raw_response(client.getMatchesForPicHash(pichash, summary=forward_as_summary))
    # getMatchesForPicBlockHash
    elif re_match := re.match(r"query/picblockhash/(?P<picblockhash>[0-9a-fA-F]{16})(?P<as_summary>/summary)?$", api_path):
        picblockhash = int(re_match.group("picblockhash"), 16)
        forward_as_summary = True if re_match.group("as_summary") is not None else False
        return handle_raw_response(client.getMatchesForPicBlockHash(picblockhash, summary=forward_as_summary))
    # getQueueData
    elif re_match := re.match(r"jobs$", api_path):
        forward_start = 0
        forward_limit = 0
        forward_method = None
        forward_filter = None
        forward_state = None
        forward_ascending = False
        try:
            forward_start = int(request.args.get("start", 0))
            forward_limit = int(request.args.get("limit", 0))
            forward_method = request.args.get("method", None)
            forward_filter = request.args.get("filter", None)
            forward_state = request.args.get("state", None)
            forward_ascending = request.args.get("ascending", False, stringified_bool)
        except Exception:
            pass
        return handle_raw_response(
            client.getQueueData(
                start=forward_start, 
                limit=forward_limit, 
                method=forward_method, 
                filter=forward_filter, 
                state=forward_state, 
                ascending=forward_ascending
            )
        )
    # getJobData, getResultForJob
    elif re_match := re.match(r"jobs/(?P<job_id>[0-9a-fA-F]+)(?P<result_for_job>/result)?$", api_path):
        job_id = re_match.group("job_id")
        forward_result = True if re_match.group("result_for_job") is not None else False
        compact = request.args.get("compact", default=False, type=stringified_bool)
        if forward_result:
            return handle_raw_response(client.getResultForJob(job_id, compact=compact))
        else:
            return handle_raw_response(client.getJobData(job_id))
    # getResult, getJobForResult
    elif re_match := re.match(r"results/(?P<result_id>[0-9a-fA-F]+)(?P<job_for_result>/job)?$", api_path):
        result_id = re_match.group("result_id")
        forward_job = True if re_match.group("job_for_result") is not None else False
        if forward_job:
            return handle_raw_response(client.getJobForResult(result_id))
        else:
            return handle_raw_response(client.getResult(result_id))
    # requestMatchesForSample, requestMatchesForSampleVs
    elif re_match := re.match(r"matches/sample/(?P<sample_id>\d+)(/(?P<other_sample_id>\d+))?$", api_path):
        sample_id = re_match.group("sample_id")
        other_sample_id = re_match.group("other_sample_id")
        if other_sample_id is not None:
            return handle_raw_response(client.requestMatchesForSampleVs(sample_id, other_sample_id))
        else:
            return handle_raw_response(client.requestMatchesForSample(sample_id))
    # getMatchFunctionVs
    elif re_match := re.match(r"matches/function/(?P<function_id>\d+)(/(?P<other_function_id>\d+))?$", api_path):
        function_id = re_match.group("function_id")
        other_function_id = re_match.group("other_function_id")
        return handle_raw_response(client.getMatchFunctionVs(function_id, other_function_id))
    # getVersion
    elif re_match := re.match(r"version$", api_path):
        return handle_raw_response(client.getVersion())
    # requestMatchesForMappedBinary, requestMatchesForUnmappedBinary
    elif re_match := re.match(r"query/binary(/mapped/\d+)?$", api_path):
        binary = request.get_data()
        request_args = request.args
        minhash_threshold = request_args.get("minhash_threshold", default=None, type=nullable_int)
        pichash_size = request_args.get("pichash_size", default=None, type=nullable_int)
        band_matches_required = request_args.get("band_matches_required", default=None, type=nullable_int)
        force_recalculation = request_args.get("force_recalculation", default=False, type=stringified_bool)
        # never disassembly in the server for this as we otherwise can't distinguish the type of matching server-side
        disassemble_locally = False
        if re_match := re.match(r"query/binary/mapped/(?P<base_addr>\d+)$", api_path):
            base_address = re_match.group("base_addr")
            return handle_raw_response(
                client.requestMatchesForMappedBinary(
                    binary=binary,
                    base_address=base_address,
                    minhash_threshold=minhash_threshold,
                    pichash_size=pichash_size,
                    band_matches_required=band_matches_required,
                    disassemble_locally=disassemble_locally,
                    force_recalculation=force_recalculation
                )
            )
        else:
            return handle_raw_response(
                client.requestMatchesForUnmappedBinary(
                    binary=binary,
                    minhash_threshold=minhash_threshold,
                    pichash_size=pichash_size,
                    band_matches_required=band_matches_required,
                    disassemble_locally=disassemble_locally,
                    force_recalculation=force_recalculation
                )
            )
    return Response(status=501)
