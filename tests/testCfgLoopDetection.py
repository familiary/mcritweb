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
the numbers. Later the per-node dominator sets gave way to the dominator tree, and
findLoops got bounds on what one posted graph may cost; `_reference_run` keeps the
earlier dominator sets and addParentInfo as well, so the same equivalence tests hold
those changes to the old answers.

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
import random
import unittest
from functools import reduce

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


def _reference_dominators(graph, start):
    """dominanators() as it read before the dominator tree: one set per node, narrowed
    by intersecting the sets of its predecessors until nothing changes."""
    universal_set = set(graph.nodes())
    doms = {n: (set(universal_set) if n != start else {start}) for n in graph.nodes()}
    changed = True
    while changed:
        changed = False
        for n in universal_set - {start}:
            new_doms = {n}.union(reduce(set.intersection, (doms[pred] for pred in graph.predecessors(n)), universal_set))
            if new_doms != doms[n]:
                changed = True
                doms[n] = new_doms
    return doms


def _reference_add_parent_info(loops):
    """addParentInfo as it read before it counted its entries: list membership."""
    loops.sort(key=lambda x: len(x["nodes"]))
    for i in range(len(loops)):
        loops[i]["parent"] = ""
        for j in range(i + 1, len(loops)):
            header = loops[i]["backedge"][1]
            if header in loops[j]["nodes"]:
                if len(loops[i]["nodes"]) == len(loops[j]["nodes"]):
                    loops[i].setdefault("equal", []).append(j)
                else:
                    loops[i]["parent"] = j
                    break


def _reference_run(dot_content):
    """cfg_explorer_detector.run() as it read before #204 and before the dominator
    tree: per-node dominator sets, the pre-#204 collect_loops/getNodes and the
    list-scanning addParentInfo."""
    graph = cfg_explorer_detector.parse_dot_to_graph(dot_content)
    if graph.number_of_nodes() == 0:
        return "[]"
    roots = cfg_explorer_detector.get_roots(graph)
    assert len(roots) == 1
    dominator_sets = _reference_dominators(graph, roots[0])
    backedges = [(src, tgt) for src, tgt in graph.edges() if tgt in dominator_sets[src]]
    loops = _reference_collect_loops(graph, backedges, dominator_sets)
    _reference_add_parent_info(loops)
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


# --- input without blocks -----------------------------------------------------------

@pytest.mark.parametrize("body", ["digraph G {\n}\n", "", "not a dot graph", b"\xff\xfe not UTF-8"])
def test_find_loops_answers_a_graph_without_blocks_with_no_loops(client, as_role, body):
    """get_roots() took min() of no nodes, so a body without blocks - an empty graph, or
    anything that is not a dot graph - made findLoops answer with a 500; so did a body
    that is not UTF-8, before it reached the detector."""
    as_role("visitor")
    response = client.post("/explore/findLoops/", data=body, content_type="text/plain")
    assert response.status_code == 200
    assert json.loads(response.data) == []


def test_find_loops_does_not_take_a_block_for_the_super_root(client, as_role):
    """With several roots get_roots() adds my_super_root above them; a block of that name
    used to become one more root's parent instead, leaving no root and a 500."""
    as_role("visitor")
    loop = "a -> c\nc -> a\nb -> a\n"
    response = client.post("/explore/findLoops/", data="my_super_root -> a\n" + loop, content_type="text/plain")
    assert response.status_code == 200
    assert json.loads(response.data) == json.loads(cfg_explorer_detector.run("x -> a\n" + loop))
    assert json.loads(response.data) == [{"backedge": ["c", "a"], "nodes": ["c", "a"], "parent": ""}]


def test_main_reads_a_file_and_runs_the_same_analysis(tmp_path, cfg_loop_functions):
    """main() is run() on a file's content; it used to carry its own copy of run()'s body."""
    dot_for_loops = _dot_for_loops(cfg_loop_functions[MULTI_BACKEDGE_FUNCTION].toSmdaFunction().toDotGraph(with_api=True))
    path = tmp_path / "function.dot"
    path.write_text(dot_for_loops)
    assert cfg_explorer_detector.main(str(path)) == cfg_explorer_detector.run(dot_for_loops)
    assert json.loads(cfg_explorer_detector.main(str(path)))


# --- the dominator tree --------------------------------------------------------------

def _random_dot(rng, size):
    """A random graph: forward edges, back edges, self-loops and, now and then, a
    block nothing reaches or a second entry."""
    nodes = [f"b{i:x}" for i in range(size)]
    edges = set()
    for i in range(1, size):
        edges.add((nodes[rng.randrange(i)], nodes[i]))
    for _ in range(rng.randrange(size * 2)):
        edges.add((rng.choice(nodes), rng.choice(nodes)))
    if rng.random() < 0.3:
        # a cycle nothing enters
        edges.update({("u0", "u1"), ("u1", "u0"), ("u1", rng.choice(nodes))})
    if rng.random() < 0.3:
        # a second entry
        edges.add(("e2", rng.choice(nodes)))
    return "".join(f"{src} -> {tgt}\n" for src, tgt in sorted(edges))


