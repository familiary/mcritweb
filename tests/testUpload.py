#!/usr/bin/python
"""The Dropzone upload path - issue #27.

Flask-Dropzone was the recorded reason MCRITweb could not leave Flask 2.2.5, and it
was also the one integration nothing exercised: the suite rendered both dropzone
pages and asserted the CSRF header they emit, but no test ever posted a file through
one. That left the half of the path Werkzeug owns - multipart parsing, `request.files`,
the file wrapper handed to `json.load` - covered by nothing.

These tests drive the request the browser actually sends, so lifting the pin is
answered by the suite rather than by hand.
"""

import io
import json
import logging
import random
import re
from urllib.parse import quote

import pytest
from conftest import FAKE_MINHASH_HASH, FAKE_SHINGLER_HASH

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: The smallest thing shaped like an MCRIT export: the keys `MinHashIndex.addImportData`
#: reads before it can count anything, carrying this instance's own config hashes. Nothing
#: here needs to be a real sample, but the *shape* does have to be real - the index indexes
#: into all of these unguarded, so a fixture missing one models a 500, not an import.
SHA256 = "ab" * 32
EXPORT = {
    "config": {
        "version": "1.5.3",
        "shingler": FAKE_SHINGLER_HASH,
        "minhash": FAKE_MINHASH_HASH,
    },
    "content": {"is_compressed": False, "num_samples": 1, "num_functions": 1, "num_families": 1},
    "family_mapping": {"1": "test.family"},
    "sample_entries": {SHA256: {"sample_id": 1, "family_id": 1}},
    "function_entries": {SHA256: {"1": {"function_id": 1, "sample_id": 1}}},
}


