#!/usr/bin/python
"""instance/cache/results/ - how a stored report is found again.

`load_cached_result` used to list the whole directory, substring-match the job id
against every filename and json.load() every hit without stopping, which made every
result page O(all reports ever cached). The substring test was also wrong on its own
terms: cache files are named "<utc timestamp>-<job id>.json", so an id that occurs
anywhere in that - inside another id, or inside the timestamp - answered with
somebody else's report. See issue #68.
"""

import datetime
import json
import logging
import os
import unittest

import pytest
from fixtureData import job_id_of, load

import mcritweb.views.data as data_views
from mcritweb.views.data import cache_result, load_cached_result

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

JOB_ID = "6a74660af8b8d2c6f83664f1"


def results_dir(app):
    return os.sep.join([app.instance_path, "cache", "results"])


def write_cached(app, filename, payload):
    path = os.sep.join([results_dir(app), filename])
    with open(path, "w") as fout:
        fout.write(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_a_cached_report_is_found(app):
    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "mine"})
    with app.app_context():
        assert load_cached_result(app, JOB_ID) == {"report": "mine"}


def test_nothing_cached_is_an_empty_result(app):
    with app.app_context():
        assert load_cached_result(app, JOB_ID) == {}


def test_the_newest_of_several_cached_runs_wins(app):
    write_cached(app, f"20250101-000000-{JOB_ID}.json", {"report": "old"})
    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "new"})
    write_cached(app, f"20251231-235959-{JOB_ID}.json", {"report": "middle"})
    with app.app_context():
        assert load_cached_result(app, JOB_ID) == {"report": "new"}


def test_another_jobs_report_is_not_served_for_a_partial_id(app):
    """The old substring test answered for any id contained in a filename."""
    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "someone else's"})
    with app.app_context():
        assert load_cached_result(app, JOB_ID[:12]) == {}
        assert load_cached_result(app, JOB_ID[4:]) == {}


def test_a_partial_uuid_job_id_does_not_match_on_a_dash_boundary(app):
    """A local queue hands out uuid4 job ids, so a tail of one is itself preceded by
    a dash in the filename - a suffix test alone would accept it."""
    uuid_job_id = "2f1a9b04-1f6d-4a1e-9a1c-0c5f2f9b8e77"
    write_cached(app, f"20260806-104636-{uuid_job_id}.json", {"report": "someone else's"})
    with app.app_context():
        assert load_cached_result(app, uuid_job_id) == {"report": "someone else's"}
        assert load_cached_result(app, "4a1e-9a1c-0c5f2f9b8e77") == {}


def test_a_job_id_with_a_trailing_newline_is_not_looked_up(app):
    """`$` matches before a trailing newline, so a pattern ending in it would take
    "<id>\\n" from a URL as a cacheable id. The lookup then only missed because no
    file carries the newline - a coincidence rather than the pattern's own answer."""
    from mcritweb.views.data import is_cacheable_job_id

    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "mine"})

    assert not is_cacheable_job_id(JOB_ID + "\n")
    assert load_cached_result(app, JOB_ID + "\n") == {}
    assert is_cacheable_job_id(JOB_ID)


def test_a_job_id_that_looks_like_a_timestamp_matches_nothing(app):
    """"2026" is in every cache filename ever written."""
    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "someone else's"})
    with app.app_context():
        assert load_cached_result(app, "2026") == {}


@pytest.mark.parametrize("job_id", ["*", "?" * 24, "[a-z]" * 4, "../../../etc/passwd", "a/b", "", None, 42])
def test_a_job_id_that_is_not_a_job_id_is_refused(app, job_id):
    """It arrives straight from the URL, and it is pasted into a filesystem path -
    so the characters it may contain are the ones that cannot leave the cache
    directory. The glob metacharacters are here because they are the shape a reader
    expects to see refused, not because anything globs: `load_cached_result` matches
    names itself."""
    write_cached(app, f"20260806-104636-{JOB_ID}.json", {"report": "someone else's"})
    with app.app_context():
        assert load_cached_result(app, job_id) == {}


def test_an_unreadable_cache_file_falls_back_instead_of_failing_the_page(app):
    """cache_result renames a complete file into place now, but files written by
    earlier versions - or truncated by a full disk - are still out there."""
    write_cached(app, f"20260806-104636-{JOB_ID}.json", '{"report": "truncat')
    write_cached(app, f"20250101-000000-{JOB_ID}.json", {"report": "older but readable"})
    with app.app_context():
        assert load_cached_result(app, JOB_ID) == {"report": "older but readable"}


def test_an_unreadable_cache_file_does_not_need_an_app_context(app):
    """`load_cached_result` is handed the app, which is the signature saying it needs
    no request context - so the fallback path must log through that app rather than
    reach for `current_app`."""
    write_cached(app, f"20260806-104636-{JOB_ID}.json", '{"report": "truncat')
    assert load_cached_result(app, JOB_ID) == {}


def fetches_of(corpus_mcrit, job_id):
    return [call for call in corpus_mcrit.calls if call[0] == "getResultForJob" and call[1][0] == job_id]


