import re

from flask import Blueprint, current_app, flash, g, json, redirect, render_template, request, url_for
from mcrit.storage.SampleEntry import SampleEntry
from smda.common.SmdaReport import SmdaReport

from mcritweb.db import remember_query_filename
from mcritweb.views.authentication import visitor_required
from mcritweb.views.client import get_client, get_sample_entries, remember_samples
from mcritweb.views.cursor_pagination import CursorPagination
from mcritweb.views.pagination import Pagination
from mcritweb.views.params import parse_band_range, parse_checkbox_query_param, parse_integer_list_query_param
from mcritweb.views.utility import mcrit_server_required, query_upload_path

bp = Blueprint('analyze', __name__, url_prefix='/analyze')

#: How many samples one unique blocks request may name. `requestUniqueBlocksForSamples`
#: puts the whole list in the request *path* it builds (`/uniqueblocks/samples/1,2,3`),
#: so an unbounded selection is a request line the mcrit server refuses rather than a
#: slow query. It also bounds the per-sample lookups the selection page performs.
#: Isolating the blocks unique to hundreds of samples is a family question anyway, and
#: `blocks_family` already asks that one by id.
MAX_SELECTED_SAMPLES = 250


def get_unique_samples_from_search_result(search_result):
    samples = []
    sample_ids = set()
    for sample_dict in search_result['search_results'].values():
        sample_entry = SampleEntry.fromDict(sample_dict)
        if sample_entry.sample_id not in sample_ids:
            samples.append(sample_entry)
            sample_ids.add(sample_entry.sample_id)
    id_match = search_result['id_match']
    if id_match is not None:
        # deserialize before reading the id, as the loop above does and as
        # explore.search does with the same value. `id_match` is a wire dict, and its
        # keys equalling the entry's attribute names is a coincidence this repository
        # does not control. See issue #64.
        id_match_entry = SampleEntry.fromDict(id_match)
        if id_match_entry.sample_id not in sample_ids:
            samples.append(id_match_entry)
    return samples


@bp.route('/blocks/family/<int:family_id>')
@visitor_required
@mcrit_server_required
def blocks_family(family_id):
    client = get_client()
    family_samples = client.getSamplesByFamilyId(family_id)
    if family_samples:
        job_id = client.requestUniqueBlocksForFamily(family_id)
        return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))
    else:
        flash("Can't locate unique blocks for a family without samples", category="error")
        return redirect(url_for('explore.families'))


