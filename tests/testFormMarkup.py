#!/usr/bin/python
"""The markup of the forms, as the browser and assistive technology read it.

Each page is rendered offline against the captured corpus and parsed, so these hold for
what a template prints, not for what it looks like in its source.
"""

import collections
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of

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
    # the drop overlay's form, which a page fetches on the first drag (ADR-0023)
    pages["drop overlay"] = inputs_by_name(page_of(client, "/data/drop_overlay"))
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


#: an identifier, a code or a search term: what a phone keyboard must not capitalise or "correct"
NO_CORRECTION = {"spellcheck": "false", "autocapitalize": "off", "autocorrect": "off"}

#: page -> the ids of its search boxes
SEARCH_BOXES = {
    "/explore/search?query=a": ["query"],
    "/explore/families": ["query"],
    "/explore/samples": ["query"],
    "/explore/functions": ["query"],
    "/explore/families/0": ["query"],
    "/explore/samples/0": ["query"],
    "/data/jobs?active=getMatchesForSample": ["Search"],
    "/analyze/compare": ["Search"],
    "/analyze/compare_versus": ["Search_a", "Search_b"],
    "/analyze/cross_compare": ["Search"],
    "/analyze/unique_blocks": ["Search"],
}


@pytest.mark.parametrize("path", sorted(SEARCH_BOXES))
def test_a_search_box_is_a_search_field_without_text_correction(client, as_role, path):
    """The search language is lowercase `field:value` terms, which a phone capitalised and
    "corrected", and its Enter key said "go" or "return" rather than search. `required` on
    the <form> itself means nothing to a browser."""
    as_role("contributor")
    tags = Tags(page_of(client, path)).tags
    fields = {dict(attrs).get("id"): dict(attrs) for tag, attrs in tags if tag == "input"}
    for box in SEARCH_BOXES[path]:
        attrs = fields[box]
        assert {name: attrs.get(name) for name in ("type", "enterkeyhint", *NO_CORRECTION)} == \
            dict(type="search", enterkeyhint="search", **NO_CORRECTION), (path, box)
    assert [attrs for tag, attrs in tags if tag == "form" and "required" in dict(attrs)] == []


def test_identifiers_and_codes_are_typed_as_they_are(client, as_role, make_user):
    """Usernames, the family, version and base address of an upload, and the tokens."""
    pages = credential_fields(client, as_role, make_user)
    fields = [("login", "username"), ("registration", "username"), ("registration", "registrationToken"),
              ("first registration", "mcritServerToken"), ("first registration", "setRegistrationToken"),
              ("settings", "username"), ("drop overlay", "family"), ("drop overlay", "version"), ("drop overlay", "base_addr")]
    wrong = [(page, name) for page, name in fields
             if {attr: pages[page][name].get(attr) for attr in NO_CORRECTION} != NO_CORRECTION]
    assert wrong == []


@pytest.mark.parametrize("path, textarea", [("/analyze/cross_compare_from_hash_list", "hashlist"),
                                            (None, "yara_text")])
def test_a_list_of_hashes_and_a_rule_are_not_spellchecked(client, as_role, path, textarea):
    """One SHA-256 per line, or a YARA rule: a spellchecker underlines every line."""
    as_role("contributor")
    if path is None:
        path = f"/data/result/{job_id_of('unique_blocks')}?tab=yara"
    areas = {dict(attrs).get("name"): dict(attrs) for tag, attrs in Tags(page_of(client, path)).tags if tag == "textarea"}
    assert areas[textarea].get("spellcheck") == "false"


def test_every_password_field_has_a_show_password_button(client, as_role, make_user):
    """A real button, not a submit one, that says whether it is showing the password and
    which field it shows (static/form_fields.js)."""
    pages = {"first registration": page_of(client, "/register")}
    make_user("admin")
    pages["login"] = page_of(client, "/login")
    pages["registration"] = page_of(client, "/register")
    as_role("contributor")
    pages["settings"] = page_of(client, "/settings")
    for page, html in pages.items():
        tags = Tags(html).tags
        passwords = [dict(attrs)["id"] for tag, attrs in tags if tag == "input" and dict(attrs).get("type") == "password"]
        toggles = {dict(attrs).get("aria-controls"): dict(attrs) for tag, attrs in tags
                   if tag == "button" and "password-toggle" in (dict(attrs).get("class") or "")}
        assert passwords, page
        for field in passwords:
            assert field in toggles, (page, field)
            assert (toggles[field].get("type"), toggles[field].get("aria-pressed")) == ("button", "false"), (page, field)


