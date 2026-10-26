"""The white space a page does not need, dropped before the page is gzipped.

The templates are indented for whoever reads them, and a rendered page carries every
indent and blank line of them, and the comments of its inline scripts: a quarter of a
result page's bytes, and about 1.6 KB of the 16 KB it weighs gzipped. The first round
trip of a slow connection carries about 14.6 KB, which is where that decides whether the
page can be painted from it (docs/adr/0022-pages-are-thinned-before-they-are-gzipped.md).

- Between tags, a run of white space that holds a line break becomes the line break. A
  browser shows a run of white space as one space wherever it sits, so the page looks the
  same; a run without a line break, which may separate two words, is left as it is.
- What is shown or run as written is not touched: `pre`, `textarea` and `style`, comments,
  and everything inside a tag, where an attribute value may hold a line break of its own
  (hint.css prints one).
- In an inline script, the indentation, the blank lines and the lines that are only a `//`
  comment. A script with a line break inside a template literal, a block comment or a line
  continued by a backslash is left alone, since there a line's indent or its first
  characters may be part of the value.

Only a page, a response that starts with a doctype: the dot graph is `text/html` too,
and is text. Off with MCRITWEB_THIN_PAGES.
"""

import re

from flask import current_app

#: a start tag or an end tag, with its attribute values, which may hold a `>` or a line break
TAG = r"<[a-zA-Z/!](?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
#: a run of HTML white space (not every character \s matches: the no-break space is text)
BREAK = r"[ \t\r\f]*\n[ \t\r\n\f]*"
PATTERN = re.compile(
    r"<script\b(?P<attributes>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>(?P<script>.*?)</script\s*>"
    r"|<(?P<raw>pre|textarea|style)\b.*?</(?P=raw)\s*>"
    r"|<!--.*?-->"
    rf"|{TAG}"
    rf"|(?P<break>{BREAK})",
    re.S | re.I,
)
#: the type of a script, when it has one
SCRIPT_TYPE = re.compile(r"""(?<![\w-])type\s*=\s*["']?([^\s"'>]+)""", re.I)
CODE_TYPES = {"module", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript"}
#: places where a line of a script may not be touched
TEMPLATE_LITERAL = re.compile(r"`(?:[^`\\]|\\.)*\n(?:[^`\\]|\\.)*`")
KEPT_SCRIPT = re.compile(r"/\*|\\\n")
#: the bytes of a page beyond which it is left as it is
MAX_SIZE = 4 * 1024 * 1024


def thin_script(source):
    """The script without the indentation, blank lines and `//` comment lines."""
    if "\n" not in source or KEPT_SCRIPT.search(source) or TEMPLATE_LITERAL.search(source):
        return source
    lines = [line.strip() for line in source.split("\n")]
    return "\n" + "\n".join(line for line in lines if line and not line.startswith("//")) + "\n"


def thin(html):
    """The page with its white space collapsed and its inline scripts thinned."""
    def replace(match):
        if match.group("break"):
            return "\n"
        if match.group("script") is not None:
            declared = SCRIPT_TYPE.search(match.group("attributes"))
            if declared and declared.group(1).lower() not in CODE_TYPES:
                return match.group(0)
            return f"<script{match.group('attributes')}>{thin_script(match.group('script'))}</script>"
        return match.group(0)
    return PATTERN.sub(replace, html)


def is_a_page(response):
    return (
        response.mimetype == "text/html"
        and response.status_code != 304
        and not response.direct_passthrough
        and response.content_length is not None
        and response.content_length <= MAX_SIZE
    )


def thin_response(response):
    if current_app.config.get("MCRITWEB_THIN_PAGES", True) and is_a_page(response):
        html = response.get_data(as_text=True)
        if html.lstrip().lower().startswith("<!doctype html"):
            response.set_data(thin(html))
    return response


def register(app):
    # after compression's, which runs the other way round: this has run when it does
    app.after_request(thin_response)