@bp.route('/blocks/sample/<int:sample_id>')
@visitor_required
@mcrit_server_required
def blocks_sample(sample_id):
    client = get_client()
    job_id = client.requestUniqueBlocksForSamples([sample_id])
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/unique_blocks')
@visitor_required
@mcrit_server_required
def unique_blocks():
    """Pick the sample set to isolate unique blocks for. See issue #93.

    The backend has always accepted a list - only the cubes button on a sample row,
    which passes exactly one id, reached it. The YARA rule parameters are deliberately
    not here: they are not job parameters, `data.result` applies them to the cached
    result at render time, so they belong on the page that shows the rule.
    """
    client = get_client()

    selected_list = parse_integer_list_query_param(request, 'samples') or []
    if request.args.get('samples') and not selected_list:
        flash('The selected samples were not a list of sample ids.', category='error')
    # order and repetition carry no meaning for a set of samples, and normalizing here
    # keeps the selection stable across page loads and the submit link deduplicated
    selected_list = sorted(set(selected_list))
    if len(selected_list) > MAX_SELECTED_SAMPLES:
        flash(f'A unique blocks request can name at most {MAX_SELECTED_SAMPLES} samples, the rest of the selection was dropped.', category='warning')
        selected_list = selected_list[:MAX_SELECTED_SAMPLES]

    query = request.args.get('query', "")
    samples = []
    pagination = CursorPagination(request, default_sort="sample_id")
    results = client.search_samples(query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is not None:
        samples = get_unique_samples_from_search_result(results)
        # a selected sample listed in the search below needs no lookup of its own
        remember_samples(samples)

    pagination_selected = Pagination(request, len(selected_list), limit=10, query_param="ps", limit_param="psl")
    # id -> entry, or None for one the backend would not resolve. Only the page being
    # rendered is looked up: McritClient has no batched sample lookup, so resolving the
    # whole selection here would cost one round trip per selected sample on every page
    # view. start_unique_blocks checks the rest, once, on a deliberate submit.
    selected_dict = get_sample_entries(selected_list[pagination_selected.start_index:pagination_selected.start_index + pagination_selected.limit])
    unresolved_ids = [sample_id for sample_id, sample in selected_dict.items() if sample is None]
    if unresolved_ids:
        # kept in the selection, not dropped. `handle_response` answers None for a 500 as
        # readily as for a 404, so this is not evidence that the sample is gone - and
        # editing someone's sample set on it means the next submit quietly analyses a
        # different set. The row renders unresolved, with the same remove button as the
        # others, so it is the reader who decides.
        #
        # Dropping them also meant redirecting to a cleaned selection, and the page only
        # ever checks the ten ids it is rendering: a selection of 250 stale ids unwound
        # ten at a time, which is 25 redirect hops. Browsers stop following around 20, so
        # the selection that most needed cleaning was the one that could not load at all.
        flash(f"MCRIT did not confirm sample id {', '.join(str(sample_id) for sample_id in unresolved_ids)} - they may have been deleted, or the backend may be unavailable.", category="warning")
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")

    return render_template(
        "unique_blocks.html",
        samples=samples,
        pagination=pagination,
        selected_ids=selected_list,
        selected_samples=selected_dict,
        pagination_selected=pagination_selected,
        max_selected=MAX_SELECTED_SAMPLES,
        query=query,
    )


@bp.route('/start_unique_blocks')
@visitor_required
@mcrit_server_required
def start_unique_blocks():
    client = get_client()
    selected_list = parse_integer_list_query_param(request, 'samples')
    if not selected_list:
        # a list that was sent but is unparseable is a different problem from no
        # selection at all, and telling someone to select a sample on a page where
        # several are selected is how #94 stayed hidden in cross_compare
        if request.args.get('samples'):
            flash('The samples to isolate unique blocks for were not a list of sample ids.', category='error')
        else:
            flash('Please select at least one sample to isolate unique blocks for.', category='error')
        return redirect(url_for('analyze.unique_blocks'))
    # neither unique blocks method takes force_recalculation, so mcrit answers a repeat
    # out of its descriptor cache with the job it already has - but only for a request
    # that hashes the same. The list is part of that hash, so [2, 1] and [1, 2] would
    # otherwise run the same analysis twice.
    sample_ids = sorted(set(selected_list))
    if len(sample_ids) > MAX_SELECTED_SAMPLES:
        flash(f'A unique blocks request can name at most {MAX_SELECTED_SAMPLES} samples.', category='error')
        return redirect(url_for('analyze.unique_blocks', samples=request.args.get('samples')))
    # every id, not just the ten the selection page happened to render. The page checks
    # the slice it is showing, so an id that scrolled off it, a stale one whose sample
    # was deleted since, or a hand-written query string all reached the backend and
    # queued a job that could only fail. Bounded by MAX_SELECTED_SAMPLES above, and
    # paid once on a deliberate submit rather than on every page view.
    unknown_ids = [sample_id for sample_id in sample_ids if not client.isSampleId(sample_id)]
    if unknown_ids:
        # the selection comes back whole. isSampleId is False for a 500 as well as for a
        # 404, so removing these would rewrite the sample set on a backend hiccup and the
        # retry would silently analyse a different one. Refusing to submit is the part
        # that is certainly right; the selection page is where the set gets edited.
        flash(f"MCRIT did not confirm sample id {', '.join(str(sample_id) for sample_id in unknown_ids)} - they may have been deleted, or the backend may be unavailable. Nothing was submitted.", category='error')
        return redirect(url_for('analyze.unique_blocks', samples=",".join(str(sample_id) for sample_id in sample_ids)))
    job_id = client.requestUniqueBlocksForSamples(sample_ids)
    if job_id is None:
        # the client answers None for anything that was not a 200, and url_for cannot
        # build the job link from that
        flash('MCRIT did not accept the unique blocks request.', category='error')
        return redirect(url_for('analyze.unique_blocks', samples=",".join([str(id) for id in sample_ids])))
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/compare_submit_query')
@visitor_required
def compare_submit_query():
    return render_template("compare_submit_query.html")

@bp.route('/cross_compare_from_hash_list', methods=['GET', 'POST'])
@visitor_required
@mcrit_server_required
def cross_compare_from_hash_list():
    client = get_client()

    selected = ""
    cached_list = []
    selected_list = []
    is_forcing_rematch = True if request.args.get('rematch', 'false').lower() == "true" else False
    is_only_selected = True if request.args.get('onlySelected', 'false').lower() == "true" else False
    if request.method == 'POST':
        hash_list = request.form.get('hashlist', '').strip().splitlines()
        # sanitize to sha256 hashes
        sanitized_hashes = []
        for h in hash_list:
            h = h.strip()
            if re.match(r'^[a-fA-F0-9]{64}$', h):
                sanitized_hashes.append(h)
            else:
                flash(f"Hash '{h}' is not a valid SHA256 hash and was ignored", category="warning")
        if not sanitized_hashes:
            flash("No valid hashes provided", category="error")
            return redirect(url_for('analyze.cross_compare_from_hash_list'))
        # get sample ids from hashes
        selected_samples = []
        for h in sanitized_hashes:
            sample_entry = client.getSampleBySha256(h)
            if sample_entry is not None:
                selected_samples.append(sample_entry)
            else:
                flash(f"Hash '{h}' does not correspond to any sample in the database and was ignored", category="warning")
        if not selected_samples:
            flash("No valid samples found for the provided hashes", category="error")
            return redirect(url_for('analyze.cross_compare_from_hash_list'))
        # redirect to cross_compare with selected samples
        selected = ",".join([str(s.sample_id) for s in selected_samples])

        selected_list = [int(x) for x in selected.split(',') if x != '']


        # fill up search part with all samples
        pagination = CursorPagination(request, default_sort="sample_id")
        results = client.search_samples("", **pagination.getSearchParams(), limit=pagination.limit)
        pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
            
        # unused? -> pagination_selected = Pagination(request, len(selected_list), limit=25, query_param="ps")

        return redirect(url_for(
            "analyze.cross_compare",
            samples = ",".join([str(id) for id in selected_list]),
            cache = ",".join([str(id) for id in cached_list]),
            rematch = "true" if is_forcing_rematch else "false",
            onlySelected = "true" if is_only_selected else "false",
        ))
    else:
        return render_template("cross_compare_from_hash_list.html")

@bp.route('/cross_compare', methods=['GET','POST'])
@visitor_required
@mcrit_server_required
def cross_compare():
    client = get_client()

    selected = request.args.get('samples', '').strip(',')
    cached = request.args.get('cache','').strip(',')
    is_forcing_rematch = True if request.args.get('rematch', 'false').lower() == "true" else False
    is_only_selected = True if request.args.get('onlySelected', 'false').lower() == "true" else False

    cached_list = [int(x) for x in cached.split(',') if x!='']
    selected_list = [int(x) for x in selected.split(',') if x != '']

    query = request.args.get('query', "")
    samples = []
    pagination = CursorPagination(request, default_sort="sample_id")
    results = client.search_samples(query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is not None:
        samples = get_unique_samples_from_search_result(results)
        # a selected sample listed in the search below needs no lookup of its own
        remember_samples(samples)

    pagination_selected = Pagination(request, len(selected_list), limit=10, query_param="ps", limit_param="psl")
    selected_dict = get_sample_entries(sorted(selected_list)[pagination_selected.start_index:pagination_selected.start_index+pagination_selected.limit])
    invalid_ids = []
    for id, sample in selected_dict.items():
        if sample is None:
            invalid_ids.append(id)
    for invalid_id in invalid_ids:
        selected_dict.pop(invalid_id)
        selected_list.remove(invalid_id)
        flash(f"Sample with Id {invalid_id} does not exist and was ignored", category="warning")

    if invalid_ids:
        return redirect(url_for(
            "analyze.cross_compare",
            samples = ",".join([str(id) for id in selected_list]),
            cache = ",".join([str(id) for id in cached_list]),
            rematch = "true" if is_forcing_rematch else "false",
        ))
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")

    # #53: the tint the search table used to hand-roll as two inline style attributes.
    # A sample already in the selection wins over one merely clicked on this page,
    # which is the order those two attributes were written in.
    row_decorations = {sample_id: {"tint": "pending"} for sample_id in cached_list}
    row_decorations.update({sample_id: {"tint": "selected"} for sample_id in selected_list})

    return render_template(
        "cross_compare.html",
        samples=samples,       # all / searched samples
        pagination=pagination, # all / searched samples
        selected_ids=selected_list,
        selected_samples=selected_dict.values(),
        pagination_selected=pagination_selected,
        cached=cached_list,
        rematch=is_forcing_rematch,
        only_selected=is_only_selected,
        query=query,
        row_decorations=row_decorations,
    )


@bp.route('/start_cross_compare')
@visitor_required
@mcrit_server_required
def start_cross_compare():
    client = get_client()
    # both used to be forwarded as the raw query string. "false" is truthy, so an
    # unticked box both forced a recalculation and silently turned on group-only
    # matching, which is a different comparison from the one the user asked for.
    rematch = parse_checkbox_query_param(request, 'rematch')
    only_selected = parse_checkbox_query_param(request, 'onlySelected')
    minhash_band_range = parse_band_range(request)
    # this route used to read `samples` by hand and only bind job_id inside the
    # "something was selected" branch, so a bare /analyze/start_cross_compare fell
    # through to a redirect naming a variable that was never assigned. The helper
    # also rejects a non-numeric list, which used to raise from int(). See #94.
    selected_list = parse_integer_list_query_param(request, 'samples')
    if not selected_list:
        # the two cases were reported identically, and the wrong one of the two: a
        # malformed `samples` told the user to select a sample on a page where several
        # were selected, which is how the leading comma in cross_compare.html's
        # createJob() went unnoticed from the initial commit until v1.4.8
        if request.args.get('samples'):
            flash('The samples to cross compare were not a list of sample ids.', category='error')
        else:
            flash('Please select at least one sample to cross compare.', category='error')
        return redirect(url_for('analyze.cross_compare'))
    job_id = client.requestMatchesCross(selected_list, force_recalculation=rematch, sample_group_only=only_selected, band_matches_required=minhash_band_range)
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))


