import copy
import hashlib
import inspect
import os
import re
import types
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode

from flask import Blueprint, Response, current_app, flash, g, json, redirect, render_template, request, send_from_directory, session, url_for
from mcrit.client.McritClient import McritClient
from mcrit.config.MinHashConfig import MinHashConfig
from mcrit.config.StorageConfig import StorageConfig
from mcrit.libs.utility import decode_two_complement
from mcrit.queue.LocalQueue import Job
from mcrit.queue.QueueRemoteCalls import to_binary as canonicalise_queue_json
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.MatchedFunctionEntry import MatchedFunctionEntry
from mcrit.storage.MatchingResult import MatchingResult
from mcrit.storage.SampleEntry import SampleEntry
from mcrit.storage.UniqueBlocksResult import UniqueBlocksResult, wrap_string
from smda.common.SmdaReport import SmdaReport
from werkzeug.exceptions import NotFound

from mcritweb.backend_errors import require_result, with_server_errors
from mcritweb.db import UserColumnSettings, UserFilters, get_query_filename, utc_now
from mcritweb.views.analyze import query as analyze_query
from mcritweb.views.authentication import contributor_required, visitor_required
from mcritweb.views.client import get_client, get_family_entries, get_sample_entries
from mcritweb.views.cross_compare import CROSS_ORDERINGS, get_sample_to_job_id, lazy_cross_panes, order_sample_ids, score_to_color
from mcritweb.views.functiondiff import get_function_diff
from mcritweb.views.MatchReportRenderer import (
    DEFAULT_DIAGRAM_VARIANT,
    DIAGRAM_PALETTES,
    MatchReportRenderer,
    count_diagram_blocks,
    stacked_diagram_size,
)
from mcritweb.views.memo import app_memo
from mcritweb.views.pagination import Pagination
from mcritweb.views.params import (
    parse_base_addr_form_param,
    parse_bitness_form_param,
    parse_checkbox_query_param,
    parse_choice_query_param,
    parse_integer_list_query_param,
    parse_integer_query_param,
    parse_str_query_param,
    parseBaseAddrAndBitnessFromFilename,
    parseIsDumpFromFilename,
    slider_position_for_band_range,
)
from mcritweb.views.result_sorting import (
    AGGREGATED_FUNCTION_SORT_KEYS,
    FAMILY_SAMPLE_SORT_KEYS,
    MATCHED_FUNCTION_SORT_KEYS,
    sorted_page,
)
from mcritweb.views.ScoreColorProvider import ScoreColorProvider
from mcritweb.views.utility import (
    describable_jobs,
    get_session_user_id,
    job_parameters_or_blank,
    job_parameters_or_none,
    mcrit_server_required,
    query_upload_path,
)

bp = Blueprint('data', __name__, url_prefix='/data')

################################################################
# Helper functions
################################################################

#: A job id as the backend hands it out: a mongo ObjectId (24 hex), which a local
#: queue mints too since mcrit 1.14 - before that it was a uuid4, and setup.py still
#: allows those releases. A cached report is named after its job id, and the id
#: arrives straight from the URL - so keep it to characters that cannot leave the
#: cache directory (no os.sep, no ".."), and to a length a filename can hold. `\Z`,
#: not `$`, which also matches before a trailing newline - the convention master's
#: own JOB_ID_PATTERN states for the download route.
JOB_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}\Z")


def is_cacheable_job_id(job_id):
    """Whether `job_id` may be pasted into a cache filename.

    Anything else is simply not cached and not looked up - the report is then
    fetched from the backend, which is slower but always correct.
    """
    return isinstance(job_id, str) and JOB_ID_RE.match(job_id) is not None


#: The palettes a diagram has a file of its own for besides the default one, which keeps
#: the name it had before there was more than one: "<name>.dark.png" is the diagram of
#: "<name>.png" drawn in the dark palette (#70). The default's own name is not accepted
#: with a variant in it (no ".light"), as that would be a second name, and a second
#: cache file, for the same picture.
DIAGRAM_VARIANTS = tuple(variant for variant in DIAGRAM_PALETTES if variant != DEFAULT_DIAGRAM_VARIANT)


#: The diagram filenames create_match_diagram writes and the four result templates
#: ask for: "<job_id>.png", or "<job_id>-famid_7.png" / "-samid_7" / "-funid_7", each
#: of them optionally with a variant in front of the extension ("<job_id>.dark.png").
#: A job id has no dot in it, so the variant cannot be taken for part of one. The
#: variants come from the palettes the renderer has, so a name is accepted exactly when
#: it can be drawn.
#: The job id is lazy so that the optional filter suffix is preferred over letting
#: the id swallow it - a uuid4 job id, which a local queue issued before mcrit 1.14,
#: contains dashes, so a greedy id would read "<uuid>-famid_7.png" as one long
#: unfiltered id. Query reports number their functions negatively, hence the
#: optional sign.
#: The filter id is exactly how str(int) spells it, because the whole point of
#: recognising the name is to render the file that was asked for: "0001" and "-0"
#: would be rendered as "-famid_1" and "-funid_0", leaving the requested name still
#: missing and the report re-parsed on every hit for as long as anyone asked. Off
#: this grammar they are simply part of a job id nobody has, which is a backend miss
#: and no report parse - the same as any other name the app never wrote.
DIAGRAM_FILENAME_RE = re.compile(
    r"^(?P<job_id>[0-9A-Za-z_-]{1,64}?)"
    r"(?:-(?P<filter_kind>famid|samid|funid)_(?P<filter_id>0|-?[1-9][0-9]{0,17}))?"
    r"(?:\.(?P<variant>" + "|".join(re.escape(variant) for variant in DIAGRAM_VARIANTS) + r"))?"
    r"\.png\Z"
)


#: The width of the strftime prefix cache_result puts in front of the job id.
CACHE_TIMESTAMP_LENGTH = len("20260806-104636")
#: How a pretty-printed report begins: json.dump with indent=1 breaks the line after the
#: opening bracket of an object or a list, and compact output never does.
PRETTY_PRINTED_REPORT_STARTS = (b"{\n", b"[\n")
#: The last byte of a whole compact object or list.
CLOSING_BYTES = {b"{": b"}", b"[": b"]"}


def quote_backend_query_value(value):
    """Percent-encode one value for a query string `McritClient` builds by hand.

    `safe=""` escapes every character outside the unreserved set, and escapes nothing
    inside it. That is exactly the set `requests.requote_uri` leaves alone on the way
    to the wire, so no escape written here is undone and none is written twice:
    "100%" stays "100%", "%41" stays "%41", "C++_sample.exe" keeps its plusses.

    Correct only while the client concatenates rather than passing `params=` to
    requests, which would encode these a second time. `pyproject.toml` has no ceiling
    on mcrit, so tests/testSubmitMetadata.py asserts the concatenation is still there.

    Used for `addBinarySample`'s three text fields. `getQueueData` and
    `deleteQueueData` build their query strings the same way and are reached with
    unvalidated values through `api.api_router`; that is a separate change.
    """
    return quote(str(value), safe="")


def trim_result_cache(app):
    """Evict the oldest cached reports until instance/cache/results/ is back within
    RESULT_CACHE_MAX_BYTES and RESULT_CACHE_MAX_FILES.

    Oldest by modification time, which is when cache_result wrote the file. The
    timestamp in the filename says the same to the second; mtime breaks the ties that
    leaves within a second, and orders a file cache_result did not name as well. That is
    oldest-written rather than least-recently-used, because a cache hit does not touch
    the file; evicting a report that is still popular costs one fetch from the backend,
    after which it is the newest again. A single report larger than the byte bound is
    evicted straight away - the page it was fetched for is rendered from memory anyway.
    Called after every write, so the directory never holds more than one report past
    its bounds. A deployment upgrading with more reports than that is trimmed in full
    by its first write.

    The directory is shared by concurrent requests and, under a multi-process server, by
    other processes, so nothing is kept in memory between calls. Two trims racing for the
    same victim is harmless: whichever comes second finds it gone. A request still
    reading an evicted report keeps its open file on POSIX; only one that has listed the
    directory but not yet opened the file misses it.
    """
    max_bytes = app.config.get("RESULT_CACHE_MAX_BYTES")
    max_files = app.config.get("RESULT_CACHE_MAX_FILES")
    if max_bytes is None and max_files is None:
        return
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    cached = []
    with os.scandir(cache_path) as entries:
        for entry in entries:
            try:
                if entry.is_file():
                    stat = entry.stat()
                    cached.append((stat.st_mtime_ns, entry.name, stat.st_size))
            except FileNotFoundError:
                # evicted by a concurrent trim since the listing
                continue
    num_bytes = sum(size for _, _, size in cached)
    num_files = len(cached)
    for _, filename, size in sorted(cached):
        if (max_bytes is None or num_bytes <= max_bytes) and (max_files is None or num_files <= max_files):
            break
        try:
            os.remove(os.sep.join([cache_path, filename]))
        except FileNotFoundError:
            # a concurrent trim got there first, which is just as good
            pass
        except OSError:
            # Windows refuses to remove a file that is open for reading; the next
            # trim will have another go at it
            app.logger.warning("Could not evict cached result %s", filename, exc_info=True)
            continue
        num_bytes -= size
        num_files -= 1


def load_cached_result(app, job_id):
    """The most recently cached report for `job_id`, or {} if there is none.

    Cache files are named "<utc timestamp>-<job id>.json", which the old code looked
    for with `job_id in filename`. That answered for any id occurring anywhere in a
    name - inside a longer id, or inside the timestamp, where "2026" matches every
    file ever written - and, having no reason to stop, it json.load()ed every match
    and kept the last. A job re-fetched a handful of times therefore cost a full
    parse of its report per cached copy. Matching the id against exactly the part of
    the name it occupies, and reading only the newest hit, fixes both. See issue #68.
    """
    if not is_cacheable_job_id(job_id):
        return {}
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    suffix = f"-{job_id}.json"
    # the length test is what keeps a *partial* id from matching on a dash boundary,
    # which "endswith" alone would allow for the uuid4 ids a local queue handed out
    # before mcrit 1.14
    expected_length = CACHE_TIMESTAMP_LENGTH + len(suffix)
    cached = [name for name in os.listdir(cache_path) if len(name) == expected_length and name.endswith(suffix)]
    # the timestamp prefix is fixed-width, so lexical order is chronological order
    for name in sorted(cached, reverse=True):
        path = os.sep.join([cache_path, name])
        try:
            with open(path) as fin:
                return json.load(fin)
        except (OSError, ValueError):
            # a cache file we cannot read is not a reason to fail the page - fall
            # through to the next one, and ultimately to fetching from the backend
            app.logger.exception("Ignoring unreadable cached result %s", path)
    return {}


def find_cached_result_filename(app, job_id):
    """Name of the newest cached report for a job that can be served unread, or None.

    Matches the whole `<timestamp>-<job_id>.json` name that cache_result writes, as
    load_cached_result does: this file is handed to the caller as-is, so a short or
    crafted job_id must not be able to select a report that merely contains it. The
    timestamp prefix sorts chronologically, so the newest capture wins for a job that
    has been fetched more than once.

    Unread also means unchecked, so a pretty-printed report does not qualify. It was
    written in place by an older version and may have been cut short by a crash; only
    parsing it can tell, which would double the cost of every download, so it is left
    to the caller to load like any other cached report. cache_result has written
    compact only for as long as it has written through write_atomically - the two
    reached master in one merge - so a compact file was renamed into place whole. Its
    last byte is checked all the same, which is one seek: it also catches a file the
    pre-merge branch of #229 wrote compact but in place. A scalar result (`true`, a
    count) is a few bytes, written whole or not at all, and qualifies as it is.
    """
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    suffix = f"-{job_id}.json"
    # the length test too, as in load_cached_result: `endswith` alone lets the tail of
    # a uuid4 job id - which has dashes of its own - select another job's report
    expected_length = CACHE_TIMESTAMP_LENGTH + len(suffix)
    candidates = [filename for filename in os.listdir(cache_path) if len(filename) == expected_length and filename.endswith(suffix)]
    for filename in sorted(candidates, reverse=True):
        try:
            with open(os.sep.join([cache_path, filename]), "rb") as fin:
                head = fin.read(2)
                if not head or head in PRETTY_PRINTED_REPORT_STARTS:
                    continue
                closing = CLOSING_BYTES.get(head[:1])
                if closing is not None:
                    fin.seek(-1, os.SEEK_END)
                    if fin.read(1) != closing:
                        continue
                return filename
        except OSError:
            # evicted since the listing, or unreadable: not one to hand out either way
            continue
    return None


def cache_result(app, job_info, matching_result):
    if job_info.result is not None and is_cacheable_job_id(job_info.job_id):
        cache_path = os.sep.join([app.instance_path, "cache", "results"])
        timestamped_filename = utc_now().strftime(f"%Y%m%d-%H%M%S-{job_info.job_id}.json")
        # the filename is only second-resolution, so two requests for the same job can
        # pick the same one - write elsewhere and rename, or a reader gets half a file
        # compact: the file is only ever parsed, or streamed as the download, and
        # indentation made it about 1.5 times the size on disk and on the wire. The
        # download streams a compact file unchecked (find_cached_result_filename), so
        # never write one other than through write_atomically
        write_atomically(app, cache_path, timestamped_filename, lambda fout: json.dump(matching_result, fout, separators=(",", ":")), "w")
        try:
            trim_result_cache(app)
        except Exception:
            # bookkeeping: the report is written and the page is rendered from memory,
            # so a trim that fails (an unreadable directory, say) must not fail it
            app.logger.exception("Could not trim the result cache")


#: The permissions a cached file is asked for with, before the process umask is
#: applied to them. The same 0666 a plain `open(path, "w")` asks for, so a cached
#: report or diagram keeps the mode the in-place writes left it with - 0644 under a
#: default umask, and whatever the operator chose otherwise. `tempfile.mkstemp`
#: cannot do this job: it hard-codes 0600 because it is built for files that are
#: secrets, and neither of these is one - both are derived from a report the app
#: serves. The umask cannot be read back either, only temporarily set to zero, which
#: is process-wide and would hand a concurrent request a world-writable file.
CACHE_FILE_MODE = 0o666


def incomplete_cache_path(app):
    """Where a cache file lives until it is complete.

    A sibling of the two cache directories rather than a subdirectory of either,
    because `diagram_file` serves every name under cache/diagrams: a temporary file
    there is fetchable under its own name for the whole render window, and after a
    SIGKILL forever - the partial file this is all here to prevent, in a new shape.
    Being under the same instance/cache is also what keeps os.replace a rename
    rather than a copy, so the two must stay on one filesystem.
    """
    return os.sep.join([app.instance_path, "cache", "incomplete"])