def upload(client, payload, filename="export.json", field="file"):
    """POST a file the way the dropzone does: multipart, one part, XHR."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post(
        "/data/import",
        data={field: (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_the_import_page_offers_a_dropzone_posting_to_the_import_route(client, as_role):
    """Flask-Dropzone renders through Jinja macros (`dropzone.create`), which is
    exactly the surface that broke on Flask 2.3 when `flask.Markup` was removed. If
    the extension is incompatible again, this is where it shows first."""
    as_role("contributor")
    page = client.get("/data/import").get_data(as_text=True)
    assert 'action="/data/import"' in page
    assert 'class="dropzone"' in page


def test_an_uploaded_export_reaches_the_backend(client, as_role, fake_mcrit):
    """The whole point of the route: a multipart part named `file`, parsed out of the
    request by Werkzeug and forwarded as a dict."""
    as_role("contributor")
    response = upload(client, EXPORT)

    assert response.status_code == 200
    forwarded = [call for call in fake_mcrit.calls if call[0] == "addImportData"]
    assert len(forwarded) == 1, "the upload never reached the backend"
    assert forwarded[0][1][0] == EXPORT


def test_the_import_report_is_carried_to_the_completion_page(client, as_role):
    """The upload response is not what the user sees - the dropzone redirects to
    `data.import_complete`, which reads the report back out of the session. Two
    requests, so the report has to survive the hop."""
    as_role("contributor")
    upload(client, EXPORT)

    page = client.get("/data/import_complete").get_data(as_text=True)
    assert "Import completed" in page
    assert "num_samples_imported" in page


def test_the_report_is_consumed_once(client, as_role):
    """`import_complete` pops the report. A second visit must not re-report an import
    that already happened - it should fall back to saying so instead.

    The message it falls back to used to be the malformed-file one, which is what made a
    refused-but-valid export unreportable; the fallback now says nothing was imported,
    which is what an emptied session actually means. What is pinned is unchanged: the
    report is consumed exactly once, and the page still tells the user where they are."""
    as_role("contributor")
    upload(client, EXPORT)
    client.get("/data/import_complete")

    page = client.get("/data/import_complete").get_data(as_text=True)
    assert "Import completed" not in page
    assert "nothing was imported" in page.lower()


@pytest.mark.parametrize(
    "payload, reason",
    [
        (b"this is not json", "not JSON at all"),
        (b'["a", "list"]', "JSON, but not the dict the client demands"),
    ],
)
def test_an_unusable_upload_is_reported_rather_than_a_500(client, as_role, payload, reason):
    """Anyone can drop the wrong file into a dropzone, so the wrong file is a normal
    input, not an exceptional one. Whatever the page says, it must not be a traceback."""
    as_role("contributor")
    response = upload(client, payload)
    assert response.status_code < 500, f"upload that is {reason} took the page down"


# --- the three ways an import can end, and telling them apart ---------------------

#: Genuine MCRIT data from an instance configured differently to ours: same schema, other
#: hashes. `MinHashIndex.addImportData` compares `config.shingler` and `config.minhash`
#: against its own and bare-`return`s when either differs, so the client hands the view a
#: `None` report - the same falsy value an empty session yields, which is how a perfectly
#: good file came to be reported as malformed JSON.
INCOMPATIBLE_EXPORT = dict(EXPORT, config=dict(
    EXPORT["config"],
    shingler="shingler-hash-of-some-other-instance",
    minhash="minhash-hash-of-some-other-instance",
))

#: The other half of the same refusal: an export from before the version floor. This one
#: mcritweb can attribute exactly, because the check is on a field the file carries.
OUTDATED_EXPORT = dict(EXPORT, config=dict(EXPORT["config"], version="0.0.0"))

#: A JSON object with a config this instance would accept, and nothing else. It clears all
#: three config checks and then KeyErrors inside `MinHashIndex.addImportData`, which is a
#: 500 - and `handle_response` reports a 500 as the same `None` a refusal produces. The
#: shingler provably matched, so a shingler mismatch is the one thing it cannot be.
NOT_AN_EXPORT = {"config": EXPORT["config"], "some_other_json": [1, 2, 3]}


def test_a_file_that_is_not_mcrit_data_is_refused_as_such(client, as_role):
    """(a) The dropzone posts by XHR, so a 200 makes it redirect to the completion page
    and the user never reads the response. A file that is not MCRIT data has to come back
    as an error status, carrying the reason as its body."""
    as_role("contributor")
    response = upload(client, b"this is not json")

    assert 400 <= response.status_code < 500, "a rejected upload answered 2xx"
    assert "valid MCRIT data in JSON format" in response.get_data(as_text=True)


def test_an_export_this_instance_refuses_is_not_called_malformed(client, as_role):
    """(b) The file is fine; the configurations are incompatible. Saying "not valid MCRIT
    data in JSON format" here points the contributor at their file, which is the one thing
    that is not wrong."""
    as_role("contributor")
    response = upload(client, INCOMPATIBLE_EXPORT)
    body = response.get_data(as_text=True)

    assert 400 <= response.status_code < 500
    assert "valid MCRIT data in JSON format" not in body, "a refused export blamed the file"
    assert "shingler" in body and "minhash" in body.lower(), f"no reason given: {body!r}"


def test_a_refused_export_explains_itself_on_the_completion_page_too(client, as_role):
    """The dropzone is not the only way to arrive: the redirect target is a plain GET, and
    whoever lands on it must be told the same thing, not the malformed-file message."""
    as_role("contributor")
    upload(client, INCOMPATIBLE_EXPORT)

    page = client.get("/data/import_complete").get_data(as_text=True)
    assert "Import completed" not in page
    assert "valid MCRIT data in JSON format" not in page
    assert "shingler" in page, "the completion page did not carry the reason"


def test_an_export_from_before_the_version_floor_names_the_version(client, as_role):
    """The third refusal in `addImportData` is on `config.version`, a field the upload
    itself carries - so this one can be attributed precisely rather than as "one of two"."""
    as_role("contributor")
    body = upload(client, OUTDATED_EXPORT).get_data(as_text=True)

    assert "config.version" in body, f"the version floor was not named: {body!r}"
    assert "0.0.0" in body


def test_a_bare_visit_to_the_completion_page_says_nothing_was_imported(client, as_role):
    """(c) No import happened in this session at all - a bookmark, a reload, a back button.
    Nothing was uploaded, so nothing about the upload can be wrong."""
    as_role("contributor")
    page = client.get("/data/import_complete").get_data(as_text=True)

    assert "Import completed" not in page
    assert "valid MCRIT data in JSON format" not in page, "an empty session blamed a file"
    assert "nothing was imported" in page.lower()


#: Said as fact, this is the claim the view is not entitled to make: `None` from the client
#: is also what a 401, a 500 and an unreachable database look like, and `handle_response`
#: flattens all of them to the same value. Naming it as the likeliest cause is fine; stating
#: it happened is a second wrong answer replacing the first one.
ASSERTED_AS_FACT = "the file is valid MCRIT data, but it was created by"


def test_a_backend_error_is_not_reported_as_a_config_mismatch(client, as_role, fake_mcrit):
    """A rejected apitoken returns 401, which `handle_response` has no branch for, so the
    view gets the same `None` a config refusal gives it. Blaming the shingler here is a
    false statement about a file that would have imported fine."""
    as_role("contributor")
    fake_mcrit.refuses_authentication = True

    body = upload(client, EXPORT).get_data(as_text=True)
    assert ASSERTED_AS_FACT not in body, "a backend failure was reported as a config mismatch"
    assert "likeliest" in body, "an undistinguishable cause was stated without hedging"
    assert "log" in body, "the reader was not pointed at where the reason actually is"


def test_a_json_object_that_is_not_an_export_is_not_blamed_on_the_shingler(client, as_role):
    """The config checks pass - that is the point - and the index then KeyErrors on
    `content`. Whatever this file's problem is, the shingler hash matched on the way past."""
    as_role("contributor")

    body = upload(client, NOT_AN_EXPORT).get_data(as_text=True)
    assert ASSERTED_AS_FACT not in body, "a 500 was reported as a config mismatch"
    assert "likeliest" in body
    assert "log" in body


