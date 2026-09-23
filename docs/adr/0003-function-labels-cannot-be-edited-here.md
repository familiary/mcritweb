# Function labels cannot be edited from MCRITweb

---
status: accepted — blocked upstream, re-check when mcrit exposes a write path
---

Issue #72 asks for a way to modify functions — renaming them, or editing their labels —
from the web interface. **MCRITweb cannot implement this**, and the reason is not a gap
in this repository that someone could fill. It was checked at three layers of the
installed `mcrit` **1.8.1**, which is also the newest release on PyPI (after 1.6.1,
1.6.2, 1.7.0, 1.7.1, 1.8.0), so this is not a "bump the dependency" problem either.
`mcritweb` pins `mcrit>=1.5.3`.

1. **`McritClient` has no function-mutating call.** Its entire `### Functions ###`
   section is getters: `getFunctionsBySampleId`, `getFunctions`, `getFunctionsByIds`,
   `isFunctionId`, `getFunctionById`. The mutating HTTP calls in the whole client are
   `POST /respawn`, `POST /samples`, `POST /samples/binary`, `PUT /families/{id}`,
   `DELETE /families/{id}`, `PUT /samples/{id}`, `DELETE /samples/{id}`, `DELETE /jobs*`
   and `POST /import` — plus `POST /functions`, which is `getFunctionsByIds`, a **read**
   that uses POST only because the id list travels in the body.
2. **The server exposes no route.** `mcrit/server/FunctionResource.py` defines `on_get`,
   `on_post_collection` and `on_get_collection` — no `on_put`, `on_patch` or
   `on_delete` — so a `PUT /functions/{id}` would answer 405.
3. **The business layer has no such operation.** Every function method on
   `MinHashIndex` is a read, and its own docstring listing the operations that "need to
   be jobs to ensure database consistency" names exactly `deleteSample`, `deleteFamily`,
   `modifyFamily` and `modifySample`. Functions are deliberately absent.

The near-miss is `StorageInterface.updateFunctionLabels(smda_report, username)`, which is
called only from inside `MinHashIndex.addReport` — it fires when a *whole sample* is
added, not when a person edits one function.

## Consequences

Nothing in MCRITweb should be built against a function-mutating endpoint until one
exists. In particular, do not add a form, a route or a `routePolicy` row in anticipation:
a control that cannot work is worse than an absent one, and this repository has already
learned that lesson elsewhere (see issue #65, where an empty table invited visitors to a
page that answers 403).

The ask belongs on `danielplohmann/mcrit` as a request for a function-update endpoint —
naming `FunctionResource`, `MinHashIndex` and `StorageInterface.updateFunctionLabels` as
the three places it would have to appear. When it lands, the MCRITweb half is small and
follows the shape `modifySample` already uses.

## Outcome

Not yet. Re-check `McritClient`'s `### Functions ###` section on the next dependency
bump; if it grows a mutating verb, this decision is stale.
