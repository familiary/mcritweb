import hashlib
import os
import re
from urllib.parse import quote

from flask import Blueprint, Response, current_app, flash, json, redirect, render_template, request, send_from_directory, session, url_for
from mcrit.libs.utility import decode_two_complement
from mcrit.queue.LocalQueue import Job
from mcrit.queue.QueueRemoteCalls import to_binary as canonicalise_queue_json
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.MatchedFunctionEntry import MatchedFunctionEntry
from mcrit.storage.MatchingResult import MatchingResult
from mcrit.storage.SampleEntry import SampleEntry
from mcrit.storage.UniqueBlocksResult import UniqueBlocksResult
from smda.common.SmdaReport import SmdaReport

from mcritweb.db import UserColumnSettings, UserFilters, get_query_filename, utc_now
from mcritweb.views.analyze import query as analyze_query
from mcritweb.views.authentication import contributor_required, visitor_required
from mcritweb.views.client import get_client
from mcritweb.views.cross_compare import get_sample_to_job_id, score_to_color
from mcritweb.views.functiondiff import get_function_diff
from mcritweb.views.MatchReportRenderer import MatchReportRenderer
from mcritweb.views.pagination import Pagination
from mcritweb.views.params import (
    parse_checkbox_query_param,
    parse_integer_list_query_param,
    parse_integer_query_param,
    parse_str_query_param,
    parseBaseAddrFromFilename,
    parseBitnessFromFilename,
)
from mcritweb.views.ScoreColorProvider import ScoreColorProvider
from mcritweb.views.utility import get_session_user_id, mcrit_server_required, query_upload_path

bp = Blueprint('data', __name__, url_prefix='/data')

################################################################
# Helper functions
################################################################

def quote_backend_query_value(value):
    """Percent-encode one value for a query string `McritClient` builds by hand.

    `safe=""` escapes every character outside the unreserved set, and escapes nothing
    inside it. That is exactly the set `requests.requote_uri` leaves alone on the way
    to the wire, so no escape written here is undone and none is written twice:
    "100%" stays "100%", "%41" stays "%41", "C++_sample.exe" keeps its plusses.

    Correct only while the client concatenates rather than passing `params=` to
    requests, which would encode these a second time. `setup.py` has no ceiling on
    mcrit, so tests/testSubmitMetadata.py asserts the concatenation is still there.

    Used for `addBinarySample`'s three text fields. `getQueueData` and
    `deleteQueueData` build their query strings the same way and are reached with
    unvalidated values through `api.api_router`; that is a separate change.
    """
    return quote(str(value), safe="")


def load_cached_result(app, job_id):
    matching_result = {}
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    for filename in os.listdir(cache_path):
        if job_id in filename and filename.endswith("json"):
            with open(cache_path + os.sep + filename) as fin:
                matching_result = json.load(fin)
    return matching_result


def find_cached_result_filename(app, job_id):
    """Name of the newest cached report for a job, or None if none is cached.

    Matches the whole `<timestamp>-<job_id>.json` name that cache_result writes,
    rather than a substring of it as load_cached_result does: this file is handed to
    the caller as-is, so a short or crafted job_id must not be able to select a
    report that merely contains it. The timestamp prefix sorts chronologically, so
    the newest capture wins for a job that has been fetched more than once.
    """
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    candidates = [filename for filename in os.listdir(cache_path) if filename.endswith(f"-{job_id}.json")]
    return max(candidates) if candidates else None


def cache_result(app, job_info, matching_result):
    # TODO potentially implement a cache control that manages maximum allowed cache size?
    if job_info.result is not None:
        cache_path = os.sep.join([app.instance_path, "cache", "results"])
        timestamped_filename = utc_now().strftime(f"%Y%m%d-%H%M%S-{job_info.job_id}.json")
        with open(cache_path + os.sep + timestamped_filename, "w") as fout:
            json.dump(matching_result, fout, indent=1)


def create_match_diagram(app, job_id, matching_result, filtered_family_id=None, filtered_sample_id=None, filtered_function_id=None):
    cache_path = os.sep.join([app.instance_path, "cache", "diagrams"])
    filter_suffix = ""
    if filtered_family_id is not None:
        filter_suffix = f"-famid_{filtered_family_id}"
    elif filtered_sample_id is not None:
        filter_suffix = f"-samid_{filtered_sample_id}"
    elif filtered_function_id is not None:
        filter_suffix = f"-funid_{filtered_function_id}"
    output_path = cache_path + os.sep + job_id + filter_suffix + ".png"
    if not os.path.isfile(output_path):
        renderer = MatchReportRenderer()
        renderer.processReport(matching_result)
        image = renderer.renderStackedDiagram(filtered_family_id=filtered_family_id, filtered_sample_id=filtered_sample_id, filtered_function_id=filtered_function_id)
        image.save(output_path)
        # `app`, not `current_app`: this function takes an app precisely so it does
        # not depend on a request context, and uses app.instance_path fifteen lines up.
        app.logger.debug("stored new MCRIT diagram: %s", output_path)

# https://stackoverflow.com/a/39842765
# https://stackoverflow.com/a/26972238
# https://flask.palletsprojects.com/en/1.0.x/api/#flask.send_from_directory
@bp.route('/diagrams/<path:filename>')
@visitor_required
def diagram_file(filename):
    cache_path = os.sep.join([current_app.instance_path, "cache", "diagrams"])
    return send_from_directory(cache_path, filename)


################################################################
# Import + Export
################################################################

#: Key under which a refused upload leaves its reason in the session. The completion page
#: is reached by redirect, one request later, so the reason has to survive the hop - and it
#: has to be distinguishable from both a finished import and an untouched session, which is
#: precisely what a bare `None` in `last_import` was not.
IMPORT_REJECTED = "rejected"

#: What an upload that is not MCRIT data gets told. Kept verbatim: it is the honest answer
#: for this case, and only for this case.
NOT_MCRIT_DATA = "This doesn't seem to be valid MCRIT data in JSON format"


def _quote_from_upload(value, limit=64):
    """One value out of the upload, cut to a length that can be quoted back safely.

    The reason ends up in the session, which Flask signs into a single cookie; Werkzeug's
    ceiling is 4093 bytes and a browser drops an oversized cookie without saying so, which
    would take the login, the pending flash and the CSRF token with it. `config.shingler` is
    whatever the uploaded file says it is, so it is bounded here rather than trusted. Not an
    escaping concern - the flash is autoescaped and Dropzone writes the body as textContent.
    """
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "[...]"


def _import_refusal_reason(import_data):
    """What can honestly be said about a JSON object the backend returned no report for.

    Only one of the answers here is a fact, and it is a fact about the file rather than about
    the backend: `MinHashIndex.addImportData` refuses anything whose `config.version` is not
    above "0.0.0", which is checked against a field the upload itself carries, so it holds
    whatever the backend was doing at the time.

    Everything else is a guess, and has to read like one. `handle_response` collapses 500,
    501, 400, 404, 410 *and* every status it does not branch on - falcon's AuthMiddleware
    answers a bad or missing apitoken with a 401 - into the same `None` the config refusals
    produce, and the index also raises its way to a 500 on any JSON object that is not an
    export, since it reads `content`, `family_mapping` and `sample_entries` unguarded. The
    two config hashes are the likeliest cause by far and the backend does not publish its own
    to compare against (`GET /config` is HTTP_NOT_IMPLEMENTED), so they are named as likely,
    with the values the file carries, and the reader is sent to the backend log - which is
    where the actual reason was written, by whichever of these it was.
    """
    config = import_data.get("config")
    if not isinstance(config, dict):
        return None
    version = config.get("version")
    if not isinstance(version, str):
        return None
    if version <= "0.0.0":
        return ("This export cannot be imported: it declares config.version "
                f"'{_quote_from_upload(version)}', and MCRIT only imports exports above 0.0.0. "
                "The file is not malformed - re-export it from an instance running a current "
                "MCRIT.")
    return ("This MCRIT instance did not import the file, and the backend did not report why "
            "in a form this application can read - its log has the reason. The likeliest cause "
            "is that the export was created by an instance whose shingler or minhash "
            "configuration differs from this one's: minhashes are only comparable when both "
            f"match exactly, and this file carries config.shingler "
            f"'{_quote_from_upload(config.get('shingler'))}' and config.minhash "
            f"'{_quote_from_upload(config.get('minhash'))}'. A backend-side failure looks the "
            "same from here, though - a rejected API token, or an error while importing - so "
            "check the log before re-exporting anything.")


def _refuse_import(reason):
    """Answer one rejected upload, in both of the places the user can read it.

    The dropzone posts by XHR: on a non-2xx it marks the tile failed and shows the response
    body as the message (`_handleUploadError` -> `_errorProcessing` in static/dropzone.js),
    where a 200 has it report an upload that worked. It redirects to the completion page
    either way, because the page's `queuecomplete` handler fires for a failed file too - so
    the reason also has to survive that hop, which is what the session marker is for. Without
    it the completion page reads an empty `last_import` and blames a file that was fine.
    """
    session["last_import"] = {IMPORT_REJECTED: reason}
    return reason, 400


@bp.route('/import',methods=('GET', 'POST'))
@contributor_required
@mcrit_server_required
def import_view():
    if request.method == 'POST':
        # dropping the wrong file into a dropzone is ordinary user input, not an
        # exceptional condition - report it the way import_complete already does
        # instead of letting json.load or the client's own type check raise a 500
        try:
            import_data = json.load(request.files['file'])
        except (KeyError, ValueError):
            import_data = None
        if not isinstance(import_data, dict):
            return _refuse_import(NOT_MCRIT_DATA)
        client = get_client()
        import_report = client.addImportData(import_data)
        if not import_report:
            # no report: a config refusal, a stray .json the index raised on, a 401, or any
            # other backend failure - `handle_response` returns None for all of them, so the
            # reason has to be hedged accordingly (see _import_refusal_reason)
            reason = _import_refusal_reason(import_data)
            return _refuse_import(reason if reason is not None else NOT_MCRIT_DATA)
        session["last_import"] = import_report
    return render_template("import.html")

