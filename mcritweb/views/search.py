"""One page of a search, as entry objects (fkie-cad/mcritweb#64).

MCRIT's search endpoints answer a dict of dicts, and every view that lists families,
samples or functions used to walk that dict and call `fromDict` on each value itself.
Newer mcrit versions answer the same search as a `SearchResult` of entry objects from
`McritClient.searchFamilies()` / `searchSamples()` / `searchFunctions()`. `search_page()`
uses the typed method where the client has one and deserialises the dict otherwise, so
the views see entry objects either way.
"""

from typing import Any, Dict, List, Optional

from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.FunctionEntry import FunctionEntry
from mcrit.storage.SampleEntry import SampleEntry

_KINDS = {
    # kind: (typed client method, dict client method, entry class)
    "families": ("searchFamilies", "search_families", FamilyEntry),
    "samples": ("searchSamples", "search_samples", SampleEntry),
    "functions": ("searchFunctions", "search_functions", FunctionEntry),
}


class SearchPage:
    """The entries of one search page in their answered order, the paging cursor, and the
    entries a numeric or sha256 search term named directly."""

    def __init__(self, entries: List[Any], cursor: Dict[str, Optional[str]], id_match: Any = None, sha_match: Any = None) -> None:
        self.entries = entries
        self.cursor = {"forward": cursor.get("forward"), "backward": cursor.get("backward")}
        self.id_match = id_match
        self.sha_match = sha_match

    @property
    def direct_matches(self) -> List[Any]:
        """id and sha match, without the duplicate when both name the same entry"""
        matches = [match for match in (self.id_match, self.sha_match) if match is not None]
        return [match for index, match in enumerate(matches) if match not in matches[:index]]

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def fromDict(cls, data: Dict[str, Any], entry_class) -> "SearchPage":
        from_dict = entry_class.fromDict
        entries = [from_dict(value) for value in (data.get("search_results") or {}).values()]
        id_match = data.get("id_match")
        sha_match = data.get("sha_match")
        return cls(
            entries,
            data.get("cursor") or {},
            from_dict(id_match) if id_match else None,
            from_dict(sha_match) if sha_match else None,
        )

    @classmethod
    def fromSearchResult(cls, result) -> "SearchPage":
        """from mcrit's SearchResult (entries already objects, keyed by id)"""
        return cls(list(result.entries.values()), result.cursor, result.id_match, result.sha_match)


def search_page(client, kind: str, search_term: str, **params) -> Optional[SearchPage]:
    """Run the `kind` ("families", "samples", "functions") search with the client's
    `cursor` / `is_ascending` / `sort_by` / `limit` parameters. None when the search failed."""
    typed_name, dict_name, entry_class = _KINDS[kind]
    # looked up on the class: the test doubles answer any attribute on the instance
    if callable(getattr(type(client), typed_name, None)):
        result = getattr(client, typed_name)(search_term, **params)
        return None if result is None else SearchPage.fromSearchResult(result)
    data = getattr(client, dict_name)(search_term, **params)
    return None if data is None else SearchPage.fromDict(data, entry_class)
