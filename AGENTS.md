# AGENTS.md — MCRITweb (Flask front-end)

MCRITweb is the **browser front-end and user management layer** for [MCRIT](https://github.com/danielplohmann/mcrit) (MinHash-based Code Relationship & Investigation Toolkit). It is a Flask application that renders server-side Jinja2 templates and talks to an **existing** MCRIT backend over its REST API via `McritClient` from the `mcrit` package.

This repository owns **no analysis data of its own**. Families, samples, functions, jobs, and matching results all live in the MCRIT backend; the local SQLite database only holds users, their preferences, and the backend connection settings. For the matching methodology (PicHash/MinHash, LSH banding) see the [mcrit `AGENTS.md`](../mcrit/AGENTS.md).

## Repository layout

- `mcritweb/` — package source.
  - `__init__.py` — the **app factory** (`create_app`), blueprint registration, Jinja filters/globals, and the `/` index route.
  - `db.py` — SQLite access layer: `UserInfo`, `ServerInfo`, `UserFilters`, `UserColumnSettings`, plus `init_db()`, `migrate()`, and the `flask init-db` CLI command.
  - `sql/` — `CREATE TABLE` scripts for the four tables (`user`, `user_filters`, `user_column_settings`, `server`).
  - `views/` — one module per blueprint plus helpers.
    - `explore.py` (`/explore`) — families/samples/functions browsing, search, single-entry pages, CFG dot-graph endpoints.
    - `analyze.py` (`/analyze`) — job creation: 1vsN, 1vs1, cross-compare, unique blocks, query-by-upload.
    - `data.py` (`/data`) — jobs, result rendering + filtering, import/export, submit, link hunt, diagram serving. Largest module.
    - `authentication.py` (`/`) — login/register/logout/settings and **all authorization decorators**.
    - `administration.py` (`/admin`) — user management, server settings, per-user filter/column settings, maintenance jobs.
    - `api.py` (`/api`) — token-authenticated **passthrough** to the MCRIT backend REST API.
    - `pagination.py` / `cursor_pagination.py` — the two pagination models (see "Key concepts").
    - `client.py` — `get_client()`, the single construction point for the backend client.
    - `utility.py` — server URL/token lookup, `mcrit_server_required`, session user + username resolution, per-user column setup, path setup, version parsing.
    - `params.py` — every `parse_*_query_param` / `parse_*_post_param` helper, plus filename-derived bitness and base address.
    - `MatchReportRenderer.py` — PIL-based rendering of the stacked match diagram PNGs.
    - `ScoreColorProvider.py`, `cross_compare.py`, `cfg_explorer_detector.py` — presentation helpers.
  - `templates/` — Jinja2 templates. `base.html` is the layout; `table/` holds reusable row/header macros; `js/` holds script partials.
  - `static/` — **vendored** front-end assets (Bootstrap 5.0.2, jQuery, jQuery-UI, DataTables, Dropzone, Font Awesome, SortableJS, `trace_CFG/` from CFGExplorer, `autocomplete.js`) plus project CSS/JS.
    - **`autocomplete.js` is vendored, and nothing in the file says so** — no banner, no version, no license text, and it sits at the top level rather than in a directory of its own, so it reads as ours and has been mistaken for ours. It is byte-for-byte `gch1p/bootstrap-5-autocomplete` at commit `5ce977959b12` (2022-01-05), line endings aside — our copy is CRLF. Verify, and expect no output: `curl -s https://raw.githubusercontent.com/gch1p/bootstrap-5-autocomplete/5ce977959b12/autocomplete.js | diff --strip-trailing-cr - mcritweb/static/autocomplete.js`. That revision predates upstream's ESM wrapper, which is why there is no `import`/`export` here and why `e.target` (not the later `e.currentTarget`) is upstream's code rather than a local edit. Upstream declares MIT **in its README only** — there is no LICENSE file and no copyright line to vendor — so this provenance note is the record.
- `instance/` — runtime state, **git-ignored**: `mcritweb.sqlite`, `cache/` (results + diagrams), `temp/` (uploads, reports). Optional `instance/config.py` overrides app config.
- `tests/` — the offline suite (see "Testing"). `conftest.py` holds the app and backend fixtures, `routePolicy.py` the declared access policy, `fixtureData.py` + `fixtures/` the captured backend reports.
- `docs/manual/` — the user manual (markdown + screenshots), for readers on GitHub.
- `docs/agents/` — configuration read by the agent skills: issue tracker, triage labels, domain-doc layout.
- **The user manual has one copy: `docs/manual/README.md`.** `mcritweb/manual.py` renders it at request time for `/help`; `templates/help.html` is only the frame around the result, and prose written into it is the duplication of #91 coming back. Screenshots live beside the markdown in `docs/manual/images/` — the markdown's relative links are what make it render on GitHub — and are served by the `help_image` route, with the prefix substituted at render time. Markdown's `toc` extension supplies the heading ids that four templates link to (`url_for('help') + '#search'`), so it is load-bearing rather than decorative.
- `setup.py`, `requirements.txt`, `flask_env.sh`, `Makefile` — build/run config.

## Development setup

The README states Python 3.12+ - inherited from mcrit, which has declared it since v1.10.0 (v1.5.0 to v1.9.x declared 3.11); the reference deployment (`docker-mcrit`) runs **Python 3.12**. Target 3.12 for anything new.

```bash
make init   # requirements.txt, plus pytest/pytest-cov/ruff at the versions CI pins
```

`pytest`, `pytest-cov` and `ruff` are not runtime dependencies, so they are not in
`requirements.txt`; `mcrit` declares the first two under its `dev` extra, so they do
not arrive with it either.

A running MCRIT backend (server + worker + MongoDB) is required for essentially every page beyond login/register. Without it, `mcrit_server_required` flashes an error and redirects to the index.

## Common commands

```bash
source ./flask_env.sh     # sets FLASK_APP=mcritweb, FLASK_DEBUG=1
flask init-db             # once, before first use — creates instance/mcritweb.sqlite
flask run                 # http://127.0.0.1:5000/
python -m pytest          # the offline suite, no backend needed
ruff check .              # config in ruff.toml; CI runs exactly this
```

The first browser visit redirects to `/register`; the first registered user automatically becomes `admin` and configures the backend URL/token in the same form.

Optional: set `PROFILER=True` in `instance/config.py` while `FLASK_DEBUG=1` to enable Werkzeug's `ProfilerMiddleware` (output in `instance/profiler/`).

## Architecture primer

- **App factory + blueprints.** `create_app()` builds the app, calls `db.init_app` and `db.migrate`, then registers the six blueprints. There is no ORM and no Flask extension for auth — everything is hand-rolled around `sqlite3` and `flask.session`.
- **One seam for the backend client.** Views call `get_client()` from `views/client.py`; never construct a `McritClient` directly. The no-argument case is cached on `g` for the request, so a page of views reads the server URL and token from SQLite once. Passing kwargs (the API passthrough needs `raw_responses=True` and its own header-derived username) always returns a fresh instance. Tests substitute a backend through the `MCRIT_CLIENT_FACTORY` config key, which is why the seam exists.
- **Request → job → result.** Long-running operations (matching, cross-compare, unique blocks, submissions) return a `job_id`; views redirect to `data.job_by_id` with a `refresh=N` parameter, and the job page polls until the result is ready. Result rendering dispatches on the `job_info.parameters` prefix in `data.result()`.
- **Result caching.** Fetched result JSON is written to `instance/cache/results/`, and match diagrams are rendered once to `instance/cache/diagrams/<job_id>[-famid_N|-samid_N|-funid_N].png`. Both are keyed by `job_id` and never invalidated — a changed renderer needs the cache cleared to be visible.
- **Filtering happens client-of-backend side.** `MatchingResult.setFilterValues()` / `.applyFilterValues()` (from `mcrit`) are driven by query parameters, falling back to the user's stored `UserFilters` when no filter params are present.

## Key concepts

**The domain vocabulary lives in [`CONTEXT.md`](CONTEXT.md)** — Family, Sample, Function, Query, Job, MinHash, PicHash, Band, Library, the three tokens, roles and operation mode. Read it before naming anything. What follows is the mechanism behind those terms, not their definitions.

- **Role enforcement** — decorators in `authentication.py`: `login_required`, `visitor_required`, `contributor_required`, `admin_required`, plus `token_required` (API) and `multi_user`. `mcrit_server_required` (in `utility.py`) checks backend reachability. Apply the **narrowest** role a route needs, and place the role decorator **before** `mcrit_server_required` so authorization is settled without a backend round-trip.
- **`@bp.route` goes on top, always.** Decorators apply bottom-up, so `bp.route` runs first and registers whatever function is beneath it. An auth decorator written *above* `@bp.route` wraps a name Flask never sees and enforces nothing, while reading exactly like protection — it has happened twice in this codebase. `testRoutePolicy.py` fails on any new occurrence.
  ```python
  @bp.route('/settings')   # first line, outermost
  @login_required          # everything that must run is below it
  def settings():
  ```
- **Where the settings live** — operation mode, both server-side tokens and the backend URL are columns on the single-row `server` table; per-user tokens are `user.apitoken`. `multi_user` blocks registration in single-user mode.
- **Two paginations** — `CursorPagination` (cursor-based, for backend `search_*` endpoints; supports prefixes so several tables can paginate on one page) and `Pagination` (offset-based, for slicing in-memory result lists). Use `CursorPagination` for anything backed by a backend search.
- **User column settings** — `UserColumnSettings` lets each user pick and order the columns of seven tables. Positions are integers, `-1` meaning "not active".
- **User filters** — `UserFilters` stores the defaults; `MatchingResult.setFilterValues()` / `.applyFilterValues()` apply them.

## Code conventions

- **Python:** 4-space indent. `ruff check .` is configured (`ruff.toml`) and CI runs exactly that; no formatter is configured, deliberately — this codebase has never been formatted and reflowing it would bury the history of every file. Match the surrounding style rather than reformatting; do **not** introduce a repo-wide reformat as a side effect of a change.
- **Templates:** Jinja2 with Bootstrap 5 markup. Put reusable table rows/headers in `templates/table/` as macros and import them; do not copy row markup between pages. To make a *particular* row render differently - a badge on an exact hit, a tinted selection - pass the table macro `row_decorations`, a `{row_id: decoration}` mapping (`templates/table/row_decoration.html`, issue #53), rather than adding another boolean flag to every macro in the chain. Tint and badge colours in a decoration are names resolved against a palette there, never CSS or class names, because row values come from the corpus.
- **Front-end:** no build step, no npm, no bundler. All libraries are vendored under `static/` and included via `url_for('static', ...)` in `base.html`. Do not add a CDN link (deployments are expected to work offline) and do not add a toolchain without being asked.
- **Tables reload by navigating, not by swapping.** A pagination, sort or page-size click is an ordinary link click; `static/page_loading.js` covers the wait with a spinner. Partial rendering was considered and declined in [ADR-0003](docs/adr/0014-no-htmx-for-table-reloads.md) (issue #60) — note in particular that every row-click handler is a direct `$(sel).click(...)` bound at `$(document).ready`, so replacing a table's markup kills its rows silently.
- **Route naming:** blueprint + snake_case function; always build URLs with `url_for(...)`, never string concatenation.
- **User feedback:** `flash(msg, category=...)` with categories `error` / `warning` / `success` / `info` — `base.html` maps these to Bootstrap alert classes.
- **Request parsing:** use the `parse_*_query_param` / `parse_*_post_param` helpers in `params.py` instead of ad-hoc `request.args.get` + `int()`.
- **Logging:** the codebase uses bare `print()` in several places. Prefer `current_app.logger` for anything new; don't mass-convert existing calls.
- **License:** GPL-3.0-only.

## Web-specific guardrails

- **Autoescaping is your safety net — `|safe` disables it.** Any value crossing into JavaScript uses `{{ value|tojson }}`, which escapes for a script context; `tests/testScriptEscaping.py` fails on a `|safe` anywhere inside a `<script>` block, so there is no exception list to consult. Note that plain autoescaping is *no* substitute there: it is HTML escaping, and a browser does not decode `&#34;` inside script content, so dropping `|safe` produces a mangled literal rather than a safe one. Never pass user- or backend-supplied strings through `|safe` — a family name is chosen by whoever submits or renames a family, and #85 was a real breakout, not a hypothetical one.
- **`|tojson` ends at the script tag, and a JS sink can be further on.** `static/autocomplete.js` builds each type-ahead suggestion as an HTML string and assigns it to `innerHTML`, so a family name that *is* markup executed there even though `|tojson` had delivered it as a perfectly correct JS string. Two sinks in that one string, not one: the button's text, and the `data-label`/`data-value` attributes the same value is interpolated into — an attribute break needs no angle bracket, so escaping only `<`/`>` would look like a fix and not be one. It is vendored and not ours to patch, so both call sites — `templates/js/ac_family_names.html` and the `submit_or_query_dropzone` macro — feed it through the `autocomplete_items` filter (`__init__.py`), which HTML-escapes label and value on the way in. `tests/testAutocompleteEscaping.py` fails on a `new Autocomplete(`/`setData(` fed data the filter did not build. When a value crosses into JavaScript, ask what the script does with it next — `|tojson` is the transport, not the whole answer.
  - **The escaping costs display, and the cost is real.** The widget finds the lookup in the escaped label and then slices *that* string at the offsets it found (`autocomplete.js:70-77`), so a lookup ending inside an entity severs it: a family named `R&D`, searched for as `R&`, renders as `R&amp;D`. Matching degrades the same way — a lookup containing `&`, `<`, `>`, `"` or `'` no longer substring-matches. Selecting is unaffected; `data-label` still decodes to the original name, so the form posts what it always did. It **cannot** be fixed from the call site: `item.label` (`:110`) is the single value the matcher (`:114`), the highlight index (`:70-72`), the rendered slices (`:75-77`) and the attribute (`:90`) all read, so there is no second field to point at raw text for matching and escaped text for rendering. Fixing it properly means overriding `createItem` from our own script, or replacing the widget — either way, a deliberate change, not a drive-by. `test_the_highlighter_severs_an_entity` pins the current behaviour and tells you when it stops.
- **Every unsafe method needs a CSRF token** — `mcritweb/csrf.py`, a `before_request` check on anything that is not `GET`/`HEAD`/`OPTIONS`/`TRACE`. A new form gets `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`; `tests/testCsrf.py` fails on a `method=post` form that does not have one. Scripts read the token from `<meta name="csrf-token">` (`csrfToken()` in `post_action.js`) and send it as an `X-CSRFToken` header. Exempt a route only when it does not authenticate by session cookie — `/api` is the one case, since a cross-site request cannot supply its `apitoken` header. The module deliberately mirrors `flask-wtf`'s names so issue #27 can swap it for the real extension without touching a template; see [ADR-0002](docs/adr/0002-hand-rolled-csrf.md).
- **A route that writes must be `POST`-only** — `methods=('POST',)`, not a `request.method` check inside a route that also accepts `GET`. A `GET` that writes can be fired by anything that makes a browser fetch a URL: an `<img>` tag, a link scanner, a prefetch. `tests/routePolicy.py` records which routes write and `testRoutePolicy.py` detects a `GET` that writes without saying so. The five `analyze` job submitters are the deliberate exception — navigational URLs that queue a job (issue #97). They are safe to repeat because the **backend already deduplicates**: `QueueRemoteCalls` hashes the method name and parameters into a descriptor and returns the existing job unless `force_recalculation` is set. That only holds if the flag is a real `bool` — forward a raw query string and `"false"` arrives truthy, which forces a fresh job every visit. Parse any such flag with `parse_checkbox_query_param`. Do not copy the `GET`-writes shape itself for anything new.
- **Actions are not links.** For a control that triggers a write, use `<button data-post="{{ url_for(...) }}">` — `static/post_action.js` turns the click into a `POST`. An `<a href>` to a writing route is a bug even if JavaScript intercepts it, because middle-click and prefetch do not run the handler.
- **`SECRET_KEY` is generated and kept in `instance/secret_key`** when the operator has not set one in `instance/config.py` (`mcritweb/secret_key.py`). It used to default to `'dev'`, which let anyone reading this repository sign a session cookie saying `role: admin`. An explicit key still wins, and is still the right answer for a multi-host deployment, where every host must share one.
- **Never log or render secrets:** user API tokens, the server token, password hashes. `ServerInfo.__str__` contains tokens — do not `print` or flash it.
- **`request.remote_addr` is the proxy unless `TRUSTED_PROXY_COUNT` says otherwise.** The reference deployment puts NGINX in front, so the app's only peer is the proxy. `create_app()` wraps `wsgi_app` in Werkzeug's `ProxyFix` when that key is non-zero, taking the Nth `X-Forwarded-For` value **from the right** — the end a trusted proxy appends to. It defaults to `0`: the header is client-written until a trusted proxy has touched it, so trusting it uninvited lets a caller choose their own identity, and a value that is not a hop count (a bool, a float, a count past `MAX_TRUSTED_PROXY_COUNT`) is refused back to `0` rather than guessed at. The count drives `X-Forwarded-For` only — `X-Forwarded-Proto` is replaced rather than appended by a proxy, so it is always read one value deep. Anything that meters, rate-limits or logs per client reads `request.remote_addr` and therefore depends on this being set correctly; do not add a second, ad-hoc way to guess the client address. `tests/testTrustedProxy.py` covers both directions, README documents the setting.
- **Uploads** land in `instance/temp/uploads/` named by SHA-256. Two ceilings apply and both are config keys, set in `create_app()` and overridable from `instance/config.py`: `MAX_CONTENT_LENGTH` is the app-wide request body limit Werkzeug enforces with a 413 before buffering — generous by necessity, since it applies to every route and `/data/import` takes whole-corpus exports — and `QUERY_UPLOAD_LIMITS` is a `{role: bytes}` mapping checked in `analyze.query`, defaulting to 1 MiB for `visitor` and uncapped for any role it does not name (#19). Keep both checks in place when touching the upload paths; `tests/testAppConfig.py` covers them.
- **Uploads** land in `instance/temp/uploads/`. A query upload (`analyze.query`) is named by the **job id** the backend issued for it, and is written only once that job is queued; `data.submit` still names its own copy by the SHA-256 of the bytes. No part of either name may come from the request: naming a `.smda` query by the `sha256` its own report declares let any visitor overwrite another user's stored query, and naming it by a digest of the upload instead makes it unfindable, because promoting a query to a sample (#9) has only the job to go on. `views/utility.query_upload_path` is the single definition of that path, used by both sides. Two ceilings apply and both are config keys, set in `create_app()` and overridable from `instance/config.py`: `MAX_CONTENT_LENGTH` is the app-wide request body limit Werkzeug enforces with a 413 before buffering — generous by necessity, since it applies to every route and `/data/import` takes whole-corpus exports — and `QUERY_UPLOAD_LIMITS` is a `{role: bytes}` mapping checked in `analyze.query`, defaulting to 1 MiB for `visitor` and uncapped for any role it does not name (#19). Keep both checks in place when touching the upload paths; `tests/testAppConfig.py` covers them.
- **The `/api` blueprint is a passthrough, not an API of its own.** When the backend `McritClient` gains a method, extend the router in `api.py` by adding a regex branch — keep paths and parameter names aligned with the backend's REST API rather than inventing new ones. A token carries its owner's role (`g.api_user`), so a branch that writes must also be listed in `CONTRIBUTOR_ONLY` in that module; otherwise the API becomes the cheap way around a role check in the UI.
- **Validate IDs before use.** Route converters use `<int(signed=True):...>` where negative IDs are meaningful (query samples have negative `sample_id`). Check `client.isSampleId` / `isFamilyId` / `isFunctionId` before acting on user-supplied IDs.

## Database changes

The SQLite schema is versioned by hand — there is no migration framework. Adding or changing a column means touching **all** of:

1. `mcritweb/sql/create_table_*.sql` — the fresh-install schema.
2. The corresponding class in `mcritweb/db.py` (`fromDb` / `fromDict` / `toDict` / `saveToDb`).
3. `db.migrate()` — an idempotent `ALTER TABLE` / `CREATE TABLE` guarded by a check, so existing deployments upgrade on next start.
4. The README "Version History" entry, flagging the DB change (existing entries use `BREAKS DB -> ...`).

Adding a **table column setting** additionally means updating `UserColumnSettings._default_settings`, `create_table_user_column_settings.sql`, and the relevant row/header macros in `templates/table/`. The `sql/` scripts start with `DROP TABLE IF EXISTS` — they are for initialization only and must never be run against a populated database.

## Testing

`python -m pytest` runs the suite with **no backend and no network** — pagination, user filters, the app-factory fixtures in `tests/conftest.py`, and `testMigrations.py`, which upgrades databases built in historical schemas (transcribed from release tags, not read from git — a CI checkout has no tags). `pytest.ini` maps the existing `test*.py` naming; keep it rather than renaming to `test_foo.py`. `make test` and `make lint` run exactly what CI runs (`python3 -m pytest`, `python3 -m ruff check .`) — they used to call `nose` and a `.pylintrc` that has never existed in this repository, fixed in v1.4.7.

Three backends are available to tests, all offline. `fake_mcrit` is strict — an unknown method raises `NotImplementedError` naming itself, so gaps surface as actionable failures. `recording_mcrit` never raises, for asking "did this request write anything". `corpus_mcrit` serves real captured reports from `tests/fixtures/` and is what makes result pages renderable; see the README there.

`corpus_mcrit` also implements the **search/cursor protocol** (`_page` in `fixtureData.py`), so `explore.*` can be tested with rows rather than against an empty result set. It models the contract the views depend on — an opaque token, a forward cursor only while results remain, a backward one only off the first page — not mcrit's cursor encoding or its `field:value` query parser; a test needing those needs a real backend. Note that `search_results` values are **dicts**, as they arrive off the wire, and the views must call `.fromDict` on them — as must `id_match` and `sha_match`. That is the client's inconsistency, not a choice of the fake's; see [ADR-0003](docs/adr/0010-search-results-arrive-as-dicts.md) for what `mcrit` would have to change to remove the requirement.

**Adding a route means adding a row to `tests/routePolicy.py`** — who may call it, and whether it writes. `testRoutePolicy.py` fails on any endpoint in the url_map without one. That table is the record of the current access policy; change a value only together with the code, so it keeps describing reality. Two sets in it, `IN_VIEW_GUARD` and `KNOWN_INERT_DECORATORS`, are ratchets: both are empty today, and both may only shrink. An entry appearing in either is a regression, not a note.

`tests/fixtures/` holds captured backend reports; `tests/fixtures/regenerate.py` rebuilds them against any instance that has one finished job of each type, so they are not tied to one machine. Two trims in it are load-bearing rather than cosmetic — read its module docstring before changing them.

Coverage is thin and nothing exercises a real backend, so for anything touching views or templates still **verify by exercising the app**: `flask run` against a reachable MCRIT backend and walk the affected pages. When changing shared template macros (`table/*.html`), check every page that imports them — a macro is typically used by 3–5 templates. Results are cached under `instance/cache/` and never invalidated, so clear it when validating result rendering.

CI (`.github/workflows/test.yml`) runs `ruff check .` plus the suite on Python 3.12, 3.13 and 3.14 — the last two became reachable only once the Flask 2.2.5 pin was lifted in #27, since it calls `pkgutil.get_loader`, removed in 3.14. There is deliberately **no `ruff format` check** — this codebase has never been formatted and reflowing it would bury the history of every file. Keep `ruff check .` clean; the rule set in `ruff.toml` mirrors mcrit's.

## Versioning & releases

- The version lives in `setup.py` and is **parsed at runtime** by `get_mcritweb_version_from_setup()` (regex on `version="X.Y.Z",`) — keep that literal format intact.
- A release adds a dated entry at the top of the README "Version History" (` * YYYY-MM-DD vX.Y.Z: <summary>`) and bumps `setup.py`. Historic commit message for this: `bump X.Y.Z`.
- **Do not bump the version unless explicitly asked.**
- MCRITweb is **deployed from a checkout** — a container image or a local clone — and no wheel or sdist is ever built or published. `setup.py` exists for the runtime version string and for `pip install -e .`; its `packages` list is not a distribution concern.
- `mcrit>=1.10.0` is pinned in both `setup.py` and `requirements.txt` — the two must stay in sync. MCRITweb consumes backend data classes (`MatchingResult`, `SampleEntry`, `FunctionEntry`, `UniqueBlocksResult`, …) directly, so a backend release can break rendering; when a fix depends on new backend behavior, raise the floor in both files and say so in the changelog entry.
- `flask>=3.0` and `werkzeug>=3.0`. The old hard pins at 2.2.5 / 2.3.3 were lifted in issue #27; the lower bounds are there to stop a resolver sliding back to a 2.x that cannot run on Python 3.12+. See ADR-0001 for what was checked.

## Agent guardrails

- **Never** run `git commit`, `git push`, or open a PR unless explicitly instructed.
- **Never** commit anything from `instance/` (SQLite DB, uploads, cached results/diagrams) or an `instance/config.py`.
- **Do not** modify vendored assets under `static/` (Bootstrap, jQuery, DataTables, Dropzone, Font Awesome, SortableJS, `trace_CFG/`, `autocomplete.js`); most carry their own licenses, and `autocomplete.js` carries none at all — its provenance is recorded above instead, which is the only thing standing between it and being edited as if it were ours.
  - **`static/trace_CFG/main_duo.js` is not stock either, it is a project fork.** It is 3,549 lines against `main.js`'s 3,533 and `diff` between the two is 513 lines: it already carried our own patches (`// MCRIT resize to minimum of width and height...`, the CSRF headers marked `// mcritweb: issue #83`) before issue #74 added the one hook the comparison page needs, marked `// mcritweb, issue #74`: `showGraph()` records each pane's zoom behaviour in `graph_zooms` and calls `onGraphShown(graph_id)`, which `static/function_compare.js` (project code) implements. Everything else for the synchronised and combined views lives in `function_compare.js`. Refreshing `main_duo.js` from upstream would silently drop the hook and re-break the CSRF headers; `tests/testFunctionPages.py::test_main_duo_keeps_the_hook_function_compare_needs` fails when it is gone. `loopCollapser.js`, `loopify_dagre.js`, `fnManip.js` and `main.js` itself are untouched and should stay that way.
  - **`static/dropzone.js` is the other exception, and it is load-bearing.** It carries our own patch (marked `START OF PATCH FOR EARLY CONTENT DELIVERY`) that reads the head of the dropped file and exposes it as `file.upload.header` / `header_metadata`, which is what pre-fills the submit form. Stock Dropzone has no such field. Replacing this file from upstream silently disables the pre-fill — `request_filename_info` catches bare `Exception` and returns `{}`, so nothing errors. Note also that `static/dropzone.min.js` is **unpatched**, and flask-dropzone's own `dropzone.load()` macro serves exactly that file: never call `dropzone.load()`, both templates load `dropzone.js` by hand for this reason.
- **Do not** change matching or scoring semantics here — MCRITweb only presents what the backend computes. Score→color mappings (`ScoreColorProvider`, `cross_compare.score_to_color`) are presentation and may change; scores themselves may not.
- When work depends on backend behavior, read `../mcrit` rather than guessing at `McritClient`'s surface.
- Clear `instance/cache/` when validating changes to result rendering or diagram generation — otherwise you will be looking at stale output.

## Outward-facing artifacts

Issues, issue comments, PRs — anything carrying the project's name. Filing is hard to un-notify, so the bar is higher than for a local change.

- **Search for duplicates first, and read the near-misses.** Most of this backlog was filed 2022-08 → 2022-11 in a private repository and migrated verbatim, and several items have since had scope silently absorbed by a v1.4.x release. A symptom you just diagnosed is more often a comment on an existing issue than a new issue, and the person who filed it is still waiting. Reading the backlog as if it described today's code will send you to fix what is already fixed.
- **Read the migration footer before trusting a body's age or its issue references.** Every migrated issue ends with `<sub>Migrated from the project's previous, private repository danielplohmann/mcritweb (issue #NN), opened YYYY-MM-DD by …</sub>`, so the GitHub creation date (2026-08-04 for most of the backlog) is the migration date, not the filing date. References *within* those bodies are already safe — they are written as `` `danielplohmann/mcritweb#148` ``, repo-qualified and backticked so GitHub does not autolink them. The footer's own parenthetical is the one trap: it is a bare `#NN`, which GitHub autolinks to whatever live issue now holds that number (#49's footer points at #98, unrelated). Take it as provenance, not as a link.
- **Verify every `file:line` mechanically against the revision you cite** — `git show <rev>:<path> | sed -n '<line>p'` — and name that revision in the comment when the line is load-bearing. A reference written while the working tree carries local edits will be wrong, and wrong line numbers make a reader distrust the whole document. Line numbers also rot: a fix in the same file invalidates them, so prefer naming the symbol and use the line as a pointer, not as the identifier.
- **Tag each claim as measured or inferred**, and give a cheap way to confirm the inferred ones. `tests/` is deliberately offline, so "not reproduced against a real backend" is the normal state for anything past login — say that rather than eliding it. Undifferentiated confidence is how a plausible guess ends up treated as a finding.
- **Say if an implementation already exists** and invite coordination instead of assuming a patch is wanted.
- **Group trivia and low-confidence items into one issue.** Keep separate issues only for things that have to be closed separately.
- **Match the house voice** — read two or three existing issues before writing. The established shape is a short statement of the problem, evidence with `file:line` in backticks, impact, then options or a suggested fix, and no more confidence than the evidence carries.

## Agent skills

### Issue tracker

GitHub Issues on `fkie-cad/mcritweb`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical labels, used as-is; `wontfix` already exists in the repo. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — the glossary is [`CONTEXT.md`](CONTEXT.md), decisions are in [`docs/adr/`](docs/adr/). See `docs/agents/domain.md` for how the skills consume them.

## Related repositories (reference only)

- [mcrit](https://github.com/danielplohmann/mcrit) — core server, worker, Python client, CLI. MCRITweb is a client of it.
- [docker-mcrit](https://github.com/danielplohmann/docker-mcrit) — the recommended deployment: MongoDB + mcrit-server + mcrit-worker + mcritweb behind NGINX.
- [mcrit-plugins](https://github.com/danielplohmann/mcrit-plugin) — IDA Pro integration plugin (a sibling client of the same backend).
- [smda](https://github.com/danielplohmann/smda) — the disassembler producing the `SmdaReport` format handled in submit/query.
- [mcrit-data](https://github.com/danielplohmann/mcrit-data) — ready-to-use reference data.
