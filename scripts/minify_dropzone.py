#!/usr/bin/env python3
"""Write mcritweb/static/dropzone-patched.min.js, the file the pages load, from the patched
mcritweb/static/dropzone.js.

dropzone.js is the 343 KB unminified webpack build of Dropzone 5.9.2 with the project's
patch for early content delivery (AGENTS.md). Compiling it was most of the long task the
query, submit and import pages run after their first paint. This strips its comments and
whitespace with rjsmin, which renames nothing, and keeps the licence notices the comments
carried in a header. Run it after changing dropzone.js; tests/testAssetDiet.py fails when the
two differ.

    python -m pip install "rjsmin==1.3.0"
    python scripts/minify_dropzone.py

dropzone.js stays in the repository as the source, unreferenced by any page.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "mcritweb"
SOURCE = ROOT / "static" / "dropzone.js"
TARGET = ROOT / "static" / "dropzone-patched.min.js"
#: the notices of what the build bundles, which rjsmin would drop with the other comments
HEADER = """/*! Dropzone 5.9.2 by Matias Meno, MIT License, https://github.com/dropzone/dropzone,
 * with MCRITweb's patch for early content delivery (AGENTS.md). It bundles contentloaded.js
 * by Diego Perini (MIT License) and core-js by Denis Pushkarev (MIT License).
 * Written by scripts/minify_dropzone.py from static/dropzone.js: change that file, then run
 * the script. */
"""


def minified(source):
    import rjsmin

    return HEADER + rjsmin.jsmin(source) + "\n"


def main():
    TARGET.write_text(minified(SOURCE.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"{TARGET.relative_to(ROOT.parent)}: {SOURCE.stat().st_size:,} -> {TARGET.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
