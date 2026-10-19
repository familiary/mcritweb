#!/usr/bin/python
"""The icon webfont has to start downloading with the rest of the page.

A font is not a resource the browser's preload scanner can see. It is discovered only
once the stylesheet that names it has been parsed *and* the first layout has found an
element that draws with it, which puts it a full render-blocking round behind everything
else in <head>. Measured on this app with headless Chromium against a local server: the
landing page fires all seven stylesheets, all eight scripts and both logos in one burst,
and `fa-solid-900.woff2` only follows a clear gap after the last of them - 145 ms at a
390x844 viewport, 152 ms at 1280x800, and that against a server one hop away. all.css
declares `font-display: block`, so until it lands every `<i class="fa-...">` is a blank
box - the navbar's Explore entries among them, which is what issue #62 asks about.

The one attribute that is easy to get wrong is `crossorigin`. Fonts are fetched in CORS
mode even same-origin, so a preload without it does not match the fetch the CSS later
makes and the file is downloaded twice: dropping the attribute and re-measuring gave two
requests for the same 150 KB, one at +221 ms and one at +400 ms. Hence a test of its own.

The preload is emitted for signed-in pages only, and the reason is worth spelling out
because the first version of this file got it wrong. It guarded the invariant "every
page really needs the solid face" with a *markup* match for `class="fa-solid"`, which an
anonymous page satisfies - and which proves nothing, because the one solid glyph on
/login and /register is the navbar help icon, and that sits inside `.navbar-collapse`.
Below 992px Bootstrap gives that `display: none`, so the glyph has no box and the font
is fetched and thrown away. Measured with headless Chromium: /login at 390x844 requested
fa-solid-900.woff2, 150 KB, and `document.fonts` reported nothing loaded - on the two
pages a first-time visitor sees, at the width where the transfer costs most.

A markup assertion cannot tell a rendered box from a hidden one, so this file no longer
pretends to. What it can check is the structural fact the measurement rests on: a
signed-in page draws solid icons in its *content*, outside the collapsible navbar, so
the font is needed at every width. The layout claim itself is measured out of band.
"""

import logging
import re
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PRELOAD_LINK = re.compile(rb"<link\s[^>]*rel=\"preload\"[^>]*>")
SOLID_FONT = b"webfonts/fa-solid-900.woff2"


def preload_links(html):
    return PRELOAD_LINK.findall(html)


@pytest.fixture
def signed_in(client, as_role):
    """The preload only appears for a signed-in user - see the module docstring."""
    as_role("visitor")
    response = client.get("/")
    assert response.status_code == 200
    return response


def test_the_icon_font_is_preloaded(signed_in):
    links = [link for link in preload_links(signed_in.data) if SOLID_FONT in link]
    assert links, f"no preload for the icon font in:\n{preload_links(signed_in.data)}"
    link = links[0]
    assert b'as="font"' in link, link
    assert b'type="font/woff2"' in link, link


def test_the_preload_is_crossorigin(signed_in):
    """Without it the font is fetched twice - once for the preload in no-cors mode and
    once for the CSS in cors mode - which costs more than the preload saves."""
    link = next(link for link in preload_links(signed_in.data) if SOLID_FONT in link)
    assert b"crossorigin" in link, link


def test_the_preloaded_font_is_actually_served(client, signed_in):
    """A FontAwesome upgrade that renames the file would otherwise leave a preload
    pointing at a 404 that nothing else in the app notices."""
    link = next(link for link in preload_links(signed_in.data) if SOLID_FONT in link)
    href = re.search(rb'href="([^"]+)"', link).group(1).decode()

    font = client.get(href)
    assert font.status_code == 200, f"{href} -> {font.status_code}"
    assert len(font.get_data()) > 0


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_an_anonymous_page_does_not_preload_the_font(client, make_user, path):
    """These two draw exactly one solid glyph, the navbar help icon, and it is inside
    .navbar-collapse - so below 992px it has no box and the 150 KB is fetched and
    discarded. They are also the two pages a first-time visitor sees.

    make_user rather than as_role: a user has to exist or the app treats the instance as
    unconfigured and redirects to registration, but the client must stay signed out."""
    make_user(role="visitor")
    response = client.get(path)

    assert response.status_code == 200
    fonts = [link for link in preload_links(response.data) if b'as="font"' in link]
    assert fonts == [], f"{path} preloads a font it may never draw with: {fonts}"


def test_a_signed_in_page_draws_icons_outside_the_collapsing_navbar(client, as_role):
    """What makes the preload pay: the icons on a signed-in page are in the content, so
    they are drawn at every width, unlike the navbar's which vanish below 992px.

    This is a structural check, not a rendering one - it cannot prove a box exists. It
    is here to fail loudly if the content icons ever go, which is the assumption the
    measurement in the docstring rests on.
    """
    as_role("visitor")
    body = client.get("/explore/samples").data

    after_the_navbar = body[body.index(b"</nav>"):]
    assert re.search(rb'class="(fas|fa-solid)[ "]', after_the_navbar), \
        "no solid icon outside the navbar - the preload may no longer be paying for itself"


def test_only_the_face_every_page_draws_with_is_preloaded(signed_in):
    """Preloading the regular, brands or v4-compatibility faces would warn in the console
    and waste the transfer on every page that never uses them."""
    fonts = [link for link in preload_links(signed_in.data) if b'as="font"' in link]
    assert len(fonts) == 1, fonts
    for unused in (b"fa-regular-400", b"fa-brands-400", b"fa-v4compatibility"):
        assert unused not in fonts[0], fonts[0]


if __name__ == "__main__":
    unittest.main()
