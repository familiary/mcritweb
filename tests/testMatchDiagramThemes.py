#!/usr/bin/python
"""The match diagram in the dark palette (#70).

The diagram is an opaque PNG of data, so CSS cannot recolour it: it is drawn once per
palette, and the page offers the dark file to a dark page through <picture>. What these
tests hold onto:

- the light file does not change by a byte of pixels, because cached diagrams are
  keyed by name and never invalidated, and the live instance already holds hundreds;
- the dark file is the same drawing with two neutrals swapped (the ground, which is
  also what "no match" is painted, and the frame), every data hue untouched, and those
  two are the very values static/theme-dark.css gives the page and its borders;
- the palette is read from the file name that was asked for and from nothing else. An
  earlier attempt drew it from the viewer's theme, so a light viewer asking for the
  dark name cached a light picture under it for everyone;
- the name grammar accepts the dark names and nothing else it did not already accept.
"""

import io
import itertools
import logging
import math
import pathlib
import re
import unittest
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of, load
from mcrit.storage.MatchingResult import MatchingResult
from PIL import Image, ImageChops
from testMatchDiagramDrawing import (
    FILTER_KWARGS,
    MASTER_DIGESTS,
    SYNTHETIC_DIGESTS,
    _digest,
    _render_report,
    _synthetic_renderer,
)
from testMatchDiagrams import cached_diagrams, report_trimmed_to_the_fixture_pool
from testResultPages import CorpusWithEveryMatchedFunction

from mcritweb.db import UserInfo
from mcritweb.views.data import (
    DIAGRAM_FILENAME_RE,
    DIAGRAM_VARIANTS,
    create_match_diagram,
    diagram_variant_suffix,
    match_diagram_size,
)
from mcritweb.views.MatchReportRenderer import DEFAULT_DIAGRAM_VARIANT, DIAGRAM_PALETTES, MatchReportRenderer, diagram_palette, mix_toward_ground

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).parent.parent
THEMES = ["light", "dark", "system"]

WHITE = (0xff, 0xff, 0xff)
LIGHT_FRAME = (0x22, 0x22, 0x22)
DARK_GROUND = (0x14, 0x1a, 0x21)
DARK_FRAME = (0x65, 0x77, 0x90)
#: how much of itself a data colour keeps in the dark palette (see DIAGRAM_PALETTES)
DARK_HUE_STRENGTH = 0.78


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def widened_corpus(app):
    """The captured corpus, answering any function id: the sample-filtered page renders as
    "corrupted" otherwise, without a diagram (see testResultPages)."""
    backend = CorpusWithEveryMatchedFunction()
    app.config["MCRIT_CLIENT_FACTORY"] = lambda **kwargs: backend
    return backend


@pytest.fixture
def as_theme(app, as_role):
    """Log in as a visitor whose stored theme is `theme`."""
    def _as_theme(theme):
        user_id = as_role("visitor")
        with app.app_context():
            user_info = UserInfo.fromDb(user_id=user_id)
            user_info.theme = theme
            user_info.saveToDb()
        return user_id
    return _as_theme


def colour_mask(image, colour):
    """An "L" image: 255 where a pixel of `image` is exactly `colour`, 0 elsewhere.

    Done with the image library's own operations because the synthetic diagram is
    millions of pixels, which is minutes of work for a Python loop.
    """
    mask = None
    for band, value in zip(image.convert("RGB").split(), colour):
        band_mask = band.point(lambda level, value=value: 255 if level == value else 0)
        mask = band_mask if mask is None else ImageChops.darker(mask, band_mask)
    return mask


def contains(image, colour):
    return colour_mask(image, colour).getbbox() is not None


def dark_of(colour):
    """What a colour of the light diagram becomes in the dark one, written out here
    rather than taken from the renderer: the two neutrals are swapped, and every data
    hue keeps DARK_HUE_STRENGTH of itself over the dark ground."""
    if colour == WHITE:
        return DARK_GROUND
    if colour == LIGHT_FRAME:
        return DARK_FRAME
    return tuple(round(ground + DARK_HUE_STRENGTH * (channel - ground)) for ground, channel in zip(DARK_GROUND, colour))


