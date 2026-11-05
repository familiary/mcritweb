# Token API

MCRITweb's `/api` uses the `apitoken` header and the current role of the token's
owner. Browser cookies and a caller-supplied `username` header grant no authority.
Unknown or retired tokens receive 403. Pending accounts can use only their own
account and preference endpoints; corpus access still requires visitor or above.
A role change, user deletion or token rotation takes effect on the next request.

Point `McritClient` at `https://your-host/api` to use the backend operations below.
Admin maintenance uses **POST on MCRITweb**, including operations whose legacy
backend endpoint uses GET; use an HTTP client for these admin requests.

| Minimum role | Method and path (under `/api`) | Operation |
| --- | --- | --- |
| Visitor | `GET status`, `GET version` | Backend status/version |
| Visitor | `GET families`, `GET families/<id>` | Families; `with_samples=true` includes samples |
| Visitor | `GET samples`, `GET samples/<id>`, `GET samples/sha256/<sha256>` | Samples; collections accept `start`/`limit` |
| Visitor | `GET samples/<id>/functions`, `GET samples/<id>/smda` | Functions or reconstructed SMDA report |
| Visitor | `GET functions`, `GET functions/<id>` | Functions; `with_xcfg=true` includes the graph |
| Visitor | `POST samples/ids`, `POST families/ids`, `POST functions` | Comma-separated IDs in the raw body; samples may have negative query IDs |
| Visitor | `GET search/families`, `GET search/samples`, `GET search/functions` | `query`, `cursor`, `sort_by`, `is_ascending`, `limit` |
| Visitor | `GET jobs`, `GET jobs/stats`, `GET jobs/count` | Queue and statistics; queue selectors include `sample_ids`/`job_ids` |
| Visitor | `GET jobs/<id>`, `GET jobs/<id>/result`, `GET results/<id>`, `GET results/<id>/job` | Jobs/results; job results accept `compact` |
| Visitor | `GET matches/sample/<id>`, `GET matches/sample/<id>/<other_id>` | 1vsN / 1vs1 matching |
| Visitor | `GET matches/sample/cross/<ids>` | Cross-compare; `sample_group_only` selects within the group |
| Visitor | `GET matches/function/<id>/<other_id>` | Compare functions |
| Visitor | `GET uniqueblocks/family/<id>`, `GET uniqueblocks/samples/<ids>` | Unique blocks; optional `covers_required` and `min_instructions` |
| Visitor | `POST query`, `POST query/function` | SMDA JSON report / single-function query |
| Visitor | `POST query/binary`, `POST query/binary/mapped/<decimal_base_address>` | Query bytes without adding them to the corpus |
| Visitor | `GET query/pichash/<16-hex>`, `GET query/picblockhash/<16-hex>` | Hash matches; append `/summary` for summaries |
| Contributor | `POST samples` | Add an SMDA JSON report to the corpus |
| Contributor | `POST samples/binary` | Submit raw binary bytes |
| Contributor | `PUT samples/<id>`, `PUT families/<id>`, `PUT functions/<id>` | Edit metadata / rename a function |
| Contributor | `DELETE samples/<id>`, `DELETE families/<id>`, `DELETE jobs/<24-hex-id>` | Delete an entry or job; family deletion accepts `keep_samples` |
| Contributor | `POST import`, `GET export`, `GET export/<ids>` | Import a JSON export / export the corpus or a selection; `compress=false` disables compression |
| Admin | `POST rebuild_index`, `POST rebuild_picblockhash_index` | Rebuild indexes |
| Admin | `POST recalculate_pichashes`, `POST recalculate_minhashes`, `POST repair_minhashes`, `POST recompute_family_stats` | Corpus maintenance |
| Admin | `POST respawn` | Reset the backend and clear local cached state; requires `{"reset_server":"RESET"}` |
| Admin | `GET admin/users` | List users, in multi-user mode |
| Admin | `PUT admin/users/<id>/role` | Change role with `{"role":"visitor"}` (or pending/contributor/admin) |
| Admin | `DELETE admin/users/<id>` | Delete an account and its preferences; idempotent 204 |
| Admin | `POST admin/users/regenerate_legacy_apitokens` | Rotate pre-1.5.0 tokens; returns a count |
| Admin | `GET admin/server`, `PUT admin/server` | Read/update the local backend connection settings |