def test_loops_match_the_set_based_dominators_on_random_graphs():
    """The dominator tree has to answer every `a in doms[b]` the per-node sets did,
    including for blocks the entry never reaches, which the sets left at "every
    block"."""
    rng = random.Random(4711)
    for _ in range(300):
        dot = _random_dot(rng, rng.randrange(2, 40))
        assert cfg_explorer_detector.run(dot) == _reference_run(dot), dot


def test_dominator_membership_matches_the_set_based_dominators():
    rng = random.Random(1174)
    for _ in range(100):
        graph = cfg_explorer_detector.parse_dot_to_graph(_random_dot(rng, rng.randrange(2, 30)))
        root = cfg_explorer_detector.get_roots(graph)[0]
        expected = _reference_dominators(graph, root)
        actual = cfg_explorer_detector.dominanators(graph, root)
        for node in graph.nodes():
            assert {a for a in graph.nodes() if a in actual[node]} == expected[node]


# --- bounds on a posted graph --------------------------------------------------------

def _fan(blocks):
    """A chain whose every block also jumps back to the entry: one loop per block
    after the entry, each one the whole chain so far - an answer the square of the
    graph."""
    lines = [f"b{i:x} -> b{i + 1:x}" for i in range(blocks - 1)]
    lines += [f"b{i:x} -> b0" for i in range(1, blocks)]
    return "\n".join(lines) + "\n"


def _star(loops):
    """One header with many two-block loops of it: of one length, so each lists every
    later one as equal."""
    return "e -> h\n" + "".join(f"h -> s{i:x}\ns{i:x} -> h\n" for i in range(loops))


def _self_loops(blocks):
    """Blocks that each loop to themselves: many loops, none of them equal to another."""
    return "".join(f"e -> b{i:x}\nb{i:x} -> b{i:x}\n" for i in range(blocks))


def _leaves(chain):
    """A chain whose last block jumps to a leaf of every chain block: no loops, but the
    shape immediate_dominators is quadratic on."""
    lines = [f"c{i:x} -> c{i + 1:x}" for i in range(chain - 1)]
    lines += [f"c{i:x} -> x{i:x}\nc{chain - 1:x} -> x{i:x}" for i in range(chain)]
    return "\n".join(lines) + "\n"


def _dense(blocks):
    """Every block jumps to every later one: few blocks, many edges."""
    return "".join(f"b{i:x} -> b{j:x}\n" for i in range(blocks) for j in range(i + 1, blocks))


@pytest.mark.parametrize("dot", [_leaves(41), _dense(200), _fan(700), _self_loops(2001)],
                         ids=["blocks", "edges", "bodies", "back-edges"])
def test_a_graph_whose_answer_would_outgrow_the_bounds_gets_no_loops(client, as_role, caplog, monkeypatch, dot):
    """Still JSON, so the page draws the graph without loops instead of nothing; and
    a line in the log saying why. The block bound is lowered for this test: a graph
    over the real one takes the set-based dominators this replaced hours."""
    monkeypatch.setattr(cfg_explorer_detector, "MAX_BLOCKS", 80)
    as_role("visitor")
    logging.disable(logging.NOTSET)
    try:
        with caplog.at_level(logging.WARNING):
            response = client.post("/explore/findLoops/", data=dot, content_type="text/plain")
    finally:
        logging.disable(logging.CRITICAL)
    assert response.status_code == 200
    assert json.loads(response.data) == []
    assert any("not analysed" in record.getMessage() for record in caplog.records)


def test_many_equal_loops_keep_their_loops_and_lose_only_equal_entries(client, as_role):
    """A flattened dispatcher: hundreds of two-block loops on one header. The page draws
    every loop; only the "equal" lists, which it does not read, are cut."""
    as_role("visitor")
    dot = _star(700)
    response = client.post("/explore/findLoops/", data=dot, content_type="text/plain")
    assert response.status_code == 200
    loops = json.loads(response.data)
    expected = json.loads(_reference_run(dot))
    assert len(loops) == len(expected) == 700
    assert [(loop["backedge"], loop["nodes"], loop["parent"]) for loop in loops] == \
        [(loop["backedge"], loop["nodes"], loop["parent"]) for loop in expected]
    assert sum(len(loop.get("equal", [])) for loop in loops) == cfg_explorer_detector.MAX_EQUAL_ENTRIES
    assert sum(len(loop.get("equal", [])) for loop in expected) > cfg_explorer_detector.MAX_EQUAL_ENTRIES


def test_a_graph_just_inside_the_bounds_is_still_answered(client, as_role):
    as_role("visitor")
    dot = _fan(600)
    response = client.post("/explore/findLoops/", data=dot, content_type="text/plain")
    assert response.status_code == 200
    assert response.data.decode() == _reference_run(dot)


if __name__ == "__main__":
    unittest.main()
