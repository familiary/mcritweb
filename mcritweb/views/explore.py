import re
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from mcrit.queue.JobCollection import JobCollection
from mcrit.storage.SampleEntry import SampleEntry

import mcritweb.views.cfg_explorer_detector as cfg_explorer_detector
from mcritweb.autocomplete import autocomplete_items
from mcritweb.views.authentication import contributor_required, visitor_required
from mcritweb.views.client import get_client
from mcritweb.views.cursor_pagination import CursorPagination
from mcritweb.views.functiondiff import get_combined_dot_graph
from mcritweb.views.pagination import request_args_for_link_building
from mcritweb.views.search import search_page
from mcritweb.views.utility import get_user_column_setup, mcrit_server_required

bp = Blueprint('explore', __name__, url_prefix='/explore')

#: what `?type=` may name on the search page. The page defaults to all three when the
#: parameter is absent, and anything else in it is a hand-written URL rather than
#: something the form can produce.
SEARCHABLE_TYPES = ("family", "sample", "function")


# Row decorations for the two exact hits mcrit reports alongside a search (#53, #56). See
# templates/table/row_decoration.html for the shape; the variant names a Bootstrap
# badge colour, which is why it is a name and not a class.
ID_MATCH_DECORATION = {"badges": [{"text": "ID match", "variant": "success"}]}
SHA_MATCH_DECORATION = {"badges": [{"text": "SHA256 match", "variant": "success"}]}


def exact_match_decorations(results, id_field):
    """The row decorations for the records the backend answered as an exact hit on an
    identifier, keyed by the id the table macro renders.

    `id_match` and `sha_match` arrive beside the page's entries, and once they are folded
    into the same table a row that is there because the query *was* its id reads as an
    ordinary text hit. The row macros badge the ones named here, through the row
    decoration convention of issue #53. See issue #56.
    """
    decorations = {}
    # `is None`, not `not results`: a SearchPage is as long as its text hits, so the
    # page that found nothing but the exact id is falsy - and that is the page the
    # badge is for
    if results is None:
        return decorations
    # sha first, so the id wins if one record somehow came back as both - which is also
    # the order the samples view folds them in
    for key, decoration in (("sha_match", SHA_MATCH_DECORATION), ("id_match", ID_MATCH_DECORATION)):
        match = getattr(results, key)
        if match is not None:
            decorations[getattr(match, id_field)] = decoration
    return decorations


def exact_matches_to_prepend(results, pagination):
    """The exact identifier hits that belong at the top of *this* page: the first
    page's, and none at all on any page after it.

    mcrit derives `id_match` (and `sha_match`) from the search term alone, before the
    cursor is applied, and attaches them to every page it answers - in
    `MinHashIndex.getFamilySearchResults` and its two siblings the lookup runs at the
    top of the method and the assignment happens after `_getSearchResultTemplate` has
    already windowed the text hits, so the value is cursor-independent. Prepending it
    unconditionally therefore repeated one row, exact-match badge and all, on page 2,
    3, 4 ... of a listing.

    First page only, rather than de-duplicating: a view renders a single page and has
    no memory of the ones before it, so there is nothing here to de-duplicate against
    - the check would have to live in the browser or in a session. And the placement
    only means anything on the first page anyway: "the record you named, at the top"
    is a statement about the top of the listing, not about the top of whichever page
    the reader happened to walk to.

    The prepended row is over and above `limit`, so a first page can carry limit+1
    rows. Dropping a text hit to make room would hide that hit for good: mcrit builds
    the forward cursor from the last entry it returned (`_getSearchResultTemplate`),
    so the next page resumes *after* the row we dropped rather than at it. Issue #56
    is about a listing hiding a record that exists; trading one hidden record for
    another would be no fix. The exact hit is an answer to the query rather than a
    member of the paged result set, so it sits outside the page budget.

    All four places that list search results use this: the three listings and the
    three tables of /explore/search.
    """
    if results is None or not pagination.is_first_page:
        return []
    # sha first, so the id wins if one record came back as both - the order the
    # samples view has always folded them in, and the order exact_match_marks uses
    return [match for match in (results.sha_match, results.id_match) if match is not None]

#: A search term that is exactly a SHA-256. Its own case, because a person pasting one
#: is asking "is this sample in the collection?" and deserves that answer. See #79.
SHA256_PATTERN = re.compile(r"[a-fA-F0-9]{64}")


