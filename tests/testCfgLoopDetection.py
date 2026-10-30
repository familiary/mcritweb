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
findLoops got bounds on what one posted graph may cost, with the parser stopping at
them; `_reference_run` keeps the earlier dominator sets, addParentInfo and parser as
well, so the same equivalence tests hold those changes to the old answers.

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

import io
import json
import logging
import random
import re
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


def _reference_parse_dot_to_graph(dot_content):
    """parse_dot_to_graph as it read before it stopped at the bounds: every line of the
    split text tried against both regexes."""
    edge_rx = re.compile(r"^\s*(?P<src>[\w_][\w_\d]*)(:[\w]*)?\s*\-\>\s*(?P<tgt>[\w_][\w_\d]*)(:[\w]*)?")
    node_rx = re.compile(r"^\s*(?P<node>[\w_][\w_\d]*)(:[\w]*)?\s*\[shape\=\w+(,comment=\".*\")?,label\=")
    graph = nwx.DiGraph()
    for line in dot_content.split("\n"):
        if line:
            match = node_rx.match(line)
            if match is not None:
                graph.add_node(match.group("node"), _name=match.group("node"))
            match = edge_rx.match(line)
            if match is not None:
                graph.add_node(match.group("src"), _name=match.group("src"))
                graph.add_node(match.group("tgt"), _name=match.group("tgt"))
                graph.add_edge(match.group("src"), match.group("tgt"))
    return graph


def _reference_run(dot_content):
    """cfg_explorer_detector.run() as it read before #204 and before the dominator
    tree: per-node dominator sets, the pre-#204 collect_loops/getNodes and the
    list-scanning addParentInfo, over the parser of before the bounds."""
    graph = _reference_parse_dot_to_graph(dot_content)
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


@pytest.mark.parametrize("dot,max_blocks,limit", [
    (_leaves(41), 80, "max-blocks=80"),
    (_dense(200), None, f"max-edges={cfg_explorer_detector.MAX_EDGES}"),
    (_fan(700), None, f"max-loop-body-entries={cfg_explorer_detector.MAX_LOOP_BODY_ENTRIES}"),
    (_self_loops(2001), None, f"max-back-edges={cfg_explorer_detector.MAX_BACK_EDGES}"),
], ids=["blocks", "edges", "bodies", "back-edges"])
def test_a_graph_whose_answer_would_outgrow_the_bounds_gets_no_loops(client, as_role, caplog, monkeypatch, dot, max_blocks, limit):
    """Still JSON and still a 200, so the page draws the graph without loops instead
    of nothing; a header the page reads to say why, naming the bound; and a line in
    the log. The block bound is lowered for its own case only: a graph over the real
    one takes the set-based dominators this replaced hours, and below 80 blocks none of
    the others would reach its bound."""
    if max_blocks is not None:
        monkeypatch.setattr(cfg_explorer_detector, "MAX_BLOCKS", max_blocks)
    as_role("visitor")
    logging.disable(logging.NOTSET)
    try:
        with caplog.at_level(logging.WARNING):
            response = client.post("/explore/findLoops/", data=dot, content_type="text/plain")
    finally:
        logging.disable(logging.CRITICAL)
    assert response.status_code == 200
    assert json.loads(response.data) == []
    assert response.headers["X-Loop-Analysis"] == f"skipped; {limit}"
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
    assert "X-Loop-Analysis" not in response.headers


@pytest.mark.parametrize("dot,bound,limit", [
    (_fan(100000), {"max_blocks": 100}, "max-blocks=100"),
    (_dense(300), {"max_edges": 100}, "max-edges=100"),
], ids=["blocks", "edges"])
def test_parsing_stops_at_a_bound(monkeypatch, dot, bound, limit):
    """The bounds used to be checked on the whole graph, once it was built: 32 MiB of
    edges took 1.6 GiB to parse before the block bound refused it. Now the parser gives
    up as soon as the graph is past one."""
    added = []
    real_add_edge = nwx.DiGraph.add_edge
    monkeypatch.setattr(nwx.DiGraph, "add_edge",
                        lambda self, u, v, **attr: added.append((u, v)) or real_add_edge(self, u, v, **attr))
    with pytest.raises(cfg_explorer_detector.LoopAnalysisTooLarge) as raised:
        cfg_explorer_detector.parse_dot_to_graph(dot, **bound)
    assert raised.value.limit == limit
    assert len(added) <= 101


