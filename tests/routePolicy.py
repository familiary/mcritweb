"""The access policy of every route, written down so it can be asserted.

This module is data, not tests - `testRoutePolicy.py` reads it. It is deliberately
not named `test*.py` so pytest does not collect it.

**What is recorded here is what the app does today**, derived by exercising every
route in `app.url_map` as each role and observing whether the request was admitted
or rejected. It is a baseline to review and argue with, not a statement that the
current behaviour is correct. Where the observed behaviour looks wrong, the row
carries a comment rather than a corrected value - change the value only together
with the code, so the test stays a description of reality.

Two facts per route:

`role`    the least privileged caller that gets past the gate. Authorization is
          settled before the view body runs, so this is observable without any
          backend or fixture data.

`writes`  whether the view changes state - the local SQLite database or the MCRIT
          backend - and, crucially, whether a plain GET is enough to do it. See
          issue #84. CSRF tokens now cover every unsafe method (issue #83), but a
          token cannot cover a route that writes on GET: a browser sends no token
          on a plain navigation, so every WRITES_ON_GET row is still reachable
          from any page a logged-in user visits, e.g. through an <img> tag.

Adding a route means adding a row here; `testRoutePolicy.py` fails on any endpoint
in the url_map that is missing, and on any row naming an endpoint that no longer
exists.
"""

# --- the least privileged caller a route admits ----------------------------------

PUBLIC = "public"                # no session needed at all
LOGGED_IN = "logged-in"          # any authenticated user, including role 'pending'
VISITOR = "visitor"              # visitor, contributor or admin
CONTRIBUTOR = "contributor"      # contributor or admin
ADMIN = "admin"                  # admin only
APITOKEN = "apitoken"            # not session-based: an `apitoken` request header

ROLE_ORDER = [PUBLIC, LOGGED_IN, VISITOR, CONTRIBUTOR, ADMIN]

# --- what a route changes --------------------------------------------------------

READ_ONLY = "read-only"
WRITES_ON_POST = "writes-on-post"   # the write is behind `if request.method == 'POST'`
WRITES_ON_GET = "writes-on-get"     # a bare GET is enough - issue #84

# Not recorded per route, because it is a property of a shared helper rather than of
# any one view: `utility.get_user_column_setup()` creates the caller's
# user_column_settings row when it is missing, and `authentication.settings` does the
# same for user_filters. Every table-rendering page therefore writes on its first
# visit by a given user. It is idempotent and not caller-controlled, so the write
# detector seeds those rows up front rather than treating each page as a writer.

# McritClient methods that change backend state or queue work. Used to detect a
# route that writes on GET without anyone having declared it, so keep it current
# when the client grows a new verb.
MUTATING_CLIENT_CALLS = {
    "addBinarySample",
    "addImportData",
    "addReport",
    "deleteFamily",
    "deleteJob",
    "deleteSample",
    "modifyFamily",
    "modifySample",
    "rebuildIndex",
    "recalculateMinHashes",
    "recalculateMinHashesForSample",
    "recalculatePicHashes",
    "requestMatchesCross",
    "requestMatchesForMappedBinary",
    "requestMatchesForSample",
    "requestMatchesForSampleVs",
    "requestMatchesForSmdaReport",
    "requestMatchesForUnmappedBinary",
    "requestUniqueBlocksForFamily",
    # the method is plural - the singular spelling matched nothing on McritClient, so
    # the detector was blind to every unique-blocks submission
    "requestUniqueBlocksForSamples",
    "respawn",
    "updateFamily",
    "updateSample",
}

# Routes whose rejection is performed inside the view instead of by a decorator, so
# an unauthorized caller is redirected to the index rather than to /login. Both
# ratchets below only ever shrink: an entry that is no longer needed is a cleanup,
# a new entry is a regression. Both are empty - keep them that way.
IN_VIEW_GUARD = set()

# `@<role>_required` written above `@bp.route` never runs, because bp.route is
# applied first and registers the undecorated function.
KNOWN_INERT_DECORATORS = set()

