#!/usr/bin/python
"""What a copy says when it is done, and the controls that copy.

A copy reported its outcome in ways that did not reach everyone. The YARA rule's and the
registration URL's used alert(), which takes the focus and holds the page until it is
answered; the clipboard buttons showed a "Copied!" tooltip, which a screen reader is not
told about. The registration URL's control was an icon with an onclick, which a keyboard
could not reach and a screen reader did not see, and its copy called
`navigator.clipboard`, which a browser leaves undefined on a page served over plain HTTP,
so there it did nothing and said nothing. Every copy now reports in the status message of
static/status_message.js, a live region, and falls back to a copy of a selection.

testStatusMessageBrowser.py makes the copies in Chromium. It skips where playwright is not
installed, CI included, so these lints are what CI keeps.
"""

import gzip
import logging
import pathlib
import re
import unittest

import pytest

from mcritweb.db import ServerInfo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "mcritweb"

#: The project's own scripts: the templates and the files under static/ that are ours.
OWN_SCRIPTS = sorted(PACKAGE.joinpath("templates").rglob("*.html")) + [
    PACKAGE / "static" / name
    for name in ("post_action.js", "page_loading.js", "upload_form.js", "family_names.js", "live_search.js",
                 "dropzone_attach.js", "function_compare.js", "status_message.js")
]

#: The clipboard button the registration URL is copied with, and the URL it carries.
REGISTRATION_COPY = re.compile(
    r"<button type=\"button\"[^>]*title=\"Copy registration URL\"[^>]*data-clipboard-value=\"([^\"]*)\"[^>]*>"
)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def test_nothing_reports_through_an_alert():
    """alert() takes the focus and blocks the page until it is answered, which is a lot to
    ask for "Copied"; a status message says it without either."""
    found = [
        f"{path.relative_to(PACKAGE.parent)}:{number}"
        for path in OWN_SCRIPTS if path.exists()
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"\balert\(", line)
    ]
    assert found == [], f"alert() is still called: {found}"


def test_the_registration_url_is_copied_by_a_button(app, client, as_role):
    """The copy was an `<i onclick>`: no Tab stop, no role and no name, so only a mouse
    could use it. It is the clipboard button the rest of the site copies with, named for
    what it copies, and it carries the whole URL, built by url_for."""
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = "a.token-1"
        server_info.saveToDb()
    as_role("admin")
    page = client.get("/admin/server").get_data(as_text=True)

    assert not re.search(r"<i\b[^>]*\sonclick=", page), "an icon still takes the click"
    copy = REGISTRATION_COPY.search(page)
    assert copy, "no button copies the registration URL"
    assert copy.group(1) == "http://localhost/register?token=a.token-1"


def test_every_page_queues_the_status_message(client, as_role):
    """The clipboard buttons are on every kind of page, and every page reports a copy."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    assert re.search(r"<script( nonce=\"[^\"]*\")?>later\(\"/static/status_message\.js(\?v=\w+)?\"\)</script>", page)


def test_the_clipboard_button_copies_without_a_secure_context(client, as_role):
    """`navigator.clipboard` is there only on a page served over HTTPS or from localhost,
    and MCRITweb is often served over plain HTTP. The clipboard button called it
    regardless, so there a copy threw before it did anything. It asks first now, and
    copies a selection when it cannot use it."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    # the whole script, since there is no telling where the helper's own closing brace is
    # once the page has lost its indentation
    helpers = [body for body in re.findall(r"<script[^>]*>(.*?)</script>", page, re.S) if "function copy_to_clipboard(btn)" in body]
    assert len(helpers) == 1, "the page carries no clipboard helper"
    code = helpers[0]
    assert re.search(r"if \(window\.isSecureContext && navigator\.clipboard\)", code), "navigator.clipboard is used unasked"
    assert 'execCommand("copy")' in code, "there is no copy for a page without navigator.clipboard"
    assert "mcritCopied(" in code, "the copy does not report"
    status_message_js = (PACKAGE / "static" / "status_message.js").read_text()
    assert "function mcritCopied(" in status_message_js and "mcritStatus(" in status_message_js, "the copy does not report in the status message"


def test_every_page_queues_the_script_that_reports_a_copy_and_no_file_for_it_alone(client, as_role):
    """The report of a copy was a file of its own, clipboard.js, queued on every page. Each
    file the queue adds costs a phone blocking time: Lighthouse's mobile run of the index
    page went from about 17 ms to 56 ms with it. It rides in status_message.js, which it
    uses and which every page queues already."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    assert re.search(r"<script( nonce=\"[^\"]*\")?>later\(\"/static/status_message\.js(\?v=\w+)?\"\)</script>", page)
    assert "/static/clipboard.js" not in page


def test_the_tooltips_escape_key_has_no_file_of_its_own(client, as_role):
    """Escape for the hint tooltips was hints.js, a file of its own queued on every page.
    Each file the queue adds is one more ordered request before the page's scripts have run:
    on the build of all the pull requests the index page took 35 to 45 ms of blocking time in
    Lighthouse's mobile run against the base's 8 to 15, and folding the every-page files into
    the ones the base already queued brought it back to 10. The listener rides in
    status_message.js, which every page queues already."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    assert "/static/hints.js" not in page
    assert not (PACKAGE / "static" / "hints.js").exists()
    assert 'DISMISSED = "hints-dismissed"' in (PACKAGE / "static" / "status_message.js").read_text()


#: what the helper in the page may weigh gzipped; it was 628 bytes with the report in it
CLIPBOARD_HELPER_BUDGET = 460


def test_the_page_carries_only_the_copy_not_the_report_of_it(client, as_role):
    """The page's helper used to hold the tooltip set-up and the report of a copy as well,
    which nothing needs before the scripts are in: 628 bytes gzipped, sent with every page.
    What has to be in the page is what copies, so that a press right
    after the first paint copies; the rest is in status_message.js, which is cached."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    blocks = [body for body in re.findall(r"<script[^>]*>(.*?)</script>", page, re.S) if "function copy_to_clipboard" in body]
    assert len(blocks) == 1, "the page carries no clipboard helper, or more than one"

    assert "bootstrap.Tooltip" not in blocks[0] and "mcritStatus" not in blocks[0]
    assert "tooltip()" not in blocks[0], "the tooltips are set up in status_message.js"
    assert len(gzip.compress(blocks[0].encode(), 6)) <= CLIPBOARD_HELPER_BUDGET


if __name__ == "__main__":
    unittest.main()


def test_the_empty_message_box_is_on_every_page_from_the_start(client, as_role):
    """status_message.js used to add its box to <body> once the page had loaded, and any
    element added there makes the browser restyle the whole page (bootstrap's `legend +
    *`): 300 ms on a 100 by 100 cross compare matrix. The box is in the markup, empty and
    before <main>, and the script only fills it."""
    as_role("visitor")
    for path in ("/explore/samples", "/help"):
        page = client.get(path).get_data(as_text=True)
        assert page.count('<div class="status-message"></div>') == 1, path
        assert page.index('<div class="status-message">') < page.index('<main class="content"'), path