def write_atomically(app, directory, filename, write, mode="wb"):
    """Hand `write` a handle on a temporary file, then rename it into place.

    Both caches are written while other requests are reading them, and a diagram in
    particular takes long enough to render that a reader can easily catch a partial
    file - which would then be served, and browser-cached, as a truncated image.
    os.replace is atomic, so a reader sees either no file or a complete one.
    """
    temp_directory = incomplete_cache_path(app)
    os.makedirs(temp_directory, exist_ok=True)
    temp_path = os.sep.join([temp_directory, uuid.uuid4().hex + ".part"])
    try:
        # opened through `open` rather than tempfile, so that the permissions come out
        # the way the in-place writes left them; the opener adds O_EXCL, so a name
        # that somehow already exists is an error rather than a silent clobber
        # newline="" for the text modes. The cached report is served back verbatim as
        # the raw result, so it has to be byte-for-byte what json.dump wrote - and text
        # mode rewrites the newline on the way out, which on Windows makes every one a
        # CRLF and the served file stop matching the bytes it claims to be. Harmless on
        # Linux, where text mode translates nothing, so CI cannot see this either way.
        # `newline` is not a valid argument in binary mode, hence the conditional.
        text_kwargs = {} if "b" in mode else {"newline": ""}
        with open(temp_path, mode, opener=lambda path, flags: os.open(path, flags | os.O_EXCL, CACHE_FILE_MODE), **text_kwargs) as fout:
            write(fout)
            # on disk before the rename: renamed first, a power loss can leave the new
            # name on a file whose data never got there, and the download streams a
            # compact report without reading it
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(temp_path, os.sep.join([directory, filename]))
    finally:
        # os.replace consumed it on the success path; this is the failure path
        if os.path.isfile(temp_path):
            os.remove(temp_path)


def match_diagram_size(result_json):
    """The pixel size the diagram of `result_json` renders to, or None if unknown.

    The diagram is no longer drawn during the page request - the browser fetches it
    from data.diagram_file afterwards - so the page has to reserve the box it will
    land in, or the tables below it jump down when it arrives. That box is decided
    entirely by how many instruction blocks the reference sample's matchable
    functions add up to, and the report already carries the instruction count of
    every one of them: mcrit's `_summarizeMatches` writes an entry per function of
    the reference sample, matched or not. So the size is known without asking the
    backend anything.

    MatchReportRenderer reads those instruction counts from
    `getFunctionsBySampleId(reference sample)` instead, which is the same list - it
    is what the matcher summarised the report from (mcrit MatcherSample/MatcherVs).
    The two can only disagree if the sample was deleted after the job ran, and then
    the reservation is merely the wrong size: the browser takes the image's own
    dimensions once it has loaded.

    None for a query report, whose reference sample is not stored: the renderer then
    has no function entries at all and draws an empty diagram, and pinning a size to
    that is not worth the guess.
    """
    function_summaries = result_json.get("matches", {}).get("functions") if isinstance(result_json, dict) else None
    if not function_summaries:
        return None
    # same test MatchingResult.fromDict decides is_query by - a query's functions are
    # numbered negatively, and its reference sample is not one the backend stores
    if any(function_summary["fid"] < 0 for function_summary in function_summaries):
        return None
    return stacked_diagram_size(count_diagram_blocks([function_summary["num_instructions"] for function_summary in function_summaries]))


def diagram_variant_suffix(variant):
    """What the palette `variant` adds in front of a diagram's extension: nothing for
    the default one, so that its files keep the names they were cached under."""
    if variant == DEFAULT_DIAGRAM_VARIANT:
        return ""
    if variant in DIAGRAM_VARIANTS:
        return "." + variant
    raise ValueError(f"No diagram palette named {variant!r}")


def create_match_diagram(app, job_id, matching_result, filtered_family_id=None, filtered_sample_id=None, filtered_function_id=None, variant=DEFAULT_DIAGRAM_VARIANT):
    cache_path = os.sep.join([app.instance_path, "cache", "diagrams"])
    filter_suffix = ""
    if filtered_family_id is not None:
        filter_suffix = f"-famid_{filtered_family_id}"
    elif filtered_sample_id is not None:
        filter_suffix = f"-samid_{filtered_sample_id}"
    elif filtered_function_id is not None:
        filter_suffix = f"-funid_{filtered_function_id}"
    output_filename = job_id + filter_suffix + diagram_variant_suffix(variant) + ".png"
    output_path = cache_path + os.sep + output_filename
    if not os.path.isfile(output_path):
        renderer = MatchReportRenderer()
        renderer.processReport(matching_result)
        image = renderer.renderStackedDiagram(filtered_family_id=filtered_family_id, filtered_sample_id=filtered_sample_id, filtered_function_id=filtered_function_id, variant=variant)
        write_atomically(app, cache_path, output_filename, lambda fout: image.save(fout, format="PNG"))
        # `app`, not `current_app`: this function takes an app precisely so it does
        # not depend on a request context, and uses app.instance_path fifteen lines up.
        app.logger.debug("stored new MCRIT diagram: %s", output_path)


def render_missing_match_diagram(app, filename_match):
    """Render the diagram `filename_match` names, so that diagram_file can serve it.

    Rendering used to happen inline in result_matches_for_sample_or_query, before
    render_template, which put the whole PIL render plus a getFunctionsBySampleId
    round trip in front of the HTML of the first view of every result page - by far
    the largest part of it (issue #68). The image already had its own route and its
    own <img> request, so the work belongs here: the page now returns immediately
    and the browser fetches the diagram alongside it.

    The report is re-read rather than passed along, which is sound because the
    renderer only ever looks at the unfiltered `function_matches`, `library_matches`
    and `reference_sample_entry` - the filtering the view applies before rendering
    today does not reach any of them. tests/testMatchDiagrams.py pins that down.
    """
    job_id = filename_match.group("job_id")
    filter_kind = filename_match.group("filter_kind")
    filter_id = int(filename_match.group("filter_id")) if filter_kind else None
    # the palette comes from the name that was asked for and from nothing else - not
    # from who is asking: this file is cached under that name for everyone
    variant = filename_match.group("variant") or DEFAULT_DIAGRAM_VARIANT
    client = get_client()
    result_json = load_cached_result(app, job_id)
    if not result_json:
        result_json = client.getResultForJob(job_id)
    if not isinstance(result_json, dict) or not result_json:
        return
    matching_result = MatchingResult.fromDict(result_json)
    # only a family or sample this report matched, which is every filter the result
    # page offers a link for - so a hand-written URL cannot fill the unbounded diagram
    # cache with one render per family or sample in the corpus for each job. The
    # report answers it without the isFamilyId/isSampleId round trips this replaced.
    if filter_kind == "famid" and filter_id not in {sample_match.family_id for sample_match in matching_result.sample_matches}:
        return
    if filter_kind == "samid" and filter_id not in {sample_match.sample_id for sample_match in matching_result.sample_matches}:
        return
    # the same for a function id, which the report can answer by itself. A query
    # report additionally has no stored functions to lay a diagram out over, so the
    # function-filtered variant was never generated for one and still is not
    if filter_kind == "funid" and (matching_result.is_query or filter_id not in matching_result.function_id_to_family_ids_matched):
        return
    create_match_diagram(
        app,
        job_id,
        matching_result,
        filtered_family_id=filter_id if filter_kind == "famid" else None,
        filtered_sample_id=filter_id if filter_kind == "samid" else None,
        filtered_function_id=filter_id if filter_kind == "funid" else None,
        variant=variant,
    )

# https://stackoverflow.com/a/39842765
# https://stackoverflow.com/a/26972238
# https://flask.palletsprojects.com/en/1.0.x/api/#flask.send_from_directory
@bp.route('/diagrams/<path:filename>')
@visitor_required
def diagram_file(filename):
    cache_path = os.sep.join([current_app.instance_path, "cache", "diagrams"])
    filename_match = DIAGRAM_FILENAME_RE.match(filename)
    if filename_match is not None:
        # the job id the way the backend mints it, as for the result pages (see
        # JOB_ID_PATTERN) - or every casing of one id is a render and a file of its own
        filename = filename.lower()
        filename_match = DIAGRAM_FILENAME_RE.match(filename)
    # only a name this route itself could have produced is worth rendering; anything
    # else - including a diagram cached under a name we no longer generate - is left
    # to send_from_directory, which serves it if it is there and 404s if it is not
    if filename_match is not None and not os.path.isfile(os.sep.join([cache_path, filename])):
        try:
            render_missing_match_diagram(current_app, filename_match)
        except Exception:
            # a diagram is decoration on a page that has already been served, and
            # every reason it can fail (a job id naming a report of some other kind,
            # a backend that went away, a report referring to a deleted sample) ends
            # in a broken image rather than the 500 on the whole page it used to be
            current_app.logger.exception("Could not render match diagram %s", filename)
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
        except (KeyError, ValueError, RecursionError):
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


#: How mcrit's export routes wrap an export: `jsonify({"status": "successful", "data": ...})`,
#: which serialises with json's default separators, so the export itself is what lies
#: between these two.
EXPORT_ENVELOPE = (b'{"status": "successful", "data": ', b"}")
#: The size of the pieces a passed-through export is sent in.
EXPORT_CHUNK_SIZE = 1 << 20
#: How many samples an export holds, read off its first bytes: MinHashIndex.getExportData
#: puts its counts first, as a flat object, and the backend keeps that order on the wire.
EXPORT_SAMPLE_COUNT_RE = re.compile(rb'\{"content": \{[^{}]*"num_samples": ([0-9]+)')


def fetch_export(sample_ids=None, require_samples=False):
    """The export of `sample_ids` (every sample for None) as its JSON bytes in pieces, with
    their total length, or None when the backend did not export - or, with
    `require_samples`, exported no sample at all.

    Parsing a whole-corpus export into a dict and serialising it again held both at once
    (#202). Asked in raw mode, the client hands over the backend's response, and the
    export is its body minus the envelope: it is passed on as it came, in pieces, without
    another copy of it being built. An mcrit client that ignores `raw_responses` for
    exports, as 1.9.0's does, answers the parsed export instead, which is serialised as
    before.
    """
    exported = get_client(raw_responses=True).getExportData(sample_ids)
    if isinstance(exported, dict):
        if require_samples and not exported.get("content", {}).get("num_samples"):
            return None
        serialised = json.dumps(exported).encode("utf-8")
        return [serialised], len(serialised)
    if exported is None or exported.status_code != 200:
        return None
    body = exported.content
    head, tail = EXPORT_ENVELOPE
    # a failed export answers its own envelope, `"status": "failed"`
    if not (body.startswith(head) and body.endswith(tail)):
        return None
    if require_samples:
        sample_count = EXPORT_SAMPLE_COUNT_RE.match(body, len(head))
        # an export that does not open with its counts is passed on rather than refused:
        # it was asked for by id, so it is not every sample whatever it holds
        if sample_count is not None and not int(sample_count.group(1)):
            return None
    export = memoryview(body)[len(head):len(body) - len(tail)]
    pieces = (bytes(export[offset:offset + EXPORT_CHUNK_SIZE]) for offset in range(0, len(export), EXPORT_CHUNK_SIZE))
    return pieces, len(export)


def export_download(export, filename):
    """The download of what `fetch_export` answered. It says its length, which Werkzeug
    cannot count in advance for pieces that are still to be cut, so the browser can show
    the size and the progress of the download."""
    pieces, length = export
    return Response(
        pieces,
        mimetype='application/json',
        headers={"Content-disposition": "attachment; filename=" + filename, "Content-Length": str(length)})


@bp.route('/export',methods=('GET', 'POST'))
@contributor_required
@mcrit_server_required
def export_view():
    if request.method == 'POST':
        requested_samples = request.form['samples']
        if requested_samples == "":
            export_file = fetch_export()
            if export_file is None:
                flash('MCRIT did not export the samples - it could not export them, or the export was too large.', category='error')
                return render_template("export.html")
            return export_download(export_file, "export_all_samples.json")
        # NOTE it might be nice to allow [<number>, <number>-<number>, ...] to enable 
        # spans of consecutive sample_ids
        elif re.match(r"^\d+(?:[\s]*,[\s]*\d+)*$", requested_samples):
            sample_ids = [int(sample_id.strip()) for sample_id in requested_samples.split(',')]
            export_file = fetch_export(sample_ids)
            if export_file is None:
                flash('MCRIT did not export the samples - it could not export them, or the export was too large.', category='error')
                return render_template("export.html")
            return export_download(export_file, "export_samples.json")
        else:
            flash('Please use a comma-separated list of sample_ids in your export request.', category='error')
            return render_template("export.html")
    return render_template("export.html")

@bp.route('/specific_export/<type>/<item_id>')
@contributor_required
@mcrit_server_required
def specific_export(type, item_id):
    client = get_client()
    # the backend reads an empty selection as "every sample", so an id that is not a
    # plain number, or a family without samples, must not reach getExportData
    if type in ('family', 'samples') and not re.fullmatch(r"[0-9]{1,18}", item_id):
        flash(f'Only a {"family" if type == "family" else "sample"} id made of digits can be exported.', category='error')
        return redirect(url_for('data.export_view'))
    if type == 'family':
        samples = client.getSamplesByFamilyId(int(item_id))
        export_file = fetch_export([x.sample_id for x in samples.values()]) if samples else None
        if export_file is None:
            flash(f'MCRIT did not export family "{item_id}" - it may not exist or have no samples, or MCRIT could not export it.', category='error')
            return redirect(url_for('data.export_view'))
        return export_download(export_file, "export_family_" + str(item_id) + ".json")
    if type == 'samples':
        # the backend leaves out a sample_id it does not know, so the export itself says
        # whether there was one
        export_file = fetch_export([int(item_id)], require_samples=True)
        if export_file is None:
            flash(f'MCRIT did not export sample "{item_id}" - it may not exist, or MCRIT could not export it.', category='error')
            return redirect(url_for('data.export_view'))
        return export_download(export_file, "export_samples.json")
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
    # the backend checks both ids itself; None is an unknown id, a failure raises
    match_info = with_server_errors(client).getMatchFunctionVs(function_id_a, function_id_b)
    if match_info is not None:
        function_entry = FunctionEntry.fromDict(match_info["function_entry_a"])
        pichash_matches_a = client.getMatchesForPicHash(function_entry.pichash, summary=True)
        sample_entry_a = SampleEntry.fromDict(match_info["sample_entry_a"])
        other_function_entry = FunctionEntry.fromDict(match_info["function_entry_b"])
        sample_entry_b = SampleEntry.fromDict(match_info["sample_entry_b"])
        pichash_matches_b = client.getMatchesForPicHash(other_function_entry.pichash, summary=True)
        matched_function_entry = MatchedFunctionEntry(match_info["match_entry"]["fid"], match_info["match_entry"]["num_bytes"], match_info["match_entry"]["offset"], match_info["match_entry"]["matches"])
        # the graphs' block colours and matches are not on the page: its viewer fetches
        # them from match_functions_diff once the graphs are wanted
        return render_template(
            "result_compare_function_vs.html",
            entry_a=function_entry,
            entry_b=other_function_entry,
            sample_entry_a=sample_entry_a,
            sample_entry_b=sample_entry_b,
            pichash_matches_a=pichash_matches_a,
            pichash_matches_b=pichash_matches_b,
            match_result=matched_function_entry,
        )
    flash("One of the function_ids is not valid.", category='error')
    return render_template("index.html")