def colours_of(image):
    return {colour for _count, colour in image.convert("RGB").getcolors(maxcolors=1 << 24)}


def with_the_light_colours_replaced_by_the_dark_ones(light):
    """What the dark diagram has to be, given the light one."""
    expected = light.convert("RGB")
    for colour in colours_of(light):
        expected.paste(dark_of(colour), mask=colour_mask(light, colour))
    return expected


def png_of(response):
    return Image.open(io.BytesIO(response.data)).convert("RGB")


# --- the palettes ------------------------------------------------------------------

def css_colour(stylesheet, token):
    """The #rrggbb value `token` is given in static/theme-dark.css, as a tuple."""
    css = (ROOT / "mcritweb" / "static" / stylesheet).read_text()
    found = re.findall(rf"^\s*{re.escape(token)}\s*:\s*#([0-9a-fA-F]{{6}})\s*;", css, re.MULTILINE)
    assert len(found) == 1, f"{token} is not given exactly once as a #rrggbb value in {stylesheet}: {found}"
    return tuple(int(found[0][index:index + 2], 16) for index in (0, 2, 4))


def test_the_dark_ground_is_the_dark_page_colour():
    """The diagram has to sit on the page without a visible edge, so its ground is the
    page's own colour - and both are only ever repeated by hand."""
    assert DIAGRAM_PALETTES["dark"]["ground"] == css_colour("theme-dark.css", "--surface-page")


def test_the_dark_frame_is_the_dark_control_border():
    assert DIAGRAM_PALETTES["dark"]["frame"] == css_colour("theme-dark.css", "--border-control")


def test_the_palettes_are_the_two_this_was_designed_with():
    """Each value is pinned once more here, so that a palette that moves is a decision
    somebody made in a diff and not a side effect: the light one decides what every
    cached diagram looks like."""
    assert DIAGRAM_PALETTES == {
        "light": {"ground": WHITE, "frame": LIGHT_FRAME, "hue_strength": 1.0},
        "dark": {"ground": DARK_GROUND, "frame": DARK_FRAME, "hue_strength": DARK_HUE_STRENGTH},
    }
    assert DEFAULT_DIAGRAM_VARIANT == "light"
    assert DIAGRAM_VARIANTS == ("dark",)


@pytest.mark.parametrize("variant", ["", "sepia", "Dark", None, ["dark"], 0])
def test_a_palette_nobody_defined_is_refused_by_name(variant):
    with pytest.raises(ValueError):
        diagram_palette(variant)
    with pytest.raises(ValueError):
        diagram_variant_suffix(variant)
    with pytest.raises(ValueError):
        MatchReportRenderer().renderStackedDiagram(variant=variant)


def test_a_palette_nobody_defined_writes_no_file(app, corpus_mcrit):
    report = corpus_mcrit.getResultForJob(job_id_of("matches_for_sample"))
    with app.app_context():
        with pytest.raises(ValueError):
            create_match_diagram(app, job_id_of("matches_for_sample"), MatchingResult.fromDict(report), variant="sepia")
    assert cached_diagrams(app) == []


def data_bands():
    """The data colours each of the three bands can show, from the renderer's own maps
    (level 0 is "no match", which is the ground)."""
    frequency, matching = MatchReportRenderer.frequency_color_map, MatchReportRenderer.matching_color_map_50
    return {
        "top": [colour for level, colour in sorted(frequency.items()) if level],
        "bottom": [colour for level, colour in sorted(matching.items()) if level],
        "library": list(MatchReportRenderer.library_color_map.values()),
    }


def linear(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(colour):
    red, green, blue = (linear(channel) for channel in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def lab_of(colour):
    """CIELAB under D65."""
    red, green, blue = (linear(channel) for channel in colour)
    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))


