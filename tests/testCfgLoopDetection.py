#!/usr/bin/python
"""The CFG dot-graph and loop-detection endpoints, checked against the pre-#204
algorithms they replaced.

Issue #204: fetchDotGraph (mcritweb/views/explore.py) rebuilt the picblockhash
comment on every block with its own `dot_graph.replace()` call, each one rescanning
the whole (already-grown) graph string - O(blocks x graph size) for what a single
pass can do. cfg_explorer_detector.getNodes, called once per loop back edge from
collect_loops, reversed the whole control flow graph again for every back edge, for a
depth-first search that only ever needs the one reversed copy. Both were measured
against the largest functions of a real ripgrep binary (900+ blocks) - see PR.md for
the numbers. addParentInfo is quadratic in the number of loops in shape, but no real
or synthetic loop count made that cost anything worth the complexity of changing it,
so it is untouched here; the equivalence tests below still exercise it end to end.

Neither endpoint was meant to change what it returns, only how much work it does to
get there. `_reference_dot_graph` and `_reference_run` are the pre-#204 algorithms,
transcribed rather than imported so a regression in the real code cannot drag the
oracle down with it. The equivalence tests hold the real endpoints to matching them
exactly, over five real functions captured from a live backend
(tests/fixtures/cfg_loop_functions.json - see tests/fixtures/README.md). The
structural tests separately prove the fix itself happened - fewer regex/reverse
calls than blocks/back edges, counted rather than timed, since wall-clock time is not
something a test should assert on.
"""

import json
import logging
import unittest

import networkx as nwx
import pytest
from conftest import FakeMcritClient
from fixtureData import load
from mcrit.storage.FunctionEntry import FunctionEntry

import mcritweb.views.cfg_explorer_detector as cfg_explorer_detector
import mcritweb.views.explore as explore

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: cfg_loop_functions.json, by role - real functions picked for the loop shape their
#: name describes. 16505 and 16243 are ripgrep (sample 23); 15630 is ripgrep too;
#: 2758 is fastlist (sample 16, 32-bit); 5790 is clipboardy (sample 19). All five
#: carry picblockhashes. test_fixture_covers_the_shapes_the_tests_below_rely_on
#: guards these claims against a fixture that stops meaning what it says.
MULTI_BACKEDGE_FUNCTION = 16505  # 34 blocks, 3 back edges, one of them nested in another
SELF_LOOP_FUNCTION = 16243       # 4 blocks, one back edge from a block to itself
NO_LOOP_FUNCTION = 15630         # 3 blocks, no back edges
OTHER_SAMPLE_FUNCTIONS = (2758, 5790)  # off-ripgrep functions, each with one self-loop
FIXTURE_FUNCTION_IDS = (MULTI_BACKEDGE_FUNCTION, SELF_LOOP_FUNCTION, NO_LOOP_FUNCTION, *OTHER_SAMPLE_FUNCTIONS)


class CfgLoopMcritClient(FakeMcritClient):
    """Serves only the functions captured in cfg_loop_functions.json, by id."""

    def __init__(self, functions, **kwargs):
        super().__init__(**kwargs)
        self._functions = functions

    def getFunctionById(self, function_id, *args, **kwargs):
        self._record("getFunctionById", function_id, *args, **kwargs)
        return self._functions.get(int(function_id))


@pytest.fixture
def cfg_loop_functions():
    """The fixture's FunctionEntry objects, by (int) function id."""
    return {int(fid): FunctionEntry.fromDict(entry) for fid, entry in load("cfg_loop_functions").items()}


@pytest.fixture
def fake_mcrit(cfg_loop_functions):
    """Wire the app in this module to the five captured functions."""
    return CfgLoopMcritClient(cfg_loop_functions)


# --- the pre-#204 algorithms, kept only as the oracle for the tests below --------

def _reference_dot_graph(function_entry):
    """fetchDotGraph's block-comment fixup exactly as it read before #204: one
    dot_graph.replace() call per block."""
    smda_function = function_entry.toSmdaFunction()
    dot_graph = smda_function.toDotGraph(with_api=True)
    pbh_by_offset = {pbh["offset"]: pbh for pbh in function_entry.picblockhashes or []}
    for smda_block in smda_function.getBlocks():
        needle = f',label="{smda_block.offset:x}'
        replacement = f',comment=""{needle}'
        if smda_block.offset in pbh_by_offset:
            replacement = f',comment="0x{pbh_by_offset[smda_block.offset]["hash"]:x}"{needle}'
        dot_graph = dot_graph.replace(needle, replacement)
    return dot_graph


