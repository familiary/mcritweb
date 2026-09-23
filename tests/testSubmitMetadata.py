#!/usr/bin/python
"""Submit metadata has to survive McritClient's hand-built query string.

`data.submit` hands filename, family and version to `McritClient.addBinarySample`,
which does not build its request with requests' `params=`. It concatenates them
itself (mcrit 1.8.1, `McritClient.addBinarySample`)::

    query_fields.append(f"family={family}")
    ...
    query_string = "?" + "&".join(query_fields)

and the backend reads the values straight back out of `req.params`
(`SampleResource.on_post_submit_binary`, which still carries the upstream
``# TODO parse respective query fields -> escape / sanitize input``). So every
character that means something in a query string travels unescaped from the form
into the backend's parameter dict:

* a file named ``C++_sample.exe`` is stored as ``C  _sample.exe``, because the URL
  keeps the ``+`` and the backend decodes ``+`` as a space;
* a family of ``R&D`` is stored as ``R``, with ``D`` arriving as a stray parameter;
* a family of ``x&is_dump=1&base_addr=0x41414141`` appends *parameters of its own*,
  and the backend disassembles the upload as a mapped dump at an address the form's
  own validation never saw.

Nothing on this route validates family or version, and `f.filename` is chosen by the
browser, so the first two fire with no adversary at all. The route is
`@contributor_required`, which bounds the third but does not make it not-smuggling.

These tests therefore run the *real* client - a fake one cannot show a bug that lives
in the client's URL building - and assert on what the backend would receive: the URL
is re-quoted the way `requests` re-quotes it on the way to the wire, then parsed by
falcon exactly as the server parses it.
"""

import io
import logging
from types import SimpleNamespace

import falcon
import falcon.testing
import pytest
import requests
from mcrit.client.McritClient import McritClient

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: Where the fixture pretends the backend lives. Only the query string is asserted on.
BACKEND = "http://backend.invalid:8000"

#: What the dropzone appends alongside the file, from `#dropzone-additional-fields-form`.
SUBMIT_FIELDS = {"family": "test.family", "version": "1.0", "options": "unmapped"}


class CannedResponse:
    """The two shapes `handle_response` understands: a body it accepts, or a refusal."""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def wire(monkeypatch):
    """A real `McritClient` with `requests` swapped out underneath it.

    The substitution has to sit one layer below the client, because the client is
    what is under test. `mcrit.client.McritClient` does a module-level
    ``import requests``, so replacing that name reaches every call it makes and
    nothing else - the suite stays offline and the global `requests` module, which
    the assertions below still need, is untouched.
    """
    sent = SimpleNamespace(urls=[])

    def _get(url, **kwargs):
        # getSampleBySha256 - nothing is in the corpus, so the submit carries on to
        # the addBinarySample branch rather than short-circuiting as a known sample.
        return CannedResponse({"status": "failed", "data": {}})

    def _post(url, *args, **kwargs):
        sent.urls.append(url)
        return CannedResponse({"status": "successful", "data": "0123456789abcdef01234567"})

    monkeypatch.setattr(
        "mcrit.client.McritClient.requests", SimpleNamespace(get=_get, post=_post)
    )
    sent.client = McritClient(mcrit_server=BACKEND)
    return sent


@pytest.fixture
def fake_mcrit(wire):
    """Override the conftest fake: this module wants the real client."""
    return wire.client


def submit_binary(client, content=b"MZ metadata", filename="sample.exe", **fields):
    """POST to the sample dropzone the way the browser does."""
    data = dict(SUBMIT_FIELDS, **fields)
    data["file"] = (io.BytesIO(content), filename)
    return client.post("/data/submit", data=data, content_type="multipart/form-data")


def backend_params(url):
    """What falcon hands `SampleResource.on_post_submit_binary` for `url`.

    Two decodes stand between the client's f-string and the server, and both bite.
    `requests` re-quotes whatever URL it is handed (`requote_uri`) before it goes on
    the wire - that step unquotes escapes for unreserved characters, so an encoding
    that emits them would be silently undone. Falcon then parses the query string
    with `+` meaning space, which is why an unencoded `C++_sample.exe` loses its
    plusses. Reasoning about either is how this bug survives; run them instead.
    """
    prepared = requests.PreparedRequest()
    prepared.prepare_url(url, None)
    query_string = prepared.url.partition("?")[2]
    environ = falcon.testing.create_environ(path="/samples/binary", query_string=query_string)
    return falcon.Request(environ).params