def flash_search_failed(query, collection):
    """Report a search the backend did not answer.

    `search_*` returns None only when the call itself failed - a search that matched
    nothing is a well-formed result with no rows, and the pages say so themselves
    (issue #54). So this is an error about the backend, and wording it as "no results"
    would send the reader looking for the wrong thing. Issue #79.
    """
    # the family page composes its own query ("family_id:3 <what the user typed>") and
    # passes only the typed half, so no message quotes MCRIT's query syntax back at
    # someone who never wrote it. That leaves the empty case, which needs no quoting.
    subject = f" for '{query}'" if query else ""
    flash(
        f"Could not search MCRIT's {collection}{subject} - the backend did not answer. "
        "It may be unreachable, or unable to handle this search term; check the server "
        "settings and the backend's log.",
        category="error",
    )


def sha256_second_opinion(sha256):
    """Ask the backend directly about one hash. Returns a SampleEntry, "absent", or None.

    `getSampleBySha256` in its ordinary mode cannot answer this question. Its return
    passes through `handle_response`, which maps 400/404/410 and 500/501 to None alike
    and falls through to None for every status it does not enumerate - 401, 403, 502,
    503. So a plain None means "not in the collection, or the call failed, and I cannot
    tell you which". Reporting that as absence would state as fact something we do not
    know, and would replace a backend-failure notice with a reassuring one - the exact
    error issue #79 is about, pointed the other way.

    Raw mode returns the response instead, so the status can be read. Only 404 is
    absence; anything else is a second failure and gets the generic message.
    """
    response = get_client(raw_responses=True).getSampleBySha256(sha256)
    if response.status_code == 404:
        return "absent"
    if response.status_code in (200, 202):
        payload = response.json()
        if payload.get("status") == "successful" and payload.get("data") is not None:
            return SampleEntry.fromDict(payload["data"])
    return None


def flash_sample_search_failed(client, query):
    """As above, but a SHA-256 can get a real answer rather than an apology.

    That is what issue #79 asks for: someone pastes a hash to find out whether the
    sample is known, and "search failed" tells them nothing. `getSampleBySha256` is a
    different endpoint from the search, so it can still answer when the search could
    not. One extra round-trip, only on a path that has already failed - and only when
    it can answer definitively, see sha256_second_opinion.
    """
    if SHA256_PATTERN.fullmatch(query or ""):
        try:
            # samples store their hash lowercase (it is an SMDA hexdigest) and the
            # backend's lookup is an exact match - `find_one({"sha256": ...})` - so an
            # uppercase paste would come back 404 and be reported as absence. The
            # message still quotes what the reader typed.
            answer = sha256_second_opinion(query.lower())
        except Exception:
            # the lookup is a best-effort second opinion; if it fails too, the generic
            # message below is still true
            current_app.logger.exception("SHA-256 lookup failed after a failed sample search")
        else:
            if answer == "absent":
                flash(f"No sample with SHA-256 {query} is in the collection.", category="info")
                return
            if answer is not None:
                flash(
                    f"Could not search MCRIT's samples for '{query}' - the backend did not "
                    f"answer. A sample with that SHA-256 does exist, as sample {answer.sample_id}.",
                    category="error",
                )
                return
            # answer is None: the direct lookup failed too, so nothing is known about
            # this hash. Fall through to the generic message rather than inventing one.
    flash_search_failed(query, "samples")


#: The queue methods a sample listing can show. `table/sample_row.html` asks the
#: collection for `matching_only=True` and for `method="getMatchesForSample"`, and
#: `Job.has_sample_id` answers only for the methods that carry a sample id as an
#: argument - so of the matching methods even `combineMatchesToCross` never reaches a
#: row. Everything else in the queue used to be fetched, deserialised into a `Job` and
#: dropped again on every listing view. mcrit's `/jobs` takes one method per request
#: and applies it as a mongo query *before* start/limit, so naming them costs two
#: requests and leaves submissions, minhashing and block jobs on the server. See #77.
SAMPLE_ROW_JOB_METHODS = ("getMatchesForSample", "getMatchesForSampleVs")

