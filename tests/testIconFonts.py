#!/usr/bin/python
"""The icon webfonts the pages load are cut to the glyphs the templates draw.

base.html preloads the solid face on every signed-in page, and it was the whole 150 KB
font for the 35 of its 1,389 glyphs the templates use. scripts/subset_fontawesome.py now
writes the solid and regular faces cut to those glyphs, and to the text the faces cover,
to static/webfonts/subset/, and the subset CSS draws from them. Cutting needs fontTools with brotli, which CI does not have,
so these read the fonts with the standard library: the TrueType file's character map
directly, and of the woff2 its table directory, which woff2 does not compress.
"""

import logging
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import subset_fontawesome  # noqa: E402

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

STATIC = ROOT / "mcritweb" / "static"
CSS = STATIC / "css" / "fontawesome-subset.css"
WEBFONTS = STATIC / "webfonts"
#: the faces the script cuts, written out so that the tests below collect without it
FACES = ("fa-solid-900", "fa-regular-400")


def sfnt_tables(data):
    """{tag: (offset, length)} of a TrueType file."""
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for index in range(num_tables):
        tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * index:28 + 16 * index])
        tables[tag.decode("latin-1")] = (offset, length)
    return tables


def character_map(data):
    """The codepoints a TrueType file has a glyph for, from its Windows Unicode cmap."""
    offset, _ = sfnt_tables(data)["cmap"]
    num_subtables = struct.unpack(">H", data[offset + 2:offset + 4])[0]
    subtables = {}
    for index in range(num_subtables):
        platform, encoding, start = struct.unpack(">HHI", data[offset + 4 + 8 * index:offset + 12 + 8 * index])
        subtables[(platform, encoding)] = offset + start
    codepoints = set()
    if (3, 10) in subtables:
        start = subtables[(3, 10)]
        assert struct.unpack(">H", data[start:start + 2])[0] == 12
        groups = struct.unpack(">I", data[start + 12:start + 16])[0]
        for index in range(groups):
            first, last, _ = struct.unpack(">III", data[start + 16 + 12 * index:start + 28 + 12 * index])
            codepoints.update(range(first, last + 1))
        return codepoints
    start = subtables[(3, 1)]
    assert struct.unpack(">H", data[start:start + 2])[0] == 4
    segments = struct.unpack(">H", data[start + 6:start + 8])[0] // 2
    ends = struct.unpack(f">{segments}H", data[start + 14:start + 14 + 2 * segments])
    starts = struct.unpack(f">{segments}H", data[start + 16 + 2 * segments:start + 16 + 4 * segments])
    deltas = struct.unpack(f">{segments}h", data[start + 16 + 4 * segments:start + 16 + 6 * segments])
    ranges_at = start + 16 + 6 * segments
    ranges = struct.unpack(f">{segments}H", data[ranges_at:ranges_at + 2 * segments])
    for segment in range(segments):
        for codepoint in range(starts[segment], ends[segment] + 1):
            if codepoint == 0xFFFF:
                continue
            if ranges[segment] == 0:
                glyph = (codepoint + deltas[segment]) & 0xFFFF
            else:
                at = ranges_at + 2 * segment + ranges[segment] + 2 * (codepoint - starts[segment])
                glyph = struct.unpack(">H", data[at:at + 2])[0]
                glyph = (glyph + deltas[segment]) & 0xFFFF if glyph else 0
            if glyph:
                codepoints.add(codepoint)
    return codepoints


#: the tags woff2 numbers 0-62 in its table directory, in the order of its specification
WOFF2_KNOWN_TAGS = (
    "cmap head hhea hmtx maxp name OS/2 post cvt  fpgm glyf loca prep CFF  VORG EBDT EBLC gasp hdmx kern LTSH PCLT VDMX "
    "vhea vmtx BASE GDEF GPOS GSUB EBSC JSTF MATH CBDT CBLC COLR CPAL SVG  sbix acnt avar bdat bloc bsln cvar fdsc feat "
    "fmtx fvar gvar hsty just lcar mort morx opbd prop trak Zapf Silf Glat Gloc Feat Sill"
)
WOFF2_TAGS = [WOFF2_KNOWN_TAGS[index:index + 4] for index in range(0, len(WOFF2_KNOWN_TAGS), 5)]


def _base128(data, position):
    value = 0
    for _ in range(5):
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("UIntBase128 longer than five bytes")


def woff2_table_lengths(data):
    """{tag: length of the table before compression} from a woff2 file's table directory."""
    assert data[:4] == b"wOF2"
    num_tables = struct.unpack(">H", data[12:14])[0]
    position, lengths = 48, {}
    for _ in range(num_tables):
        flags = data[position]
        position += 1
        if flags & 0x3F == 63:
            tag = data[position:position + 4].decode("latin-1")
            position += 4
        else:
            tag = WOFF2_TAGS[flags & 0x3F]
        length, position = _base128(data, position)
        transformed = (flags >> 6) == 0 if tag in ("glyf", "loca") else (flags >> 6) != 0
        if transformed:
            _, position = _base128(data, position)
        lengths[tag] = length
    return lengths