@pytest.fixture
def submitted(client, as_role, wire):
    """Submit an upload as a contributor, and hand back the backend's view of it."""
    as_role("contributor")

    def _submitted(**kwargs):
        before = len(wire.urls)
        response = submit_binary(client, **kwargs)
        assert response.status_code == 202, response.get_data(as_text=True)[:200]
        assert len(wire.urls) == before + 1, "the upload never reached the client"
        return backend_params(wire.urls[-1])

    return _submitted


def test_the_client_still_leaves_the_query_string_to_us(wire):
    """The assumption the whole fix rests on, asserted rather than assumed.

    `data.quote_backend_query_value` is correct only while `McritClient.addBinarySample`
    concatenates its query string by hand. If a future mcrit hands requests `params=`
    instead - the durable fix, and one this repository cannot ship - requests would
    encode the already-encoded values a second time and every family would be stored
    as "R%26D". `setup.py` pins `mcrit>=1.5.3` with no ceiling, so a deployment that
    merely pip-upgrades would corrupt metadata quietly. This fails first, and loudly.

    Measured against mcrit 1.8.1: a raw '&' reaches the URL untouched, which is only
    true of a client that does no encoding of its own. Under `params=` the query
    string would not be in the URL handed to `requests.post` at all.
    """
    wire.client.addBinarySample(b"MZ", filename="a.exe", family="R&D", version="1.0")

    assert "family=R&D" in wire.urls[-1], (
        "mcrit's addBinarySample no longer builds its query string by hand. It now "
        "encodes the parameters itself, so data.quote_backend_query_value double-"
        "encodes: drop it and pass the raw values, and raise the mcrit floor in "
        "setup.py and requirements.txt to the release that fixed it."
    )


def test_a_filename_keeps_its_plus_signs(submitted):
    """`+` is a space in a query string, and the filename is the browser's, not ours -
    C++ project names are ordinary, so this one corrupts real submissions with nobody
    doing anything unusual. It is also why the fix is encoding rather than a validation
    set: a filename legitimately contains characters an allowlist would have to refuse."""
    assert submitted(filename="C++_sample.exe")["filename"] == "C++_sample.exe"


def test_a_family_keeps_its_ampersand(submitted):
    """`R&D` is a plausible family name and survives the form untouched - it is only
    the URL the client builds by hand that cuts it in half."""
    params = submitted(family="R&D")
    assert params["family"] == "R&D"
    assert "D" not in params, "the tail of the family arrived as a parameter of its own"


def test_a_version_keeps_its_ampersand(submitted):
    """Version goes through the same concatenation and is validated no more than family is."""
    params = submitted(version="1.0 & up")
    assert params["version"] == "1.0 & up"
    assert "up" not in params


def test_a_family_cannot_smuggle_a_dump_base_address(submitted):
    """The one that is not merely corruption. `is_dump` and `base_addr` are decided by
    the view - from the 'dumped' radio and a hex field - and this upload is 'unmapped',
    so neither may appear. Smuggled in, they make the backend disassemble the upload as
    a mapped dump at an attacker-chosen address, bypassing the form's own parsing."""
    params = submitted(family="x&is_dump=1&base_addr=0x41414141")

    assert params["family"] == "x&is_dump=1&base_addr=0x41414141"
    assert "is_dump" not in params, "an unmapped upload was smuggled into the dump path"
    assert "base_addr" not in params, "a base address the view never parsed reached the backend"


def test_a_percent_in_the_metadata_is_not_double_encoded(submitted):
    """The other half of encoding correctly. A family of `100%` must arrive as `100%`,
    and one that already looks encoded (`%41`) must not be decoded to `A` on the way -
    `requote_uri` decodes escapes for unreserved characters, so an encoder that emits
    them would round-trip wrong in exactly this spot."""
    assert submitted(family="100%")["family"] == "100%"
    assert submitted(version="%41")["version"] == "%41"


def test_the_fields_the_view_computes_still_arrive(submitted):
    """Encoding the three text fields must not disturb the ones the view derives: a
    real dump still has to reach the backend as a dump, with its base address."""
    params = submitted(options="dumped", bitness="64", base_addr="0x140000000")

    assert params["is_dump"] == "1"
    assert params["base_addr"] == "0x140000000"
    assert params["bitness"] == "64"


def test_an_ordinary_submission_is_unchanged(submitted):
    """The encoding is invisible for values that never needed it - no `%` creeps into a
    plain family name, which is what the backend stores and every page then shows."""
    params = submitted(filename="thing.exe", family="test.family", version="1.0")

    assert params["filename"] == "thing.exe"
    assert params["family"] == "test.family"
    assert params["version"] == "1.0"