@bp.route('/import_complete')
@contributor_required
def import_complete():
    import_results = session.pop('last_import', None)
    if isinstance(import_results, dict) and IMPORT_REJECTED in import_results:
        flash(import_results[IMPORT_REJECTED], category='error')
        return render_template("import.html")
    if import_results:
        return render_template("import_complete.html", results=import_results)
    flash("Nothing was imported in this session - drop an MCRIT export below to import one.", category='info')
    return render_template("import.html")


@bp.route('/export',methods=('GET', 'POST'))
@contributor_required
@mcrit_server_required
def export_view():
    if request.method == 'POST':
        requested_samples = request.form['samples']
        client = get_client()
        if requested_samples == "":
            export_file = json.dumps(client.getExportData())
            return Response(
                export_file,
                mimetype='application/json',
                headers={"Content-disposition":
                        "attachment; filename=export_all_samples.json"})
        # NOTE it might be nice to allow [<number>, <number>-<number>, ...] to enable 
        # spans of consecutive sample_ids
        elif re.match(r"^\d+(?:[\s]*,[\s]*\d+)*$", requested_samples):
            sample_ids = [int(sample_id.strip()) for sample_id in requested_samples.split(',')]
            export_file = json.dumps(client.getExportData(sample_ids))
            return Response(
                export_file,
                mimetype='application/json',
                headers={"Content-disposition":
                        "attachment; filename=export_samples.json"})
        else:
            flash('Please use a comma-separated list of sample_ids in your export request.', category='error')
            return render_template("export.html")
    return render_template("export.html")

@bp.route('/specific_export/<type>/<item_id>')
@contributor_required
@mcrit_server_required
def specific_export(type, item_id):
    client = get_client()
    if type == 'family':
        samples = client.getSamplesByFamilyId(item_id)
        sample_ids = [x.sample_id for x in samples.values()]
        export_file = json.dumps(client.getExportData(sample_ids))
        return Response(
            export_file,
            mimetype='application/json',
            headers={"Content-disposition":
                    "attachment; filename=export_family_"+str(item_id)+".json"})
    if type == 'samples':
        sample_ids = []
        sample_entry = client.getSampleById(item_id)
        if sample_entry:
            sample_ids.append(sample_entry.sample_id)
        export_file = json.dumps(client.getExportData(sample_ids))
        return Response(
            export_file,
            mimetype='application/json',
            headers={"Content-disposition":
                    "attachment; filename=export_samples.json"})
    # <type> is unconstrained, so anything but the two known values used to fall off
    # the end of this function and return None, which Flask answers with a 500
    flash(f'"{type}" cannot be exported - use "family" or "samples".', category='error')
    return redirect(url_for('data.export_view'))

################################################################
# Direct Function Matching
################################################################

@bp.route('/matches/function/<function_id_a>/<function_id_b>')
@visitor_required
@mcrit_server_required
def match_functions(function_id_a, function_id_b):
    client = get_client()
    if client.isFunctionId(function_id_a) and client.isFunctionId(function_id_b):
        match_info = client.getMatchFunctionVs(function_id_a, function_id_b)
        function_entry = FunctionEntry.fromDict(match_info["function_entry_a"])
        pichash_matches_a = client.getMatchesForPicHash(function_entry.pichash, summary=True)
        sample_entry_a = SampleEntry.fromDict(match_info["sample_entry_a"])
        other_function_entry = FunctionEntry.fromDict(match_info["function_entry_b"])
        sample_entry_b = SampleEntry.fromDict(match_info["sample_entry_b"])
        pichash_matches_b = client.getMatchesForPicHash(other_function_entry.pichash, summary=True)
        matched_function_entry = MatchedFunctionEntry(match_info["match_entry"]["fid"], match_info["match_entry"]["num_bytes"], match_info["match_entry"]["offset"], match_info["match_entry"]["matches"])
        # the entries in match_info carry their xcfg, so the diff need not fetch them again
        function_diff = get_function_diff(function_id_a, function_id_b, function_entry, other_function_entry)
        return render_template(
            "result_compare_function_vs.html",
            entry_a=function_entry,
            entry_b=other_function_entry,
            sample_entry_a=sample_entry_a,
            sample_entry_b=sample_entry_b,
            pichash_matches_a=pichash_matches_a,
            pichash_matches_b=pichash_matches_b,
            match_result=matched_function_entry,
            # the template serialises these with |tojson, which escapes for a script
            # context - pre-serialising here would hand it a string to re-encode
            node_colors=function_diff["node_colors"],
            node_matches=function_diff["node_matches"],
        )
    flash("One of the function_ids is not valid.", category='error')
    return render_template("index.html")

################################################################
# Result presentation
################################################################

# job ids are hex object ids - the same shape views/api.py accepts. Constraining
# job_id once, at the door, is what keeps it harmless in all three places the
# download puts it: a cache filename match, a path handed to send_from_directory,
# and a filename in the Content-Disposition header. Matched with fullmatch rather
# than `$`, which would also accept a trailing newline - and a newline is exactly
# what splits a response header in two.
JOB_ID_PATTERN = re.compile(r"[0-9a-fA-F]+")


@bp.route('/result/<job_id>/download')
@visitor_required
@mcrit_server_required
def download_result(job_id):
    """Serve the report a job produced as a JSON file, exactly as it came from the
    backend - i.e. what MatchingResult.fromDict consumes. See issue #75."""
    client = get_client()
    job_info = None
    if JOB_ID_PATTERN.fullmatch(job_id):
        job_info = client.getJobData(job_id)
    if job_info is None:
        return render_template("result_invalid.html", job_id=job_id)
    cached_filename = find_cached_result_filename(current_app, job_id)
    if cached_filename is not None:
        # prefer the cache and stream the file untouched: it is byte-for-byte what
        # the backend answered, so this costs no parse and re-encode of a report that
        # can run to tens of megabytes, and the download cannot disagree with the
        # page that was rendered from the same file. Nothing goes stale by preferring
        # it - a finished job's result never changes, which is also why the cache is
        # never invalidated.
        cache_path = os.sep.join([current_app.instance_path, "cache", "results"])
        return send_from_directory(
            cache_path,
            cached_filename,
            mimetype='application/json',
            as_attachment=True,
            download_name=f"mcrit_result_{job_id}.json")
    result_json = client.getResultForJob(job_id)
    if not result_json:
        # unfinished, failed, or a job type that produces nothing - the report page
        # already knows how to tell those apart, so let it do the talking
        flash('This job has no result to download.', category='error')
        return redirect(url_for('data.result', job_id=job_id))
    cache_result(current_app, job_info, result_json)
    # the fetch above already holds the whole report as a dict, so serialising it
    # once more is the cheap half of a cache miss; it is the cached path that keeps
    # every later download off the heap. indent=1 as cache_result writes it, so both
    # paths answer with the same bytes.
    return Response(
        json.dumps(result_json, indent=1),
        mimetype='application/json',
        headers={"Content-disposition":
                f"attachment; filename=mcrit_result_{job_id}.json"})