#: 12 kB of hash-shaped, high-entropy hex. A repetitive string is not a reproduction here:
#: Flask signs the session with itsdangerous, whose URL-safe serializer zlib-compresses the
#: payload, so `"f" * 12000` arrives as a few hundred bytes and the cookie never overflows.
#: A real `config.shingler` is a hash, and a hash does not compress.
HUGE_HASH = random.Random(0).randbytes(6000).hex()


def test_a_huge_config_value_cannot_blow_up_the_session_cookie(client, as_role, recwarn):
    """The reason quotes the upload back at the user and is then stored in the session,
    which Flask signs into one cookie. Werkzeug's limit is 4093 bytes and a browser drops
    an oversized cookie silently - taking the login, the flash and the CSRF token with it,
    so the redirect to the completion page would land on the login form."""
    as_role("contributor")
    huge = dict(EXPORT, config=dict(EXPORT["config"], shingler=HUGE_HASH))

    response = upload(client, huge)
    oversized = [w for w in recwarn.list if "cookie is too large" in str(w.message)]
    assert not oversized, f"the refusal logged the user out: {oversized[0].message}"
    assert len(response.get_data()) < 2048, "the 400 echoed the upload back at the browser"


# --- the sample dropzone: binary payloads, and the fields that ride with them ------

#: Comfortably past the 500 kB `max_form_memory_size` Werkzeug 3.1 began enforcing.
#: That limit is scoped to non-file fields - `formparser.MultiPartParser` sets
#: `field_size = None` for a File part - and this is what holds that to be true, since
#: the whole point of this dropzone is uploading executables.
LARGE_BINARY = b"MZ" + bytes(range(256)) * 8192

#: What the dropzone's `sending` handler appends alongside the file, from
#: `#dropzone-additional-fields-form`.
SUBMIT_FIELDS = {"family": "test.family", "version": "1.0", "options": "unmapped"}


def submit_binary(client, content, filename="sample.exe", **fields):
    """POST to the sample dropzone the way the browser does: one file part plus the
    additional form fields, in a single multipart body."""
    data = dict(SUBMIT_FIELDS, **fields)
    data["file"] = (io.BytesIO(content), filename)
    return client.post("/data/submit", data=data, content_type="multipart/form-data")


def test_a_binary_far_past_the_form_memory_limit_is_still_accepted(client, as_role, fake_mcrit):
    """The upload is a file part, so Werkzeug's non-file field limit must not apply to
    it - and the bytes must arrive intact, not truncated at a buffer boundary."""
    as_role("contributor")
    response = submit_binary(client, LARGE_BINARY)

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    queued = [call for call in fake_mcrit.calls if call[0] == "addBinarySample"]
    assert len(queued) == 1, "the binary never reached the backend"
    assert queued[0][1][0] == LARGE_BINARY


def test_the_fields_beside_the_file_travel_with_it(client, as_role, fake_mcrit):
    """Family and version are typed into a form that is *not* the dropzone's own; the
    `sending` handler copies them into the multipart body. If that ever stops working
    every upload lands unlabelled.

    What arrives at the client is the wire form, not the typed form: `data.submit`
    percent-encodes all three for the query string McritClient builds by hand, so the
    expectations are written through the same encoder. These particular values happen
    to be invariant under it, and asserting them raw would pass for that reason alone -
    and then confuse whoever first puts a '+' or an '&' in a fixture here. What the
    encoding is *for* is tests/testSubmitMetadata.py."""
    as_role("contributor")
    submit_binary(client, b"MZ small", filename="thing.exe")

    _, _, kwargs = next(c for c in fake_mcrit.calls if c[0] == "addBinarySample")
    assert kwargs["family"] == quote("test.family", safe="")
    assert kwargs["version"] == quote("1.0", safe="")
    assert kwargs["filename"] == quote("thing.exe", safe="")


