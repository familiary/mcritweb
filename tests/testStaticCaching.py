#!/usr/bin/python
"""A static file is kept by the browser until it changes.

Flask sent every /static/ file with `Cache-Control: no-cache`, so the browser asked for
each one again on every page: on /explore/samples after a visit to /explore/families, all
16 came back 304, five of them stylesheets the first paint waits for. At 150 ms round trips
that put the second page's first paint at 550 ms on a phone, against 454 ms once they were
kept (medians of 18 runs). mcritweb/static_cache.py puts a digest of the file in every URL url_for builds for
it, and the answer to such a URL may be kept for a year; its docstring says which files
go without one, and why.
"""

import logging
import os
import pathlib
import posixpath
import re
import unittest

import pytest
from conftest import log_in_as
from fixtureData import job_id_of

from mcritweb import static_cache
from mcritweb.db import UserInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"

STATIC_URL = re.compile(r"/static/([^\"'\s<>?)]+)(\?[^\"'\s<>)]*)?")
#: pages between them naming every kind of static URL: stylesheets, the font preload, logos,
#: scripts queued with later(), the Dropzone loader's data- attributes, the CFG pages'
#: scripts and layout worker, the cross compare's jQuery UI
PAGES = [
    "/", "/explore/samples", "/settings", "/help", "/analyze/query", "/data/submit", "/data/import",
    "/explore/functions/6", "/data/matches/function/98/883", "cross_compare",
]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def url_of(path):
    return f"/data/result/{job_id_of(path)}" if path == "cross_compare" else path


def static_urls(page):
    """(file, query) for each static URL on the page."""
    return STATIC_URL.findall(page)


def cache_control(response):
    return {directive.strip() for directive in response.headers.get("Cache-Control", "").split(",") if directive.strip()}


def named_by_templates():
    """The static files a template names with url_for."""
    named = set()
    for template in TEMPLATES.rglob("*.html"):
        named.update(re.findall(r"url_for\('static',\s*filename='([^']+)'\)", template.read_text()))
    return named


def named_by_static_files():
    """The static files that another static file the templates load names by a relative URL:
    a stylesheet's url(), a worker's importScripts()."""
    loaded = named_by_templates()
    named = set()
    for name in loaded:
        source = (STATIC / name).read_text(errors="replace")
        if name.endswith(".css"):
            references = re.findall(r"url\(\s*[\"']?([^\"')]+)", source)
        elif name.endswith(".js"):
            references = [ref for call in re.findall(r"importScripts\(([^)]*)\)", source) for ref in re.findall(r"[\"']([^\"']+)[\"']", call)]
        else:
            continue
        for reference in references:
            if not reference.startswith(("data:", "/", "#", "http:", "https:")):
                named.add(posixpath.normpath(posixpath.join(posixpath.dirname(name), reference.split("?")[0].split("#")[0])))
    return named


@pytest.mark.parametrize("path", PAGES)
def test_every_static_url_on_a_page_carries_its_files_version(client, as_role, path):
    """url_for adds it, in every place a page names a file: a <link>, an <img> and its dark
    <source>, a later() call, a data- attribute a script fetches from."""
    as_role("contributor")
    response = client.get(url_of(path))
    assert response.status_code == 200, path
    urls = static_urls(response.get_data(as_text=True))
    assert urls, path
    for name, query in urls:
        if name.startswith(static_cache.NAMED_BY_STATIC_FILES):
            assert query == "", f"{path}: {name} is named by a static file, which asks for it without a version"
        else:
            assert query == f"?v={static_cache.version_of(str(STATIC), name)}", f"{path}: /static/{name}{query}"


def test_the_versioned_urls_reach_every_kind_of_reference(client, as_role):
    """The ones a version would be easy to miss on: queued scripts, the Dropzone loader's
    data- attributes, the layout worker, a dark logo's <source>."""
    as_role("contributor")
    samples = client.get("/explore/samples").get_data(as_text=True)
    assert re.search(r'later\("/static/jquery\.js\?v=\w{12}"\)', samples)
    assert re.search(r'data-dropzone-js="/static/dropzone-patched\.min\.js\?v=\w{12}"', samples)
    assert re.search(r'data-dropzone-css="/static/dropzone\.min\.css\?v=\w{12}"', samples)
    function = client.get("/explore/functions/6").get_data(as_text=True)
    assert re.search(r'data-layout-worker="/static/trace_CFG/layout_worker\.js\?v=\w{12}"', function)


def test_a_dark_page_versions_its_dark_files(client, app, make_user):
    user_id = make_user(role="visitor")
    with app.app_context():
        user = UserInfo.fromDb(user_id=user_id)
        user.theme = "dark"
        user.saveToDb()
    log_in_as(client, user_id)
    page = client.get("/explore/samples").get_data(as_text=True)
    assert re.search(r'href="/static/theme-dark\.css\?v=\w{12}"', page)
    assert re.search(r'srcset="/static/d20_mcrit_cabaret_dark\.png\?v=\w{12}"', page)


