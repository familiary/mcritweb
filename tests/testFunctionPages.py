#!/usr/bin/python
"""The function page and the function comparison page, rendered against the corpus.

Issue #34 asked the function page for an accordion, a MinHash indicator, an analyze
button, the shingles and the API usage; issue #74 asked the comparison page for
synchronised graphs and a combined view. The graphs are drawn in the browser, so what
can be asserted offline is what the browser is given: the page markup, the match data
`functiondiff` computes, and the combined dot graph it serves.

`functions_reference_<id>` fixtures keep their `xcfg`, which is what the block
comparison needs; `functions_matched` has it dropped, so a pair from there dies in
`toSmdaFunction` - this is why the pairs below come from the reference pools.
"""

import copy
import json
import logging
import re
import unittest

import pytest
from cfgColors import CFG_EDGES, CFG_FILLS, LIGHT_CFG_COLORS, blend, contrast, custom_properties, dark_value, hex_to_rgb, script_palette

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: two functions from different reference samples with the same PicHash
IDENTICAL_PAIR = (98, 882)
#: two functions of the same family that differ - the pair the diff has to work for
DIFFERENT_PAIR = (98, 883)
#: a reference function with more than one block
MULTI_BLOCK_FUNCTION = 6


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def diff_of(app, function_id_a, function_id_b):
    from mcritweb.views.functiondiff import get_function_diff
    with app.test_request_context("/"):
        return get_function_diff(function_id_a, function_id_b)


# --- the function page (#34) -------------------------------------------------------

def test_the_function_page_renders_its_sections(client, as_role):
    as_role("visitor")
    response = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}")
    assert response.status_code == 200
    page = response.data.decode()
    for heading in ("Function Info", "MinHash &amp; Shingles", "Basic Blocks &amp; PicBlockHashes", "Control Flow Graph"):
        assert heading in page, f"section {heading!r} missing"
    assert 'class="accordion' in page
    # the API usage is behind a button, not in the flow of the page
    assert 'id="apiUsageModal"' in page


def test_the_function_page_offers_the_analyze_actions(client, as_role, fake_mcrit):
    as_role("visitor")
    response = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}")
    page = response.data.decode()
    entry = fake_mcrit._functions[MULTI_BLOCK_FUNCTION]
    assert "Compare with function" in page
    assert f"/data/matches/function/{MULTI_BLOCK_FUNCTION}/0" in page
    assert f"pichash%3A0x{entry.pichash:016x}" in page or f"pichash:0x{entry.pichash:016x}" in page
    assert f"/analyze/cross_compare?samples={entry.sample_id}" in page
    assert f"/explore/samples/{entry.sample_id}" in page


def test_the_function_page_says_whether_a_minhash_exists(client, as_role, fake_mcrit):
    as_role("visitor")
    entry = fake_mcrit._functions[MULTI_BLOCK_FUNCTION]
    assert len(entry.minhash) > 0, "the fixture function is expected to carry a MinHash"
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert "MinHash available" in page
    assert entry.minhash.hex() in page
    # every block the backend hashed is listed
    for block in entry.picblockhashes:
        assert f"0x{block['hash']:016x}" in page


def test_the_function_page_documents_the_api_without_leaking_the_token(client, as_role, make_user):
    as_role("visitor")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").data.decode()
    assert f"/api/functions/{MULTI_BLOCK_FUNCTION}" in page
    assert f"/api/functions/{MULTI_BLOCK_FUNCTION}?with_xcfg=true" in page
    assert "/api/query/pichash/" in page
    assert "/api//" not in page, "the placeholder path segment leaked into the API base"
    # make_user gives every account a predictable token, and it must not be on the page
    assert "apitoken-visitor" not in page
    assert "your API token" in page


def test_an_unknown_function_still_redirects(client, as_role):
    as_role("visitor")
    response = client.get("/explore/functions/987654321")
    assert response.status_code == 302


# --- the block comparison (#74) ----------------------------------------------------

def test_identical_functions_match_block_for_block(app):
    from mcritweb.views.functiondiff import COLOR_UNMATCHED
    diff = diff_of(app, *IDENTICAL_PAIR)
    assert len(diff["node_colors"]["a"]) == len(diff["node_colors"]["b"])
    unmatched = [node for node, color in diff["node_colors"]["a"].items() if color == COLOR_UNMATCHED]
    assert unmatched == []
    # one partner per block, both ways
    assert len(diff["pairs"]) == len(diff["node_colors"]["a"])
    assert len({a for a, _ in diff["pairs"]}) == len(diff["pairs"])
    assert len({b for _, b in diff["pairs"]}) == len(diff["pairs"])


def test_node_matches_are_consistent_with_the_pairs(app):
    from mcritweb.views.functiondiff import COLOR_UNMATCHED
    diff = diff_of(app, *DIFFERENT_PAIR)
    for offset_a, offset_b in diff["pairs"]:
        assert f"Node0x{offset_b:x}" in diff["node_matches"]["a"][f"Node0x{offset_a:x}"]
        assert f"Node0x{offset_a:x}" in diff["node_matches"]["b"][f"Node0x{offset_b:x}"]
    # a matched block is never drawn in the unmatched colour
    for node in diff["node_matches"]["a"]:
        assert diff["node_colors"]["a"][node] != COLOR_UNMATCHED