ROUTE_POLICY = {
    # --- public surface ----------------------------------------------------------
    # Admits anyone, but index.html gates all content behind `{% if g.user %}` and
    # shows 'pending' only a holding message, so nothing leaks into the HTML. The
    # view does run getQueueData, a getSampleById/getFamily per job and
    # search_samples *before* rendering, so an anonymous hit still costs the backend
    # an N+1 round of queries whose results are discarded. A role decorator cannot
    # fix that on its own: index also performs the first-user redirect to /register,
    # which a gate in front of it would turn into a redirect to /login.
    "index": (PUBLIC, READ_ONLY),
    "help": (PUBLIC, READ_ONLY),   # the user manual, linked from the navbar
    # the manual's screenshots. Public because the page they illustrate is, and
    # served from docs/manual/images/ rather than static/ so that the markdown's
    # relative links keep working for readers on GitHub. See issue #91.
    "help_image": (PUBLIC, READ_ONLY),
    "authentication.login": (PUBLIC, WRITES_ON_POST),
    # multi_user only blocks registration in single-user mode, it is not an auth gate
    "authentication.register": (PUBLIC, WRITES_ON_POST),
    "static": (PUBLIC, READ_ONLY),
    "dropzone.static": (PUBLIC, READ_ONLY),

    # --- authenticated, any role including 'pending' -----------------------------
    "authentication.logout": (LOGGED_IN, WRITES_ON_GET),   # session teardown, by design
    "authentication.settings": (LOGGED_IN, READ_ONLY),
    "admin.change_username": (LOGGED_IN, WRITES_ON_POST),   # GET raises 400 on request.form
    "admin.change_password": (LOGGED_IN, WRITES_ON_POST),   # GET raises 400 on request.form
    "admin.change_default_filter": (LOGGED_IN, WRITES_ON_POST),
    "admin.change_column_settings": (LOGGED_IN, WRITES_ON_POST),
    "admin.reset_column_settings": (LOGGED_IN, WRITES_ON_POST),
    "admin.regenerate_apitoken": (LOGGED_IN, WRITES_ON_POST),   # only ever the caller's own row

    # --- visitor and above -------------------------------------------------------
    "explore.families": (VISITOR, READ_ONLY),
    # the family type-ahead behind the edit modals (#77). Visitors already read every
    # family name off explore.families, so this exposes nothing the listing does not.
    "explore.family_names": (VISITOR, READ_ONLY),
    "explore.family_by_id": (VISITOR, READ_ONLY),
    "explore.samples": (VISITOR, READ_ONLY),
    "explore.sample_by_id": (VISITOR, READ_ONLY),
    "explore.functions": (VISITOR, READ_ONLY),
    "explore.function_by_id": (VISITOR, READ_ONLY),
    "explore.fetchDotGraph": (VISITOR, READ_ONLY),
    "explore.fetchCombinedDotGraph": (VISITOR, READ_ONLY),
    "explore.findLoops": (VISITOR, READ_ONLY),
    "explore.getPicBlockMatches": (VISITOR, READ_ONLY),
    "explore.search": (VISITOR, READ_ONLY),
    "explore.statistics": (VISITOR, READ_ONLY),
    "analyze.compare": (VISITOR, READ_ONLY),
    "analyze.compare_versus": (VISITOR, READ_ONLY),
    "analyze.compare_submit_query": (VISITOR, READ_ONLY),
    "analyze.cross_compare": (VISITOR, READ_ONLY),
    "analyze.cross_compare_from_hash_list": (VISITOR, READ_ONLY),
    # the configuration page for unique blocks - it selects samples and looks them up,
    # the submission is the separate route below. See issue #93.
    "analyze.unique_blocks": (VISITOR, READ_ONLY),
    # Job submission by GET, deliberately: the URL names a comparison, so it is worth
    # bookmarking and sharing. Not destructive, and idempotent since issue #97 - the
    # backend returns the existing job for identical parameters unless the caller asks
    # for a recalculation, so a repeat, a prefetch or a double-click costs nothing.
    "analyze.compare_all": (VISITOR, WRITES_ON_GET),
    "analyze.compare_vs": (VISITOR, WRITES_ON_GET),
    # same shape one level down: it queues the *parent sample's* 1-vs-N and lands on
    # that report filtered to the function, because the backend has no per-function
    # job. Reuses an existing job unless asked to rematch, so it is idempotent too.
    #
    # It does widen the surface, and the docstring above is why that is worth writing
    # down: this row takes a *function* id where its siblings take a sample id, so
    # many more URLs reach it - `<img src="/analyze/compare_function/12345">` on any
    # page a logged-in visitor loads queues a match. The set of *jobs* reachable that
    # way does not grow, because every function resolves to its parent sample and
    # `analyze.compare_all` already queues those from a bare GET. So this is more
    # spellings of an existing capability, not a new one.
    "analyze.compare_function": (VISITOR, WRITES_ON_GET),
    "analyze.blocks_family": (VISITOR, WRITES_ON_GET),
    "analyze.blocks_sample": (VISITOR, WRITES_ON_GET),
    # the same shape as the two above and as start_cross_compare, and unconditionally
    # idempotent rather than idempotent-unless-asked: neither unique-blocks method takes
    # a force_recalculation, so mcrit answers a repeat from its descriptor cache. The
    # route sorts and deduplicates the selection so that a repeat hashes the same.
    "analyze.start_unique_blocks": (VISITOR, WRITES_ON_GET),
    "analyze.start_cross_compare": (VISITOR, WRITES_ON_GET),
    "analyze.query": (VISITOR, WRITES_ON_POST),
    "data.jobs": (VISITOR, READ_ONLY),
    "data.job_by_id": (VISITOR, READ_ONLY),
    "data.job_status": (VISITOR, READ_ONLY),
    "data.result": (VISITOR, READ_ONLY),
    # writes the report to instance/cache/results on a cache miss, exactly as
    # data.result does, which is local caching rather than a state change
    "data.download_result": (VISITOR, READ_ONLY),
    "data.linkhunt": (VISITOR, READ_ONLY),
    "data.match_functions": (VISITOR, READ_ONLY),
    # serves instance/cache/diagrams; the <img> tags on the result pages carry the
    # session cookie, so gating it costs nothing and stops a cached diagram being
    # fetchable by anyone who knows a job_id
    "data.diagram_file": (VISITOR, READ_ONLY),

    # --- contributor and above ---------------------------------------------------
    "data.submit": (CONTRIBUTOR, WRITES_ON_POST),
    "data.submit_or_query": (CONTRIBUTOR, WRITES_ON_POST),
    # adds the file a query was run for to the corpus - the same write data.submit
    # performs, from a copy already on disk instead of a fresh upload. See issue #9.
    "data.promote_query": (CONTRIBUTOR, WRITES_ON_POST),
    "data.import_view": (CONTRIBUTOR, WRITES_ON_POST),
    "data.import_complete": (CONTRIBUTOR, READ_ONLY),
    "data.export_view": (CONTRIBUTOR, READ_ONLY),
    "data.specific_export": (CONTRIBUTOR, READ_ONLY),
    "data.request_filename_info": (CONTRIBUTOR, READ_ONLY),   # classifies a posted filename
    "data.delete_job_by_id": (CONTRIBUTOR, WRITES_ON_POST),
    "explore.modifyFamily": (CONTRIBUTOR, WRITES_ON_POST),
    "explore.modifySample": (CONTRIBUTOR, WRITES_ON_POST),

    # --- admin only --------------------------------------------------------------
    "admin.users": (ADMIN, READ_ONLY),
    "admin.server": (ADMIN, READ_ONLY),
    "admin.delete_user": (ADMIN, WRITES_ON_POST),
    "admin.change_user_role": (ADMIN, WRITES_ON_POST),
    "admin.change_server": (ADMIN, WRITES_ON_POST),
    "admin.reset_server": (ADMIN, WRITES_ON_POST),
    "admin.schedule_rebuild_index": (ADMIN, WRITES_ON_POST),
    "admin.schedule_recalc_minhashes": (ADMIN, WRITES_ON_POST),
    "admin.schedule_recalc_pichashes": (ADMIN, WRITES_ON_POST),

    # --- token authenticated -----------------------------------------------------
    # Dispatches an allowlist of ~18 path patterns to the backend. The gate is the
    # `apitoken` header, and the token's role is applied on top of it: 'pending' is
    # refused outright, POST /samples (addReport) needs contributor as data.submit
    # does, everything else is a read or a job submission at visitor level. See
    # tests/testApiTokens.py.
    "api.api_router": (APITOKEN, WRITES_ON_POST),
}
