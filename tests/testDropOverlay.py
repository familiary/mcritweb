#!/usr/bin/python
"""The drop overlay is fetched on the first drag, not carried by every page.

Every signed-in page but the upload pages carried the overlay's dialog, its upload form,
the form's Dropzone options and the family type-ahead's tag: about 1.7 to 1.9 KB of the
page gzipped, for the moment a file is dragged over it. On a result page that is the
difference between fitting the first round trip, about 14.6 KB (ADR-0022), with room to
spare or not. The page has a slot now, and static/upload_form.js fetches the overlay from
data.drop_overlay on the first drag (ADR-0023). testDropOverlayBrowser.py drags files.
"""

import gzip
import json
import re
from html import unescape
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of
from testAccessibility import ADMIN_PAGES, VISITOR_PAGES

from mcritweb.csrf import ENABLED_KEY, FIELD_NAME, unmask

RESULT = f"/data/result/{job_id_of('matches_for_sample')}"

#: signed-in pages that are not an upload form, each with a different amount around the slot
OVERLAY_PAGES = ["/", "/explore/samples", "/explore/samples/0", RESULT, "/data/export", "/settings"]

SLOT = re.compile(r'<div id="drop-overlay-slot" hidden data-overlay="([^"]+)"[^>]*></div>')
OPTIONS = re.compile(r"data-dropzone-options='([^']*)'")


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def gzipped(markup):
    # the level mcritweb/compression.py sends pages at
    return len(gzip.compress(markup.encode(), 6))


@pytest.mark.parametrize("path", OVERLAY_PAGES)
def test_a_signed_in_page_has_a_slot_for_the_overlay_and_not_the_overlay(client, as_role, path):
    """The form, its options and the dialog were in every signed-in page. What is left is
    the slot, which says where the overlay and Dropzone are fetched from."""
    as_role("contributor")
    page = client.get(path).get_data(as_text=True)
    for needle in ('id="submitFileModal"', 'id="dropzone-additional-fields-form"', 'id="myDropzone"', "mcritDropzoneOptions", 'id="family"'):
        assert needle not in page, f"{path} still carries {needle}"
    slot = SLOT.search(page)
    assert slot and slot.group(1) == "/data/drop_overlay", f"{path} has no slot for the overlay"
    assert re.search(r'data-dropzone-js="/static/dropzone-patched\.min\.js\?v=\w+"', slot.group(0)), slot.group(0)
    assert '/static/upload_form.js' in page, "nothing on the page would fetch it"


@pytest.mark.parametrize("path", ["/", RESULT])
def test_the_overlay_was_most_of_two_kilobytes_of_the_page(client, as_role, path):
    """Measured with the overlay put back where the page used to carry it: the page is
    that much lighter in the first round trip. 1.77 KB on `/`, 1.93 KB on a result page."""
    as_role("contributor")
    page = client.get(path).get_data(as_text=True)
    overlay = client.get("/data/drop_overlay")
    assert overlay.status_code == 200
    slot = SLOT.search(page).group(0)
    with_the_overlay = page.replace(slot, overlay.get_data(as_text=True))
    assert gzipped(with_the_overlay) - gzipped(page) >= 1500


def test_the_upload_pages_keep_their_form_and_have_no_slot(client, as_role):
    """Their content is the drop zone: they render it, as before, and no overlay."""
    as_role("contributor")
    for path in ("/analyze/query", "/data/submit", "/data/import"):
        page = client.get(path).get_data(as_text=True)
        assert 'id="myDropzone"' in page, path
        assert "drop-overlay-slot" not in page and "submitFileModal" not in page, path


def test_no_overlay_for_those_it_is_not_shown_to(app, client, make_user):
    """No one signed out has a slot, nor someone whose account waits to be approved; the
    overlay answers them as the pages they cannot see do."""
    assert "drop-overlay-slot" not in client.get("/login").get_data(as_text=True)
    response = client.get("/data/drop_overlay")
    assert response.status_code == 302 and "/login" in response.headers["Location"]

    from conftest import log_in_as
    log_in_as(client, make_user("pending"))
    assert "drop-overlay-slot" not in client.get("/").get_data(as_text=True)
    assert client.get("/data/drop_overlay").status_code == 403