def test_the_combined_graph_holds_both_functions(app, fake_mcrit):
    from mcritweb.views.functiondiff import get_combined_dot_graph
    with app.test_request_context("/"):
        dot = get_combined_dot_graph(*DIFFERENT_PAIR)
    function_a = fake_mcrit._functions[DIFFERENT_PAIR[0]].toSmdaFunction()
    function_b = fake_mcrit._functions[DIFFERENT_PAIR[1]].toSmdaFunction()
    diff = diff_of(app, *DIFFERENT_PAIR)
    paired_b = {offset_b for _, offset_b in diff["pairs"]}
    # every block of A is a node under its own id
    for block in function_a.getBlocks():
        assert re.search(rf"^  Node0x{block.offset:x} \[", dot, re.MULTILINE), f"block 0x{block.offset:x} of A missing"
    # every block of B is either merged into A's node or drawn as a B-only node
    for block in function_b.getBlocks():
        if block.offset in paired_b:
            assert f"| B 0x{block.offset:x}" in dot
        else:
            assert re.search(rf"^  NodeB0x{block.offset:x} \[", dot, re.MULTILINE), f"block 0x{block.offset:x} of B missing"
    # every edge of A is there under A's ids, tagged with the side(s) that have it
    for source, targets in function_a.blockrefs.items():
        for target in targets:
            assert re.search(rf"^  Node0x{source:x} -> Node0x{target:x} \[.*side=\"a?b?\"", dot, re.MULTILINE)
    # the format main_duo.js parses: record nodes with \l-separated lines
    assert "shape=record" in dot
    assert r"\l" in dot


def test_the_combined_graph_of_identical_functions_has_no_single_sided_parts(app):
    from mcritweb.views.functiondiff import get_combined_dot_graph
    with app.test_request_context("/"):
        dot = get_combined_dot_graph(*IDENTICAL_PAIR)
    assert "NodeB" not in dot
    assert 'side="a"' not in dot
    assert 'side="b"' not in dot


# --- the comparison page and its routes ---------------------------------------------

def test_the_comparison_page_renders_with_the_match_data(client, as_role):
    as_role("visitor")
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    assert response.status_code == 200
    page = response.data.decode()
    assert "Side by side" in page and "Combined" in page
    assert 'id="syncGraphs"' in page
    assert f"/explore/fetchCombinedDotGraph/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}" in page
    assert f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}/diff" in page
    # the script itself comes with the rest of the graph viewer, once the graphs are wanted
    assert re.search(r'"/static/function_compare\.js(\?v=\w+)?"', page)
    assert f"/api/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}" in page


def test_the_comparison_graphs_name_the_layout_worker(client, as_role):
    """Each graph is laid out in the worker its svg names (`layOutGraph()` in main_duo.js);
    without one the page lays them out itself, as it did before."""
    import os
    as_role("visitor")
    page = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}").get_data(as_text=True)
    for panel in ("a", "b", "c"):
        svg = re.search(rf'<svg id="graphContainer_{panel}"[^>]*>', page)
        assert svg and re.search(r'data-layout-worker="/static/trace_CFG/layout_worker\.js(\?v=\w+)?"', svg.group(0)), panel
    assert os.path.isfile(os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static", "trace_CFG", "layout_worker.js"))


def test_the_comparison_graphs_are_drawn_from_the_worker_s_layout():
    """The two graphs side by side and the combined one are drawn from the layout
    `layOutGraph()` calls back with; a refresh of main_duo.js from upstream would lay them out
    on the page again. The browser tests (testFunctionVsBrowser.py) check the drawing itself."""
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static")
    with open(os.path.join(static, "trace_CFG", "main_duo.js")) as f:
        main_duo = f.read()
    with open(os.path.join(static, "function_compare.js")) as f:
        function_compare = f.read()
    for panel in ("a", "b"):
        assert re.search(rf'layOutGraph\(cfgPanels\.{panel}\.rendered, cfgPanels\.{panel}\.container, function\(laidOut\) \{{\s*'
                         rf'showGraph\("{panel}", node_colors, laidOut\);', main_duo), panel
    assert "renderer.layout({ run: function() { return laidOut; } });" in main_duo
    assert re.search(r'layOutGraph\(graph, "#graphContainer_c", function \(laidOut\) \{\s*onceCombinedShown\(function \(\) \{'
                     r'[^}]*renderCombined\(graph, laidOut\);', function_compare)
    assert "renderer.layout({ run: function () { return laidOut; } });" in function_compare


def test_the_comparison_page_asks_for_the_pair_without_checking_the_ids_first(client, as_role, fake_mcrit):
    """The backend checks both ids when it is asked for the comparison, so two
    `isFunctionId` requests ahead of it only repeated that check (issue #207)."""
    as_role("visitor")
    fake_mcrit.calls.clear()
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    assert response.status_code == 200
    called = [name for name, _, _ in fake_mcrit.calls]
    assert "isFunctionId" not in called
    assert called.count("getMatchFunctionVs") == 1


def test_the_comparison_page_leaves_the_block_data_to_its_viewer(client, as_role, fake_mcrit):
    """The colours and matches are an entry per block and per match, and only the graph
    viewer reads them: the page neither carries them nor computes them, the viewer fetches
    them from match_functions_diff once the graphs are wanted."""
    as_role("visitor")
    fake_mcrit.calls.clear()
    page = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}").data.decode()
    assert "Node0x" not in page
    assert _diff_calls(fake_mcrit) == []


def test_the_block_data_route_answers_the_comparison(client, as_role, app):
    from mcritweb.views.functiondiff import get_function_diff
    as_role("visitor")
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}/diff")
    assert response.status_code == 200 and response.mimetype == "application/json"
    with app.test_request_context("/"):
        diff = get_function_diff(*DIFFERENT_PAIR)
    assert response.get_json() == {"node_colors": diff["node_colors"], "node_matches": diff["node_matches"]}
    assert any(response.get_json()["node_matches"]["a"].values())


def test_the_block_data_route_answers_404_for_an_unknown_function(client, as_role):
    as_role("visitor")
    response = client.get("/data/matches/function/98/987654321/diff")
    assert response.status_code == 404
    # the route's own answer, not the 404 of a route that is not there
    assert response.get_json() == {"error": "One of the function_ids is not valid."}