def _reference_get_nodes(graph, backedge):
    """getNodes exactly as it read before #204: a fresh graph.reverse() copy per
    back edge."""
    if backedge[0] == backedge[1]:
        return [backedge[0]]
    reverse_graph = graph.reverse()
    reverse_graph.remove_node(backedge[1])
    node_list = list(nwx.dfs_preorder_nodes(reverse_graph, backedge[0]))
    node_list.append(backedge[1])
    return node_list


def _reference_collect_loops(graph, backedges, dominator_sets):
    """collect_loops's own logic, over _reference_get_nodes instead of the fixed one."""
    result = []
    for backedge in backedges:
        nodes = list(filter(
            lambda node: backedge[1] in dominator_sets[node],
            _reference_get_nodes(graph, backedge),
        ))
        result.append({"backedge": backedge, "nodes": nodes})
    return result


def _reference_run(dot_content):
    """cfg_explorer_detector.run(), with the pre-#204 collect_loops/getNodes.
    addParentInfo is untouched by the fix, so the real one is used here too."""
    graph = cfg_explorer_detector.parse_dot_to_graph(dot_content)
    roots = cfg_explorer_detector.get_roots(graph)
    assert len(roots) == 1
    dominator_sets = cfg_explorer_detector.dominanators(graph, roots[0])
    backedges = cfg_explorer_detector.compute_backedges(graph, dominator_sets)
    loops = _reference_collect_loops(graph, backedges, dominator_sets)
    cfg_explorer_detector.addParentInfo(loops)
    return json.dumps(loops)


def _dot_for_loops(dot_graph):
    """The transform main.js applies before posting a fetched dot graph to
    findLoops - \\l is a Graphviz line break, not the newline parse_dot_to_graph
    matches lines on."""
    return dot_graph.replace("\\l", "\n")


# --- fixture sanity ----------------------------------------------------------------

def test_fixture_covers_the_shapes_the_tests_below_rely_on(cfg_loop_functions):
    """Guards the tests below: without a no-loop, a self-loop and a multi-back-edge
    function among the fixtures, they would all exercise the same branch."""
    backedges_by_function = {}
    for function_id, entry in cfg_loop_functions.items():
        dot_graph = _dot_for_loops(entry.toSmdaFunction().toDotGraph(with_api=True))
        graph = cfg_explorer_detector.parse_dot_to_graph(dot_graph)
        dominator_sets = cfg_explorer_detector.dominanators(graph, cfg_explorer_detector.get_roots(graph)[0])
        backedges_by_function[function_id] = cfg_explorer_detector.compute_backedges(graph, dominator_sets)

    assert backedges_by_function[NO_LOOP_FUNCTION] == []
    assert len(backedges_by_function[MULTI_BACKEDGE_FUNCTION]) >= 2
    assert any(src == tgt for src, tgt in backedges_by_function[SELF_LOOP_FUNCTION])
    assert all(cfg_loop_functions[fid].picblockhashes for fid in FIXTURE_FUNCTION_IDS), (
        "every fixture function is meant to carry picblockhashes"
    )


# --- equivalence: the real endpoints against the pre-#204 algorithms ---------------

def test_dot_graph_matches_the_pre_204_algorithm(client, as_role, cfg_loop_functions):
    as_role("visitor")
    for function_id in FIXTURE_FUNCTION_IDS:
        expected = _reference_dot_graph(cfg_loop_functions[function_id])
        response = client.get(f"/explore/fetchDotGraph/{function_id}")
        assert response.status_code == 200
        assert response.data.decode() == expected, f"function {function_id} diverged from the pre-#204 dot graph"


def test_loops_match_the_pre_204_algorithm(client, as_role, cfg_loop_functions):
    as_role("visitor")
    for function_id in FIXTURE_FUNCTION_IDS:
        dot_for_loops = _dot_for_loops(_reference_dot_graph(cfg_loop_functions[function_id]))
        expected = _reference_run(dot_for_loops)

        response = client.post("/explore/findLoops/", data=dot_for_loops, content_type="text/plain")
        assert response.status_code == 200
        assert response.data.decode() == expected, f"function {function_id} diverged from the pre-#204 loops"


