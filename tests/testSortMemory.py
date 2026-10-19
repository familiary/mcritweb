#!/usr/bin/python
"""The sort order a user last chose, remembered across visits (issue #58).

Before this, every listing and every search reverted to its default sort - id,
ascending - the moment the sort parameters fell out of the URL, which is any time
the user navigates by anything other than a paging link. Sorting samples by
filename and then clicking through to a sample and back put the table straight
back into sample_id order.

The memory lives in `flask.session`, so it is per browser rather than per account;
`mcritweb/views/cursor_pagination.py` documents why that was chosen over the
per-user tables in `db.py`. The tests below pin the three properties that make it
safe rather than merely convenient:

* an explicit sort in the URL always wins, so a shared link renders what the
  sender saw and the user can always get back to a different order;
* a stored value is checked against the columns the table macros actually offer
  **on the way out of the session as well as on the way in**, so a value written by
  an older version - or by anyone able to hand the browser a session cookie -
  cannot reach the backend query or a template;
* the memory is per listing type, so sorting samples does not reorder families.

`corpus_mcrit` records every search call it is handed, which is what lets these
assert on the sort the view asked the backend for rather than on row markup.
"""

import logging
import pathlib
import re
import unittest

import pytest

from mcritweb.views.cursor_pagination import SORT_MEMORY_SESSION_KEY, SORTABLE_FIELDS

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def last_search(backend, kind):
    """The (sort_by, is_ascending) the view last asked the backend for."""
    calls = [call for call in backend.calls if call[0] == f"search_{kind}"]
    assert calls, f"the view made no search_{kind} call"
    _name, _args, kwargs = calls[-1]
    return kwargs["sort_by"], kwargs["is_ascending"]


def sample_ids(response):
    """Sample ids in the order the rows were rendered."""
    return re.findall(r'class="sample-id">(\d+)</th>', response.get_data(as_text=True))


# --- remembering ------------------------------------------------------------------

def test_a_sort_survives_leaving_the_page_and_coming_back(client, as_role, fake_mcrit):
    """The defect itself: the second request carries no sort parameters at all."""
    as_role("visitor")
    sorted_page = client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/explore/statistics")
    revisited = client.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("filename", False)
    assert sample_ids(revisited) == sample_ids(sorted_page)
    assert sample_ids(revisited) != sorted(sample_ids(revisited), key=int)


def test_a_direction_chosen_without_a_field_is_remembered_too(client, as_role, fake_mcrit):
    """Reversing the default column sets `ascending` and leaves `sort` out of the
    URL, because the pagination only emits `sort` when it differs from the default.
    That is still a sort the user chose."""
    as_role("visitor")
    client.get("/explore/families?ascending=false")
    client.get("/explore/families")

    assert last_search(fake_mcrit, "families") == ("family_id", False)


def test_the_remembered_sort_reaches_the_search_page(client, as_role, fake_mcrit):
    """The listing and the search page render the same table macro, so they share
    one memory - the issue is about search, and a user who sorted samples by
    filename means it there too."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/explore/search?query=a")

    assert last_search(fake_mcrit, "samples") == ("filename", False)


def test_the_memory_is_kept_per_listing_type(client, as_role, fake_mcrit):
    """`family_id` is a legal sort for functions as well as for families. If the
    memory were one global slot, sorting one table would reorder the other."""
    as_role("visitor")
    client.get("/explore/functions?sort=family_id&ascending=false")
    client.get("/explore/families")

    assert last_search(fake_mcrit, "families") == ("family_id", True)


def test_the_memory_is_per_session(client, app, as_role, fake_mcrit):
    """It is stored in the session cookie, so a second browser starts from the
    defaults. This is the trade-off the choice of storage makes; asserting it keeps
    the documented behaviour and the implementation from drifting apart."""
    user_id = as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")

    other_browser = app.test_client()
    with other_browser.session_transaction() as other_session:
        other_session["user_id"] = user_id
    other_browser.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


def test_logging_out_forgets_the_sort(client, as_role, fake_mcrit):
    """`logout` clears the session and the memory lives in it, so the next person to
    use this browser starts from the defaults rather than from a stranger's view.
    The manual states this, which makes it worth pinning."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/logout")

    as_role("visitor", username="nextuser")
    client.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


# --- the URL always wins ----------------------------------------------------------

def test_an_explicit_sort_overrides_the_remembered_one(client, as_role, fake_mcrit):
    """Without this the feature makes the app feel broken: a link would render
    differently for the sender and the recipient, and no click could get the table
    back to the default order."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/explore/samples?sort=sample_id&ascending=true")

    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


def test_an_explicit_direction_overrides_the_remembered_sort(client, as_role, fake_mcrit):
    """`?ascending=false` alone is a complete choice - default field, reversed - and
    is what the header link emits for the default column. Honouring the remembered
    field here would render something the sender of that link never saw."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=true")
    client.get("/explore/samples?ascending=false")

    assert last_search(fake_mcrit, "samples") == ("sample_id", False)


def test_the_explicit_sort_becomes_the_remembered_one(client, as_role, fake_mcrit):
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/explore/samples?sort=version&ascending=true")
    client.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("version", True)


