#!/usr/bin/python
"""The stacked match diagram must come out pixel for pixel as it did before #188.

`drawBlock` and `drawFrame` used to fill every pixel from a Python loop; they now hand
the rectangle to PIL. `_calculateOutputMap` lost its per-function set allocations and
`processReport` its unconditional stats pass on the way. None of that may change a
single pixel: the diagrams are cached by job id and never invalidated, so a render that
differed would leave old and new images side by side in the same cache.

The hashes below were produced by the pixel-loop renderer of e4bfa55 on exactly these
inputs - every variant `data.py` draws (plain, `famid`, `samid`, `funid`) over the
captured corpus, the query report, and a synthetic sample large enough to wrap into
many columns, which the 100 captured reference functions are not.
"""

import hashlib
import logging
import random
from types import SimpleNamespace

import pytest
from fixtureData import load
from mcrit.storage.MatchingResult import MatchingResult
from PIL import Image, ImageDraw

from mcritweb.views.MatchReportRenderer import MatchReportRenderer


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def _digest(image):
    return hashlib.sha256(f"{image.mode}{image.size}".encode() + image.tobytes()).hexdigest()


def _render_report(report, variant, filter_id):
    """What data.create_match_diagram draws for this report and filter."""
    matching_result = MatchingResult.fromDict(load(f"{report}.result"))
    kwargs = {}
    if variant == "famid":
        matching_result.filterToFamilyId(filter_id)
        kwargs["filtered_family_id"] = filter_id
    elif variant == "samid":
        matching_result.filterToSampleId(filter_id)
        kwargs["filtered_sample_id"] = filter_id
    elif variant == "funid":
        kwargs["filtered_function_id"] = filter_id
    renderer = MatchReportRenderer()
    renderer.processReport(matching_result)
    return renderer.renderStackedDiagram(**kwargs)


def _synthetic_renderer(num_functions=2500, seed=188):
    """A reference sample of `num_functions` functions and random matches against 40
    families, fed straight into the renderer's maps the way processReport fills them."""
    rng = random.Random(seed)
    renderer = MatchReportRenderer()
    renderer.sample_info = SimpleNamespace(sample_id=0, family_id=1)
    renderer.function_infos = {}
    for function_id in range(num_functions):
        num_instructions = rng.choice([rng.randint(1, 9), rng.randint(10, 60), rng.randint(60, 400)])
        renderer.function_infos[function_id] = SimpleNamespace(num_instructions=num_instructions)
        if rng.random() < 0.3:
            continue
        for _ in range(rng.randint(1, 2 ** rng.randint(0, 7))):
            family_id = rng.randint(0, 40)
            match = SimpleNamespace(
                function_id=function_id,
                matched_family_id=family_id,
                matched_sample_id=family_id * 3 + rng.randint(0, 2),
                matched_score=rng.randint(40, 100),
                match_is_pichash=rng.random() < 0.2,
                match_is_library=rng.random() < 0.1,
            )
            renderer.matches_by_function_id.setdefault(function_id, []).append(match)
            renderer.function_family_match_map.setdefault(function_id, set()).add(match.matched_family_id)
            renderer.function_sample_match_map.setdefault(function_id, set()).add(match.matched_sample_id)
            library_families = renderer.function_library_match_map.setdefault(function_id, set())
            if match.match_is_library:
                library_families.add(match.matched_family_id)
        if rng.random() < 0.05:
            renderer.function_library_global_map[function_id] = rng.randint(1, 3)
    return renderer


#: (report, variant, filter id) -> sha256 of the image master rendered for it
MASTER_DIGESTS = {
    ("matches_for_sample", "plain", None): "3a1ca157549ab1b3c087d5e04dbc596588144a0df5593deac13080477ebcbf7e",
    ("matches_for_sample", "famid", 2): "197b774b2c285fe25370a8cde5c2fdc6aa39c7d00b860153ad582bc74e116763",
    ("matches_for_sample", "samid", 3): "b307f768286d7329a0d15cd7fc644137675031f332955e723339b0246045bcbb",
    ("matches_for_sample", "funid", 6): "dd1a55b6904d128d23ce0f7f9e6eee2d1f020c7546c1cac02168c44f78731a35",
    # the functions of a query sample are not fetched, so this is only the empty frame
    ("matches_for_query", "plain", None): "ef4b31957946d06e61d85654bb4745f37c46f0bd028ac098b2f3328210a7f533",
}

