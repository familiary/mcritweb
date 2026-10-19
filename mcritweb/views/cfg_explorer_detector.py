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
from copy import copy
from functools import reduce

import networkx as nwx


def dominanators(graph, start):
    '''
    Compute all dominators using O(n^2) algorithm
    '''
    # Universal set of nodes
    universal_set = set(graph.nodes())
    # Set of nodes to iterate over
    iter_set = universal_set - set([start])
    # Initial set of dominator nodes
    # if not the start node, then n is dominated by the universal set
    # if the start node, then n is only dominated by n
    doms = {
        n: (
            copy(universal_set)
            if n != start
            else set([start])
        )
        for n in graph.nodes()
    }
    # Iterative algorithm
    changed = True
    while changed:
        changed = False
        for n in iter_set:
            # Union of n and ...
            new_doms = set([n]).union(
                # ... intersection of all current dominators of n's predecessors
                reduce(
                    set.intersection,
                    map(
                        lambda pred: doms[pred],
                        graph.predecessors(n)
                    ),
                    universal_set
                )
            )
            # if changed, changed true and set doms
            if new_doms != doms[n]:
                changed = True
                doms[n] = new_doms
    return doms


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
        graph.add_node("my_super_root", _name="my_super_root")
        for r in roots:
            graph.add_edge("my_super_root", r)
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
    reverse_graph = graph.reverse()
    result = []
    for backedge in backedges:
        work_unit = compute_loops_from_backedges_WorkUnit(backedge=backedge, graph=graph, dominanators=dominanators)
        result.append(compute_loops_from_backedges(work_unit, reverse_graph=reverse_graph))
    return result

def addParentInfo(loopsObj):
    loopsObj.sort(key=lambda x: len(x["nodes"]))
    for i in range(len(loopsObj)):
        loopsObj[i]["parent"] = ""
        for j in range(i + 1, len(loopsObj)):
            header = loopsObj[i]["backedge"][1]
            if header in loopsObj[j]["nodes"]:
                if len(loopsObj[i]["nodes"]) == len(loopsObj[j]["nodes"]):
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
    roots = get_roots(graph)
    assert len(roots) == 1, "Must have exactly one root to perform analysis: {}".format(str(roots))
    root = roots[0]
    dominanator_dict = dominanators(graph, root)
    backedges = compute_backedges(graph, dominanator_dict)
    loops = collect_loops(graph, backedges, dominanator_dict)
    addParentInfo(loops)
    return json.dumps(loops)


def main(file_path):
    dot_content = load_dot_file(file_path)
    # print("dot_content", dot_content)
    graph = parse_dot_to_graph(dot_content)
    # print("graph", graph)
    roots = get_roots(graph)
    assert len(roots) == 1, "Must have exactly one root to perform analysis: {}".format(str(roots))
    root = roots[0]
    # print("root", root)
    dominanator_dict = dominanators(graph, root)
    # print("dominanator_dict", dominanator_dict)
    backedges = compute_backedges(graph, dominanator_dict)
    # print("backedges", backedges)
    loops = collect_loops(graph, backedges, dominanator_dict)
    # print("loops", loops)
    addParentInfo(loops)
    return json.dumps(loops)
