#!/usr/bin/python
"""`mcritweb.views.search.search_page` - the views' one way of running a search.

MCRIT's search endpoints answer a dict of dicts. Newer mcrit versions also answer the same
search as objects (`McritClient.searchFamilies()` etc., fkie-cad/mcritweb#64). The adapter
has to hand the views entry objects from either client, so both paths are tested with a
client that only has the one or the other.
"""

import logging
import unittest

import pytest
from fixtureData import CorpusMcritClient
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.SampleEntry import SampleEntry

from mcritweb.views.cursor_pagination import CursorPagination
from mcritweb.views.search import SearchPage, search_page

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

try:
    from mcrit.storage.SearchResult import SearchResult
except ImportError:  # the mcrit release on PyPI does not answer objects yet
    SearchResult = None


class DictOnlyClient:
    """a client of the current mcrit release: search_* methods answering the wire dict"""

    def __init__(self, corpus):
        self._corpus = corpus
        self.calls = []

    def search_samples(self, *args, **kwargs):
        self.calls.append(("search_samples", args, kwargs))
        return self._corpus.search_samples(*args, **kwargs)

    def search_families(self, *args, **kwargs):
        self.calls.append(("search_families", args, kwargs))
        return self._corpus.search_families(*args, **kwargs)

    def search_functions(self, *args, **kwargs):
        return self._corpus.search_functions(*args, **kwargs)

    def __getattr__(self, name):
        # like the test doubles in conftest/fixtureData: any other attribute exists and blows up
        def _unimplemented(*args, **kwargs):
            raise NotImplementedError(name)
        return _unimplemented


class _ObjectAnswer:
    """what mcrit's SearchResult looks like to the adapter, for an mcrit release without it"""

    def __init__(self, data, entry_class):
        self.entries = {int(key): entry_class.fromDict(value) for key, value in data["search_results"].items()}
        self.cursor = data["cursor"]
        self.id_match = entry_class.fromDict(data["id_match"]) if data.get("id_match") else None
        self.sha_match = entry_class.fromDict(data["sha_match"]) if data.get("sha_match") else None


class TypedClient(DictOnlyClient):
    """a client of a newer mcrit: the typed methods, answering objects"""

    def _typed(self, data, entry_class):
        if data is None:
            return None
        if SearchResult is not None:
            return SearchResult.fromDict(data, entry_class)
        return _ObjectAnswer(data, entry_class)

    def searchSamples(self, *args, **kwargs):
        self.calls.append(("searchSamples", args, kwargs))
        return self._typed(self._corpus.search_samples(*args, **kwargs), SampleEntry)

    def searchFamilies(self, *args, **kwargs):
        self.calls.append(("searchFamilies", args, kwargs))
        return self._typed(self._corpus.search_families(*args, **kwargs), FamilyEntry)

    def search_samples(self, *args, **kwargs):
        raise AssertionError("the adapter must prefer the typed method")


class FailingClient(DictOnlyClient):
    """a failed request: None from the typed method (samples) and from the dict one (families)"""

    def searchSamples(self, *args, **kwargs):
        return None

    def search_families(self, *args, **kwargs):
        return None


@pytest.fixture(params=[DictOnlyClient, TypedClient], ids=["dict", "objects"])
def search_client(request):
    return request.param(CorpusMcritClient())


def test_a_page_holds_entry_objects_in_answered_order(search_client):
    page = search_page(search_client, "samples", "", limit=5, sort_by="sample_id", is_ascending=False)
    assert isinstance(page, SearchPage)
    assert len(page) == 5
    assert all(isinstance(entry, SampleEntry) for entry in page)
    assert [entry.sample_id for entry in page] == sorted((entry.sample_id for entry in page), reverse=True)
    assert page.cursor["forward"] is not None and page.cursor["backward"] is None
    assert page.direct_matches == []


def test_the_parameters_reach_the_client_unchanged(search_client):
    search_page(search_client, "samples", "term", cursor=None, is_ascending=True, sort_by="sample_id", limit=7)
    name, args, kwargs = search_client.calls[-1]
    assert name in ("search_samples", "searchSamples")
    assert args == ("term",)
    assert kwargs == {"cursor": None, "is_ascending": True, "sort_by": "sample_id", "limit": 7}


def test_a_numeric_term_names_its_entry_directly(search_client):
    sample = next(iter(search_client._corpus._samples.values()))
    page = search_page(search_client, "samples", str(sample.sample_id))
    assert isinstance(page.id_match, SampleEntry)
    assert page.id_match.sample_id == sample.sample_id
    assert page.direct_matches == [page.id_match]


def test_a_sha256_names_its_sample_directly(search_client):
    sample = next(iter(search_client._corpus._samples.values()))
    page = search_page(search_client, "samples", sample.sha256)
    assert isinstance(page.sha_match, SampleEntry)
    assert page.sha_match.sample_id == sample.sample_id
    assert len(page.direct_matches) == 1


def test_families_are_family_entries(search_client):
    page = search_page(search_client, "families", "", limit=100)
    assert len(page) == len(search_client._corpus._families)
    assert all(isinstance(entry, FamilyEntry) for entry in page)


def test_a_failed_search_is_none():
    client = FailingClient(CorpusMcritClient())
    assert search_page(client, "samples", "") is None
    assert search_page(client, "families", "") is None


def test_the_typed_method_is_preferred_where_the_client_has_one():
    client = TypedClient(CorpusMcritClient())
    page = search_page(client, "samples", "", limit=3)
    assert len(page) == 3
    assert client.calls[-1][0] == "searchSamples"


def test_the_compare_pickers_keep_the_direct_matches():
    from mcritweb.views.analyze import get_unique_samples_from_search_result

    corpus = CorpusMcritClient()
    first, second = list(corpus._samples.values())[:2]
    # an id match and a sha256 match that the page itself does not hold, plus a page entry
    page = SearchPage([second], {"forward": None, "backward": None}, id_match=first, sha_match=first)
    unique = get_unique_samples_from_search_result(page)
    assert [sample.sample_id for sample in unique] == [second.sample_id, first.sample_id]
    # a sha256 search alone still names its sample
    page = SearchPage([], {"forward": None, "backward": None}, sha_match=first)
    assert [sample.sample_id for sample in get_unique_samples_from_search_result(page)] == [first.sample_id]


class _Request:
    def __init__(self, args):
        self.args = args
        self.endpoint = "test.endpoint"
        self.view_args = {}


def test_the_pagination_reads_its_cursor_from_a_page():
    page = SearchPage([], {"forward": "fwd", "backward": None})
    pagination = CursorPagination(_Request({}), default_sort="sample_id", limit=10)
    pagination.read_cursor_from_result(page)
    assert pagination.cursor["forward"] == "fwd"
    assert pagination.cursor["backward"] is None
    # and still from the wire dict
    pagination.read_cursor_from_result({"cursor": {"forward": None, "backward": "back"}})
    assert pagination.cursor["backward"] == "back"


def test_a_page_from_the_wire_dict_round_trips_the_entries():
    corpus = CorpusMcritClient()
    data = corpus.search_functions("", limit=4)
    page = SearchPage.fromDict(data, FunctionEntry)
    assert [entry.function_id for entry in page] == [int(key) for key in data["search_results"]]
    assert [entry.toDict() for entry in page] == list(data["search_results"].values())


if __name__ == "__main__":
    unittest.main()