@bp.route('/result/<job_id>')
@visitor_required
@mcrit_server_required
# TODO:  refactor, simplify
def result(job_id):
    client = get_client()
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = client.getJobData(job_id)
    if not result_json:
        # otherwise obtain result report from remote
        result_json = client.getResultForJob(job_id)
        if result_json:
            cache_result(current_app, job_info, result_json)
    if result_json:
        # TODO validation - only parse to matching_result if this data type is appropriate 
        # re-format result report for visualization and choose respective template
        if job_info is None:
            return render_template("result_invalid.html", job_id=job_id)
        if job_info.parameters.startswith("getMatchesForSampleVs"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForSample"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForSmdaReport"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForMappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForUnmappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("combineMatchesToCross"):
            return result_matches_for_cross(job_info, result_json)
        # NOTE: 'updateMinHashes' is the start of 'updateMinHashesForSample'.
        # For this reason, these two elif clauses should not be reordered
        elif job_info.parameters.startswith("updateMinHashesForSample"):
            return render_template("result_empty.html", job_id=job_id)
        elif job_info.parameters.startswith("updateMinHashes"):
            return render_template("result_empty.html", job_id=job_id)
        elif job_info.parameters.startswith("getUniqueBlocks"):
            return result_unique_blocks(job_info, result_json)
        elif job_info.parameters.startswith("addBinarySample"):
            return redirect(url_for('explore.sample_by_id', sample_id=result_json['sample_info']['sample_id']))
        # modify and delete samples and families
        elif job_info.parameters.startswith("deleteSample"):
            return redirect(url_for('explore.samples'))
        elif job_info.parameters.startswith("modifySample"):
            return redirect(url_for('explore.samples'))
        elif job_info.parameters.startswith("deleteFamily"):
            return redirect(url_for('explore.families'))
        elif job_info.parameters.startswith("modifyFamily"):
            return redirect(url_for('explore.families'))
        elif job_info.parameters in ["rebuildIndex()", "recalculatePicHashes()", "recalculateMinHashes()"]:
            return render_template("result_maintenance.html", result=result_json, job_info=job_info)
    elif job_info and not (job_info.is_finished or job_info.is_failed or job_info.is_terminated):
        # if we are not done processing, list job data
        return render_template("job_in_progress.html", job_info=job_info)
    else:
        # if we can't find job or result, we have to assume the job_id was invalid
        return render_template("result_invalid.html", job_id=job_id)

#: `UniqueBlocksResult.generateYaraRule`'s own defaults, restated so the page can offer
#: them as a form. These are not job parameters - the backend stores blocks and
#: statistics, and the rule is composed here from that cached result on every render -
#: so changing one reapplies to a job that already ran. See issue #93.
YARA_RULE_DEFAULTS = {
    "min_ins": None,
    "max_ins": None,
    "min_bytes": None,
    "max_bytes": None,
    "required_per_sample": 10,
    "condition_required": 7,
}

#: The rule is YARA source, offered for copying straight into a scanner: "0 of them"
#: matches nothing and a negative count does not compile. The bounds above have no such
#: floor - generateBlockCover reads 0 as "no bound".
#:
#: This only floors the number the caller asks for. `renderRule` emits
#: `min(len(block_hashes), condition_required)`, so a bound that filters every block
#: reaches "0 of them" from underneath the clamp - see `build_yara_rule`.
YARA_CONDITION_MINIMUM = 1

#: `required_per_sample` is the one knob that costs time rather than only changing the
#: answer: generateBlockCover picks one block per pass and rescans the rest, so asking
#: for k blocks per sample is O(k*n). Measured against a 6124-block report, the default
#: 10 costs 0.08s, 100 costs 0.9s and 1000 costs 24s - so an unbounded query parameter
#: would let any visitor turn this page into a long-running request. A rule wanting more
#: than this many strings out of one sample is not a usable YARA rule either.
YARA_REQUIRED_PER_SAMPLE_MAXIMUM = 100


def parse_yara_rule_params(request):
    """The rule generation knobs as query parameters, clamped to values YARA accepts."""
    yara_params = dict(YARA_RULE_DEFAULTS)
    for name in YARA_RULE_DEFAULTS:
        value = parse_integer_query_param(request, name)
        if value is not None:
            # a negative bound is not something the form can produce and not something a
            # user can mean: as a minimum it filters nothing, as a maximum everything
            yara_params[name] = max(0, value)
    yara_params["condition_required"] = max(YARA_CONDITION_MINIMUM, yara_params["condition_required"])
    yara_params["required_per_sample"] = min(YARA_REQUIRED_PER_SAMPLE_MAXIMUM, yara_params["required_per_sample"])
    return yara_params


def build_yara_rule(blocks_result, yara_params):
    """The rule, plus the block cover it was built from - or None for no rule.

    `generateYaraRule` throws the cover away, but the page reports what the rule covers,
    and those numbers move with the parameters - so they cannot be read off the
    statistics the backend stored under its own defaults.

    A cover with no blocks in it has no rule to render. `renderRule` would still produce
    one, but it is not YARA: an empty `strings:` section is a syntax error on its own,
    and `min(len(block_hashes), condition_required)` writes "0 of them" underneath
    YARA_CONDITION_MINIMUM. No condition rescues that, so nothing is offered to copy.
    """
    ubr = UniqueBlocksResult.fromDict(blocks_result)
    block_cover = ubr.generateBlockCover(
        min_ins=yara_params["min_ins"],
        max_ins=yara_params["max_ins"],
        min_bytes=yara_params["min_bytes"],
        max_bytes=yara_params["max_bytes"],
        required_per_sample=yara_params["required_per_sample"],
    )
    if not block_cover["block_hashes"]:
        return None, block_cover
    return ubr.renderRule(block_cover, yara_params["condition_required"], wrap_at=40), block_cover

def result_unique_blocks(job_info, blocks_result: dict):
    client = get_client()
    payload_params = json.loads(job_info.payload["params"])
    sample_ids = payload_params["0"]
    # a sample set is what analyze.unique_blocks submits; the one-click buttons send a
    # list of one, and a family job names its samples this way too
    sample_id = sample_ids[0] if sample_ids else None
    family_id = None
    family_entry = None
    if "family_id" in payload_params:
        family_id = payload_params["family_id"]
        family_entry = client.getFamily(family_id)
    if blocks_result is None:
        if family_id is not None:
            flash(f"No results for unique blocks in family with id {family_id}", category="error")
        else:
            flash(f"No results for unique blocks in family with id {sample_id}", category="error")
    blocks_statistics = blocks_result["statistics"]
    yara_params = parse_yara_rule_params(request)
    yara_rule, yara_cover = build_yara_rule(blocks_result, yara_params)
    # only what the caller actually changed, so the forms can carry the rule parameters
    # across the block filter and back without pinning the defaults into every link
    yara_query = {name: value for name, value in yara_params.items() if value != YARA_RULE_DEFAULTS[name]}
    unique_blocks = blocks_result["unique_blocks"]

    paginated_blocks = []
    # TODO this result object has changed, we should split it into stats/blocks/yara and process further
    if unique_blocks is not None:
        min_score = parse_integer_query_param(request, "min_score")
        min_block_length = parse_integer_query_param(request, "min_block_length")
        max_block_length = parse_integer_query_param(request, "max_block_length")
        active_tab = request.args.get('tab','stats')
        active_tab = active_tab if active_tab in ["stats", "yara", "blocks"] else "stats"
        filtered_blocks = unique_blocks
        if min_score:
            filtered_blocks = {pichash: block for pichash, block in unique_blocks.items() if block["score"] >= min_score}
        if min_block_length or max_block_length:
            min_block_length = 0 if min_block_length is None else min_block_length
            max_block_length = 0xFFFFFFFF if max_block_length is None else max_block_length
            filtered_blocks = {pichash: block for pichash, block in filtered_blocks.items() if max_block_length >= block["length"] >= min_block_length}
        unique_blocks = filtered_blocks
        number_of_unique_blocks = len(filtered_blocks)
        block_pagination = Pagination(request, number_of_unique_blocks, limit=100, query_param="blkp", limit_param="blkl")
        index = 0
        for pichash, result in sorted(unique_blocks.items(), key=lambda x: x[1]["score"], reverse=True):
            if index >= block_pagination.end_index:
                break
            if index >= block_pagination.start_index:
                yarafied = f"/* picblockhash: {pichash} \n"
                maxlen_ins = max([len(ins[1]) for ins in result["instructions"]])
                for ins in result["instructions"]:
                    yarafied += f" * {ins[1]:{maxlen_ins}} | {ins[2]} {ins[3]}\n"
                yarafied += " */\n"
                yarafied += "{ " + re.sub(r"(.{80})", "\\1\n", result["escaped_sequence"], flags=re.DOTALL) + " }"
                unique_blocks[pichash]["yarafied"] = yarafied
                paginated_block = result
                paginated_block["key"] = pichash
                paginated_block["yarafied"] = yarafied
                paginated_blocks.append(paginated_block)
            index += 1
    # TODO pass the new result objects as single arguments and then render them in page tabs on the template
    return render_template("result_unique_blocks.html", job_info=job_info, family_entry=family_entry, sample_id=sample_id, sample_ids=sample_ids, yara_rule=yara_rule, yara_cover=yara_cover, yara_params=yara_params, yara_query=yara_query, statistics=blocks_statistics, results=paginated_blocks, blkp=block_pagination, active_tab=active_tab)

#: Shown when a stored result names something the backend can no longer resolve. The
#: cross-compare path has said this about samples for a long time; issue #96 is the
#: same situation one level down, where the missing thing is a function.
MISSING_ENTRIES_REASON = "MCRIT was not able to retrieve information for all functions referenced by this result. This might be a result of having deleted samples from the database since it was processed. Please consider starting a new job."


def assign_matched_offsets(client, function_matches):
    """Attach the offset of each matched function, in place.

    Returns False when the backend no longer has every function the result names.
    `getFunctionsByIds` answers only the entries that still exist, and a stored
    result refers to functions by id, so deleting a sample after a job finished
    leaves ids behind that resolve to nothing. Indexing the lookup directly made
    that a KeyError and a 500 on a report that is otherwise still readable - see
    issue #96. The caller decides what to do about it; all three call sites in this
    module render `result_corrupted.html`, which is what the cross-compare path
    already does for a missing sample.
    """
    matched_function_ids = list({match.matched_function_id for match in function_matches})
    matched_function_entries_by_id = client.getFunctionsByIds(matched_function_ids) or {}
    is_complete = True
    for function_match in function_matches:
        function_entry = matched_function_entries_by_id.get(function_match.matched_function_id)
        if function_entry is None:
            is_complete = False
            continue
        function_match.matched_offset = function_entry.offset
    return is_complete


def name_query_sample(job_info, matching_result: MatchingResult):
    """Fill in the filename of a queried binary, in place.

    A query is matched without being stored, so the backend has no sample of its own
    to name and sends `filename: ""` back in the report - the result page showed "-"
    where every other input sample shows a name (issue #40). None of the query
    endpoints accepts a filename either, so the upload's name only ever existed here,
    and `analyze.query` records it against the job id it was queued as.

    Keyed off a negative sample_id rather than `MatchingResult.is_query`, which is
    derived from the sign of the last function match and stays False for a report
    that matched nothing.
    """
    sample_entry = matching_result.reference_sample_entry
    if sample_entry is None or sample_entry.sample_id is None or sample_entry.sample_id >= 0:
        return
    if not sample_entry.filename:
        sample_entry.filename = get_query_filename(job_info.job_id) or ""


def result_matches_for_sample_or_query(job_info, matching_result: MatchingResult):
    name_query_sample(job_info, matching_result)
    score_color_provider = ScoreColorProvider()
    filtered_family_id = parse_integer_query_param(request, "famid")
    filtered_sample_id = parse_integer_query_param(request, "samid")
    filtered_function_id = parse_integer_query_param(request, "funid")
    filter_action = parse_str_query_param(request, "filter_button_action")
    # generic filtering on family/sample results
    filter_direct_min_score = parse_integer_query_param(request, "filter_direct_min_score")
    filter_direct_nonlib_min_score = parse_integer_query_param(request, "filter_direct_nonlib_min_score")
    filter_frequency_min_score = parse_integer_query_param(request, "filter_frequency_min_score")
    filter_frequency_nonlib_min_score = parse_integer_query_param(request, "filter_frequency_nonlib_min_score")
    filter_unique_only = parse_checkbox_query_param(request, "filter_unique_only")
    filter_exclude_own_family = parse_checkbox_query_param(request, "filter_exclude_own_family")
    filter_family_name = parse_str_query_param(request, "filter_family_name")
    # generic filtering of function results
    filter_function_min_score = parse_integer_query_param(request, "filter_function_min_score")
    filter_function_max_score = parse_integer_query_param(request, "filter_function_max_score")
    filter_function_offset = parse_integer_query_param(request, "filter_function_offset")
    filter_max_num_families = parse_integer_query_param(request, "filter_max_num_families")
    filter_min_num_samples = parse_integer_query_param(request, "filter_min_num_samples")
    filter_max_num_samples = parse_integer_query_param(request, "filter_max_num_samples")
    filter_exclude_library = parse_checkbox_query_param(request, "filter_exclude_library")
    filter_exclude_pic = parse_checkbox_query_param(request, "filter_exclude_pic")
    filter_func_unique = parse_checkbox_query_param(request, "filter_func_unique")
    user_id = get_session_user_id()
    if (all(flag is None for flag in [filter_direct_min_score, filter_frequency_min_score, filter_family_name,
                filter_function_min_score, filter_function_max_score, filter_min_num_samples, filter_max_num_samples, filter_max_num_families, filter_function_offset])
            and not any([filter_unique_only, filter_exclude_own_family, filter_exclude_library, filter_exclude_pic, filter_func_unique])
            and not filter_action == "clear"):
        # load default filters
        # adjust filters based on family/sample filtering
        user_filters = UserFilters.fromDb(user_id)
        # if we don't have them yet, create them
        if user_filters is None:
            user_filters = UserFilters.fromDict(user_id, {})
            user_filters.saveToDb()
        filter_values = user_filters.toDict()
        if filtered_family_id is None and filtered_sample_id is None and filtered_function_id is None:
            filter_values["filter_min_num_samples"] = None
            filter_values["filter_max_num_samples"] = None
            filter_values["filter_max_num_families"] = None
        elif filtered_family_id is not None:
            filter_values["filter_min_num_samples"] = None
            filter_values["filter_max_num_samples"] = None
            filter_values["filter_max_num_families"] = None
        elif filtered_sample_id is not None:
            filter_values["filter_min_num_samples"] = None
            filter_values["filter_max_num_samples"] = None
            filter_values["filter_max_num_families"] = None
    elif filter_action == "clear":
        filter_values = {
            "filter_direct_min_score": None,
            "filter_direct_nonlib_min_score": None,
            "filter_frequency_min_score": None,
            "filter_frequency_nonlib_min_score": None,
            "filter_unique_only": None,
            "filter_exclude_own_family": None,
            "filter_family_name": None,
            "filter_function_min_score": None,
            "filter_function_max_score": None,
            "filter_function_offset": None,
            "filter_max_num_families": None,
            "filter_min_num_samples": None,
            "filter_max_num_samples": None,
            "filter_exclude_library": None,
            "filter_exclude_pic": None,
            "filter_func_unique": None,
        }
    else:
        filter_values = {
            "filter_direct_min_score": filter_direct_min_score,
            "filter_direct_nonlib_min_score": filter_direct_nonlib_min_score,
            "filter_frequency_min_score": filter_frequency_min_score,
            "filter_frequency_nonlib_min_score": filter_frequency_nonlib_min_score,
            "filter_unique_only": filter_unique_only,
            "filter_exclude_own_family": filter_exclude_own_family,
            "filter_family_name": filter_family_name,
            "filter_function_min_score": filter_function_min_score,
            "filter_function_max_score": filter_function_max_score,
            "filter_function_offset": filter_function_offset,
            "filter_max_num_families": filter_max_num_families,
            "filter_min_num_samples": filter_min_num_samples,
            "filter_max_num_samples": filter_max_num_samples,
            "filter_exclude_library": filter_exclude_library,
            "filter_exclude_pic": filter_exclude_pic,
            "filter_func_unique": filter_func_unique,
        }
    matching_result.setFilterValues(filter_values)
    matching_result.getUniqueFamilyMatchInfoForSample(None)
    matching_result.applyFilterValues()

    client = get_client()

    # load user column setup from database
    user_column_settings = UserColumnSettings.fromDb(user_id)
    # if we don't have them yet, create them
    if user_column_settings is None:
        user_column_settings = UserColumnSettings(user_id)
        user_column_settings.saveToDb()
    ucs_dict = user_column_settings.toUserColumnSettings()
    user_column_setup_family_library = ucs_dict["result_family_table"]["active"]
    user_column_setup_function_all = ucs_dict["result_function_unfiltered_table"]["active"]
    # note that in getMatchesForSampleVs - we can never determine if a function is unique across the data set, so we ignore the field
    user_column_setup_function_sample = ucs_dict["result_function_sample_filtered_table"]["active"]
    user_column_setup_function_function = ucs_dict["result_function_function_filtered_table"]["active"]
    # filtered for family
    if filtered_family_id is not None and client.isFamilyId(filtered_family_id):
        matching_result.filterToFamilyId(filtered_family_id)
        create_match_diagram(current_app, job_info.job_id, matching_result, filtered_family_id=filtered_family_id)
        sample_pagination = Pagination(request, matching_result.num_sample_matches, limit=10, query_param="samp", limit_param="sampl")
        function_pagination = Pagination(request, len(matching_result.getAggregatedFunctionMatches()), limit=100, query_param="funp", limit_param="funl")
        # result_compare_family.html and result_compare_all.html draw one row per matched
        # function of the reference sample, aggregated over the samples it matched, and print
        # "filtered" as the rest of the report beside it. That subtraction only means anything
        # if both sides count the same thing, so the total goes in aggregated too - taken off
        # the raw match count, it read as a four-figure "filtered" with no filter applied.
        num_original_aggregated_functions = len(matching_result.getAggregatedFunctionMatches(unfiltered=True))
        return render_template("result_compare_family.html", famid=filtered_family_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, num_original_aggregated_functions=num_original_aggregated_functions, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_all) 
    # filtered for sample
    elif filtered_sample_id is not None and client.isSampleId(filtered_sample_id):
        matching_result.filterToSampleId(filtered_sample_id)
        create_match_diagram(current_app, job_info.job_id, matching_result, filtered_sample_id=filtered_sample_id)
        filtered_sample_entry = client.getSampleById(filtered_sample_id)
        matching_result.other_sample_entry = filtered_sample_entry
        # get offsets for matched functions
        if not assign_matched_offsets(client, matching_result.filtered_function_matches):
            return render_template("result_corrupted.html", reason=MISSING_ENTRIES_REASON, job_info=job_info)
        sample_pagination = Pagination(request, 1, limit=10, query_param="samp", limit_param="sampl")
        # result_compare_sample.html draws one row per function match (it has an Offset B and a
        # Function B column, which only an individual match has), so it slices getFunctionsSlice
        # and this has to count the same list. Aggregating collapses the several functions of
        # this sample that one query function can match, and paginating over that count left the
        # tail of the table on no page at all.
        function_pagination = Pagination(request, matching_result.num_function_matches, limit=100, query_param="funp", limit_param="funl")
        return render_template("result_compare_sample.html", samid=filtered_sample_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_sample) 
    # filter for function - treat family/sample part as if there was no filter
    elif filtered_function_id is not None and filtered_function_id in matching_result.function_id_to_family_ids_matched:
        if not matching_result.is_query:
            create_match_diagram(current_app, job_info.job_id, matching_result, filtered_function_id=filtered_function_id)
        matching_result.filterToFunctionId(filtered_function_id)
        matching_result.filtered_function_matches = sorted(matching_result.filtered_function_matches, key=lambda x: (x.matched_score, x.match_is_pichash, x.matched_family_id, x.matched_sample_id, x.matched_function_id), reverse=True)
        # pull all function_entries, as we want to have their offsets
        if not assign_matched_offsets(client, matching_result.filtered_function_matches):
            return render_template("result_corrupted.html", reason=MISSING_ENTRIES_REASON, job_info=job_info)
        # set up pagination
        family_pagination = Pagination(request, matching_result.num_family_matches, limit=10, query_param="famp", limit_param="fampl")
        function_pagination = Pagination(request, matching_result.num_function_matches, limit=100, query_param="funp", limit_param="funl")
        return render_template("result_compare_function.html", funid=filtered_function_id, job_info=job_info, famp=family_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_function) 
    # 1 vs 1 result
    elif job_info.parameters.startswith("getMatchesForSampleVs("):
        # get offsets for matched functions
        if not assign_matched_offsets(client, matching_result.filtered_function_matches):
            return render_template("result_corrupted.html", reason=MISSING_ENTRIES_REASON, job_info=job_info)
        # we need to slice function matches ourselves based on pagination
        function_pagination = Pagination(request, matching_result.num_function_matches, limit=100, query_param="funp", limit_param="funl")
        return render_template("result_compare_vs.html", job_info=job_info, matching_result=matching_result, funp=function_pagination, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_sample)
    # unfiltered / default -> also 1 vs group
    else:
        create_match_diagram(current_app, job_info.job_id, matching_result)
        family_pagination = Pagination(request, matching_result.num_family_matches, limit=10, query_param="famp", limit_param="fampl")
        library_pagination = Pagination(request, matching_result.num_library_matches, limit=10, query_param="libp", limit_param="libl")
        function_pagination = Pagination(request, len(matching_result.getAggregatedFunctionMatches()), limit=100, query_param="funp", limit_param="funl")
        # the total behind the "filtered" figure, aggregated to match the rows - see above
        num_original_aggregated_functions = len(matching_result.getAggregatedFunctionMatches(unfiltered=True))
        # a query can be promoted to a sample (issue #9), but only while the file it
        # was run for is still on this host - the page has to say which it is. The file
        # is filed under the job's own id, so this costs no round trip either
        is_query_result = job_info.method in QUERY_UPLOAD_KINDS
        return render_template("result_compare_all.html", job_info=job_info, famp=family_pagination, libp=library_pagination, funp=function_pagination, num_original_aggregated_functions=num_original_aggregated_functions, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_all, is_query_result=is_query_result, can_promote_query=is_query_result and query_upload_exists(current_app, job_info.job_id))


def result_matches_for_cross(job_info, result_json):
    client = get_client()
    samples = []
    sample_ids = [int(id) for id in next(iter(result_json.values()))["clustered_sequence"]]
    for sample_id in sample_ids:
        sample_entry = client.getSampleById(sample_id)
        if sample_entry:
            samples.append(sample_entry)
        else:
            reason = "MCRIT was not able to retrieve information for all samples specified in the original job task. This might be a result of having deleted samples from the database since it was processed. Please consider starting a new job."
            return render_template("result_corrupted.html", reason=reason, job_info=job_info)
    custom_order = request.args.get('custom','')
    samples_by_method = {}
    sample_indices = {}
    for method, method_results in result_json.items():
        if custom_order:
            order = custom_order.split(',')
        elif "clustered_sequence" in method_results:
            order = method_results["clustered_sequence"]
        else:
            order = None
        ordered_samples = []
        if order:
            for order_sample_id in order:
                for sample in samples:
                    if str(sample.sample_id) == str(order_sample_id):
                        ordered_samples.append(sample)
                        break
                else:
                    reason = "MCRIT was not able to produce the chosen custom ordering, as some sample_ids are not part of the cross compare originally specified."
                    # job_info, not result_json: the template reads job_info.job_id for
                    # its heading and for the delete link, and a result dict has neither.
                    # The sibling corrupted branch above already passes the Job.
                    return render_template("result_corrupted.html", reason=reason, job_info=job_info)
        if ordered_samples != []:
            samples_by_method[method] = ordered_samples
        else:
            samples_by_method[method] = samples
        sample_indices[method] = [x for index, x in enumerate([sample.sample_id for sample in samples_by_method[method]]) if (index+1) % 5 == 0]
    return render_template('result_cross.html',
        is_corrupted=False,
        samples=samples_by_method,
        sample_indices = sample_indices,
        job_info=job_info,
        sample_to_job_id=get_sample_to_job_id(job_info),
        matching_matches={method: result_json[method]["matching_matches"] for method in result_json.keys()},
        matching_percent={method: result_json[method]["matching_percent"] for method in result_json.keys()},
        score_to_color=score_to_color,
    )


################################################################
# Link Hunting
################################################################

#: The job methods whose result is a MatchingResult, which is the only shape link
#: hunting can read. Prefix matching, so getMatchesForSample also covers
#: getMatchesForSampleVs - a 1v1 report is a MatchingResult too.
LINKHUNTABLE_METHODS = (
    "getMatchesForSample",
    "getMatchesForSmdaReport",
    "getMatchesForMappedBinary",
    "getMatchesForUnmappedBinary",
)


@bp.route('/linkhunt/<job_id>')
@visitor_required
@mcrit_server_required
# TODO:  refactor, simplify
def linkhunt(job_id):
    client = get_client()
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = client.getJobData(job_id)
    if not result_json:
        # otherwise obtain result report from remote
            result_json = client.getResultForJob(job_id)
            if result_json:
                cache_result(current_app, job_info, result_json)
    if job_info is None:
        # nothing in the queue under this id. A result without a job would be the same
        # answer: there is nothing here to interpret.
        return render_template("result_invalid.html", job_id=job_id)

    if result_json:
        # TODO validation - only parse to matching_result if this data type is appropriate
        # re-format result report for visualization and choose respective template
        if job_info.parameters.startswith(LINKHUNTABLE_METHODS):
            matching_result = MatchingResult.fromDict(result_json)
            return linkhunt_for_sample_or_query(job_info, matching_result)
        # a report of some other kind. Link hunting reads a MatchingResult, so a cross
        # compare or a unique-blocks report cannot answer it - and used to fall off the
        # end of this chain, where a view returning None is a 500 rather than an answer.
        return render_template("result_incompatible.html", job_id=job_id)

    # no report. That is not the same as "still working": a minhashing job or a
    # collection change stores no result at all and is finished the moment it runs, so
    # the old `elif job_info:` showed a progress page for a job that ended long ago -
    # permanently, since it never changes.
    if job_info.is_failed or job_info.is_terminated:
        reason = ("This job was terminated before it could finish."
                  if job_info.is_terminated else
                  "This job ran out of attempts and failed. The backend's log will say why.")
        return render_template("job_failed.html", job_info=job_info, reason=reason)
    if job_info.is_finished:
        if job_info.parameters.startswith(LINKHUNTABLE_METHODS):
            # the right kind of job; it just produced nothing to hunt through
            return render_template("result_empty.html", job_id=job_id)
        return render_template("result_incompatible.html", job_id=job_id)
    # genuinely still working
    return render_template("job_in_progress.html", job_info=job_info)

def linkhunt_for_sample_or_query(job_info, matching_result: MatchingResult):
    name_query_sample(job_info, matching_result)
    client = get_client()
    score_color_provider = ScoreColorProvider()
    # generic filtering of function results
    filter_action = parse_str_query_param(request, "filter_button_action")
    filter_min_score = parse_integer_query_param(request, "filter_min_score")
    filter_lib_min_score = parse_integer_query_param(request, "filter_lib_min_score")
    filter_link_score = parse_integer_query_param(request, "filter_link_score")
    filter_min_size = parse_integer_query_param(request, "filter_min_size")
    filter_min_offset = parse_integer_query_param(request, "filter_min_offset")
    filter_max_offset = parse_integer_query_param(request, "filter_max_offset")
    filter_unpenalized_family_count = parse_integer_query_param(request, "filter_unpenalized_family_count")
    filter_exclude_families = parse_integer_list_query_param(request, "filter_exclude_families")
    filter_exclude_samples = parse_integer_list_query_param(request, "filter_exclude_samples")
    filter_strongest_per_family = parse_checkbox_query_param(request, "filter_strongest_per_family")
    if (all(flag is None for flag in [filter_min_score, filter_lib_min_score, filter_link_score, filter_min_size,
                filter_min_offset, filter_max_offset, filter_exclude_families, filter_exclude_samples])
                and not filter_strongest_per_family
                and not filter_action == "clear"):
        # specify default filters
        filter_min_score = 65
        filter_lib_min_score = 80
        filter_link_score = 30
        filter_min_size = 50
        filter_min_offset = None
        filter_max_offset = None
        # own family id
        filter_exclude_families = [matching_result.reference_sample_entry.family_id]
        filter_exclude_samples = []
        filter_unpenalized_family_count = 2
        filter_strongest_per_family = True
    elif filter_action == "clear":
        filter_min_score = None
        filter_lib_min_score = None
        filter_link_score = None
        filter_min_size = None
        filter_min_offset = None
        filter_max_offset = None
        # own family id
        filter_exclude_families = None
        filter_exclude_samples = None
        filter_unpenalized_family_count = 2
        filter_strongest_per_family = False
    filter_values = {
        "filter_min_score": filter_min_score,
        "filter_lib_min_score": filter_lib_min_score,
        "filter_link_score": filter_link_score,
        "filter_min_size": filter_min_size,
        "filter_min_offset": filter_min_offset,
        "filter_max_offset": filter_max_offset,
        "filter_exclude_families": ", ".join([str(famid) for famid in filter_exclude_families]) if filter_exclude_families is not None else "",
        "filter_exclude_samples": ", ".join([str(samid) for samid in filter_exclude_samples]) if filter_exclude_samples is not None else "",
        "filter_unpenalized_family_count": filter_unpenalized_family_count,
        "filter_strongest_per_family": filter_strongest_per_family,
    }
    matching_result.setFilterValues(filter_values)
    link_hunt_result = matching_result.getLinkHuntResults(filter_min_score, filter_lib_min_score, filter_min_size, filter_min_offset, filter_max_offset, filter_unpenalized_family_count, filter_exclude_families, filter_exclude_samples, filter_strongest_per_family)

    function_entries = client.getFunctionsBySampleId(matching_result.reference_sample_entry.sample_id)
    # TODO: probably need to paginate them as well
    link_clusters = matching_result.clusterLinkHuntResult(function_entries, link_hunt_result)
    link_clusters = sorted([cluster for cluster in link_clusters if len(cluster["links"]) > 1], key=lambda x: x["score"], reverse=True)

    if filter_link_score:
        link_clusters = [cluster for cluster in link_clusters if cluster["score"] > filter_link_score]
        link_hunt_result = [link for link in link_hunt_result if link.matched_link_score > filter_link_score]

    function_pagination = Pagination(request, len(link_hunt_result), limit=100, query_param="funp", limit_param="funl")
    return render_template("linkhunt.html", job_info=job_info, funp=function_pagination, matching_result=matching_result, lc=link_clusters, lhr=link_hunt_result, scp=score_color_provider)


################################################################
# Listing Job information
################################################################

#: The job types this front end knows the names of. Not the authority on what a job type
#: is - the backend is, and `known_job_category` below defers to it. This is only the
#: fallback for a type the backend is not currently reporting, because it has no jobs of
#: that kind. `Job.method_types["all"]` is not even the whole local list: it omits
#: recalculatePicHashes and recalculateMinHashes, which the admin maintenance routes
#: create and which the menu does render.
JOB_CATEGORIES = tuple(Job(None, None).method_types["all"]) + (
    "recalculatePicHashes",
    "recalculateMinHashes",
)


def known_job_category(category, statistics):
    """Is `category` a job type, as far as anyone here can tell?

    The backend is authoritative: a method it reports in its queue statistics is a real
    one whether or not this front end has heard of it, so an installation whose backend
    grows a new job type keeps working without a release here. The local list covers the
    other direction - a type with no jobs right now is absent from the statistics and
    still has a tab.

    The residue is a type that is both new to this front end and has no jobs yet; it is
    indistinguishable from a typo, and gets the typo's answer.
    """
    return category in statistics or category in JOB_CATEGORIES


@bp.route('/jobs',methods=('GET', 'POST'))
@visitor_required
@mcrit_server_required
def jobs():
    query = None
    if request.method == 'POST':
        query = request.form['Search']
    # used for job/method collections
    client = get_client()
    # sort order
    ascending = request.args.get('ascending', 'false').lower() == "true"
    statistics = client.getQueueStatistics()
    if statistics is None:
        # `handle_response` answers None for every non-200 - a backend that is down, one
        # that is a version behind and has no such endpoint, a 500 mid-query. Read as a
        # dict below it is a TypeError and this page is a stack trace, so the one call
        # this whole view is built on has to be allowed to fail. An empty mapping renders
        # the page it would render for an empty queue, plus a message saying which it is.
        flash("Ups, reading MCRIT's job queue failed - the queue could not be summarized.", category="error")
        statistics = {}
    job_template = Job(None, None)
    # dynamically create the job page with nested menu based on groups from statistics and Job.method_types
    active_category = request.args.get('active', None)
    # checked before "totals" is added to statistics below, so ?active=totals is not
    # accidentally a category
    if active_category is not None and not known_job_category(active_category, statistics):
        # rendering an empty list would read as a fact about the queue rather than
        # about the URL, so say which it is and fall back to the default tab
        flash(f'"{active_category}" is not a job type.', category="error")
        active_category = None
    summarized_groups = {"matching": 0, "query": 0, "blocks": 0, "minhashing": 0, "collection": 0}
    for group in summarized_groups.keys():
        for category in job_template.method_types[group]:
            if category in statistics:
                summarized_groups[group] += sum(statistics[category].values())
    if active_category is None:
        for category in job_template.method_types["all"]:
            if category in statistics:
                active_category = category
                break
    # if we filter by state, don't filter by type
    state_category = request.args.get('state', None)
    if state_category:
        active_category = None
    totals = {"queued": 0, "in_progress": 0, "finished": 0, "failed": 0, "terminated": 0}
    for category, status_dict in statistics.items():
        for state, count in status_dict.items():
            if state not in totals:
                totals[state] = 0
            totals[state] += count
    statistics["totals"] = totals
    # build menu information
    jobs = None
    pagination = None
    menu_configuration = {
        "menu": [
            {"group": "matching", "title": f"Matching ({summarized_groups['matching']})", "active": active_category in ["getMatchesForSample", "getMatchesForSampleVs", "combineMatchesToCross"], "available": True, "submenu": [
                {"name": "getMatchesForSample", "title": f"getMatchesForSample ({sum(statistics['getMatchesForSample'].values()) if 'getMatchesForSample' in statistics else 0})", "active": "getMatchesForSample" == active_category, "available": "getMatchesForSample" in statistics},
                {"name": "getMatchesForSampleVs", "title": f"getMatchesForSampleVs ({sum(statistics['getMatchesForSampleVs'].values()) if 'getMatchesForSampleVs' in statistics else 0})", "active": "getMatchesForSampleVs" == active_category, "available": "getMatchesForSampleVs" in statistics},
                {"name": "combineMatchesToCross", "title": f"combineMatchesToCross ({sum(statistics['combineMatchesToCross'].values()) if 'combineMatchesToCross' in statistics else 0})", "active": "combineMatchesToCross" == active_category, "available": "combineMatchesToCross" in statistics},
            ]}, 
            {"group": "query", "title": f"Query ({summarized_groups['query']})", "active": active_category in ["getMatchesForUnmappedBinary", "getMatchesForMappedBinary", "getMatchesForSmdaReport"], "available": True, "submenu": [
                {"name": "getMatchesForUnmappedBinary", "title": f"getMatchesForUnmappedBinary ({sum(statistics['getMatchesForUnmappedBinary'].values()) if 'getMatchesForUnmappedBinary' in statistics else 0})", "active": "getMatchesForUnmappedBinary" == active_category, "available": "getMatchesForUnmappedBinary" in statistics},
                {"name": "getMatchesForMappedBinary", "title": f"getMatchesForMappedBinary ({sum(statistics['getMatchesForMappedBinary'].values()) if 'getMatchesForMappedBinary' in statistics else 0})", "active": "getMatchesForMappedBinary" == active_category, "available": "getMatchesForMappedBinary" in statistics},
                {"name": "getMatchesForSmdaReport", "title": f"getMatchesForSmdaReport ({sum(statistics['getMatchesForSmdaReport'].values()) if 'getMatchesForSmdaReport' in statistics else 0})", "active": "getMatchesForSmdaReport" == active_category, "available": "getMatchesForSmdaReport" in statistics},
            ]}, 
            {"group": "getUniqueBlocks", "title": f"Blocks ({summarized_groups['blocks']})", "active": "getUniqueBlocks" == active_category, "available": "getUniqueBlocks" in statistics},
            {"group": "minhashing", "title": f"Minhashing ({summarized_groups['minhashing']})", "active": active_category in ["updateMinHashesForSample", "updateMinHashes", "rebuildIndex"], "available": True, "submenu": [
                {"name": "updateMinHashesForSample", "title": f"updateMinHashesForSample ({sum(statistics['updateMinHashesForSample'].values()) if 'updateMinHashesForSample' in statistics else 0})", "active": "updateMinHashesForSample" == active_category, "available": "updateMinHashesForSample" in statistics},
                {"name": "updateMinHashes", "title": f"updateMinHashes ({sum(statistics['updateMinHashes'].values()) if 'updateMinHashes' in statistics else 0})", "active": "updateMinHashes" == active_category, "available": "updateMinHashes" in statistics},
                {"name": "rebuildIndex", "title": f"rebuildIndex ({sum(statistics['rebuildIndex'].values()) if 'rebuildIndex' in statistics else 0})", "active": "rebuildIndex" == active_category, "available": "rebuildIndex" in statistics},
                {"name": "recalculateMinHashes", "title": f"recalculateMinHashes ({sum(statistics['recalculateMinHashes'].values()) if 'recalculateMinHashes' in statistics else 0})", "active": "recalculateMinHashes" == active_category, "available": "recalculateMinHashes" in statistics},
                {"name": "recalculatePicHashes", "title": f"recalculatePicHashes ({sum(statistics['recalculatePicHashes'].values()) if 'recalculatePicHashes' in statistics else 0})", "active": "recalculatePicHashes" == active_category, "available": "recalculatePicHashes" in statistics},
            ]}, 
            {"group": "collection", "title": f"Collection ({summarized_groups['collection']})", "active": active_category in ["addBinarySample", "deleteSample", "modifySample", "deleteFamily", "modifyFamily"], "available": True, "submenu": [
                {"name": "addBinarySample", "title": f"addBinarySample ({sum(statistics['addBinarySample'].values()) if 'addBinarySample' in statistics else 0})", "active": "addBinarySample" == active_category, "available": "addBinarySample" in statistics},
                {"name": "deleteSample", "title": f"deleteSample ({sum(statistics['deleteSample'].values()) if 'deleteSample' in statistics else 0})", "active": "deleteSample" == active_category, "available": "deleteSample" in statistics},
                {"name": "modifySample", "title": f"modifySample ({sum(statistics['modifySample'].values()) if 'modifySample' in statistics else 0})", "active": "modifySample" == active_category, "available": "modifySample" in statistics},
                {"name": "deleteFamily", "title": f"deleteFamily ({sum(statistics['deleteFamily'].values()) if 'deleteFamily' in statistics else 0})", "active": "deleteFamily" == active_category, "available": "deleteFamily" in statistics},
                {"name": "modifyFamily", "title": f"modifyFamily ({sum(statistics['modifyFamily'].values()) if 'modifyFamily' in statistics else 0})", "active": "modifyFamily" == active_category, "available": "modifyFamily" in statistics},
            ]}, 
        ],
        "statistics": statistics
    }
    if active_category is None:
        max_count = statistics["totals"][state_category] if state_category in statistics["totals"] else 0
        pagination = Pagination(request, max_count, limit=25, query_param="p", limit_param="l")
    else:
        # getQueueStatistics only reports categories that have at least one job, so a
        # type that has never run - or whose jobs were all deleted through this page's
        # own per-category delete - is absent, and indexing it was a 500
        max_count = sum(statistics.get(active_category, {}).values()) if active_category else 0
        pagination = Pagination(request, max_count, limit=25, query_param="p")
    jobs = client.getQueueData(start=pagination.start_index, limit=pagination.limit, method=active_category, state=state_category, ascending=ascending)
    samples_by_id = {}
    families_by_id = {}
    if jobs:
        for job in jobs:
            if job.sample_ids is not None:
                for sample_id in [sid for sid in job.sample_ids if sid not in samples_by_id]:
                    samples_by_id[sample_id] = client.getSampleById(sample_id)
        for job in jobs:
            if job.family_id is not None:
                families_by_id[job.family_id] = client.getFamily(job.family_id)
    return render_template('jobs.html', families=families_by_id, samples=samples_by_id, active=active_category, state=state_category, jobs=jobs, menu_configuration=menu_configuration, p=pagination, query=query)


@bp.route('/jobs/<job_id>')
@visitor_required
@mcrit_server_required
def job_by_id(job_id):
    auto_refresh = 0
    auto_forward = 0
    client = get_client()
    suppress_processing_message = False
    try:
        auto_refresh = int(request.args.get("refresh"))
    except TypeError:
        pass
    try:
        auto_forward = int(request.args.get("forward"))
    except TypeError:
        pass

    job_info = client.getJobData(job_id)
    if auto_refresh and job_info and job_info.is_failed:
        auto_refresh = 0
        suppress_processing_message = True
        flash('The job failed!', category='error')

    if job_info is None:
        return render_template("job_invalid.html", job_id=job_id)

    if job_info.finished_at is not None:
        if auto_forward:
            if 'addBinarySample' in job_info.parameters:
                suppress_processing_message = True
                flash('Sample submitted successfully!', category='success')
            # `analyze.compare_function` asks for the parent sample's job but wants the
            # report filtered to one function, so the filter has to survive the forward
            # - otherwise it silently becomes the sample report again. See issue #35.
            # Parsed rather than reflected, so only an integer reaches the next URL.
            filtered_function_id = parse_integer_query_param(request, "funid")
            if filtered_function_id is not None:
                return redirect(url_for('data.result', job_id=job_id, funid=filtered_function_id))
            return redirect(url_for('data.result', job_id=job_id))
    if 'addBinarySample' in job_info.parameters and not suppress_processing_message and auto_refresh:
        flash('We received your sample, currently processing!', category='info')
    # a dependency can be gone by the time this page is opened - deleted through this
    # app's own job delete, which also has a "delete every job of this method" form, or
    # cleaned up in the backend - and getJobData answers None for it rather than raising.
    # Sorting that None on .number used to take the whole overview down with a 500.
    resolved_children = [client.getJobData(id) for id in job_info.all_dependencies]
    missing_children = sum(1 for job in resolved_children if job is None)
    child_jobs = sorted([job for job in resolved_children if job is not None], key=lambda x: x.number)
    samples_by_id = {}
    families_by_id = {}
    if child_jobs:
        for job in child_jobs:
            if job.sample_ids is not None:
                for sample_id in [sid for sid in job.sample_ids if sid not in samples_by_id]:
                    samples_by_id[sample_id] = client.getSampleById(sample_id)
        for job in child_jobs:
            if job.family_id is not None:
                families_by_id[job.family_id] = client.getFamily(job.family_id)
    # only a page that will poll needs it, so a finished job's page never reads it
    job_state = job_overview_state(job_info) if auto_refresh > 0 and not job_info.finished_at else None
    return render_template('job_overview.html', families=families_by_id, samples=samples_by_id, job_info=job_info, job_state=job_state, auto_refresh=auto_refresh, child_jobs=child_jobs, missing_children=missing_children)


def job_overview_state(job_info):
    """What the overview of a running job shows that can still change, read off the job
    alone - without its sub-jobs or the samples and families they name.

    The overview polls `job_status` for this and reloads once the answer differs from
    what it rendered. A reload fetches every sub-job and every sample they name, so it
    should happen when there is something new to show, not on every tick. See #183.
    """
    return {
        "started": job_info.started_at is not None,
        "finished": job_info.finished_at is not None,
        "failed": job_info.is_failed,
        "progress": job_info.progress,
        # the queue drops a sub-job from this list when it finishes, and also when it
        # fails for good (out of attempts, whether by its own error or by a dead worker),
        # so this counts the sub-jobs still to settle without asking for any of them. It
        # is in the job document the backend sends, but mcrit's Job has no property for
        # it, so it is read defensively: a Job without it costs this one reload trigger,
        # not the page.
        "unfinished_sub_jobs": len(getattr(job_info, "_data", {}).get("unfinished_dependencies") or []),
    }


@bp.route('/jobs/<job_id>/status')
@visitor_required
@mcrit_server_required
def job_status(job_id):
    """The job overview's polling endpoint: one getJobData, as JSON."""
    job_info = get_client().getJobData(job_id)
    if job_info is None:
        return {"error": "unknown job"}, 404
    return job_overview_state(job_info)


@bp.route('/jobs/<job_id>/delete', methods=('POST',))
@contributor_required
@mcrit_server_required
def delete_job_by_id(job_id):
    client = get_client()
    # deleting a job is destructive and irreversible, so it is worth a line in the
    # log - but at info, and naming the job rather than dumping the descriptor it
    # used to print in full on every delete.
    current_app.logger.info("deleting job %s", job_id)
    # the getJobData call that stood here fed nothing but the removed print - its
    # result was never read, so every deletion paid for a backend round-trip to
    # produce a line of stdout.
    if job_id.startswith("state_"):
        state = job_id.replace("state_", "")
        jobs = client.getQueueData(state=state)
        if jobs:
            for job in jobs:
                client.deleteJob(job.job_id)
    elif job_id.startswith("category_"):
        category = job_id.replace("category_", "")
        jobs = client.getQueueData(method=category)
        if jobs:
            for job in jobs:
                client.deleteJob(job.job_id)
    else:
        client.deleteJob(job_id)
    return redirect(url_for("data.jobs"))
    


################################################################
# Binary submission
################################################################

@bp.route('/request_filename_info', methods=['POST'])
@contributor_required
@mcrit_server_required
def request_filename_info():
    try:
        data_as_dict = json.loads(request.data)
        filename = data_as_dict["filename"]
        # the prefix carries base_addr and bitness; family and version only ever turn up
        # in the second window the client cuts around "metadata" (see dropzone.js).
        # .get so a client that predates that field still behaves exactly as before
        file_header = data_as_dict["file_header"] + data_as_dict.get("file_metadata", "")
    except Exception:
        filename = ""
        file_header = ""
    result = {}
    if filename.endswith(".smda"):
        result = {
            "smda": True,
            "family": None,
            "version": None,
            "bitness": None,
            "base_addr": None,
        }
        # parse from smda report
        match_family = re.search(r'"family": "(?P<family>[^"^<^>]+)"', file_header)
        if match_family:
            result['family'] = match_family.group('family')
        match_version = re.search(r'"version": "(?P<version>[^"^<^>]+)"', file_header)
        if match_version:
            result['version'] = match_version.group('version')
        match_bitness = re.search('"bitness": (?P<bitness>(16|32|64))', file_header)
        if match_bitness:
            result['bitness'] = int(match_bitness.group('bitness'))
        match_baseaddr = re.search(r'"base_addr": (?P<base_addr>\d+)', file_header)
        if match_baseaddr:
            result['base_addr'] = hex(int(match_baseaddr.group('base_addr')))
    elif 'dump' in filename:
        result['dump'] = True
        result['bitness'] = parseBitnessFromFilename(filename)
        base_address = parseBaseAddrFromFilename(filename)
        result['base_addr'] = "" if not base_address else hex(base_address)
    else:
        result['dump'] = False
    return json.dumps(result), 200


@bp.route('/submit_or_query', methods=('POST',))
@contributor_required
@mcrit_server_required
def submit_or_query():
    form_type = request.form['form_type']
    # NOTE: we do not use redirect to prevent resending of large file data
    if form_type == "query_form":
        return analyze_query()
        # return redirect(url_for("analyze.query"), code=307)
    elif form_type == "submit_form":
        return submit()
        # return redirect(url_for("data.submit"), code=307)


@bp.route('/submit',methods=('GET', 'POST'))
@contributor_required
@mcrit_server_required
def submit():
    client = get_client()
    if request.method == 'POST':
        f = request.files.get('file')
        if f is None:
            flash("Please upload a file", category='error')
            return "", 400 # Bad Request
        family = request.form['family']
        version = request.form['version']
        bitness = None
        base_address = None
        form_options = request.form['options']
        is_dump = form_options == "dumped"
        is_dump_or_smda = form_options in ['dumped', 'smda']
        if is_dump_or_smda:
            bitness = int(request.form['bitness'])
            base_address = int(request.form['base_addr'], 16)

        binary_content = f.read()
        if form_options == "smda":
            content_as_dict = json.loads(binary_content)
            smda_report = SmdaReport.fromDict(content_as_dict)
            upload_sha256 = smda_report.sha256
        else:
            # check here if it is already part of corpus
            upload_sha256 = hashlib.sha256(binary_content).hexdigest()
        sample_entry = client.getSampleBySha256(upload_sha256)
        if sample_entry is None:
            if form_options == "smda" and smda_report:
                new_sample_entry, job_id = client.addReport(smda_report)
                return url_for('explore.sample_by_id', sample_id=new_sample_entry.sample_id), 202 # Accepted
            else:
                with open(os.sep.join([current_app.instance_path, "temp", "uploads", upload_sha256]), "wb") as fout:
                    fout.write(binary_content)
                # These three ride in the query string McritClient builds by hand, so
                # they are escaped rather than trusted. All three are str by
                # construction - `f` is None-checked above, and both form fields would
                # have raised a 400 before here. The cost is length: a 255-character
                # non-ASCII filename encodes to ~2.3 kB of request line, against
                # gunicorn's 4094-byte default for the whole line.
                job_id = client.addBinarySample(binary_content,
                    filename=quote_backend_query_value(f.filename),
                    family=quote_backend_query_value(family),
                    version=quote_backend_query_value(version),
                    is_dump=is_dump, base_addr=base_address, bitness=bitness)
                return url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1), 202 # Accepted
        else:
            flash('Sample was already in database', category='warning')
            return url_for('explore.sample_by_id', sample_id=sample_entry.sample_id), 202 # Accepted
    all_families = client.getFamilies()
    family_names = [family_entry.family_name for family_entry in all_families.values()]
    return render_template('submit.html', families=family_names, show_submit_fields=True)


