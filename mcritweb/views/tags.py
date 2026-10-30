"""Tags on families, samples and functions (familiary/mcrit#53).

mcrit keeps a list of tags on every family, sample and function and hands it out with the
entry. `McritClient.addTags` / `removeTags` change it, `getTags` counts the tags in use, and
the search `tags:"<tag>"` finds what carries one. MCRITweb shows them and edits them; it
decides nothing about what a tag may be, that is `mcrit.libs.tags`.

Two things have to be there, and either can be missing:

- the installed mcrit package. Only a release with tags has the three client methods, and the
  entry classes of an older one drop a `tags` field they do not know, so with it nothing reads
  as tagged and nothing is offered. The templates read the attribute through the `getattr`
  filter for that reason.
- the backend. A current client can be talking to an older server, which answers the tag
  routes with 404: its entries read as untagged, which they are, and editing is not offered,
  since a control that cannot work is worse than an absent one (ADR-0003).
"""

from flask import current_app, flash, g

try:
    # what a tag may be and how many one request may name are mcrit's to say
    from mcrit.libs.tags import MAX_TAGS_PER_REQUEST, normalizeTags
except ImportError:
    # an mcrit package from before tags, whose client has no tag methods either
    MAX_TAGS_PER_REQUEST, normalizeTags = None, None

#: what McritClient needs for tags; all three arrived together
TAG_CLIENT_METHODS = ("addTags", "removeTags", "getTags")
#: the entities mcrit tags, as `getTags` and `addTags` name them
TAGGED_ENTITIES = ("family", "sample", "function")


def client_has_tags(client):
    """Whether the installed McritClient can tag at all."""
    if normalizeTags is None:
        return False
    # looked up on the class, as search_page does: the test doubles answer any attribute
    # on the instance with a callable
    return all(callable(getattr(type(client), name, None)) for name in TAG_CLIENT_METHODS)


def backend_has_tags(client):
    """Whether the connected backend stores tags. Asked once per request.

    The backend is asked rather than its version compared: no mcrit release carries tags yet
    to compare against, and a build of mcrit's main branch reports the release before it.
    `getTags("family")` is answered by every backend that has the routes and is a 404 - None
    from the client - on one that has not. Families are what is counted, because there are
    few of them and the count is read off an index.
    """
    if "backend_has_tags" not in g:
        supported = False
        if client_has_tags(client):
            try:
                supported = client.getTags("family") is not None
            except Exception:
                current_app.logger.warning("Could not ask MCRIT whether it stores tags.", exc_info=True)
        g.backend_has_tags = supported
    return g.backend_has_tags


def can_edit_tags(client):
    """Whether this request may be offered tag editing: a contributor, against a backend with tags.
    The role is checked first, so a visitor's page costs no question to the backend."""
    user = g.get("user")
    return user is not None and user.role in ("admin", "contributor") and backend_has_tags(client)


def tag_search_query(tag):
    """The search query that finds what carries `tag`. Quoted, because a tag may hold spaces
    and colons. mcrit's tags hold neither quotes nor backslashes, but a backend's answer is
    not this module's to trust, so both are escaped the way mcrit's query parser reads them."""
    return 'tags:"' + str(tag).replace("\\", "\\\\").replace('"', '\\"') + '"'


def requested_tags(client, form, field):
    """The tags the comma-separated `form[field]` asks for, as mcrit stores them, or None when
    the form has no such field: a form that never showed tags changes none.

    Raises ValueError, with a message for the user, for a tag mcrit would refuse, so the
    caller can refuse the whole form before anything is written.
    """
    value = form.get(field)
    if value is None:
        return None
    if not client_has_tags(client):
        raise ValueError("The installed mcrit package cannot tag; tags need an mcrit release that has them.")
    if not backend_has_tags(client):
        raise ValueError("The connected MCRIT backend does not store tags.")
    return normalizeTags([name for name in value.split(",") if name.strip()])


def save_tags(client, kind, entity_id, current, requested):
    """Make the entity carry `requested` instead of `current`, and say how that went.

    mcrit has no replace-all, deliberately: the difference is removed and added, so a tag
    someone added since this form was rendered is kept rather than erased. Removals go
    first, so that swapping tags on an entity at mcrit's cap does not pass the cap on the
    way. Requests are split at mcrit's per-request limit.

    Returns whether there was a change to make.
    """
    if requested is None:
        return False
    current = list(current or [])
    removed = [tag for tag in current if tag not in requested]
    added = [tag for tag in requested if tag not in current]
    if not removed and not added:
        return False
    for change, tags in ((client.removeTags, removed), (client.addTags, added)):
        for start in range(0, len(tags), MAX_TAGS_PER_REQUEST):
            if change(kind, entity_id, tags[start:start + MAX_TAGS_PER_REQUEST]) is None:
                flash(f"MCRIT did not accept the tags of {kind} {entity_id}.", category="error")
                return True
    flash(f"Tags of {kind} {entity_id} saved.", category="success")
    return True


def tag_counts(client):
    """{tag: {entity: count}} over families, samples and functions, sorted by tag, or None
    when the client or the backend has no tags."""
    if not client_has_tags(client):
        return None
    counts = {}
    for entity in TAGGED_ENTITIES:
        try:
            entity_counts = client.getTags(entity)
        except Exception:
            current_app.logger.warning("Could not read the %s tags from MCRIT.", entity, exc_info=True)
            entity_counts = None
        if entity_counts is None:
            return None
        for tag, count in entity_counts.items():
            counts.setdefault(tag, {})[entity] = count
    return dict(sorted(counts.items()))
