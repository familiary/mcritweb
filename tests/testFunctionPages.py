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
    from mcritweb.views.functiondiff import COLOR_UNMATCHED, build_combined_dot_graph, get_function_diff
    with app.test_request_context("/"):
        diff = get_function_diff(*DIFFERENT_PAIR)
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
