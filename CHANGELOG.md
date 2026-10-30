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
- The sample page shows where a sample's sha256 comes from, in its SHA256 row (familiary/mcrit#270).
  A sample MCRIT disassembled from an uploaded binary is marked "verified against the uploaded
  binary"; one indexed from a submitted report or an import carries a note that its sha256 was taken
  from there and not checked against a binary. A sample stored before mcrit recorded this shows
  nothing. Neither does any sample while the `mcrit` package installed with MCRITweb predates the
  field, since MCRITweb reads samples through its `SampleEntry`: nothing breaks, the marker appears
  once the package is upgraded.
- A finished or failed job's page has a **Rerun job** button, which queues the same request again
  with a forced recalculation, and a link back to the form it was submitted from with its samples
  and matching parameters filled in (#55). With an MCRIT whose client has `requestJobRerun`
  (familiary/mcrit#311) the backend reruns the job itself from what it stored, so the button covers
  every analysis, queries, unique blocks and 1-vs-group comparisons included, and terminated jobs
  too. A role with a query upload cap - by default a visitor - reruns only its own queries, since a
  stored query may be larger than that role could have uploaded. With an older one it is offered on
  the 1vsN, 1vs1 and cross compare jobs it can rebuild from their parameters.
- Every user can delete their own account under Settings, "Delete account", by typing their password
  again; until now only an admin could, from the users page. It removes what the admin's delete
  removes - the account with its password, API token and preferences - signs it out in every
  browser and lands on the login page. A wrong password counts against the same limit as a failed
  login. The first account registered cannot delete itself, as an admin cannot delete it either: it
  is the administrator the instance always keeps, and so is an admin when no other account holds the
  role, in case that account was demoted or removed in the database. Samples, families and jobs stay on the backend.

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
- The function page no longer freezes for seconds on a large function (#267). Above 250 basic blocks
  it shows the code at once and lays the control flow graph out only when asked to; dagre's layout
  had frozen the page for about 19 s on a 971-block function, now the page responds in 0.2 s.
- Control flow graphs are laid out about three times faster on large functions, with the same
  result: 5.6 s instead of 21.7 s for a 971-block function. A patch to the vendored dagre builds the
  graph of each rank in one pass instead of walking the whole graph once per rank.
- The browser keeps the static files from one page to the next instead of asking for each of them
  again. Their URLs carry a digest of the file (`?v=`), which changes when the file does, and a URL
  with the current digest may be kept for a year; on a phone at 150 ms round trips the next page
  paints about 100 ms sooner. The files a stylesheet or the CFG layout worker names themselves (the
  icon fonts, the CFG viewer's images and its dagre bundle) are kept for an hour. Match diagrams
  and the manual's screenshots are still revalidated on every view. A compressible file sent
  without gzip now says `Vary: Accept-Encoding` as well, so that a shared cache keeping it does not
  hand that copy to clients that take gzip.
- The help page asks for its first screenshot, its largest paint, at high priority.
- Phones and tablets no longer download the Fraunhofer logo, which the navbar shows only from 992px
  up.
- The help page and the query, submit, import and export forms preload the icon font only from
  992px up. Below that they draw no icon until the menu is opened, and their first paint on a phone
  comes about 50 ms sooner.
- A running job's overview stops polling its status, and its elapsed time stops counting, while
  its tab is hidden; it polls at once when the tab is shown again. A long job watched from a
  background tab cost the backend a status read every interval for as long as the tab was open.
- A function page shows its code without waiting for the match lookup of every block: each block's
  "Matches" line says "looking up..." until its answer arrives.
- A cross compare result no longer waits for jQuery UI's 36 KB theme stylesheet before its first
  paint; the one rule of it the sample list uses is in `style.css`.
- A page is sent without the indentation and blank lines of its templates, and without the comment
  lines of its inline scripts: a result page is 1.5 KB smaller gzipped, a tenth of it, which is
  what a slow connection's first round trip, about 14.6 KB, can be the difference of for the
  page's first paint. It is drawn as before. `MCRITWEB_THIN_PAGES = False` in `instance/config.py`
  sends the page as the templates write it.
- The drop-a-file overlay is fetched when a file is first dragged over the page, with Dropzone,
  instead of being carried by every signed-in page: about 1.7 KB less of each page gzipped, 1.9 KB
  of a result page, and the family type-ahead's script is no longer loaded on pages that have no
  family field of their own. A file let go before the overlay is in is now kept and put in its
  drop zone; the browser used to open it in place of the page, or drop it. The new
  `/data/drop_overlay` answers the overlay to any signed-in user but a pending one.
- The project's own stylesheets (`style.css`, `theme-dark.css`, `navbar.css`, `signin.css`) are
  served without their comments. The comments stay in the files, where AGENTS.md wants the reasons
  for a rule, but a stylesheet is what a page's first paint waits for, and they were most of its
  bytes. The ETag, gzip, 304 answers and cache lifetimes are those of any other static file;
  vendored stylesheets are sent as they are.

### Fixed

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
- Submitting the binary of a sample MCRIT has not verified against a binary - one indexed from a
  submitted report or an import, or stored before mcrit recorded this - sends it to MCRIT instead of
  stopping at "Sample was already in database", and so does promoting a binary query of such a
  sample (familiary/mcrit#270, #306). MCRIT then replaces the sample's stored analysis with its own
  disassembly of the file under the same sample id, and you are taken to the job. A family or
  version left blank is not sent, so the sample keeps its own. An mcrit without #306 refuses the
  upload, and MCRITweb then says the sample was already in the database and opens it, as before; a
  verified sample, and an .smda report of a known sha256, are handled as before without asking
  MCRIT.

### Security

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
- Every response's `Content-Security-Policy` now says what a page may load and send, not only who
  may frame it: no plugin (`object-src 'none'`), no `<base>` (`base-uri 'none'`), forms posted only
  back to MCRITweb (`form-action 'self'`), fetches, fonts, images, workers and manifests from
  MCRITweb's own origin only, and no audio or video. Everything MCRITweb loads already comes from
  its own origin, so no page changes; markup injected past autoescaping can no longer load an
  object, re-point the page's relative links or post a form to another site. A reverse proxy that
  adds a policy of its own should stop doing so: browsers enforce both.
- Pages run only the scripts MCRITweb wrote. The `Content-Security-Policy` now runs a script element
  only when it carries the nonce drawn for that response, which every page writes on its own scripts
  and injected markup cannot know, plus what those scripts load themselves (`'strict-dynamic'`):
  the queued page scripts, the graph viewer and its worker, Dropzone. A `<script>` that slipped past
  autoescaping, as family names once could before #85, no longer runs, and neither does `eval`.
  Handler attributes such as `onclick` still run for now (`script-src-attr 'unsafe-inline'`), until
  they become listeners. Every page was walked in Chromium as each role and signed out under the
  new policy, with uploads, live search, the graphs, the comparison view, tabs, dialogs, sorting,
  the copy buttons and a running job's polling exercised, without a violation.
- Every response refuses the camera, the microphone, the location, payments and USB
  (`Permissions-Policy`), none of which MCRITweb uses, so script injected into a page cannot ask
  for them. Served over HTTPS, responses also carry `Strict-Transport-Security: max-age=31536000;
  includeSubDomains`, so a browser that has reached the instance once is not talked into plain HTTP
  again, and `Cross-Origin-Opener-Policy: same-origin-allow-popups`, so a site that opens MCRITweb
  in a window gets no handle on it. Neither is sent over plain HTTP. **Upgrading:** behind a proxy
  that terminates TLS, MCRITweb knows a request was secure only with `TRUSTED_PROXY_COUNT` set.
  docker-mcrit's TLS site already adds the same HSTS value; a browser acts on the first such header,
  MCRITweb's, so change it with `STRICT_TRANSPORT_SECURITY` in `instance/config.py` rather than in
  the proxy, or set that to `None` to leave the header to the proxy. See "HTTPS" in `README.md`.
- **The session cookie is now `__Host-session`, and everyone has to log in once more after
  upgrading**, as the session generation change above asks of them anyway. A browser takes a cookie
  of that name only from the instance's own host, over HTTPS, so a sibling subdomain, or someone in
  the middle of a plain-HTTP request before the user signs in, can no longer slip them a validly
  signed session of the attacker's own account, or log them out. **Upgrading:** an instance whose
  cookie cannot carry the prefix keeps the name `session`: one served over plain HTTP with
  `SESSION_COOKIE_SECURE = False`, one sharing the cookie across subdomains through
  `SESSION_COOKIE_DOMAIN`, one under a `SESSION_COOKIE_PATH` or `APPLICATION_ROOT` other than `/`,
  and one that sets `SESSION_COOKIE_NAME` itself.
- Every value a page writes into a script now goes through `|tojson`, and the lint that already
  refused `|safe` there refuses a bare one too. The admin server page's copied registration link
  keeps a token with a quote or a trailing backslash intact instead of breaking the page's script, a
  family name with `?`, `#` or a space links to that family on Malpedia rather than to a query of
  its own, and a cross compare cell carries its link as data instead of a job id inside an
  `onclick` script.
- The admin server page no longer writes the backend server token into its form, where it sat in
  clear in the page source, the browser's cache and on screen. The field starts empty and the page
  says whether a token is set: an empty field keeps the stored token, a typed one replaces it, and
  the new "Remove Token" button removes it, which an empty field used to do.
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
  v1.6.0 marked end too. **BREAKS DB -> the `user` table gains a `session_generation` column**,
  added on first start by an idempotent migration that starts every account at 0.
- The settings page no longer prints the API token. "Show token" fetches it, with the CSRF token,
  from a new `POST /admin/show_apitoken` that answers only the signed-in account's own token, as
  JSON, uncompressed and with `Cache-Control: no-store`; a regenerated token is still shown at once.
  With no secret left on it, the settings page is gzipped and may be cached like every other page,
  which takes it from about 42 KB on the wire to about 9 KB, and the token is taken off it when it
  is left, so Back does not bring it back from the back/forward cache.
- The server page (registration token) is sent with `Cache-Control: no-store`. Without it, Back
  after logout restored the page from the browser's back/forward cache with the token on it,
  without asking the server. Every other page keeps its caching.

## [1.7.0] - 2026-10-29

Written for the README's version history, where it was published, and kept as it was.

Forty-eight merged pull requests: ten security fixes, a reworked jobs page and cross compare, and a UI pass for consistency, small screens and accessibility. **Three changes affect existing deployments.** *(1)* **Backups of `instance/` change:** the SQLite database runs in WAL mode from the first start after upgrading (#262), so `mcritweb.sqlite-wal` and `mcritweb.sqlite-shm` sit next to `mcritweb.sqlite`, and copying `mcritweb.sqlite` alone while MCRITweb runs can miss the latest changes; see "SQLite database" above. A read-only database file still starts (#322). *(2)* **The result cache is bounded** (#229): `RESULT_CACHE_MAX_BYTES` (1 GiB) and `RESULT_CACHE_MAX_FILES` (1000), and the oldest-written reports are evicted first, where before it grew without limit. Raise both in `instance/config.py` for an instance that relies on many large cached reports. The diagrams cache is still unbounded. *(3)* **Pre-1.5.0 API tokens can now be retired** (#254): users holding a 32-character token see a warning on `/settings`, the admin users page counts and marks those accounts and can regenerate them all at once, and `ACCEPT_LEGACY_APITOKENS = False` in `instance/config.py` then answers such a token with a 403 that says why. The default stays `True` for this release, so nothing changes until an operator chooses the moment; see "API tokens issued before 1.5.0" above. **Security fixes.** A crafted link could add event handlers to a result page's filter inputs, which wrote URL values into unquoted attributes; every such attribute is quoted (#325). A password change accepted an empty password, which then signed in with an empty password field; it is refused, and a sign-in with an empty password fails whatever the account holds (#326). Wrong current passwords on the settings forms count against the login throttle like failed logins (#329). The loop analysis behind the function pages was cubic and read any body it was sent, so one request from a logged-in visitor could hold a worker for minutes; it is close to linear, reads at most `FIND_LOOPS_MAX_BYTES` (32 MiB) and stops at 8000 blocks, and the page says when it skipped a function (#270, #282). Job ids from the URL are checked before they reach the backend client, on every job route including the status endpoint (#279), and the sha256 an SMDA report declares is checked before it goes into a backend path (#271). Query values stay inside the URL strings of the pagination handlers (#275). A cross compare's hash list is bounded and reports what it ignored (#280), as is an order that repeats a sample, which had let one id rendered 200 times make a 12.5 MB page (#159). A match diagram is rendered only for a family or sample the report matched, which closed a way to fill the diagrams cache with one image per family and sample in the corpus (#145). A throttled login logs at most 32 characters of the username (#281). jQuery UI goes to 1.13.3 for CVE-2022-31160, which no MCRITweb page reached (#261). **Fixes worth knowing.** An edit dialog kept "Delete" ticked into the next row's dialog, so a plain rename could delete a family, with its samples, or a sample; every dialog now opens disarmed (#327). Column settings Save and Reset answered 400 and work again, and deleting a user from the Admins tab stays on it (#253). The generated `instance/secret_key` is written atomically, also on filesystems without hard links, so two workers starting without it can no longer end up with different keys and refuse each other's logins (#263, #322). A failed backend call on a page that needs its answer shows a 502 or 503 page, or a 404 naming what is missing, instead of "not found" (#130). Upper-case job ids no longer miss the result cache (#322). Dropped dumps are recognised by name more reliably: a de-dump marker always means not a dump, `_0X` addresses in upper case are read, and only 32 or 64 bit is accepted (#141). **Jobs and results.** The jobs page names its tab in the URL, its search box works, it orders by job number over the whole queue and has a tab for the per-sample jobs of a cross compare (#132, #115); a running job's page polls its status and reloads only when it changed (#214); every job is named the same way everywhere, shows who requested it (#122, #172), and a Unique Blocks job folds its samples into ranges that open into the full list (#303). Cross compare shows its matrix in three orders and renders only the visible tab (#159, #213, #226), the cross-compare picker lets a pre-selected sample be clicked off again (#305), and only cells with matches link (#305). Result pages draw the match diagram on its own request (#145), round scores instead of truncating them (#148) and recompute the method statistics for the matches on screen (#142). The YARA copy button copies what the box holds (#147). **Search and editing.** The sample, family and per-family/per-sample searches filter as you type, and `0x<offset>` on a sample page finds that function (#293); matched terms are marked in the results (#136), and searches go through one adapter (#174). Contributors rename a function from its page or through `PUT /api/functions/<id>` (#173). Sample and family edits are confirmed by reading them back rather than by waiting (#219). The server page schedules mcrit's three corpus repairs, behind a confirmation (#212), and the Statistics page shows the MCRITweb, backend, client library and SMDA versions in labelled sections (#301). **Layout and accessibility.** One layout across the pages, with shared breadcrumbs on the comparison pages and no fixed 900 px minimum width (#253); the logos are smaller, and logged-out and pending users no longer get the upload overlay. Long job descriptions wrap (#117), every empty table says what is missing (#119), headings, labels, contrast and link targets pass Lighthouse's accessibility and SEO audits (#244, #302). Also #297. Tests: 2447 collected, 2428 passed and 19 skipped against mcrit 1.16.0, up from 1370 at v1.6.0.

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
