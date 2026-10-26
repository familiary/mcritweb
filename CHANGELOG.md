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

- Families, samples and functions show their tags (familiary/mcrit#53) under the heading of their
  pages, each linking to the search for everything that carries it, and the statistics page lists
  the tags in use. Contributors edit them in the family and sample edit dialogs and in a tags
  dialog on the function page, and API tokens through `POST`/`DELETE` on
  `/api/<families|samples|functions>/<id>/tags` and `GET /api/tags`. Saving adds and removes only
  what changed, so a tag someone added meanwhile is kept. **Upgrading:** nothing about tags shows
  until both sides have them. The installed `mcrit` needs the tag methods on `McritClient`, which
  no release has yet, so the requirement is not raised; the backend is asked whether it stores
  tags (an older one answers 404), because a build of mcrit with tags still reports the version
  before them.
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
- Every user can delete their own account under Settings, "Delete account", by typing their password
  again; until now only an admin could, from the users page. It removes what the admin's delete
  removes - the account with its password, API token and preferences - signs it out in every
  browser and lands on the login page. A wrong password counts against the same limit as a failed
  login. The first account registered cannot delete itself, as an admin cannot delete it either: it
  is the administrator the instance always keeps, and so is an admin when no other account holds the
  role, in case that account was demoted or removed in the database. Samples, families and jobs stay on the backend.
- The password fields of the sign-in, registration and settings forms have a Show button, which shows
  what was typed and hides it again, so a password typed on a phone keyboard can be checked without
  typing it twice. A shown password is hidden again as the form is sent.
- A "Skip to main content" link is the first stop of the Tab key on every page, shown only while it
  has focus, so keyboard users no longer cross the logos and menus to reach a page's content. The
  navbar, the jobs page's category bar and every pager are named navigation landmarks, the search
  boxes are search landmarks, and the current page of a pager, the current compare picker and the
  current job category are announced as current rather than shown by colour alone.
- Find in page (Ctrl+F) finds text in the closed panels of a function's page, such as a PicBlockHash
  or the MinHash, and opens the panel it lands in, in browsers that support `hidden="until-found"`
  (Chromium and Firefox). Elsewhere the panels behave as before.

### Changed

- The project's own stylesheets (`style.css`, `theme-dark.css`, `navbar.css`, `signin.css`) are
  served without their comments. The comments stay in the files, where AGENTS.md wants the reasons
  for a rule, but a stylesheet is what a page's first paint waits for, and they were most of its
  bytes. The ETag, gzip, 304 answers and cache lifetimes are those of any other static file;
  vendored stylesheets are sent as they are.
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
- On a screen 1200px wide or more, the column headings of a list or result table stay at the top of
  the window while its rows scroll by, so the 100 or 250 rows of a page can be read without
  scrolling back up to tell the score columns apart.
- A table or cross compare matrix that scrolls sideways fades out at its right edge while there is
  more of it to the right, in Chrome and Edge; a touch scrollbar stays hidden until the table is
  touched, so nothing used to say there was more. Other browsers show no fade, as before.
  The matrix's fade follows its scroll position by a scroll-driven animation rather than a
  scroll-state container, which made every tab switch of a 100 x 100 matrix spend about 190ms more
  restyling the page.
- The help page's paragraphs and list items are at most 80 characters wide, instead of running the
  width of the window, up to about 200 characters a line on a wide screen; its screenshots, tables
  and code still use the whole width.
- The pages follow the text size set for the whole system on Android and ChromeOS, in Chrome and
  Edge: they opt in with `<meta name="text-scale" content="scale">`. The text that was sized in
  pixels and so would not have grown - the score tooltips, the cross compare matrix's labels, the
  login fields - is sized in `rem` instead, and a copy button grows with its icon instead of
  clipping it; it is at least 24px square, up from 16px.
  The tooltips take a line height of 1.4 instead of 1, so one that wraps onto several lines stays
  readable.
- On a phone, a tablet or a window under 1200px wide, a result page lays out its function matches
  only when they are scrolled near, in browsers that support `content-visibility`. With 250 rows a
  page that halves the layout and style work before the page is first shown (2.7 s to 1.0 s of
  layout on a phone-speed processor), and the page is as long as before while the table waits, to
  within half a percent, so its scrollbar does not jump when the table is laid out.
- A page is sent without the indentation and blank lines of its templates, and without the comment
  lines of its inline scripts: a result page is 1.5 KB smaller gzipped, a tenth of it, which is
  what a slow connection's first round trip, about 14.6 KB, can be the difference of for the
  page's first paint. It is drawn as before. `MCRITWEB_THIN_PAGES = False` in `instance/config.py`
  sends the page as the templates write it.
- The clipboard buttons' tooltips and the report of a copy, which every page carried in an inline
  script, are in `status_message.js`, queued after the first paint, next to the status message they
  use rather than in a file of their own, which cost a phone 40 ms of blocking time on every page. The
  page keeps only the part that copies, so a press right after the first paint still copies, and
  every page is about 190 bytes smaller gzipped.
- What a keyboard needs from a page is sent once, in the cached scripts, instead of in every copy of
  the page: the "Rows per page" select's wait for Enter is part of `post_action.js` (the select only
  says where it loads), and the cross compare matrix's arrow keys and score tooltips are
  `cross_matrix.js`. A page with a pager is about 440 bytes smaller gzipped, the cross compare
  result about as much. A size chosen in the moment before the script has loaded is applied
  once it has.
