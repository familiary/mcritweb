"""The cross compare set up from a pasted list of SHA256 hashes.

Each hash costs one backend lookup, and every line it ignored used to leave a flash of
its own in the cookie session.
"""
import secrets

import pytest

#: analyze.MAX_HASH_LIST_LENGTH, written out so that these tests run, and fail, on code without it
MAX_HASH_LIST_LENGTH = 250


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def _lookups(corpus_mcrit):
    return [call[1][0] for call in corpus_mcrit.calls if call[0] == "getSampleBySha256"]


def _flashes(client):
    with client.session_transaction() as test_session:
        return [message for _category, message in test_session.get("_flashes", [])]


def _session_cookie(response):
    return next(value for key, value in response.headers.items()
                if key == "Set-Cookie" and value.startswith("session="))


def _known_sha256(corpus_mcrit, count):
    return [sample.sha256 for sample in list(corpus_mcrit._samples.values())[:count]]


def test_a_list_of_unknown_hashes_still_fits_in_the_session(client, as_role):
    """A hundred hashes the corpus does not have: one flash per hash made a session
    cookie of about 5.7 KB, past the 4093 bytes a browser is guaranteed to keep, so
    the user got none of the messages."""
    as_role("visitor")
    hashes = [secrets.token_hex(32) for _ in range(100)]

    response = client.post("/analyze/cross_compare_from_hash_list", data={"hashlist": "\n".join(hashes)})

    assert response.status_code == 302
    assert len(_session_cookie(response)) < 4093
    flashes = _flashes(client)
    assert "100 hash(es) do not correspond to any sample" in flashes[0]
    assert hashes[0] in flashes[0]
    assert hashes[-1] not in flashes[0]


def test_lines_that_are_not_hashes_are_reported_together(client, as_role, corpus_mcrit):
    as_role("visitor")
    known = _known_sha256(corpus_mcrit, 1)[0]
    lines = [known, "", "not a hash"] + [f"md5 {i:032x}" for i in range(50)]

    response = client.post("/analyze/cross_compare_from_hash_list", data={"hashlist": "\n".join(lines)})

    assert response.status_code == 302
    flashes = _flashes(client)
    assert len(flashes) == 1
    # the blank line is not counted as one
    assert flashes[0].startswith("51 line(s) are not SHA256 hashes and were ignored: 'not a hash', ")


def test_each_hash_is_looked_up_once_and_in_lowercase(client, as_role, corpus_mcrit):
    """The corpus stores lowercase hashes and the backend matches them exactly, so an
    uppercase one named no sample."""
    as_role("visitor")
    first, second = _known_sha256(corpus_mcrit, 2)
    lines = [first, first.upper(), second, first]

    response = client.post("/analyze/cross_compare_from_hash_list", data={"hashlist": "\n".join(lines)})

    assert _lookups(corpus_mcrit) == [first, second]
    assert response.status_code == 302
    assert "/analyze/cross_compare?samples=" in response.headers["Location"]
    assert _flashes(client) == []


def test_a_list_longer_than_the_cap_is_cut_before_any_lookup(client, as_role, corpus_mcrit):
    as_role("visitor")
    hashes = [secrets.token_hex(32) for _ in range(MAX_HASH_LIST_LENGTH + 30)]

    client.post("/analyze/cross_compare_from_hash_list", data={"hashlist": "\n".join(hashes)})

    assert _lookups(corpus_mcrit) == hashes[:MAX_HASH_LIST_LENGTH]
    assert any(f"at most {MAX_HASH_LIST_LENGTH} samples, the 30 after that were ignored" in flash
               for flash in _flashes(client))


def test_the_form_names_the_cap(client, as_role):
    as_role("visitor")

    page = client.get("/analyze/cross_compare_from_hash_list").get_data(as_text=True)

    assert f"at most {MAX_HASH_LIST_LENGTH}" in page


def test_the_written_out_cap_is_the_one_the_view_uses():
    from mcritweb.views.analyze import MAX_HASH_LIST_LENGTH as cap

    assert cap == MAX_HASH_LIST_LENGTH
