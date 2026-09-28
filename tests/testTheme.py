#!/usr/bin/python
"""The per-user theme - issue #70.

#70 asked for a dark mode "set in profile, so nothing has to be dynamically
switchable", which is what these cover: a stored preference, written onto <html>
while the page is rendered, and a palette the templates actually go through.

The last test here is the one that keeps the feature working. A theme is only a
theme while every colour on the page is reachable from one place: a single
`color: black` written into a template outlives any palette, and 76 of them were
what stopped the tokenised stylesheet from being a dark mode in the first place.
"""

import logging
import os
import re
import unittest

import pytest

from mcritweb.db import UserInfo
from mcritweb.views.ScoreColorProvider import ScoreColorProvider

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

TEMPLATE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcritweb", "templates"
)


def set_theme(app, user_id, theme):
    with app.app_context():
        user_info = UserInfo.fromDb(user_id=user_id)
        user_info.theme = theme
        user_info.saveToDb()


# --- the preference reaches the page ---------------------------------------------

def test_a_page_declares_the_stored_theme(app, client, as_role):
    user_id = as_role("visitor")
    set_theme(app, user_id, "dark")

    body = client.get("/settings").get_data(as_text=True)

    assert 'data-theme="dark"' in body


def test_a_page_declares_light_for_a_user_who_has_not_chosen(client, as_role):
    as_role("visitor")

    body = client.get("/settings").get_data(as_text=True)

    assert 'data-theme="light"' in body


def test_a_logged_out_page_still_declares_a_theme(client, make_user):
    """/login renders for a caller with no row to read a preference from."""
    make_user(role="visitor")   # an empty user table redirects everything to registration

    body = client.get("/login").get_data(as_text=True)

    assert 'data-theme="light"' in body


def test_the_dark_stylesheet_is_linked_after_every_vendored_one(client, as_role):
    """Its rules have to outrank Bootstrap's, which is a matter of document order."""
    as_role("visitor")

    body = client.get("/settings").get_data(as_text=True)

    assert body.index("theme-dark.css") > body.index("bootstrap.css")
    assert body.index("theme-dark.css") > body.index("dataTables.bootstrap5.min.css")


# --- changing it -----------------------------------------------------------------