Admin includes contributor and visitor permissions. Contributor includes visitor
permissions. Import/export have the same contributor requirement as the web UI.
The root account (user 1) cannot be deleted or have its role changed.

PUT requests accept a JSON object or form fields. Sample edits accept `family_name`,
`version`, `component`, `is_library`; family edits accept `family_name`, `is_library`, `actors` (up to 64 short names);
function edits accept `function_name` (up to 256 printable ASCII characters, empty
clears it). Omitted fields stay unchanged, empty strings and `false` are preserved,
and unknown fields or malformed types receive 400. Account, preference, metadata
edit and hash-selection bodies are capped at 64 KiB before parsing. Metadata content restrictions
are also checked by the backend, as for edits from the UI.

Corpus SMDA submissions also require a 64-digit hexadecimal SHA-256, normalized
to lowercase, as the upload form does.

Binary submission accepts `filename`, `family`, `version`, `is_dump`, `base_addr`
(hexadecimal) and `bitness` (32 or 64) as query parameters. Dumps require both an
address fitting in 64 bits and a bitness. Metadata is encoded before the backend
client builds its URL. Empty binaries receive 400. Query uploads use the same
`QUERY_UPLOAD_LIMITS` per role as the UI, including `POST query`.

Sample matching and query requests accept `minhash_threshold`, `pichash_size`,
`band_matches_required`, `force_recalculation`, and, when the installed client
supports them, `band_df_cutoff`, `shortlist_size` and `preset`. The Python client
wire name `minhash_score` is also accepted for `minhash_threshold`; conflicting
values receive 400. Cross-compare and 1vs1 do not accept a shortlist. Single-function
SMDA queries also accept `exclude_self_matches`. Boolean inputs are `true`, `false`,
`1`, `0` (case-insensitive). Invalid booleans/integers receive 400 rather than silently
changing the request. Unique-block sample sets are sorted and deduplicated and
capped at the UI's 250 distinct IDs before backend lookups. Missing samples and empty
families cannot queue a unique-block job. Backend errors during validation are
returned without removing IDs or submitting a partial set.

Server updates accept only `mcrit_server_url` and `mcrit_server_token`; omitting a
field preserves it and an empty token explicitly clears it. URLs must be HTTP(S)
without credentials, query or fragment. Local administration works while the backend
is unreachable. User lists contain no password hashes or API tokens, and server
responses contain no server or registration token. Bulk rotation never returns the
new tokens; users retrieve their own tokens through settings or password authentication.

Backend JSON envelopes, status codes and bytes are preserved, including errors and
202 job submissions. Unrelated backend headers/cookies are not forwarded. A failed
connection receives 502 and a timeout 504, without a browser login redirect or flash
session. Every API response is `Cache-Control: no-store`. Unsupported methods on a
known path receive 405 with `Allow`; unknown operations receive 501. A method missing
from the installed Python client also receives 501 (for example, reconstructed SMDA
reports need a client that provides `getSmdaReportForSample`).

```sh
curl -H "apitoken: $MCRITWEB_API_TOKEN" \
  'https://your-host/api/uniqueblocks/samples/1,2'

curl -X PUT -H "apitoken: $MCRITWEB_API_TOKEN" -H 'Content-Type: application/json' \
  --data '{"version":"2.0","is_library":false}' 'https://your-host/api/samples/1'

curl -X POST -H "apitoken: $MCRITWEB_API_TOKEN" \
  'https://your-host/api/rebuild_index'
```

## Accounts and first-user setup

`POST /api/auth/token` accepts `username` and `password` as JSON or form fields. It
returns the account's API token without creating a browser session. Unknown names
and wrong passwords have the same 401 response and share the UI login throttle;
a spent budget receives 429. Password proof can replace a legacy token when a
deployment has retired it. Credentials are bounded to a 64 KiB declared body and
4096 characters per field, before password hashing.

`POST /api/auth/register` accepts `username`, `password`, `password_confirm` and,
when required, `registration_token`. Normal registrations and duplicate names
receive the same generic 202 message. New accounts are pending and cannot read or
change the corpus until approved. Single-user mode blocks further registrations.

