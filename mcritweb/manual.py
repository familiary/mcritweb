"""The user manual, rendered from its markdown source at request time.

`docs/manual/README.md` is now the only copy. It used to be maintained twice - the
markdown for readers on GitHub, and a hand-written Jinja duplicate for `/help` in
the running app - with nothing keeping the two in agreement, and the in-app copy is
the one users actually see. The 15 screenshots were stored twice for the same
reason. See issue #91.

Rendering here rather than generating a template at build time means there is no
generated artefact to fall out of date: editing the markdown *is* editing the page,
so the class of drift the issue describes cannot recur. The cost is one pure-Python
dependency and a parse on a rarely-visited route, and the parse is cached against
the file's mtime, so in practice it happens once per edit.

The manual lives outside the package because its primary audience reads it on
GitHub. Reaching up out of `mcritweb/` for it follows what
`get_mcritweb_version()` already does for `pyproject.toml`, and holds for the
same reason: MCRITweb is deployed from a checkout, never from a built wheel.
"""

import pathlib
import re

import markdown
from markupsafe import Markup

#: `toc` is not decorative - it gives every heading an id, and templates link to
#: `url_for('help') + '#search'`. Losing it would break those four links silently.
EXTENSIONS = ("toc", "tables", "fenced_code", "sane_lists")

MANUAL_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "manual" / "README.md"
IMAGE_DIRECTORY = MANUAL_PATH.parent / "images"

#: The prefix the markdown uses for screenshots, relative to itself.
MARKDOWN_IMAGE_PREFIX = 'src="images/'

MISSING_MANUAL = Markup(
    "<h1>Documentation</h1><p>The user manual is not available in this deployment: "
    "<code>docs/manual/README.md</code> is missing. It is part of the repository, so "
    "this usually means the checkout is incomplete.</p>"
)

#: A screenshot's source as the markdown writes it, `src="images/x.png"` or `x.webp`.
SCREENSHOT_SOURCE = re.compile(r'src="images/([^"/]+)"')

_cache = {}


def _image_size(path):
    """(width, height) from a PNG's or a lossless WebP's header, or None if the file is
    missing or neither. A lossless WebP keeps them in its first chunk, VP8L, as 14 bits
    each, less one. A lossy or extended WebP is not read: no screenshot is one, and
    test_every_screenshot_carries_the_size_of_its_file fails if one comes along."""
    try:
        with open(path, "rb") as f:
            head = f.read(25)
    except OSError:
        return None
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    if len(head) == 25 and head[:4] == b"RIFF" and head[8:16] == b"WEBPVP8L" and head[20] == 0x2F:
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _with_size(match):
    # A lazy screenshot is a 2px placeholder until it arrives. Without its size, the
    # ones loading next to a /help#section target push the section away after the
    # browser has scrolled to it. With width and height, and `height: auto` in
    # style.css, the space is reserved before the image loads.
    size = _image_size(IMAGE_DIRECTORY / match.group(1))
    if size is None:
        return match.group(0)
    return f'{match.group(0)} width="{size[0]}" height="{size[1]}"'


def render(image_url_prefix):
    """The manual as HTML, with its screenshot links pointed at `image_url_prefix`.

    The markdown refers to `images/x.png` relative to itself, which is what makes it
    render on GitHub. In the app the same files are served from a route, so the
    prefix is substituted here rather than written into the source.
    """
    try:
        stamp = MANUAL_PATH.stat().st_mtime_ns
    except OSError:
        return MISSING_MANUAL

    key = (stamp, image_url_prefix)
    if key not in _cache:
        # the input is a file in this repository, not anything a request supplied,
        # which is what makes marking the output safe defensible here
        html = markdown.markdown(MANUAL_PATH.read_text(encoding="utf-8"), extensions=list(EXTENSIONS))
        _cache.clear()
        html = SCREENSHOT_SOURCE.sub(_with_size, html)
        html = html.replace(MARKDOWN_IMAGE_PREFIX, f'src="{image_url_prefix}')
        # the first screenshot is the page's largest paint, so it is asked for at high priority,
        # which the browser otherwise gives only the logo; the rest download as they are scrolled to
        first, _, rest = html.partition("<img ")
        if rest:
            html = first + '<img fetchpriority="high" ' + rest.replace("<img ", '<img loading="lazy" ')
        _cache[key] = Markup(html)
    return _cache[key]