class _UnansweringClient:
    """A backend that fails the function lookups: the client answers None for a 500."""

    def __init__(self, corpus):
        self._corpus = corpus

    def getFunctionById(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._corpus, name)


def test_the_block_data_route_answers_404_when_the_backend_does_not_give_the_functions(client, as_role, app, fake_mcrit):
    """The page was rendered from both functions, so this is a failure the viewer has to
    report, not a comparison with nothing in it."""
    as_role("visitor")
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _UnansweringClient(fake_mcrit)
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}/diff")
    assert response.status_code == 404
    assert response.get_json() == {"error": "One of the function_ids is not valid."}


def test_the_comparison_page_reports_an_unknown_function_id(client, as_role):
    as_role("visitor")
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/999999")
    assert response.status_code == 200
    assert b"One of the function_ids is not valid" in response.data


class _ComparisonFailingClient:
    """A backend that knows both functions but fails the comparison itself: the
    client answers None for a 500 just as it does for a 404."""

    def __init__(self, corpus):
        self._corpus = corpus

    def getMatchFunctionVs(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._corpus, name)


def test_the_comparison_page_reports_a_failed_comparison_instead_of_500ing(client, as_role, app, fake_mcrit):
    """Both ids passed `isFunctionId`, so a None from the comparison went on into
    `match_info["function_entry_a"]` and took the page down."""
    as_role("visitor")
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _ComparisonFailingClient(fake_mcrit)
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    assert response.status_code == 200
    assert b"One of the function_ids is not valid" in response.data


