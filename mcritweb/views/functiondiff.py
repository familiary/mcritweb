"""Basic-block comparison behind the CFG node colouring of the function diff view.

Split out of utility.py: this is the part that needs smda's instruction escaper,
rapidfuzz and a backend client, and it is only used by the function-vs-function
comparison. See issue #88.

Four layers of block matching are applied in order, each overriding the previous
one for the blocks it matches:

1. escaped instruction sequences (green)
2. ad-hoc PicBlockHashes over every block (light teal)
3. the indexed PicBlockHashes the backend stores (teal) - blocks of 4+ instructions
4. a thresholded edit distance over the still unmatched blocks (green to orange)

Besides the colour per node, `get_function_diff` records which blocks matched which
(`node_matches`, for linked highlighting across the two graphs) and reduces those
to a one-to-one `pairs` list, which is what the combined graph of issue #74 is
built from.
"""

import hashlib
import struct

from rapidfuzz.distance import Levenshtein
from smda.intel.IntelInstructionEscaper import IntelInstructionEscaper

from mcritweb.views.client import get_client

#: the base colour of a block nothing matched
COLOR_UNMATCHED = "#FFA0A0"
COLOR_ESCAPED_MATCH = "#00ff00"
COLOR_ADHOC_PICBLOCK_MATCH = "#C0F4FF"
COLOR_FULL_PICBLOCK_MATCH = "#00DDFF"
#: a block that exists only in function B, used by the combined view where the
#: unmatched colour above is reserved for blocks that exist only in function A
COLOR_ONLY_IN_B = "#D0B0FF"

LEVENSHTEIN_COLORS = {
    0: "#40ff40",
    1: "#c0ff80",
    2: "#FFFF40",
    3: "#FFCC40",
}

EDGE_COLOR_BOTH = "#000000"
EDGE_COLOR_ONLY_A = "#d62728"
EDGE_COLOR_ONLY_B = "#7b2cbf"


def node_id(offset):
    return f"Node0x{offset:x}"


def _hash_sequence(sequence):
    return struct.unpack("Q", hashlib.sha256(sequence).digest()[:8])[0]


#: a hash shared by more blocks than this on both sides is no longer paired as a
#: full product but by rank, see _pair_by_hash
MAX_FULL_PRODUCT = 64
#: how many rank neighbours a block in a large bucket is paired with, either way
RANK_WINDOW = 4


def _pair_by_hash(hashes_a, hashes_b):
    """Every (offset_a, offset_b) whose block hashes are equal - many-to-many.

    Small buckets are paired completely. A hash that many blocks on both sides
    share (a lone `ret`, an obfuscator's filler) would otherwise expand into a
    Cartesian product that dominates request time and page size, so a large bucket
    pairs each block with its rank neighbours in address order instead - every
    block still gets partners, and identical blocks at corresponding positions
    find each other.
    """
    by_hash_a = {}
    for entry in hashes_a:
        by_hash_a.setdefault(entry["hash"], []).append(entry["offset"])
    by_hash_b = {}
    for entry in hashes_b:
        by_hash_b.setdefault(entry["hash"], []).append(entry["offset"])
    pairs = []
    for block_hash, offsets_a in by_hash_a.items():
        offsets_b = by_hash_b.get(block_hash)
        if not offsets_b:
            continue
        offsets_a = sorted(offsets_a)
        offsets_b = sorted(offsets_b)
        if len(offsets_a) * len(offsets_b) <= MAX_FULL_PRODUCT:
            pairs.extend((offset_a, offset_b) for offset_a in offsets_a for offset_b in offsets_b)
            continue
        bucket = set()
        for index_a, offset_a in enumerate(offsets_a):
            index_b = round(index_a * (len(offsets_b) - 1) / max(len(offsets_a) - 1, 1))
            for offset_b in offsets_b[max(0, index_b - RANK_WINDOW):index_b + RANK_WINDOW + 1]:
                bucket.add((offset_a, offset_b))
        for index_b, offset_b in enumerate(offsets_b):
            index_a = round(index_b * (len(offsets_a) - 1) / max(len(offsets_b) - 1, 1))
            for offset_a in offsets_a[max(0, index_a - RANK_WINDOW):index_a + RANK_WINDOW + 1]:
                bucket.add((offset_a, offset_b))
        pairs.extend(sorted(bucket))
    return pairs