def test_the_table_lists_are_the_specifications():
    assert len(WOFF2_TAGS) == 63
    assert WOFF2_TAGS[10:12] == ["glyf", "loca"] and WOFF2_TAGS[62] == "Sill"


def test_the_script_cuts_the_faces_these_tests_read():
    assert subset_fontawesome.FACES == FACES


def test_the_subset_css_draws_the_two_faces_from_their_cut_files():
    css = CSS.read_text()
    for face in FACES:
        whole = re.findall(rf'url\("\.\./webfonts/{face}\.', css)
        cut = re.findall(rf'url\("\.\./webfonts/subset/{face}\.(woff2|ttf)"\)', css)
        assert whole == [], f"the subset CSS still draws {face} from the whole font"
        # every @font-face of the face names both formats
        assert cut and cut.count("woff2") == cut.count("ttf"), cut


@pytest.mark.parametrize("face", FACES)
def test_each_cut_face_has_exactly_the_glyphs_the_css_draws_and_its_text(face):
    """What makes the cut safe: every codepoint the CSS draws that the whole face has is
    still there, and so is the text it covers, which Chromium lays the icons out by. Nothing
    else is, so a font left behind after an icon is added fails."""
    drawn = set(subset_fontawesome.used_codepoints(CSS.read_text()))
    whole = character_map((WEBFONTS / f"{face}.ttf").read_bytes())
    cut = character_map((WEBFONTS / "subset" / f"{face}.ttf").read_bytes())
    assert drawn & whole, f"the CSS draws nothing from {face}"
    assert subset_fontawesome.TEXT_CODEPOINTS & whole, f"{face} maps no text"
    assert cut == (drawn | subset_fontawesome.TEXT_CODEPOINTS) & whole


def test_every_codepoint_the_css_draws_is_in_the_solid_face():
    drawn = set(subset_fontawesome.used_codepoints(CSS.read_text()))
    assert drawn - character_map((WEBFONTS / "subset" / "fa-solid-900.ttf").read_bytes()) == set()


@pytest.mark.parametrize("face", FACES)
def test_the_woff2_holds_the_tables_of_the_truetype_file(face):
    """The woff2 is what a browser loads, and only a brotli decoder can read its glyphs.
    Its directory gives each table's length before compression, which has to be the
    TrueType file's for the two to have come from one cut. `glyf` is the exception: a
    woff2 decoder rebuilds it, padding each glyph its own way, so it may come out up to
    two bytes a glyph longer. maxp and hmtx, whose lengths follow the number of glyphs,
    are compared exactly."""
    woff2 = woff2_table_lengths((WEBFONTS / "subset" / f"{face}.woff2").read_bytes())
    truetype_data = (WEBFONTS / "subset" / f"{face}.ttf").read_bytes()
    tables = sfnt_tables(truetype_data)
    truetype = {tag: length for tag, (_, length) in tables.items()}
    num_glyphs = struct.unpack(">H", truetype_data[tables["maxp"][0] + 4:tables["maxp"][0] + 6])[0]
    assert sorted(woff2) == sorted(truetype)
    assert {tag: length for tag, length in woff2.items() if tag != "glyf"} == {tag: length for tag, length in truetype.items() if tag != "glyf"}
    assert 0 <= woff2["glyf"] - truetype["glyf"] <= 2 * num_glyphs


@pytest.mark.parametrize("face", FACES)
def test_the_cut_faces_are_a_fraction_of_the_whole_ones(face):
    for suffix in (".woff2", ".ttf"):
        whole = (WEBFONTS / f"{face}{suffix}").stat().st_size
        cut = (WEBFONTS / "subset" / f"{face}{suffix}").stat().st_size
        assert cut < whole / 4, f"{face}{suffix}: {cut} of {whole} bytes"


@pytest.mark.parametrize("face", FACES)
def test_the_cut_faces_keep_the_copyright_and_the_name(face):
    data = (WEBFONTS / "subset" / f"{face}.ttf").read_bytes()
    for record in ("Copyright (c) Font Awesome", "Font Awesome 6 Free"):
        assert record.encode("utf-16-be") in data, record


def test_the_preloaded_face_is_the_cut_one():
    base = (ROOT / "mcritweb" / "templates" / "base.html").read_text()
    assert "filename='webfonts/subset/fa-solid-900.woff2'" in base
    assert "filename='webfonts/fa-solid-900.woff2'" not in base
