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
