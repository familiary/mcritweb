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
    diff = diff_of(app, *IDENTICAL_PAIR)
    assert len(diff["node_colors"]["a"]) == len(diff["node_colors"]["b"])
    unmatched = [node for node, color in diff["node_colors"]["a"].items() if color == "#FFA0A0"]
    assert unmatched == []
    # one partner per block, both ways
    assert len(diff["pairs"]) == len(diff["node_colors"]["a"])
    assert len({a for a, _ in diff["pairs"]}) == len(diff["pairs"])
    assert len({b for _, b in diff["pairs"]}) == len(diff["pairs"])


def test_node_matches_are_consistent_with_the_pairs(app):
    diff = diff_of(app, *DIFFERENT_PAIR)
    for offset_a, offset_b in diff["pairs"]:
        assert f"Node0x{offset_b:x}" in diff["node_matches"]["a"][f"Node0x{offset_a:x}"]
        assert f"Node0x{offset_a:x}" in diff["node_matches"]["b"][f"Node0x{offset_b:x}"]
    # a matched block is never drawn in the unmatched colour
    for node in diff["node_matches"]["a"]:
        assert diff["node_colors"]["a"][node] != "#FFA0A0"


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
    assert "nodeMatches" in page
    assert "function_compare.js" in page
    assert f"/api/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}" in page


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


def _page_colors(page):
    return re.search(r"var js_node_colors = (.*);", page).group(1), re.search(r"nodeMatches: (.*),\n", page).group(1)


def test_a_repeated_comparison_is_not_recomputed(client, as_role, fake_mcrit):
    as_role("visitor")
    url = f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    first = client.get(url).data.decode()
    assert _diff_calls(fake_mcrit), "the first view has to compute the comparison"
    fake_mcrit.calls.clear()
    second = client.get(url).data.decode()
    assert _diff_calls(fake_mcrit) == []
    assert _page_colors(second) == _page_colors(first)


def test_the_combined_graph_reuses_the_comparison_of_the_page(client, as_role, app, fake_mcrit):
    from mcritweb.views.functiondiff import build_combined_dot_graph, get_function_diff
    as_role("visitor")
    client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
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
    url = f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    first = _page_colors(client.get(url).data.decode())
    # the recalculation now finds the first blocks of both functions to be equal
    first_blocks = []
    for function_id in DIFFERENT_PAIR:
        entry = fake_mcrit._functions[function_id]
        offset = entry.toSmdaFunction().getBlocks()[0].offset
        first_blocks.append(node_id(offset))
        entry.picblockhashes = [{"offset": offset, "hash": 0x186, "size": 4}]
    second = _page_colors(client.get(url).data.decode())
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
    page = client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}").data.decode()
    assert _page_colors(page)[0] == '{"a": {}, "b": {}}'


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
    url = f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    client.get(url)
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.url = "http://127.0.0.1:9999"
        server_info.saveToDb()
    fake_mcrit.calls.clear()
    client.get(url)
    assert _diff_calls(fake_mcrit)


def test_a_backend_reset_clears_the_function_diff_memo(client, as_role, app, fake_mcrit):
    from mcritweb.views.functiondiff import _function_diff_memo
    fake_mcrit.respawn = lambda: None
    as_role("admin")
    client.get(f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}")
    with app.app_context():
        assert len(_function_diff_memo()) == 1
    assert client.post("/admin/reset_server", data={"reset_server": "RESET"}).status_code == 302
    with app.app_context():
        assert len(_function_diff_memo()) == 0


def test_an_empty_comparison_is_not_kept(client, as_role, app, fake_mcrit):
    """Without both xcfgs there is nothing to keep; the next view must try again."""
    as_role("visitor")
    url = f"/data/matches/function/{DIFFERENT_PAIR[0]}/{DIFFERENT_PAIR[1]}"
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: _DroppedDisassemblyClient(fake_mcrit)
    client.get(url)
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: fake_mcrit
    colors, _ = _page_colors(client.get(url).data.decode())
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
    page_url = f"/data/matches/function/{function_id_a}/{function_id_b}"
    combined_url = f"/explore/fetchCombinedDotGraph/{function_id_a}/{function_id_b}"
    first_page = _page_colors(client.get(page_url).data.decode())
    first_dot = client.get(combined_url).data.decode()
    # after the reset, the id of B names a copy of A's identical twin
    reused = copy.deepcopy(fake_mcrit._functions[IDENTICAL_PAIR[1]])
    reused.function_id = function_id_b
    fake_mcrit._functions[function_id_b] = reused
    second_page = _page_colors(client.get(page_url).data.decode())
    second_dot = client.get(combined_url).data.decode()
    assert second_page != first_page and second_dot != first_dot
    assert COLOR_UNMATCHED not in json.loads(second_page[0])["a"].values()
    assert 'side="b"' not in second_dot


def test_a_combined_graph_weighs_more_in_the_memo():
    """The memo is bounded by bytes, and a drawn graph counts towards them."""
    from mcritweb.views.functiondiff import _estimated_size
    from mcritweb.views.memo import BoundedMemo
    colors = {f"Node0x{offset:x}": "#FFA0A0" for offset in range(10)}
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