def test_a_dump_carries_its_bitness_and_base_address(client, as_role, fake_mcrit):
    """The 'dumped' radio reveals two more fields, and the view parses both - base
    address as hex. A memory dump without them cannot be disassembled correctly."""
    as_role("contributor")
    submit_binary(client, b"MZ dumped", options="dumped", bitness="64", base_addr="0x140000000")

    _, _, kwargs = next(c for c in fake_mcrit.calls if c[0] == "addBinarySample")
    assert kwargs["is_dump"] is True
    assert kwargs["bitness"] == 64
    assert kwargs["base_addr"] == 0x140000000


# --- the filename probe the dropzone fires on drop ---------------------------------

def filename_info(client, filename, file_header="", file_metadata=None):
    """The XHR `addedfile` sends: a JSON body, so a header-borne CSRF token and no
    form field at all. `file_metadata` is omitted entirely when None, which is what a
    client older than that field looks like."""
    body = {"filename": filename, "file_header": file_header, "form": []}
    if file_metadata is not None:
        body["file_metadata"] = file_metadata
    response = client.post(
        "/data/request_filename_info", data=json.dumps(body), content_type="application/json"
    )
    return json.loads(response.get_data(as_text=True))


def smda_report_text(family="test.family", version="1.0"):
    """A report serialised the way the smda CLI writes one: `BatchProcessor.py:70` does
    `json.dump(..., indent=1, sort_keys=True)`. That sorting is the whole problem -
    base_addr and bitness sort to the front, while metadata lands after code_areas,
    code_sections and disassembly_errors."""
    report = {
        "architecture": "intel", "abi": "cdecl", "base_addr": 4194304,
        "binary_size": 2371584, "bitness": 64,
        "code_areas": [[4198400, 4300000], [4300000, 4400000], [4400000, 4500000]],
        "code_sections": [["", 4198400, 4300000], ["", 4300000, 4400000]],
        "confidence_threshold": 0.5,
        "disassembly_errors": {str(a): "decode" for a in range(4198400, 4198430)},
        "execution_time": 12.34, "identified_alignment": 16,
        "metadata": {
            "binweight": 1234, "component": "", "family": family, "filename": "sample.exe",
            "is_library": False, "is_buffer": False, "language": "C", "version": version,
        },
        "message": "", "oep": 4198400, "sha256": "ab" * 32, "smda_version": "4.4.4",
        "statistics": {}, "status": "ok", "timestamp": "2026-08-07T12-00-00", "xcfg": {},
    }
    return json.dumps(report, indent=1, sort_keys=True)


def browser_windows(text):
    """The two slices the patched `dropzone.js` cuts and sends: the first 1024 bytes,
    and 1024 bytes from wherever `"metadata"` actually begins."""
    data = text.encode()
    at = data.find(b'"metadata"')
    return data[:1024].decode(), ("" if at < 0 else data[at:at + 1024].decode())


def test_a_dump_filename_yields_bitness_and_base_address(client, as_role):
    """`_0x` plus 8 hex digits means 32-bit, more than 8 means 64-bit - this is what
    pre-fills the form the moment a file is dropped."""
    as_role("contributor")
    assert filename_info(client, "malware_dump_0x140000000.bin") == {
        "dump": True,
        "bitness": 64,
        "base_addr": "0x140000000",
    }


def test_an_smda_report_is_read_out_of_the_uploaded_header(client, as_role):
    """For .smda the answers come from the first bytes of the file itself, which the
    browser reads and sends as text. Regex over a prefix, so a truncated header is
    normal input."""
    as_role("contributor")
    header = '{"family": "test.family", "version": "2.1", "bitness": 32, "base_addr": 4194304'
    result = filename_info(client, "report.smda", header)

    assert result["smda"] is True
    assert result["family"] == "test.family"
    assert result["version"] == "2.1"
    assert result["bitness"] == 32
    assert result["base_addr"] == "0x400000"


def test_an_unreadable_probe_body_claims_nothing(client, as_role):
    """The probe swallows a body it cannot read and carries on with an empty filename,
    which every test below has to survive as an ordinary input rather than a 500."""
    as_role("contributor")
    response = client.post("/data/request_filename_info", data=b"{not json",
                           content_type="application/json")
    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {"dump": False}