def test_changing_the_theme_persists_and_shows_on_the_next_page(app, client, as_role):
    user_id = as_role("visitor")

    response = client.post("/admin/change_theme", data={"theme": "dark"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings")
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).theme == "dark"
    assert 'data-theme="dark"' in client.get("/settings").get_data(as_text=True)


def test_a_theme_this_application_has_no_palette_for_is_refused(app, client, as_role):
    user_id = as_role("visitor")
    set_theme(app, user_id, "dark")

    response = client.post("/admin/change_theme", data={"theme": "solarized"})

    assert response.status_code == 302
    with app.app_context():
        assert UserInfo.fromDb(user_id=user_id).theme == "dark", "an unknown name was stored"


def test_a_get_cannot_change_the_theme(client, as_role):
    as_role("visitor")
    assert client.get("/admin/change_theme").status_code == 405


# --- the score palette -----------------------------------------------------------

def test_the_light_score_palette_is_unchanged():
    """The dark variant is a second ground for the same hues, not a new heat map."""
    scp = ScoreColorProvider("light")

    assert scp.getMatchHexColorByScore100(95, 0.4) == "99ccff"
    assert scp.getMatchHexColorByScore50(75, 0.4) == "ffffae"
    assert scp.getFrequencyHexColorByScore(85, 0.4) == "a5fea9"


def test_a_cell_with_no_score_is_painted_the_page_it_sits_on():
    """The reason the provider needed a dark variant at all: it blended every tint
    toward 255 and returned white for "nothing matched", so a dark result table
    came out a field of near-white blocks with the empty cells brightest."""
    assert ScoreColorProvider("light").getMatchHexColorByScore100(0) == "ffffff"
    assert ScoreColorProvider("dark").getMatchHexColorByScore100(0) == "1a1d20"
    assert ScoreColorProvider("dark").getUniqueColorScore(0) == "1a1d20"


def test_a_dark_score_tint_stays_dark():
    """Every step of the map, so a single mis-signed blend cannot slip through."""
    scp = ScoreColorProvider("dark")
    for score in range(0, 101, 5):
        tint = scp.getMatchHexColorByScore100(score, 0.4)
        channels = [int(tint[index:index + 2], 16) for index in (0, 2, 4)]
        assert max(channels) < 160, f"score {score} paints {tint}, too light for a dark page"


def test_an_unknown_theme_falls_back_to_the_light_ground():
    assert ScoreColorProvider("solarized").getMatchHexColorByScore100(0) == "ffffff"


def test_the_match_diagram_is_drawn_on_the_ground_it_will_be_shown_on():
    """The PIL renderer has the same white-as-paper assumption the score provider
    had, and its output is a cached PNG - so a light diagram on a dark result page
    cannot be fixed by CSS afterwards. Imported inside the test: the module pulls in
    PIL and mcrit, which issue #88 keeps out of a plain helper import."""
    from mcritweb.views.MatchReportRenderer import MatchReportRenderer

    assert MatchReportRenderer().ground == (0xff, 0xff, 0xff)
    assert MatchReportRenderer("dark").ground == (0x1a, 0x1d, 0x20)
    # index 0 of the frequency map is "no families matched" - the paper, not white
    assert MatchReportRenderer().frequency_color_map[0] == (0xff, 0xff, 0xff)
    assert MatchReportRenderer("dark").frequency_color_map[0] == (0x1a, 0x1d, 0x20)


# --- the templates go through the palette ----------------------------------------

#: Declarations whose value may name a colour. A shorthand is included because that
#: is how `border: 2px dashed black` used to get written.
COLOUR_PROPERTIES = (
    "color", "background", "background-color", "outline", "box-shadow",
    "border", "border-color", "border-top", "border-right", "border-bottom", "border-left",
)

#: Tokens that appear in those declarations and are not colours.
NON_COLOUR_TOKENS = {
    "auto", "content-box", "currentcolor", "dashed", "dotted", "double", "groove",
    "hidden", "inherit", "initial", "inset", "medium", "none", "no-repeat", "outset",
    "padding-box", "border-box", "repeat", "ridge", "solid", "thick", "thin",
    "transparent", "unset", "center", "left", "right", "top", "bottom",
    # what a Jinja expression is replaced by: the score palettes, which follow the
    # theme in ScoreColorProvider and are covered by the tests above
    "computed",
}

#: Anything the view computes. Substituted out before the CSS is parsed, so that a
#: score colour is not read as a literal - and neither is the `{`/`}` of a `{% if %}`.
JINJA = re.compile(r"#?\{[{%].*?[}%]\}", re.DOTALL)

STYLE_ATTRIBUTE = re.compile(r"""style\s*=\s*"([^"]*)\"""")
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
#: `dropzone.style('...')` in Jinja - a style attribute the macro writes for us.
STYLE_CALL = re.compile(r"\.style\('([^']*)'\)")
TOKEN = re.compile(r"[#\w().,%-]+")


def style_texts(source):
    """Every place a template can spell out CSS."""
    for match in STYLE_ATTRIBUTE.finditer(source):
        yield match.group(1)
    for match in STYLE_BLOCK.finditer(source):
        yield match.group(1)
    for match in STYLE_CALL.finditer(source):
        yield match.group(1)


def literal_colours(style_text):
    """Tokens in a colour-valued declaration that are neither var() nor a keyword."""
    findings = []
    for declaration in re.split(r"[;{}]", JINJA.sub(" computed ", style_text)):
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        if prop.strip().lower() not in COLOUR_PROPERTIES:
            continue
        for token in TOKEN.findall(value):
            lowered = token.lower()
            if lowered.startswith(("var(", "url(", "calc(")) or lowered in NON_COLOUR_TOKENS:
                continue
            if re.fullmatch(r"[\d.]+(px|em|rem|%|pt|vh|vw)?", lowered):
                continue
            findings.append(f"{prop.strip()}: {token}")
    return findings


def template_files():
    for directory, _, filenames in os.walk(TEMPLATE_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                yield os.path.join(directory, filename)


@pytest.mark.parametrize("path", list(template_files()), ids=lambda path: os.path.basename(path))
def test_no_template_spells_out_a_colour(path):
    """A colour written into a template cannot follow a theme.

    `static/style.css` declares the palette and `static/theme-dark.css` redefines it;
    a literal here is outside both, and renders the same on a dark page as on a light
    one - which is what 76 of these did before #70 was finished.
    """
    with open(path, encoding="utf-8") as template:
        source = template.read()

    findings = [finding for text in style_texts(source) for finding in literal_colours(text)]

    assert not findings, (
        f"{os.path.relpath(path, TEMPLATE_ROOT)} paints a colour the theme cannot reach: "
        f"{findings}. Name it in static/style.css and use var(--name) here."
    )


if __name__ == "__main__":
    unittest.main()