@pytest.mark.parametrize("dot", [
    "a -> b\r\nb -> a\r\n",
    "\n\n  a:s0 -> b:n  \n\tb -> a [label=\"x -> y\"]\n  \n",
    'a [shape=box,comment="0x1",label="a\nmov eax, ebx\njmp b\n"]\nb [shape=box,label="b"]\na -> b\n',
    "xy\n" * 50 + "a -> b",
    "a -> b -> c\nc->a\n",
    "->\n[shape=\n-> b\n",
    "",
], ids=["crlf", "ports-and-indents", "node-lines", "junk-lines", "chained", "bare-tokens", "empty"])
def test_the_parser_reads_a_graph_as_it_did_before(dot):
    """It reads only the lines that contain "->" or "[shape=", in one scan, instead of
    splitting the text into all of its lines first; those are the only lines either
    regex could match, so the graph is the same, down to the order of its blocks and
    edges, which the order of the loops follows."""
    graph = cfg_explorer_detector.parse_dot_to_graph(dot)
    reference = _reference_parse_dot_to_graph(dot)
    assert list(graph.nodes(data=True)) == list(reference.nodes(data=True))
    assert list(graph.edges()) == list(reference.edges())


def test_the_parser_reads_the_captured_graphs_as_it_did_before(cfg_loop_functions):
    for function_id in FIXTURE_FUNCTION_IDS:
        dot = _dot_for_loops(_reference_dot_graph(cfg_loop_functions[function_id]))
        graph = cfg_explorer_detector.parse_dot_to_graph(dot)
        reference = _reference_parse_dot_to_graph(dot)
        assert list(graph.nodes(data=True)) == list(reference.nodes(data=True)), function_id
        assert list(graph.edges()) == list(reference.edges()), function_id


# --- the body, capped before it is read ---------------------------------------------

#: A cap the tests can cross cheaply; FIND_LOOPS_MAX_BYTES is 32 MiB.
SMALL_CAP = 4096

#: A chunked body: the test client always declares a length, so the declaration is
#: blanked in the environ and the body is marked as framed by the server.
CHUNKED = {"CONTENT_LENGTH": "", "HTTP_TRANSFER_ENCODING": "chunked", "wsgi.input_terminated": True}


@pytest.fixture
def small_cap(app):
    app.config["FIND_LOOPS_MAX_BYTES"] = SMALL_CAP
    return SMALL_CAP


def _post_body(client, body, declared):
    """Posts `body` from a stream the test can ask afterwards how far it was read."""
    stream = io.BytesIO(body)
    environ = {"wsgi.input": stream}
    environ.update({"CONTENT_LENGTH": str(len(body))} if declared else CHUNKED)
    response = client.post("/explore/findLoops/", content_type="text/plain", environ_overrides=environ)
    return response, stream


def test_the_cap_is_32_mib(app):
    assert app.config["FIND_LOOPS_MAX_BYTES"] == 32 * 2**20


def test_a_body_over_the_cap_is_answered_without_being_read(client, as_role, small_cap, monkeypatch):
    """The cost was in the reading: the bounds can only be checked on a parsed graph,
    and MAX_CONTENT_LENGTH lets 1 GiB through. A body declaring more than the cap is
    answered on that declaration alone, as one past the bounds is answered."""
    from werkzeug.wrappers import Request

    reads = []
    real_get_data = Request.get_data
    monkeypatch.setattr(Request, "get_data", lambda self, *args, **kwargs: reads.append(1) or real_get_data(self, *args, **kwargs))
    monkeypatch.setattr(cfg_explorer_detector, "run", lambda dot_content: pytest.fail("the body was analysed"))
    as_role("visitor")

    response, stream = _post_body(client, _fan(1000).encode(), declared=True)

    assert response.status_code == 200
    assert json.loads(response.data) == []
    assert response.headers["X-Loop-Analysis"] == f"skipped; max-bytes={SMALL_CAP}"
    assert stream.tell() == 0, "the body was read"
    assert not reads, "the body was read"


@pytest.mark.parametrize("size", [SMALL_CAP + 1, 64 * SMALL_CAP])
def test_a_body_that_declares_no_length_is_read_no_further_than_the_cap(client, as_role, small_cap, monkeypatch, size):
    """Only reading a chunked body tells its size, so it is read up to one byte past
    the cap, and no further."""
    monkeypatch.setattr(cfg_explorer_detector, "run", lambda dot_content: pytest.fail("the body was analysed"))
    as_role("visitor")

    response, stream = _post_body(client, b"a -> b\n" * (size // 7 + 1), declared=False)

    assert response.status_code == 200
    assert json.loads(response.data) == []
    assert response.headers["X-Loop-Analysis"] == f"skipped; max-bytes={SMALL_CAP}"
    assert stream.tell() <= SMALL_CAP + 1


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "chunked"])
def test_a_body_of_exactly_the_cap_is_analysed(client, as_role, small_cap, declared):
    as_role("visitor")
    dot = _fan(20)
    body = dot.encode().ljust(SMALL_CAP, b"\n")

    response, _ = _post_body(client, body, declared)

    assert response.status_code == 200
    assert response.data.decode() == _reference_run(dot)
    assert json.loads(response.data)
    assert "X-Loop-Analysis" not in response.headers


if __name__ == "__main__":
    unittest.main()
