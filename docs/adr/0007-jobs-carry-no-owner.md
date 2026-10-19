# Jobs carry no owner, so MCRITweb cannot annotate activity with one

---
status: accepted — blocked upstream, and there is a design question to settle first
---

Issue #37 proposes annotating objects such as jobs with user uuids, for accountability,
convenience and personalization. **MCRITweb cannot do this**, because the storage that
would hold the annotation is the backend's job document and this application has no write
path into it.

What already flows: MCRITweb sends a username on every backend call —
`views/client.py::default_client_factory` passes `username=get_username()` into
`McritClient`, which puts it in `self.headers`. The backend uses it for **logging only**:
`mcrit/server/utils.py:10-13` reads the header and calls
`index._storage.dbLogEvent(message, username=username)` → `MongoDbStorage._dbLog`, which
writes to a `logs` collection with a `username` index. The only *domain object* that
persists a username is `FunctionLabelEntry`.

Jobs carry no owner field at all. `mcrit/queue/LocalQueue.py::Job` exposes `locked_by`
(the worker), `created_at`, `payload` and `all_dependencies` — nothing about who asked.
The captured job fixtures confirm it: `cross_compare.job.json` has no user key of any
kind.

MCRITweb also has no user uuid to send. `sql/create_table_user.sql` is `id INTEGER
PRIMARY KEY AUTOINCREMENT, username, password, role, registered, last_login, apitoken`.
The only uuids in the codebase are `server_uuid` (per-instance, `authentication.py:92`)
and the md5-of-uuid4 used to mint `apitoken`.

## Consequences

**Do not add a `uuid` column to the user table in anticipation.** It would be dead schema:
per `AGENTS.md` a column change means touching `sql/`, `db.py`, `db.migrate()` and the
README version history — four files, one of them among the most contended in the repo —
for no user-visible effect, and `db.py` would then carry a field nothing reads.

**`username` is already on the wire**, so the tempting shortcut is to persist that and be
done with no schema change on this side. It does not work, and the reason is in this
repository rather than upstream, so it is a settled requirement rather than an open
question:

- **Usernames are renameable.** `administration.change_username` lets any user rename
  themselves with their own password - no admin involved. Every job created before the
  rename would keep the old name, so one user's history splits in two with nothing tying
  the halves together.
- **Usernames are reusable.** `administration.delete_user` does
  `DELETE FROM user WHERE id = ?`, which frees the name; the next registration can take
  it. Old jobs then read as belonging to whoever holds the name now.

So an owner field has to key on something stable, and MCRITweb does not currently have
one to send: `sql/create_table_user.sql` gives an autoincrement `id`, which is stable but
local to one MCRITweb instance and meaningless to a backend that several could share.
That is what makes the uuid the right shape - not a preference, and not something to ask
the maintainers about.

The order still matters, though. The uuid is only worth adding once the backend has
somewhere to put it, or it is the dead schema the previous paragraph rules out.

## Outcome

Re-file the substance on `danielplohmann/mcrit` as a request for an owner field on the
job document, stating the stable-identifier requirement as established rather than as a
question: the field cannot be a username, because MCRITweb renames and frees them.
