#!/usr/bin/python

import logging
import re
import unittest

import pytest
from fixtureData import job_id_of

from mcritweb.views.pagination import Pagination, request_args_for_link_building

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


class MockRequest:

    def __init__(self, params, view_args=None) -> None:
        self.args = params
        # Pagination reads these in __init__ for later url_for() link generation.
        # The tests never call get_link(), so placeholder values are enough.
        self.endpoint = "test.endpoint"
        self.view_args = view_args if view_args is not None else {}


class PaginationTestSuite(unittest.TestCase):
    """Run a full example on a memory dump"""

    def testInitializations(self):
        test_values = [
            # no page query param
            {"req": {}, "max_value": 1000, "expected_page": 1, "expected_index": 0},
            # expected behavior, default limit
            {"req": {"p": 5}, "max_value": 1000, "expected_page": 5, "expected_index": 200},
            # expected behavior, custom limit
            {"req": {"p": 3}, "max_value": 1000, "limit": 100, "expected_page": 3, "expected_index": 200},
            # faulty query params
            {"req": {"p": 0}, "max_value": 1000, "expected_page": 1, "expected_index": 0},
            {"req": {"p": -1}, "max_value": 1000, "expected_page": 1, "expected_index": 0},
            {"req": {"p": "no_int"}, "max_value": 1000, "expected_page": 1, "expected_index": 0}
        ]
        for test_set in test_values:
            p = Pagination(MockRequest(test_set["req"]), test_set["max_value"])
            if "limit" in test_set:
                p = Pagination(MockRequest(test_set["req"]), test_set["max_value"], limit=test_set["limit"])
            self.assertEqual(p.page, test_set["expected_page"])
            self.assertEqual(p.start_index, test_set["expected_index"])

    def testPaginatedNav(self):
        test_values = [
            # no content
            {"req": {}, "max_value": 0, "expected_max_page": 1, "expected_page_index": 0, "expected_pages": [1]},
            # incomplete first page
            {"req": {"p": 1}, "max_value": 10, "expected_max_page": 1, "expected_page_index": 0, "expected_pages": [1]},
            # less pages than pagination_width
            {"req": {"p": 1}, "max_value": 70, "expected_max_page": 2, "expected_page_index": 0, "expected_pages": [1, 2]},
            # walking over a regular pagination
            {"req": {"p": 1}, "max_value": 251, "expected_max_page": 6, "expected_page_index": 0, "expected_pages": [1, 2, 3]},
            {"req": {"p": 1}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 0, "expected_pages": [1, 2, 3]},
            {"req": {"p": 2}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 1, "expected_pages": [1, 2, 3, 4]},
            {"req": {"p": 3}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 2, "expected_pages": [1, 2, 3, 4, 5]},
            {"req": {"p": 4}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 2, "expected_pages": [2, 3, 4, 5, 6]},
            {"req": {"p": 5}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 2, "expected_pages": [3, 4, 5, 6]},
            {"req": {"p": 6}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 2, "expected_pages": [4, 5, 6]},
            # reaching beyond max page returns last page
            {"req": {"p": 10}, "max_value": 300, "expected_max_page": 6, "expected_page_index": 2, "expected_pages": [4, 5, 6]},
            # expected behavior, custom limit
            {"req": {"p": 5}, "max_value": 1000, "limit": 100, "expected_max_page": 10, "expected_page_index": 2, "expected_pages": [3, 4, 5, 6, 7]},
        ]
        for test_set in test_values:
            p = Pagination(MockRequest(test_set["req"]), test_set["max_value"])
            if "limit" in test_set:
                p = Pagination(MockRequest(test_set["req"]), test_set["max_value"], limit=test_set["limit"])
            print(p)
            self.assertEqual(p.max_page, test_set["expected_max_page"])
            self.assertEqual(p.page_index, test_set["expected_page_index"])
            self.assertEqual(p.pages, test_set["expected_pages"])


# --- query parameters that collide with url_for() --------------------------------
#
# Both pagination classes rebuild the current URL by splatting the request's args
# back into url_for(), which made every query parameter a possible collision with
# url_for()'s own arguments. `?endpoint=` raised a TypeError and `?_method=` a
# BuildError - both HTTP 500 - while `?_external=` and `?_scheme=` raised nothing
# and silently rebuilt every link as an absolute URL off the Host header.
#
# One page per class, because they are used by different pages and were broken
# independently: /explore/samples is a CursorPagination page (get_link and
# get_sort_link), /data/jobs a Pagination one (get_link). `explore.search` splats
# the same request args into url_for() to normalise a repeated `?type=`, and is
# covered here too - the URL it builds goes out in a Location header.

PAGINATED_PAGES = [
    ("/explore/samples", "CursorPagination"),
    ("/data/jobs", "Pagination"),
]