@pytest.mark.parametrize("filename", ["sample.exe", "notepad_0x00400000.bin", "d.u.m.p"])
def test_an_ordinary_filename_claims_nothing(client, as_role, filename):
    """No pattern matched must mean "not a dump", not a half-filled form - not even for
    a name that carries a base address, or the letters of "dump" spelled apart."""
    as_role("contributor")
    assert filename_info(client, filename) == {"dump": False}


def test_a_realistically_serialised_smda_report_fills_every_field(client, as_role):
    """The one that was broken in the live instance: base address filled, family and
    version stayed empty, because a 1024-byte prefix never reaches metadata."""
    as_role("contributor")
    header, metadata = browser_windows(smda_report_text())
    assert '"family"' not in header, "fixture no longer reproduces the bug it exists for"

    assert filename_info(client, "report.smda", header, metadata) == {
        "smda": True,
        "family": "test.family",
        "version": "1.0",
        "bitness": 64,
        "base_addr": "0x400000",
    }


def test_a_client_that_sends_no_metadata_window_still_works(client, as_role):
    """A cached older dropzone.js omits the field entirely. It must degrade to what it
    could always do - base address and bitness - not fail the request."""
    as_role("contributor")
    header, _ = browser_windows(smda_report_text())
    result = filename_info(client, "report.smda", header)

    assert result["bitness"] == 64
    assert result["base_addr"] == "0x400000"
    assert result["family"] is None


# --- "dedumped" is not a dump - issue #44 -------------------------------------------

@pytest.mark.parametrize(
    "filename",
    [
        "sample_dedumped.bin",
        # a de-dumped file often keeps the base address of the dump it came from in its
        # name; that must not turn it back into a dump either
        "sample_dedumped_0x00400000.bin",
        "dedumped_thing",
        # the separator is a matter of taste, and so is the stem: excluding the single
        # literal "dedumped" left every one of these reading as a dump
        "sample_de-dumped.bin",
        "sample_de_dumped.bin",
        "sample_de.dumped.bin",
        "sample de dumped.bin",
        "sample_dedump.bin",
        "sample_dedumping.bin",
        "sample_dedumped2.bin",
        # case only ever cancelled out: "DeDumped" was excluded by the *positive* test
        # being case-sensitive too, and "DEdumped" was excluded by neither
        "sample_DeDumped.bin",
        "sample.DEDUMPED.bin",
        "sample_DEdumped.bin",
        "SAMPLE_DEDUMPED.BIN",
    ],
)
def test_a_dedumped_file_is_offered_as_unmapped(client, as_role, filename):
    """`dedumped` contains `dump`, so the substring test used to answer "dump" for a
    file that is precisely the opposite - un-mapped again. The form then opened its
    dump fields, prefilled with the empty base address the filename does not carry,
    and submitting that took the request down."""
    as_role("contributor")
    assert filename_info(client, filename) == {"dump": False}


@pytest.mark.parametrize(
    "filename, bitness, base_addr",
    [
        ("malware_dump_0x140000000.bin", 64, "0x140000000"),
        ("malware_dump_0x00400000.bin", 32, "0x400000"),
        # no base address in the name, but still a dump the user has to fill in
        ("notepad.dumped", None, ""),
        ("memdump", None, ""),
        # "de" only marks a de-dump where it starts a token - inside a word it is just
        # the tail of the word before it
        ("widedump.bin", None, ""),
        ("sidedump.bin", None, ""),
        ("WideDump.bin", None, ""),
        # both markers: the name carries a de-dump marker *and* a dump of its own, so
        # the dump wins. The other way round costs the user a base address they have
        ("dump_dedumped_0x400000.bin", None, ""),
        ("dump_dedumped_0x00400000.bin", 32, "0x400000"),
        # a behaviour change: master matched "dump" case-sensitively, so a name a
        # Windows tool wrote in caps was not offered as a dump at all
        ("SAMPLE_DUMP.BIN", None, ""),
        ("SAMPLE_DUMP_0x00400000.BIN", 32, "0x400000"),
        ("Sample_Dump.bin", None, ""),
    ],
)
def test_a_genuine_dump_is_still_recognised(client, as_role, filename, bitness, base_addr):
    """The narrowed test must only remove de-dumps, nothing else that reads as a
    dump."""
    as_role("contributor")
    assert filename_info(client, filename) == {
        "dump": True,
        "bitness": bitness,
        "base_addr": base_addr,
    }


