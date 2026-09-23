#!/usr/bin/python
"""The CFG view against function entries the backend stored without their xcfg.

mcrit deletes a FunctionEntry's control flow graph after minhashing when
STORAGE_DROP_DISASSEMBLY is set - its own MinHashIndex.getFunctionGraph is a
NotImplementedError carrying the same note - and both the single-function page and the
1-vs-1 comparison page rebuild an SmdaFunction from whatever getFunctionById hands
back. Before #67 that raised straight out of the view: a 500 from
/explore/fetchDotGraph, a 500 for the whole comparison page, and a CFG panel that
stayed blank without ever saying why.

This treats the symptom. The cause is in mcrit; docs/adr/0003 records the round trip
that was measured to find it, and that issue stays open.

No new fixture is needed: tests/fixtures/regenerate.py already keeps the reference
pool's xcfg and drops the matched pool's, so the corpus carries both kinds of entry.
"""

import logging
import unittest

import pytest

from mcritweb.views.explore import NO_XCFG_DOT_GRAPH
from mcritweb.views.functiondiff import empty_function_diff, get_function_diff

LOG = logging.getLogger(__name__)
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def _with_xcfg(backend):
    """A captured function id whose control flow graph survived capture."""
    for function_id, entry in sorted(backend._functions.items()):
        if entry.xcfg and entry.xcfg.get("blocks"):
            return function_id
    raise AssertionError("the corpus holds no entry with an xcfg")


def _without_xcfg(backend):
    """A captured function id whose control flow graph was dropped."""
    for function_id, entry in sorted(backend._functions.items()):
        if not entry.xcfg:
            return function_id
    raise AssertionError("the corpus holds no entry without an xcfg")


def test_corpus_offers_both_kinds_of_entry(fake_mcrit):
    """Guards the tests below: if regenerate.py ever stops dropping the xcfg of the
    matched pool, they would all silently exercise the same intact branch."""
    assert _with_xcfg(fake_mcrit) != _without_xcfg(fake_mcrit)


def test_dot_graph_rendered_for_entry_with_xcfg(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get(f"/explore/fetchDotGraph/{_with_xcfg(fake_mcrit)}")
    assert response.status_code == 200
    body = response.data.decode()
    assert body.startswith('digraph "CFG for 0x')
    assert "Node0x" in body
    assert NO_XCFG_DOT_GRAPH not in body


def test_dot_graph_for_entry_without_xcfg_explains_itself(client, as_role, fake_mcrit):
    """Used to raise ValueError('serialized function is incomplete') -> HTTP 500."""
    as_role("visitor")
    response = client.get(f"/explore/fetchDotGraph/{_without_xcfg(fake_mcrit)}")
    assert response.status_code == 200
    assert response.data.decode() == NO_XCFG_DOT_GRAPH


def test_message_does_not_blame_export_import():
    """The stand-in graph told the user the CFG "is not part of exported data", which a
    measured round trip disproved: getExportData -> addImportData reproduces the xcfg
    field for field, over MemoryStorage, MongoDB and the HTTP export (docs/adr/0003).
    Naming the wrong cause in the UI sends people to look in the wrong place."""
    assert "export" not in NO_XCFG_DOT_GRAPH.lower()
    assert "import" not in NO_XCFG_DOT_GRAPH.lower()


def test_message_graph_names_no_block_hash(client, as_role, fake_mcrit):
    """The front end fires a synchronous getPicBlockMatches lookup for every node
    carrying a non-empty `comment`, so the stand-in node must leave it empty."""
    assert 'comment=""' in NO_XCFG_DOT_GRAPH
    assert NO_XCFG_DOT_GRAPH.count("[") == NO_XCFG_DOT_GRAPH.count("]") == 1
    assert NO_XCFG_DOT_GRAPH.count("{") == NO_XCFG_DOT_GRAPH.count("}") == 1
    # nothing from the analysed binary is interpolated into it
    assert "%" not in NO_XCFG_DOT_GRAPH and "{}" not in NO_XCFG_DOT_GRAPH


def test_unknown_function_still_yields_no_graph(client, as_role):
    """An id that is not in the corpus is not the same as an entry without a graph,
    and must not claim disassembly was merely dropped."""
    as_role("visitor")
    response = client.get("/explore/fetchDotGraph/99999999")
    assert response.status_code == 200
    assert response.data == b""


def test_dot_graph_survives_absent_picblockhashes(client, as_role, fake_mcrit):
    """The other half of #67 asks whether picblockhashes disappear on import. If they
    do, the block-hash fixup must not take the graph down with it."""
    as_role("visitor")
    function_id = _with_xcfg(fake_mcrit)
    fake_mcrit._functions[function_id].picblockhashes = None
    response = client.get(f"/explore/fetchDotGraph/{function_id}")
    assert response.status_code == 200
    assert response.data.decode().startswith('digraph "CFG for 0x')


def test_node_colors_degrade_without_xcfg(app, fake_mcrit):
    """get_function_diff rebuilds both graphs, so it took the comparison page
    down with a 500 before either pane could render."""
    with app.test_request_context():
        diff = get_function_diff(_with_xcfg(fake_mcrit), _without_xcfg(fake_mcrit))
    assert diff["node_colors"] == {"a": {}, "b": {}}
    assert diff["pairs"] == []
    assert diff["smda_functions"] is None
    assert diff["node_colors"] == empty_function_diff()["node_colors"]


def test_node_colors_still_computed_with_xcfg(app, fake_mcrit):
    """The guard above must not swallow the ordinary case."""
    function_id = _with_xcfg(fake_mcrit)
    with app.test_request_context():
        colors = get_function_diff(function_id, function_id)["node_colors"]
    assert colors["a"] and colors["b"]
    assert all(key.startswith("Node0x") for key in colors["a"])


if __name__ == "__main__":
    unittest.main()