#: Every name url_for() takes for itself instead of putting it in the URL.
RESERVED_ARGS = ["endpoint", "_external", "_scheme", "_anchor", "_method"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Rows, so the widgets render paging links there is something to assert on."""
    return corpus_mcrit


def pagination_links(response, path):
    """The paging and sorting URLs the pagination macros built for `path`.

    Sort links are emitted inside an onclick rather than an href, so both spellings
    are collected: `_external` poisons them just the same. Other links on the page
    point at the same path - the jobs state menu, for one - so a link only counts as
    a pagination link once it carries the page parameter.
    """
    body = response.get_data(as_text=True)
    found = re.findall(rf"""(?:href="|window\.location\.href=')([^"']*{re.escape(path)}\?[^"']*)""", body)
    links = [link.replace("&amp;", "&") for link in found]
    return [link for link in links if re.search(r"[?&](?:p|page)=", link)]


@pytest.mark.parametrize("path, pagination_class", PAGINATED_PAGES)
def test_a_query_parameter_named_endpoint_does_not_break_the_page(client, as_role, path, pagination_class):
    """`url_for() got multiple values for argument 'endpoint'` -> HTTP 500."""
    as_role("admin")
    response = client.get(f"{path}?endpoint=x")

    assert response.status_code == 200, f"{pagination_class} page {path} died on ?endpoint="
    assert pagination_links(response, path), "no pagination links to check"
    assert not [link for link in pagination_links(response, path) if "endpoint=" in link]


@pytest.mark.parametrize("path, pagination_class", PAGINATED_PAGES)
@pytest.mark.parametrize("reserved", ["_method=DELETE", "_scheme=https"])
def test_a_reserved_underscore_parameter_does_not_break_the_page(client, as_role, path, pagination_class, reserved):
    """`_method` failed the build outright - `BuildError: Could not build url for
    endpoint ... ('DELETE')`, HTTP 500. `_scheme` is the quiet one: Flask turns
    `_external` on by itself whenever a scheme is given (`app.py`: `_external =
    _scheme is not None`), so it rewrote every link as an absolute URL instead of
    raising."""
    as_role("admin")
    response = client.get(f"{path}?{reserved}")

    assert response.status_code == 200, f"{pagination_class} page {path} died on ?{reserved}"
    links = pagination_links(response, path)
    assert links, "no pagination links to check"
    assert not [link for link in links if reserved.split("=")[0] in link]
    assert not [link for link in links if "//" in link], f"{reserved} made the links absolute: {links[:3]}"


@pytest.mark.parametrize("path, pagination_class", PAGINATED_PAGES)
def test_external_cannot_be_turned_on_from_the_query_string(client, as_role, path, pagination_class):
    """The one with security weight: `?_external=1` made url_for() build absolute
    URLs, and the host it builds them from is the request's own Host header. A link
    handed to a visitor then points wherever the Host said - here at
    http://attacker.example/... - so the query string alone poisons every paging and
    sorting link on the page."""
    user_id = as_role("admin")
    # the session cookie is scoped to the host it was set on, so the spoofed Host
    # needs a logged-in cookie of its own
    with client.session_transaction(base_url="http://attacker.example/") as session:
        session["user_id"] = user_id

    response = client.get(f"{path}?_external=1", headers={"Host": "attacker.example"})

    assert response.status_code == 200
    links = pagination_links(response, path)
    assert links, "no pagination links to check"
    assert not [link for link in links if "//" in link], f"{pagination_class} built absolute links: {links[:3]}"
    assert "attacker.example" not in response.get_data(as_text=True)


@pytest.mark.parametrize("path, pagination_class", PAGINATED_PAGES)
def test_an_anchor_from_the_query_string_does_not_reach_the_links(client, as_role, path, pagination_class):
    """`_anchor` is a supported argument of the pagination macros, which is exactly
    why a visitor-supplied one must not be able to take its place."""
    as_role("admin")
    response = client.get(f"{path}?_anchor=evil")

    assert response.status_code == 200
    assert not [link for link in pagination_links(response, path) if "#evil" in link]


@pytest.mark.parametrize("path, param, value", [
    ("/explore/samples", "query", "zeus"),
    ("/data/jobs", "state", "finished"),
])
def test_an_ordinary_query_parameter_is_still_carried_into_the_links(client, as_role, path, param, value):
    """The counterweight: only the reserved names are dropped. A filter the visitor
    set has to survive into the next page's URL or paging loses the filter."""
    as_role("admin")
    response = client.get(f"{path}?{param}={value}")

    links = pagination_links(response, path)
    assert links, "no pagination links to check"
    assert all(f"{param}={value}" in link for link in links), links[:3]


