#!/usr/bin/python
"""What the pages say when a search does not come back.

`McritClient.search_*` answers None when the call to the backend failed. A search
that simply matched nothing is a well-formed result with no rows - a different thing,
reported by the pages themselves since issue #54. The old message, "Ups, search for X
in MCRIT's samples failed!", was the same either way and told the reader nothing
actionable. Issue #79.

The SHA-256 case gets its own answer: someone pasting a hash is asking whether the
sample is known, and `getSampleBySha256` can still say so when the search endpoint
could not.
"""

import logging
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: 64 hex characters, and deliberately not one the corpus holds
ABSENT_SHA256 = "f" * 64


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


def _search_fails(monkeypatch, backend):
    for method in ("search_families", "search_samples", "search_functions"):
        monkeypatch.setattr(backend, method, lambda *args, **kwargs: None)


def test_a_failed_family_search_says_the_backend_did_not_answer(client, as_role, fake_mcrit, monkeypatch):
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get("/explore/families?query=anything", follow_redirects=True).get_data(as_text=True)

    assert "the backend did not answer" in page
    assert "Ups" not in page, "the old wording is gone"


def test_a_failed_search_for_something_that_is_not_a_hash_says_so_plainly(client, as_role, fake_mcrit, monkeypatch):
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get("/explore/samples?query=notahash", follow_redirects=True).get_data(as_text=True)

    assert "Could not search MCRIT&#39;s samples for &#39;notahash&#39;" in page
    assert "SHA-256" not in page, "no hash was involved"


def test_a_hash_that_is_not_in_the_collection_is_reported_as_absent(client, as_role, fake_mcrit, monkeypatch):
    """The answer issue #79 actually asks for."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get(f"/explore/samples?query={ABSENT_SHA256}", follow_redirects=True).get_data(as_text=True)

    assert f"No sample with SHA-256 {ABSENT_SHA256} is in the collection." in page
    assert "did not answer" not in page, "this is an answer, not a failure"


def test_a_hash_that_is_in_the_collection_is_not_reported_as_absent(client, as_role, fake_mcrit, monkeypatch):
    """The lookup has to be able to say "yes" too, or it is just a nicer way to be
    wrong - and being told a sample is missing when it is there is the worse error."""
    as_role("visitor")
    known = next(iter(fake_mcrit._samples.values()))
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get(f"/explore/samples?query={known.sha256}", follow_redirects=True).get_data(as_text=True)

    assert "is not in the collection" not in page
    assert f"does exist, as sample {known.sample_id}" in page


def test_the_page_survives_the_lookup_failing_as_well(client, as_role, fake_mcrit, monkeypatch):
    """The second opinion is best-effort. A backend that is down will fail both calls,
    and that must be a message rather than a 500."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    def unreachable(*args, **kwargs):
        raise ConnectionError("backend is down")

    monkeypatch.setattr(fake_mcrit, "getSampleBySha256", unreachable)

    response = client.get(f"/explore/samples?query={ABSENT_SHA256}", follow_redirects=True)

    assert response.status_code == 200
    assert "the backend did not answer" in response.get_data(as_text=True)