################################################################
# Promoting a query to a sample - issue #9
################################################################

#: The query job methods, mapped to the kind of upload each was made from. A query is
#: matched without ever being stored, so promoting one means resubmitting the same
#: bytes the same way they were queried.
QUERY_UPLOAD_KINDS = {
    "getMatchesForUnmappedBinary": "unmapped",
    "getMatchesForMappedBinary": "dumped",
    "getMatchesForSmdaReport": "smda",
}

#: What may be submitted as a family or a version. `McritClient.addBinarySample`
#: builds its request by concatenating these into a query string without
#: percent-encoding, so a value carrying '&' or '=' would append parameters of its own
#: to the backend call. Anything outside this set is refused rather than escaped,
#: because escaping it correctly depends on internals of a client we do not own.
#: The same reasoning keeps out characters that arrive as something else: '+' is safe
#: per `requote_uri`, so `requests` leaves it in the URL, and Falcon decodes a query
#: string with `unquote_plus` - "win.a+b" would be stored as "win.a b", and only on
#: this path, since the .smda path posts the report as JSON and keeps it. A space is
#: sent as %20 and does arrive as one, so it stays.
#: `\Z` rather than `$`, which would also match before a trailing newline.
PROMOTION_METADATA = re.compile(r"^[A-Za-z0-9 ._-]{0,64}\Z")