def test_the_comparison_page_flags_a_pichash_match(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/matches/function/{IDENTICAL_PAIR[0]}/{IDENTICAL_PAIR[1]}").data.decode()
    assert "PicHash</span>" in page


def test_the_combined_graph_route_serves_dot(client, as_role):
    as_role("visitor")
    response = client.get(f"/explore/fetchCombinedDotGraph/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    assert response.status_code == 200
    assert response.data.startswith(b'digraph "Combined CFG')


def test_the_combined_graph_route_answers_empty_for_an_unknown_function(client, as_role):
    as_role("visitor")
    response = client.get("/explore/fetchCombinedDotGraph/98/987654321")
    assert response.status_code == 200
    assert response.data == b""


def test_the_single_graph_route_still_serves_dot(client, as_role):
    as_role("visitor")
    response = client.get(f"/explore/fetchDotGraph/{MULTI_BLOCK_FUNCTION}")
    assert response.status_code == 200
    assert response.data.startswith(b'digraph "CFG')


@pytest.mark.parametrize("path", ["/explore/fetchDotGraph/84", "/explore/fetchCombinedDotGraph/84/943"])
def test_a_graph_carrying_markup_is_not_served_as_a_page(client, as_role, corpus_mcrit, path):
    """An API name is copied out of the sample's import table into the dot text, so a
    sample can name an import `<img src=x onerror=...>`. Served as Flask's default
    text/html, opening the route as a page ran it; the CFG viewers read it with d3.xhr
    and do not care about the type."""
    corpus_mcrit._functions[84].xcfg["apirefs"]["4257248"] = "gdi32.dll!<img src=x onerror=alert(1)>"
    as_role("visitor")
    response = client.get(path)

    assert response.status_code == 200
    assert b"<img src=x onerror=alert(1)>" in response.data, "the fixture no longer reaches the graph"
    assert response.mimetype == "text/plain"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/login", "/static/autocomplete.js"])
def test_every_response_forbids_content_sniffing(client, path):
    assert client.get(path).headers["X-Content-Type-Options"] == "nosniff"



# --- the guarantees the review asked for ---------------------------------------------

def test_a_matched_colour_always_has_a_partner(app):
    """A block wearing a match colour is in node_matches; nothing is coloured as
    matched while pointing at nothing (a later layer may re-match only one end of
    an earlier pair)."""
    from mcritweb.views.functiondiff import COLOR_UNMATCHED
    diff = diff_of(app, *DIFFERENT_PAIR)
    for side in ("a", "b"):
        for node, color in diff["node_colors"][side].items():
            if color != COLOR_UNMATCHED:
                assert node in diff["node_matches"][side], f"{side}:{node} is coloured {color} but has no partner"
            else:
                assert node not in diff["node_matches"][side]


def test_the_combined_graph_carries_b_code_only_where_it_differs(app):
    """Identical functions live at different addresses, and addresses alone must not
    count as a difference - otherwise every block would claim one."""
    from mcritweb.views.functiondiff import get_combined_dot_graph
    with app.test_request_context("/"):
        dot = get_combined_dot_graph(*IDENTICAL_PAIR)
    assert re.search(r'comment="[^"]+"', dot) is None


def test_an_a_block_without_a_partner_is_drawn_a_only_in_red(app, fake_mcrit):
    from mcritweb.views.functiondiff import COLOR_UNMATCHED, _compute_function_diff, build_combined_dot_graph
    with app.test_request_context("/"):
        diff = _compute_function_diff(*DIFFERENT_PAIR)
    smda_a, smda_b = diff["smda_functions"]
    # take the pairing away from the first block: it must then be red and "only"
    first_offset = smda_a.getBlocks()[0].offset
    pairs = [pair for pair in diff["pairs"] if pair[0] != first_offset]
    dot = build_combined_dot_graph(smda_a, smda_b, pairs, diff["node_colors"])
    line = next(line for line in dot.splitlines() if line.startswith(f"  Node0x{first_offset:x} ["))
    assert f'fillcolor="{COLOR_UNMATCHED}"' in line
    assert f"A 0x{first_offset:x} only" in line


class _DroppedDisassemblyClient:
    """A backend with STORAGE_DROP_DISASSEMBLY: entries come back with xcfg == {}."""

    def __init__(self, corpus):
        self._corpus = corpus

    def getFunctionById(self, function_id, *args, **kwargs):
        entry = self._corpus.getFunctionById(function_id, *args, **kwargs)
        if entry is not None:
            # a copy: the corpus hands out the entries it keeps, and the next client is a
            # backend that still has the disassembly
            entry = copy.copy(entry)
            entry.xcfg = {}
        return entry

    def __getattr__(self, name):
        return getattr(self._corpus, name)


def test_dropped_disassembly_yields_an_empty_diff_not_a_server_error(app, fake_mcrit):
    from mcritweb.views.functiondiff import get_combined_dot_graph, get_function_diff
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _DroppedDisassemblyClient(fake_mcrit)
    with app.test_request_context("/"):
        diff = get_function_diff(*DIFFERENT_PAIR)
        assert diff["pairs"] == [] and diff["node_matches"] == {"a": {}, "b": {}}
        assert get_combined_dot_graph(*DIFFERENT_PAIR) == ""


def test_the_comparison_page_survives_dropped_disassembly(client, as_role, app, fake_mcrit):
    as_role("visitor")
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _DroppedDisassemblyClient(fake_mcrit)
    response = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    assert response.status_code == 200

def test_a_large_bucket_of_identical_blocks_is_not_a_cartesian_product():
    """1000 identical blocks on either side must not become a million pairs, and
    every block must still have a partner (pure-python helper, no app needed)."""
    from mcritweb.views.functiondiff import RANK_WINDOW, _pair_by_hash
    hashes_a = [{"offset": 0x1000 + 16 * i, "hash": 0xABCD} for i in range(1000)]
    hashes_b = [{"offset": 0x9000 + 16 * i, "hash": 0xABCD} for i in range(1000)]
    pairs = _pair_by_hash(hashes_a, hashes_b)
    assert len(pairs) <= (2 * RANK_WINDOW + 1) * 2000
    assert {a for a, _ in pairs} == {entry["offset"] for entry in hashes_a}
    assert {b for _, b in pairs} == {entry["offset"] for entry in hashes_b}
    # blocks at the same rank find each other
    assert (0x1000 + 16 * 500, 0x9000 + 16 * 500) in pairs
    # a small bucket is still paired completely
    small = _pair_by_hash(hashes_a[:3], hashes_b[:5])
    assert len(small) == 15


def test_the_combined_graph_route_validates_the_ids(client, as_role, fake_mcrit):
    as_role("visitor")
    fake_mcrit.calls.clear()
    response = client.get("/explore/fetchCombinedDotGraph/98/987654321")
    assert response.status_code == 200 and response.data == b""
    assert not any(name == "getFunctionById" for name, _, _ in fake_mcrit.calls)


# --- the comparison is computed once (#186) -----------------------------------------

def _diff_calls(fake_mcrit, names=("getFunctionById", "getSampleById")):
    """The backend calls only the comparison makes: the xcfg fetches and the two
    samples layer 2 reads. The page's own getMatchFunctionVs is not among them."""
    return [name for name, _, _ in fake_mcrit.calls if name in names]


def _diff_url(function_id_a, function_id_b):
    """Where the comparison page's viewer fetches the colours and matches."""
    return f"/data/matches/function/{function_id_a}/{function_id_b}/diff"


def _colors_of(response):
    """The colours and the matches a fetch of the comparison answered, each as JSON text."""
    assert response.status_code == 200
    answer = response.get_json()
    return json.dumps(answer["node_colors"]), json.dumps(answer["node_matches"])


def test_a_repeated_comparison_is_not_recomputed(client, as_role, fake_mcrit):
    as_role("visitor")
    url = _diff_url(*DIFFERENT_PAIR)
    first = _colors_of(client.get(url))
    assert "getSampleById" in _diff_calls(fake_mcrit), "the first fetch has to compute the comparison"
    fake_mcrit.calls.clear()
    second = _colors_of(client.get(url))
    # the two entries are fetched for the memo key, and nothing else
    assert _diff_calls(fake_mcrit) == ["getFunctionById", "getFunctionById"]
    assert second == first


def test_the_combined_graph_reuses_the_comparison_the_viewer_fetched(client, as_role, app, fake_mcrit):
    from mcritweb.views.functiondiff import build_combined_dot_graph, get_function_diff
    as_role("visitor")
    client.get(_diff_url(*DIFFERENT_PAIR))
    url = f"/explore/fetchCombinedDotGraph/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    for _ in range(2):
        fake_mcrit.calls.clear()
        dot = client.get(url).data.decode()
        # the two entries are fetched for the memo key, and nothing else
        assert _diff_calls(fake_mcrit) == ["getFunctionById", "getFunctionById"]
    # and it is the graph drawn from the comparison's own colours
    with app.test_request_context("/"):
        diff = get_function_diff(*DIFFERENT_PAIR)
    smda_a, smda_b = (fake_mcrit._functions[function_id].toSmdaFunction() for function_id in DIFFERENT_PAIR)
    assert dot == build_combined_dot_graph(smda_a, smda_b, diff["pairs"], diff["node_colors"])


def test_recalculated_picblockhashes_give_fresh_colours(client, as_role, fake_mcrit):
    """mcrit's recalculateAllPicHashes rewrites the stored PicBlockHashes in place,
    under the same function ids - layer 3 then colours differently."""
    from mcritweb.views.functiondiff import COLOR_FULL_PICBLOCK_MATCH, node_id
    as_role("visitor")
    url = _diff_url(*DIFFERENT_PAIR)
    first = _colors_of(client.get(url))
    # the recalculation now finds the first blocks of both functions to be equal
    first_blocks = []
    for function_id in DIFFERENT_PAIR:
        entry = fake_mcrit._functions[function_id]
        offset = entry.toSmdaFunction().getBlocks()[0].offset
        first_blocks.append(node_id(offset))
        entry.picblockhashes = [{"offset": offset, "hash": 0x186, "size": 4}]
    second = _colors_of(client.get(url))
    assert second != first
    colors = json.loads(second[0])
    assert colors["a"][first_blocks[0]] == colors["b"][first_blocks[1]] == COLOR_FULL_PICBLOCK_MATCH


def test_a_dropped_disassembly_is_not_drawn_from_memory(client, as_role, app, fake_mcrit):
    """Once the backend dropped the xcfg (STORAGE_DROP_DISASSEMBLY), the combined graph
    answers empty, as it did before there was a memo."""
    as_role("visitor")
    url = f"/explore/fetchCombinedDotGraph/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    assert client.get(url).data.startswith(b'digraph "Combined CFG')
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _DroppedDisassemblyClient(fake_mcrit)
    assert client.get(url).data == b""
    assert _colors_of(client.get(_diff_url(*DIFFERENT_PAIR)))[0] == '{"a": {}, "b": {}}'


def test_layer_4_tells_a_jz_from_a_jnz():
    """Layer 1 compares mnemonic groups, so a jz and a jnz are the same instruction to
    it; the edit distance of layer 4 compares the mnemonics themselves."""
    from mcritweb.views.functiondiff import _escaped_blocks, _escaped_pairs, _levenshtein_pairs

    class Instruction:
        def __init__(self, mnemonic, operands):
            self.mnemonic, self.operands = mnemonic, operands

    class Block:
        def __init__(self, offset, instructions):
            self.offset, self._instructions = offset, [Instruction(*instruction) for instruction in instructions]

        def getInstructions(self):
            return self._instructions

    class Function:
        def __init__(self, *blocks):
            self._blocks = blocks

        def getBlocks(self):
            return list(self._blocks)

    escaped_a = _escaped_blocks(Function(Block(0x10, [("cmp", "eax, 1"), ("jz", "0x20")]), Block(0x20, [("ret", "")])))
    escaped_b = _escaped_blocks(Function(Block(0x110, [("cmp", "eax, 1"), ("jnz", "0x120")]), Block(0x120, [("ret", "")])))
    assert (0x10, 0x110) in _escaped_pairs(escaped_a, escaped_b)
    unmatched = {"a": [0x10, 0x20], "b": [0x110, 0x120]}
    assert _levenshtein_pairs(escaped_a, escaped_b, unmatched) == [(0x20, 0x120, 0), (0x10, 0x110, 1)]


def test_more_than_223_distinct_instructions_are_compared():
    """The symbols stopped at chr(0xff), so past 223 distinct unmatched instructions
    across both functions layer 4 raised and the comparison page answered a 500."""
    from mcritweb.views.functiondiff import _levenshtein_pairs

    def blocks(base):
        # 300 one-instruction blocks, each a different instruction; function B has
        # the same ones at other addresses
        return {base + i: [(f"op{i}", f"op{i}", "")] for i in range(300)}

    escaped_a, escaped_b = blocks(0x1000), blocks(0x9000)
    unmatched = {"a": list(escaped_a), "b": list(escaped_b)}
    pairs = _levenshtein_pairs(escaped_a, escaped_b, unmatched)
    assert sorted((a, b) for a, b, distance in pairs if distance == 0) == [(0x1000 + i, 0x9000 + i) for i in range(300)]


def test_the_symbols_skip_the_surrogates():
    """rapidfuzz takes any code point, but a surrogate is not a character."""
    from mcritweb.views.functiondiff import _symbol

    assert _symbol(0) == " "
    assert _symbol(0xD800 - 0x20 - 1) == chr(0xD7FF)
    assert _symbol(0xD800 - 0x20) == chr(0xE000)


def test_the_cache_does_not_keep_the_function_names(client, as_role, fake_mcrit):
    """Names and labels can be changed in the backend, and the page shows them."""
    as_role("visitor")
    url = f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    client.get(url)
    fake_mcrit._functions[DIFFERENT_PAIR[0]].function_name = "renamed_after_the_first_view"
    assert "renamed_after_the_first_view" in client.get(url).data.decode()


def test_the_cache_is_per_backend(client, as_role, app, fake_mcrit):
    """Pointed at another backend, the same ids are other functions."""
    from mcritweb.db import ServerInfo
    as_role("visitor")
    url = _diff_url(*DIFFERENT_PAIR)
    client.get(url)
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.url = "http://127.0.0.1:9999"
        server_info.saveToDb()
    fake_mcrit.calls.clear()
    client.get(url)
    assert "getSampleById" in _diff_calls(fake_mcrit)


def test_a_backend_reset_clears_the_function_diff_memo(client, as_role, app, fake_mcrit):
    from mcritweb.views.functiondiff import _function_diff_memo
    fake_mcrit.respawn = lambda: None
    as_role("admin")
    client.get(_diff_url(*DIFFERENT_PAIR))
    with app.app_context():
        assert len(_function_diff_memo()) == 1
    assert client.post("/admin/reset_server", data={"reset_server": "RESET"}).status_code == 302
    with app.app_context():
        assert len(_function_diff_memo()) == 0


def test_an_empty_comparison_is_not_kept(client, as_role, app, fake_mcrit):
    """Without both xcfgs there is nothing to keep; the next view must try again."""
    as_role("visitor")
    url = _diff_url(*DIFFERENT_PAIR)
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _DroppedDisassemblyClient(fake_mcrit)
    client.get(url)
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: fake_mcrit
    colors, _ = _colors_of(client.get(url))
    assert "Node0x" in colors


def test_a_reused_id_is_not_served_its_predecessors_comparison(client, as_role, fake_mcrit):
    """A reset restarts mcrit's id counters, and a reset done elsewhere (another
    worker, mcrit's own client) does not clear this process's memo. Most functions
    store no PicBlockHashes, so only the xcfg tells the old function from the new."""
    from mcritweb.views.functiondiff import COLOR_UNMATCHED
    as_role("visitor")
    function_id_a, function_id_b = DIFFERENT_PAIR
    for function_id in (function_id_a, function_id_b, IDENTICAL_PAIR[1]):
        fake_mcrit._functions[function_id].picblockhashes = []
    diff_url = _diff_url(function_id_a, function_id_b)
    combined_url = f"/explore/fetchCombinedDotGraph/{function_id_a}/{function_id_b}"
    first_diff = _colors_of(client.get(diff_url))
    first_dot = client.get(combined_url).data.decode()
    # after the reset, the id of B names a copy of A's identical twin
    reused = copy.deepcopy(fake_mcrit._functions[IDENTICAL_PAIR[1]])
    reused.function_id = function_id_b
    fake_mcrit._functions[function_id_b] = reused
    second_diff = _colors_of(client.get(diff_url))
    second_dot = client.get(combined_url).data.decode()
    assert second_diff != first_diff and second_dot != first_dot
    assert COLOR_UNMATCHED not in json.loads(second_diff[0])["a"].values()
    assert 'side="b"' not in second_dot


def test_a_combined_graph_weighs_more_in_the_memo():
    """The memo is bounded by bytes, and a drawn graph counts towards them."""
    from mcritweb.views.functiondiff import COLOR_UNMATCHED, _estimated_size
    from mcritweb.views.memo import BoundedMemo
    colors = {f"Node0x{offset:x}": COLOR_UNMATCHED for offset in range(10)}
    entry = {"node_colors": {"a": colors, "b": colors}, "node_matches": {"a": {}, "b": {}}, "pairs": [], "combined_dot": None}
    size = _estimated_size(entry)
    memo = BoundedMemo(3 * size, weigh=_estimated_size)
    for key in ("a", "b", "c"):
        memo.store(key, entry)
    # the same comparison with its graph drawn weighs twice as much, and pushes two out
    memo.store("d", dict(entry, combined_dot="x" * size))
    assert memo.lookup("a") is None and memo.lookup("b") is None
    assert memo.lookup("c") is not None and memo.lookup("d") is not None


if __name__ == "__main__":
    unittest.main()


def test_main_duo_keeps_the_hook_function_compare_needs():
    """`main_duo.js` is a project fork of a vendored file; a refresh from upstream would
    silently drop the hook `function_compare.js` synchronises the panes through."""
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static")
    with open(os.path.join(static, "trace_CFG", "main_duo.js")) as f:
        main_duo = f.read()
    with open(os.path.join(static, "function_compare.js")) as f:
        function_compare = f.read()
    assert "graph_zooms[graph_id] = {zoom: zoom, svg: svg, inner: inner, initialScale: initialScale};" in main_duo
    assert 'if (typeof onGraphShown === "function") {' in main_duo
    assert "function onGraphShown(graph_id) {" in function_compare


def test_hovering_a_graph_node_scrolls_this_pages_code_panel():
    """Hovering a node scrolls the code panel to its block; main.js used to look the panel
    up as #xcfg_right, which only the comparison page has, and threw on every hover.
    tests/testFunctionPageHoverBrowser.py watches it happen; this guards it in CI."""
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "mcritweb")
    with open(os.path.join(root, "static", "trace_CFG", "main.js")) as f:
        code = re.sub(r"(?m)//.*$", "", f.read())  # comments may name either id
    with open(os.path.join(root, "templates", "single_function.html")) as f:
        page = f.read()
    assert 'id="xcfg_text_right"' in page and 'id="xcfg_right"' not in page
    assert "#xcfg_right" not in code
    assert re.search(r'd3\.select\(\s*"#xcfg_text_right"\s*\)', code)
    assert re.search(r"scrollTop\s*\+=\s*[^;]*getBoundingClientRect\(\)\.top\s*-\s*\w+\.getBoundingClientRect\(\)\.top", code)


def test_the_comparison_tooltips_are_placed_by_one_helper():
    """Both side-by-side tooltips and the combined view's go through `placeCfgTooltip`; the
    side-by-side ones took the drawn block's width, which most instructions ran out of,
    and the combined one was not held in its pane. tests/testFunctionVsBrowser.py measures
    the result in a browser; this guards the wiring in CI."""
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static")
    with open(os.path.join(static, "function_compare.js")) as f:
        compare = f.read()
    with open(os.path.join(static, "trace_CFG", "main_duo.js")) as f:
        main_duo = re.sub(r"(?m)//.*$", "", f.read())
    assert re.search(r"function\s+placeCfgTooltip\s*\(\s*tooltip\s*,\s*frame\s*,\s*mouse\s*\)", compare)
    assert re.search(r"placeCfgTooltip\(\s*tooltip\.node\(\)\s*,", compare)
    assert re.search(r"placeCfgTooltip\(\s*d3\.select\(\s*tooltip_id\s*\)\.node\(\)\s*,", main_duo)
    assert "getBBox().width * graph_scale" not in main_duo


@pytest.mark.parametrize("script", ["main.js", "main_duo.js"])
def test_the_cfg_scripts_make_no_synchronous_request(script):
    """A synchronous XMLHttpRequest on the main thread is deprecated and freezes the page
    for every block lookup; `getCodefromGraph` fetches the block matches asynchronously."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static", "trace_CFG", script)
    with open(path) as f:
        source = f.read()
    assert not re.search(r"\.open\s*\([^;]*,\s*false\s*\)", source)
    assert 'fetch("../getPicBlockMatches/" + hash_only)' in source


def _main_js_function(name):
    """The source of `function <name>(...)` in main.js, up to the next function at the
    same indentation; functions nested inside it are indented deeper and stay in."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "mcritweb", "static", "trace_CFG", "main.js")
    with open(path) as f:
        source = f.read()
    start = re.search(r"\n(\s*)function " + re.escape(name) + r"\(", source)
    assert start, name
    end = source.find("\n" + start.group(1) + "function ", start.end())
    return source[start.start():end]


def _calls(source, call):
    """Where `call(...)` appears in `source`, whitespace inside it ignored."""
    return [m.start() for m in re.finditer(re.escape(call).replace(r"\ ", r"\s*") + r"\s*\(", source)]


def test_the_function_page_carries_the_deferred_graph_prompt(client, as_role):
    """main.js shows `#cfgDeferred` and toggles `.cfg-graph-control` for a large graph (#267);
    without them in the template the page would neither offer the graph nor block its controls."""
    as_role("visitor")
    page = client.get(f"/explore/functions/{MULTI_BLOCK_FUNCTION}").get_data(as_text=True)
    deferred = re.search(r'<div id="cfgDeferred"[^>]*>', page)
    assert deferred and re.search(r'class="[^"]*\bhidden\b', deferred.group(0))
    note = re.search(r'<p id="cfgDeferredNote"[^>]*>', page)
    assert note and 'role="status"' in note.group(0) and 'aria-live="polite"' in note.group(0)
    assert 'id="cfgDeferredBlocks"' in page
    assert re.search(r'<button type="button"[^>]*id="drawGraph"', page)
    for control in ("showCycles", "showLoops", "loopBgFill", "enableTooltip"):
        tag = re.search(r'<input\b[^>]*\bid="' + control + r'"[^>]*>', page)
        assert tag and re.search(r'class="[^"]*\bcfg-graph-control\b', tag.group(0)), control


def test_a_large_graph_is_offered_rather_than_laid_out_on_load():
    """dagre's layout froze the page for seconds on large functions (#267): above
    CFG_CODE_FIRST_BLOCKS the code panel is shown at once and the graph follows from the
    layout worker; above CFG_DEFERRED_LAYOUT_BLOCKS it waits for a click."""
    load = _main_js_function("loadWithDotGraphAndFunctionId")
    branch = re.search(r"if\s*\(\s*num_blocks\s*>\s*CFG_CODE_FIRST_BLOCKS\s*\|\|\s*num_blocks\s*>\s*CFG_DEFERRED_LAYOUT_BLOCKS\s*\)", load)
    otherwise = re.search(r"\}\s*else\s*\{", load[branch.end():])
    large, small = load[branch.end():branch.end() + otherwise.start()], load[branch.end() + otherwise.end():]
    assert _calls(large, "getCodefromGraph") and _calls(large, "offerDeferredGraph")
    assert re.search(r"if\s*\(\s*num_blocks\s*<=\s*CFG_DEFERRED_LAYOUT_BLOCKS\s*\)\s*\{\s*drawDeferredGraph\(", large)
    assert not _calls(large, "findLoopsAndShowGraph") and _calls(small, "findLoopsAndShowGraph")


def test_every_failure_to_draw_the_graph_is_reported():
    """A failed loop request, an answer that is not loop data, a graph that cannot be
    measured or laid out, or a drawing that throws must bring the prompt and its button
    back rather than leave a page waiting (#267)."""
    draw = _main_js_function("findLoopsAndShowGraph")
    assert re.search(r"if\s*\(\s*err\s*\|\|\s*!result\s*\)\s*\{\s*graphNotDrawn\(", draw)
    # parsing the answer, measuring the graph and drawing it each fail into graphNotDrawn()
    attempts = re.findall(r"try\s*\{(.*?)\}\s*catch\s*\(error\)\s*\{(.*?)\}", draw, re.S)
    assert [bool(_calls(t, "JSON.parse")) for t, _ in attempts] == [True, False, False]
    assert [bool(_calls(t, "measureGraph")) for t, _ in attempts] == [False, True, False]
    assert [bool(_calls(t, "showGraph")) for t, _ in attempts] == [False, False, True]
    assert all(_calls(c, "graphNotDrawn") for _, c in attempts)
    # and a layout the worker could not make
    assert re.search(r"layOutInWorker\(.*function\s*\(reason,\s*error\)\s*\{\s*graphNotDrawn\(reason", draw, re.S)
    failed = _main_js_function("graphNotDrawn")
    assert re.search(r'd3\.select\("#drawGraph"\)\.property\("disabled",\s*false\)', failed)
    assert re.search(r'd3\.select\("#cfgDeferred"\)\.classed\("hidden",\s*false\)', failed)
    assert _calls(failed, "getCodefromGraph")


def test_the_code_panel_is_built_once_per_page():
    """A deferred graph's code panel exists before the graph, and a failed layout can ask for
    it after showGraph() did; a second build would send every block lookup again (#267).
    getCodefromGraph() returns before its first lookup when it has already run."""
    build = re.sub(r"(?m)^\s*//.*$", "", _main_js_function("getCodefromGraph"))
    first_lookup = build.find("var nodes")
    assert re.search(r"if\s*\(\s*isCodePanelRequested\s*\)\s*\{\s*return;\s*\}", build[:first_lookup])
    assert re.search(r"isCodePanelRequested\s*=\s*true;", build[:first_lookup])
    assert re.search(r"isCodePanelRequested\s*=\s*false;", _main_js_function("initialize"))


# --- the block colours are names, so each theme can paint them (#70) ---------------------

def _functiondiff_colors():
    from mcritweb.views import functiondiff
    return [
        functiondiff.COLOR_UNMATCHED, functiondiff.COLOR_ESCAPED_MATCH, functiondiff.COLOR_ADHOC_PICBLOCK_MATCH,
        functiondiff.COLOR_FULL_PICBLOCK_MATCH, functiondiff.COLOR_ONLY_IN_B, *functiondiff.LEVENSHTEIN_COLORS.values(),
        functiondiff.EDGE_COLOR_BOTH, functiondiff.EDGE_COLOR_ONLY_A, functiondiff.EDGE_COLOR_ONLY_B,
    ]


def _var_name(color):
    match = re.fullmatch(r"var\((--cfg-[a-z0-9-]+)\)", color)
    assert match, f"{color!r} is not a reference to a --cfg-* property"
    return match.group(1)


def test_functiondiff_hands_out_the_names_of_the_twelve_colors():
    names = [_var_name(color) for color in _functiondiff_colors()]
    assert sorted(names) == sorted(LIGHT_CFG_COLORS)


def test_the_light_values_are_the_colors_the_graphs_always_had():
    light = custom_properties("style.css")
    for name, value in LIGHT_CFG_COLORS.items():
        assert light.get(name) == value, name


def test_the_dark_theme_gives_every_color_a_value_of_its_own():
    light, dark = custom_properties("style.css"), custom_properties("theme-dark.css")
    for name in LIGHT_CFG_COLORS:
        assert name in dark, f"{name} has no dark value"
        assert dark[name].lower() != light[name].lower(), f"{name} is the same in both themes"
    # two blocks that look alike are two kinds of block nobody can tell apart
    fills = [dark[name].lower() for name in CFG_FILLS]
    assert len(set(fills)) == len(fills)


def test_the_dark_blocks_keep_their_text_readable_and_their_edges_visible():
    """WCAG 2.2: 4.5:1 for the text on a block, 3:1 for what is drawn on the canvas."""
    text = hex_to_rgb(dark_value("--text-body"))
    canvas = hex_to_rgb(dark_value("--cfg-canvas"))
    for name in CFG_FILLS:
        assert contrast(hex_to_rgb(dark_value(name)), text) >= 4.5, f"{name}: {dark_value(name)}"
    for name in CFG_EDGES + ["--cfg-highlight", "--cfg-matched", "--cfg-backedge"]:
        assert contrast(hex_to_rgb(dark_value(name)), canvas) >= 3, f"{name}: {dark_value(name)}"


def test_the_graphs_carry_names_and_no_hex(app):
    from mcritweb.views.functiondiff import get_combined_dot_graph
    diff = diff_of(app, *DIFFERENT_PAIR)
    with app.test_request_context("/"):
        dot = get_combined_dot_graph(*DIFFERENT_PAIR)
    colors = [color for side in diff["node_colors"].values() for color in side.values()]
    colors += re.findall(r'(?:fillcolor|color)="([^"]*)"', dot)
    assert colors, "the comparison has no colours to check"
    for color in colors:
        assert _var_name(color) in LIGHT_CFG_COLORS


def test_the_legend_names_what_the_graphs_use(client, as_role):
    as_role("visitor")
    page = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}").data.decode()
    legend = re.search(r'<span class="cfg-legend".*?</div>', page, re.S).group(0)
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", legend) is None, "the legend writes a colour of its own"
    assert sorted(set(re.findall(r"var\((--cfg-[a-z0-9-]+)\)", legend))) == sorted(LIGHT_CFG_COLORS)


