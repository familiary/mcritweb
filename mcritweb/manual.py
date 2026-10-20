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

_cache = {}


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
        _cache[key] = Markup(html.replace(MARKDOWN_IMAGE_PREFIX, f'src="{image_url_prefix}'))
    return _cache[key]