def query_upload_exists(app, job_id):
    """Whether a query can still be promoted, i.e. whether its bytes are still here.

    The bytes of a query live in the backend's GridFS behind a job reference, and the
    backend exposes no route that reads a job's *input* back, so the copy
    `analyze.query` keeps is the only one that can be resubmitted. It follows that a
    query is promotable only on the host that received it, and only when it arrived
    through the web upload - `api.api_router` never writes that file.

    It is filed under the job id, which is why nothing here has to reason about hashes
    to find it. `utility.query_upload_path` is the single definition of that name, and
    carries the reason it is not a hash: the sha256 a query report records is the one
    the uploaded .smda report declared about itself, so naming the file by it let one
    visitor overwrite another user's stored query - and a digest of the uploaded bytes,
    which fixes that, is a value nothing on this side of the feature can reconstruct.
    """
    upload_path = query_upload_path(app, job_id)
    return upload_path is not None and os.path.isfile(upload_path)


def query_payload_sha256(job_info):
    """The sha256 the backend recorded for the payload a query ran on, or None.

    `QueueRemoteCalls` hashes every parameter it ships through GridFS and writes the
    hashes into the job's descriptor, which `Job.sha256` reads back for exactly the
    three query methods. That is the only statement about the queried bytes that a
    promotion does not get from the file it is about to resubmit, and it is worth
    having even now that the file is named by the job rather than by anything the
    upload chose: the folder is shared, unpruned, and a report checked against a field
    of its own only ever agrees with itself. This hash was taken over what the job
    actually ran on.

    The descriptor is job data from the backend, so it is read defensively rather than
    trusted to be shaped as expected.
    """
    try:
        return job_info.sha256
    except (LookupError, TypeError, ValueError):
        current_app.logger.warning("promote_query - job %s records no payload hash", job_info.job_id)
        return None