def delta_e(first, second):
    return math.dist(lab_of(first), lab_of(second))


def dark_palette_colours():
    """Each band's colours as the dark palette draws them, with the ground and the frame."""
    palette = DIAGRAM_PALETTES["dark"]
    return {
        name: [mix_toward_ground(hue, palette["ground"], palette["hue_strength"]) for hue in hues] + [palette["ground"], palette["frame"]]
        for name, hues in data_bands().items()
    }


def test_a_hue_is_pulled_toward_the_ground_by_the_strength():
    # 0.78 of the way from the ground to each channel of pure yellow, rounded
    assert mix_toward_ground((0xff, 0xff, 0x35), DARK_GROUND, 0.78) == (203, 205, 49)
    hues = [hue for band in data_bands().values() for hue in band]
    assert len(set(hues)) >= 11
    for hue in hues:
        assert mix_toward_ground(hue, DARK_GROUND, 1.0) == hue
        assert mix_toward_ground(hue, WHITE, 1.0) == hue
        assert mix_toward_ground(hue, DARK_GROUND, 0) == DARK_GROUND
        assert all(min(ground, channel) <= mixed <= max(ground, channel) for ground, channel, mixed in zip(DARK_GROUND, hue, mix_toward_ground(hue, DARK_GROUND, 0.78)))


def test_the_light_palette_draws_every_hue_as_it_is():
    palette = DIAGRAM_PALETTES["light"]
    for hue in [hue for band in data_bands().values() for hue in band]:
        assert mix_toward_ground(hue, palette["ground"], palette["hue_strength"]) == hue


def test_no_data_colour_outshines_the_body_text_of_the_dark_page():
    """The saturated hues glow on a dark ground: pure yellow, green and cyan are brighter
    than the page's own text. Pulled toward the ground they must come out clearly
    dimmer: at most 85% of its luminance (the brightest, yellow, is at 75% of the
    present --text-body, and at 84% of the dimmer #cfd7e0 that was considered)."""
    text = relative_luminance(css_colour("theme-dark.css", "--text-body"))
    palette = DIAGRAM_PALETTES["dark"]
    hues = [hue for band in data_bands().values() for hue in band]
    # or there would be nothing here to keep in check
    assert max(relative_luminance(hue) for hue in hues) > text
    mixed = [mix_toward_ground(hue, palette["ground"], palette["hue_strength"]) for hue in hues]
    assert max(relative_luminance(colour) for colour in mixed) <= 0.85 * text


def test_the_levels_of_a_band_stay_apart_in_the_dark_palette():
    """Pulling the hues toward the ground also pulls them toward each other. Every pair
    that can sit side by side in a band - each level, the ground a cell without a match
    has, the frame between functions - must stay at least 18 apart in CIELAB. The closest
    pair is the two blues of the bottom band, 24 apart in the light palette."""
    for name, colours in dark_palette_colours().items():
        for first, second in itertools.combinations(colours, 2):
            assert delta_e(first, second) >= 18, (name, first, second, delta_e(first, second))


# --- the drawing -------------------------------------------------------------------

def assert_the_dark_drawing_is_the_light_one_in_the_dark_colours(light, dark):
    """Pixel by pixel: ground -> dark ground, frame -> dark frame, a data hue -> the
    same hue at DARK_HUE_STRENGTH over the dark ground.

    Nothing else may differ - not a size, not a block position - which also means no
    cell may keep the light ground (that is the "no match" cell on a dark page) and no
    hue may keep its full strength.
    """
    assert dark.size == light.size
    assert dark.convert("RGB").tobytes() == with_the_light_colours_replaced_by_the_dark_ones(light).tobytes()
    hues = colours_of(light) - {WHITE, LIGHT_FRAME}
    for colour in {WHITE, LIGHT_FRAME} | hues:
        assert not contains(dark, colour), colour
    assert contains(dark, DARK_FRAME)
    # the data hues that were compared, so that a caller can tell this was not two
    # pictures with nothing in them
    return hues