@pytest.mark.parametrize("script", ["main.js", "main_duo.js"])
def test_every_cycle_color_keeps_the_text_readable_in_the_dark_theme(script):
    """`Show Cycles` gives a block one of these inline and the text on it is the dark
    theme's own, so the dark rule shows them at --cfg-cycle-opacity over the canvas."""
    palette = script_palette(script, r"^\s*var colores_g = \[")
    assert len(palette) == 16
    opacity = float(dark_value("--cfg-cycle-opacity"))
    canvas = hex_to_rgb(dark_value("--cfg-canvas"))
    text = hex_to_rgb(dark_value("--text-body"))
    for color in palette:
        assert contrast(blend(hex_to_rgb(color), opacity, canvas), text) >= 4.5, color


def test_the_loop_label_is_the_first_loop_color_in_both_themes():
    """The label of "Show Loop Boundaries" is the colour the outermost loop is drawn in:
    the first entry of the two scripts' palettes in the light theme, and that colour at the
    boundary opacity over the canvas in the dark one."""
    first = {script_palette("main_duo.js", r"^\s*var loopBgColors = \[")[0], script_palette("loopify_dagre.js", r"^\s*var colors = \[")[0]}
    assert len(first) == 1, "the two scripts disagree on the first loop colour"
    color = first.pop()
    assert custom_properties("style.css")["--cfg-loop-label"].lower() == color
    label = custom_properties("theme-dark.css")["--cfg-loop-label"]
    assert color in label and "var(--cfg-hull-opacity)" in label and "var(--cfg-canvas)" in label


def test_the_hovered_line_of_code_is_a_lift_of_the_accent_not_a_band():
    """The line under the pointer in the code panel keeps the body text at 7:1."""
    lift = custom_properties("theme-dark.css")["--cfg-code-active"]
    strength = int(re.fullmatch(r"color-mix\(in srgb, var\(--accent\) (\d+)%, transparent\)", lift).group(1)) / 100
    shown = blend(hex_to_rgb(dark_value("--accent")), strength, hex_to_rgb(dark_value("--cfg-canvas")))
    assert strength <= 0.2
    assert contrast(shown, hex_to_rgb(dark_value("--text-body"))) >= 7