@bp.route('/matches/function/<int(signed=True):function_id_a>/<int(signed=True):function_id_b>/diff')
@visitor_required
@mcrit_server_required
def match_functions_diff(function_id_a, function_id_b):
    """The comparison's block colours and matches, for the graph viewer of the page above.

    They were part of the page, as JSON in a script, one entry per block and per match, so
    they grew with the two functions. Only the viewer reads them, and it starts once the
    graphs are wanted. Memoized with the comparison (get_function_diff); a function without
    its disassembly answers empty maps.

    The page was rendered from both functions, so a function the backend does not give
    back here is one it failed to answer for, or one deleted since. That is a 404, which the
    viewer reports, rather than empty maps, which it would draw as uncoloured graphs.
    """
    client = get_client()
    function_entry = client.getFunctionById(function_id_a, with_xcfg=True)
    other_function_entry = client.getFunctionById(function_id_b, with_xcfg=True)
    if function_entry is None or other_function_entry is None:
        return {"error": "One of the function_ids is not valid."}, 404
    diff = get_function_diff(function_id_a, function_id_b, function_entry, other_function_entry)
    return {"node_colors": diff["node_colors"], "node_matches": diff["node_matches"]}

################################################################
# Result presentation
################################################################

# job ids are hex object ids - the same shape views/api.py accepts. Constraining
# job_id once, at the door, is what keeps it harmless in all three places the
# download puts it: a cache filename match, a path handed to send_from_directory,
# and a filename in the Content-Disposition header. Matched with fullmatch rather
# than `$`, which would also accept a trailing newline - and a newline is exactly
# what splits a response header in two. Every route that hands a job id from the
# request to getJobData, getResultForJob or deleteJob checks it too: the backend
# client puts it into its request path unquoted, where ".." resolves to the
# backend's root and its answer was read as a job. Upper case passes, and is then
# lower-cased: the backend mints str(ObjectId), which is lower-case hex, and takes
# either, but a cached report is named with the id it answers with - so an
# upper-case URL missed the cache on every visit and wrote another copy.
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
        job_id = job_id.lower()
        job_info = with_server_errors(client).getJobData(job_id)
    if job_info is None:
        return render_template("result_invalid.html", job_id=job_id)
    cached_filename = find_cached_result_filename(current_app, job_id)
    if cached_filename is not None:
        # prefer the cache and stream the file untouched: it is byte-for-byte what
        # the backend answered, so this costs no parse and re-encode of a report that
        # can run to tens of megabytes, and the download cannot disagree with the
        # page that was rendered from the same file. Nothing goes stale by preferring
        # it - a finished job's result never changes, which is why nothing is ever
        # evicted for being stale, only for size (see trim_result_cache).
        cache_path = os.sep.join([current_app.instance_path, "cache", "results"])
        try:
            return send_from_directory(
                cache_path,
                cached_filename,
                mimetype='application/json',
                as_attachment=True,
                download_name=f"mcrit_result_{job_id}.json")
        except (NotFound, FileNotFoundError):
            # evicted by trim_result_cache since it was looked up: NotFound if that
            # happened before send_from_directory checked for the file, and the bare
            # error if it happened between that check and the open. Fetch it again.
            pass
    # what is cached but was not served is pretty-printed, from before writes were
    # atomic, or does not end the way it begins: loading it is the check, a file cut
    # short is skipped, and caching it again below makes it one the next download streams
    result_json = load_cached_result(current_app, job_id) or client.getResultForJob(job_id)
    if not result_json:
        # unfinished, failed, or a job type that produces nothing - the report page
        # already knows how to tell those apart, so let it do the talking
        flash('This job has no result to download.', category='error')
        return redirect(url_for('data.result', job_id=job_id))
    cache_result(current_app, job_info, result_json)
    # the fetch above already holds the whole report as a dict, so serialising it
    # once more is the cheap half of a cache miss; it is the cached path that keeps
    # every later download off the heap. Compact, as cache_result writes it, so both
    # paths answer with the same bytes.
    return Response(
        json.dumps(result_json, separators=(",", ":")),
        mimetype='application/json',
        headers={"Content-disposition":
                f"attachment; filename=mcrit_result_{job_id}.json"})


