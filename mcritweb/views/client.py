"""Single construction point for the MCRIT backend client.

Views call get_client() rather than building a McritClient themselves. That gives
tests one place to substitute a fake backend - via the MCRIT_CLIENT_FACTORY config
key - instead of patching the McritClient name in every view module, and it
collapses the repeated per-view ServerInfo lookups into one instance per request.

See issue #88.
"""

from flask import current_app, g
from mcrit.client.McritClient import McritClient

from mcritweb.views.utility import get_server_token, get_server_url, get_username


def default_client_factory(username=None, **kwargs):
    """Build a client from the server settings stored in the local database."""
    client = McritClient(
        mcrit_server=get_server_url(),
        apitoken=get_server_token(),
        username=get_username() if username is None else username,
        **kwargs
    )
    # set as an attribute rather than passed to the constructor, so an mcrit that predates
    # the timeout keeps working and simply goes on without one
    client.timeout = current_app.config.get("MCRIT_CLIENT_TIMEOUT", (10, 280))
    return client


def get_client(**kwargs):
    """Return the MCRIT client to use for this request.

    The no-argument case is cached on `g`, since every view on a page would
    otherwise re-read the server URL and token from SQLite. Callers passing kwargs
    (the API passthrough needs raw_responses=True, and supplies its own username
    resolved from request headers) always get a fresh instance, because those
    clients differ in behaviour and must not be shared.
    """
    factory = current_app.config.get("MCRIT_CLIENT_FACTORY", default_client_factory)
    if kwargs:
        return factory(**kwargs)
    if "mcrit_client" not in g:
        g.mcrit_client = factory()
    return g.mcrit_client


def get_sample_entries(sample_ids):
    """{sample_id: SampleEntry, or None if the backend answered none} for the distinct
    ids in `sample_ids`, asking the backend for each id at most once per request.

    The ids not yet known go to the backend in one `getSamplesByIds` request, whatever
    their number. What is known already costs nothing: one sample named by several jobs
    on a page, or one the page holds for another reason, which it hands over with
    `remember_samples`. See issue #191.

    A backend older than mcrit 1.12 has no `/samples/ids` and answers the batch with a
    404, which the client turns into `{}`. That empty answer to a non-empty request
    falls back to one `getSampleById` per id, so MCRITweb keeps working against an
    older backend and pays the N requests only there.
    """
    client = get_client()
    return _entries_by_id("samples", sample_ids, client.getSamplesByIds, client.getSampleById)


def get_family_entries(family_ids):
    """`get_sample_entries` for families, through one `getFamiliesByIds` request. Those
    entries carry no sample lists; nothing that asks for them here reads one, and the
    fallback to one `getFamily` per id asks for none either."""
    client = get_client()
    return _entries_by_id("families", family_ids, client.getFamiliesByIds, lambda family_id: client.getFamily(family_id, with_samples=False))


def remember_samples(sample_entries):
    """Make entries this request already fetched another way - the rows of a sample
    search, the sample a page is about - known to `get_sample_entries`."""
    known = _known_entries("samples")
    for sample_entry in sample_entries:
        known[sample_entry.sample_id] = sample_entry


def _known_entries(kind):
    # on `g`, like the client itself: a lookup lasts as long as the request that made it
    if "known_entries" not in g:
        g.known_entries = {"samples": {}, "families": {}}
    return g.known_entries[kind]


def _entries_by_id(kind, entry_ids, fetch_many, fetch_one):
    known = _known_entries(kind)
    entry_ids = list(dict.fromkeys(entry_ids))
    missing = [entry_id for entry_id in entry_ids if entry_id not in known]
    if missing:
        # an id the backend has no entry for is absent from its answer, and a failed
        # request answers {} - either way the id maps to None, as a single lookup did
        found = fetch_many(missing) or {}
        if not found:
            # nothing at all for a non-empty request is what a backend without the batch
            # route answers, so ask the way that backend understands. A current backend
            # answers {} only when none of the ids exists, and then this costs N misses.
            found = {entry_id: fetch_one(entry_id) for entry_id in missing}
        for entry_id in missing:
            known[entry_id] = found.get(entry_id)
    return {entry_id: known[entry_id] for entry_id in entry_ids}