For the first account only, registration also requires `mcrit_server_url` and
`operation_mode` (`single` or `multi`), and optionally accepts `mcrit_server_token`
and `registration_token_new`. A write lock and a fresh empty-table check make the
account and connection setup atomic: two concurrent requests cannot create two
initial administrators. No subsequent registration may change those settings.

| Signed-in account operation | Input / output |
| --- | --- |
| `GET me` | Own identity and role, without credentials |
| `PUT me/username` | `username`, `password` (current password required) |
| `PUT me/password` | `current_password`, `password`, `password_confirm`; returns the replacement API token |
| `POST me/apitoken` | Replace own token and return the new one |
| `DELETE auth/token` | Revoke the presented token (API logout), returning 204 |
| `GET me/filters`, `PUT me/filters` | Read/update the UI's saved result filters; omitted values are preserved |
| `GET me/columns`, `PUT me/columns`, `DELETE me/columns` | Read/update/reset table columns; PUT takes `{"column_settings":{"functions_table":{"active":["function_name","function_id"]}}}` |
| `GET capabilities` | Operations available to the caller's role, including availability in the installed client |

The account ID always comes from the token. Request fields cannot select another
user, change a role or read somebody else's token. Credential changes require the
current password and use the settings forms' shared verification and throttle.
Changing a password revokes the old token and existing browser sessions; obtain
the replacement token from the response. API requests never authenticate through
or refresh a browser cookie. API logout revokes its bearer credential rather than
changing a cookie session. Preferences use the same database models, clamping and
column-order normalization as the UI; unknown columns and duplicate entries are
refused.

## Analysis workflows and downloads

These operations return data or downloadable artifacts instead of HTML. The filter,
YARA, LinkHunt, graph and cross-ordering algorithms are shared with the UI.

| Minimum role | Operation | Details |
| --- | --- | --- |
| Visitor | `GET jobs/<id>/filtered` | The UI's matching filters, with `famid`, `samid` or `funid` scope; `funp`/`funl` and `samp`/`sampl` page the output |
| Visitor | `GET jobs/<id>/linkhunt` | LinkHunt links, clusters and filters; accepts the same `filter_*`, `clup`/`clul`, `funp`/`funl` controls as the UI |
| Visitor | `GET jobs/<id>/yara` | Generated rule, cover, statistics and the UI's YARA parameters |
| Visitor | `GET jobs/<id>/cross` | Matrix data and sample metadata ordered by `order=clustered|sample_id|family` or a validated `custom` list |
| Visitor | `GET jobs/<id>/diagram` | PNG match diagram, optionally scoped by `famid`, `samid`, `funid` |
| Visitor | `GET jobs/<id>/download` | Download the original result JSON as an attachment |
| Visitor | `GET jobs/<id>/status` | The same progress snapshot as the overview's polling endpoint |
| Visitor | `POST functions/<id>/analyze` or `GET matches/function/<id>` | Queue the parent sample's comparison, as the function page does; read its result with `funid=<id>` |
| Visitor | `GET functions/<id>/graph` | DOT graph as plain text |
| Visitor | `GET functions/<id>/diff/<other_id>` | Block colours, correspondences and pairs; `format=dot` returns the combined graph |
| Visitor | `POST graphs/loops` | DOT body to the UI's loop detector; the same byte/graph caps and skipped-analysis header apply |
| Visitor | `POST selections/samples/by-sha256` | `{"sha256":["...", "..."]}` or newline text in that field; returns selected IDs, missing hashes and invalid lines, capped at 250 |
| Visitor | `GET samples/<id>/binary` | Raw binary attachment, only if the backend is configured to retain and serve binaries |
| Contributor | `POST jobs/<id>/promote` | Optional `family`/`version`; promote the verified local input of a query without another upload |
| Contributor | `GET exports/family/<id>`, `GET exports/sample/<id>` | The UI's selected export action |
| Contributor | `POST uploads/metadata` | The upload form's `filename`, `file_header`, optional `file_metadata` probe, bounded to 1 MiB |
| Contributor | `POST jobs/delete-selection` | Delete a snapshot selected by `state`, `method` or `job_ids`; at most 10000 jobs per request |
| Contributor | `DELETE jobs` | Backend queue cleanup by `method`, `created_before`, `finished_before`; an unfiltered deletion requires `confirm=DELETE` |