@pytest.mark.parametrize(("report", "variant", "filter_id"), list(MASTER_DIGESTS))
def test_the_dark_diagram_is_the_light_one_in_the_dark_colours(app, report, variant, filter_id):
    with app.app_context():
        light = _render_report(report, variant, filter_id, palette="light")
        dark = _render_report(report, variant, filter_id, palette="dark")
    hues = assert_the_dark_drawing_is_the_light_one_in_the_dark_colours(light, dark)
    # a query's functions are not fetched, so its diagram is only the empty frame, and
    # the one function the funid diagram is filtered to has no match in this report
    assert bool(hues) == (report != "matches_for_query" and variant != "funid")


@pytest.mark.parametrize(("variant", "filter_id"), list(SYNTHETIC_DIGESTS))
def test_the_dark_large_diagram_is_the_light_one_in_the_dark_colours(variant, filter_id):
    """Many columns, every colour of every band, the filtered tops and the padding."""
    kwargs = {FILTER_KWARGS[variant]: filter_id} if variant in FILTER_KWARGS else {}
    light = _synthetic_renderer().renderStackedDiagram(variant="light", **kwargs)
    dark = _synthetic_renderer().renderStackedDiagram(variant="dark", **kwargs)

    hues = assert_the_dark_drawing_is_the_light_one_in_the_dark_colours(light, dark)
    assert light.size[1] > 400
    # ten of the eleven hues the three bands can show, where the corpus report has a
    # handful: the one-for-one rule above was checked against nearly all of them
    assert len(hues) >= (1 if variant == "funid" else 10), sorted(hues)


def test_the_dark_diagram_is_drawn_from_the_same_report_state_as_the_light_one():
    """One processed report, both palettes: the variant is an argument of the drawing
    and the renderer keeps none, so the order of the two calls changes nothing."""
    renderer = _synthetic_renderer()
    light_first = renderer.renderStackedDiagram()
    dark = renderer.renderStackedDiagram(variant="dark")
    light_again = renderer.renderStackedDiagram()

    assert light_first.tobytes() == light_again.tobytes()
    assert dark.tobytes() != light_first.tobytes()


def test_the_family_legend_follows_the_palette():
    for variant, palette in DIAGRAM_PALETTES.items():
        image = Image.new("RGB", (20, 20), (1, 2, 3))
        MatchReportRenderer().drawFamilyLegend(image, 2, 2, variant=variant)
        assert image.getpixel((2, 2)) == palette["frame"]
        assert image.getpixel((3, 3)) == palette["ground"]
        assert image.getpixel((0, 0)) == (1, 2, 3)


# --- the file names ----------------------------------------------------------------

JOB = "6a74660af8b8d2c6f83664f1"
UUID = "2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77"


@pytest.mark.parametrize(
    "filename, expected",
    [
        (f"{JOB}.dark.png", (JOB, None, None, "dark")),
        (f"{JOB}-famid_7.dark.png", (JOB, "famid", "7", "dark")),
        (f"{JOB}-samid_0.dark.png", (JOB, "samid", "0", "dark")),
        (f"{JOB}-funid_-3.dark.png", (JOB, "funid", "-3", "dark")),
        (f"{UUID}.dark.png", (UUID, None, None, "dark")),
        (f"{UUID}-famid_7.dark.png", (UUID, "famid", "7", "dark")),
        # the light names are the ones that were always written, with no variant in them
        (f"{JOB}.png", (JOB, None, None, None)),
        (f"{JOB}-famid_7.png", (JOB, "famid", "7", None)),
        (f"{UUID}-funid_-3.png", (UUID, "funid", "-3", None)),
    ],
)
def test_diagram_filenames_are_parsed_back_into_their_parts_with_the_palette(filename, expected):
    match = DIAGRAM_FILENAME_RE.match(filename)
    assert match is not None
    assert (match.group("job_id"), match.group("filter_kind"), match.group("filter_id"), match.group("variant")) == expected


