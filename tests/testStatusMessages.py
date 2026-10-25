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
                 "dropzone_attach.js", "function_compare.js", "hints.js", "status_message.js")
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
    assert re.search(r"<script>later\(\"/static/status_message\.js\"\)</script>", page)


def test_the_clipboard_button_copies_without_a_secure_context(client, as_role):
    """`navigator.clipboard` is there only on a page served over HTTPS or from localhost,
    and MCRITweb is often served over plain HTTP. The clipboard button called it
    regardless, so there a copy threw before it did anything. It asks first now, and
    copies a selection when it cannot use it."""
    as_role("visitor")
    page = client.get("/explore/samples").get_data(as_text=True)
    helper = re.search(r"function copy_to_clipboard\(btn\)\s*\{(.*?)\n\}", page, re.S)
    assert helper, "the page carries no clipboard helper"
    code = helper.group(1)
    assert re.search(r"if \(window\.isSecureContext && navigator\.clipboard\)", code), "navigator.clipboard is used unasked"
    assert 'execCommand("copy")' in code, "there is no copy for a page without navigator.clipboard"
    assert "mcritStatus(" in code, "the copy does not report in the status message"


if __name__ == "__main__":
    unittest.main()