- An icon-only link or button names itself once in the markup. Its tooltip, which repeated the
  `aria-label` word for word in a `title` on each of a match table's hundred rows, is given by
  `page_loading.js` once the page has painted, so a result page is smaller by the text of those
  titles and nothing changes on screen.
- The drop-a-file overlay is fetched when a file is first dragged over the page, with Dropzone,
  instead of being carried by every signed-in page: about 1.7 KB less of each page gzipped, 1.9 KB
  of a result page, and the family type-ahead's script is no longer loaded on pages that have no
  family field of their own. A file let go before the overlay is in is now kept and put in its
  drop zone; the browser used to open it in place of the page, or drop it. The new
  `/data/drop_overlay` answers the overlay to any signed-in user but a pending one.

### Fixed

- The edit, rename and API-usage dialogs open from the keyboard. Their buttons on the family and
  sample lists, a sample's and a function's page and the cross compare result were links without an
  address, which Tab passes over, so editing or deleting a family or sample, renaming a function or
  reading its API usage took a mouse. They are buttons now.
- Links that go to another page, such as a row's export, unique blocks and Show result buttons,
  no longer tell a screen reader they are buttons, on which its user would press Space and nothing
  happen. The navbar's and the jobs page's menus open with Space as well as Enter, now that their
  toggles are buttons rather than links to "#", and an account waiting for a role has its Explore,
  Analyze and Data menus switched off for a screen reader too, which read them as working buttons.
- Controls that look disabled say so to a screen reader and are no tab stop where they lead
  nowhere. A pager's First and Previous on the first page, and Next and Last on the last, were links
  that reloaded the page; the search pager's Last page was a tab stop without a role whose reason
  was only in its tooltip; a jobs category with nothing in it, and the YARA tab of a report without
  a rule, were greyed out with nothing to say why.
- A sortable column's header is still read as the header of its column, and says whether the table
  is sorted by it and which way. It used to be a button standing in for the header, so a screen
  reader named no column for the cells below it and never said how the table was sorted. The sort
  is a link inside the header now, which also opens in a new tab with a middle click, and the Unique
  Blocks statistics, which only took a mouse click, sort from the keyboard too.
- "Rows per page" can be changed with the keyboard. A select changes its value at every press of an
  arrow key, and the page loaded at the first one, so stepping from 25 to 100 loaded the page at 50
  and put the cursor back at the top. A change made with a key now waits for Enter, or for focus to
  leave the select; one made with the mouse or a touch screen still loads at once. On the search
  page the select did nothing at all, for want of the script it calls.
- Dialogs put the cursor where it belongs and give it back. The compare and rename dialogs of a
  function's page open with the cursor in their field, which their `autofocus` asked for and
  Bootstrap never gave. Closing the jobs page's delete dialog, or one opened from a menu, puts
  focus back on the button or menu that opened it; it fell to the top of the page, where a
  keyboard user started over.