@bp.route('/compare')
@visitor_required
@mcrit_server_required
def compare():
    client = get_client()

    query = request.args.get('query', "")
    samples = []
    pagination = CursorPagination(request, default_sort="sample_id")
    results = client.search_samples(query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
    else:
        samples = get_unique_samples_from_search_result(results)

    rematch = True if request.args.get('rematch', 'true').lower() == "true" else False
    return render_template(
        "compare.html",
        samples=samples,
        pagination=pagination,
        selected=request.args.get('selected', ""),
        rematch=rematch,
        query=query
    )


@bp.route('/compare_versus')
@visitor_required
@mcrit_server_required
def compare_versus():
    client = get_client()

    parameters = {}
    for a_or_b in "ab":
        query = request.args.get(f'query_{a_or_b}', "")
        samples = {}
        pagination = CursorPagination(request, default_sort="sample_id", query_param_prefix=a_or_b)
        results = client.search_samples(query, **pagination.getSearchParams(), limit=pagination.limit)
        pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
        else:
            samples = get_unique_samples_from_search_result(results)
        selected=request.args.get(f'selected_{a_or_b}', "")

        parameters[f"samples_{a_or_b}"] = samples
        parameters[f"pagination_{a_or_b}"] = pagination 
        parameters[f"selected_{a_or_b}"] = selected 
        parameters[f"query_{a_or_b}"] = query 

    parameters["rematch"] = True if request.args.get('rematch', 'true').lower() == "true" else False
    return render_template("compare_versus.html", **parameters)

@bp.route('/compare/<sample_id_a>')
@visitor_required
@mcrit_server_required
def compare_all(sample_id_a):
    client = get_client()
    # the checkbox in compare.html submits rematch=true *or* rematch=false, and the
    # raw string "false" is truthy - so an unticked box forced a recalculation and
    # queued a fresh job on every visit. See issue #97.
    rematch = parse_checkbox_query_param(request, 'rematch')
    minhash_band_range = parse_band_range(request)
    job_id = client.requestMatchesForSample(sample_id_a, force_recalculation=rematch, band_matches_required=minhash_band_range)
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))