def _stored_picblock_pairs(function_entry_a, function_entry_b):
    return _pair_by_hash(function_entry_a.picblockhashes, function_entry_b.picblockhashes)


def _adhoc_picblock_hashes(smda_function, sample_entry):
    hashes = []
    for block in smda_function.getBlocks():
        escaped_binary_seq = []
        for instruction in block.getInstructions():
            escaped_binary_seq.append(instruction.getEscapedBinary(IntelInstructionEscaper, escape_intraprocedural_jumps=True, lower_addr=sample_entry.base_addr, upper_addr=sample_entry.base_addr + sample_entry.binary_size))
        as_bytes = bytes([ord(c) for c in "".join(escaped_binary_seq)])
        hashes.append({"offset": block.offset, "hash": _hash_sequence(as_bytes)})
    return hashes


def _adhoc_picblock_pairs(function_a, function_b, smda_function_a, smda_function_b):
    client = get_client()
    sample_a = client.getSampleById(function_a.sample_id)
    sample_b = client.getSampleById(function_b.sample_id)
    return _pair_by_hash(_adhoc_picblock_hashes(smda_function_a, sample_a), _adhoc_picblock_hashes(smda_function_b, sample_b))


def _escaped_sequence(block):
    """The block's instructions with addresses and immediates escaped away."""
    return [IntelInstructionEscaper.escapeMnemonic(instruction.mnemonic) + " " + IntelInstructionEscaper.escapeOperands(instruction) for instruction in block.getInstructions()]


def _escaped_hashes(smda_function):
    hashes = []
    for block in smda_function.getBlocks():
        merged = ";".join(_escaped_sequence(block))
        hashes.append({"offset": block.offset, "hash": _hash_sequence(merged.encode("ascii"))})
    return hashes


def _escaped_pairs(smda_function_a, smda_function_b):
    return _pair_by_hash(_escaped_hashes(smda_function_a), _escaped_hashes(smda_function_b))


def _levenshtein_pairs(smda_function_a, smda_function_b, unmatched_nodes):
    """(offset_a, offset_b, distance) for the still unmatched blocks, one-to-one."""
    # across all blocks in unmatched nodes, collect tokens and map to symbols
    # token -> symbol, like "M REG, REG" -> 0
    # we use symbols from chr(0x20) to chr(0x7e), i.e. up to 94 printables, which "should always be enough (TM)""
    alphabet = {}
    num_symbols = 0

    def symbolify(smda_function, unmatched, side):
        nonlocal num_symbols
        # offset -> symbolified block
        candidate_blocks = {}
        for block in smda_function.getBlocks():
            if block.offset not in unmatched:
                continue
            symbolified_block = ""
            for instruction in block.getInstructions():
                escaped_ins = instruction.mnemonic + " " + IntelInstructionEscaper.escapeOperands(instruction)
                if escaped_ins not in alphabet:
                    alphabet[escaped_ins] = chr(0x20 + num_symbols)
                    num_symbols += 1
                    if num_symbols > 0xff-0x20:
                        # the alphabet was printed here before raising: on a request path,
                        # dumping every distinct instruction in the function to stdout. The
                        # size is the part that explains the failure, so it goes where a
                        # reader of the traceback will actually see it. See #165 - #175's
                        # deduplication of these two loops had restored the print.
                        raise Exception(
                            f"Too many distinct instructions to compare: {num_symbols} "
                            f"across both functions, limit {0xff - 0x20}. Overflowed while "
                            f"symbolifying function {side}.")
                symbolified_block += alphabet[escaped_ins]
            candidate_blocks[block.offset] = symbolified_block
        return candidate_blocks

    candidate_blocks_a = symbolify(smda_function_a, unmatched_nodes["a"], "a")
    candidate_blocks_b = symbolify(smda_function_b, unmatched_nodes["b"], "b")

    by_score = {0: [], 1: [], 2: [], 3: []}
    for block_a, symbols_a in candidate_blocks_a.items():
        for block_b, symbols_b in candidate_blocks_b.items():
            if abs(len(symbols_a) - len(symbols_b)) > 4:
                continue
            distance = Levenshtein.distance(symbols_a, symbols_b, score_cutoff=3)
            if distance < 4:
                by_score[distance].append((block_a, block_b))
    used_blocks = set()
    pairs = []
    for score, candidates in by_score.items():
        for block_a, block_b in candidates:
            if block_a not in used_blocks and block_b not in used_blocks:
                pairs.append((block_a, block_b, score))
                used_blocks.add(block_a)
                used_blocks.add(block_b)
    return pairs