def test_dot_graph_survives_missing_picblockhashes(client, as_role, cfg_loop_functions):
    """Edge case both the old and new fixups have to agree on: no picblockhashes at
    all, not just none matching any block (the other half of #67 - see
    test_dot_graph_survives_absent_picblockhashes in testCfgGraphs.py)."""
    as_role("visitor")
    function_id = MULTI_BACKEDGE_FUNCTION
    cfg_loop_functions[function_id].picblockhashes = None
    expected = _reference_dot_graph(cfg_loop_functions[function_id])
    response = client.get(f"/explore/fetchDotGraph/{function_id}")
    assert response.status_code == 200
    assert response.data.decode() == expected
    assert 'comment=""' in response.data.decode()


# --- structural: proving the fix itself, not merely its output ---------------------

def test_dot_graph_fixup_scans_the_graph_once_per_request(client, as_role, monkeypatch, cfg_loop_functions):
    """The #204 fix for fetchDotGraph: one re.sub pass over the graph text, rather
    than one dot_graph.replace() call per block. Counting calls, not timing them -
    a 34-block function scanned once by the fix would still look fast against
    unfixed master's replace loop only by coincidence of measurement noise, so this
    is the assertion that actually pins the change down."""
    as_role("visitor")
    calls = []
    real_pattern = explore.BLOCK_LABEL_RX

    class CountingPattern:
        def sub(self, *args, **kwargs):
            calls.append(1)
            return real_pattern.sub(*args, **kwargs)

    monkeypatch.setattr(explore, "BLOCK_LABEL_RX", CountingPattern())

    function_id = MULTI_BACKEDGE_FUNCTION
    num_blocks = cfg_loop_functions[function_id].num_blocks
    assert num_blocks > 1, "need more than one block for this test to mean anything"

    response = client.get(f"/explore/fetchDotGraph/{function_id}")
    assert response.status_code == 200
    assert len(calls) == 1, f"expected one pass over a {num_blocks}-block graph, got {len(calls)}"


def test_find_loops_reverses_the_graph_once_per_request(client, as_role, monkeypatch):
    """The #204 fix for collect_loops/getNodes: one graph.reverse(), shared across
    every back edge in the request, rather than one fresh reversed copy per back
    edge. MULTI_BACKEDGE_FUNCTION has 3 back edges, so unfixed master reverses the
    graph 3 times to answer this request."""
    as_role("visitor")
    reverse_calls = []
    real_reverse = nwx.DiGraph.reverse

    def counting_reverse(self, *args, **kwargs):
        reverse_calls.append(1)
        return real_reverse(self, *args, **kwargs)

    monkeypatch.setattr(nwx.DiGraph, "reverse", counting_reverse)

    dot_graph = client.get(f"/explore/fetchDotGraph/{MULTI_BACKEDGE_FUNCTION}").data.decode()
    response = client.post("/explore/findLoops/", data=_dot_for_loops(dot_graph), content_type="text/plain")
    assert response.status_code == 200

    loops = json.loads(response.data.decode())
    assert len(loops) >= 2, "need at least two back edges for this test to mean anything"
    assert len(reverse_calls) == 1, f"expected one reverse() shared across {len(loops)} back edges, got {len(reverse_calls)}"


def test_get_nodes_without_a_shared_reverse_graph_answers_as_before(client, as_role):
    """collect_loops always hands getNodes its shared reversed graph; called without
    one, getNodes builds its own. Both must give the pre-#204 answer."""
    as_role("visitor")
    dot_graph = client.get(f"/explore/fetchDotGraph/{MULTI_BACKEDGE_FUNCTION}").data.decode()
    graph = cfg_explorer_detector.parse_dot_to_graph(_dot_for_loops(dot_graph))
    dominator_sets = cfg_explorer_detector.dominanators(graph, cfg_explorer_detector.get_roots(graph)[0])
    backedges = cfg_explorer_detector.compute_backedges(graph, dominator_sets)
    assert len(backedges) >= 2, "need at least two back edges for this test to mean anything"
    shared_reverse_graph = graph.reverse()
    for backedge in backedges:
        expected = _reference_get_nodes(graph, backedge)
        assert cfg_explorer_detector.getNodes(graph, backedge) == expected
        assert cfg_explorer_detector.getNodes(graph, backedge, reverse_graph=shared_reverse_graph) == expected


if __name__ == "__main__":
    unittest.main()