@bp.route('/compare_function/<int:function_id>')
@visitor_required
@mcrit_server_required
def compare_function(function_id):
    """1 vs N for a single function, which the backend only knows how to do per sample.

    So this is the parent sample's match job, read through the function filter that
    `data.result` already implements as `?funid=`. Before issue #35 the Analyze button
    on a function row pointed at the sample picker instead, which lost the function.

    An existing job is reused - `force_recalculation` defaults to False, as on
    `compare_all` since issue #97 - because a table of function rows is a table of
    clicks and each one would otherwise queue a full sample match. `?rematch=true`
    still forces a fresh job for a result that has gone stale.

    The route only accepts a non-negative id: a query sample's functions are numbered
    negatively and have no sample in the database to match against.
    """
    client = get_client()
    function_entry = client.getFunctionById(function_id)
    if function_entry is None:
        flash(f"There is no function with id {function_id}.", category='error')
        return redirect(url_for('explore.functions'))
    rematch = parse_checkbox_query_param(request, 'rematch')
    minhash_band_range = parse_band_range(request)
    job_id = client.requestMatchesForSample(function_entry.sample_id, force_recalculation=rematch, band_matches_required=minhash_band_range)
    if job_id is None:
        # `handle_response` answers None for every non-200 - 400, 404, 410, 500, 501 and
        # the fall-through alike - so a backend that refused the job is indistinguishable
        # here from one that is down. Either way there is no id, and `url_for` is then
        # handed job_id=None: werkzeug drops a None value rather than rendering it, then
        # cannot build the rule without it and raises BuildError. That is a 500 with a
        # traceback where a sentence was owed.
        #
        # Every sibling route in this module needs the same guard and gets it from
        # issue #43's `require_result`. This route is the one that fix could not cover,
        # because it does not exist on that branch - it arrives with this one.
        #
        # The fallback is the listing rather than this function's own page, so a backend
        # that is genuinely down does not answer with a second, wrong message ("There is
        # no function with id N") stacked on top of this one.
        flash('Ups, MCRIT would not start the job.', category='error')
        return redirect(url_for('explore.functions'))
    # forward=1 so a job that is already finished goes straight to the report; while it
    # is still running the job page auto-refreshes and carries funid along until it is
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1, funid=function_id))