@pytest.mark.parametrize("variant", ["light", "dark"])
@pytest.mark.parametrize("kind, filter_id", [(None, None), ("famid", "7"), ("samid", "0"), ("funid", "-3")])
def test_the_name_a_render_is_written_under_is_the_name_it_was_asked_for(kind, filter_id, variant):
    """The route renders what the name says and then serves that name, so a name that
    parses to parts which compose to a different name could never be answered from
    disk: the report would be parsed again on every hit."""
    asked = JOB + (f"-{kind}_{filter_id}" if kind else "") + diagram_variant_suffix(variant) + ".png"
    match = DIAGRAM_FILENAME_RE.match(asked)

    assert match is not None
    written = match.group("job_id")
    if match.group("filter_kind"):
        written += f"-{match.group('filter_kind')}_{int(match.group('filter_id'))}"
    written += diagram_variant_suffix(match.group("variant") or DEFAULT_DIAGRAM_VARIANT) + ".png"
    assert written == asked


def test_the_default_palette_keeps_its_name_and_the_others_add_a_variant():
    assert diagram_variant_suffix("light") == ""
    assert diagram_variant_suffix("dark") == ".dark"


@pytest.mark.parametrize(
    "filename",
    [
        # ".light" would be a second name, and a second cached file, for the same picture
        f"{JOB}.light.png",
        f"{JOB}-famid_7.light.png",
        f"{JOB}.Dark.png",
        f"{JOB}.DARK.png",
        f"{JOB}.darker.png",
        f"{JOB}.dark.dark.png",
        f"{JOB}.dark.light.png",
        f"{JOB}.dark.png.png",
        f"{JOB}.dark.jpg",
        f"{JOB}.dark",
        f"{JOB}..png",
        f"{JOB}.dark .png",
        # the variant goes last, after the filter
        f"{JOB}.dark-famid_7.png",
        f"{JOB}.dark_famid_7.png",
        ".dark.png",
        "dark.png.png",
        # as long as a job id may be, plus one
        "a" * 65 + ".dark.png",
        f"{JOB}.dark.png\n",
        f"../{JOB}.dark.png",
        f"sub/{JOB}.dark.png",
        f"{JOB}%2edark.png",
    ],
)
def test_a_dark_name_the_app_never_wrote_is_not_parsed_as_a_dark_name(filename):
    match = DIAGRAM_FILENAME_RE.match(filename)
    assert match is None, match.groupdict()


@pytest.mark.parametrize(
    "filename",
    [
        # the earlier "-dark" spelling is nobody's variant: a dash is part of a job id
        # (a uuid4 has four), so it is a job id that nothing has
        f"{JOB}-dark.png",
        f"{JOB}-famid_7-dark.png",
        f"{JOB}-famid_0001.dark.png",
        f"{JOB}-samid_01.dark.png",
        f"{JOB}-funid_-0.dark.png",
    ],
)
def test_a_name_off_the_grammar_is_a_job_id_and_not_a_variant(filename):
    match = DIAGRAM_FILENAME_RE.match(filename)
    if match is not None:
        assert match.group("filter_kind") is None, match.groupdict()
        assert match.group("job_id").startswith(JOB)


# --- the route renders the name it was asked for -----------------------------------

SUFFIXES = ["", "-famid_1", "-samid_1", "-funid_0"]
SAMPLE_JOB = job_id_of("matches_for_sample")


def corner_colours(response):
    """The canvas and the first frame corner of a served diagram."""
    image = png_of(response)
    return image.getpixel((0, 0)), image.getpixel((10, 10))


def test_the_route_renders_every_dark_name_on_demand(client, as_role, app):
    as_role("visitor")
    cached = []
    for suffix in SUFFIXES:
        filename = f"{SAMPLE_JOB}{suffix}.dark.png"
        response = client.get(f"/data/diagrams/{filename}")

        assert response.status_code == 200, filename
        assert response.data.startswith(b"\x89PNG\r\n\x1a\n")
        cached.append(filename)
        assert cached_diagrams(app) == sorted(cached), "the render was not cached under the name it was asked for"
        assert corner_colours(response) == (DARK_GROUND, DARK_FRAME), filename