#: How many names `family_names` offers a type-ahead. The widget shows five of them;
#: the slack is there so a prefix with near-duplicates still reaches five.
FAMILY_NAME_SUGGESTIONS = 10

#: What `/explore/search` searches when the caller named nothing. See `search()`.
DEFAULT_SEARCH_TYPES = ["family", "sample"]


def sample_row_job_collection(client, samples):
    """The jobs a sample listing annotates its rows with.

    `filterToSampleIds` is what makes the result exact: it keeps only jobs whose own
    `sample_id` - their first argument - is on the page, which is the same set the row
    macro would have found in a collection built from the whole queue. An empty page
    has nothing to annotate, so it asks the backend for nothing.

    A failed queue read used to take the whole page down (`JobCollection(None)`), so
    say what was lost and render the rows without their annotations instead.

    All or nothing across the two requests. Keeping the half that answered would
    render badges that count some of a sample's matching jobs and not others, and the
    "Last 1:N Job" link comes from only one of the two methods - so a partial result
    is not a smaller true answer, it is a wrong one, shown with no indication that it
    is wrong. An empty collection at least matches what the message says.

    Concatenating the per-method answers would leave the result newest-first only
    *within* a method, so the merge is sorted back into one order. `Job.number` is the
    queue's own submission counter - `MongoQueue.put` takes it from a mongo counter,
    `LocalQueue.put` from an instance one - so descending `number` is the newest-first
    order an unqualified `getQueueData()` answered in. A job old enough to predate the
    counter carries no number, which `Job.number` reports as -1; those sort last, as
    the oldest, and python's stable sort leaves them in the order the backend listed
    them.
    """
    if not samples:
        return JobCollection([])
    jobs = []
    for method in SAMPLE_ROW_JOB_METHODS:
        jobs_for_method = client.getQueueData(method=method)
        if jobs_for_method is None:
            flash("Ups, reading MCRIT's job queue failed - rows are shown without their job annotations.", category="error")
            return JobCollection([])
        jobs.extend(jobs_for_method)
    jobs.sort(key=lambda job: job.number if isinstance(job.number, int) else -1, reverse=True)
    job_collection = JobCollection(jobs)
    job_collection.filterToSampleIds([sample.sample_id for sample in samples])
    return job_collection


##############################################################
### Unfiltered Collections: Families, Samples, Function
##############################################################

@bp.route('/modifyFamily', methods=['POST'])
@contributor_required
@mcrit_server_required
def modifyFamily():
    if request.method=='POST':
        data = request.data
        data = data.decode("utf-8")
        if not request.form.to_dict(flat=False):
            # returning None from a view is a TypeError inside Flask, so an empty
            # form submission answered with a 500 rather than saying what was wrong
            flash("Nothing to change - the form arrived empty.", category="error")
            return redirect(url_for('explore.families'))
        client = get_client()
        family_id = request.form.get("family_id", None)
        if family_id is None: 
            flash("No valid family_id received.", category="error")
            return redirect(url_for('explore.families'))
        family_entry = None
        try:
            family_id = int(family_id)
            family_entry = client.getFamily(family_id)
            if family_entry is None:
                raise ValueError
        except Exception:
            flash("No valid family_id received.", category="error")
            return redirect(url_for('explore.families'))
        # check if we want ot keep samples
        is_family_keeping_samples = True if request.form.get("family_keeping_samples", None) is not None else False
        is_family_delete = True if request.form.get("family_delete", None) is not None else False
        # delete family
        if is_family_delete:
            job_id = client.deleteFamily(family_id, keep_samples=is_family_keeping_samples)
            flash("Job to delete family was scheduled.", category="info")
            return redirect(url_for('data.job_by_id', job_id=job_id, refresh=5))
        # check if sample_entry should be modified
        new_family_name = request.form.get("family_new_name", None)
        new_is_library = True if request.form.get("family_is_library", None) is not None else False
        if new_family_name is None or new_family_name == family_entry.family:
            new_family_name = None
        if new_is_library is None or new_is_library == family_entry.is_library:
            new_is_library = None
        if any([item is not None for item in [new_family_name, new_is_library]]):
            job_id = client.modifyFamily(family_id, family_name=new_family_name, is_library=new_is_library)
            time.sleep(0.3)
        flash("Job to modify family was scheduled.", category="info")
    return redirect(url_for('explore.families'))

