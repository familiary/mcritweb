# MCRITweb
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/fkie-cad/mcritweb)

MCRITweb is a Flask-based user interface for the [MinHash-based Code Recognition & Investigation Toolkit (MCRIT)](https://github.com/danielplohmann/mcrit).  
MCRITweb has been developed by Daniel Enders, Manuel Blatt, and Daniel Plohmann.

## Installation

We highly recommend using the dockerized deployment available at [docker-mcrit](https://github.com/danielplohmann/docker-mcrit).

If you instead want to go for a direct installation, a few dependencies have to be installed.  
First, ensure that Python 3.12+ is available, then simply use pip to cover the requirements:
```bash
# install python and MCRIT dependencies
$ sudo apt install python3 python3-pip
$ pip install -r requirements.txt 
```

Obviously, also make sure that the backend [MCRIT](https://github.com/danielplohmann/mcrit) is fully installed, configured, and running.


## Usage

### Dockerized Usage

We highly recommend to use the fully packaged [docker-mcrit](https://github.com/danielplohmann/docker-mcrit) for trivial deployment and usage.  
First and foremost, this will ensure that you have fully compatible versions across all components.

### Standalone Usage

If you instead want to run MCRITweb as a standalone tool, the following steps will enable this:

Running flask commands requires you to set environment variables in your shell:  
`$ source ./flask_env.sh`

before the first usage, create an empty database:   
`$ flask init-db`

and then to run MCRITweb, execute:  
`$ flask run`

Note that most functionality of MCRITweb will only work if an MCRIT backend is configured and available.

### Running behind a reverse proxy

If MCRITweb is served through a reverse proxy - which the recommended [docker-mcrit](https://github.com/danielplohmann/docker-mcrit) deployment does, with NGINX in front - the app never sees a client address. Every request arrives from the proxy, so `request.remote_addr` is the proxy's address and the failed-login throttle would meter every caller in the world into a single bucket: ten failed logins from anyone would refuse the next login attempt for *everybody* until the window expired.

Tell MCRITweb how many proxies are in front of it, in `instance/config.py`:

```python
# one reverse proxy (e.g. the NGINX in docker-mcrit) between the internet and this app
TRUSTED_PROXY_COUNT = 1
```

The default is `0`, meaning the app is served directly and nothing in the request headers is trusted. Leave it at `0` unless a proxy really is in front, because `X-Forwarded-For` is written by whoever sent the request until a proxy you trust has appended to it.

The count is a number of hops **from the right-hand end of `X-Forwarded-For`**, because that is the end a trusted proxy appends to (NGINX's `$proxy_add_x_forwarded_for` adds its peer's address after whatever arrived). With `TRUSTED_PROXY_COUNT = 1` and a header reading `1.2.3.4, 5.6.7.8, 203.0.113.7`, the client address is `203.0.113.7` - the entry your proxy wrote - and the two to its left, which the client could have invented, are ignored. The proxy must be configured to append rather than replace; NGINX does this with:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Both ways of getting the number wrong are worth stating plainly:

* **Too low** (say `1` when two proxies are chained) meters the *inner* proxy instead of the client. Every request shares one bucket again, and ten failures from anywhere lock the instance out of login for the length of the window.
* **Too high** (say `2` behind a single proxy) reads past everything your proxy wrote and into what the client sent. An attacker then chooses their own throttle key: a fresh value per request evades the throttle entirely, and a chosen value spends someone else's budget for them. When the header is shorter than the configured count, MCRITweb falls back to the proxy's address - the lockout in the first bullet.

`TRUSTED_PROXY_COUNT` must be a whole number of hops between 0 and 16. Anything else - a bool (`True` is a plausible way to write "yes, I'm behind a proxy", but it does not say how many), a float, a word, a count larger than any real chain - is refused, logged, and treated as 0. It fails closed on purpose: guessing here means either a whole-instance lockout or a throttle the client gets to key.

Only `X-Forwarded-For` and `X-Forwarded-Proto` are honoured, and only when `TRUSTED_PROXY_COUNT` is set. `X-Forwarded-Host`, `-Port` and `-Prefix` are deliberately ignored: they change what the app believes its own address is, nothing here needs them, and a proxy that does not set them would leave them forgeable.

The hop count applies to `X-Forwarded-For` only. `X-Forwarded-Proto` is always read one value deep, because the two headers are written differently: a proxy *appends* to `X-Forwarded-For` (`$proxy_add_x_forwarded_for`, so it grows with the chain) but *replaces* `X-Forwarded-Proto` (`$scheme`, so it carries one value however many proxies there are). If some proxy in your chain appends to `X-Forwarded-Proto` instead of replacing it, MCRITweb reads the innermost value, which is the one you want anyway.

One rough edge, noted rather than fixed: Werkzeug parses `X-Forwarded-For` as a list header, so a client sending something unparseable - an unterminated quote, say - produces no usable address and MCRITweb falls back to the proxy's. Those attempts are then metered against the proxy. It costs the sender their own attempts and nobody else's, since a well-formed request still resolves to its own address, so the fix would be re-parsing the header in front of Werkzeug for no gain.


### Backend timeout

Every call MCRITweb makes to the MCRIT backend is bounded by `MCRIT_CLIENT_TIMEOUT`, a `(connect, read)` pair in seconds that defaults to `(10, 280)`. Without it a backend that is down or hung held a gunicorn request thread indefinitely, since gunicorn's own timeout does not reclaim a thread under the `gthread` worker. The read bound sits under the 300 s that docker-mcrit's NGINX waits, so a slow backend ends in MCRITweb's error page rather than a proxy 504. Raise both together if long synchronous calls - a whole-corpus import or export through the API passthrough - need more, or set the read to `None` in `instance/config.py` to wait indefinitely. It takes effect with an MCRIT release whose `McritClient` supports a timeout; an older one ignores it.

## Version History
 * 2026-09-21 v1.5.0: Forty-six merged pull requests, including four security fixes, three new database migrations and the function comparison view. **Four changes affect existing deployments, and the first two need an operator decision.** *(1)* The schema gains three things on first start, through idempotent migrations that alter nothing existing: a `login_attempt` table, a `query_upload` table, and user timestamps rewritten as explicit UTC (#98). No operator action beyond restarting. *(2)* **Deployments behind a reverse proxy need one setting**: `TRUSTED_PROXY_COUNT` in `instance/config.py`, the number of proxies in front of the app. `/login` and `/register` now meter failed attempts per source address over a fifteen-minute window, so password and registration-token guessing is no longer unmetered (#101); without the setting the throttle keys on the proxy's address, so ten failed logins from anywhere refuse everyone's next login and nothing is metered per attacker. It is never per account, because a per-account lockout would let anyone deny service to any account whose name they know. The default is `0` - served directly, no request header trusted - since trusting `X-Forwarded-For` uninvited would let a caller choose their own throttle key. *(3)* Query uploads are filed under the backend-issued job id instead of a sha256 taken from the uploaded report, so **every file already in `instance/temp/uploads/` is orphaned** and can be deleted, and a query made before this upgrade cannot be promoted to a sample - its result page reports the file as no longer available. *(4)* An `.smda` report declaring a malformed sha256 is now accepted rather than refused with 400, because nothing on that path reads the field any more. **The four security fixes.** Any visitor could destroy another user's stored query: the upload filename came from a sha256 the uploader types, written with no existence check, so naming an upload after someone else's query overwrote it permanently (#169). A family name was rendered as HTML by the type-ahead, and family names are chosen by whoever submits or renames a family - the vendored `autocomplete.js` assigns its suggestions through `innerHTML`, so both the button text and the `data-label`/`data-value` attributes were live sinks; escaping now happens once, in `mcritweb/autocomplete.py`, and a test fails on any call site fed data that filter did not build (#168). Submit metadata was concatenated into the backend's query string unescaped (#170). And `/analyze/query` let a visitor choose where its upload was written (#104, superseded on the same route by #169). Alongside them, `/login` no longer reports "Incorrect username" and "Incorrect password" separately, which had confirmed whether an account exists one request at a time (#101), and API tokens come from `secrets.token_hex` rather than `md5(uuid4().bytes)` - the entropy was always UUID4's so no existing token was weak, and existing tokens stay valid, but MD5 in an auth path is a finding every auditor writes up (#100). **New capability.** The function page gains sections and a synchronised, combinable comparison of two functions, with a BinDiff-style combined graph, linked block highlighting and a reset for the two panes, which are fitted independently and so start at different scales (#34, #74); loop and cycle highlighting works there too (#69), a single function can be analyzed instead of its parent sample (#35), and the CFG view survives a function stored without an xcfg (part of #67). Unique Blocks gets a page of its own with a sample set and rule knobs (#93). A query can be promoted to a stored sample (#9). Results export as raw JSON (#75), an import says what it is doing while it does it (#66), a paginated page shows a spinner while it loads (#60), a listing remembers the sort order it was last viewed with (#58), and a job shows the MinHash setting it was submitted with (#32). **Fixes.** The explore pages stopped downloading the whole job queue and every family on every view (#77). Five server errors are gone: the job overview with a deleted sub-job, the jobs page for a category with no jobs, `/data/jobs` when the backend will not summarize the queue, any paginated page carrying a query parameter named after one of `url_for()`'s own arguments, and a link hunt for a report that has none. A valid MCRIT export refused for a shingler or minhash configuration mismatch no longer claims to be malformed JSON, which sent the reader looking at their file rather than at the two instances' configurations (#167). The sample-filtered result page paginated a different list from the one it drew. Seven `print()` calls were removed from request paths, one of them dumping a whole 1-vs-1 match report to stdout (#165). A search hit is listed once when it is also the id match (#78), a listing page finds what the search page finds (part of #56), a search that matched nothing says so (#54), a hash lookup is answered rather than apologised for (#79), column headings and row buttons stay on one line (#52), scripts no longer leak their variables onto `window` (#61), and the navbar icons are no longer blank on first paint (#62). **Packaging and docs.** `python_requires` declares `>=3.11`, which was always true and never stated - it is inherited from mcrit's own floor, so an install on 3.10 previously failed with a resolver error naming neither Python nor the version the reader needed (#166). The manual's lists render as lists on `/help` again: nine of them followed a paragraph with no blank line between, which GitHub renders and Python-Markdown does not, so they had been coming out as one paragraph of literal asterisks. Six issues that cannot be solved in MCRITweb are now recorded where the next reader will look rather than rediscovered (#37, #47, #48, #57, #59, #72). Adds 729 tests, for **968 in total**. Six of them drive a real browser through Playwright - five proving the type-ahead escaping end to end, one the function comparison view - and they skip unless Playwright is installed, which CI does not yet do; they are currently the only automated check that the `innerHTML` sink in #168 stays closed.
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


## Credits & Notes

MCRITweb uses the following projects:  
* the awesome [CFGExplorer](https://github.com/hdc-arizona/cfgexplorer) library, published by the Humans, Data, and Computers Lab at CS Arizona, is used to visualize disassembly.  
* `bootstrap`, `jquery`, and `font-awesome` for its appearence. 

Pull requests welcome! :)


## License
```
    MCRITweb
    Copyright (C) 2022  Daniel Enders, Manuel Blatt, Daniel Plohmann

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
    
    Some plug-ins and libraries may have different licenses. 
    If so, a license file is provided in the plug-in's folder.
```
