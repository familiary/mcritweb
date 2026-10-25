# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) over what a deployment sees:
the routes, the configuration keys in `instance/config.py`, and the SQLite schema. A release that
changes the schema, adds a dependency or changes a default that affects an existing deployment says
so in its entry, under **Upgrading**, because MCRITweb is deployed from a checkout and a pull that
only brings code is not the release that was tested.

Add your entry to `[Unreleased]` when the change merges, while the reasoning is still at hand,
rather than reconstructing it from the commit log at release time.

## [Unreleased]

### Added

- A dark theme (#70). Every user picks Light, Dark or Match my system under Settings, "Change
  theme"; accounts start on Light, so nothing changes until someone picks another. **BREAKS DB ->
  the `user` table gains a `theme` column**, added on first start by an idempotent migration that
  leaves existing rows NULL, which reads as Light.
- Pushing a `vX.Y.Z` tag now cuts the release. The workflow refuses to continue unless the tag
  matches `pyproject.toml`, `CHANGELOG.md` has a section for it, the commit is on `master` and CI
  passed there; it then builds the sdist and wheel, checks their metadata, installs the wheel into a
  clean environment to prove the templates, static assets and schema ship with the package, and
  creates the GitHub release from that version's changelog section with the generated contributor
  list appended and the artifacts attached. Nothing is published to PyPI: MCRITweb is deployed from
  a checkout. Pre-release tags (`v1.5.0rc1`) are marked as such. See `RELEASING.md`.
- A pull request that changes `mcritweb/` or `pyproject.toml` has to add a `CHANGELOG.md` entry or
  carry the `no-changelog` label; CI checks it.
- The jobs list and the job page show who requested a job (#172). mcrit 1.10 and later record it;
  an older backend, or a job from before, shows a dash. **Upgrading:** every user, visitors
  included, sees the requester of every job.
- Contributors can rename a function from its page, and through `PUT /api/functions/<id>` (#173).
  **Upgrading:** this needs a backend of mcrit 1.10.0 or newer; against an older one the button is
  disabled. A rename is recorded as a label on the backend.
- The admin server page schedules the three corpus repairs mcrit 1.9.0 added, each behind a
  confirmation; their jobs appear in the jobs list and their reports can be read (#212).
- The search term is marked in the rows that matched it (#136).
- The cross compare matrix can be ordered three ways: clustered, by sample id and by family (#159).
- The jobs page search box works, ordering by job number covers the whole queue, and a tab lists the
  per-sample jobs a cross compare runs (#115).
- The Unique Blocks statistics table has a Version column, and each YARA rule comment names the
  function its block comes from (#147).
- The jobs mcrit 1.13 and 1.15 added (`rebuildBandDfIndex`, `rebuildFunctionRangeIndex`,
  `deleteOrphanedQueueFiles`, `getBandDfCutoffCoverage`) have a name and an empty-state message.
- The sample page shows where a sample's sha256 comes from, in its SHA256 row (familiary/mcrit#270).
  A sample MCRIT disassembled from an uploaded binary is marked "verified against the uploaded
  binary"; one indexed from a submitted report or an import carries a note that its sha256 was taken
  from there and not checked against a binary. A sample stored before mcrit recorded this shows
  nothing. Neither does any sample while the `mcrit` package installed with MCRITweb predates the
  field, since MCRITweb reads samples through its `SampleEntry`: nothing breaks, the marker appears
  once the package is upgraded.
- The search boxes of a sample's page, the family and sample lists and a family's page filter their
  table as you type, without reloading the page; Enter still loads it. On a sample's page a bare hex
  number such as `0x6840` finds the function at that offset. The function list and the combined
  search page still wait for Enter, since an unscoped function search can take tens of seconds on
  the backend (docs/adr/0016-slow-function-search-is-mcrits.md).
- A finished or failed job's page has a **Rerun job** button, which queues the same request again
  with a forced recalculation, and a link back to the form it was submitted from with its samples
  and matching parameters filled in (#55). With an MCRIT whose client has `requestJobRerun`
  (familiary/mcrit#311) the backend reruns the job itself from what it stored, so the button covers
  every analysis, queries, unique blocks and 1-vs-group comparisons included, and terminated jobs
  too. A role with a query upload cap - by default a visitor - reruns only its own queries, since a
  stored query may be larger than that role could have uploaded. With an older one it is offered on
  the 1vsN, 1vs1 and cross compare jobs it can rebuild from their parameters.

### Changed

- Packaging metadata moved from `setup.py` to `pyproject.toml` (PEP 621), which is now the only
  place the version is declared: the running version shown on the server page and stored in the
  `server` table is read from it, through `get_mcritweb_version()`. `ruff.toml` and `pytest.ini`
  folded into the same file. `requirements.txt` stays, for `docker-mcrit` and anyone installing
  from a checkout without building the package, and a test keeps it identical to the declared
  dependencies. **Upgrading:** `pip install -r requirements.txt` still works; `pip install -e .`
  is now equivalent, and `pip install -e ".[dev]"` brings the test and lint tooling, which used
  to arrive as a side effect of `mcrit` and no longer does since MCRIT 1.8.0.
- The release history moved out of `README.md` into this file; the entries below are unchanged.
- Every job method has one name, used wherever a job is named; the Task row of the job and result
  pages describes the job in words, with the raw call as its tooltip (#122). Long job descriptions
  wrap (#117).
- An empty table says what is missing, per job category, and no longer invites a visitor to a page
  that answers 403 (#119).
- The jobs list keeps its tab in the URL, so a refresh, the back button and a link reproduce the page
  (#132).
- A running job's overview polls its status and reloads only when something changed (#214).
- Score columns round instead of truncating, and their tooltips state the fraction behind the
  percentage (#148). The Matching Method Statistics are computed for the matches on screen (#142).
- A cross compare result renders only the visible matrix and fills the other tabs on first click
  (#213); its tooltips are built on hover (#226).
- The match diagram is rendered by a request of its own into a reserved box, so the first view of a
  result page shows the box and the image a moment later (#145).
- The result cache is bounded, by `RESULT_CACHE_MAX_BYTES` and `RESULT_CACHE_MAX_FILES`, and stored
  as compact JSON (#229). **Upgrading:** by default it holds 1 GiB or 1000 reports and evicts the
  oldest written; the diagrams cache is still unbounded.
- Sample and family changes are confirmed by reading them back instead of waiting 0.3 s. A family
  rename counts as done only once the old family answers 404, since mcrit moves its samples to the
  family with the new name and deletes the old one (#219).
- Heading levels, labels, contrast and link target sizes, from the Lighthouse accessibility and
  SEO audits (#244).
- Searches are read through one adapter, `views/search.py` (#174).
- The SQLite database runs in WAL mode, switched on at the first start after upgrading and then kept
  in the file, so a write in one gunicorn worker no longer holds up reads in the other, nor a read a
  write. **Backups of `instance/` change:** `mcritweb.sqlite-wal` and `mcritweb.sqlite-shm` now sit
  next to `mcritweb.sqlite`, and copying `mcritweb.sqlite` alone while MCRITweb runs can miss the
  latest changes; see "SQLite database" in `README.md`.
- The function page no longer freezes for seconds on a large function (#267). Above 250 basic blocks
  it shows the code at once and lays the control flow graph out only when asked to; dagre's layout
  had frozen the page for about 19 s on a 971-block function, now the page responds in 0.2 s.
- Control flow graphs are laid out about three times faster on large functions, with the same
  result: 5.6 s instead of 21.7 s for a 971-block function. A patch to the vendored dagre builds the
  graph of each rank in one pass instead of walking the whole graph once per rank.

### Fixed

- The YARA copy button copies what the text area holds (#147).
- One unreadable job no longer takes a job listing down (#115).
- A match diagram is cached under its whole name, its aggregation is counted once, and cache files
  are written atomically (#145).
- The generated `instance/secret_key` is written atomically. When two gunicorn workers started
  without the file, each could keep a key of its own, and a login or CSRF token from one worker was
  then refused by the other until a restart. A fresh docker-mcrit install was not affected, because
  `flask init-db` creates the key before gunicorn starts; upgrading from before 1.4.4, or starting
  after the key file was deleted, was. An explicit `SECRET_KEY` in `instance/config.py` is
  unaffected.
- Control flow graph hovering fixed on both function pages. On a function's page, hovering a node
  scrolls the code panel to that block again; it threw an error on every hover instead, because it
  looked for the comparison page's panel. On the comparison page, a block's tooltip is as wide as
  its longest instruction and stays inside its pane and the window; it was sized from the drawn
  block, so most instructions ran out of the box, and the combined view's "B's code" tooltip was not
  held inside the pane at all.
- Windows' contrast themes (forced colours) no longer hide what Bootstrap draws with fills and
  shadows. A ticked checkbox or chosen radio button looked empty, the MinHash slider vanished, the
  keyboard's focus did not show on buttons, fields, the pager or accordion headers, the current tab
  and page looked like the others, the cross compare matrix and the CFG comparison's legend were
  blank, and a dialog's close cross vanished on a dark palette (on a light one, in the dark theme).
  Each now has something the forced palette keeps: the browser's own boxes, outlines in the user's
  highlight colour, a cross in the user's button text colour, and the data colours left as they
  are.
- In the light theme, the edges of fields, check boxes and radio buttons, and the ring the
  keyboard's focus draws, can be seen: Bootstrap's grey edge was 1.5:1 against the page (a check
  box's 1.8:1) and its focus ring 1.4:1, where WCAG asks 3:1. They are 3.2:1 or more now, on the
  page and on the grey chrome. The search boxes and check boxes that turn the ring off (five pages
  open with the focus in one) show an outline in the accent colour instead, in both themes.
- In the light theme the current page of a pager is underlined in the accent colour, as in the dark
  theme. Its grey fill alone was 1.14:1 against the white page, so the page you were on could hardly
  be told from the others.
- Three light-theme colours that fell short of WCAG are a step darker: the tick of a yes/no column
  (2.5:1 on the darker score tints, now 3.9:1), the "rendering diagram" caption shown while a match
  diagram loads (3.6:1, now 5.0:1), and a link in a table head, such as the jobs list's "#" (4.4:1,
  now 9.4:1).
- In the light theme, the blocks "Show Cycles" and "Show Loops" colour keep readable text. They
  were painted in full-strength colours with white text, 2.1:1 on the orange; the colour now shows
  at half strength under the page's own text colour, 6.9:1 or more, as the dark theme already did
  with its values.
- The upload form's extra fields open and close at once, instead of sliding, for a reader who has
  asked the system for reduced motion; jQuery's slides took no notice of the setting. A change of
  the setting is followed while the page is open.
- A page that is not there, one the account's role does not reach, a form the CSRF check refuses
  and a method a route does not take (404, 403, 400 and 405) are answered with a page in the site's
  layout and in the account's theme. They were Werkzeug's bare documents, white even on the dark
  theme. The page needs nothing from the MCRIT backend. The API, and scripts that ask for JSON,
  get the same answer as before.
- With reduced motion asked for, a file waiting in the drop zone no longer pulses for as long as it
  waits, and the score tooltips appear at once instead of sliding into place.
- In the dark theme, the progress bar of a file waiting in the drop zone is dark too; Dropzone drew
  it as a bright white bar in the dark preview card.
- A reader who has asked the system for more contrast sees the arrow of a column that sorts but is
  not sorted at full strength. It is faint by design, 2.1:1 on the light table head, and it is the
  only sign that a column sorts.

### Security

- jQuery UI goes from 1.13.1 to 1.13.3, which fixes CVE-2022-31160: a `checkboxradio` widget re-read
  its label's escaped text as HTML on refresh. MCRITweb creates no checkboxradio, so no page of its
  own reached the flaw, but the library is loaded on every page. The only jQuery UI widget MCRITweb
  uses, the sortable sample order on cross compare results, behaves as before. Static files only;
  nothing to do beyond deploying the new code.
- Every response carries `Referrer-Policy: same-origin` (#284), so following a link from a MCRITweb
  page to another site no longer tells that site where it came from: current browsers sent it the
  instance's address, older ones the whole URL, with its job id, sample hash or search terms. A
  reverse proxy that adds the header itself should stop doing so, or browsers receive it twice.
- A visitor's query upload over the cap is refused before any of it is parsed, wherever its CSRF
  token was put (#284). The CSRF check used to look for a token in the form when no header carried
  one, which parsed the whole body, up to `MAX_CONTENT_LENGTH`, before `/analyze/query` could refuse
  it. That route now takes its token from the `X-CSRFToken` or `X-CSRF-Token` header only, which is
  how its upload dropzone sends it; a script posting the token there as a form field gets a 400.
- Two registrations or renames at the same moment can no longer take one name in two cases, such as
  `Eve` and `eve` (#284). The case-insensitive lookup that refuses such a name and the write after
  it are now one transaction under SQLite's write lock, as first-user registration already was.
  Pairs already stored are left alone, as before.
- **Logging out ends every session of the account, in every browser, and everyone has to log in once
  more after upgrading** (#284). The session is a signed cookie, so logging out cleared only the
  browser's own copy, and a copy taken from it - a shared machine, a proxy log, a browser profile -
  stayed valid for the 31 days Flask accepts it. Each account now has a session generation that a
  logout moves on, folded into the mark sessions carry since #288, so every session issued before
  the logout ends on its next request. Changing the password still ends the account's other
  sessions. Logging out does not replace the API token, since every client using it would break on
  each logout; the logout message points to `/settings` for issuing a new one. A session without a
  mark is ended rather than given one, which closes the gap v1.6.0 noted - one left unused across a
  password change used to survive it - and since the mark now covers the generation, the sessions
  v1.6.0 marked end too. **Upgrading:** the `user` table gains a `session_generation` column, added
  on first start by an idempotent migration that starts every account at 0.
- The pre-1.5.0 (32-character) API tokens, which 1.5.0 had left valid indefinitely, are now visible
  and movable: a user whose token has the old shape sees a warning and a highlighted regenerate
  button on the settings page, and the admin users page counts the accounts still holding one, marks
  their rows, and offers a "Regenerate all legacy tokens" action (#250). An operator can then retire
  the old tokens by setting `ACCEPT_LEGACY_APITOKENS = False` in `instance/config.py` - the default
  stays `True` for one release - after which a 32-character token is answered with a 403 that says
  so, rather than the bare 403 an unknown token gets. See "API tokens issued before 1.5.0" in
  `README.md` for the operator's view.

## [1.6.0] - 2026-10-16

Written for the README's version history, where it was published, and kept as it was.

Forty-two merged pull requests: twelve security fixes, a timeout on every backend call, and pages that cost the server much less. **Five changes affect existing deployments.** *(1)* **Python 3.12 and mcrit 1.12.0 or later are required** (#242, #248), following mcrit's own floor since 1.10.0. Against an older backend the batch lookups fall back to single requests, so MCRITweb and MCRIT need not upgrade in lockstep. *(2)* **Changing a password replaces the account's API token and ends its other sessions** (#288): the token is shown on `/settings`, so whoever could read that page with a stolen session could have copied it. Anything using the old token has to be given the new one, and the flash message after the change says so. A session from before the upgrade carries no mark until its next request, so one left unused across a password change survives that change. *(3)* **Pages refuse to be framed by other sites, and every response carries `X-Content-Type-Options: nosniff`** (#289, #272), so embedding MCRITweb in another site's page stops working (`frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`). A reverse proxy that adds either header itself should stop doing so, or browsers receive it twice. *(4)* **Every backend call is bounded by `MCRIT_CLIENT_TIMEOUT`**, a new `(connect, read)` setting that defaults to `(10, 280)` seconds, under the 300 s docker-mcrit's NGINX waits (#241). A hung backend no longer holds a gunicorn thread for good. Raise it for whole-corpus imports or exports through the API passthrough. *(5)* Submitting a binary no longer leaves a SHA-256-named copy in `instance/temp/uploads/` (#211). Existing files there whose names are 64 hex digits are orphaned and can be removed. **Security fixes.** Only one of two simultaneous first registrations becomes admin (#286). A rename to a name taken after the lookup is refused (#277), and new usernames that differ from an existing one only in case are refused at registration and rename, while names already stored are left alone (#287). The per-role query upload cap applies to the `/api` query routes too (#273), and it is checked from `Content-Length` before the body is read (#290). A successful login forgets only the failed logins of the name that logged in, and wrong registration tokens are counted on their own budget, which a login can no longer reset (#276). Saving a user writes back only the columns the request changed, so a login in flight can no longer undo a demotion, a token rotation or a rename, or re-create a deleted account (#278). The username and password forms accept only POST, and "username taken" is checked only after the password is proven (#231). Dot-graph routes are served as `text/plain` (#272). **Performance.** The backend reachability probe is reused for 5 s instead of once per request (#114). Pages look each sample and family up once, and the missing ones in one batched request (#221, #238). Job dependencies are fetched in chunks of 100 (#238), and sample listings ask only for their page's jobs (#239). The submit form fetches family names as they are typed (#222). Link hunt results are memoized, so paging or changing link scores takes 20-100 ms instead of 0.5-1.6 s (#217), and the unique-blocks cover and function comparison are cached too (#215, #216). The match diagram is 16-123x faster with byte-identical images (#218), the CFG view and the function match table do one pass instead of many (#230, #224), and the API passthrough forwards the backend's bytes instead of re-encoding them (#228). Also #223, #232, #220, #246, #227, #237. **Visible changes.** Link hunt clusters show 10 per page, and long clusters fold (#225). An empty result, a failed job and a terminated job each get their own page instead of "job not found" (#107), and a result whose job type is unknown renders a page instead of a 500 (#240). API clients receive the backend's JSON error bodies instead of empty ones (#228). A session whose user was deleted lands on the login page (#220), and a sample without a timestamp shows none instead of "None" (#285). **Fixes.** Block comments no longer land on the wrong CFG node when one offset's hex is a prefix of another's (#230). Malformed function-ID lists reach the backend instead of raising an unbound-name error, a form-encoded list posted to `/api/functions` is read as the list it is, and hash-list cross comparison no longer runs an unused sample search (#208, #201). Link hunt defaults the family count when the form leaves it empty (#234), a cross compare's job row lists the unnamed family's samples (#236), input the UI never sends gets a 400 instead of a 500 (#283), and conditional icon tags are closed in both branches (#209). Tests: 1370 collected, 1362 passed and 8 skipped against mcrit 1.15.0, up from 968 at v1.5.0.

## [1.5.0] - 2026-09-21

Written for the README's version history, where it was published, and kept as it was.

Forty-six merged pull requests, including four security fixes, three new database migrations and the function comparison view. **Four changes affect existing deployments, and the first two need an operator decision.** *(1)* The schema gains three things on first start, through idempotent migrations that alter nothing existing: a `login_attempt` table, a `query_upload` table, and user timestamps rewritten as explicit UTC (#98). No operator action beyond restarting. *(2)* **Deployments behind a reverse proxy need one setting**: `TRUSTED_PROXY_COUNT` in `instance/config.py`, the number of proxies in front of the app. `/login` and `/register` now meter failed attempts per source address over a fifteen-minute window, so password and registration-token guessing is no longer unmetered (#101); without the setting the throttle keys on the proxy's address, so ten failed logins from anywhere refuse everyone's next login and nothing is metered per attacker. It is never per account, because a per-account lockout would let anyone deny service to any account whose name they know. The default is `0` - served directly, no request header trusted - since trusting `X-Forwarded-For` uninvited would let a caller choose their own throttle key. *(3)* Query uploads are filed under the backend-issued job id instead of a sha256 taken from the uploaded report, so **every file already in `instance/temp/uploads/` is orphaned** and can be deleted, and a query made before this upgrade cannot be promoted to a sample - its result page reports the file as no longer available. *(4)* An `.smda` report declaring a malformed sha256 is now accepted rather than refused with 400, because nothing on that path reads the field any more. **The four security fixes.** Any visitor could destroy another user's stored query: the upload filename came from a sha256 the uploader types, written with no existence check, so naming an upload after someone else's query overwrote it permanently (#169). A family name was rendered as HTML by the type-ahead, and family names are chosen by whoever submits or renames a family - the vendored `autocomplete.js` assigns its suggestions through `innerHTML`, so both the button text and the `data-label`/`data-value` attributes were live sinks; escaping now happens once, in `mcritweb/autocomplete.py`, and a test fails on any call site fed data that filter did not build (#168). Submit metadata was concatenated into the backend's query string unescaped (#170). And `/analyze/query` let a visitor choose where its upload was written (#104, superseded on the same route by #169). Alongside them, `/login` no longer reports "Incorrect username" and "Incorrect password" separately, which had confirmed whether an account exists one request at a time (#101), and API tokens come from `secrets.token_hex` rather than `md5(uuid4().bytes)` - the entropy was always UUID4's so no existing token was weak, and existing tokens stay valid, but MD5 in an auth path is a finding every auditor writes up (#100). **New capability.** The function page gains sections and a synchronised, combinable comparison of two functions, with a BinDiff-style combined graph, linked block highlighting and a reset for the two panes, which are fitted independently and so start at different scales (#34, #74); loop and cycle highlighting works there too (#69), a single function can be analyzed instead of its parent sample (#35), and the CFG view survives a function stored without an xcfg (part of #67). Unique Blocks gets a page of its own with a sample set and rule knobs (#93). A query can be promoted to a stored sample (#9). Results export as raw JSON (#75), an import says what it is doing while it does it (#66), a paginated page shows a spinner while it loads (#60), a listing remembers the sort order it was last viewed with (#58), and a job shows the MinHash setting it was submitted with (#32). **Fixes.** The explore pages stopped downloading the whole job queue and every family on every view (#77). Five server errors are gone: the job overview with a deleted sub-job, the jobs page for a category with no jobs, `/data/jobs` when the backend will not summarize the queue, any paginated page carrying a query parameter named after one of `url_for()`'s own arguments, and a link hunt for a report that has none. A valid MCRIT export refused for a shingler or minhash configuration mismatch no longer claims to be malformed JSON, which sent the reader looking at their file rather than at the two instances' configurations (#167). The sample-filtered result page paginated a different list from the one it drew. Seven `print()` calls were removed from request paths, one of them dumping a whole 1-vs-1 match report to stdout (#165). A search hit is listed once when it is also the id match (#78), a listing page finds what the search page finds (part of #56), a search that matched nothing says so (#54), a hash lookup is answered rather than apologised for (#79), column headings and row buttons stay on one line (#52), scripts no longer leak their variables onto `window` (#61), and the navbar icons are no longer blank on first paint (#62). **Packaging and docs.** `python_requires` declares `>=3.11`, which was always true and never stated - it is inherited from mcrit's own floor, so an install on 3.10 previously failed with a resolver error naming neither Python nor the version the reader needed (#166). The manual's lists render as lists on `/help` again: nine of them followed a paragraph with no blank line between, which GitHub renders and Python-Markdown does not, so they had been coming out as one paragraph of literal asterisks. Six issues that cannot be solved in MCRITweb are now recorded where the next reader will look rather than rediscovered (#37, #47, #48, #57, #59, #72). Adds 729 tests, for **968 in total**. Six of them drive a real browser through Playwright - five proving the type-ahead escaping end to end, one the function comparison view - and they skip unless Playwright is installed, which CI does not yet do; they are currently the only automated check that the `innerHTML` sink in #168 stays closed.

## Older releases

Recorded as they were written in the README at the time, newest first.

 * 2026-08-10 v1.4.8: Hardening, plus one bug older than the issue that found it. **One change affects existing deployments:** the session cookie is now `Secure`, so it is only sent over HTTPS. The reference `docker-mcrit` deployment terminates TLS in NGINX and is unaffected, and a local `flask run` is unaffected because the flag is off while `FLASK_DEBUG` is set - but an instance served over plain HTTP in production will not be able to log in until `SESSION_COOKIE_SECURE = False` is set in `instance/config.py`. Values crossing into JavaScript are now escaped for a script context (#85). That issue named eight `|safe` sites; there were fifteen, because a search for `|safe` does not find `| safe`, and two of the seven it missed were a real injection rather than hardening: family names went into a quoted JavaScript string literal on the family, sample and submit pages, so a family named with a quote in it could run script in another user's browser. Family names are chosen by whoever submits or renames a family, and are not validated here. Two new request-size ceilings, both configurable: `MAX_CONTENT_LENGTH` (1 GiB, enough for a whole-corpus import) makes an oversized body a 413 before it is buffered, where previously there was no limit at all; and `QUERY_UPLOAD_LIMITS` replaces the 1 MiB visitor cap that was hardcoded in `analyze.query`, as a `{role: bytes}` mapping with the same default (#19). Cross compare works again from the sample selection page: the sample list was built by joining two halves with a comma, so an empty half left a leading or trailing one, which `/analyze/start_cross_compare` has refused since v1.4.5 - it reported "select at least one sample" on a page where samples were plainly selected. The API passthrough's router branches are anchored, so a path merely *beginning* with a known one no longer dispatches to it, and it no longer prints the caller's username to stdout on every request. Adds 34 tests, for 239 in total.
 * 2026-08-07 v1.4.7: Lifts the Flask and Werkzeug pins that had held the project on a 2023 release (#27) - now `flask>=3.0` and `werkzeug>=3.0`, resolved against Flask 3.1.3 and Werkzeug 3.1.8. **Dependencies changed, so `pip install -r requirements.txt` has to be re-run and a container image rebuilt against this tag** - a deployment that pulls only code keeps the old Flask and is not running what this release was tested as. No application code needed changing to accommodate the upgrade. Python 3.13 and 3.14 join the CI matrix; 3.14 had been unreachable because Flask 2.2.5 calls `pkgutil.get_loader`, removed there. Two fixes to the upload paths, both older than this release: `/data/import` returned a server error for a file that was not JSON, or was JSON but not an object, and now reports it the way the completion page already did; and dropping an `.smda` report on the submit dropzone filled the base address but never family or version, because smda writes reports sorted and indented, which puts `metadata` past the 1024-byte window the browser sends - the browser now also sends a window cut from around `metadata`. Adds 14 tests over the upload paths, which nothing had exercised further than "the page renders", for 205 in total. Also points the Makefile at pytest and ruff instead of nose and pylint, and drops a deprecated positional `re.sub` count.
 * 2026-08-06 v1.4.6: **This release adds a dependency - `markdown` - so `pip install -r requirements.txt` has to be re-run, and a container image has to be rebuilt against this tag.** The app imports it at startup, so an environment that skips this will not boot. Fixes a crash that took down `/explore/search` for any query matching a sample, which had been there since the search page was written. Three more server errors fixed: editing a family or a sample with an empty form, and `/data/specific_export/<type>/<id>` for any type other than `family` or `samples`. The user manual is no longer maintained twice - `docs/manual/README.md` is the only copy and `/help` renders it, so the two can no longer drift, and the screenshots are stored once instead of twice (#91). It also now documents LinkHunt and Unique Blocks, neither of which had been described anywhere (#92).
 * 2026-08-06 v1.4.5: Bugfixes, no feature changes. **One affects results you may already have looked at:** cross compare passed the "only selected samples" checkbox to the backend as a string, and `"false"` is truthy, so an unticked box silently ran group-only matching - a different comparison from the one requested. The same bug set `force_recalculation` on nearly every job submission, which is why a repeated or double-clicked comparison queued a duplicate instead of reusing the finished job the backend already had (#97). Three crashes fixed: `/analyze/start_cross_compare` with no samples, or with a non-numeric sample list (#94); changing the role of a user who no longer exists, or to a role that does not exist (#95); and a 1-vs-1 or filtered result page whose matched functions have since been deleted, which now shows the "results are corrupted" page the cross-compare view already used instead of a server error (#96). That page's "Delete job data" button also worked again - it had pointed at a route that became POST-only in v1.4.3.
 * 2026-08-06 v1.4.4: Closes the CSRF hole (#83). Every state-changing request now needs a token bound to the session, so a page on another site can no longer act with a logged-in user's privileges - deleting samples or families, repointing the MCRIT backend, promoting an account or changing a password. **Two changes affect existing deployments:** `SECRET_KEY` no longer defaults to `'dev'` - an unset key is generated once and kept in `instance/secret_key`, which logs everyone out on the first start after upgrading; and the session cookie is now `SameSite=Lax`. Operators who set `SECRET_KEY` in `instance/config.py` are unaffected, and that remains the right answer for a multi-host deployment. Adds 38 tests and `docs/adr/0002-hand-rolled-csrf.md`, which records why the check is hand-rolled today and how it is meant to become `flask-wtf` when the Flask pin lifts (#27).
 * 2026-08-06 v1.4.3: Hardening and test foundation, no feature changes. **Four changes affect existing usage:** deleting a user, changing a role, deleting a job and the three maintenance jobs now require POST, so they can no longer be fired by anything that merely makes a browser fetch a URL; the API passthrough applies the role behind the token, refusing `pending` accounts entirely and requiring contributor to add a report; cached match diagrams need a session; and the documentation moved from `/admin/help` to `/help`. Fixed along the way: the settings page returned a server error after changing a username, password or default filter, an unconfirmed server reset raised instead of answering, and `/settings` carried an authorization decorator that never ran. Also adds an offline test suite (82 tests, no backend required) plus ruff and GitHub Actions CI.
 * 2026-08-04 v1.4.2: Adjusted for mcrit >= 1.5.3, where a matching report shares its entry objects between the full and the filtered match lists: the match diagram renderer no longer writes the negated query-report function_id back onto those shared entries, which would have corrupted the function_ids of query report tables. Also fixed a missing comma that merged the fastcluster and networkx requirements into one invalid entry.
 * 2025-12-10 v1.4.1: Can now start cross jobs where only selected samples are matched among each other (faster), minor fixes
 * 2025-12-09 v1.4.0: Customizable column setup for all tables per user, QoL improvements for cross jobs (start from SHA256 list, edit meta data in results view), minor fixes
 * 2025-08-22 v1.3.7: Bugfix for function compare page not rendering.
 * 2025-07-30 v1.3.6: Preselect sample in 1:N job, show matching score on function compare pages (if available), documentation accessible directly in mcritweb.
 * 2025-07-30 v1.3.5: Documentation now available within MCRITweb, links to search syntax besides the search fields. Function 1v1 shows match score.
 * 2025-01-21 v1.3.4: Fixed a bug in the job overview, where in-progress cross compare jobs would cause a server error (500)
 * 2024-03-19 v1.3.3: It is now possible to submit and query with SMDA reports through the WebUI.
 * 2024-03-04 v1.3.2: Added safety checks for when there are no jobs to be rendered.
 * 2024-01-26 v1.3.1: Fixed redundant queries in sample detail pages. Also minor convenience updates.
 * 2024-01-26 v1.3.0: Adaptions for the 1.3.0 milestone release. It is now possible to trigger the PicHash/MinHash and Index rebuild jobs from the Server/Admin page.
 * 2024-01-09 v1.2.22: API passthrough for results can now also use compact flag (THX: @yankovs!).
 * 2024-01-02 v1.2.21: YARA rule generation for UniqueBlocks now uses the respective data class from backend, which fixes rendering bugs.
 * 2024-01-02 v1.2.20: Extended API passthrough for queue status, fixed username annotation for calls (THX: @yankovs!).
 * 2023-12-28 v1.2.19: Enabled API passthrough for binary query matching (THX: @yankovs!).
 * 2023-12-13 v1.2.18: Fixed special case with unique blocks job for empty sample list.
 * 2023-12-12 v1.2.17: Function Diff view should now work better for obfuscated functions with lots of unique instruction tokens.
 * 2023-12-05 v1.2.16: More expressive job tables, now showing recent data on index page.
 * 2023-12-01 v1.2.13: Contributor and above can now delete jobs, jobs also filterable by state.
 * 2023-11-29 v1.2.11: Ensure user filters exist when using them the first time (THX: @rootbsd!).
 * 2023-11-20 v1.2.10: Supporting back end API token via server settings. Now also using proper ORM for all SQLite interactions.
 * 2023-10-17 v1.2.9: Fix for empty job pages (THX: @yankovs!).
 * 2023-10-17 v1.2.8: Rewrite of Job view which should now perform much better on larger collections.
 * 2023-10-03 v1.2.2: Result can now filter to min number of samples as well.
 * 2023-10-02 v1.2.0: Milestone release for Virus Bulletin 2023.
 * 2023-09-18 v1.1.7: It's now possible to actually deactivate Minhash matching in jobs.
 * 2023-09-15 v1.1.6: Quality of Life improvements in several UI elements.
 * 2023-09-08 v1.0.21: All McritClient calls are now passing on usernames/apitokens to the backend.
 * 2023-08-30 v1.0.19: Clustering functions by ICFG connectivity when doing link hunt.
 * 2023-08-25 v1.0.15: Integrated link hunt to result display.
 * 2023-06-06 v1.0.7: Extended result filters for family name, function offsets, and unique family function hits.
 * 2023-06-06 v1.0.6: Bugfix for use of new MatchingResult methods when showing 1v1 results.
 * 2023-06-02 v1.0.5: Fixed ResultView for Query results. Slight improvement to Jobs table. Adjusted API passthrough for function collections.
 * 2023-05-12 v1.0.4: Extended API passthrough for creation of matching jobs in MCRIT.
 * 2023-05-08 v1.0.3: More consistent result filter behavior.
 * 2023-04-14 v1.0.2: Started working on documentation. Fixed minor things.
 * 2023-04-10 v1.0.0: Milestone release for Botconf 2023.
 * 2023-04-10 v0.15.0: Shaping user role visitor more towards a demo account: limited visibility of menus/content, disallowed username/password change, but allowing them to upload files for query, up to size 1MB.
 * 2023-03-24 v0.14.2: API forward for adding / updating SmdaReports.
 * 2023-03-23 v0.14.1: UserInfo database object introduced and exposing apitoken in the UI.
 * 2023-03-21 v0.14.0: API forward for querying multiple function_entries by function_id.
 * 2023-03-19 v0.12.3: API forward for single SmdaFunction queries.
 * 2023-03-17 v0.12.1: Fix for special case of not rendering function graph, fix for default filters if no DB entry found.
 * 2023-03-15 v0.12.0: User now have apitokens that can be used to interact with the MCRIT instance behind mcritweb via api-passthrough (BREAKS DB -> ALTER TABLE user ADD apitoken VARCHAR).
 * 2023-03-14 v0.11.1: API calls are now shown on rendered graphs
 * 2023-03-14 v0.11.0: Users may now store a preference for default result filters (BREAKS DB -> CREATE TABLE user_filters).
 * 2023-03-13 v0.10.6: Filtering of family/sample result table is now possible.
 * 2023-02-27 v0.10.5: Now showing if function matches are unique in a family.
 * 2023-02-21 v0.10.4: More fixes and usability improvements on match result pages.
 * 2023-02-17 v0.10.2: Various usability improvements on match result pages.
 * 2023-01-15 v0.9.13: Allow filtering matching results by score, number of family matches, and exclude library matches.
 * 2022-12-15 v0.9.10: Allow setting Minhash fuzziness for candidate selection.
 * 2022-12-13 v0.9.7: Allow matching of arbitrary functions by their IDs.
 * 2022-11-18 v0.9.5: Modify and Delete functions for samples and families.
 * 2022-11-03 v0.9.1: Improved Unique Blocks Isolation and added YARA generation.
 * 2022-10-14 v0.9.0: Initial public beta release.

[Unreleased]: https://github.com/fkie-cad/mcritweb/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/fkie-cad/mcritweb/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/fkie-cad/mcritweb/compare/v1.4.8...v1.5.0