@bp.route('/familyNames')
@visitor_required
@mcrit_server_required
def family_names():
    """Names for the family type-ahead in the edit modals, as JSON.

    Every page carrying one of those modals used to embed the complete list of family
    names in its source. mcrit answers `getFamilies()` with one storage lookup per
    family (`MinHashIndex.getFamilies`), so a listing paid for the whole family table
    on every view whether or not anyone opened a modal, and the cost grew with the
    corpus. A prefix needs a handful of names, and `search_families` is the bounded way
    to ask for them. Visitors already read every family name off `/explore/families`,
    so this exposes nothing new. See issue #77.

    A backend that cannot answer costs the suggestions, not the modal - the field is a
    free-text input and stays usable without them.

    Answers `{label, value}` pairs rather than bare names, escaped by
    `mcritweb.autocomplete.autocomplete_items` - the same function behind the
    `|autocomplete_items` filter that the shipped-with-the-page type-aheads use. The
    consumer is the vendored autocomplete.js either way, and it renders every
    suggestion through innerHTML, so escaping here is what keeps a family name from
    executing (#168). `jsonify` protects the transport, not the sink.
    """
    query = request.args.get('q', "")
    client = get_client()
    results = search_page(client, "families", query, limit=FAMILY_NAME_SUGGESTIONS)
    if results is None:
        return {"suggestions": []}
    names = [entry.family_name for entry in results.entries]
    return {"suggestions": autocomplete_items(names)}