def test_a_url_that_carries_a_cursor_keeps_its_own_sort(client, as_role, fake_mcrit, corpus_mcrit):
    """A cursor encodes the sort it was issued for, so replaying it under a different
    one asks the backend to continue a run it never started. Every link the pagination
    builds carries both, so only a hand-edited or truncated URL gets here - it must
    behave as it did before the memory existed."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    cursor = corpus_mcrit.search_samples("", limit=10)["cursor"]["forward"]

    client.get(f"/explore/samples?limit=10&page=2&cursor={cursor}")

    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


def test_an_unchanged_sort_does_not_rewrite_the_cookie(client, as_role):
    """The memory rides in the session cookie, so a careless write would put a
    Set-Cookie on every listing response and grow every subsequent request."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")

    repeated = client.get("/explore/samples?sort=filename&ascending=false")
    recalled = client.get("/explore/samples")

    assert "Set-Cookie" not in repeated.headers
    assert "Set-Cookie" not in recalled.headers


# --- what comes back out of the session is validated ------------------------------

@pytest.mark.parametrize(
    "stored",
    [
        {"sample": ["binweight", True]},          # a field the table offers no link for
        {"sample": ["'; DROP TABLE user; --", True]},
        {"sample": ["family_id", True]},          # legal for functions, not offered here
        {"sample": "filename"},                   # not a pair
        {"sample": ["filename"]},                 # too short
        {"sample": ["filename", True, "extra"]},  # too long
        {"sample": None},
        "not-a-mapping",
        [["sample", "filename"]],
        42,
    ],
)
def test_a_stored_sort_that_is_not_offered_is_ignored(client, as_role, fake_mcrit, stored):
    """A session cookie is signed, not trusted: it survives a release that removed a
    column, and it is user input the moment anyone can hand a browser one. Every
    shape here has to end at the default sort rather than at the backend."""
    as_role("visitor")
    with client.session_transaction() as test_session:
        test_session[SORT_MEMORY_SESSION_KEY] = stored

    response = client.get("/explore/samples")

    assert response.status_code == 200
    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


def test_a_stored_sort_never_reaches_the_rendered_page(client, as_role):
    """The remembered value is also written back into every paging and sort link, so
    an unchecked one would land in the page as well as in the query."""
    as_role("visitor")
    with client.session_transaction() as test_session:
        test_session[SORT_MEMORY_SESSION_KEY] = {"sample": ["nosuchcolumn", True]}

    page = client.get("/explore/samples").get_data(as_text=True)

    assert "nosuchcolumn" not in page


def test_a_sort_the_table_does_not_offer_is_not_stored(client, as_role, fake_mcrit):
    """The gate on the way in. `binweight` is a legal sort for the backend but no
    header links to it, so a URL may use it and the next visit must not."""
    as_role("visitor")
    client.get("/explore/samples?sort=binweight&ascending=false")
    client.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("sample_id", True)


def test_a_sort_the_table_does_not_offer_leaves_the_memory_alone(client, as_role, fake_mcrit):
    """A URL the allow-list rejects is still served as asked - that pass-through is
    older than this feature and someone's bookmark may rely on it - but it must not
    quietly wipe what the user last chose either."""
    as_role("visitor")
    client.get("/explore/samples?sort=filename&ascending=false")
    client.get("/explore/samples?sort=binweight&ascending=true")
    client.get("/explore/samples")

    assert last_search(fake_mcrit, "samples") == ("filename", False)


def test_only_known_listing_types_stay_in_the_session(client, as_role):
    """Anything else in there is a leftover from an older version, and it would ride
    along in the cookie on every request forever."""
    as_role("visitor")
    with client.session_transaction() as test_session:
        test_session[SORT_MEMORY_SESSION_KEY] = {"leftover": ["whatever", True]}
    client.get("/explore/samples?sort=filename&ascending=false")

    with client.session_transaction() as test_session:
        assert set(test_session[SORT_MEMORY_SESSION_KEY]) == {"sample"}


# --- the allow-list describes the tables ------------------------------------------

SORTABLE_HEADER = re.compile(r'sortable_header_col\(sort_pagination,\s*"([^"]+)"')

#: The macro file each listing type's header lives in.
HEADER_MACROS = {
    "family": "family_row.html",
    "sample": "sample_row.html",
    "function": "function_row.html",
}


@pytest.mark.parametrize("kind, macro_file", sorted(HEADER_MACROS.items()))
def test_the_allow_list_is_exactly_what_the_headers_offer(kind, macro_file):
    """The allow-list is a copy of what the table macros link to, so it rots the
    moment a sortable column is added or removed - a new column would silently not
    be remembered, a removed one would keep being replayed at the backend. Reading
    the macros here is what makes the copy self-checking."""
    macro = pathlib.Path("mcritweb/templates/table") / macro_file
    offered = set(SORTABLE_HEADER.findall(macro.read_text()))

    assert offered == set(SORTABLE_FIELDS[kind])


if __name__ == "__main__":
    unittest.main()