def test_the_combined_search_page_reports_a_failure_the_same_way(client, as_role, fake_mcrit, monkeypatch):
    """`explore.search` has its own copy of each of these branches."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get(f"/explore/search?query={ABSENT_SHA256}", follow_redirects=True).get_data(as_text=True)

    assert f"No sample with SHA-256 {ABSENT_SHA256} is in the collection." in page
    assert "Ups" not in page


if __name__ == "__main__":
    unittest.main()


# --- the second opinion has to be able to say "I do not know" ----------------
#
# `getSampleBySha256` in its ordinary mode returns None for "not in the collection" and
# for every failure alike: handle_response maps 400/404/410 and 500/501 to None, and
# falls through to None for any status it does not enumerate - 401, 403, 502, 503. The
# first version of this feature read that None as absence, so a backend answering 500 on
# every endpoint produced "No sample with SHA-256 X is in the collection" as an `info`
# message, which is a false statement of fact *and* suppressed the failure notice for
# samples. That is the error issue #79 is about, pointed the other way.

class FailingLookup:
    """A raw-mode backend that answers `status` to every SHA-256 lookup."""

    def __init__(self, status):
        self.status = status
        self.raw = True

    def getSampleBySha256(self, *args, **kwargs):
        from fixtureData import RawResponse
        return RawResponse(self.status)


@pytest.mark.parametrize("status", [500, 502, 401, 403, 400])
def test_a_lookup_that_failed_is_not_reported_as_absence(client, app, as_role, fake_mcrit, monkeypatch, status):
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)
    failing = FailingLookup(status)
    app.config["MCRIT_CLIENT_FACTORY"] = (
        lambda **kwargs: failing if kwargs.get("raw_responses") else fake_mcrit
    )

    page = client.get(f"/explore/samples?query={ABSENT_SHA256}", follow_redirects=True).get_data(as_text=True)

    assert "is in the collection" not in page, f"claimed absence from an HTTP {status}"
    assert "the backend did not answer" in page, "and hid the failure while doing it"


def test_only_a_404_is_read_as_absence(client, app, as_role, fake_mcrit, monkeypatch):
    """The positive half of the test above: 404 really is the backend saying no."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)
    app.config["MCRIT_CLIENT_FACTORY"] = (
        lambda **kwargs: FailingLookup(404) if kwargs.get("raw_responses") else fake_mcrit
    )

    page = client.get(f"/explore/samples?query={ABSENT_SHA256}", follow_redirects=True).get_data(as_text=True)

    assert f"No sample with SHA-256 {ABSENT_SHA256} is in the collection." in page


def test_the_family_page_does_not_quote_its_own_query_syntax_back(client, as_role, fake_mcrit, monkeypatch):
    """`family_by_id` composes "family_id:N <what the user typed>" before searching.
    Reporting the failure with that string shows MCRIT's query language to someone who
    never wrote it - and, if the typed half were a bare hash, would run the SHA-256
    second opinion against a string that is not one."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get("/explore/families/1?query=zzz", follow_redirects=True).get_data(as_text=True)

    assert "family_id:" not in page
    assert "for &#39;zzz&#39;" in page


def test_a_failed_search_with_no_search_term_still_reads_as_a_sentence(client, as_role, fake_mcrit, monkeypatch):
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get("/explore/families/1", follow_redirects=True).get_data(as_text=True)

    assert "Could not search MCRIT&#39;s samples - the backend did not answer." in page
    assert "for &#39;&#39;" not in page


def test_an_uppercase_hash_finds_the_sample_that_is_there(client, as_role, fake_mcrit, monkeypatch):
    """A hash pasted out of a report or a mail often arrives uppercase. The stored
    value is a lowercase hexdigest and the backend's lookup is an exact match, so
    forwarding the term unchanged reported a sample that is present as absent - a
    confident wrong answer, which is the failure mode #79 exists to remove."""
    as_role("visitor")
    known = next(iter(fake_mcrit._samples.values()))
    _search_fails(monkeypatch, fake_mcrit)

    page = client.get(f"/explore/samples?query={known.sha256.upper()}", follow_redirects=True).get_data(as_text=True)

    assert "is in the collection" not in page, "reported a known sample as absent"
    assert f"does exist, as sample {known.sample_id}" in page


def test_an_uppercase_hash_that_is_absent_is_still_reported_as_absent(client, as_role, fake_mcrit, monkeypatch):
    """And the message quotes the hash the way it was typed, not the folded form."""
    as_role("visitor")
    _search_fails(monkeypatch, fake_mcrit)
    typed = ABSENT_SHA256.upper()

    page = client.get(f"/explore/samples?query={typed}", follow_redirects=True).get_data(as_text=True)

    assert f"No sample with SHA-256 {typed} is in the collection." in page


def test_the_lookup_is_asked_with_the_folded_hash(client, as_role, fake_mcrit, monkeypatch):
    """Pinning the fold at the boundary rather than through the message, so a future
    change to the wording cannot hide it."""
    as_role("visitor")
    known = next(iter(fake_mcrit._samples.values()))
    _search_fails(monkeypatch, fake_mcrit)

    client.get(f"/explore/samples?query={known.sha256.upper()}", follow_redirects=True)

    asked = [call for call in fake_mcrit.calls if call[0] == "getSampleBySha256"]
    assert asked, "the second opinion was never asked"
    assert asked[-1][1][0] == known.sha256