def test_a_file_another_static_file_names_is_asked_for_by_the_url_that_file_uses():
    """A stylesheet's url() and a worker's importScripts() carry no version, so a page that
    names the same file has to leave it off too, or the browser fetches the file twice:
    the font preload and the font face, the CFG pages' dagre script and the layout worker's."""
    assert "trace_CFG/assets/plus.png" in named_by_static_files(), "the scanner finds no url()"
    named_twice = named_by_static_files() & named_by_templates()
    assert {"webfonts/subset/fa-solid-900.woff2", "trace_CFG/lib/dagre-d3.min.js"} <= named_twice
    for name in named_twice:
        assert static_cache.version_of(str(STATIC), name) is None, f"{name} is named by a static file as well: add it to NAMED_BY_STATIC_FILES"


def test_the_preloaded_font_is_the_one_the_stylesheet_asks_for(client, as_role):
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    preload = re.search(r'<link rel="preload" as="font"[^>]* href="([^"]+)"', page).group(1)
    stylesheet = (STATIC / "css" / "fontawesome-subset.css").read_text()
    assert '"../webfonts/subset/fa-solid-900.woff2"' in stylesheet
    assert preload == "/static/webfonts/subset/fa-solid-900.woff2"


def test_a_versioned_file_is_kept_for_a_year(client, as_role):
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    href = re.search(r'href="(/static/style\.css\?v=\w+)"', page).group(1)
    response = client.get(href)
    assert response.status_code == 200
    assert cache_control(response) == {"public", "max-age=31536000", "immutable"}


@pytest.mark.parametrize("url", [
    "/static/style.css",
    "/static/style.css?v=000000000000",
    "/static/webfonts/subset/fa-solid-900.woff2",
    "/static/trace_CFG/assets/plus.png",
])
def test_any_other_static_url_is_revalidated_after_an_hour(client, url):
    """No version, or not the file's: the file may change under the same URL, so it is
    never kept as if it could not."""
    response = client.get(url)
    assert response.status_code == 200, url
    assert cache_control(response) == {"public", "max-age=3600"}, url


def test_a_missing_file_is_not_kept(client):
    response = client.get("/static/no-such-file.css?v=000000000000")
    assert response.status_code == 404
    assert "max-age" not in response.headers.get("Cache-Control", "")


def test_the_diagrams_and_the_manuals_screenshots_are_revalidated_on_every_view(client, as_role):
    """They are sent with send_from_directory as well, and a diagram is re-rendered under
    the same name when the cache is cleared."""
    as_role("visitor")
    diagram = client.get(f"/data/diagrams/{job_id_of('matches_for_sample')}.png")
    assert diagram.status_code == 200 and diagram.mimetype == "image/png"
    assert cache_control(diagram) == {"no-cache"}
    from mcritweb import manual
    screenshot = sorted(os.listdir(manual.IMAGE_DIRECTORY))[0]
    response = client.get(f"/help/images/{screenshot}")
    assert response.status_code == 200
    assert cache_control(response) == {"no-cache"}


def test_a_versioned_file_is_still_gzipped_and_varies_by_encoding(client, as_role):
    """A cache that keeps a file for a year has to know that a client which does not take
    gzip was sent another body, or it hands that one to everyone. So the plain answer says
    Vary: Accept-Encoding as well, which it did not."""
    as_role("visitor")
    href = re.search(r'href="(/static/style\.css\?v=\w+)"', client.get("/explore/samples").get_data(as_text=True)).group(1)
    gzipped = client.get(href, headers={"Accept-Encoding": "gzip"})
    assert gzipped.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in gzipped.headers.get("Vary", "")
    assert "immutable" in cache_control(gzipped)
    plain = client.get(href, headers={"Accept-Encoding": "identity"})
    assert plain.headers.get("Content-Encoding") is None
    assert "Accept-Encoding" in plain.headers.get("Vary", ""), plain.headers
    again = client.get(href, headers={"Accept-Encoding": "gzip", "If-None-Match": gzipped.headers["ETag"]})
    assert again.status_code == 304
    assert "Accept-Encoding" in again.headers.get("Vary", "")
    assert cache_control(again) == {"public", "max-age=31536000", "immutable"}


def test_a_changed_file_gets_a_new_version(tmp_path):
    """What a `git pull` and a restart, or an edit under `flask run`, has to do to the URL."""
    sheet = tmp_path / "sheet.css"
    sheet.write_text("a { color: inherit }")
    first = static_cache.version_of(str(tmp_path), "sheet.css")
    assert re.fullmatch(r"[0-9a-f]{12}", first)

    sheet.write_text("a { color: currentcolor }")
    stat = sheet.stat()
    os.utime(sheet, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert static_cache.version_of(str(tmp_path), "sheet.css") not in (None, first)

    # the same bytes written again keep their URL, and with it what the browser holds
    sheet.write_text("a { color: inherit }")
    os.utime(sheet, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2 * 10**9))
    assert static_cache.version_of(str(tmp_path), "sheet.css") == first


@pytest.mark.parametrize("name", ["missing.css", "../outside.css", ""])
def test_a_file_that_is_not_there_gets_no_version(tmp_path, name):
    (tmp_path.parent / "outside.css").write_text("a {}")
    assert static_cache.version_of(str(tmp_path), name) is None


if __name__ == "__main__":
    unittest.main()