@pytest.mark.parametrize("requested", ["light", "dark"])
@pytest.mark.parametrize("viewer", THEMES)
def test_the_palette_is_the_one_in_the_name_whoever_asks(client, as_theme, app, viewer, requested):
    """The picture is cached under the name for everybody, so the person who happens to
    ask first must not decide what it looks like."""
    as_theme(viewer)
    filename = SAMPLE_JOB + diagram_variant_suffix(requested) + ".png"
    response = client.get(f"/data/diagrams/{filename}")

    assert response.status_code == 200
    assert corner_colours(response) == (DIAGRAM_PALETTES[requested]["ground"], DIAGRAM_PALETTES[requested]["frame"])
    assert cached_diagrams(app) == [filename]


def test_the_two_palettes_are_cached_side_by_side_and_do_not_touch_each_other(client, as_role, app, corpus_mcrit):
    as_role("visitor")
    light = client.get(f"/data/diagrams/{SAMPLE_JOB}.png").data
    dark = client.get(f"/data/diagrams/{SAMPLE_JOB}.dark.png").data
    corpus_mcrit.calls.clear()
    light_again = client.get(f"/data/diagrams/{SAMPLE_JOB}.png").data
    dark_again = client.get(f"/data/diagrams/{SAMPLE_JOB}.dark.png").data

    assert cached_diagrams(app) == [f"{SAMPLE_JOB}.dark.png", f"{SAMPLE_JOB}.png"]
    assert light != dark
    assert (light_again, dark_again) == (light, dark)
    assert corpus_mcrit.calls == [], "a cached diagram still went to the backend"


def test_the_light_files_the_route_writes_hold_the_pixels_master_drew(client, as_role):
    """Same name, same picture: what the route caches for the names every page used
    before there were two palettes is the diagram master drew for them (the hashes are
    master's, from testMatchDiagramDrawing)."""
    as_role("visitor")
    cases = [
        ("", "matches_for_sample", "plain", None),
        ("-famid_2", "matches_for_sample", "famid", 2),
        ("-samid_3", "matches_for_sample", "samid", 3),
        ("-funid_6", "matches_for_sample", "funid", 6),
        ("", "matches_for_query", "plain", None),
    ]
    for suffix, report, variant, filter_id in cases:
        response = client.get(f"/data/diagrams/{job_id_of(report)}{suffix}.png")

        assert response.status_code == 200, (report, suffix)
        assert _digest(Image.open(io.BytesIO(response.data))) == MASTER_DIGESTS[(report, variant, filter_id)], (report, suffix)


def test_the_dark_diagram_is_the_size_of_the_light_one(client, as_role):
    as_role("visitor")
    for suffix in SUFFIXES:
        light = client.get(f"/data/diagrams/{SAMPLE_JOB}{suffix}.png")
        dark = client.get(f"/data/diagrams/{SAMPLE_JOB}{suffix}.dark.png")

        assert png_of(dark).size == png_of(light).size, suffix


@pytest.mark.parametrize("variant", ["light", "dark"])
@pytest.mark.parametrize("report", ["matches_for_sample", "matches_for_sample_vs"])
def test_both_palettes_come_out_the_size_the_page_reserves(app, corpus_mcrit, report, variant):
    """match_diagram_size reserves one box for the image and its dark variant."""
    trimmed = report_trimmed_to_the_fixture_pool(corpus_mcrit, report)
    with app.test_request_context("/"):
        renderer = MatchReportRenderer()
        renderer.processReport(MatchingResult.fromDict(trimmed))
        rendered = renderer.renderStackedDiagram(variant=variant)

    assert match_diagram_size(trimmed) == rendered.size


# --- and refuses what it refuses for the light one ---------------------------------