# --- the dump fields are user input, so they are validated - issue #44 --------------

def query_binary(client, content, filename="sample.bin", **fields):
    """POST to the query half of the same dropzone."""
    data = dict({"options": "unmapped"}, **fields)
    data["file"] = (io.BytesIO(content), filename)
    return client.post("/analyze/query", data=data, content_type="multipart/form-data")


#: Everything a browser can put in the base address field that is not an address. The
#: field is a free text input and the dropzone serialises the form by hand, so none of
#: this is filtered before it reaches the view.
BAD_BASE_ADDRESSES = [
    "",                       # what a prefilled 'dedumped' form submits
    "   ",
    "0x",
    "not an address",
    "0xdeadbeefzz",
    "-0x400000",              # int(_, 16) accepts this, MCRIT cannot map it
    "0x1_0000",               # so does int(_, 16), via PEP 515 separators
    "0x10000000000000000",    # one bit past a 64 bit address space
]


@pytest.mark.parametrize("base_addr", BAD_BASE_ADDRESSES)
def test_a_dump_submit_without_a_usable_base_address_is_refused(client, as_role, fake_mcrit, base_addr):
    """`int(request.form['base_addr'], 16)` had no guard, so an empty field - the one a
    'dedumped' filename used to produce - was a 500 on ordinary user input."""
    as_role("contributor")
    response = submit_binary(client, b"MZ dumped", options="dumped", bitness="32", base_addr=base_addr)

    assert response.status_code == 400, response.get_data(as_text=True)[:200]
    assert not [call for call in fake_mcrit.calls if call[0] == "addBinarySample"], \
        "a sample was submitted without a base address the user actually gave"


@pytest.mark.parametrize("base_addr", BAD_BASE_ADDRESSES)
def test_a_dump_query_without_a_usable_base_address_is_refused(client, as_role, fake_mcrit, base_addr):
    """The same field on the query half of the same form, which parses it separately."""
    as_role("contributor")
    response = query_binary(client, b"MZ dumped", options="dumped", base_addr=base_addr)

    assert response.status_code == 400, response.get_data(as_text=True)[:200]
    assert not [call for call in fake_mcrit.calls if call[0] == "requestMatchesForMappedBinary"], \
        "a match was requested at a base address the user never gave"


def test_a_dump_submit_without_any_base_address_field_is_refused(client, as_role):
    """An absent field, not an empty one: `request.form['base_addr']` raised a
    KeyError, which at least was a 400 - but silently, with nothing said to the user."""
    as_role("contributor")
    data = {"family": "f", "version": "1", "options": "dumped", "bitness": "32",
            "file": (io.BytesIO(b"MZ dumped"), "sample_dedumped.bin")}
    response = client.post("/data/submit", data=data, content_type="multipart/form-data")
    assert response.status_code == 400


@pytest.mark.parametrize("bitness", ["", "0", "48", "thirtytwo", "32.0", "0x20"])
def test_a_dump_submit_without_a_usable_bitness_is_refused(client, as_role, fake_mcrit, bitness):
    """An unchecked radio is simply absent from the serialised form, and `int()` on
    whatever else arrives is the same unguarded crash."""
    as_role("contributor")
    response = submit_binary(client, b"MZ dumped", options="dumped", bitness=bitness, base_addr="0x400000")

    assert response.status_code == 400, response.get_data(as_text=True)[:200]
    assert not [call for call in fake_mcrit.calls if call[0] == "addBinarySample"]


def test_a_dump_submit_with_no_bitness_field_at_all_is_refused(client, as_role):
    """What the browser actually sends when neither bitness radio was ever clicked."""
    as_role("contributor")
    data = {"family": "f", "version": "1", "options": "dumped", "base_addr": "0x400000",
            "file": (io.BytesIO(b"MZ dumped"), "sample_dedumped.bin")}
    response = client.post("/data/submit", data=data, content_type="multipart/form-data")
    assert response.status_code == 400


@pytest.mark.parametrize("base_addr, parsed", [("0x400000", 0x400000), ("400000", 0x400000),
                                               ("0X400000", 0x400000), (" 0x400000 ", 0x400000),
                                               ("0xffffffffffffffff", 0xFFFFFFFFFFFFFFFF), ("0x0", 0)])