#: page -> the id of its MinHash slider; every page that is signed in carries the drop
#: overlay's as well
SLIDERS = {
    "/analyze/compare?minhashBandRange=1": "minhashBandRange",
    "/analyze/compare_versus": "minhashBandRange",
    "/analyze/cross_compare?minhashBandRange=3": "minhashBandRange",
    "/analyze/query": "minhashBandRange_dropzone",
}
SLIDER_WORDS = ["Off", "Fast", "Standard", "Complete"]


@pytest.mark.parametrize("path", sorted(SLIDERS))
def test_a_minhash_slider_says_what_its_position_means(client, as_role, path):
    """The slider's value is 0-3 and its meaning is the word printed beside it, which was
    tied to nothing: a screen reader said "2" where the page said "Standard"."""
    as_role("contributor")
    sliders = {dict(attrs)["id"]: dict(attrs) for tag, attrs in Tags(page_of(client, path)).tags
               if tag == "input" and dict(attrs).get("type") == "range"}
    slider = sliders[SLIDERS[path]]
    assert slider.get("aria-valuetext") == SLIDER_WORDS[int(slider["value"])]


class ButtonText(HTMLParser):
    """The visible text of the upload form's button, `#submit-dropzone`."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.depth, self.text = 0, ""
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if self.depth or (tag == "button" and dict(attrs).get("id") == "submit-dropzone"):
            self.depth += 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text += data


@pytest.mark.parametrize("path, label", [("/data/submit", "Submit"), ("/analyze/query", "Query"),
                                         # the drop overlay, whose form opens on "Match against database"
                                         ("/data/drop_overlay", "Query")])
def test_the_upload_button_says_what_the_chosen_mode_does(client, as_role, path, label):
    """The overlay's button said "Submit" while the form was set to match the file against
    the database, which is a query; the query page's own button says "Query"."""
    as_role("contributor")
    assert ButtonText(page_of(client, path)).text.strip() == label