def test_a_dark_diagram_nobody_can_render_is_a_404_not_a_500(client, as_role, app, corpus_mcrit):
    """Every refusal the light names have (testMatchDiagrams), asked for in the dark one:
    the variant must not be a way round the bound on what is rendered and cached."""
    report = MatchingResult.fromDict(load("matches_for_sample.result"))
    matched_families = {sample_match.family_id for sample_match in report.sample_matches}
    matched_samples = {sample_match.sample_id for sample_match in report.sample_matches}
    unmatched_family = sorted(set(corpus_mcrit._families) - matched_families)
    unmatched_sample = sorted(set(corpus_mcrit._samples) - matched_samples)
    assert unmatched_family and unmatched_sample, "the corpus holds nothing the report did not match"
    as_role("visitor")

    names = [
        "ffffffffffffffffffffffff.dark.png",                  # no such job
        f"{SAMPLE_JOB}-famid_notanumber.dark.png",
        "not a diagram.dark.png",
        "../results/anything.dark.png",
        "*.dark.png",
        "[a-z].dark.png",
        # a filter the page would never have linked to
        f"{SAMPLE_JOB}-famid_9999.dark.png",
        f"{SAMPLE_JOB}-samid_9999.dark.png",
        f"{SAMPLE_JOB}-funid_9999.dark.png",
        # an id spelled other than the app spells it
        f"{SAMPLE_JOB}-famid_0001.dark.png",
        f"{SAMPLE_JOB}-samid_01.dark.png",
        f"{SAMPLE_JOB}-funid_-0.dark.png",
        # real families and samples that this report did not match
        f"{SAMPLE_JOB}-famid_{unmatched_family[0]}.dark.png",
        f"{SAMPLE_JOB}-samid_{unmatched_sample[0]}.dark.png",
        # jobs that hold no match report, and a query's functions that are not stored
        f"{job_id_of('cross_compare')}.dark.png",
        f"{job_id_of('unique_blocks')}.dark.png",
        f"{job_id_of('matches_for_query')}-funid_-1.dark.png",
        # the earlier spelling of the variant is a job id that does not exist
        f"{SAMPLE_JOB}-dark.png",
    ]
    for name in names:
        response = client.get(f"/data/diagrams/{name}")

        assert response.status_code in (301, 400, 404), (name, response.status_code)
        assert cached_diagrams(app) == [], f"{name} left a file behind"


def test_a_name_off_the_grammar_is_not_even_looked_up(client, as_role, app, corpus_mcrit):
    """Not a name the route would ever write, so not worth a backend call either: it is
    handed to send_from_directory, which has nothing under it."""
    as_role("visitor")
    for name in (
        f"{SAMPLE_JOB}.light.png",
        f"{SAMPLE_JOB}.Dark.png",
        f"{SAMPLE_JOB}.dark.dark.png",
        f"{SAMPLE_JOB}.dark.png.png",
        f"{SAMPLE_JOB}.dark-famid_1.png",
    ):
        corpus_mcrit.calls.clear()
        response = client.get(f"/data/diagrams/{name}")

        assert response.status_code == 404, name
        assert corpus_mcrit.calls == [], name
        assert cached_diagrams(app) == [], name