def _apply_layer(node_colors, node_layers, layer_pairs, layer_index, pairs, color_of):
    """Colour both ends of every pair and record which layer coloured them.

    A block matched again by a later layer takes that layer's colour, so the pairs
    of an earlier layer stop describing it; `_collect_matches` keeps only the pairs
    whose ends both wear the colour of that pair's layer.
    """
    for pair in pairs:
        offset_a, offset_b = pair[0], pair[1]
        color = color_of(pair)
        for side, offset in (("a", offset_a), ("b", offset_b)):
            key = node_id(offset)
            node_colors[side][key] = color
            node_layers[side][key] = layer_index
        layer_pairs.append((layer_index, node_id(offset_a), node_id(offset_b)))


def _collect_matches(node_layers, layer_pairs):
    """{"a": {node_id: [node_ids of B]}, "b": {...}}, symmetric by construction."""
    node_matches = {"a": {}, "b": {}}
    for layer_index, key_a, key_b in layer_pairs:
        if node_layers["a"].get(key_a) != layer_index or node_layers["b"].get(key_b) != layer_index:
            continue
        node_matches["a"].setdefault(key_a, []).append(key_b)
        node_matches["b"].setdefault(key_b, []).append(key_a)
    return node_matches


def _drop_orphans(node_colors, node_layers, layer_pairs):
    """Uncolour blocks whose layer no longer has a surviving pair for them.

    A later layer can re-match one end of an earlier pair only; the other end would
    then wear a match colour with nothing to point at. It goes back to unmatched, so
    the edit-distance layer gets a chance at it and the page never shows a matched
    colour without a partner.
    """
    node_matches = _collect_matches(node_layers, layer_pairs)
    for side in ("a", "b"):
        for key in list(node_layers[side]):
            if key not in node_matches[side]:
                del node_layers[side][key]
                node_colors[side][key] = COLOR_UNMATCHED


def _one_to_one_pairs(node_matches):
    """Reduce the many-to-many matches to one partner per block.

    Identical small blocks (a lone `ret`, say) match each other in every combination,
    but a combined graph needs each block drawn once - so every block is paired with
    the first unused partner in address order.
    """
    used_b = set()
    pairs = []
    # blocks with the fewest candidates first, so a block with a single partner is
    # not robbed of it by an earlier block that had a choice
    for key_a in sorted(node_matches["a"], key=lambda key: (len(node_matches["a"][key]), int(key[6:], 16))):
        for key_b in sorted(node_matches["a"][key_a], key=lambda key: int(key[6:], 16)):
            if key_b not in used_b:
                used_b.add(key_b)
                pairs.append((int(key_a[6:], 16), int(key_b[6:], 16)))
                break
    return pairs


def empty_function_diff(function_entry=None, other_function_entry=None):
    return {"node_colors": {"a": {}, "b": {}}, "node_matches": {"a": {}, "b": {}}, "pairs": [], "functions": (function_entry, other_function_entry), "smda_functions": None}