class FormFields(HTMLParser):
    """{form action or "": [attributes of each field a keyboard types into, in order]}."""

    TYPED = {None, "text", "password", "search", "url", "email", "number"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms, self.current = {}, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = self.forms.setdefault(attrs.get("action") or "", [])
        elif tag == "input" and self.current is not None and attrs.get("type") in self.TYPED:
            self.current.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def test_the_last_field_of_a_credential_form_has_a_done_key(app, client, as_role, make_user):
    """The sign-in guide's enterkeyhint="done" on the final input, so a phone keyboard's Enter
    key says the form is finished there; the fields before it keep the default."""
    forms = [FormFields(page_of(client, "/register")).forms]
    make_user("admin")
    forms.append(FormFields(page_of(client, "/login")).forms)
    forms.append(FormFields(page_of(client, "/register")).forms)
    with app.app_context():
        server_info = ServerInfo.fromDb()
        server_info.registration_token = "token"
        server_info.saveToDb()
    forms.append(FormFields(page_of(client, "/register")).forms)
    as_role("contributor")
    settings = FormFields(page_of(client, "/settings")).forms
    forms.append({action: fields for action, fields in settings.items() if "change_username" in action or "change_password" in action})

    checked = 0
    for page in forms:
        for action, fields in page.items():
            if not fields or not any(field.get("type") == "password" for field in fields):
                continue
            hints = [field.get("enterkeyhint") for field in fields]
            assert hints[-1] == "done" and "done" not in hints[:-1], (action, [field.get("name") for field in fields], hints)
            checked += 1
    assert checked == 6, checked


class FormNotes(HTMLParser):
    """[(form attributes, [its typed fields' attributes])] and the text of every element with an id."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms, self.texts, self.open = [], {}, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.forms.append((attrs, []))
        elif tag == "input" and self.forms and attrs.get("type") in FormFields.TYPED:
            self.forms[-1][1].append(attrs)
        if tag not in ("input", "br", "img", "link", "meta"):
            self.open.append(attrs.get("id"))
            if attrs.get("id"):
                self.texts[attrs["id"]] = ""

    def handle_endtag(self, tag):
        if self.open and tag not in ("input", "br", "img", "link", "meta"):
            self.open.pop()

    def handle_data(self, data):
        for element in self.open:
            if element:
                self.texts[element] += data


def required_notes(html):
    """{action: (the text the form is described by, whether all its typed fields are required)}."""
    page = FormNotes(html)
    notes = {}
    for attrs, fields in page.forms:
        if any(field.get("type") == "password" for field in fields):
            text = " ".join(page.texts.get(id_, "").strip() for id_ in (attrs.get("aria-describedby") or "").split())
            notes[attrs.get("action") or ""] = (text, all("required" in field for field in fields))
    return notes


def test_a_form_whose_fields_are_all_required_says_so_once(app, client, as_role, make_user):
    """No field said it was required. Where every field is, the form says so once at its top,
    and is described by it, rather than marking each field; the first user's form, which
    mixes in optional fields, says that the ones that are optional say so."""
    first = required_notes(page_of(client, "/register"))
    assert first == {"": ("Fields are required unless they say optional.", False)}
    make_user("admin")
    pages = [required_notes(page_of(client, "/login")), required_notes(page_of(client, "/register"))]
    as_role("contributor")
    pages.append(required_notes(page_of(client, "/settings")))
    found = [note for page in pages for note in page.values()]
    # sign-in, registration, and the settings page's rename, password and account deletion
    assert found == [("All fields are required.", True)] * 5, found


def test_a_page_that_edits_samples_still_has_its_dialog(client, as_role):
    """The dialog is printed where an edit control can open it, and once."""
    as_role("contributor")
    ids = Tags(page_of(client, "/explore/samples")).ids()
    assert ids.count("editSampleModal") == 1


def result_variants():
    matches, versus = job_id_of("matches_for_sample"), job_id_of("matches_for_sample_vs")
    return [f"/data/result/{job_id_of(report)}" for report in
            ["matches_for_sample", "matches_for_sample_vs", "matches_for_query", "cross_compare", "unique_blocks"]] + [
        # a family's page prints two filter forms, one over its samples and one over its functions
        f"/data/result/{matches}?famid=1",
        f"/data/result/{matches}?famid=1&filter_direct_min_score=10&filter_function_min_score=10&filter_unique_only=on",
        f"/data/result/{matches}?famid=1&samp=1&funp=2&funl=10",
        f"/data/result/{versus}?samid=3",
        f"/data/result/{versus}?samid=3&funp=1",
        f"/data/result/{matches}?funid=2",
        f"/data/result/{matches}?funid=2&funp=1",
    ]


@pytest.mark.parametrize("path", result_variants())
def test_no_id_is_used_twice_on_a_result_page(client, as_role, path):
    """The family page printed its samples filter and its functions filter with the same
    hidden `famid`, `samp` and `funp` inputs, ids included, so an id lookup (a label's `for`,
    a script, a fragment) found the first form's whichever one it meant. The hidden inputs
    are read by name."""
    as_role("contributor")
    counts = collections.Counter(Tags(page_of(client, path)).ids())
    assert [name for name, count in counts.items() if count > 1] == []


def test_the_family_filters_still_carry_the_position_by_name(client, as_role):
    """Dropping the ids must not drop the values: each filter form submits where the reader is."""
    as_role("contributor")
    html = page_of(client, f"/data/result/{job_id_of('matches_for_sample')}?famid=1&samp=1&funp=2")
    carried = [dict(attrs) for tag, attrs in Tags(html).tags if tag == "input" and dict(attrs).get("type") == "hidden"
               and "id" not in dict(attrs)]
    names = collections.Counter(field["name"] for field in carried if field["name"] in ("famid", "samp", "funp"))
    assert names == {"famid": 2, "samp": 2, "funp": 2}
    assert {field["name"]: field["value"] for field in carried if field["name"] != "csrf_token"} == {"famid": "1", "samp": "1", "funp": "2"}