With no explicit matching filters, `/filtered` uses the token owner's saved defaults.
Explicit filter parameters describe that request instead; a submitted `false` is not
replaced by a saved `true`. The `filter_button_action=clear` action clears filters as
in the UI. Local workflow failures return JSON with the relevant HTTP status,
including 409 for an unfinished/incompatible result, rather than an HTML redirect.

API query submissions retain their input under a validated backend job ID on this
host, just as web uploads do. Promotion recomputes its payload digest and compares it
to the job descriptor before writing to the corpus. A changed input, unreadable
report, invalid mapping, or unavailable local file cannot be promoted. Already-stored
samples are returned without resubmission. Retention is best-effort after a successful
backend submission; a disk failure does not hide a job that has already been queued.
A query submitted through another host or tool can only be promoted if its verified
input is present on this host. Re-uploading to `samples` or `samples/binary` remains
available when it is not.

## Additional backend operations

All public MCRIT client operations with a server-side effect have API counterparts.
Client-only helpers are represented by their underlying operations: `is*Id` uses a
GET lookup, `getSamplesByFamilyId` uses a family with samples, `getJobCount` uses the
queue listing, and `awaitResult` polls job/result endpoints. `setApitoken` and
`setUsername` configure a local client; the proxy never lets them override the
identity established by its token.

Additional admin POST actions are `complete_minhashes`,
`rebuild_function_range_index`, `rebuild_band_df_index`, `band_df_cutoff_coverage`
(optional `band_df_cutoff`), and `delete_orphaned_queue_files` (`dry_run=true` for a
count without deletion). Maintenance is POST-only at MCRITweb even where the backend
uses legacy GET routes. Queue cleanup dates must be `YYYY-MM-DD` or
`YYYY-MM-DDTHH:MM:SS` in the backend's UTC convention; invalid dates never silently
remove a deletion selector.

Version-dependent operations are registered explicitly and return 501 when the
installed client does not support them, rather than discarding options:

- Tag-capable clients: `GET tags?entity=family|sample|function`; contributor-only
  `POST`/`DELETE <families|samples|functions>/<id>/tags` with `{"tags":["reviewed"]}`.
  Tags are normalized, validated and capped at 100 per request.
- Extended unique-block clients: `gentle_length_score`, `max_blocks_per_function`,
  `demote_generic_blocks`, and POST on either unique-block route with
  `{"exclude_picblockhashes":[12,"0xff"]}`. Negative sets are bounded to 100000
  unsigned 64-bit hashes, an 8 MiB body limit and the role's upload cap.
- Rerun-capable clients: `POST jobs/<id>/rerun`. Only finished, failed or terminated
  analyses may be rerun. A role with a query upload cap can rerun only its own
  queries, so the endpoint cannot bypass that cap using another account's input.

`ROUTES` owns dispatch and permission policy; its role keyword is mandatory.
`api_coverage.UI_OPERATIONS` maps every manual UI route to concrete API operations.
Tests compare that inventory against Flask's complete UI route map and compare all
public `McritClient` methods against routed operations or client-only explanations.
Adding a UI action or backend method without addressing API coverage fails those
checks. Tags from open PR #338 and reruns from #133 already have optional API routes;
those UI PRs do not have to recreate a separate dispatch chain.

## Validation revisions

The implementation was checked against the fetched MCRITweb `origin/master`
(`8fd5a8cb983b2bffe143c2dc2d7960cf7635f1bd`) and MCRIT `origin/main`
(`37d69c42fb94b508f54d4f2db022942a0f46a2e5`). Compatibility checks also use
the minimum supported client, MCRIT 1.12.0, and the combined open-PR integration
head from MCRIT #308 (`664c3ecf068b3b355c02570801528ff8a1549ddc`). The latter
is an additional compatibility target, not a dependency on unmerged changes.

Live checks use a real Falcon backend behind Waitress with isolated in-memory
storage and a local queue, not a production MongoDB corpus. This change also fixes
MCRIT's LocalQueue statistics refresh signature, so `/api/jobs/stats` works on that
backend; a regression test exercises the same queue caller contract as the server.