def test_the_anchor_the_template_asks_for_still_reaches_the_links(client, as_role):
    """The other half of dropping `_anchor` from the query string: the macros pass
    an `_anchor` of their own through `kwargs_overwrites`, which is deliberately not
    filtered, so a result page's paging links must still land on the right table."""
    as_role("visitor")
    response = client.get(f"/data/result/{job_id_of('matches_for_sample')}")

    assert response.status_code == 200
    assert "#sample-matches" in response.get_data(as_text=True)


# --- a query parameter shadowing a view arg --------------------------------------
#
# The same collision one level down, inside the filter itself: `dict(**view_args,
# **args)` raises TypeError on a duplicate key rather than resolving it, so
# /data/result/<job_id>?job_id=x was an HTTP 500 on every paginated route with a URL
# variable. The view arg has to win the merge, or the links leave the current page.

#: (path, the view arg it carries, the pagination class it exercises). One page per
#: class again: /data/result/<job_id> holds several Paginations, /explore/families/
#: <family_id> a CursorPagination.
PAGES_WITH_A_VIEW_ARG = [
    (f"/data/result/{job_id_of('matches_for_sample')}", "job_id", "Pagination"),
    ("/explore/families/1", "family_id", "CursorPagination"),
]


@pytest.mark.parametrize("path, view_arg, pagination_class", PAGES_WITH_A_VIEW_ARG)
def test_a_query_parameter_shadowing_a_view_arg_does_not_break_the_page(client, as_role, path, view_arg, pagination_class):
    """`TypeError: dict() got multiple values for keyword argument 'job_id'`, raised
    building the filtered args rather than in url_for()."""
    as_role("admin")
    response = client.get(f"{path}?{view_arg}=shadow")

    assert response.status_code == 200, f"{pagination_class} page {path} died on ?{view_arg}="


def test_the_view_arg_wins_over_a_query_parameter_of_the_same_name(client, as_role):
    """The path is what the request actually resolved to, so it is the authoritative
    value for rebuilding that path: every link on /data/result/<job_id>?job_id=shadow
    has to stay on this job rather than point at /data/result/shadow.

    The result page is the one asserted on because the family page renders no
    pagination widget against the offline corpus - `family_id:` is not a query the
    fixture's search parser answers - so there would be no links to look at."""
    job_id = job_id_of("matches_for_sample")
    as_role("admin")
    body = client.get(f"/data/result/{job_id}?job_id=shadow").get_data(as_text=True)

    assert f"/data/result/{job_id}?" in body, "the page built no links back to itself"
    assert "/data/result/shadow" not in body
    assert "job_id=shadow" not in body


# --- explore.search rebuilds its own URL, into a Location header ------------------

MULTI_TYPE_SEARCH = "/explore/search?query=x&type=sample&type=family"


@pytest.mark.parametrize("reserved", ["endpoint=z", "_method=DELETE"])
def test_the_search_redirect_survives_a_reserved_query_parameter(client, as_role, reserved):
    """`explore.search` normalises a repeated `?type=` by redirecting to itself with
    the request's args splatted into url_for() - the same collision as the pagination
    links, and the same two 500s."""
    as_role("visitor")
    response = client.get(f"{MULTI_TYPE_SEARCH}&{reserved}")

    assert response.status_code == 302, f"the search redirect died on ?{reserved}"
    assert response.headers["Location"].startswith("/explore/search?")
    assert reserved.split("=")[0] not in response.headers["Location"]


def test_the_search_redirect_cannot_be_pointed_at_another_host(client, as_role):
    """Worse here than in a page of links: this URL goes out in a Location header, so
    `?_external=1` turned the normalising redirect into a redirect to whatever the
    Host header said."""
    user_id = as_role("visitor")
    with client.session_transaction(base_url="http://attacker.example/") as session:
        session["user_id"] = user_id

    response = client.get(f"{MULTI_TYPE_SEARCH}&_external=1", headers={"Host": "attacker.example"})

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/explore/search?"), response.headers["Location"]
    assert "attacker.example" not in response.headers["Location"]


def test_the_search_redirect_still_normalises_the_repeated_type(client, as_role):
    """The counterweight: filtering the args must not cost the redirect its job."""
    as_role("visitor")
    response = client.get(MULTI_TYPE_SEARCH)

    assert response.status_code == 302
    assert response.headers["Location"] == "/explore/search?query=x&type=sample,family"



@pytest.mark.parametrize("reserved", RESERVED_ARGS)
def test_the_arg_filter_drops_every_name_url_for_reserves(reserved):
    filtered = request_args_for_link_building(MockRequest({reserved: "x", "keep": "yes"}))

    assert filtered == {"keep": "yes"}


def test_the_arg_filter_resolves_a_shadowed_view_arg_in_favour_of_the_path():
    """Both classes merge through this one function, so the rule is pinned here
    rather than once per class."""
    filtered = request_args_for_link_building(MockRequest({"job_id": "shadow"}, view_args={"job_id": "real"}))

    assert filtered == {"job_id": "real"}


if __name__ == "__main__":
    unittest.main()
