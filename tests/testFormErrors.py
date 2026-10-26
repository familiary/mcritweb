#!/usr/bin/python
"""A refused form says what was wrong beside the field, and keeps what was typed.

The sign-in, registration and settings forms said what was wrong only in the flash above
the page, which names no field, and came back empty: a first-user setup with a password
typed twice differently returned with the username, the backend URL, the operation mode and
the proposed registration token all gone, and a refused rename returned on another tab. The
flash stays, and the field it concerns is now marked invalid and described by the message.
Passwords, and the server token, are never printed back.
"""

from html.parser import HTMLParser

import pytest
from werkzeug.security import generate_password_hash

from mcritweb.db import UserInfo, get_db

PASSWORD = "correct horse battery staple"


class Form(HTMLParser):
    """The fields of a page by id, the text of every element with an id, and the tabs."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.fields, self.texts, self.tabs, self.open = {}, {}, {}, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("input", "select", "textarea") and attrs.get("id"):
            self.fields[attrs["id"]] = attrs
        if attrs.get("role") == "tab":
            self.tabs[attrs["id"]] = attrs.get("aria-selected")
        if attrs.get("id") and tag not in ("input", "br", "img"):
            self.open.append(attrs["id"])
            self.texts[attrs["id"]] = ""
        else:
            self.open.append(None)

    def handle_endtag(self, tag):
        if self.open:
            self.open.pop()

    def handle_data(self, data):
        for element in self.open:
            if element:
                self.texts[element] += data


def described_by(form, field):
    return " ".join(form.texts.get(id_, "").strip() for id_ in form.fields[field].get("aria-describedby", "").split())


@pytest.fixture
def empty_instance(app):
    """No user yet, so /register is the first-user form."""
    with app.app_context():
        get_db().execute("DELETE FROM user")
        get_db().commit()


def first_user_form(**changes):
    form = {"username": "alice", "inputPassword1": PASSWORD, "inputPassword2": "something else",
            "url": "http://10.0.0.5:8000", "mcritServerToken": "server-secret",
            "setRegistrationToken": "the-token-the-admin-copied", "operationMode": "multi"}
    form.update(changes)
    return form


def test_a_refused_first_registration_keeps_everything_but_the_secrets(client, empty_instance):
    response = client.post("/register", data=first_user_form())
    page = response.get_data(as_text=True)
    form = Form(page)

    assert form.fields["username"].get("value") == "alice"
    assert form.fields["server"].get("value") == "http://10.0.0.5:8000"
    assert form.fields["set-registration-token"].get("value") == "the-token-the-admin-copied"
    assert "checked" in form.fields["radio_multi"] and "checked" not in form.fields["radio_single"]
    for secret in (PASSWORD, "something else", "server-secret"):
        assert secret not in page


def test_a_refused_registration_marks_the_field_it_concerns(client, empty_instance):
    form = Form(client.post("/register", data=first_user_form()).get_data(as_text=True))

    assert form.fields["inputPassword2"].get("aria-invalid") == "true"
    assert "The passwords do not match" in described_by(form, "inputPassword2")
    assert "aria-invalid" not in form.fields["username"]
    # the field to fix is the one the page focuses
    assert "autofocus" in form.fields["inputPassword2"] and "autofocus" not in form.fields["username"]


def test_a_malformed_username_is_marked_and_kept_with_its_rule(client, empty_instance):
    form = Form(client.post("/register", data=first_user_form(username="a__b", inputPassword2=PASSWORD)).get_data(as_text=True))

    assert form.fields["username"].get("value") == "a__b"
    assert form.fields["username"].get("aria-invalid") == "true"
    description = described_by(form, "username")
    assert "3 to 20 characters" in description and "Username has wrong format" in description


def test_the_username_rule_is_written_out_beside_the_field(client, as_role, empty_instance):
    """It was the regular expression itself, in a title: no help on a touch screen, and
    none to anyone who does not read regular expressions."""
    register = Form(client.get("/register").get_data(as_text=True))
    as_role("contributor")
    settings = Form(client.get("/settings").get_data(as_text=True))
    for form in (register, settings):
        assert "letters A to Z without accents" in described_by(form, "username")
        assert "^(?=" not in form.fields["username"].get("title", "")


def test_a_refused_sign_in_keeps_the_name_and_marks_both_fields(client, make_user):
    make_user("visitor")
    response = client.post("/login", data={"username": "visitoruser", "inputPassword": "wrong"})
    page = response.get_data(as_text=True)
    form = Form(page)

    assert form.fields["username"].get("value") == "visitoruser"
    assert "value" not in form.fields["current-password"] and "wrong" not in page
    for field in ("username", "current-password"):
        assert form.fields[field].get("aria-invalid") == "true", field
        assert "Incorrect username or password." in described_by(form, field), field
    assert "autofocus" in form.fields["current-password"]


def test_a_refused_rename_opens_its_tab_with_the_name_and_the_message(client, as_role):
    as_role("contributor")
    client.post("/admin/change_username", data={"username": "no", "inputPassword1": "password"})
    form = Form(client.get("/settings").get_data(as_text=True))

    assert form.tabs["pills-username-tab"] == "true" and form.tabs["pills-info-tab"] == "false"
    assert form.fields["username"].get("value") == "no"
    assert form.fields["username"].get("aria-invalid") == "true"
    assert "Username has invalid format." in described_by(form, "username")


def test_a_wrong_password_on_a_rename_marks_the_password(client, as_role):
    as_role("contributor")
    client.post("/admin/change_username", data={"username": "newname", "inputPassword1": "wrong"})
    form = Form(client.get("/settings").get_data(as_text=True))

    assert form.fields["inputPassword1"].get("aria-invalid") == "true"
    assert "value" not in form.fields["inputPassword1"]
    assert form.fields["username"].get("value") == "newname"


@pytest.mark.parametrize("posted, field, message", [
    ({"inputPassword2": "password", "inputPassword3": "", "inputPassword4": ""}, "inputPassword3", "Password is required."),
    ({"inputPassword2": "password", "inputPassword3": "new-one", "inputPassword4": "other"}, "inputPassword4", "do not match"),
    ({"inputPassword2": "wrong", "inputPassword3": "new-one", "inputPassword4": "new-one"}, "inputPassword2", "Incorrect password!"),
])
def test_a_refused_password_change_marks_the_field(client, as_role, posted, field, message):
    as_role("contributor")
    client.post("/admin/change_password", data=posted)
    page = client.get("/settings").get_data(as_text=True)
    form = Form(page)

    assert form.tabs["pills-password-tab"] == "true"
    assert form.fields[field].get("aria-invalid") == "true"
    assert message in described_by(form, field)
    assert "new-one" not in page and "value" not in form.fields[field]


def test_the_message_is_shown_once(client, as_role):
    as_role("contributor")
    client.post("/admin/change_username", data={"username": "no", "inputPassword1": "password"})
    client.get("/settings")
    form = Form(client.get("/settings").get_data(as_text=True))

    assert form.tabs["pills-info-tab"] == "true"
    assert "aria-invalid" not in form.fields["username"] and "value" not in form.fields["username"]


def test_a_long_value_is_not_carried_in_the_session(app, client, make_user):
    """The session is a cookie; what was typed rides in it only while it is short."""
    with app.app_context():
        user_info = UserInfo()
        user_info.username = "carol"
        user_info.password = generate_password_hash(PASSWORD)
        user_info.role = "contributor"
        user_info.apitoken = "apitoken-carol"
        user_info.saveToDb()
    client.post("/login", data={"username": "carol", "inputPassword": PASSWORD})
    response = client.post("/admin/change_username", data={"username": "x" * 5000, "inputPassword1": PASSWORD})
    assert len(response.headers.get("Set-Cookie", "")) < 1000
    form = Form(client.get("/settings").get_data(as_text=True))
    assert form.fields["username"].get("aria-invalid") == "true"
    assert "value" not in form.fields["username"]
