"""
MIT License

Copyright (c) 2021 hdc-arizona

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import re
from collections import namedtuple

import networkx as nwx

#: Bounds on what one posted graph may cost. immediate_dominators is quadratic on a
#: graph built for it (a chain whose end jumps to a leaf of every chain block): 1.7 s
#: at 8000 blocks, 8.6 s at 16000. The loop bodies are the answer itself, and a graph
#: where every block jumps back to the entry makes one that grows with the square of
#: its blocks: 15 MB for 2000. The largest functions of the test corpus (971 blocks,
#: 1539 edges) have at most 86 loops and 3340 body entries in all.
MAX_BLOCKS = 8000
MAX_EDGES = 16000
MAX_BACK_EDGES = 2000
MAX_LOOP_BODY_ENTRIES = 200000
#: "equal" lists, past which addParentInfo stops recording them. Loops of one length
#: that share a header each list the later ones, so a flattened dispatcher with a few
#: thousand such loops would make an answer the square of that; the page does not read
#: them (loopCollapser.js only has a TODO for it), so they are cut, not the loops.
MAX_EQUAL_ENTRIES = 200000


class LoopAnalysisTooLarge(Exception):
    pass


class DominatorSets:
    '''
    The dominators of every node, as dominanators() answers them: `a in doms[b]`
    is True when every path from the start node to b passes through a.

    Answered from the dominator tree rather than stored as one set per node. The
    iterative set intersection this replaces held n sets of up to n nodes and
    recomputed each of them on every pass, which a posted graph of 2000 blocks
    could keep busy for minutes; the tree gives the same answer from two numbers
    per node.
    '''

    def __init__(self, graph, start):
        # networkx before 3.6 lists the start node as its own immediate dominator,
        # later versions leave it out; neither makes it a child of anything
        idom = nwx.immediate_dominators(graph, start)
        children = {}
        for node, parent in idom.items():
            if parent is not None and node != parent:
                children.setdefault(parent, []).append(node)
        # a node dominates another when it encloses it in a depth-first walk of
        # the tree: entered before it and left after it
        self._entered, self._left = {}, {}
        clock = 0
        stack = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                self._left[node] = clock
            else:
                self._entered[node] = clock
                stack.append((node, True))
                stack.extend((child, False) for child in children.get(node, ()))
            clock += 1

    def dominates(self, dominator, node):
        if node not in self._entered:
            # not reachable from the start node: the iterative algorithm never
            # narrowed such a node's set from "every node", so neither does this
            return True
        if dominator not in self._entered:
            return False
        return (self._entered[dominator] <= self._entered[node]
                and self._left[node] <= self._left[dominator])

    def __getitem__(self, node):
        return _DominatorsOf(self, node)


class _DominatorsOf:
    def __init__(self, dominator_sets, node):
        self._dominator_sets = dominator_sets
        self._node = node

    def __contains__(self, dominator):
        return self._dominator_sets.dominates(dominator, self._node)


def dominanators(graph, start):
    '''
    Compute all dominators, from the dominator tree (see DominatorSets)
    '''
    return DominatorSets(graph, start)


def load_dot_file(file_path):
    dot_content = ""
    with open(file_path) as fin:
        dot_content = fin.read()
    return dot_content


def parse_dot_to_graph(dot_content):
    '''
    'Parse' dot file (by collecting edges) into a networkx graph object
    '''
    # Regex to capture edges ("node_a -> node_b")
    # edge_rx = re.compile( r"^\s*(?P<src>[\w_][\w_\d]*)\s*\-\>\s*(?P<tgt>[\w_][\w_\d]*)\s*\[.*?label=\"\s*ct\s*:\s*(?P<count>\d+)\s*\".*\]\s*;\s*$" )
    # edge_rx = re.compile( r"^\s*(?P<src>[\w_][\w_\d]*)\s*\-\>\s*(?P<tgt>[\w_][\w_\d]*)" )
    edge_rx = re.compile(
        r"^\s*(?P<src>[\w_][\w_\d]*)(:[\w]*)?\s*\-\>\s*(?P<tgt>[\w_][\w_\d]*)(:[\w]*)?")
    node_rx = re.compile(
        r"^\s*(?P<node>[\w_][\w_\d]*)(:[\w]*)?\s*\[shape\=\w+(,comment=\".*\")?,label\=")
    graph = nwx.DiGraph()
    # For each file
    for line in dot_content.split("\n"):
        if line:
            # Try and match node names - doesn't matter if they are added multiple times as long as they have the same identifier
            match = node_rx.match(line)
            if match is not None:
                # Add edge in graph
                graph.add_node(match.group("node"), _name=match.group("node"))
            # Try and match edges
            match = edge_rx.match(line)
            if match is not None:
                # Add edge in graph
                graph.add_node(match.group("src"), _name=match.group("src"))
                graph.add_node(match.group("tgt"), _name=match.group("tgt"))
                graph.add_edge(match.group("src"), match.group("tgt"))
                continue
    return graph


def get_roots(graph):
    '''
    Get roots (nodes with no incomming edges) of a graph
    IF there are more than one roots, then create a superroot
    and add edges from superroot to the roots
    '''
    roots = list(set(graph.nodes()) -
                 set(map(lambda twople: twople[1], graph.edges())))
    if len(roots) > 1:
        # a block of that name would be one more root's parent instead of a new root
        super_root = "my_super_root"
        while super_root in graph:
            super_root += "_"
        graph.add_node(super_root, _name=super_root)
        for r in roots:
            graph.add_edge(super_root, r)
    # only ever case we noted like this so far is if we somehow loop to our entry block
    # in that case, we return the block with lowest address as entry node
    elif len(roots) == 0:
        return [min(graph.nodes())]

    return list(set(graph.nodes()) -
                set(map(lambda twople: twople[1], graph.edges())))


def compute_backedges(graph, dominators):
    '''
    Compute the backedges of a graph, given already computed dominators
    '''
    return list(
        filter(
            lambda src_tgt: src_tgt[1] in dominators[src_tgt[0]],
            graph.edges()
        )
    )


'''
Collect list of loops.
Returns a list of dictionaries, each with two entries:
+ backedge: backedge that defines the loop
+ nodes: collection of nodes that compose the entire loop
'''

'''
compute_loops_from_backedges_WorkUnit
Type that wraps a work unit for the compute_loops_from_backedges task.
'''
compute_loops_from_backedges_WorkUnit = namedtuple(
    "compute_loops_from_backedges_WorkUnit", [
        "backedge", "graph", "dominanators"])


def compute_loops_from_backedges(work_unit, reverse_graph=None):
    backedge = work_unit.backedge
    graph = work_unit.graph
    dominanators = work_unit.dominanators
    return {
        "backedge": backedge,
        "nodes":
        # Filter out nodes where the target of the backedge is not in its
        # dominator set
        list(filter(
            lambda node: backedge[1] in dominanators[node],
            getNodes(graph, backedge, reverse_graph=reverse_graph)
        ))
    }


def getNodes(graph, backedge, reverse_graph=None):
    '''
    Nodes reachable from backedge[0] without passing through the loop header
    backedge[1], in the reversed graph - i.e. the body of the loop this backedge
    closes.

    collect_loops computes the reversed graph once for the whole set of backedges
    and passes it in as `reverse_graph`, since it does not depend on which backedge
    is being resolved. A caller with only one backedge can omit it and this builds
    its own, as before.
    '''
    if backedge[0] == backedge[1]:
        return [backedge[0]]
    if reverse_graph is None:
        reverse_graph = graph.reverse()
    # exclude the header by viewing it out rather than removing it, so a
    # reverse_graph shared across backedges is never mutated
    header = backedge[1]
    reachable = nwx.subgraph_view(reverse_graph, filter_node=lambda node: node != header)
    nodeList = list(nwx.dfs_preorder_nodes(reachable, backedge[0]))
    nodeList.append(header)
    return nodeList


def collect_loops(graph, backedges, dominanators):
    '''
    Computes the loop each backedge defines: the backedge itself and the nodes that
    make up its body.

    A plain loop, not farmed out to a pool - nothing here runs in parallel. Every
    backedge shares the same graph and dominator sets, so the one thing worth doing
    once for all of them is the reversed graph getNodes needs for its reachability
    search; that is built here and passed to each call instead of being rebuilt from
    the same graph for every backedge.
    '''
    if len(backedges) > MAX_BACK_EDGES:
        raise LoopAnalysisTooLarge(f"{len(backedges)} back edges")
    reverse_graph = graph.reverse()
    result = []
    entries = 0
    for backedge in backedges:
        work_unit = compute_loops_from_backedges_WorkUnit(backedge=backedge, graph=graph, dominanators=dominanators)
        result.append(compute_loops_from_backedges(work_unit, reverse_graph=reverse_graph))
        entries += len(result[-1]["nodes"])
        if entries > MAX_LOOP_BODY_ENTRIES:
            raise LoopAnalysisTooLarge(f"more than {MAX_LOOP_BODY_ENTRIES} loop body entries")
    return result

def addParentInfo(loopsObj):
    equal_entries = 0
    loopsObj.sort(key=lambda x: len(x["nodes"]))
    # membership in a set rather than a scan of the node list; same answer
    node_sets = [set(loop["nodes"]) for loop in loopsObj]
    for i in range(len(loopsObj)):
        loopsObj[i]["parent"] = ""
        for j in range(i + 1, len(loopsObj)):
            header = loopsObj[i]["backedge"][1]
            if header in node_sets[j]:
                if len(loopsObj[i]["nodes"]) == len(loopsObj[j]["nodes"]):
                    if equal_entries >= MAX_EQUAL_ENTRIES:
                        continue
                    equal_entries += 1
                    if "equal" in loopsObj[i]:
                        loopsObj[i]["equal"].append(j)
                    else:
                        loopsObj[i]["equal"] = [j]
                else:
                    loopsObj[i]["parent"] = j
                    break


def run(dot_content):
    # print(dot_content)
    graph = parse_dot_to_graph(dot_content)
    # a graph without blocks has no loops, and get_roots() cannot take min() of no nodes
    if graph.number_of_nodes() == 0:
        return "[]"
    if graph.number_of_nodes() > MAX_BLOCKS or graph.number_of_edges() > MAX_EDGES:
        raise LoopAnalysisTooLarge(f"{graph.number_of_nodes()} blocks, {graph.number_of_edges()} edges")
    roots = get_roots(graph)
    assert len(roots) == 1, "Must have exactly one root to perform analysis: {}".format(str(roots))
    root = roots[0]
    dominanator_dict = dominanators(graph, root)
    backedges = compute_backedges(graph, dominanator_dict)
    loops = collect_loops(graph, backedges, dominanator_dict)
    addParentInfo(loops)
    return json.dumps(loops)


def main(file_path):
    return run(load_dot_file(file_path))