def get_function_diff(function_id_a, function_id_b, function_entry=None, other_function_entry=None):
    """Compare two stored functions block by block.

    Entries already fetched with their xcfg can be passed in, which spares the two
    backend round-trips; otherwise they are fetched here.

    Returns a dict with
      node_colors   {"a": {node_id: colour}, "b": {...}}, one entry per block
      node_matches  {"a": {node_id: [node_ids of B]}, "b": {...}}, only matched blocks
      pairs         [(offset_a, offset_b), ...], a one-to-one selection of the above
      functions     (function_entry_a, function_entry_b)
      smda_functions (SmdaFunction a, SmdaFunction b), or None when there was nothing to compare

    A backend that dropped the disassembly (STORAGE_DROP_DISASSEMBLY) answers with an
    empty xcfg (`None` means it was not requested, `{}` that it is gone), and the
    diff is then empty rather than a server error.
    """
    client = get_client()
    if function_entry is None or not function_entry.xcfg:
        function_entry = client.getFunctionById(function_id_a, with_xcfg=True)
    if other_function_entry is None or not other_function_entry.xcfg:
        other_function_entry = client.getFunctionById(function_id_b, with_xcfg=True)
    if function_entry is None or other_function_entry is None or not function_entry.xcfg or not other_function_entry.xcfg:
        return empty_function_diff(function_entry, other_function_entry)
    smda_function_a = function_entry.toSmdaFunction()
    smda_function_b = other_function_entry.toSmdaFunction()
    node_colors = {"a": {}, "b": {}}
    node_layers = {"a": {}, "b": {}}
    layer_pairs = []
    # no match / base color: bleak red
    for block in smda_function_a.getBlocks():
        node_colors["a"][node_id(block.offset)] = COLOR_UNMATCHED
    for block in smda_function_b.getBlocks():
        node_colors["b"][node_id(block.offset)] = COLOR_UNMATCHED
    # escaped blocks matches
    _apply_layer(node_colors, node_layers, layer_pairs, 1, _escaped_pairs(smda_function_a, smda_function_b), lambda pair: COLOR_ESCAPED_MATCH)
    # ad-hoc picblock match (small BB): bleak teal
    _apply_layer(node_colors, node_layers, layer_pairs, 2, _adhoc_picblock_pairs(function_entry, other_function_entry, smda_function_a, smda_function_b), lambda pair: COLOR_ADHOC_PICBLOCK_MATCH)
    # override "full" picblocks with 4+ addresses
    _apply_layer(node_colors, node_layers, layer_pairs, 3, _stored_picblock_pairs(function_entry, other_function_entry), lambda pair: COLOR_FULL_PICBLOCK_MATCH)
    _drop_orphans(node_colors, node_layers, layer_pairs)
    # compare everything not colored by now using our adapted Levenshtein
    unmatched_nodes = {
        "a": [int(k[6:], 16) for k, v in node_colors["a"].items() if v == COLOR_UNMATCHED],
        "b": [int(k[6:], 16) for k, v in node_colors["b"].items() if v == COLOR_UNMATCHED],
    }
    _apply_layer(node_colors, node_layers, layer_pairs, 4, _levenshtein_pairs(smda_function_a, smda_function_b, unmatched_nodes), lambda pair: LEVENSHTEIN_COLORS[pair[2]])
    node_matches = _collect_matches(node_layers, layer_pairs)
    return {
        "node_colors": node_colors,
        "node_matches": node_matches,
        "pairs": _one_to_one_pairs(node_matches),
        "functions": (function_entry, other_function_entry),
        "smda_functions": (smda_function_a, smda_function_b),
    }


def _dot_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _dot_label(lines):
    """Lines joined with the left-aligning `\\l` smda's own dot export uses."""
    return r"\l".join(_dot_escape(line) for line in lines)


def _block_lines(smda_function, block):
    lines = []
    for smda_ins in block.getInstructions():
        apiref_str = smda_function.apirefs.get(smda_ins.offset, "")
        if apiref_str:
            lines.append(f"{smda_ins.offset:x}: {smda_ins.mnemonic} [{apiref_str}]")
        else:
            lines.append(f"{smda_ins.offset:x}: {smda_ins.mnemonic} {smda_ins.operands}")
    return lines


