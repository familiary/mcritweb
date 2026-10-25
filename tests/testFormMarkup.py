#!/usr/bin/python
"""The markup of the forms, as the browser and assistive technology read it.

Each page is rendered offline against the captured corpus and parsed, so these hold for
what a template prints, not for what it looks like in its source.
"""

import collections
from html.parser import HTMLParser

import pytest

from mcritweb.db import ServerInfo


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


class Tags(HTMLParser):
    """Every start tag of a page, as (tag, attributes), in order."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def ids(self):
        return [value for _tag, attrs in self.tags for name, value in attrs if name == "id"]


def page_of(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.get_data(as_text=True)


def test_the_sample_dialog_has_no_stray_quote_in_its_form(client, as_role):
    """`action='...''` closed the value and then opened an attribute named `'`, which a
    browser keeps as an attribute of that name on the form."""
    as_role("contributor")
    stray = [(tag, name) for tag, attrs in Tags(page_of(client, "/explore/samples/0")).tags
             for name, _value in attrs if "'" in name or '"' in name]
    assert stray == []


@pytest.mark.parametrize("path", ["/settings", "/analyze/compare_versus"])
def test_no_id_is_used_twice(client, as_role, path):
    """Settings gave all seven column lists `id="items"`, and the 1 vs 1 page printed the
    sample dialog once per table, so `editSampleModal`, `sample_id`, `sample_family_name`
    and the rest were there twice, and the second dialog's labels named the first one's
    fields. Neither page has an edit control that opens that dialog."""
    as_role("contributor")
    counts = collections.Counter(Tags(page_of(client, path)).ids())
    assert [name for name, count in counts.items() if count > 1] == []


def inputs_by_name(html):
    return {dict(attrs).get("name"): dict(attrs) for tag, attrs in Tags(html).tags if tag == "input"}


def credential_fields(client, as_role, make_user):
    """{page: {field name: attributes}} of every form that holds a username or a password."""
    pages = {"first registration": inputs_by_name(page_of(client, "/register"))}
    make_user("admin")
    pages["login"] = inputs_by_name(page_of(client, "/login"))
    # the token field is there only while the instance asks for one
    with client.application.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = "token"
        server_info.saveToDb()
    pages["registration"] = inputs_by_name(page_of(client, "/register"))
    as_role("contributor")
    pages["settings"] = inputs_by_name(page_of(client, "/settings"))
    return pages


#: page -> field -> the autocomplete token it must carry
AUTOCOMPLETE = {
    "login": {"username": "username", "inputPassword": "current-password"},
    "registration": {"username": "username", "inputPassword1": "new-password", "inputPassword2": "new-password",
                     "registrationToken": "off"},
    "first registration": {"username": "username", "inputPassword1": "new-password", "inputPassword2": "new-password",
                           "mcritServerToken": "off", "setRegistrationToken": "off"},
    # a rename wants a name the account does not have yet, so its own is not offered
    "settings": {"username": "off", "inputPassword1": "current-password", "inputPassword2": "current-password",
                 "inputPassword3": "new-password", "inputPassword4": "new-password"},
}


def test_every_credential_field_says_what_it_holds(client, as_role, make_user):
    """No field carried an autocomplete token, so a password manager guessed: the
    change-password form is three password fields in a row, where it could put the saved
    password into "new" and "confirm" and never offer to generate one."""
    pages = credential_fields(client, as_role, make_user)
    tokens = {page: {name: pages[page].get(name, {}).get("autocomplete") for name in fields}
              for page, fields in AUTOCOMPLETE.items()}
    assert tokens == AUTOCOMPLETE
    # and no password field anywhere is left to guess
    for page, fields in pages.items():
        for name, attrs in fields.items():
            if attrs.get("type") == "password":
                assert attrs.get("autocomplete") in ("current-password", "new-password"), (page, name)


def test_the_sign_in_password_is_the_current_password_by_id_too(client, make_user):
    """The id is the other half of what the sign-in guide asks of a sign-in form, and the
    label has to follow it."""
    make_user("admin")
    html = page_of(client, "/login")
    assert inputs_by_name(html)["inputPassword"].get("id") == "current-password"
    labels = [dict(attrs).get("for") for tag, attrs in Tags(html).tags if tag == "label"]
    assert "current-password" in labels


def test_a_page_that_edits_samples_still_has_its_dialog(client, as_role):
    """The dialog is printed where an edit control can open it, and once."""
    as_role("contributor")
    ids = Tags(page_of(client, "/explore/samples")).ids()
    assert ids.count("editSampleModal") == 1
