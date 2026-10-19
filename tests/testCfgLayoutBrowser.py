#!/usr/bin/python
"""The vendored dagre's layout, run in a browser on its own.

Before ordering the blocks of each rank, dagre 0.4.5 builds one small graph per rank
(`buildLayerGraphs`). It built each of them with `filterNodes`, which walks every node
and edge of the whole layout graph, so that step cost ranks times the graph's size. On
a real function of 971 blocks it was 12 of the 22 seconds the layout took. The vendored
copy now builds all of them in one pass; these tests hold it to that, and to the layout
the unpatched code gives.

No app is involved: the page is blank and loads `d3.min.js` and `dagre-d3.min.js` from
the source tree. `playwright` is not a dependency of this project and CI does not
install it, so this module skips there rather than failing.
"""

import json
import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

LIB = os.path.join(os.path.dirname(__file__), os.pardir, "mcritweb", "static", "trace_CFG", "lib")

#: buildLayerGraphs' last line as dagre 0.4.5 ships it: a filterNodes per rank
UNPATCHED = "return b.forEach(function(b,c){f[c]=a.filterNodes(d(b))}),f}"

#: A seeded graph shaped like a control flow graph: a chain of blocks, some jumping
#: back (loops) and some forward (branches), with clusters, nested ones among them,
#: when `clusters` is set. Built in the page, laid out by the page's dagre, and
#: answered as every node's and edge's position.
LAYOUT = """([size, seed, clusters]) => {
    let state = seed;
    const random = () => (state = (state * 1103515245 + 12345) % 2147483648) / 2147483648;
    const nodes = [], edges = [];
    for (let i = 0; i < size; i++) {
        nodes.push({id: 'n' + i, value: {label: 'block ' + i,
                    width: 20 + Math.floor(random() * 120), height: 10 + Math.floor(random() * 60)}});
    }
    let count = 0;
    const edge = (u, v) => edges.push({id: 'e' + count++, u: 'n' + u, v: 'n' + v, value: {}});
    if (clusters) {
        // a switch: block 10 jumps to the blocks 11 to 18, which all meet again at 19, so
        // they share a rank; the clusters take some of them that would not sit together
        nodes.push({id: 'c1', value: {}, children: ['n11', 'n14', 'c2']});
        nodes.push({id: 'c2', value: {}, children: ['n16', 'n18']});
        for (let i = 11; i <= 18; i++) { edge(10, i); edge(i, 19); }
    }
    for (let i = 0; i + 1 < size; i++) {
        edge(i, i + 1);
        if (random() < 0.15) { edge(i, Math.floor(random() * size)); }
    }
    const graph = dagreD3.json.decode(nodes, edges, clusters ? 'cdigraph' : 'digraph');
    const out = dagreD3.layout().run(graph);
    const placed = {nodes: {}, edges: {}};
    out.eachNode((u, value) => { placed.nodes[u] = [value.x, value.y, value.width, value.height, value.order, value.rank]; });
    out.eachEdge((e, u, v, value) => { placed.edges[e] = [u, v, value.points]; });
    return JSON.stringify(placed);
}"""

GRAPHS = [(60, 1, False), (250, 2, False), (400, 3, False), (120, 4, True), (300, 5, True)]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            # a Chromium that playwright did not install itself is named through MCRITWEB_CHROMIUM
            browser = playwright.chromium.launch(executable_path=os.environ.get("MCRITWEB_CHROMIUM") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium for playwright to drive: {error}")
        try:
            yield browser
        finally:
            browser.close()


def dagre_page(browser, dagre_source):
    page = browser.new_page()
    page.set_content("<html><body></body></html>")
    page.add_script_tag(path=os.path.join(LIB, "d3.min.js"))
    page.add_script_tag(content=dagre_source)
    return page


def vendored_dagre():
    with open(os.path.join(LIB, "dagre-d3.min.js")) as source:
        return source.read()


def unpatched_dagre():
    """The vendored dagre with buildLayerGraphs' last line as dagre 0.4.5 has it."""
    source = vendored_dagre()
    start = source.index("/* mcritweb: the layer graphs")
    end = source.index(",f}", start) + len(",f}")
    unpatched = source[:start] + UNPATCHED + source[end:]
    assert unpatched.count(UNPATCHED) == 1 and len(unpatched) < len(source)
    return unpatched


def test_the_layer_graphs_are_built_in_one_pass(browser):
    """How many nodes `filterNodes` walks while a 400-block graph is laid out. With the
    whole layout graph walked once per rank it was about 15 million; what is left is
    dagre's other uses of it, on the small per-rank graphs, about 38 thousand."""
    page = dagre_page(browser, vendored_dagre())
    walked = page.evaluate("""layout => {
        let walked = 0;
        const filterNodes = dagreD3.Digraph.prototype.filterNodes;
        dagreD3.Digraph.prototype.filterNodes = function () {
            walked += this.order();
            return filterNodes.apply(this, arguments);
        };
        eval(layout)([400, 3, false]);
        return walked;
    }""", LAYOUT)

    assert walked < 100000


@pytest.mark.parametrize("size,seed,clusters", GRAPHS)
def test_the_layout_is_the_one_the_unpatched_code_gives(browser, size, seed, clusters):
    """Every node's position, size, rank and order and every edge's points are the same
    as with the unpatched buildLayerGraphs. Without clusters the layer graphs are the
    ones filterNodes built; with clusters, whose members filterNodes also re-parented
    within each layer, the layout still comes out the same."""
    patched = json.loads(dagre_page(browser, vendored_dagre()).evaluate(LAYOUT, [size, seed, clusters]))
    unpatched = json.loads(dagre_page(browser, unpatched_dagre()).evaluate(LAYOUT, [size, seed, clusters]))

    assert len(patched["nodes"]) >= size
    assert patched == unpatched