def test_a_well_formed_base_address_still_reaches_the_backend(client, as_role, fake_mcrit, base_addr, parsed):
    """The guard must not narrow what used to work: `int(_, 16)` accepted a bare hex
    string, an 0x prefix in either case, and surrounding whitespace."""
    as_role("contributor")
    response = query_binary(client, b"MZ dumped", options="dumped", base_addr=base_addr)

    assert response.status_code < 400, response.get_data(as_text=True)[:200]
    _, args, _ = next(c for c in fake_mcrit.calls if c[0] == "requestMatchesForMappedBinary")
    assert args[1] == parsed


# --- an .smda report is not a memory dump, and carries its own address -------------

def smda_upload(client, route, filename="report.smda", **fields):
    """POST a serialised SMDA report through either half of the dropzone, with only
    the fields the browser actually sends for one: `#base_addr` and the bitness radios
    belong to the "Dumped" option and stay empty here."""
    data = dict({"family": "test.family", "version": "1.0", "options": "smda"}, **fields)
    data["file"] = (io.BytesIO(smda_report_text().encode()), filename)
    return client.post(route, data=data, content_type="multipart/form-data")


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"a": 1}', b"", b"\x00\x01\x02"],
    ids=["not-json", "not-an-object", "incomplete", "empty", "binary"],
)
@pytest.mark.parametrize("route", ["/data/submit", "/analyze/query"])
def test_a_body_that_is_not_a_readable_smda_report_is_refused(client, as_role, fake_mcrit, route, body):
    """The file is whatever was dropped on the page, so an unreadable one is ordinary
    input and has to become a message rather than a stack trace.

    This used to be covered by accident on the `smda` option: the base address was
    demanded first, so an empty one answered 400 before anything was parsed. That check
    now correctly applies only to a dump - the SMDA path never reads a base address -
    which leaves the parse as the first thing an unreadable body reaches. Without a
    guard of its own that is a 500, and it was already one on master whenever the
    dropzone had filled the address in, which it does for a `.smda` drop.
    """
    as_role("contributor")
    response = client.post(
        route,
        data={
            "family": "test.family", "version": "1.0", "options": "smda",
            "file": (io.BytesIO(body), "report.smda"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert not [call for call in fake_mcrit.calls if call[0] in ("addReport", "addBinarySample", "requestMatchesForSmdaReport")], \
        "an unreadable report reached the backend"


@pytest.mark.parametrize("base_addr", ["", "not an address"])
def test_an_smda_submit_ignores_the_dump_fields(client, as_role, fake_mcrit, base_addr):
    """`McritClient.addReport` takes the report and nothing else - the base address and
    bitness it uses are the ones inside the report. Validating the dump form fields on
    this path refuses a perfectly good report over a field nobody reads, and the probe
    that pre-fills those fields clears them for a .smda drop."""
    as_role("contributor")
    response = smda_upload(client, "/data/submit", base_addr=base_addr)

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    reported = [call for call in fake_mcrit.calls if call[0] == "addReport"]
    assert len(reported) == 1, "the report never reached the backend"
    assert reported[0][1][0].base_addr == 0x400000
    assert not [call for call in fake_mcrit.calls if call[0] == "addBinarySample"], \
        "the report was submitted as a raw binary instead"


def test_an_smda_submit_needs_no_dump_fields_at_all(client, as_role, fake_mcrit):
    """The fields are absent, not empty, whenever the user never opened the dump
    option - which is every .smda upload the probe recognises, and every report the
    user picks "SMDA" for by hand because its name does not end in .smda."""
    as_role("contributor")
    data = {"family": "f", "version": "1", "options": "smda",
            "file": (io.BytesIO(smda_report_text().encode()), "report.json")}
    response = client.post("/data/submit", data=data, content_type="multipart/form-data")

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    assert [call for call in fake_mcrit.calls if call[0] == "addReport"]


@pytest.mark.parametrize("base_addr", ["", "not an address"])
def test_an_smda_query_ignores_the_dump_fields(client, as_role, fake_mcrit, base_addr):
    """`requestMatchesForSmdaReport` takes no base address either, so the query half
    must not refuse the upload over one."""
    as_role("contributor")
    response = smda_upload(client, "/analyze/query", base_addr=base_addr)

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    queried = [call for call in fake_mcrit.calls if call[0] == "requestMatchesForSmdaReport"]
    assert len(queried) == 1, "the report never reached the backend"
    assert queried[0][1][0].base_addr == 0x400000


def test_an_smda_query_needs_no_dump_fields_at_all(client, as_role, fake_mcrit):
    """The query form has no bitness radios at all, so this is what it sends whenever
    the user has not typed a base address into the field the "Dumped" option owns."""
    as_role("contributor")
    data = {"options": "smda",
            "file": (io.BytesIO(smda_report_text().encode()), "report.smda")}
    response = client.post("/analyze/query", data=data, content_type="multipart/form-data")

    assert response.status_code == 202, response.get_data(as_text=True)[:200]
    assert [call for call in fake_mcrit.calls if call[0] == "requestMatchesForSmdaReport"]


def test_the_refusal_message_survives_to_the_page_the_dropzone_reloads(client, as_role):
    """`flash()` followed by an empty 400 body says nothing by itself - the dropzone's
    own error handler is what shows it: `myDropzone.on('error', ... location.reload())`
    in table/submit_or_query_dropzone.html re-renders the page, and the message is
    still in the session waiting for it."""
    as_role("contributor")
    refused = submit_binary(client, b"MZ dumped", options="dumped", bitness="32", base_addr="")
    assert refused.status_code == 400
    assert refused.get_data() == b"", "the 400 body is empty, so the flash is all the user gets"

    reloaded = client.get("/data/submit").get_data(as_text=True)
    assert "Please enter the base address" in reloaded


# --- what the import page says while the import runs - issue #66 -------------------
#
# The import page used to go silent the moment a file was dropped. Dropzone draws its
# own upload bar, but that bar is finished once the last byte has left the browser -
# and the *import* has not even started then. MCRIT's `/import` is a single blocking
# POST (`McritClient.addImportData` -> `StatusResource.on_post_import`): it is not
# queued, it hands back no job_id, and so there is no job for `job_in_progress.html`
# to render a real percentage from. What the page can honestly do is say which phase
# the request is in and keep saying it until the request comes back, which is what
# these tests hold in place.
#
# They assert on the rendered wiring, because the behaviour itself only exists in a
# browser; it was checked by hand against a backend stub with a slow addImportData.

#: The extension emits `Dropzone.options.myDropzone = {...}` in its own script block.
#: Scoping the assertions to it keeps them from passing on some unrelated script in
#: base.html.
DROPZONE_OPTIONS = re.compile(r"Dropzone\.options\.myDropzone\s*=\s*\{.*?\n\s*\};", re.DOTALL)


def dropzone_options(page):
    match = DROPZONE_OPTIONS.search(page)
    assert match, "the import page no longer configures a dropzone"
    return match.group(0)


def test_the_import_page_says_the_upload_is_being_processed(client, as_role):
    """Issue #66: after the drop, the page showed the file tile and nothing else for
    however long the server took. The upload phase and the far longer server phase
    that follows it both have to be announced, or the user cannot tell a running
    import from a page that ignored the drop."""
    as_role("contributor")
    page = client.get("/data/import").get_data(as_text=True)
    options = dropzone_options(page)

    assert 'id="import-status-text"' in page, "there is nowhere to put a status message"
    assert '.on("sending"' in options, "nothing is said when the upload starts"
    assert '.on("uploadprogress"' in options, "the upload's own progress is not reported"
    assert "importing the data" in options, "the wait on the server is not announced"


def test_a_failed_import_is_not_replaced_by_the_completion_page(client, as_role):
    """A failing import used to end up on `import_complete` being told 'This doesn't
    seem to be valid MCRIT data in JSON format' - which is a lie when the data was
    fine and the backend fell over. Flask-Dropzone's built-in redirect fires on
    `queuecomplete` whatever happened, so the page has to own the redirect and take
    the failure branch instead."""
    as_role("contributor")
    options = dropzone_options(client.get("/data/import").get_data(as_text=True))

    assert '.on("error"' in options, "an upload error is never shown"
    assert options.count("/data/import_complete") == 1, (
        "more than one navigation to import_complete - the extension's unconditional "
        "redirect is still armed alongside the page's own"
    )
    assert "Dropzone.ERROR" in options, "the redirect is not guarded by the error state"


def test_the_status_line_cannot_be_built_out_of_a_filename(client, as_role):
    """The name of a dropped file is chosen by whoever produced the file, which in a
    malware analysis UI means it is attacker-controlled. The status line quotes it
    back, so it has to be written as text and never as markup."""
    as_role("contributor")
    options = dropzone_options(client.get("/data/import").get_data(as_text=True))

    assert "textContent" in options, "the status line is not written as text"
    assert "innerHTML" not in options, "a filename reaches the page as markup"