def test_a_dark_render_that_dies_leaves_no_file_behind(client, as_role, app, monkeypatch):
    as_role("visitor")

    def explode(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(MatchReportRenderer, "renderStackedDiagram", explode)
    response = client.get(f"/data/diagrams/{SAMPLE_JOB}.dark.png")

    assert response.status_code == 404
    assert cached_diagrams(app) == []


# --- the page ----------------------------------------------------------------------

class Pictures(HTMLParser):
    """The <picture> elements of a page, as the attributes of their <source>s and <img>."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.pictures = []
        self._current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "picture":
            self._current = {"children": []}
            self.pictures.append(self._current)
        elif tag in ("source", "img") and self._current is not None:
            self._current["children"].append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "picture":
            self._current = None


def diagram_picture(response):
    """The (tag, attributes) of the one <picture> that holds the match diagram."""
    found = [
        picture["children"]
        for picture in Pictures(response.data.decode()).pictures
        if any(tag == "img" and attrs.get("src", "").startswith("/data/diagrams/") for tag, attrs in picture["children"])
    ]
    assert len(found) == 1, f"{len(found)} pictures hold a diagram"
    return found[0]


#: the four result pages that show a diagram: (query string, the filter in its file name)
PAGES = [
    ("", ""),
    ("?famid=1", "-famid_1"),
    ("?samid=1", "-samid_1"),
    ("?funid=0", "-funid_0"),
]
DARK_MEDIA = {"light": None, "dark": "all", "system": "(prefers-color-scheme: dark)"}


def parts_of(name):
    match = DIAGRAM_FILENAME_RE.match(name.removeprefix("/data/diagrams/"))
    assert match is not None, f"the route would not render {name}"
    return match.group("job_id", "filter_kind", "filter_id", "variant")


@pytest.mark.parametrize("theme", THEMES)
def test_the_page_offers_the_dark_diagram_to_a_dark_page_only(client, as_theme, widened_corpus, app, theme):
    as_theme(theme)
    width, height = match_diagram_size(widened_corpus.getResultForJob(SAMPLE_JOB))

    for query, suffix in PAGES:
        response = client.get(f"/data/result/{SAMPLE_JOB}{query}")

        assert response.status_code == 200, query
        children = diagram_picture(response)
        sources = [attrs for tag, attrs in children if tag == "source"]
        images = [attrs for tag, attrs in children if tag == "img"]
        assert len(images) == 1, query
        img = images[0]
        # the <img> the page always had, whichever theme: its name, its box, its class
        assert img["src"] == f"/data/diagrams/{SAMPLE_JOB}{suffix}.png", query
        assert (img["width"], img["height"]) == (str(width), str(height)), query
        assert img["class"] == "img-fluid", query
        assert img["alt"] == "MCRIT diagram", query
        # and still not drawn by the page, in either palette
        assert cached_diagrams(app) == [], query
        if DARK_MEDIA[theme] is None:
            assert sources == [], f"{query}: a light page was offered the dark diagram, so it would fetch it"
            continue
        assert len(sources) == 1, query
        assert sources[0]["media"] == DARK_MEDIA[theme], query
        assert sources[0]["srcset"] == f"/data/diagrams/{SAMPLE_JOB}{suffix}.dark.png", query
        # the <source> has to come first, or the browser has already picked the <img>
        assert [tag for tag, _attrs in children] == ["source", "img"], query
        # through the grammar the route validates with, so that the template cannot
        # spell the name differently from the route that renders it
        job_id, filter_kind, filter_id, variant = parts_of(sources[0]["srcset"])
        assert (job_id, filter_kind, filter_id, variant) == parts_of(img["src"])[:3] + ("dark",), query
        assert parts_of(img["src"])[3] is None, query


@pytest.mark.parametrize("theme", THEMES)
def test_a_query_report_reserves_nothing_in_any_theme(client, as_theme, theme):
    as_theme(theme)
    response = client.get(f"/data/result/{job_id_of('matches_for_query')}")

    assert response.status_code == 200
    for _tag, attrs in diagram_picture(response):
        assert "width" not in attrs and "height" not in attrs, attrs


def test_every_template_that_shows_the_diagram_imports_it_with_context():
    """The <source>'s media query is a context variable (theme_dark_media). A macro
    imported without context does not see it, and the page then silently leaves out the
    dark diagram: a bright image on a dark page, with every other test still green."""
    importers = {}
    for path in sorted((ROOT / "mcritweb" / "templates").rglob("*.html")):
        for line in path.read_text().splitlines():
            if "table/match_diagram.html" in line and "import" in line:
                importers[str(path.relative_to(ROOT))] = "with context" in line
    assert len(importers) >= 4, importers
    assert [name for name, with_context in importers.items() if not with_context] == []


if __name__ == "__main__":
    unittest.main()