- Tab lists (Settings, Users, a cross compare's matching methods, Unique Blocks) work with the arrow
  keys: a list is one tab stop, and Left, Right, Home and End move between its tabs, past a disabled
  one. A tab is shown as the keys reach it, except a cross compare's, which builds its matrix when
  first shown and waits for Enter. A Unique Blocks link to its blocks or YARA tab no longer tells a
  screen reader the statistics tab is the one selected.
- The sample pickers of Compare 1vsN, Compare 1vs1, Cross Compare and Unique Blocks can be used with
  the keyboard and a screen reader. A sample was picked by clicking its row, which no key could do,
  and the pale tint of the row was the only sign it was picked. Each row now leads with a radio
  button (1vsN, and each side of 1vs1) or a checkbox, which a click anywhere on the row still ticks;
  a sample a cross compare or Unique Blocks already holds is ticked and disabled. In forced colours,
  where the tint is lost, a picked row is framed.
- Every row of the sample, family, function and job lists, the search results and a cross compare
  result opens its page from the keyboard: its id is a link now. A click on the row was the only
  way in, which no key can make, and a queued or running job's page had no other way in at all. A
  click elsewhere on the row still opens it.
- The table columns under Settings, and the sample order of a cross compare result, can be ordered
  with the keyboard and on a touch screen. Dragging was the only way: no key reached the items, and
  the cross compare's drag took no touch, so a keyboard user could not choose a table's columns and
  a phone could not set a custom sample order. Each item has buttons to move it up and down, and a
  column one to move it between Active and Available; a screen reader is told where it went.
  The page's script adds the buttons after the first paint, into room the stylesheet keeps, rather
  than the server writing them into every item: the settings page is sent uncompressed, and with
  the buttons in it would have gone from 42 KB to 100 KB. Dragging works as before.
- A cross compare's matrix can be read and followed with the keyboard and a screen reader. Each
  score was an empty coloured square: its number showed in a tooltip on hover alone, a pair's
  report opened from a mouse click alone, and nothing named the pair a square stood for. A score is
  now a link (or, for a pair without matches, an image) named by its percentage and match count,
  under headers naming its row's and its column's sample; the matrix is one tab stop, the arrow keys
  move between its scores and show each one's tooltip, and Enter opens a pair's report. A key above
  the tabs says which range of scores each colour stands for.
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
- Four markup slips in forms. The sample page's edit dialog had a stray quote after its form's
  `action`, the settings page gave all seven column lists the same id, and the 1 vs 1 page printed
  the sample edit dialog twice, so the second copy's labels named the first copy's fields. That
  page has no edit control, so the dialog is now printed only where a row can open it. A family's
  result page gave its two filter forms the same hidden `famid`, `samp` and `funp` ids; the hidden
  inputs on the result pages are now found by name only.
- The link hunt's link score filter survives a second Filter. Its field showed the link score only
  while a library score was set, so a link score on its own showed nothing and was dropped by the
  next submit, and a library score on its own put the word "None" in the link score field.
- Adding a file to the upload form keeps the family and version typed before it. A file that
  declares neither, which is any file but an SMDA report, emptied both fields. What a file filled
  in is still taken back by the next file, unless it was edited.
- Password managers are told which field is which. The sign-in, registration and settings forms
  mark the username and the current and new passwords with their `autocomplete` tokens, and the
  token fields with `off`, so the change-password form no longer gets the saved password put into
  "new" and "confirm", and a strong password can be offered there and on registration. The sign-in
  password field's id is now `current-password`; its name is unchanged.
- Phone keyboards no longer capitalise or "correct" what is typed into a search box, a username, a
  token or the family, version and base address of an upload, and the hash list and YARA rule are
  not spellchecked. The search boxes are search fields, with a Search key on a phone keyboard, and
  the last field of the sign-in, registration, rename and password forms has a Done key.
- Every form field has a label of its own. The result, link hunt, unique blocks and default filter
  forms named a row of fields with one caption that was tied to none of them, and told "regular"
  from "nonlib" by placeholders, which are gone once a field holds a value; each field now has a
  visible label above it, and no longer describes itself with its own value to a screen reader.
  The sign-in, registration, settings and upload fields show their labels instead of relying on
  placeholders, the search boxes have a label for screen readers, and the export field answers to
  the label it shows. The filter fields are full-size form fields, so a phone no longer zooms in on
  them.
- A refused sign-in, registration or settings change says what was wrong beside the field it
  concerns, which is marked invalid for screen readers and focused, and keeps what was typed apart
  from passwords and the server token. A first-user setup with mistyped passwords used to come back
  with the username, backend URL, operation mode and proposed registration token gone, and a refused
  rename or password change came back on another tab. The message is still flashed above the page
  too. A field the browser itself refuses is announced invalid when it is shown so. The username
  rule is written out beside the registration and rename fields instead of as a regular expression
  in a tooltip; it stays ASCII, since usernames are identifiers compared without regard to case.
  The sign-in, registration, rename and password forms say at their top that all their fields are
  required, and the first user's form that its fields are required unless they say optional.
- The filter forms' number fields take numbers: a score is a whole number from 0 to 100 and a count
  one from 0, with a digit keypad on a phone, offsets are decimal or 0x hexadecimal, and the
  excluded family and sample IDs are numbers separated by commas; the browser says so before the
  form is sent. Such a field used to take anything, and what it could not parse was dropped without
  a word; a default filter of "abc" was even stored as 0 and reported as changed. The settings page
  now refuses it, stores nothing and says why beside the field.
- The upload form checks a memory dump's base address and bitness before the file is sent, and a
  refusal from the server leaves the file and every field as they were, with the reason beside the
  Submit button, ready to send again. The page used to send the whole file, then reload to show the
  server's refusal, which dropped the file, the family and version and the chosen options. The
  Submit button is enabled as soon as the page's script is in and says "Choose a file first" when
  there is none, instead of staying disabled without a reason. Family and version are marked
  optional, and the drop overlay's button says Query or Submit after the chosen mode; it said
  Submit while the form was set to match the file against the database.
- The MinHash matching slider tells a screen reader its setting in words, "Standard" rather than
  "2", as the page shows it, and draws a focus ring when it is reached by keyboard: Bootstrap's faint
  halo was all there was.
- Radio buttons sit in named groups: the upload form's "what to do with the file", file type and
  bitness, the registration's operation mode and the function comparison's graph view, and the
  search page's "search in" checkboxes. A screen reader announced "Dumped, radio button" without the
  question it answers.
- The family type-ahead tells a screen reader what it is and what it found: the field is a combobox
  controlling the list of suggestions, and how many there are is announced as they arrive. The
  suggestion under the pointer or keyboard focus has an outline, and the typed part of each is
  bold, where colour alone told them apart and forced colours took that away.
- The tooltips of scores, matrix squares and "Total (since queued)" wrap and stay inside the
  window. They were one line long, so on a phone or tablet they ran off the screen, and the matrix
  pane cut off the top of those on its first rows at any width. Where the browser can anchor a box
  to another, a tooltip opens above or below its element, whichever side has room; a phone without
  that shows it across the foot of the window. A hidden tooltip is no longer laid out at all: it
  made the cross compare pages 1120px wide in any narrower window, and the two hidden boxes behind
  every matrix square were most of the time a large matrix took to draw, so loading one and
  switching its tabs now take about half the layout work. The rules that do this are written to be
  cheap to match, and the message box of the copy buttons is on the page from the start instead of
  being added to it once it has loaded, so a 100 by 100 cross compare matrix styles as fast as it
  did before the tooltips changed, rather than 250 ms slower.
- What the tooltips say no longer needs a mouse. The byte counts behind each score, a sample's
  details in the sample pickers and the families an aggregated function matched follow their cell
  as text a screen reader reads; "Total (since queued)", "(whole job)" and the cross compare
  order buttons take the keyboard focus, show their tooltip there, and are described by it. A
  tooltip's element is underlined with dots, Escape puts a tooltip away, and the pointer can move
  onto one without it closing.
- A file let go beside the drop zone no longer replaces the page. The overlay opens under the
  pointer on the first drag, and a file dropped a little off the zone, on the overlay or the page,
  was opened by the browser in place of the page (a `.smda` report as JSON) or downloaded. Only the
  zone takes a dragged file now; elsewhere the pointer shows that it cannot be dropped. The query,
  submit and import pages, where the zone is the page's own content, are covered the same way.
- A copy says whether it worked in a message at the foot of the window, which screen readers
  announce. The YARA rule and the registration URL answered with an alert dialog, which took the
  focus until it was dismissed, and the other copy buttons only with a "Copied!" tooltip, which a
  screen reader was not told about. On a page served over plain HTTP, where browsers withhold the
  clipboard API, the copy buttons and the registration URL now copy all the same; they threw
  there, silently. Where a copy cannot be made at all, the text is left selected for Ctrl+C. The
  registration URL is copied by a button the keyboard reaches, not by a bare icon.
- A live search that the backend fails to answer says so. The table's search box showed "No results"
  and dropped the error the page reported, because the script looked for the messages in an element
  the layout does not have; the error is now shown above the table and the screen-reader status says
  the search failed.
- Screen readers hear "Yes" or "No" for the tick and cross icons that are a table cell's whole value
  (library, MinHash, PicHash, unique), where they heard nothing or "unknown character"; the link
  hunt's family-unique matches say "(unique)" beside the bold that marks them.
- Icon buttons and links say which row they act on, for screen readers and voice control: "Filter by
  function 84", "Compare function 84 with function 943", "Delete job 12", "Show result of job 12",
  "Remove sample 3", "Copy SHA256 634df40e" and so on. A result page read out a hundred identical
  "Filter by this function" links that each went somewhere else. Likewise each table's pager is named
  after its table, "Library matches pages" with "Next page of library matches" and "Page 2 of
  library matches", where every pager on a result page was "Pagination" with the same links.
- Every data table has a name screen readers announce and list (a visually hidden caption), and the
  label column of the job, sample, function, statistics, settings and server tables is the header of
  its row, so each value is read with its label. The label cells take the bold of a header cell.
- Screen readers no longer read a control flow graph as the code of all its blocks run together. On
  a function's page the graph is hidden from them, since the code panel beside it is its text, and
  the panel can be scrolled from the keyboard in every browser; on the comparison page each graph is
  one image named after its function.
- Screen readers are told when a page is loading, a file is uploading or an import moves on: each of
  these says so in a status region that is on the page before it is written to, where the loading
  overlay and the import box used to be created or shown together with their text, which a screen
  reader often does not read. A running job's progress is also shown as a progress bar, which
  assistive technology reports as one.
- Screen readers no longer read out the match diagram's "rendering diagram..." placeholder, which
  stayed in the page after the diagram covered it, nor a "slash" between the items of the result
  pages' breadcrumb. Browsers that predate alternative text for generated content show and read them
  as before.
- Every page has one top-level heading, visually hidden where the page shows none (settings, users,
  submit, import, export, register, and search before anything is searched), and its sections and
  dialogs follow it without skipping a level. The 1vsN, 1vs1 and cross compare pages, all titled
  "Compare", and a link hunt, titled as its result page, have titles of their own.
- The navbar, the jobs page's menu and the family table's header row close the elements they open,
  and the jobs page no longer closes a paragraph it never opened; browsers had repaired each, by
  rules nobody reading the templates could see.
- Screen readers no longer read the icons as "unknown character": every Font Awesome icon is hidden
  from them, the icon-only buttons that were named only by a tooltip (analyze and rename a function,
  regenerate the API token) have names of their own, and the icons that say what a value is, the
  family and sample of a job's description and the kinds of match of a PicHash, are spelled out.
- On a phone, a tablet or a small laptop a wide table scrolls sideways in a box of its own instead of
  widening the whole page. A data table is as wide as the headings, ids, families and buttons it
  keeps on one line, 1,064px on the samples list, and below about 1,100px the page scrolled sideways
  with it, taking the heading, the filters and the pagination along. Below 1200px each data table
  now scrolls on its own, and the menus in its rows, a sample's Analyze menu and a user's actions,
  open over the edge of the box rather than being cut off by it. On the function pages the box no
  longer covers the API usage button that floats before it, which stopped the button opening its
  dialog.
- The tables that list the fields of one thing - a sample, a function, a job, the settings - break a
  long value onto the next line instead of pushing the page wider than a phone: the 64-character API
  token on the settings page, a long filename, a 37-character statistics field. The labels of those
  tables keep their words whole ("Registration:", not "Registratio" and "n:"), and the tables that
  put samples or functions side by side scroll in a box of their own below 1200px where a 320px
  window leaves them too little room.
- A cross compare matrix wider than the window can be scrolled back to its first column. Centred
  in its pane, it overflowed on both sides, and the index, hash and copy button of every row sat to
  the left of where the scrollbar could go: on a phone always, on a desktop from about 40 samples.
- Searching for a SHA-256 no longer makes the page wider than the window, at any width: the heading
  that quotes the term, the notice the search flashes and an empty table's message break the hash
  across lines, as do the other headings, a family's name among them. The rule meant to do this for
  headings was written for a `<section>` no page has.
- On a phone the control flow graph and its code, or the two graphs of a comparison, are stacked
  and each takes the width of the page; side by side at every width, they were two slivers of
  about 150px. The panes are as tall as the window with the browser's bars shown, so the bottom
  of a pane no longer hides behind them.
- A graph's "Loading" note and the combined graph's error show in a short window. They sat 400px
  down a pane that is the window's height less 106px, so on a landscape phone the pane cut them
  off; they now sit about halfway down it.

### Security

- The settings page no longer prints the API token. "Show token" fetches it, with the CSRF token,
  from a new `POST /admin/show_apitoken` that answers only the signed-in account's own token, as
  JSON, uncompressed and with `Cache-Control: no-store`; a regenerated token is still shown at once.
  With no secret left on it, the settings page is gzipped and may be cached like every other page,
  which takes it from about 42 KB on the wire to about 9 KB, and the token is taken off it when it
  is left, so Back does not bring it back from the back/forward cache.
- The server page (registration token) is sent with `Cache-Control: no-store`. Without it, Back
  after logout restored the page from the browser's back/forward cache with the token on it,
  without asking the server. Every other page keeps its caching.
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
