"""How a user's colour theme (#70) reaches the page.

The theme is a stored preference, so the server knows it before it renders a byte and
nothing has to switch after load. What it does not always know is the outcome: "system"
leaves the choice to the browser's light/dark preference. Both cases come down to one
media query, which base.html puts on the <link> of static/theme-dark.css and every
`<picture>` with a dark variant puts on its <source>, so the stylesheet and the images
switch together, and live when the system preference changes.
"""

from mcritweb.db import DEFAULT_THEME, normalize_theme

#: when the dark palette applies, as a media query. None: never, and the stylesheet is
#: not linked at all.
DARK_MEDIA = {
    "light": None,
    "dark": "all",
    "system": "(prefers-color-scheme: dark)",
}

#: <meta name="color-scheme">: what the browser paints with before any stylesheet has
#: arrived - the canvas, scrollbars and form controls
COLOR_SCHEME = {
    "light": "light",
    "dark": "dark",
    "system": "light dark",
}


def theme_context(theme=DEFAULT_THEME):
    """The values base.html and the image macros render a theme with."""
    theme = normalize_theme(theme)
    return {
        "theme": theme,
        "theme_dark_media": DARK_MEDIA[theme],
        "theme_color_scheme": COLOR_SCHEME[theme],
    }