SYNTHETIC_DIGESTS = {
    ("plain", None): "14dbc6f1502ceda25cb56cd9178c291701d656f06321846978b2147988e24ddc",
    ("famid", 7): "f1bae820a9a0611f0b675f104222607b2f3760af54166be3e3346163a105ac7a",
    ("samid", 22): "6a536b61ebdda1c670674494e29864e762e575a3724a7172ab643f3c0311948f",
    ("funid", 42): "cca53e242411b4c630cd93f39bf8e55fbb27a8698e0416779a4dc48de52bf8e3",
}

OUTPUT_MAP_DIGEST = "1fd9b95465e2f957823991f0ffd9273dcdc3ceff4f60b9acce7647417282595f"

FILTER_KWARGS = {"famid": "filtered_family_id", "samid": "filtered_sample_id", "funid": "filtered_function_id"}


@pytest.mark.parametrize(("report", "variant", "filter_id"), list(MASTER_DIGESTS))
def test_corpus_diagram_is_pixel_identical_to_master(app, report, variant, filter_id):
    with app.app_context():
        image = _render_report(report, variant, filter_id)
    assert _digest(image) == MASTER_DIGESTS[(report, variant, filter_id)]


@pytest.mark.parametrize(("variant", "filter_id"), list(SYNTHETIC_DIGESTS))
def test_large_diagram_is_pixel_identical_to_master(variant, filter_id):
    renderer = _synthetic_renderer()
    kwargs = {FILTER_KWARGS[variant]: filter_id} if variant in FILTER_KWARGS else {}
    image = renderer.renderStackedDiagram(**kwargs)
    # many columns, so the stack wraps and the padding blocks at the end are drawn
    assert image.size[1] > 400
    assert _digest(image) == SYNTHETIC_DIGESTS[(variant, filter_id)]


def test_output_map_is_unchanged():
    """The whole output map, including `most_common_cluster`, which the diagram does
    not draw but which `renderText` and `_getTopClusterMapping` read."""
    output_map = _synthetic_renderer()._calculateOutputMap()
    assert hashlib.sha256(repr(sorted(output_map.items())).encode()).hexdigest() == OUTPUT_MAP_DIGEST


# --- the primitives against the loops they replaced --------------------------

def _loop_block(pixels, x1, y1, block_size, color):
    for x in range(block_size):
        for y in range(block_size):
            pixels[x1 + x, y1 + y] = color


def _loop_frame(pixels, x1, y1, x2, y2, block_size, color):
    for xpixel in range(x1 - block_size, x2 + block_size):
        for ypixel in range(y1 - block_size, y2 + block_size):
            pixels[xpixel, ypixel] = color


@pytest.mark.parametrize(("x1", "y1", "block_size"), [(0, 0, 1), (20, 20, 9), (37, 5, 13), (1, 1, 11)])
def test_draw_block_covers_what_the_pixel_loop_did(x1, y1, block_size):
    expected = Image.new("RGB", (64, 48), (0xff, 0xff, 0xff))
    _loop_block(expected.load(), x1, y1, block_size, (0x22, 0x22, 0x22))
    actual = Image.new("RGB", (64, 48), (0xff, 0xff, 0xff))
    MatchReportRenderer().drawBlock(ImageDraw.Draw(actual), x1, y1, block_size, (0x22, 0x22, 0x22))
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize(("x1", "y1", "x2", "y2", "block_size"), [(19, 19, 30, 40, 9), (19, 19, 10, 30, 9), (10, 12, 11, 13, 1)])
def test_draw_frame_covers_what_the_pixel_loop_did(x1, y1, x2, y2, block_size):
    """(19, 19, 10, 30, 9) is the frame of a report with no matchable function at all,
    where num_columns comes out as -1 - a query report, whose functions are not fetched."""
    expected = Image.new("RGB", (64, 64), (0xff, 0xff, 0xff))
    _loop_frame(expected.load(), x1, y1, x2, y2, block_size, (0x22, 0x22, 0x22))
    actual = Image.new("RGB", (64, 64), (0xff, 0xff, 0xff))
    MatchReportRenderer().drawFrame(ImageDraw.Draw(actual), x1, y1, x2, y2, block_size, (0x22, 0x22, 0x22))
    assert actual.tobytes() == expected.tobytes()


# --- the debug record ---------------------------------------------------------

def test_function_counts_are_still_logged_at_debug(app, caplog):
    """processReport now skips the counting pass unless the record will be written;
    when it will, it has to say the same thing it did."""
    matching_result = MatchingResult.fromDict(load("matches_for_sample.result"))
    # other modules switch logging off globally at import, and that outlives them
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    try:
        with app.app_context(), caplog.at_level(logging.DEBUG, logger="mcritweb.views.MatchReportRenderer"):
            MatchReportRenderer().processReport(matching_result)
    finally:
        logging.disable(disabled)
    assert "Sample has 100 functions, 91 matchable and 756 with matches." in caplog.text
