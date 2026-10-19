#!/usr/bin/python
"""`/explore/functions` hands the table entries, not raw dicts off the wire. Issue #64.

`McritClient._search_base` returns `handle_response(response)` untouched, so the values
under `search_results` are dicts, while every other accessor on the client
(`getFunctionById`, `getFamilies`, ...) returns typed entries. Making the client itself
deserialize is an mcrit change and is what the issue asks for.

The mcritweb half is that two of its three function listings disagreed about it.
`explore.search` and `explore.sample_by_id` called `FunctionEntry.fromDict` on those
values; `explore.functions` had that call commented out and appended the dict. All three
feed the same `function_table` macro.

For most rows the difference is invisible - Jinja falls back from attribute lookup to
item lookup, and the wire keys happen to equal the attribute names. It is not invisible
for `offset`: the wire dict carries it two's-complement encoded and `fromDict` runs
`decode_two_complement` over it, so a function mapped at or above the sign bit renders
as `0x-80000000` from the dict and `0xffffffff80000000` from the entry. The captured
corpus has no such function - its offsets all fit in 23 bits - which is why the bug
never showed up here; the `high_offset_function` fixture below moves one function up
there so it does.
"""

import logging
import unittest
from urllib.parse import quote

import pytest
from flask import template_rendered
from mcrit.storage.FunctionEntry import FunctionEntry

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: A mapped VA at or above the sign bit, as any kernel-mode driver sample has. The wire
#: dict carries it as the negative int64 -2147483648; `format_offset` is a bare `%0x`,
#: so it is the deserialization that decides which of these two strings a row shows.
HIGH_OFFSET = 0xFFFFFFFF80000000
OFFSET_FROM_ENTRY = "0xffffffff80000000"
OFFSET_FROM_DICT = "0x-80000000"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def rendered(app):
    """(template, context) for every template rendered during a request."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)


@pytest.fixture
def high_offset_function(fake_mcrit):
    """Moves one corpus function above the sign bit and returns it.

    The corpus is served out of `FunctionEntry` objects and re-serialized per search
    (`_page` calls `toDict`), so setting the attribute is enough to make the wire dict
    carry the two's-complement encoding of it, exactly as the backend would.

    The lowest function id is picked so the row lands on the first page of the listing
    under the default ascending `function_id` sort, and is answerable as an `id_match`
    on the search page. The tests find its row in the rendered page by pichash and read
    its offset cell by value, so both must be this function's alone - a precondition of
    a capture that could grow a collision, so asserted rather than assumed.
    """
    function = fake_mcrit._functions[min(fake_mcrit._functions)]
    function.offset = HIGH_OFFSET
    others = [other for other in fake_mcrit._functions.values() if other is not function]
    assert not any(other.pichash == function.pichash for other in others), (
        "another function shares the pichash, so matching a row on it proves nothing"
    )
    assert not any(other.offset == HIGH_OFFSET for other in others), (
        "another function sits at the same offset, so matching a cell on it proves nothing"
    )
    return function


def context_for(rendered, name):
    for template, context in rendered:
        if template.name == name:
            return context
    raise AssertionError(f"{name} was not rendered; got {[t.name for t, _ in rendered]}")


def test_the_function_listing_passes_entries_rather_than_wire_dicts(client, as_role, fake_mcrit, rendered):
    as_role("visitor")

    client.get("/explore/functions")

    functions = context_for(rendered, "functions.html")["functions"]
    assert functions, "the corpus rendered no functions, so this proves nothing"
    assert all(isinstance(function, FunctionEntry) for function in functions), (
        f"got {sorted({type(function).__name__ for function in functions})}"
    )


def test_the_listing_renders_an_offset_above_the_sign_bit(client, as_role, high_offset_function):
    """The user-visible half of the type assertion above, and the reason this is a
    rendering fix rather than a tidy-up: `offset` is the one column whose rendering
    differs between the wire dict and the entry."""
    as_role("visitor")

    listing = client.get("/explore/functions").get_data(as_text=True)

    assert f"0x{high_offset_function.pichash:016x}" in listing, "the row is not on the first page"
    assert OFFSET_FROM_ENTRY in listing
    assert OFFSET_FROM_DICT not in listing, "the offset was rendered straight off the wire"


def test_the_search_page_agrees(client, as_role, fake_mcrit, rendered):
    """The half that was already right. Asserted so the two cannot drift apart again
    from the other direction.

    Searched by name, because `explore.search` deserializes on two separate lines - one
    for `id_match`, one for the `search_results` loop - and a search by id only ever
    reaches the first. Only one function in the captured corpus carries a name, so the
    query is taken from the corpus rather than written out here; the `id_match` line is
    covered by `test_both_listings_render_the_same_row_for_the_same_function`, which
    searches by id.
    """
    as_role("visitor")
    named = [function for function in fake_mcrit._functions.values() if function.function_name]
    assert named, "no function in the corpus carries a name, so a name search proves nothing"

    client.get(f"/explore/search?query={quote(named[0].function_name)}&type=function")

    functions = context_for(rendered, "search.html")["functions"]
    assert functions, "the search returned no functions, so this proves nothing"
    assert all(isinstance(item, FunctionEntry) for item in functions)


def test_both_listings_render_the_same_row_for_the_same_function(client, as_role, high_offset_function):
    """The user-visible invariant behind the type assertions: whatever the two routes
    hand the shared macro, one function must look the same on both pages.

    Row identity is the pichash link, and the cell that would differ is the offset - so
    the two together are what "the same row" means here. The search side reaches the
    function through `id_match`, which is the `explore.search` line the name search
    above does not touch.
    """
    as_role("visitor")

    listing = client.get("/explore/functions?limit=25").get_data(as_text=True)
    search = client.get(f"/explore/search?query={high_offset_function.function_id}&type=function").get_data(as_text=True)

    # url_for percent-encodes the colon in the pichash query, so match on the rendered
    # hash rather than on the query string
    marker = f"0x{high_offset_function.pichash:016x}"
    for page, name in ((listing, "the listing"), (search, "the search page")):
        assert marker in page, f"{name} did not render the function at all"
        assert OFFSET_FROM_ENTRY in page, f"{name} rendered the offset straight off the wire"


if __name__ == "__main__":
    unittest.main()