def test_the_overlay_is_markup_without_script_and_kept_by_no_one(client, as_role):
    """A script in markup a script inserts does not run, and under a nonce policy it would
    not be let run either: what the overlay's scripts did, upload_form.js does from its
    data- attributes. It carries a session's token, so no cache keeps it."""
    as_role("visitor")
    response = client.get("/data/drop_overlay")
    overlay = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.mimetype == "text/html"
    assert "<script" not in overlay
    assert not overlay.lstrip().lower().startswith("<!doctype")
    for needle in ('id="submitFileModal"', 'id="myDropzone"', 'class="dropzone-pending"', 'id="dropzone-additional-fields-form"',
                   'id="query_form"', 'id="submit_form"', 'id="family"', 'id="minhashBandRange_dropzone"', "data-lazy",
                   'data-family-names="/explore/familyNames"', "data-family-names-js=\"/static/family_names.js?v=",
                   "data-labels='[\"Off\", \"Fast\", \"Standard\", \"Complete\"]'", ".dropzone{border: 2px dashed"):
        assert needle in overlay, needle


#: an option as flask-dropzone writes it, one to a line: `name: value, // comment`. The
#: custom options, autoProcessQueue and the token's header, share the last line.
OPTION_LINE = re.compile(r"^\s*(\w+): (.*?),\s*(?://[^\"`]*)?$")


def parked_options(page):
    """The options a page parks for Dropzone in `window.mcritDropzoneOptions`, as a dict."""
    script = re.search(r"window\.mcritDropzoneOptions = \{\n(.*?)\n\s*\};", page, re.DOTALL).group(1)
    options = {}
    for line in script.splitlines():
        custom = re.match(r"^\s*autoProcessQueue: (true|false),headers: (\{.*?\}),", line)
        if custom:
            options["autoProcessQueue"] = json.loads(custom.group(1))
            options["headers"] = json.loads(custom.group(2))
            continue
        match = OPTION_LINE.match(line)
        if match:
            # the default message is a template literal
            value = match.group(2)
            options[match.group(1)] = json.loads(json.dumps(value[1:-1]) if value.startswith("`") else value)
    return options


@pytest.fixture
def app(app):
    # the token is what the options carry and the upload is checked with
    app.config[ENABLED_KEY] = True
    return app


def test_the_overlay_has_the_options_the_query_page_writes_and_a_token_of_its_own(client, as_role):
    """The query page's options are flask-dropzone's script; the overlay's are data
    (views/data.py, drop_overlay_options). Every option is the same - the size cap,
    autoProcessQueue, the messages - and the token is the session's, masked afresh."""
    as_role("contributor")
    query = parked_options(client.get("/analyze/query").get_data(as_text=True))
    first = json.loads(unescape(OPTIONS.search(client.get("/data/drop_overlay").get_data(as_text=True)).group(1)))
    second = json.loads(unescape(OPTIONS.search(client.get("/data/drop_overlay").get_data(as_text=True)).group(1)))
    with client.session_transaction() as session:
        token = session[FIELD_NAME].encode()

    assert set(first) == set(query), set(first) ^ set(query)
    for name, value in query.items():
        if name != "headers":
            assert first[name] == value, name
    assert first["maxFilesize"] == 100000000 and first["autoProcessQueue"] is False
    for options in (query, first, second):
        assert unmask(options["headers"]["X-CSRF-Token"], len(token)) == token
    assert first["headers"] != second["headers"], "the token is not masked afresh"


class Ids(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.ids = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get("id"):
            self.ids.append(dict(attrs)["id"])


@pytest.mark.parametrize(("role", "path"), [("visitor", path) for path in VISITOR_PAGES] + [("admin", path) for path in ADMIN_PAGES])
def test_no_page_has_an_id_the_overlay_brings(client, as_role, role, path):
    """The overlay's form was checked with every page it was in. Put in the page now, its
    ids - `family`, `version`, `query_form` and the rest - must still be ones the page
    does not use, or a label or a lookup by id finds the wrong field."""
    as_role(role)
    page = client.get(path).get_data(as_text=True)
    if "drop-overlay-slot" not in page:
        return
    overlay = Ids(client.get("/data/drop_overlay").get_data(as_text=True)).ids
    assert len(overlay) == len(set(overlay))
    assert set(Ids(page).ids) & set(overlay) == set()