def test_a_download_does_not_serve_a_report_cut_short(client, as_role, app, corpus_mcrit):
    """The page skips a cached report that does not parse, and the download streamed it
    as it was. This one is what the writer before atomic writes - json.dump with
    indent=1, straight into the cache - leaves behind when the process dies halfway."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    pretty = json.dumps(load("cross_compare.result"), indent=1)
    write_cached(app, f"20250101-000000-{job_id}.json", pretty[:len(pretty) // 2])

    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert json.loads(response.data) == load("cross_compare.result")
    assert len(fetches_of(corpus_mcrit, job_id)) == 1


def test_a_pretty_printed_report_is_downloaded_from_the_cache_and_kept_compact(client, as_role, app, corpus_mcrit):
    """A whole one costs a parse rather than a fetch, and only once: it is cached again
    compact, and that copy is what the next download streams."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    write_cached(app, f"20250101-000000-{job_id}.json", json.dumps(load("cross_compare.result"), indent=1))

    first = client.get(f"/data/result/{job_id}/download")
    second = client.get(f"/data/result/{job_id}/download")

    assert json.loads(first.data) == load("cross_compare.result")
    assert fetches_of(corpus_mcrit, job_id) == []
    assert len(os.listdir(results_dir(app))) == 2
    assert second.data == first.data and b"\n" not in second.data


def test_a_compact_report_is_downloaded_without_being_parsed(client, as_role, app, monkeypatch):
    """What checking for a report cut short must not cost: one this version cached is
    complete by construction, and is streamed as it lies."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    client.get(f"/data/result/{job_id}")
    [filename] = os.listdir(results_dir(app))
    with open(os.sep.join([results_dir(app), filename]), "rb") as fin:
        on_disk = fin.read()

    def parsed(*args, **kwargs):
        raise AssertionError("the cached report was parsed to be downloaded")

    monkeypatch.setattr(data_views, "load_cached_result", parsed)
    monkeypatch.setattr(data_views.json, "load", parsed)
    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert response.data == on_disk


def test_a_compact_report_that_does_not_end_like_it_begins_is_not_streamed(client, as_role, app, corpus_mcrit):
    """Compact files are renamed into place whole, but #229's branch wrote them in place
    for a while, and before writes were synced a power loss could keep the name of a
    file whose data never got there. The last byte says which."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    compact = json.dumps(load("cross_compare.result"), separators=(",", ":"))
    write_cached(app, f"20250101-000000-{job_id}.json", compact[:len(compact) // 2])

    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert json.loads(response.data) == load("cross_compare.result")
    assert len(fetches_of(corpus_mcrit, job_id)) == 1


@pytest.mark.parametrize("payload", ["true", "5321", "[1,2]"])
def test_a_result_that_is_not_an_object_is_streamed_from_the_cache(client, as_role, app, corpus_mcrit, monkeypatch, payload):
    """A finished deleteSample answers true and a minhash update a count. Both are
    downloadable, and taken for a damaged report they were cached again on every
    download, each copy pushing a real report closer to eviction."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    write_cached(app, f"20250101-000000-{job_id}.json", payload)
    seconds = iter(range(60))
    monkeypatch.setattr(data_views, "utc_now", lambda: datetime.datetime(2026, 1, 1, 0, 0, next(seconds)))

    downloads = [client.get(f"/data/result/{job_id}/download") for _ in range(3)]

    assert [download.data for download in downloads] == [payload.encode()] * 3
    assert len(os.listdir(results_dir(app))) == 1
    assert fetches_of(corpus_mcrit, job_id) == []


def test_a_cached_report_is_synced_before_it_is_renamed_into_place(app, monkeypatch):
    """Renamed first, the new name can outlive its data across a power loss, and the
    download would stream what is left without reading it."""
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(data_views.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(data_views.os, "replace", lambda *args: (events.append("replace"), real_replace(*args))[1])
    with app.app_context():
        cache_result(app, JobStub(JOB_ID), {"some": "report"})

    assert events[:2] == ["fsync", "replace"]


def test_only_the_report_for_this_job_is_read(app, monkeypatch):
    """The point of the change: cost must not grow with the size of the cache."""
    # ids that merely contain this one, which is what the old substring test looked
    # for - it opened every one of them, and answered with whichever came last
    for index in range(50):
        write_cached(app, f"20260806-1046{index:02d}-{JOB_ID}{index:02d}.json", {"report": index})
    write_cached(app, f"20260806-104700-{JOB_ID}.json", {"report": "mine"})

    import mcritweb.views.data as data_module

    opened = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        opened.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(data_module, "open", counting_open, raising=False)
    with app.app_context():
        assert load_cached_result(app, JOB_ID) == {"report": "mine"}
    assert len(opened) == 1, f"read {len(opened)} cache files to answer for one job"


def test_a_result_page_caches_the_report_it_rendered(client, as_role, app, corpus_mcrit):
    """One file, named for this job, holding a report that reads back."""
    from fixtureData import job_id_of
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    client.get(f"/data/result/{job_id}")

    cached = os.listdir(results_dir(app))
    assert len(cached) == 1 and cached[0].endswith(f"-{job_id}.json"), cached
    with open(os.sep.join([results_dir(app), cached[0]])) as fin:
        assert json.load(fin), "the cached report did not survive a round trip"


class JobStub:
    """The two attributes cache_result reads off a job."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.result = "a result id"


def test_a_cached_result_is_written_completely_or_not_at_all(app):
    """cache_result names its file by the second, so two requests for the same job can
    aim at the same path - and json.dump writes as it goes, so a reader can arrive at
    a file that stops mid-token. Renaming a finished file into place is what keeps
    that from happening; writing in place would leave exactly it behind.

    A report that fails to serialise part way through is how that gets provoked here.
    It is not the way it happens in production - a full disk is - but it is the same
    failure: the handle has bytes in it and the write never finishes.
    """
    report = {"padding": "x" * 4096, "not_serialisable": object()}

    with pytest.raises(TypeError):
        cache_result(app, JobStub(JOB_ID), report)

    assert os.listdir(results_dir(app)) == [], "a half-serialised report was left under its final name"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


if __name__ == "__main__":
    unittest.main()