def query_report_base_address(sample_info):
    """The address a dumped query was mapped at, as an address again, or None.

    `SampleEntry.toDict` writes `base_addr` through `encode_two_complement`, so a dump
    mapped above 0x7fffffffffffffff - which is where every Windows kernel-mode dump
    sits - is recorded as a negative number. Read as one it is not an address at all,
    so it is decoded back into the unsigned 64-bit range it was encoded from, and a
    value that does not land there is refused rather than resubmitted somewhere else.
    """
    base_address = sample_info.get("base_addr")
    if not isinstance(base_address, int) or isinstance(base_address, bool):
        return None
    base_address = decode_two_complement(base_address)
    return base_address if 0 <= base_address < 0x10000000000000000 else None


def query_report_sample_info(client, job_info):
    """The `info.sample` block of a job's report, or an empty dict.

    Everything a promotion needs about the queried sample is here: the sha256 that
    names the stored upload, and - for a dump - the base address and bitness it was
    queried under, without which the backend would disassemble it differently than
    the report on screen describes. The job payload records neither.
    """
    result_json = load_cached_result(current_app, job_info.job_id)
    if not result_json:
        result_json = client.getResultForJob(job_info.job_id)
    if not isinstance(result_json, dict):
        return {}
    info = result_json.get("info")
    sample_info = info.get("sample") if isinstance(info, dict) else None
    return sample_info if isinstance(sample_info, dict) else {}