@bp.route('/compare/<sample_id_a>/<sample_id_b>')
@visitor_required
@mcrit_server_required
def compare_vs(sample_id_a, sample_id_b):
    client = get_client()
    rematch = parse_checkbox_query_param(request, 'rematch')
    minhash_band_range = parse_band_range(request)
    job_id = client.requestMatchesForSampleVs(sample_id_a, sample_id_b, force_recalculation=rematch, band_matches_required=minhash_band_range)
    return redirect(url_for('data.job_by_id', job_id=job_id, refresh=3))

@bp.route('/query',methods=('GET', 'POST'))
@visitor_required
@mcrit_server_required
def query():
    client = get_client()
    if request.method == 'POST':
        f = request.files.get('file')
        if f is None:
            flash("Please upload a file", category='error')
            return "", 400 # Bad Request

        base_address = None
        form_options = request.form['options']
        is_dump_or_smda = form_options in ['dumped', 'smda']
        if is_dump_or_smda:
            # the form also carries a bitness field, but McritClient has no parameter for it -
            # the server derives bitness from the mapped binary itself
            base_address = int(request.form['base_addr'], 16)

        binary_content = f.read()
        role_limit = current_app.config.get('QUERY_UPLOAD_LIMITS', {}).get(g.user.role)
        if role_limit is not None and len(binary_content) > role_limit:
            flash(f'Your account may only upload files for query that are up to {role_limit} bytes in size.', category='error')
            return "", 403 # Bad Request
        if form_options == "smda":
            content_as_dict = json.loads(binary_content)
            smda_report = SmdaReport.fromDict(content_as_dict)

        minhash_band_range = parse_band_range(request)
        if form_options == "smda":
            job_id = client.requestMatchesForSmdaReport(smda_report, force_recalculation=True, band_matches_required=minhash_band_range)
        elif form_options == "dumped":
            job_id = client.requestMatchesForMappedBinary(binary=binary_content, disassemble_locally=False, base_address=base_address, force_recalculation=True, band_matches_required=minhash_band_range)
        else:
            job_id = client.requestMatchesForUnmappedBinary(binary=binary_content, disassemble_locally=False, force_recalculation=True, band_matches_required=minhash_band_range)
        
        if job_id is not None:
            # a query is never stored, and no query endpoint takes a filename - so this is
            # the only record of what the uploaded file was called (#40)
            remember_query_filename(job_id, f.filename)
            # persist the upload, so the query can later be promoted to a sample (#9).
            # It is filed under the job id and not under any hash: the id is issued by
            # the backend once the job is queued, so no part of the name comes from the
            # request. Naming an .smda upload by the `sha256` its own report declares
            # let any visitor overwrite another user's stored query by declaring that
            # user's digest, and naming it by a digest of the uploaded bytes fixes that
            # but leaves the promote path with no way to find the file - a query report
            # records the sample's declared hash, not a hash of what was posted.
            # The cost is that identical uploads no longer share one file, since each
            # query is its own job while force_recalculation is set.
            # Keeping it is best-effort, and deliberately so now that it happens after
            # the job was queued: a full disk or a wrong permission here would
            # otherwise raise past this route - `mcritweb` registers no errorhandler -
            # and answer 500 for a job the backend is already running, so the submitter
            # would never be given its URL. What a failure costs is the ability to
            # promote this one query later, which the promote page reports plainly.
            upload_path = query_upload_path(current_app, job_id)
            try:
                if upload_path is None:
                    raise ValueError(f"not a job id: {job_id!r}")
                with open(upload_path, "wb") as fout:
                    fout.write(binary_content)
            except (OSError, ValueError) as storage_error:
                current_app.logger.warning("analyze.query - could not store the upload of job %r: %s", job_id, storage_error)
            flash('Sample submitted!', category='success')
            return url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1), 202 # Accepted
        else:
            flash('Sample could not be parsed / disassembled!', category='error')
            return "", 400 # Bad Request
    return render_template('query.html', families=[], show_submit_fields=False)