@bp.route('/result/<job_id>')
@visitor_required
@mcrit_server_required
# TODO:  refactor, simplify
def result(job_id):
    if not JOB_ID_PATTERN.fullmatch(job_id):
        return render_template("result_invalid.html", job_id=job_id)
    job_id = job_id.lower()
    client = get_client()
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = with_server_errors(client).getJobData(job_id)
    # kept separate from result_json because the tail of this view needs to tell an
    # empty report ({}) from one that could not be fetched (None), and truthiness
    # cannot. load_cached_result answers {} on a miss, and cache_result only ever
    # writes a truthy report, so a falsy result_json always means we got here.
    fetched = None
    if not result_json:
        # otherwise obtain result report from remote
        result_json = fetched = client.getResultForJob(job_id)
        if result_json and job_info is not None:
            cache_result(current_app, job_info, result_json)
    if result_json:
        # TODO validation - only parse to matching_result if this data type is appropriate 
        # re-format result report for visualization and choose respective template
        if job_info is None:
            return render_template("result_invalid.html", job_id=job_id)
        # the diagram's size, while the raw report is still in hand - MatchingResult
        # drops the per-function instruction counts it is worked out from
        diagram_size = match_diagram_size(result_json)
        if job_info.parameters.startswith("getMatchesForSampleVs"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result, diagram_size)
        elif job_info.parameters.startswith("getMatchesForSample"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result, diagram_size)
        elif job_info.parameters.startswith("getMatchesForSmdaReport"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result, diagram_size)
        elif job_info.parameters.startswith("getMatchesForMappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result, diagram_size)
        elif job_info.parameters.startswith("getMatchesForUnmappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result, diagram_size)
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
            # mcrit answers None here for a sample deleted while its uploaded binary
            # replaced its analysis (familiary/mcrit#306), and an older result may lack it
            sample_info = result_json.get('sample_info') if isinstance(result_json, dict) else None
            sample_id = sample_info.get('sample_id') if isinstance(sample_info, dict) else None
            if isinstance(sample_id, int) and not isinstance(sample_id, bool):
                return redirect(url_for('explore.sample_by_id', sample_id=sample_id))
            flash('The sample this job added is no longer in the collection.', category='warning')
            return redirect(url_for('explore.samples'))
        # modify and delete samples and families
        elif job_info.parameters.startswith("deleteSample"):
            return redirect(url_for('explore.samples'))
        elif job_info.parameters.startswith("modifySample"):
            return redirect(url_for('explore.samples'))
        elif job_info.parameters.startswith("deleteFamily"):
            return redirect(url_for('explore.families'))
        elif job_info.parameters.startswith("modifyFamily"):
            return redirect(url_for('explore.families'))
        elif job_info.parameters in [
            "rebuildIndex()",
            "recalculatePicHashes()",
            "recalculateMinHashes()",
            # mcrit 1.9.0. Leave one of these three out and the result of a job MCRITweb
            # itself scheduled never reaches the maintenance report.
            "repairMinHashes()",
            "recomputeFamilyStats()",
            "rebuildPicBlockHashIndex()",
        ]:
            return render_template("result_maintenance.html", result=result_json, job_info=job_info)
        else:
            # a job type this dispatch has never been taught. Falling off the end of the
            # chain returned None, which Flask answers with a 500 rather than a page.
            return render_template("result_incompatible.html", job_id=job_id)
    # no report to show. Which of the reasons for that it is decides what to say, and
    # `if result_json:` above cannot tell an empty report from an absent one - {} is
    # falsy - so a finished job with an empty result used to land in the unknown-job-id
    # page below. See issue #73.
    if job_info is None:
        # nothing knows this job id, so there is no missing result to explain
        return render_template("result_invalid.html", job_id=job_id)
    if job_info.is_failed or job_info.is_terminated:
        # not result_invalid.html: that page says the job "was not found in the system",
        # which is false for a job we are holding the record of. This one is the whole
        # point of the issue - stop reporting one reason as another.
        reason = ("This job was terminated before it could finish."
                  if job_info.is_terminated else
                  "This job ran out of attempts and failed. The backend's log will say why.")
        return render_template("job_failed.html", job_info=job_info, reason=reason)
    if job_info.is_finished:
        # Three ways to finish without a report, and they are not the same answer.
        # getResultForJob returns None both when the job recorded no result id and when
        # the document behind that id can no longer be fetched, so job_info.result is
        # what separates them.
        if job_info.result is None:
            # the job never produced one. For a matching job that is an empty report;
            # for a minhashing job or a collection change there was never going to be one
            return render_template("result_empty.html", job_id=job_id)
        if fetched is None:
            # it recorded a result and the backend could not hand it back - a purged
            # GridFS document, a re-provisioned backend that kept the job metadata.
            # Telling the analyst "this run found nothing" would be a wrong analytical
            # answer, not just wrong wording. result_invalid.html already says exactly
            # this: the result referenced by this job was not found.
            return render_template("result_invalid.html", job_id=job_id)
        # it ran to completion and the report it produced is empty. That is a result
        return render_template("result_empty.html", job_id=job_id)
    # if we are not done processing, list job data
    return render_job_in_progress(client, job_info)

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

#: How many block covers `build_yara_rule` keeps - issue #184. A cover is a pure
#: function of a finished job's report and the rule parameters, and it is the expensive
#: part of this page - the O(k*n) walk above - yet it used to be rebuilt on every
#: render: each page of the block table, each reload. An entry is a handful of block
#: hashes. Counted in entries, since the parameters arrive in the query string.
YARA_COVER_MEMO_ENTRIES = 32


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


def build_yara_rule(job_id, blocks_result, yara_params):
    """The rule, plus the block cover it was built from - or None for no rule.

    `generateYaraRule` throws the cover away, but the page reports what the rule covers,
    and those numbers move with the parameters - so they cannot be read off the
    statistics the backend stored under its own defaults.

    A cover with no blocks in it has no rule to render. `renderRule` would still produce
    one, but it is not YARA: an empty `strings:` section is a syntax error on its own,
    and `min(len(block_hashes), condition_required)` writes "0 of them" underneath
    YARA_CONDITION_MINIMUM. No condition rescues that, so nothing is offered to copy.

    Only the cover is memoized, keyed by the job and every parameter it reads. The rule
    renders in milliseconds even at YARA_REQUIRED_PER_SAMPLE_MAXIMUM, and `renderRule`
    stamps it with today's date - a memoized rule would be copied out with a stale one.
    """
    ubr = UniqueBlocksResult.fromDict(blocks_result)
    cover_params = ("min_ins", "max_ins", "min_bytes", "max_bytes", "required_per_sample")

    def generate_cover():
        block_cover = ubr.generateBlockCover(**{name: yara_params[name] for name in cover_params})
        # shared by every request that asks for these parameters, so read-only
        return types.MappingProxyType(dict(block_cover, block_hashes=tuple(block_cover["block_hashes"])))

    memo = app_memo(current_app, "unique_blocks_cover", YARA_COVER_MEMO_ENTRIES)
    block_cover = memo.get((job_id,) + tuple(yara_params[name] for name in cover_params), generate_cover)
    if not block_cover["block_hashes"]:
        return None, block_cover
    yara_rule = ubr.renderRule(block_cover, yara_params["condition_required"], wrap_at=40)
    # each block's comment also says which function it came from - see
    # name_functions_in_rule and issue #80
    return name_functions_in_rule(yara_rule, ubr.unique_blocks, block_cover), block_cover


def name_functions_in_rule(yara_rule, unique_blocks, block_cover):
    """Name the function each picblock was taken from, in its comment in the rule.

    Issue #80 asks for either function_id in the rule or a cover spread over a
    variety of function_ids. mcrit builds the cover greedily by how many uncovered
    samples a block adds, tie-broken on score, with no notion of which function a
    block sits in - so nothing stops a rule from fingerprinting a single function,
    and `condition: N of them` fails the moment that function is recompiled. Which
    blocks to pick is mcrit's decision; what can be said here is where each of them
    came from, so a reader can see the spread before shipping the rule.

    The comment is rebuilt exactly as `renderRule` writes it and replaced by name.
    A format change upstream therefore leaves the rule as it was rather than
    mangling it - the test on this is what says the annotation still lands.
    """
    for pichash in block_cover["block_hashes"]:
        entry = unique_blocks[pichash]
        rendered = f"/* picblockhash: {pichash} - coverage: {len(entry['samples'])}/{block_cover['num_samples_covered']} samples"
        yara_rule = yara_rule.replace(f"{rendered}.", f"{rendered}, function_id: {entry['function_id']}.")
    return yara_rule


def get_sample_versions(family_entry, sample_ids):
    """Map sample_id -> version for the samples a unique blocks report covers.

    The report carries no version of its own - `statistics["by_sample_id"]` is block
    counts and nothing else - so it has to be looked up. A family job needs no extra
    request for it: `getFamily` already answers with the family's samples and the
    caller is holding that entry. Whatever the family did not supply is fetched in one
    batch through `get_sample_entries` (#191), not one request per row of the table.

    A lookup that comes back None leaves that row's version blank. It does not say
    the sample is gone: `handle_response` in `McritClient` collapses a 404 and a 500
    into the same None, so a blank cell means "no version to show" and nothing more.
    Telling the two apart would take the raw response, which this seam does not
    carry - and neither of them is worth failing a whole report over.
    """
    versions = {}
    family_samples = getattr(family_entry, "samples", None) or {}
    for sample_entry in family_samples.values():
        versions[sample_entry.sample_id] = sample_entry.version
    missing = [sample_id for sample_id in sample_ids if sample_id not in versions]
    for sample_id, sample_entry in get_sample_entries(missing).items():
        if sample_entry is not None:
            versions[sample_id] = sample_entry.version
    return versions

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
    # generateBlockCover and renderRule break ties by the order they meet the blocks in.
    # A report fetched from the backend on this request has them in the backend's order,
    # the cached copy every later render reads in sorted key order - so without this the
    # first view of a job could show another rule than every view after it, and the
    # memoized cover would depend on which of the two happened to compute it (issue #184)
    blocks_result["unique_blocks"] = dict(sorted(blocks_result["unique_blocks"].items()))
    sample_versions = get_sample_versions(family_entry, [entry["sample_id"] for entry in blocks_statistics["by_sample_id"].values()])
    # the families the requested samples are listed under. For a job without a family,
    # get_sample_versions has just fetched these entries for the report's samples, which
    # are the requested ones, so this asks the backend nothing more; a family job lists
    # its family instead
    samples_by_id = get_sample_entries(sample_ids) if family_entry is None else {}
    yara_params = parse_yara_rule_params(request)
    yara_rule, yara_cover = build_yara_rule(job_info.job_id, blocks_result, yara_params)
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
        # the blocks are already in pichash order above; breaking ties by pichash here too
        # keeps the table from depending on that
        for pichash, result in sorted(unique_blocks.items(), key=lambda x: (-x[1]["score"], x[0])):
            if index >= block_pagination.end_index:
                break
            if index >= block_pagination.start_index:
                yarafied = f"/* picblockhash: {pichash} \n"
                maxlen_ins = max([len(ins[1]) for ins in result["instructions"]])
                for ins in result["instructions"]:
                    yarafied += f" * {ins[1]:{maxlen_ins}} | {ins[2]} {ins[3]}\n"
                yarafied += " */\n"
                # Wrapped between bytes, the way the rule itself is. Breaking every
                # 80th character instead cut hex bytes in half - "6a33" became "6a3"
                # and "3" - so a block copied out of this column was not valid YARA
                # once the sequence was long enough to wrap at all. See issue #80.
                yarafied += "{ " + wrap_string(result["escaped_sequence"], max_column_length=80) + " }"
                unique_blocks[pichash]["yarafied"] = yarafied
                paginated_block = result
                paginated_block["key"] = pichash
                paginated_block["yarafied"] = yarafied
                paginated_blocks.append(paginated_block)
            index += 1
    # TODO pass the new result objects as single arguments and then render them in page tabs on the template
    return render_template("result_unique_blocks.html", job_info=job_info, family_entry=family_entry, sample_id=sample_id, sample_ids=sample_ids, yara_rule=yara_rule, yara_cover=yara_cover, yara_params=yara_params, yara_query=yara_query, statistics=blocks_statistics, sample_versions=sample_versions, samples_by_id=samples_by_id, results=paginated_blocks, blkp=block_pagination, active_tab=active_tab)

#: Shown when a stored result names something the backend can no longer resolve. The
#: cross-compare path has said this about samples for a long time; issue #96 is the
#: same situation one level down, where the missing thing is a function.
MISSING_ENTRIES_REASON = "MCRIT was not able to retrieve information for all functions referenced by this result. This might be a result of having deleted samples from the database since it was processed. Please consider starting a new job."


def count_aggregated_function_matches(matching_result, unfiltered=False):
    """How many rows the aggregated function table has.

    The pagination needs the row count and nothing else, but asking
    getAggregatedFunctionMatches() for it aggregated every function match in the
    report - a dict and several set unions per match - and threw the result away,
    only for the template to build it a second time for the page slice. It groups by
    function_id and does not drop any group, so the row count is the number of
    distinct function_ids. tests/testResultPages.py asserts the two agree.

    `unfiltered` counts the whole report, as getAggregatedFunctionMatches(unfiltered=True)
    does - the total a filtered page prints its "filtered" figure against, which
    would otherwise aggregate the entire report a second time just to be counted.
    """
    function_matches = matching_result.function_matches if unfiltered else matching_result.filtered_function_matches
    return len({function_match.function_id for function_match in function_matches})


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


def aggregate_function_matches_page(matching_result: MatchingResult, pagination: Pagination):
    """The rows of the aggregated function table that fall on `pagination`'s page.

    getAggregatedFunctionMatches(start, limit) builds the aggregate of every function
    in the (filtered) report - a dict entry and several set unions per match - and
    only then slices out the page, so the template paid for the whole report to show
    a hundred rows of it (issue #194). A row is an aggregate over the matches of one
    function_id, and rows are ordered by function_id, so the page is made of exactly
    the matches whose function_id is among the page's ids. Handing only those to
    mcrit's own aggregation, on a shallow copy so that the view's filtered list is
    left alone, gives the same rows without re-implementing it here.
    """
    function_matches = matching_result.filtered_function_matches
    function_ids = sorted({function_match.function_id for function_match in function_matches})
    page_function_ids = set(function_ids[pagination.start_index:pagination.start_index + pagination.limit])
    page_result = copy.copy(matching_result)
    page_result.filtered_function_matches = [function_match for function_match in function_matches if function_match.function_id in page_function_ids]
    return page_result.getAggregatedFunctionMatches()


def aggregated_function_rows(matching_result: MatchingResult, pagination: Pagination):
    """The aggregated function table's page, sorted as `pagination` asks.

    A sort has to see every row, so a sorted page aggregates the whole (filtered)
    report and slices it. Without a sort the page comes from
    `aggregate_function_matches_page`, which aggregates only the functions on it.
    """
    return sorted_page(
        pagination, AGGREGATED_FUNCTION_SORT_KEYS, matching_result,
        all_rows=matching_result.getAggregatedFunctionMatches,
        page_rows=lambda: aggregate_function_matches_page(matching_result, pagination),
    )


def matched_function_rows(matching_result: MatchingResult, pagination: Pagination):
    """The function-to-function table's page, sorted as `pagination` asks."""
    return sorted_page(
        pagination, MATCHED_FUNCTION_SORT_KEYS, matching_result,
        all_rows=lambda: matching_result.filtered_function_matches,
        page_rows=lambda: matching_result.getFunctionsSlice(pagination.start_index, pagination.limit),
    )


def recover_score_divisors(matching_result):
    """The two totals the sample score columns are percentages of.

    mcrit normalises the plain columns against the reference sample's *matchable*
    bytes and the `nonlib_` ones against that total minus its library-matching
    functions, and puts neither on the wire - so the hover text used to divide by
    `binweight`, which is a third number, and state a percentage that was not the
    quotient it had just shown (issue #7, docs/adr/0009-nonlib-frequency-score.md).

    Both are exactly recoverable as `100 * bytes / percent` from any row that scored
    above zero, because the divisor is a property of the reference sample rather than
    of the match. Recovered from the unfiltered matches so that a filtered page shows
    the same totals as the unfiltered one.

    A report whose every row scores zero in one group leaves that group's divisor
    unrecoverable. The fallbacks keep the fraction consistent with the percentage
    anyway, since a zero numerator reads as 0% over any total.
    """
    divisors = {}
    for group, prefix in (("matchable", ""), ("nonlibrary", "nonlib_")):
        for sample_match in matching_result.sample_matches:
            for kind in ("unweighted", "score_weighted", "frequency_weighted"):
                percent = getattr(sample_match, f"matched_percent_{prefix}{kind}", 0)
                if percent:
                    divisors[group] = 100.0 * getattr(sample_match, f"matched_bytes_{prefix}{kind}") / percent
                    break
            if group in divisors:
                break
    divisors.setdefault("matchable", matching_result.reference_sample_entry.binweight)
    divisors.setdefault("nonlibrary", divisors["matchable"])
    return divisors


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


def result_matches_for_sample_or_query(job_info, matching_result: MatchingResult, diagram_size=None):
    name_query_sample(job_info, matching_result)
    score_color_provider = ScoreColorProvider()
    score_divisors = recover_score_divisors(matching_result)
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
        sample_pagination = Pagination(request, matching_result.num_sample_matches, limit=10, query_param="samp", limit_param="sampl")
        function_pagination = Pagination(request, count_aggregated_function_matches(matching_result), limit=100, query_param="funp", limit_param="funl")
        # result_compare_family.html and result_compare_all.html draw one row per matched
        # function of the reference sample, aggregated over the samples it matched, and print
        # "filtered" as the rest of the report beside it. That subtraction only means anything
        # if both sides count the same thing, so the total goes in aggregated too - taken off
        # the raw match count, it read as a four-figure "filtered" with no filter applied.
        num_original_aggregated_functions = count_aggregated_function_matches(matching_result, unfiltered=True)
        sample_rows = sorted_page(
            sample_pagination, FAMILY_SAMPLE_SORT_KEYS, matching_result,
            all_rows=matching_result.getSampleMatches,
            page_rows=lambda: matching_result.getSampleMatches(sample_pagination.start_index, sample_pagination.limit),
        )
        return render_template("result_compare_family.html", diagram_size=diagram_size, divisors=score_divisors, famid=filtered_family_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, sample_rows=sample_rows, function_rows=aggregated_function_rows(matching_result, function_pagination), num_original_aggregated_functions=num_original_aggregated_functions, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_all)
    # filtered for sample
    elif filtered_sample_id is not None and (filtered_sample_entry := client.getSampleById(filtered_sample_id)) is not None:
        matching_result.filterToSampleId(filtered_sample_id)
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
        return render_template("result_compare_sample.html", diagram_size=diagram_size, divisors=score_divisors, samid=filtered_sample_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, function_rows=matched_function_rows(matching_result, function_pagination), matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_sample)
    # filter for function - treat family/sample part as if there was no filter
    elif filtered_function_id is not None and filtered_function_id in matching_result.function_id_to_family_ids_matched:
        matching_result.filterToFunctionId(filtered_function_id)
        matching_result.filtered_function_matches = sorted(matching_result.filtered_function_matches, key=lambda x: (x.matched_score, x.match_is_pichash, x.matched_family_id, x.matched_sample_id, x.matched_function_id), reverse=True)
        # pull all function_entries, as we want to have their offsets
        if not assign_matched_offsets(client, matching_result.filtered_function_matches):
            return render_template("result_corrupted.html", reason=MISSING_ENTRIES_REASON, job_info=job_info)
        # set up pagination
        family_pagination = Pagination(request, matching_result.num_family_matches, limit=10, query_param="famp", limit_param="fampl")
        function_pagination = Pagination(request, matching_result.num_function_matches, limit=100, query_param="funp", limit_param="funl")
        return render_template("result_compare_function.html", diagram_size=diagram_size, funid=filtered_function_id, job_info=job_info, famp=family_pagination, funp=function_pagination, function_rows=matched_function_rows(matching_result, function_pagination), matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_function)
    # 1 vs 1 result
    elif job_info.parameters.startswith("getMatchesForSampleVs("):
        # get offsets for matched functions
        if not assign_matched_offsets(client, matching_result.filtered_function_matches):
            return render_template("result_corrupted.html", reason=MISSING_ENTRIES_REASON, job_info=job_info)
        # we need to slice function matches ourselves based on pagination
        function_pagination = Pagination(request, matching_result.num_function_matches, limit=100, query_param="funp", limit_param="funl")
        return render_template("result_compare_vs.html", divisors=score_divisors, job_info=job_info, matching_result=matching_result, funp=function_pagination, function_rows=matched_function_rows(matching_result, function_pagination), scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_sample)
    # unfiltered / default -> also 1 vs group
    else:
        family_pagination = Pagination(request, matching_result.num_family_matches, limit=10, query_param="famp", limit_param="fampl")
        library_pagination = Pagination(request, matching_result.num_library_matches, limit=10, query_param="libp", limit_param="libl")
        function_pagination = Pagination(request, count_aggregated_function_matches(matching_result), limit=100, query_param="funp", limit_param="funl")
        # the total behind the "filtered" figure, aggregated to match the rows - see above
        num_original_aggregated_functions = count_aggregated_function_matches(matching_result, unfiltered=True)
        # a query can be promoted to a sample (issue #9), but only while the file it
        # was run for is still on this host - the page has to say which it is. The file
        # is filed under the job's own id, so this costs no round trip either
        is_query_result = job_info.method in QUERY_UPLOAD_KINDS
        family_rows = sorted_page(
            family_pagination, FAMILY_SAMPLE_SORT_KEYS, matching_result,
            all_rows=lambda: matching_result.getBestSampleMatchesPerFamily(malware_only=True),
            page_rows=lambda: matching_result.getBestSampleMatchesPerFamily(family_pagination.start_index, family_pagination.limit, malware_only=True),
        )
        library_rows = sorted_page(
            library_pagination, FAMILY_SAMPLE_SORT_KEYS, matching_result,
            all_rows=lambda: matching_result.getBestSampleMatchesPerFamily(library_only=True),
            page_rows=lambda: matching_result.getBestSampleMatchesPerFamily(library_pagination.start_index, library_pagination.limit, library_only=True),
        )
        return render_template("result_compare_all.html", diagram_size=diagram_size, divisors=score_divisors, job_info=job_info, famp=family_pagination, libp=library_pagination, funp=function_pagination, family_rows=family_rows, library_rows=library_rows, function_rows=aggregated_function_rows(matching_result, function_pagination), num_original_aggregated_functions=num_original_aggregated_functions, matching_result=matching_result, scp=score_color_provider, ucs_famlib=user_column_setup_family_library, ucs_functions=user_column_setup_function_all, is_query_result=is_query_result, can_promote_query=is_query_result and query_upload_exists(current_app, job_info.job_id))


def order_samples(samples, order):
    """`samples` in the order `order` names them, or None if it names one that is not
    there, or one more than once.

    The ordering used to scan `samples` for every id in `order`, which made laying
    out n samples cost O(n^2) - and the cross-compare page does it once per matching
    method, so six times over. Both sides are compared as strings because `order` is
    either the report's clustered sequence or the `custom` query parameter, neither
    of which is guaranteed to arrive as an int. See issue #68.

    A repeated id is refused rather than drawn twice: every row of the matrix is a row
    and a column, so `?custom=` naming one sample a few thousand times would have the
    page render millions of cells for anyone who can view the job.
    """
    samples_by_id = {str(sample.sample_id): sample for sample in samples}
    ordered_samples = []
    for order_sample_id in order:
        sample = samples_by_id.pop(str(order_sample_id), None)
        if sample is None:
            return None
        ordered_samples.append(sample)
    return ordered_samples


def result_matches_for_cross(job_info, result_json):
    samples = []
    sample_ids = [int(id) for id in next(iter(result_json.values()))["clustered_sequence"]]
    sample_entries = get_sample_entries(sample_ids)
    for sample_id in sample_ids:
        sample_entry = sample_entries[sample_id]
        if sample_entry:
            samples.append(sample_entry)
        else:
            reason = "MCRIT was not able to retrieve information for all samples specified in the original job task. This might be a result of having deleted samples from the database since it was processed. Please consider starting a new job."
            return render_template("result_corrupted.html", reason=reason, job_info=job_info)
    custom_order = request.args.get('custom','')
    # a drag-and-drop order is the more specific request, so it wins over a named one
    ordering = parse_choice_query_param(request, 'order', CROSS_ORDERINGS, default="clustered")
    samples_by_method = {}
    sample_indices = {}
    for method, method_results in result_json.items():
        if custom_order:
            order = custom_order.split(',')
        else:
            order = order_sample_ids(samples, ordering, method_results.get("clustered_sequence"))
        ordered_samples = []
        if order:
            ordered_samples = order_samples(samples, order)
            if ordered_samples is None:
                reason = "MCRIT was not able to produce the chosen custom ordering, as some sample_ids are repeated or not part of the cross compare originally specified."
                # job_info, not result_json: the template reads job_info.job_id for
                # its heading and for the delete link, and a result dict has neither.
                # The sibling corrupted branch above already passes the Job.
                return render_template("result_corrupted.html", reason=reason, job_info=job_info)
        if ordered_samples != []:
            samples_by_method[method] = ordered_samples
        else:
            samples_by_method[method] = samples
        sample_indices[method] = [x for index, x in enumerate([sample.sample_id for sample in samples_by_method[method]]) if (index+1) % 5 == 0]
    # only the tab shown on load is rendered, the others are filled from lazy_panes on first click
    rendered_method = "unweighted" if "unweighted" in result_json else next(iter(result_json))
    return render_template('result_cross.html',
        is_corrupted=False,
        active_order="custom" if custom_order else ordering,
        samples=samples_by_method,
        sample_indices = sample_indices,
        job_info=job_info,
        sample_to_job_id=get_sample_to_job_id(job_info),
        matching_matches={method: result_json[method]["matching_matches"] for method in result_json.keys()},
        matching_percent={method: result_json[method]["matching_percent"] for method in result_json.keys()},
        score_to_color=score_to_color,
        rendered_method=rendered_method,
        lazy_panes=lazy_cross_panes(samples_by_method, result_json, rendered_method),
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
    if not JOB_ID_PATTERN.fullmatch(job_id):
        return render_template("result_invalid.html", job_id=job_id)
    job_id = job_id.lower()
    client = get_client()
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = with_server_errors(client).getJobData(job_id)
    if not result_json:
        # otherwise obtain result report from remote
            result_json = client.getResultForJob(job_id)
            if result_json and job_info is not None:
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
    return render_job_in_progress(client, job_info)

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
    if filter_unpenalized_family_count is None:
        # the form sends this field empty when the user clears it, and mcrit compares the
        # count to an int, so an empty one was a 500; both presets above use 2
        filter_unpenalized_family_count = 2
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
    link_hunt_args = (filter_min_score, filter_lib_min_score, filter_min_size, filter_min_offset, filter_max_offset, filter_unpenalized_family_count, filter_exclude_families, filter_exclude_samples, filter_strongest_per_family)
    link_hunt_result = matching_result.getLinkHuntResults(*link_hunt_args)

    # Clustering fetches every function of the reference sample and walks their call
    # references, the bulk of this request. What it clusters is the link hunt result, so
    # it is memoized per job and per argument of getLinkHuntResults; the link score
    # filter below applies to the clusters afterwards and paging is over the individual
    # links, so neither needs a new clustering. An exclusion list is only read as a set,
    # so it is keyed as one, and an empty list as None, which excludes the same nothing -
    # the default filters and a submitted copy of them differ by exactly that. Nothing
    # else is normalised. The checkbox always parses to a bool, so it has no None to
    # fold; a score, size or offset of 0 filters nothing only if the report has no
    # negative values, which getLinkHuntResults does not itself check.
    cluster_key = (job_info.job_id,) + tuple((tuple(sorted(set(arg))) or None) if isinstance(arg, list) else arg for arg in link_hunt_args)

    def cluster_link_hunt_result():
        function_entries = require_result(
            with_server_errors(client).getFunctionsBySampleId(matching_result.reference_sample_entry.sample_id),
            "the functions of the reference sample")
        # TODO: probably need to paginate them as well
        link_clusters = matching_result.clusterLinkHuntResult(function_entries, link_hunt_result)
        return sorted([cluster for cluster in link_clusters if len(cluster["links"]) > 1], key=lambda x: x["score"], reverse=True)
    # shared with every later request of the same key, so filtered below into a new list only
    link_clusters = app_memo(current_app, "linkhunt_clusters", 32).get(cluster_key, cluster_link_hunt_result)

    if filter_link_score:
        link_clusters = [cluster for cluster in link_clusters if cluster["score"] > filter_link_score]
        link_hunt_result = [link for link in link_hunt_result if link.matched_link_score > filter_link_score]

    cluster_pagination = Pagination(request, len(link_clusters), limit=10, query_param="clup", limit_param="clul")
    function_pagination = Pagination(request, len(link_hunt_result), limit=100, query_param="funp", limit_param="funl")
    return render_template("linkhunt.html", job_info=job_info, clup=cluster_pagination, funp=function_pagination, matching_result=matching_result, lc=link_clusters, lhr=link_hunt_result, scp=score_color_provider)


################################################################
# Listing Job information
################################################################

# Job.duration is finished_at - started_at of a single job. A job that waits on
# dependencies - a cross compare waits on one child per sample - does not start until
# the last child is done, so its own duration covers only the assembly of the result
# and reads as ~0 however long the matching took (issue #46). created_at ->
# finished_at brackets the children too, and both are already on the job document, so
# it costs no additional backend call. Until the job finishes, the far end of that
# span is the latest time the backend wrote on the job or its dependencies, see
# `latest_job_timestamp`.
JOB_TIMESTAMP_FMT = "%Y-%m-%d-%H:%M:%S"
#: The timestamps the queue writes on a job, all from the backend's own clock.
JOB_TIMESTAMP_FIELDS = ("created_at", "started_at", "finished_at", "locked_at")


def latest_job_timestamp(jobs):
    """The latest time the backend wrote on any of `jobs`, or None if none is readable.

    mcrit's queue stamps its jobs with `datetime.now()`, the backend's local time
    (`libs/mongoqueue.py`: created_at on enqueue, started_at and locked_at when a worker
    takes the job, locked_at again on each progress update, finished_at), and the REST
    API sends them with a "Z" all the same. Measured against this server's clock, a
    running job's total was off by the backend's UTC offset - negative east of UTC, so
    the row was hidden. Measured against the backend's own latest stamp instead, the
    total is a lower bound, short by the time since that stamp, but never off by a
    time zone.
    """
    latest = None
    for job in jobs:
        for field in JOB_TIMESTAMP_FIELDS:
            try:
                stamp = job_timestamp_as_datetime(getattr(job, field))
            except KeyError:
                # a job document written before the field existed
                continue
            if stamp is not None and (latest is None or stamp > latest):
                latest = stamp
    return latest


def dependency_progress(child_jobs):
    """How far a job waiting on dependencies actually is, or None if that is unknowable.

    `Job.progress` is the parent's own counter, and a parent does not start until its
    last child is done - so for the whole time the children run it reads 0, which is the
    other half of what issue #46 calls meaningless. The children each carry a real
    progress, and the page that shows this has already fetched them, so averaging costs
    no additional backend call.

    A finished child counts as done whatever its counter says: a worker that finishes
    without ticking progress to 1.0 would otherwise hold the parent below 100% forever.
    A child that has been deleted is skipped rather than counted as 0 - it is not
    "no progress", it is "no longer knowable", and counting it as zero would make the
    bar go backwards when a dependency is cleaned up.
    """
    known = [job for job in (child_jobs or []) if job is not None]
    if not known:
        return None
    total = 0.0
    for job in known:
        total += 1.0 if job.finished_at else min(max(job.progress or 0, 0.0), 1.0)
    return total / len(known)


def job_timestamp_as_datetime(value):
    """One of Job's timestamps as a datetime, or None if it is unreadable.

    Job.created_at / .finished_at normalize the {"$date": ...} the REST API sends into
    an ISO string, but hand back whatever the queue stored otherwise - a datetime, for
    an in-process LocalQueue. Both shapes come back at whole-second resolution: the
    string one has to be, since parsing it drops the sub-second part, and a total of
    `0:01:29.510000` beside a `0:00:00` duration reads as a different kind of number.
    """
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10] + "-" + value[11:19], JOB_TIMESTAMP_FMT)
        except ValueError:
            return None
    return None


@bp.app_template_filter('total_duration')
def total_duration(job_info, child_jobs=None):
    """How long a job that waited on dependencies has taken end to end, or None when
    that is not a number this job can produce.

    A job still waiting on its dependencies is measured up to the latest time the
    backend wrote on it or on `child_jobs`, the dependencies the page has fetched. That
    is the state issue #46 is really about - while the children run, the parent has no
    duration and a progress of 0, so the page a reader watches refresh says nothing
    at all about how long the work has been going. A page that has not fetched the
    dependencies gets None for a running job: the parent's own stamps would only give
    the time between its creation and its children's.
    """
    try:
        if not job_info.all_dependencies:
            return None
        created_at = job_timestamp_as_datetime(job_info.created_at)
        if job_info.finished_at is None:
            # a failed or terminated job never gets a finished_at, so there is no end
            # to measure to
            if job_info.is_failed or job_info.is_terminated or child_jobs is None:
                return None
            finished_at = latest_job_timestamp([job_info, *child_jobs])
        else:
            finished_at = job_timestamp_as_datetime(job_info.finished_at)
    except KeyError:
        # a job document written before one of these fields existed does not carry it
        return None
    if created_at is None or finished_at is None:
        return None
    if (created_at.tzinfo is None) != (finished_at.tzinfo is None):
        # one aware, one naive - subtracting them raises, and there is nothing
        # meaningful to show either way
        return None
    duration = finished_at - created_at
    return duration if duration.total_seconds() >= 0 else None


#: Shown on job_corrupted.html. The job document is the backend's, so there is nothing
#: to do about it here beyond naming it and offering to delete it.
UNREADABLE_PAYLOAD_REASON = (
    "The parameters this job was submitted with cannot be read back from its stored "
    "payload, so there is nothing to show for it and no request to repeat."
)


#: The job types this front end knows the names of. Not the authority on what a job type
#: is - the backend is, and `known_job_category` below defers to it. This is only the
#: fallback for a type the backend is not currently reporting, because it has no jobs of
#: that kind. `Job.method_types["all"]` is not even the whole local list: it omits
#: every job the admin maintenance routes create - recalculatePicHashes and
#: recalculateMinHashes, and the three repairs mcrit 1.9.0 added - all of which the
#: menu does render.
JOB_CATEGORIES = tuple(Job(None, None).method_types["all"]) + (
    "recalculatePicHashes",
    "recalculateMinHashes",
    # the per-sample jobs a cross compare runs, which Job.method_types never learned
    # about either; data.jobs gives them a tab (#51), so its own link has to be accepted
    # on an installation that has not run a cross compare yet
    "getMatchesForSampleVsGroup",
    "repairMinHashes",
    "recomputeFamilyStats",
    "rebuildPicBlockHashIndex",
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


@bp.route('/jobs')
@visitor_required
@mcrit_server_required
def jobs():
    # a search is a read, so it travels in the URL: the result is linkable, survives a
    # refresh, and is carried by the pagination links, which build from request.args.
    # It used to be a POST whose value was read and then never used - see issue #51.
    query = request.args.get('Search', '').strip()
    client = get_client()
    # sort order. Job creation order is the only thing mcrit can sort a queue by, but it
    # does sort the whole queue: mongoqueue's get_jobs orders by _id *before* it skips
    # and limits, so `ascending` reorders the category rather than the page. Nothing in
    # the chain from getQueueData down to the collection accepts a sort key, so Type,
    # Started, Finished and Progress cannot be ordered across pages at all - see the note
    # in jobs.html on why they are not offered as a client-side sort instead. Issue #51.
    ascending = request.args.get('ascending', 'false').lower() == "true"
    # Carry what the page understands rather than all of request.args: the page number
    # is deliberately dropped, because page 3 of one order is an unrelated slice of the
    # other, and forwarding arbitrary keys would hand url_for its own reserved ones
    # (`_method`, `_scheme`, ...) straight from the query string.
    order_args = {key: value for key, value in request.args.items()
                  if key in ("Search", "active", "state", "plimit", "l")}
    order_args["ascending"] = "false" if ascending else "true"
    order_toggle = url_for('data.jobs', **order_args)
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
    # A cross compare runs as one getMatchesForSampleVsGroup job per sample plus a
    # combineMatchesToCross job that merges them once they have all finished. mcrit's
    # Job.method_types never learned about the group jobs, so without this they have no
    # tab at all: browsing cannot reach them, the search cannot narrow them, and the
    # "Matching" count leaves them out while the totals row above it counts them.
    # This is issue #51's "what about cross jobs?" - the jobs a cross compare actually
    # does the work in were the ones the page could not list.
    for group in ("matching", "all"):
        job_template.method_types[group].append("getMatchesForSampleVsGroup")
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
    elif request.method != 'POST' and active_category is not None and request.args.get('active') != active_category:
        # the category picked above is derived from the live queue statistics, so a URL
        # that does not name it means something different on every request: a refresh
        # or a step back in history then lands on a different tab (issue #36).
        # redirect to the URL that does name it, so the browser can reproduce this page.
        # the query is rebuilt rather than handed to url_for as keyword arguments,
        # because the names url_for reserves for itself are user input here
        canonical_args = request.args.to_dict()
        canonical_args['active'] = active_category
        return redirect(f"{url_for('data.jobs')}?{urlencode(canonical_args)}")
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
            {"group": "matching", "title": f"Matching ({summarized_groups['matching']})", "active": active_category in ["getMatchesForSample", "getMatchesForSampleVs", "getMatchesForSampleVsGroup", "combineMatchesToCross"], "available": True, "submenu": [
                {"name": "getMatchesForSample", "title": f"getMatchesForSample ({sum(statistics['getMatchesForSample'].values()) if 'getMatchesForSample' in statistics else 0})", "active": "getMatchesForSample" == active_category, "available": "getMatchesForSample" in statistics},
                {"name": "getMatchesForSampleVs", "title": f"getMatchesForSampleVs ({sum(statistics['getMatchesForSampleVs'].values()) if 'getMatchesForSampleVs' in statistics else 0})", "active": "getMatchesForSampleVs" == active_category, "available": "getMatchesForSampleVs" in statistics},
                {"name": "getMatchesForSampleVsGroup", "title": f"getMatchesForSampleVsGroup ({sum(statistics['getMatchesForSampleVsGroup'].values()) if 'getMatchesForSampleVsGroup' in statistics else 0})", "active": "getMatchesForSampleVsGroup" == active_category, "available": "getMatchesForSampleVsGroup" in statistics},
                {"name": "combineMatchesToCross", "title": f"combineMatchesToCross ({sum(statistics['combineMatchesToCross'].values()) if 'combineMatchesToCross' in statistics else 0})", "active": "combineMatchesToCross" == active_category, "available": "combineMatchesToCross" in statistics},
            ]}, 
            {"group": "query", "title": f"Query ({summarized_groups['query']})", "active": active_category in ["getMatchesForUnmappedBinary", "getMatchesForMappedBinary", "getMatchesForSmdaReport"], "available": True, "submenu": [
                {"name": "getMatchesForUnmappedBinary", "title": f"getMatchesForUnmappedBinary ({sum(statistics['getMatchesForUnmappedBinary'].values()) if 'getMatchesForUnmappedBinary' in statistics else 0})", "active": "getMatchesForUnmappedBinary" == active_category, "available": "getMatchesForUnmappedBinary" in statistics},
                {"name": "getMatchesForMappedBinary", "title": f"getMatchesForMappedBinary ({sum(statistics['getMatchesForMappedBinary'].values()) if 'getMatchesForMappedBinary' in statistics else 0})", "active": "getMatchesForMappedBinary" == active_category, "available": "getMatchesForMappedBinary" in statistics},
                {"name": "getMatchesForSmdaReport", "title": f"getMatchesForSmdaReport ({sum(statistics['getMatchesForSmdaReport'].values()) if 'getMatchesForSmdaReport' in statistics else 0})", "active": "getMatchesForSmdaReport" == active_category, "available": "getMatchesForSmdaReport" in statistics},
            ]}, 
            {"group": "getUniqueBlocks", "title": f"Blocks ({summarized_groups['blocks']})", "active": "getUniqueBlocks" == active_category, "available": "getUniqueBlocks" in statistics},
            # the admin page's maintenance jobs are filed here too, pichash and family
            # statistics included; the tab is active for every entry it opens onto
            {"group": "minhashing", "title": f"Minhashing ({summarized_groups['minhashing']})", "active": active_category in ["updateMinHashesForSample", "updateMinHashes", "rebuildIndex", "recalculateMinHashes", "recalculatePicHashes", "repairMinHashes", "recomputeFamilyStats", "rebuildPicBlockHashIndex"], "available": True, "submenu": [
                {"name": "updateMinHashesForSample", "title": f"updateMinHashesForSample ({sum(statistics['updateMinHashesForSample'].values()) if 'updateMinHashesForSample' in statistics else 0})", "active": "updateMinHashesForSample" == active_category, "available": "updateMinHashesForSample" in statistics},
                {"name": "updateMinHashes", "title": f"updateMinHashes ({sum(statistics['updateMinHashes'].values()) if 'updateMinHashes' in statistics else 0})", "active": "updateMinHashes" == active_category, "available": "updateMinHashes" in statistics},
                {"name": "rebuildIndex", "title": f"rebuildIndex ({sum(statistics['rebuildIndex'].values()) if 'rebuildIndex' in statistics else 0})", "active": "rebuildIndex" == active_category, "available": "rebuildIndex" in statistics},
                {"name": "recalculateMinHashes", "title": f"recalculateMinHashes ({sum(statistics['recalculateMinHashes'].values()) if 'recalculateMinHashes' in statistics else 0})", "active": "recalculateMinHashes" == active_category, "available": "recalculateMinHashes" in statistics},
                {"name": "recalculatePicHashes", "title": f"recalculatePicHashes ({sum(statistics['recalculatePicHashes'].values()) if 'recalculatePicHashes' in statistics else 0})", "active": "recalculatePicHashes" == active_category, "available": "recalculatePicHashes" in statistics},
                {"name": "repairMinHashes", "title": f"repairMinHashes ({sum(statistics['repairMinHashes'].values()) if 'repairMinHashes' in statistics else 0})", "active": "repairMinHashes" == active_category, "available": "repairMinHashes" in statistics},
                {"name": "recomputeFamilyStats", "title": f"recomputeFamilyStats ({sum(statistics['recomputeFamilyStats'].values()) if 'recomputeFamilyStats' in statistics else 0})", "active": "recomputeFamilyStats" == active_category, "available": "recomputeFamilyStats" in statistics},
                {"name": "rebuildPicBlockHashIndex", "title": f"rebuildPicBlockHashIndex ({sum(statistics['rebuildPicBlockHashIndex'].values()) if 'rebuildPicBlockHashIndex' in statistics else 0})", "active": "rebuildPicBlockHashIndex" == active_category, "available": "rebuildPicBlockHashIndex" in statistics},
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
    limit_param = "l" if active_category is None else "plimit"
    if query:
        # The backend's own `filter` cannot be used together with paging: it slices the
        # page first and filters what is left (mcrit QueueRemoteCalls.getQueueData), so
        # `start=0, limit=25, filter=x` answers "the matches among jobs 0-24", not "the
        # first 25 matches". Asking for page 2 of a search would then skip matches, and
        # page 1 of a search whose only hit is job 60 renders empty. Fetch the category
        # unpaged and filter here, where the count that drives pagination is the count
        # of things actually shown. `getQueueData(method=...)` with no start/limit is
        # already how delete_job_by_id enumerates a category.
        queue = client.getQueueData(method=active_category, state=state_category, ascending=ascending)
        if queue is None:
            # a failed read is not an empty result: "0 jobs matching" would be a claim
            # about the queue that nobody could check
            flash("Ups, reading MCRIT's job queue failed - the search could not be run.", category="error")
        matches = [job for job in queue or [] if query.casefold() in job_parameters_or_blank(job).casefold()]
        pagination = Pagination(request, len(matches), limit=25, query_param="p", limit_param=limit_param)
        jobs = matches[pagination.start_index:pagination.end_index]
    else:
        if active_category is None:
            max_count = statistics["totals"][state_category] if state_category in statistics["totals"] else 0
        else:
            # getQueueStatistics only reports categories that have at least one job, so a
            # type that has never run - or whose jobs were all deleted through this page's
            # own per-category delete - is absent, and indexing it was a 500
            max_count = sum(statistics.get(active_category, {}).values())
        pagination = Pagination(request, max_count, limit=25, query_param="p", limit_param=limit_param)
        jobs = client.getQueueData(start=pagination.start_index, limit=pagination.limit, method=active_category, state=state_category, ascending=ascending)
    samples_by_id = {}
    families_by_id = {}
    described_jobs = describable_jobs(jobs)
    for job in described_jobs:
        if job.sample_ids is not None:
            for sample_id in [sid for sid in job.sample_ids if sid not in samples_by_id]:
                samples_by_id[sample_id] = client.getSampleById(sample_id)
    for job in described_jobs:
        if job.family_id is not None and job.family_id not in families_by_id:
            families_by_id[job.family_id] = client.getFamily(job.family_id)
    return render_template('jobs.html', families=families_by_id, samples=samples_by_id, active=active_category, state=state_category, ascending=ascending, order_toggle=order_toggle, jobs=jobs, menu_configuration=menu_configuration, p=pagination, query=query, match_count=len(matches) if query and queue is not None else None)


@bp.route('/jobs/<job_id>')
@visitor_required
@mcrit_server_required
def job_by_id(job_id):
    auto_refresh = 0
    auto_forward = 0
    client = get_client()
    suppress_processing_message = False
    # absent or not a number reads as 0, rather than a 500 for a hand-edited URL
    auto_refresh = parse_integer_query_param(request, "refresh") or 0
    auto_forward = parse_integer_query_param(request, "forward") or 0

    job_info = None
    if JOB_ID_PATTERN.fullmatch(job_id):
        job_id = job_id.lower()
        job_info = with_server_errors(client).getJobData(job_id)
    if auto_refresh and job_info and job_info.is_failed:
        auto_refresh = 0
        suppress_processing_message = True
        flash('The job failed!', category='error')

    if job_info is None:
        return render_template("job_invalid.html", job_id=job_id)

    # Before anything reads the parameters, because everything below does and the
    # overview template does too - its h1 and the job_column_table macro both print
    # them. Rendering the overview anyway would fail two ways depending on the shape:
    # a JSONDecodeError or TypeError out of Jinja for some, and for the ones that
    # raise AttributeError a blank name, which Jinja's getattr swallows into a page
    # indistinguishable from a job that simply has no parameters. This is also where
    # a great many redirects land - every job submitter, and any route that refuses a
    # job and sends the caller back to it - and a refusal that lands on a 500 has not
    # refused anything, because the flash it set is never rendered.
    parameters = job_parameters_or_none(job_info)
    if parameters is None:
        return render_template("job_corrupted.html", job_info=job_info, reason=UNREADABLE_PAYLOAD_REASON)

    if job_info.finished_at is not None:
        if auto_forward:
            # not forwarded for an unreadable job: data.result dispatches on the same
            # parameters, so it would only move the failure one page on
            if 'addBinarySample' in parameters:
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
    if 'addBinarySample' in parameters and not suppress_processing_message and auto_refresh:
        flash('We received your sample, currently processing!', category='info')
    # the dependencies in a few requests, see `dependency_jobs`. One can be gone by the
    # time this page is opened - deleted through this app's own job delete, which also
    # has a "delete every job of this method" form, or cleaned up in the backend - and
    # is then simply not in the answer. A failed read counts its dependencies as
    # missing rather than failing the overview, as a None per dependency used to.
    dependencies = list(dict.fromkeys(job_info.all_dependencies))
    resolved_children = dependency_jobs(client, dependencies)
    missing_children = len(dependencies) - len(resolved_children)
    child_jobs = sorted(resolved_children, key=lambda x: x.number)
    samples_by_id = {}
    families_by_id = {}
    # A child whose own payload cannot be read must not take its parent's page down:
    # the lookups below read that payload, and so does the row macro. Both degrade to
    # the one row rather than the page - a cross compare's children are the jobs it did
    # the work in, so dropping one would misreport what it ran (#115).
    described_children = describable_jobs(child_jobs)
    if described_children:
        samples_by_id.update(get_sample_entries(sample_id for job in described_children for sample_id in job.sample_ids or []))
        families_by_id.update(get_family_entries(job.family_id for job in described_children if job.family_id is not None))
    # only a page that will poll needs it, so a finished job's page never reads it
    job_state = job_overview_state(job_info, child_jobs if dependencies else None) if auto_refresh > 0 and not job_info.finished_at else None
    return render_template('job_overview.html', families=families_by_id, samples=samples_by_id, job_info=job_info, job_state=job_state, auto_refresh=auto_refresh, child_jobs=child_jobs, child_progress=dependency_progress(child_jobs), missing_children=missing_children, can_rerun=is_rerunnable(job_info, child_jobs), configuration_url=configuration_url(job_info, child_jobs))


def job_overview_state(job_info, child_jobs=None):
    """What the overview of a running job shows that can still change, read off the job
    and, for a job waiting on sub-jobs, off those sub-jobs - never the samples and
    families they name.

    The overview polls `job_status` for this and reloads once the answer differs from
    what it rendered. A reload fetches every sub-job and every sample they name, so it
    should happen when there is something new to show, not on every tick. See #183.

    A job waiting on sub-jobs does not start until the last of them is done, so its own
    progress reads 0 while they run, and the overview shows their average instead (#46).
    `child_jobs` are the sub-jobs the backend still has; with them the state also
    carries that average and how many it covers, for the page to update in place.
    Neither is a reload trigger: a sub-job's progress moves on every tick.
    """
    state = {
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
    if child_jobs is not None:
        known = [job for job in child_jobs if job is not None]
        state["dependency_progress"] = dependency_progress(known)
        state["dependencies"] = len(known)
    return state


@bp.route('/jobs/<job_id>/status')
@visitor_required
@mcrit_server_required
def job_status(job_id):
    """The job overview's polling endpoint, as JSON: one getJobData, and for an
    unfinished job waiting on sub-jobs one queue read per JOB_IDS_PER_REQUEST of them -
    still no samples or families, which are most of what a reload reads."""
    client = get_client()
    job_info = client.getJobData(job_id.lower()) if JOB_ID_PATTERN.fullmatch(job_id) else None
    if job_info is None:
        return {"error": "unknown job"}, 404
    child_jobs = None
    dependencies = list(dict.fromkeys(getattr(job_info, "all_dependencies", None) or []))
    if dependencies and not job_info.finished_at:
        child_jobs = dependency_jobs(client, dependencies)
    return job_overview_state(job_info, child_jobs)


#: job ids per `getQueueData(job_ids=...)` request. The ids travel in the query string,
#: 25 characters each: 250 of them, a cross compare of MAX_SELECTED_SAMPLES samples, make
#: a request line of about 6,250 bytes, over gunicorn's default limit_request_line of
#: 4,094 (mcrit's USE_GUNICORN). 100 keep it at about 2,530.
JOB_IDS_PER_REQUEST = 100


def render_job_in_progress(client, job_info):
    """The progress page of a job that has not finished. A job with dependencies gets
    them fetched, since its total is measured up to the latest time the backend wrote
    on one of them - see `total_duration`."""
    dependencies = list(dict.fromkeys(job_info.all_dependencies or []))
    child_jobs = dependency_jobs(client, dependencies) if dependencies else None
    return render_template("job_in_progress.html", job_info=job_info, child_jobs=child_jobs)


def dependency_jobs(client, job_ids):
    """The jobs of `job_ids` the backend still has, read `JOB_IDS_PER_REQUEST` at a time.

    A failed read loses only its own ids, which the caller counts as missing. A backend
    older than mcrit 1.12 ignores `job_ids` and answers the whole queue: such an answer
    already holds every job there is, so the reads stop there, and the answer is kept
    to the ids asked for.
    """
    asked = set(job_ids)
    answered = []
    for start in range(0, len(job_ids), JOB_IDS_PER_REQUEST):
        chunk = job_ids[start:start + JOB_IDS_PER_REQUEST]
        jobs = client.getQueueData(job_ids=chunk) or []
        answered.extend(jobs)
        chunk_ids = set(chunk)
        if any(job.job_id not in chunk_ids for job in jobs):
            break
    return list({job.job_id: job for job in answered if job.job_id in asked}.values())


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
    elif JOB_ID_PATTERN.fullmatch(job_id):
        client.deleteJob(job_id.lower())
    return redirect(url_for("data.jobs"))
    


################################################################
# Rerunning a job
################################################################
#
# A job records the call that produced it: payload["params"] is a JSON object of the
# arguments the backend method was called with, keyed by position ("0", "1", ...) for
# the positional ones and by name for the rest - see mcrit's QueueRemoteCalls. That is
# enough to submit the same request again, but only where "the same request" is still
# a well-defined thing to ask for, so the mapping below is deliberately short:
#
#   * the query methods (getMatchesForSmdaReport and the two binary ones) are left out
#     because the binary they matched lives in the backend's GridFS. The job holds a
#     reference to it, not the bytes, and McritClient cannot resubmit from a reference.
#   * getUniqueBlocks is left out because neither of its request methods takes
#     force_recalculation, so repeating one is answered from the cache with the very
#     job being looked at.
#   * the collection and minhashing methods are left out because they are not analyses
#     to repeat - deleteSample and deleteFamily destroy, addBinarySample has no binary
#     to resend, and the maintenance jobs have their own buttons on the admin page.
#
# Reconstructing the wrong request would be worse than offering no button: the user
# would believe they had rerun what is on screen. So every step below refuses rather
# than guesses, and a parameter that cannot be read disqualifies the whole rerun
# instead of being dropped from it.
#
# All of this is the fallback. A McritClient with requestJobRerun asks mcrit to rerun
# the job from what it stored, which covers the queries and unique blocks as well; see
# server_reruns and SERVER_RERUNNABLE_METHODS.

#: job method -> the McritClient method that submits it again.
RERUNNABLE_METHODS = {
    "getMatchesForSample": "requestMatchesForSample",
    "getMatchesForSampleVs": "requestMatchesForSampleVs",
    "combineMatchesToCross": "requestMatchesCross",
}

#: The matching parameters mcrit stores on a job under their own name; McritClient
#: takes them back as keywords of the same name. force_recalculation is deliberately
#: not among them - QueueRemoteCalls consumes it before the payload is written, so no
#: job records whether it was forced.
#:
#: Since mcrit 1.13 a job carries every one of these that applies to it, not only the
#: ones its request named: the backend fills in the rest from its configuration before
#: queueing (`resolveMatchingParams` in mcrit/index/MatchingParameters.py, called from
#: `MinHashIndex._submitMatchingJob` and `server/utils.py` `getMatchingParams`). A
#: match restricted to the samples it names (1 vs 1, 1 vs group) takes no shortlist and
#: carries none, hence the two lists.
RERUN_MATCHING_PARAMS = ("minhash_threshold", "pichash_size", "band_matches_required", "band_df_cutoff", "shortlist_size")
RESTRICTED_MATCHING_PARAMS = ("minhash_threshold", "pichash_size", "band_matches_required", "band_df_cutoff")

#: The matching parameters each method takes back. McritClient's requestMatchesForSample
#: takes all of them; requestMatchesForSampleVs and requestMatchesCross take no
#: shortlist_size (mcrit 1.16 McritClient.py:811-875).
METHOD_MATCHING_PARAMS = {
    "getMatchesForSample": RERUN_MATCHING_PARAMS,
    "getMatchesForSampleVs": RESTRICTED_MATCHING_PARAMS,
    "getMatchesForSampleVsGroup": RESTRICTED_MATCHING_PARAMS,
}

#: Recorded on a job but never sent: the backend marks a job whose shortlist it could
#: not apply when it was queued (`shortlist_unavailable`), and no client call takes
#: it. A rerun leaves it out, and the backend marks the new job again if the shortlist
#: still cannot be applied.
DERIVED_MATCHING_PARAMS = ("shortlist_unavailable",)

#: The value a backend gives the knobs the analyze forms have no control for, unless
#: its deployment configures another: mcrit's own defaults, unchanged from 1.12 to
#: 1.16 (MinHashConfig.py:23, :32, :82, StorageConfig.py STORAGE_BAND_DF_CUTOFF).
FORM_DEFAULT_MATCHING_PARAMS = {
    "minhash_threshold": MinHashConfig.MINHASH_MATCHING_THRESHOLD,
    "pichash_size": MinHashConfig.PICHASH_SIZE,
    "band_df_cutoff": StorageConfig.STORAGE_BAND_DF_CUTOFF,
    "shortlist_size": MinHashConfig.MINHASH_MATCHING_SHORTLIST_SIZE,
}

#: The two child methods a cross compare is built from, and what each one means for
#: `sample_group_only` - the only place that choice survives.
CROSS_CHILD_METHODS = {"getMatchesForSample": False, "getMatchesForSampleVsGroup": True}

#: How many positional arguments each of those backend methods takes, so that a
#: payload carrying one this does not know about can be recognised as such. Together
#: with METHOD_MATCHING_PARAMS and DERIVED_MATCHING_PARAMS this is the complete
#: parameter list of each method as of mcrit 1.16 (MATCHING_KNOBS in
#: mcrit/index/MatchingParameters.py) - a version that records a further one has to
#: be handled here, and until it is, the rerun is withheld rather than submitted
#: without it.
METHOD_ARITY = {
    "getMatchesForSample": 1,
    "getMatchesForSampleVs": 2,
    "getMatchesForSampleVsGroup": 2,
    "combineMatchesToCross": 1,
}


def job_params(job_info):
    """A job's own parameters as {index_or_name: value}, or None if unreadable.

    `Job.arguments` parses the same payload but raises on anything that is not the
    JSON object it expects, and deciding whether to offer a rerun must not be able to
    take the job page down.
    """
    try:
        payload = job_info.payload
    except (AttributeError, KeyError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        params = json.loads(payload.get("params", ""))
    except (TypeError, ValueError):
        return None
    return params if isinstance(params, dict) else None


def as_job_int(value):
    """An integer parameter, or None. A bool is an int in Python and never one of these."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def matching_kwargs(params, method):
    """The named matching parameters of a job, or None if they cannot all be honoured.

    Unreadable is one way to get None; the other is a parameter this does not know
    about, positional or named. Both would otherwise be quietly left out of the
    resubmitted call, which is how a rerun ends up being a *different* comparison than
    the one on screen - so either disqualifies the job instead.
    """
    accepted = METHOD_MATCHING_PARAMS[method]
    known = set(accepted) | set(DERIVED_MATCHING_PARAMS) | {str(position) for position in range(METHOD_ARITY[method])}
    if set(params) - known:
        return None
    kwargs = {}
    for name in accepted:
        if name in params:
            value = as_job_int(params[name])
            if value is None:
                return None
            kwargs[name] = value
    return kwargs


def cross_rerun_request(params, child_jobs):
    """The requestMatchesCross call behind a combineMatchesToCross job, or None.

    That job records only {sample_id: child_job_id}. Which comparison ran is the
    choice between the two child methods, and the matching parameters exist only on
    the children - so a rerun is offered exactly when every child named in the mapping
    is still there and they all agree.
    """
    # this method takes the mapping and nothing else, so anything further in the
    # payload is a parameter that would be lost on the way back out
    if set(params) != {"0"}:
        return None
    sample_to_job_id = params["0"]
    if not isinstance(sample_to_job_id, dict) or not sample_to_job_id:
        return None
    children_by_id = {}
    for child in child_jobs or []:
        try:
            children_by_id[child.job_id] = child
        except (AttributeError, KeyError, TypeError):
            continue
    sample_ids = []
    methods = set()
    child_kwargs = []
    for sample_key, child_job_id in sample_to_job_id.items():
        try:
            sample_ids.append(int(sample_key))
        except (TypeError, ValueError):
            return None
        child = children_by_id.get(child_job_id)
        child_params = job_params(child) if child is not None else None
        if child_params is None or child.method not in CROSS_CHILD_METHODS:
            return None
        methods.add(child.method)
        kwargs = matching_kwargs(child_params, child.method)
        if kwargs is None:
            return None
        # mcrit's getMatchesCross asks its 1-vs-corpus children for no shortlist, as
        # shortlist_size=0 (MinHashIndex.py:562-573 in 1.16), and requestMatchesCross
        # takes none. Any other size is not a child a cross compare makes.
        if kwargs.pop("shortlist_size", 0) != 0:
            return None
        child_kwargs.append(kwargs)
    if len(methods) != 1 or any(kwargs != child_kwargs[0] for kwargs in child_kwargs):
        return None
    sample_group_only = CROSS_CHILD_METHODS[methods.pop()]
    return client_takes(RERUNNABLE_METHODS["combineMatchesToCross"], (sample_ids,), dict(child_kwargs[0], sample_group_only=sample_group_only))


def client_takes(method_name, args, kwargs):
    """(method_name, args, kwargs) if the installed McritClient takes these keywords, else None.

    requirements.txt allows mcrit 1.12, whose request methods take neither
    band_df_cutoff nor shortlist_size, and a 1.13 or later backend records both. That
    client would raise a TypeError on the rerun; leaving a knob out instead would run
    another comparison than the one on screen.
    """
    try:
        parameters = inspect.signature(getattr(McritClient, method_name)).parameters
    except (AttributeError, TypeError, ValueError):
        return None
    if not any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters.values()):
        if not set(kwargs) | {"force_recalculation"} <= set(parameters):
            return None
    return method_name, args, kwargs


def rerun_request(job_info, child_jobs=None):
    """The McritClient call that reproduces `job_info`, as (method_name, args, kwargs).

    None means this job has no rerun: either its method is not one of the three above,
    or its stored parameters do not describe the original request completely.
    """
    if job_info is None:
        return None
    params = job_params(job_info)
    if params is None or job_info.method not in RERUNNABLE_METHODS:
        return None
    if job_info.method == "combineMatchesToCross":
        return cross_rerun_request(params, child_jobs)
    kwargs = matching_kwargs(params, job_info.method)
    if kwargs is None:
        return None
    sample_ids = [as_job_int(params.get(str(position))) for position in range(METHOD_ARITY[job_info.method])]
    if any(sample_id is None for sample_id in sample_ids):
        return None
    return client_takes(RERUNNABLE_METHODS[job_info.method], tuple(sample_ids), kwargs)


def has_run_its_course(job_info):
    """Whether a job is finished or failed, as opposed to queued or in progress.

    Forcing a recalculation of a job that is still running queues the same work a
    second time, so this gates both the button and the route that button posts to.
    Withholding the button alone would only hide the door - the POST is reachable
    without it.
    """
    if job_info is None:
        return False
    try:
        return job_info.finished_at is not None or bool(job_info.is_failed)
    except (AttributeError, KeyError, TypeError):
        # a record that does not carry the fields is not one to rerun either
        return False


#: The analyses mcrit reruns itself, its MinHashIndex.RERUNNABLE_METHODS
#: (familiary/mcrit#311): every method that analyses the collection, the queries, unique
#: blocks and 1-vs-group comparisons that RERUNNABLE_METHODS cannot rebuild included, and
#: none that changes it. The backend checks the job again and refuses what it will not
#: rerun; this decides where the button is offered.
SERVER_RERUNNABLE_METHODS = frozenset({
    "getMatchesForSample",
    "getMatchesForSampleVs",
    "getMatchesForSampleVsGroup",
    "getMatchesForSmdaReport",
    "getMatchesForMappedBinary",
    "getMatchesForUnmappedBinary",
    "getUniqueBlocks",
    "combineMatchesToCross",
})


def server_reruns():
    """Whether the installed McritClient can ask the backend to rerun a job itself, by
    `requestJobRerun` (POST /jobs/<job_id>/rerun, familiary/mcrit#311). The backend
    queues the job again from the payload it stored, input files included, so it covers
    every analysis rather than the three rerun_request can rebuild from parameters.

    Asked of the class, as client_takes asks it about keywords, by both the button
    (is_rerunnable) and the route, so the two agree."""
    return callable(getattr(McritClient, "requestJobRerun", None))


def may_rerun_query(job_info):
    """Whether the logged-in user may have the backend rerun this query job.

    A rerun resends no upload, so QUERY_UPLOAD_LIMITS has nothing to measure, but it
    repeats a stored query that may be larger than the user's role could have uploaded
    - and a binary query is disassembled again. So it is left to a role with no cap,
    and to whoever submitted the query, whose upload passed theirs."""
    if current_app.config.get('QUERY_UPLOAD_LIMITS', {}).get(g.user.role) is None:
        return True
    try:
        return job_info.username is not None and job_info.username == g.user.username
    except (AttributeError, KeyError, TypeError):
        return False


def has_stopped(job_info):
    """Finished, failed or terminated: the states mcrit reruns a job from."""
    if has_run_its_course(job_info):
        return True
    try:
        return bool(job_info.is_terminated)
    except (AttributeError, KeyError, TypeError):
        return False


def is_rerunnable(job_info, child_jobs=None):
    """Whether to offer a rerun of this job on its page: any analysis that has stopped
    where the backend reruns jobs itself, else one whose request rerun_request rebuilds."""
    if server_reruns():
        try:
            method = job_info.method
        except (AttributeError, KeyError, TypeError):
            return False
        if method in QUERY_UPLOAD_KINDS and not may_rerun_query(job_info):
            return False
        return method in SERVER_RERUNNABLE_METHODS and has_stopped(job_info)
    if not has_run_its_course(job_info):
        return False
    return rerun_request(job_info, child_jobs) is not None


def configuration_url(job_info, child_jobs=None):
    """The analyze page this job was submitted from, with its inputs filled in.

    Issue #55's other half: from a finished job back to the form behind it, so the
    parameters can be changed and the job resubmitted rather than retyped. Built on
    `rerun_request` so there is one recovery of a job's arguments and not two - a job
    whose request cannot be rebuilt faithfully gets no link here either.

    Preselecting is not the same as passing the ids along. `analyze.compare` and
    `analyze.compare_versus` highlight a row only when the sample is on the search
    page in front of them, and compare.html falls back to selecting the *first* row
    when it is not - so a bare `selected=` would quietly point the form at a
    different sample. Each link therefore also carries the search that puts the
    sample on the page: searching a sample id makes mcrit answer with that sample
    (`id_match`), regardless of where it would otherwise fall in the paging.

    None means no link at all, which is the honest answer whenever the form cannot
    represent the job it claims to be showing.

    Not gated on the job having finished, unlike the rerun: following a link queues
    nothing, and reopening the form of a job still in the queue to submit a variation
    of it is a reasonable thing to want.
    """
    request_to_repeat = rerun_request(job_info, child_jobs)
    if request_to_repeat is None:
        return None
    method_name, args, kwargs = request_to_repeat
    kwargs = dict(kwargs)
    sample_group_only = kwargs.pop("sample_group_only", False)
    # `band_matches_required` is the only matching parameter these forms have a
    # control for (analyze.py `start_cross_compare`, `compare_all` and `compare_vs`
    # send only it). Submitting one leaves the others to the backend, which from
    # mcrit 1.13 on records them on every job, filled in from its configuration
    # (`resolveMatchingParams`). So a job may carry them at mcrit's defaults, which
    # is what the form gets again; one that ran with another value cannot be shown
    # on the form. A job carrying no band_matches_required took the backend's own
    # default rather than a slider position, which the slider - which always submits
    # one - cannot reproduce either.
    if "band_matches_required" not in kwargs:
        return None
    if any(kwargs[name] != FORM_DEFAULT_MATCHING_PARAMS[name] for name in kwargs if name != "band_matches_required"):
        return None
    slider_position = slider_position_for_band_range(kwargs["band_matches_required"])
    if slider_position is None:
        return None
    # `rematch` is left at each page's default: force_recalculation is consumed by
    # QueueRemoteCalls before the payload is written, so no job records whether it
    # was forced and preselecting either way would be an invention.
    if method_name == "requestMatchesCross":
        return url_for('analyze.cross_compare', samples=",".join(str(sample_id) for sample_id in args[0]),
                       onlySelected="true" if sample_group_only else "false", minhashBandRange=slider_position)
    if method_name == "requestMatchesForSample":
        return url_for('analyze.compare', query=args[0], selected=args[0], minhashBandRange=slider_position)
    if method_name == "requestMatchesForSampleVs":
        return url_for('analyze.compare_versus', query_a=args[0], selected_a=args[0],
                       query_b=args[1], selected_b=args[1], minhashBandRange=slider_position)
    # a method added to RERUNNABLE_METHODS has to say which form preselects it, and
    # how, before it can be linked to one
    return None


@bp.route('/jobs/<job_id>/rerun', methods=('POST',))
@visitor_required
@mcrit_server_required
def rerun_job_by_id(job_id):
    """Submit the request a job was created from once more.

    POST only, like every other route that changes something: this queues backend work
    and an <img> tag or a prefetch must not be able to start it (issue #84).

    Visitor, because it grants nothing a visitor does not already have - the same three
    requests are reachable from /analyze/compare/<id>, /analyze/compare/<a>/<b> and
    /analyze/start_cross_compare, `rematch` included, and the analyses mcrit reruns
    itself from the analyze pages too. The one exception is a query, which a capped
    role may only rerun when it was theirs (see may_rerun_query).

    Nothing from the request is forwarded to the backend except `job_id` itself, and
    only once it is a job id by JOB_ID_PATTERN, as in `job_by_id`. Where the installed
    client has `requestJobRerun` (see server_reruns) the backend reruns the job from
    what it stored; otherwise what is submitted is rebuilt here from the stored job, read
    with the same getJobData that `job_by_id` already calls for any visitor, and not from
    anything the caller sent.
    """
    client = get_client()
    if not JOB_ID_PATTERN.fullmatch(job_id):
        flash('There is no job with that id.', category='error')
        return redirect(url_for('data.jobs'))
    if server_reruns():
        job_info = client.getJobData(job_id)
        if job_info is None:
            flash('There is no job with that id.', category='error')
            return redirect(url_for('data.jobs'))
        if job_info.method in QUERY_UPLOAD_KINDS and not may_rerun_query(job_info):
            flash('Only the user who submitted this query can rerun it with your role, since it may be larger than your upload limit.', category='error')
            return redirect(url_for('data.job_by_id', job_id=job_id))
        # mcrit checks the job as the rebuild below does, and answers None for one it does
        # not know, one still queued or running, one that is no analysis and one whose
        # input file is no longer stored
        new_job_id = client.requestJobRerun(job_id)
        if not new_job_id:
            flash('The backend did not rerun this job. It reruns an analysis that has finished, failed or been terminated, while it still has the input.', category='error')
            return redirect(url_for('data.job_by_id', job_id=job_id))
        return redirect(url_for('data.job_by_id', job_id=new_job_id, refresh=3))
    job_info = client.getJobData(job_id)
    if job_info is None:
        flash('There is no job with that id.', category='error')
        return redirect(url_for('data.jobs'))
    if not has_run_its_course(job_info):
        # the button is withheld for a job that is still running, but withholding it
        # only hides the door: this POST is reachable without it, and forcing a
        # recalculation here would queue the same work alongside the run in progress
        flash('This job has not finished yet - wait for it rather than running it twice.', category='error')
        return redirect(url_for('data.job_by_id', job_id=job_id))
    child_jobs = None
    if job_info.method == "combineMatchesToCross":
        # only this one method needs them, so the round trips are not spent on the
        # other two. `all_dependencies` is a bare lookup in mcrit's Job and raises for
        # a record that does not carry the field. Read in batches, as `job_by_id`
        # reads them; a child that is gone is missing from the answer, and
        # `cross_rerun_request` refuses a mapping that names it.
        try:
            dependencies = job_info.all_dependencies
        except (KeyError, TypeError):
            dependencies = None
        child_jobs = dependency_jobs(client, list(dict.fromkeys(dependencies))) if isinstance(dependencies, list) else []
    request_to_repeat = rerun_request(job_info, child_jobs)
    if request_to_repeat is None:
        flash('This job cannot be rerun: its original request is not fully recorded.', category='error')
        return redirect(url_for('data.job_by_id', job_id=job_id))
    method_name, args, kwargs = request_to_repeat
    # Forced, or mcrit answers from its descriptor cache with the job we started from
    # and the rerun is indistinguishable from a reload. This is the deliberate opposite
    # of the analyze routes, which must stay cacheable because they write on GET (#97).
    new_job_id = getattr(client, method_name)(*args, force_recalculation=True, **kwargs)
    if not new_job_id:
        flash('The backend did not accept the rerun; the samples it needs may be gone.', category='error')
        return redirect(url_for('data.job_by_id', job_id=job_id))
    return redirect(url_for('data.job_by_id', job_id=new_job_id, refresh=3))


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
    # a client of this endpoint other than the dropzone can send any JSON value here
    if not isinstance(filename, str) or not isinstance(file_header, str):
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
        # at most 20 digits, a 64-bit address: hex() of a longer int is fine, but int()
        # of more than 4300 digits raised here
        match_baseaddr = re.search(r'"base_addr": (?P<base_addr>\d{1,20})(?!\d)', file_header)
        if match_baseaddr:
            result['base_addr'] = hex(int(match_baseaddr.group('base_addr')))
    elif parseIsDumpFromFilename(filename):
        result['dump'] = True
        base_address, result['bitness'] = parseBaseAddrAndBitnessFromFilename(filename)
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
    # the view returned nothing for any other form_type, which Flask turns into a 500
    return "", 400 # Bad Request


#: The shape of the `sha256` an .smda report declares. smda copies that field as it
#: finds it, and McritClient.getSampleBySha256 puts it into the request path unquoted,
#: so "../../rebuild_index" asked the backend for an admin-only route instead.
#: `\Z` rather than `$`, which would also match before a trailing newline.
SMDA_SHA256 = re.compile(r"^[0-9a-fA-F]{64}\Z")

#: What a binary upload of a sample MCRIT has not verified says (familiary/mcrit#270).
REPLACING_ANALYSIS_MESSAGE = (
    "Sample {sample_id} already has this sha256, but MCRIT has not verified it against a binary. "
    "MCRIT is replacing its stored analysis with its own disassembly of this file."
)


def awaits_binary_verification(sample_entry):
    """Whether uploading this sample's binary lets MCRIT replace its analysis by its own.

    mcrit answers a binary upload of a known sha256 with 409 only for a sample whose
    `sha256_verified` is True (familiary/mcrit#306); for False (indexed from a
    submitted report or an import) or None (stored before that was recorded) it
    queues a job that disassembles the bytes and replaces the stored analysis under
    the same sample_id. An entry without the attribute, from an mcrit package that
    predates the field, is asked about too. An mcrit that predates #306 refuses with
    409, which `addBinarySample` answers as None, so the caller falls back to the
    sample that is there.
    """
    return getattr(sample_entry, "sha256_verified", None) is not True


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
        # only a memory dump needs these two: an SMDA report carries its own base address
        # and bitness, and addReport() - where the smda branch below ends - takes neither
        if is_dump:
            bitness = parse_bitness_form_param(request)
            if bitness is None:
                flash("Please select the bitness of the sample.", category='error')
                return "", 400 # Bad Request
            base_address = parse_base_addr_form_param(request)
            if base_address is None:
                flash("Please enter the base address of the sample as a hexadecimal number, e.g. 0x400000.", category='error')
                return "", 400 # Bad Request

        binary_content = f.read()
        if form_options == "smda":
            # the file is whatever was dropped on the page, so a body that is not a
            # readable SMDA report is ordinary user input and has to become a message.
            # It used to be covered by accident: the base address was demanded first,
            # and an empty one answered 400 before anything was parsed. That check is
            # now correctly limited to a dump, so this needs its own.
            try:
                smda_report = SmdaReport.fromDict(json.loads(binary_content))
            except Exception:
                current_app.logger.warning("data.submit - the uploaded file is not a readable SMDA report")
                flash('That file could not be read as an SMDA report.', category='error')
                return "", 400 # Bad Request
            upload_sha256 = smda_report.sha256
            if not isinstance(upload_sha256, str) or not SMDA_SHA256.match(upload_sha256):
                flash("The SMDA report does not carry a valid sha256.", category='error')
                return "", 400 # Bad Request
            # the corpus holds lowercase hashes and both lookups match exactly, as promote_query does
            upload_sha256 = smda_report.sha256 = upload_sha256.lower()
        else:
            # check here if it is already part of corpus
            upload_sha256 = hashlib.sha256(binary_content).hexdigest()
        sample_entry = client.getSampleBySha256(upload_sha256)
        if sample_entry is None:
            if form_options == "smda" and smda_report:
                new_sample_entry, job_id = require_result(with_server_errors(client).addReport(smda_report), "the sample for the submitted report")
                return url_for('explore.sample_by_id', sample_id=new_sample_entry.sample_id), 202 # Accepted
            else:
                # These three ride in the query string McritClient builds by hand, so
                # they are escaped rather than trusted. All three are str by
                # construction - `f` is None-checked above, and both form fields would
                # have raised a 400 before here. The cost is length: a 255-character
                # non-ASCII filename encodes to ~2.3 kB of request line, against
                # gunicorn's 4094-byte default for the whole line.
                job_id = require_result(with_server_errors(client).addBinarySample(binary_content,
                    filename=quote_backend_query_value(f.filename),
                    family=quote_backend_query_value(family),
                    version=quote_backend_query_value(version),
                    is_dump=is_dump, base_addr=base_address, bitness=bitness), "a job for the submitted sample")
                return url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1), 202 # Accepted
        if form_options != "smda" and awaits_binary_verification(sample_entry):
            # a report cannot verify anything, so only the binary itself is sent. Blank
            # fields go as None: mcrit applies whatever family or version the upload
            # names to the sample it replaces, and an empty one would clear them.
            job_id = client.addBinarySample(binary_content,
                filename=quote_backend_query_value(f.filename),
                family=quote_backend_query_value(family) if family else None,
                version=quote_backend_query_value(version) if version else None,
                is_dump=is_dump, base_addr=base_address, bitness=bitness)
            if job_id:
                flash(REPLACING_ANALYSIS_MESSAGE.format(sample_id=sample_entry.sample_id), category='info')
                return url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1), 202 # Accepted
            # an mcrit that predates replacing such samples refuses the upload with 409
        flash('Sample was already in database', category='warning')
        return url_for('explore.sample_by_id', sample_id=sample_entry.sample_id), 202 # Accepted
    return render_template('submit.html', show_submit_fields=True)


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
    job_info = client.getJobData(job_id.lower()) if JOB_ID_PATTERN.fullmatch(job_id) else None
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
    # an .smda query's report declares this value itself - see SMDA_SHA256
    if isinstance(upload_sha256, str) and SMDA_SHA256.match(upload_sha256):
        upload_sha256 = upload_sha256.lower()
    else:
        upload_sha256 = None
    if upload_sha256 is None:
        flash('The report of this query does not record which file it was run for, so it cannot be promoted.', category='error')
        return redirect(result_page)
    # whether the local copy survived or not, a sample that is already stored is the
    # answer to "promote this" - so promoting twice lands on it instead of adding it.
    # The exception is a binary query of a sample MCRIT has not verified against a
    # binary: its bytes are sent, and mcrit replaces the stored analysis by their
    # disassembly (see awaits_binary_verification). Two promotions racing past this
    # check still make one sample, but they are not told the same thing: addReport
    # answers a known sha256 with the entry that already exists, while
    # SampleResource.on_post_submit_binary refuses a verified one with 409, which
    # handle_response turns into None - so the second promotion of a binary query says
    # the sample could not be added. It is one sample all the same: the corpus is
    # checked again by Worker.addBinarySample when the job runs.
    sample_entry = client.getSampleBySha256(upload_sha256)
    upload_kind = QUERY_UPLOAD_KINDS[job_info.method]
    replacing = sample_entry is not None and upload_kind != "smda" and awaits_binary_verification(sample_entry)
    if sample_entry is not None and not replacing:
        flash('Sample was already in database', category='warning')
        return redirect(url_for('explore.sample_by_id', sample_id=sample_entry.sample_id))
    if not os.path.isfile(upload_path):
        if replacing:
            flash('Sample was already in database', category='warning')
            return redirect(url_for('explore.sample_by_id', sample_id=sample_entry.sample_id))
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
        if replacing:
            # an mcrit that predates replacing such samples refuses the upload with 409
            flash('Sample was already in database', category='warning')
            return redirect(url_for('explore.sample_by_id', sample_id=sample_entry.sample_id))
        flash('The sample could not be added to the database.', category='error')
        return redirect(result_page)
    if replacing:
        flash(REPLACING_ANALYSIS_MESSAGE.format(sample_id=sample_entry.sample_id), category='info')
    else:
        flash('The query was promoted to a sample.', category='success')
    return redirect(url_for('data.job_by_id', job_id=new_job_id, refresh=3, forward=1))