@bp.route('/promote_query/<job_id>', methods=('POST',))
@contributor_required
@mcrit_server_required
def promote_query(job_id):
    """Add the file a query was run for to the corpus, without a second upload."""
    client = get_client()
    job_info = client.getJobData(job_id)
    if job_info is None:
        flash("The given Job ID doesn't exist", category='error')
        return redirect(url_for('data.jobs'))
    result_page = url_for('data.result', job_id=job_info.job_id)
    if job_info.method not in QUERY_UPLOAD_KINDS:
        flash('Only a query can be promoted to a sample.', category='error')
        return redirect(result_page)
    upload_path = query_upload_path(current_app, job_info.job_id)
    if upload_path is None:
        flash('This job cannot be promoted.', category='error')
        return redirect(result_page)
    sample_info = query_report_sample_info(client, job_info)
    # the sample's declared sha256 no longer names the stored file. It is only what the
    # corpus is asked about below; what was actually resubmitted is checked against the
    # job descriptor further down instead.
    upload_sha256 = sample_info.get("sha256")
    upload_sha256 = upload_sha256.lower() if isinstance(upload_sha256, str) else None
    if upload_sha256 is None:
        flash('The report of this query does not record which file it was run for, so it cannot be promoted.', category='error')
        return redirect(result_page)
    # whether the local copy survived or not, a sample that is already stored is the
    # answer to "promote this" - so promoting twice lands on it instead of adding it.
    # Two promotions racing past this check still make one sample, but they are not
    # told the same thing: addReport answers a known sha256 with the entry that already
    # exists, while SampleResource.on_post_submit_binary refuses one with 409, which
    # handle_response turns into None - so the second promotion of a binary query says
    # the sample could not be added. It is one sample all the same: the corpus is
    # checked again by Worker.addBinarySample when the job runs.
    sample_entry = client.getSampleBySha256(upload_sha256)
    if sample_entry is not None:
        flash('Sample was already in database', category='warning')
        return redirect(url_for('explore.sample_by_id', sample_id=sample_entry.sample_id))
    if not os.path.isfile(upload_path):
        flash('The file this query was run for is no longer available on this server, so it cannot be promoted. Please submit it again.', category='error')
        return redirect(result_page)
    family = request.form.get('family', '').strip()
    version = request.form.get('version', '').strip()
    for field_name, field_value in (('family', family), ('version', version)):
        if not PROMOTION_METADATA.match(field_value):
            flash(f'The {field_name} may only contain up to 64 letters, digits, spaces, or any of ". _ -".', category='error')
            return redirect(result_page)
    with open(upload_path, "rb") as fin:
        upload_content = fin.read()
    upload_kind = QUERY_UPLOAD_KINDS[job_info.method]
    smda_report = None
    stored_sha256 = None
    if upload_kind == "smda":
        try:
            # the backend was handed the report, not the file: McritClient posts
            # toDict(), and QueueRemoteCalls hashes the canonicalisation of that
            smda_report = SmdaReport.fromDict(json.loads(upload_content))
            stored_sha256 = hashlib.sha256(canonicalise_queue_json(smda_report.toDict())).hexdigest()
        except Exception:
            # the uploads folder holds uploaded files, so a stored report that no
            # longer reads back is normal input and has to become a message, not a 500
            current_app.logger.warning("promote_query - could not read the stored SMDA report of job %s", job_info.job_id)
    else:
        stored_sha256 = hashlib.sha256(upload_content).hexdigest()
    # the file is only this query's input if it hashes to what the backend recorded for
    # it. Nothing weaker will do: for an .smda query the name it is filed under is one
    # the upload chose, so it is no evidence at all about the bytes it names.
    payload_sha256 = query_payload_sha256(job_info)
    if stored_sha256 is None or payload_sha256 is None or stored_sha256 != payload_sha256:
        flash('The stored copy of this query no longer matches it, so it was not promoted.', category='error')
        return redirect(result_page)
    if upload_kind == "smda":
        if family:
            smda_report.family = family
        if version:
            smda_report.version = version
        # the client answers (SampleEntry, job_id), or None when the backend refused
        added = client.addReport(smda_report)
        new_sample_entry = added[0] if isinstance(added, tuple) and added else None
        if new_sample_entry is None:
            flash('The sample could not be added to the database.', category='error')
            return redirect(result_page)
        flash('The query was promoted to a sample.', category='success')
        return redirect(url_for('explore.sample_by_id', sample_id=new_sample_entry.sample_id))
    is_dump = upload_kind == "dumped"
    base_address = None
    bitness = None
    if is_dump:
        base_address = query_report_base_address(sample_info)
        bitness = sample_info.get("bitness")
        if base_address is None or bitness not in [32, 64]:
            flash('The report of this query no longer records how the dump was mapped, so it cannot be promoted.', category='error')
            return redirect(result_page)
    new_job_id = client.addBinarySample(upload_content, family=family or None, version=version or None, is_dump=is_dump, base_addr=base_address, bitness=bitness)
    if not new_job_id:
        flash('The sample could not be added to the database.', category='error')
        return redirect(result_page)
    flash('The query was promoted to a sample.', category='success')
    return redirect(url_for('data.job_by_id', job_id=new_job_id, refresh=3, forward=1))
