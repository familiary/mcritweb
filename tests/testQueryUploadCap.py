#!/usr/bin/python
"""The per-role query cap is checked before the upload is read.

`analyze.query` compared the file with `QUERY_UPLOAD_LIMITS` only after Flask had read
and parsed the whole body, which `MAX_CONTENT_LENGTH` lets reach 1 GiB, so a visitor
could make every refused request cost that much. The declared length is now checked
first. The cap itself is covered in tests/testAppConfig.py.
"""

import io

import pytest

#: The shipped default for the one role the default mapping caps.
VISITOR_LIMIT = 1 * 2**20


def post_query(client, size):
    return client.post(
        "/analyze/query",
        data={"options": "unmapped", "file": (io.BytesIO(b"M" * size), "sample.exe")},
        content_type="multipart/form-data",
    )


#: Room the declared length may take beyond the file, for the other form fields and the
#: multipart framing. Written out rather than imported, so these tests also run against
#: code from before it existed.
FORM_ALLOWANCE = 64 * 2**10


@pytest.fixture
def form_parses(monkeypatch):
    """Records each time Flask parses a request's form and files."""
    import flask

    parsed = []
    real_load_form_data = flask.Request._load_form_data

    def load_form_data(self):
        parsed.append(self.path)
        return real_load_form_data(self)

    monkeypatch.setattr(flask.Request, "_load_form_data", load_form_data)
    return parsed


def test_an_upload_far_over_the_cap_is_refused_before_it_is_parsed(client, as_role, form_parses):
    as_role("visitor")

    response = post_query(client, VISITOR_LIMIT + FORM_ALLOWANCE + 1)

    assert response.status_code == 403
    assert form_parses == []
    with client.session_transaction() as session:
        assert "up to 1048576 bytes" in str(session.get("_flashes"))


def test_a_full_size_upload_with_every_dropzone_field_still_gets_through(client, as_role):
    """The file may be exactly the cap, with the longest filename a browser sends and
    every field the dropzone copies in beside it."""
    as_role("visitor")

    response = client.post(
        "/analyze/query",
        data={
            "form_type": "query_form", "family": "f" * 200, "version": "v" * 200, "base_addr": "",
            "options": "unmapped", "minhashBandRange": "2",
            "file": (io.BytesIO(b"M" * VISITOR_LIMIT), "n" * 251 + ".exe"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202  # queued


def test_a_capped_upload_that_declares_no_length_is_refused(client, as_role, form_parses):
    """A chunked body's size could only be found by reading it. Browsers always declare a
    length for a form upload."""
    as_role("visitor")

    response = client.post(
        "/analyze/query",
        data={"options": "unmapped", "file": (io.BytesIO(b"M" * 128), "sample.exe")},
        content_type="multipart/form-data",
        headers={"Transfer-Encoding": "chunked"},
    )

    assert response.status_code == 403
    assert form_parses == []


def test_with_csrf_on_the_dropzone_upload_is_still_refused_unparsed(app, client, as_role, form_parses):
    """The CSRF check runs before any view. It used to read the form first, which parsed
    the body before the cap could be looked at; the dropzone sends its token as a
    header, and that is now read first."""
    app.config["WTF_CSRF_ENABLED"] = True
    as_role("visitor")
    with client.session_transaction() as session:
        session["csrf_token"] = "t" * 64

    response = client.post(
        "/analyze/query",
        data={"options": "unmapped", "file": (io.BytesIO(b"M" * (VISITOR_LIMIT + FORM_ALLOWANCE + 1)), "big.exe")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": "t" * 64},
    )

    assert response.status_code == 403
    assert form_parses == []