@bp.route('/families')
@visitor_required
@mcrit_server_required
def families():
    family_id = request.args.get('family_id')
    if family_id is not None:
        return redirect(url_for('explore.family_by_id', family_id=family_id, p=request.args.get('p')))
    query = request.args.get('query', "")
    client = get_client()
    families = []
    row_decorations = {}
    pagination = CursorPagination(request, default_sort="family_id", limit=25, sort_memory="family")
    results = search_page(client, "families", query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash_search_failed(query, "families")
    else:
        # the exact-id hit arrives beside the text results, not among them, and this
        # page used to read only the latter - so searching a family page for an id
        # rendered "No families available" for a family that exists, while the search
        # page found it. It is answered with every page, so it is prepended to the
        # first one only - see exact_matches_to_prepend. Issue #56.
        by_id = {}
        for entry in exact_matches_to_prepend(results, pagination):
            by_id[entry.family_id] = entry
        for entry in results.entries:
            # the id match can also come back as a text hit; keep its position, not a copy
            by_id.setdefault(entry.family_id, entry)
        families = list(by_id.values())
        row_decorations = exact_match_decorations(results, 'family_id')
    user_column_setup = get_user_column_setup("family_table")
    return render_template("families.html", families=families, pagination=pagination, query=query, user_column_setup=user_column_setup, row_decorations=row_decorations)

@bp.route('/modifySample', methods=['POST'])
@contributor_required
@mcrit_server_required
def modifySample():
    if request.method=='POST':
        data = request.data
        data = data.decode("utf-8")
        if not request.form.to_dict(flat=False):
            flash("Nothing to change - the form arrived empty.", category="error")
            return redirect(url_for('explore.samples'))
        client = get_client()
        sample_id = request.form.get("sample_id", None)
        redirection_job_id = request.form.get("redirection_job_id", None)
        if redirection_job_id is not None and client.getJobData(redirection_job_id) is None: 
            flash("Trying to redirect from invalid job_Id.", category="error")
            return redirect(url_for('explore.samples'))
        sample_entry = None
        if sample_id is None: 
            flash("No valid sample_id received.", category="error")
            return redirect(url_for('explore.samples'))
        sample_entry = None
        try:
            sample_id = int(sample_id)
            sample_entry = client.getSampleById(sample_id)
            if sample_entry is None:
                raise ValueError
        except Exception:
            flash("No valid sample_id received.", category="error")
            return redirect(url_for('explore.samples'))
        is_sample_delete = True if request.form.get("sample_delete", None) is not None else False
        # delete sample
        if is_sample_delete:
            job_id = client.deleteSample(sample_id)
            flash("Job to delete sample was scheduled.", category="info")
            return redirect(url_for('data.job_by_id', job_id=job_id, refresh=5))
        # check if sample_entry should be modified
        new_family_name = request.form.get("sample_family_name", None)
        new_version = request.form.get("sample_version", None)
        new_is_library = True if request.form.get("sample_is_library", None) is not None else False
        if new_family_name is None or new_family_name == sample_entry.family:
            new_family_name = None
        if new_version is None or new_version == sample_entry.version:
            new_version = None
        if new_is_library is None or new_is_library == sample_entry.is_library:
            new_is_library = None
        if any([item is not None for item in [new_family_name, new_version, new_is_library]]):
            client.modifySample(sample_id, family_name=new_family_name, version=new_version, is_library=new_is_library)
            time.sleep(0.3)
        if redirection_job_id:
            time.sleep(1)
            flash("Delayed redirect for 1 second to let requested sample modification propagate", category="info")
            return redirect(url_for('data.result', job_id=redirection_job_id))
        flash("Job to modify sample was scheduled.", category="info")
    return redirect(url_for('explore.samples'))


@bp.route('/samples')
@visitor_required
@mcrit_server_required
def samples():
    sample_id = request.args.get('sample_id')
    if sample_id is not None:
        return redirect(url_for('explore.sample_by_id', sample_id=sample_id, p=request.args.get('p')))

    query = request.args.get('query', "")
    client = get_client()
    samples = []
    row_decorations = {}
    pagination = CursorPagination(request, default_sort="sample_id", limit=25, sort_memory="sample")
    results = search_page(client, "samples", query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash_sample_search_failed(client, query)
    else:
        # as in families above: the exact id, and for samples the exact sha256, arrive
        # beside the text results rather than among them, and belong to the first page
        # only. See issue #56.
        by_id = {}
        for entry in exact_matches_to_prepend(results, pagination):
            by_id[entry.sample_id] = entry
        for entry in results.entries:
            by_id.setdefault(entry.sample_id, entry)
        samples = list(by_id.values())
        row_decorations = exact_match_decorations(results, 'sample_id')

    job_collection = sample_row_job_collection(client, samples)

    user_column_setup = get_user_column_setup("samples_table")
    return render_template("samples.html", samples=samples, job_collection=job_collection, pagination=pagination, query=query, user_column_setup=user_column_setup, row_decorations=row_decorations)


@bp.route('/functions')
@visitor_required
@mcrit_server_required
def functions():
    function_id = request.args.get('function_id')
    if function_id is not None:
        return redirect(url_for('explore.function_by_id', function_id=function_id, p=request.args.get('p')))
    query = request.args.get('query', "")
    client = get_client()
    functions = []
    row_decorations = {}
    pagination = CursorPagination(request, default_sort="function_id", limit=25, sort_memory="function")
    results = search_page(client, "functions", query, **pagination.getSearchParams(), limit=pagination.limit)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash_search_failed(query, "functions")
    else:
        # as in families and samples above: keyed by id, so an exact hit that is also
        # a text hit is one row (issue #56) - and entry objects, as explore.search
        # hands the same macro (issue #64). This page used to hand function_table raw
        # dicts off the wire: invisible only because Jinja falls back from attribute to
        # item lookup and the keys happen to equal the attribute names. A renamed key,
        # or any derived property, would break this page while leaving the search page
        # working. `search_page` deserialises every value, so neither page can.
        by_id = {}
        for entry in exact_matches_to_prepend(results, pagination):
            by_id[entry.function_id] = entry
        for entry in results.entries:
            by_id.setdefault(entry.function_id, entry)
        functions = list(by_id.values())
        row_decorations = exact_match_decorations(results, 'function_id')
    user_column_setup = get_user_column_setup("functions_table")
    return render_template("functions.html", functions=functions, pagination=pagination, query=query, user_column_setup=user_column_setup, row_decorations=row_decorations)

##############################################################
### Single Entries: Families, Samples, Function
##############################################################

@bp.route('/families/<int:family_id>')
@visitor_required
@mcrit_server_required
def family_by_id(family_id):
    client = get_client()
    family_info = client.getFamily(family_id, with_samples=False)
    if family_info:
        original_query = request.args.get('query', "")
        query = f"family_id:{family_id} {original_query}"
        client = get_client()
        samples = []
        pagination = CursorPagination(request, default_sort="sample_id", limit=25, sort_memory="sample")
        results = search_page(client, "samples", query, **pagination.getSearchParams(), limit=pagination.limit)
        pagination.read_cursor_from_result(results)
        if results is None:
            # original_query, not query: the latter carries the family_id: prefix this
            # view added, and quoting it back reads as though the user typed it
            flash_sample_search_failed(client, original_query)
        else:
            samples = results.entries
        job_collection = sample_row_job_collection(client, samples)
        user_column_setup = get_user_column_setup("samples_table")
        return render_template("single_family.html", family=family_info, samples=samples, job_collection=job_collection, pagination=pagination, query=original_query, user_column_setup=user_column_setup)
    else:
        flash("The given Family ID doesn't exist", category='error')
        return redirect(url_for('explore.families'))


@bp.route('/samples/<int(signed=True):sample_id>')
@visitor_required
@mcrit_server_required
def sample_by_id(sample_id):
    client = get_client()
    sample_entry = client.getSampleById(sample_id)
    if sample_entry:
        if sample_id < 0:
            return render_template("single_query_sample.html", entry=sample_entry)
        original_query = request.args.get('query', "")
        query = f"sample_id:{sample_id} {original_query}"
        functions = []
        # bound up front: the job table below is read outside the branch that fills it,
        # and a failed function search left the name undefined - a 500 on the page that
        # was meant to report the failure. A failed queue read is the same story one
        # level down, since `JobCollection(None)` is only a TypeError waiting to happen.
        job_collection = JobCollection([])
        pagination = CursorPagination(request, default_sort="function_id", limit=100, sort_memory="function")
        results = search_page(client, "functions", query, **pagination.getSearchParams(), limit=pagination.limit)
        pagination.read_cursor_from_result(results)
        if results is None:
            flash_search_failed(query, "functions")
        else:
            # `McritClient.getQueueData` only forwards a `filter` that is a str, so
            # this used to be dropped on the floor and the whole queue came back. The
            # server matches it as a substring of the job's `method(args...)` string
            # *after* paging (`QueueRemoteCalls.getQueueData`), which is unusable with
            # a `limit` - but there is none here, and every job whose own `sample_id`
            # is this one renders that id into its parameters, so the narrowed set is
            # a superset of what `filterToSampleIds` keeps below. See #77.
            jobs = client.getQueueData(filter=str(sample_id))
            if jobs is None:
                flash("Ups, reading MCRIT's job queue failed - this sample's jobs are not shown.", category="error")
            else:
                job_collection = JobCollection(jobs)
                job_collection.filterToSampleIds([sample_id])
            functions = results.entries
        samples_by_id = {}
        for job in job_collection.getJobs():
            if job.sample_ids is not None:
                for sample_id in [sid for sid in job.sample_ids if sid not in samples_by_id]:
                    samples_by_id[sample_id] = client.getSampleById(sample_id)
        user_column_setup = get_user_column_setup("functions_table")
        return render_template("single_sample.html", entry=sample_entry, functions=functions, pagination=pagination, query=original_query, samples=samples_by_id, job_collection=job_collection, user_column_setup=user_column_setup)
    else:
        flash("The given Sample ID doesn't exist", category='error')
        return redirect(url_for('explore.samples'))


@bp.route('/functions/<int(signed=True):function_id>')
@visitor_required
@mcrit_server_required
def function_by_id(function_id):
    client = get_client()
    function_entry = client.getFunctionById(function_id)
    if function_entry:
        sample_entry = client.getSampleById(function_entry.sample_id)
        pichash_match_summary = client.getMatchesForPicHash(function_entry.pichash, summary=True)
        return render_template("single_function.html", entry=function_entry, sample_entry=sample_entry, pichash_match_summary=pichash_match_summary)
    else:
        flash("The given Function ID doesn't exist", category="error")
        return redirect(url_for('explore.functions'))

# Served in place of a control flow graph when the backend holds no xcfg for a function
# (see #67). It is a valid dot graph, so the existing front end renders it as a single
# block and the user is told why the view is empty instead of being shown a blank panel.
# The text is fixed and interpolates nothing from the analysed binary; `comment` is left
# empty so the front end does not look up a picblockhash for it.
NO_XCFG_DOT_GRAPH = (
    'digraph "No CFG" {\n'
    '  label="No control flow graph stored";\n'
    '  NodeNoXcfg [shape=record,comment="",label="No disassembly stored for this function.'
    '\\lThe backend discards the control flow graph when it is configured not to keep '
    'disassembly, and there is nothing left to draw.\\l"];\n'
    '}\n'
)


# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/fetchDotGraph/<int(signed=True):function_id>', methods=['GET'])
@visitor_required
@mcrit_server_required
def fetchDotGraph(function_id):
    client = get_client()
    function_entry = client.getFunctionById(function_id, with_xcfg=True)
    # An entry can reach us without its control flow graph: mcrit deletes the xcfg after
    # minhashing when STORAGE_DROP_DISASSEMBLY is set, and an export copies that empty
    # graph on to whoever imports it (see docs/adr/0003 and the NotImplemented
    # getFunctionGraph in mcrit's MinHashIndex). toSmdaFunction() raises on that, which
    # took the whole request down with a 500 and left the CFG panel blank without ever
    # saying why. picblockhashes can come back empty or null for the same reason.
    if function_entry and function_entry.xcfg:
        smda_function = function_entry.toSmdaFunction()
        dot_graph = smda_function.toDotGraph(with_api=True)
        # TODO can possibly do this fixup in a better place
        pbh_by_offset = {pbh["offset"]: pbh for pbh in function_entry.picblockhashes or []}
        for smda_block in smda_function.getBlocks():
            needle = f',label="{smda_block.offset:x}'
            replacement = f',comment=""{needle}'
            if smda_block.offset in pbh_by_offset:
                replacement = f',comment="0x{pbh_by_offset[smda_block.offset]["hash"]:x}"{needle}'
            dot_graph = dot_graph.replace(needle, replacement)
        return dot_graph
    if function_entry:
        # the entry exists but carries no graph - say so, rather than rendering nothing
        return NO_XCFG_DOT_GRAPH
    return ""

# helper for @bp.route('/matches/function/<function_id_a>/<function_id_b>') in data.py:
# the bindiff-style "combined" view of issue #74, both functions merged into one graph
@bp.route('/fetchCombinedDotGraph/<int(signed=True):function_id_a>/<int(signed=True):function_id_b>', methods=['GET'])
@visitor_required
@mcrit_server_required
def fetchCombinedDotGraph(function_id_a, function_id_b):
    client = get_client()
    if client.isFunctionId(function_id_a) and client.isFunctionId(function_id_b):
        # a function without disassembly yields an empty graph
        return get_combined_dot_graph(function_id_a, function_id_b)
    return ""

# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/findLoops/', methods=['GET', 'POST'])
@visitor_required
@mcrit_server_required
def findLoops():
    out_str = ""
    if request.method=='POST':
        data = request.data
        data = data.decode("utf-8")
        out_str = cfg_explorer_detector.run(data)
    return out_str


# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/getPicBlockMatches/<picblockhash>', methods=['GET'])
@visitor_required
@mcrit_server_required
def getPicBlockMatches(picblockhash):
    client = get_client()
    return client.getMatchesForPicBlockHash(int(picblockhash, 16), summary=True)

##############################################################
### Statistics + Search
##############################################################

@bp.route('/statistics')
@visitor_required
@mcrit_server_required
def statistics():
    client = get_client()
    stats = client.getStatus()
    return render_template("statistics.html", stats=stats)


@bp.route('/search')
@visitor_required
@mcrit_server_required
def search():
    query = request.args.get('query', None)
    types = request.args.getlist("type")
    if len(types) > 1:
        # the same collision the pagination classes have, one step worse: this URL
        # goes out in a Location header, so an unfiltered `?_external=1` redirected
        # the visitor to whatever the Host header said.
        args = request_args_for_link_building(request)
        args["type"] = ",".join(types)
        return redirect(url_for("explore.search", **args))
    if "type" not in request.args:
        # Functions are deliberately not in the default. A function search scans the
        # whole function collection and takes ~30 seconds on a large instance when it
        # finds nothing (issue #76), and running it unasked charged that to every
        # search from the navbar and to every pagination click on the other two
        # tables. It is one tick away, and search.html says so where the function
        # results would have been - this narrows who pays for the scan, it does not
        # make the scan faster. That part is mcrit's.
        types = list(DEFAULT_SEARCH_TYPES)
    else:
        types = request.args["type"].split(",")
    if not query:
        return render_template("search.html", search_types=types)
    client = get_client()

    # a backend that answered None is a failed search, not an empty one, and the two
    # have to look different on the page - see issue #54
    # per category, not one flag for all three: they are independent searches, and a
    # single flag suppressed the "nothing matched" message for the categories that had
    # answered perfectly well just because a different one failed
    search_failed = set()

    #TODO: show id/sha matches in extra place
    families = []
    family_decorations = {}
    family_pagination = None
    if 'family' in types:
        family_pagination = CursorPagination(request, query_param_prefix="family", default_sort="family_id", limit=25, sort_memory="family")
        results = search_page(client, "families", query, **family_pagination.getSearchParams(), limit=family_pagination.limit)
        family_pagination.read_cursor_from_result(results)
        if results is None:
            search_failed.add("family")
            flash_search_failed(query, "families")
        else:
            # the exact hit belongs to the first page and is folded in by id, as on
            # the listings - see exact_matches_to_prepend. Keying by id is what stops
            # a record that is both the exact hit and a text hit rendering twice in
            # the same table, which this branch used to do. Issue #56.
            by_id = {}
            for family in exact_matches_to_prepend(results, family_pagination):
                by_id[family.family_id] = family
            for family in results.entries:
                by_id.setdefault(family.family_id, family)
            families = list(by_id.values())
            family_decorations = exact_match_decorations(results, 'family_id')

    samples = {}
    sample_decorations = {}
    sample_pagination = None
    if 'sample' in types:
        sample_pagination = CursorPagination(request, query_param_prefix="sample", default_sort="sample_id", limit=25, sort_memory="sample")
        results = search_page(client, "samples", query, **sample_pagination.getSearchParams(), limit=sample_pagination.limit)
        sample_pagination.read_cursor_from_result(results)
        if results is None:
            search_failed.add("sample")
            flash_sample_search_failed(client, query)
        else:
            # sha256 first, then id, and only on the first page - see
            # exact_matches_to_prepend. Issue #56.
            for sample_entry in exact_matches_to_prepend(results, sample_pagination):
                samples[sample_entry.sample_id] = sample_entry
            for sample_entry in results.entries:
                samples[sample_entry.sample_id] = sample_entry
            sample_decorations = exact_match_decorations(results, 'sample_id')
    # deduplicate in case we have cases such as filename == sha256
    samples = list(samples.values())

    functions = []
    function_decorations = {}
    function_pagination = None
    if 'function' in types:
        function_pagination = CursorPagination(request, query_param_prefix="function", default_sort="function_id", limit=25, sort_memory="function")
        results = search_page(client, "functions", query, **function_pagination.getSearchParams(), limit=function_pagination.limit)
        function_pagination.read_cursor_from_result(results)
        if results is None:
            search_failed.add("function")
            flash_search_failed(query, "functions")
        else:
            # as in the families branch above: first page only, and keyed by id so
            # an exact hit that is also a text hit is one row. Issue #56.
            by_id = {}
            for function_entry in exact_matches_to_prepend(results, function_pagination):
                by_id[function_entry.function_id] = function_entry
            for function_entry in results.entries:
                by_id.setdefault(function_entry.function_id, function_entry)
            functions = list(by_id.values())
            function_decorations = exact_match_decorations(results, 'function_id')

    family_column_setup = get_user_column_setup("family_table")
    sample_column_setup = get_user_column_setup("samples_table")
    function_column_setup = get_user_column_setup("functions_table")
    return render_template(
        "search.html",
        families=families,
        samples=samples,
        functions=functions,
        family_pagination=family_pagination,
        sample_pagination=sample_pagination,
        function_pagination=function_pagination,
        query=query,
        search_types=types,
        search_failed=search_failed,
        # the categories that were asked and did answer; "nothing matched" is only a
        # true statement about those
        # filtered to categories that exist: `?type=` (empty) splits to [""], which is
        # truthy, and would let the page say nothing matched when nothing was searched
        answered_types=[t for t in types if t in SEARCHABLE_TYPES and t not in search_failed],
        family_column_setup=family_column_setup,
        sample_column_setup=sample_column_setup,
        function_column_setup=function_column_setup,
        family_decorations=family_decorations,
        sample_decorations=sample_decorations,
        function_decorations=function_decorations
    )