def build_combined_dot_graph(smda_function_a, smda_function_b, pairs, node_colors):
    """One graph holding both functions, in the format `SmdaFunction.toDotGraph` uses.

    A matched pair of blocks becomes a single node carrying A's id and colour, its
    label headed by both offsets and followed by A's instructions; where the two
    blocks differ, B's instructions travel in the node's `comment` so the page can
    show them on demand. Blocks without a partner in `pairs` keep A's id and get
    the unmatched colour; blocks only in B get a `NodeB` id and their own colour. Edges are the union of
    both control flows, coloured by which side has them.
    """
    a_to_b = {offset_a: offset_b for offset_a, offset_b in pairs}
    b_to_a = {offset_b: offset_a for offset_a, offset_b in pairs}
    blocks_b = {block.offset: block for block in smda_function_b.getBlocks()}

    def combined_id(side, offset):
        if side == "a":
            return node_id(offset)
        if offset in b_to_a:
            return node_id(b_to_a[offset])
        return f"NodeB0x{offset:x}"

    dot_graph = f'digraph "Combined CFG for 0x{smda_function_a.offset:x} and 0x{smda_function_b.offset:x}" {{\n'
    dot_graph += f'  label="Combined CFG for 0x{smda_function_a.offset:x} and 0x{smda_function_b.offset:x}";\n'
    for block in smda_function_a.getBlocks():
        lines = _block_lines(smda_function_a, block)
        comment = ""
        if block.offset in a_to_b:
            offset_b = a_to_b[block.offset]
            block_b = blocks_b[offset_b]
            color = node_colors["a"].get(node_id(block.offset), COLOR_UNMATCHED)
            lines = [f"A 0x{block.offset:x} | B 0x{offset_b:x}"] + lines
            # B's code travels along only where it differs from A's - compared with
            # addresses and immediates escaped, since those differ between any two
            # binaries without making the code different
            if _escaped_sequence(block_b) != _escaped_sequence(block):
                comment = _dot_label(_block_lines(smda_function_b, block_b))
        else:
            # a block that matched several candidates but lost them all in the
            # one-to-one reduction is drawn as A-only, in the A-only colour
            color = COLOR_UNMATCHED
            lines = [f"A 0x{block.offset:x} only"] + lines
        dot_graph += f'  {node_id(block.offset)} [shape=record,side="{"ab" if block.offset in a_to_b else "a"}",fillcolor="{color}",comment="{comment}",label="{_dot_label(lines)}"];\n'
    for block in smda_function_b.getBlocks():
        if block.offset in b_to_a:
            continue
        lines = [f"B 0x{block.offset:x} only"] + _block_lines(smda_function_b, block)
        dot_graph += f'  {combined_id("b", block.offset)} [shape=record,side="b",fillcolor="{COLOR_ONLY_IN_B}",comment="",label="{_dot_label(lines)}"];\n'
    edges_a = set()
    for source, targets in smda_function_a.blockrefs.items():
        for target in targets:
            edges_a.add((combined_id("a", source), combined_id("a", target)))
    edges_b = set()
    for source, targets in smda_function_b.blockrefs.items():
        for target in targets:
            edges_b.add((combined_id("b", source), combined_id("b", target)))
    for source, target in sorted(edges_a | edges_b):
        if (source, target) in edges_a and (source, target) in edges_b:
            attributes = f'color="{EDGE_COLOR_BOTH}",side="ab"'
        elif (source, target) in edges_a:
            attributes = f'color="{EDGE_COLOR_ONLY_A}",style=dashed,side="a"'
        else:
            attributes = f'color="{EDGE_COLOR_ONLY_B}",style=dashed,side="b"'
        dot_graph += f"  {source} -> {target} [{attributes}];\n"
    dot_graph += "}"
    return dot_graph


def get_combined_dot_graph(function_id_a, function_id_b):
    diff = get_function_diff(function_id_a, function_id_b)
    if diff["smda_functions"] is None:
        return ""
    smda_function_a, smda_function_b = diff["smda_functions"]
    return build_combined_dot_graph(smda_function_a, smda_function_b, diff["pairs"], diff["node_colors"])
