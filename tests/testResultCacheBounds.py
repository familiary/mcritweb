#!/usr/bin/python
"""instance/cache/results/ - how big a stored report is, and how many are kept.

Every report fetched for a result page or a download is written there, and before
issue #202 nothing ever removed one: the directory grew for the life of the
deployment, and every result page lists it. The reports were also indented, which
made each one about half as large again, on disk and in every download served from
the cache.

What master can and cannot show: the compact-file tests and the page-level bound
tests fail there on behaviour, as do both download fallbacks (a 404 and a 500). The
tests of trim_result_cache, of a failing trim and of the configuration fail there
only because none of it exists; they are guards on this code, not reproductions. Three
of the accepted-value cases (0, 5 and None) pass on master outright, since master
stores any value unchanged.
"""

import json
import logging
import os
import unittest

import pytest
from fixtureData import job_id_of, load

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: Three reports of different kinds, so three distinct job ids to cache.
REPORTS = ["matches_for_sample", "cross_compare", "unique_blocks"]


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """Wire the app in this module to the captured corpus (see conftest)."""
    return corpus_mcrit


def results_dir(app):
    return os.sep.join([app.instance_path, "cache", "results"])


def cached_files(app):
    return sorted(os.listdir(results_dir(app)))


def write_cached(app, filename, size, age):
    """A cache file of `size` bytes, last written `age` seconds before the others."""
    path = os.sep.join([results_dir(app), filename])
    with open(path, "w") as fout:
        fout.write("x" * size)
    os.utime(path, (1_000_000 - age, 1_000_000 - age))
    return path


def trim_result_cache(app):
    # imported here rather than at the top, so that the route-level tests above can
    # still run - and fail - against a tree that does not have it
    from mcritweb.views.data import trim_result_cache
    trim_result_cache(app)


def fetches_of(fake_mcrit, job_id):
    return [call for call in fake_mcrit.calls if call[0] == "getResultForJob" and call[1][0] == job_id]


# --- what a cached report looks like ------------------------------------------------


def test_a_cached_report_is_written_compact(app, client, as_role):
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")

    client.get(f"/data/result/{job_id}")

    [filename] = cached_files(app)
    with open(os.sep.join([results_dir(app), filename]), "rb") as fin:
        on_disk = fin.read()
    assert json.loads(on_disk) == load("matches_for_sample.result")
    assert b"\n" not in on_disk, "the cached report is still indented"
    assert b", " not in on_disk and b": " not in on_disk, "the cached report still pads its separators"


def test_a_download_is_compact_and_the_same_bytes_whether_cached_or_not(app, client, as_role):
    """download_result serialises the report itself on a cache miss and streams the
    cached file on a hit, and the two have to agree byte for byte."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")

    missed = client.get(f"/data/result/{job_id}/download")
    [filename] = cached_files(app)
    with open(os.sep.join([results_dir(app), filename]), "rb") as fin:
        on_disk = fin.read()
    hit = client.get(f"/data/result/{job_id}/download")

    assert missed.data == on_disk == hit.data
    assert b"\n" not in missed.data
    assert json.loads(hit.data) == load("cross_compare.result")


# --- the bounds, through the pages ----------------------------------------------------


def test_viewing_more_reports_than_the_file_bound_keeps_only_the_newest(app, client, as_role):
    app.config["RESULT_CACHE_MAX_FILES"] = 2
    as_role("visitor")

    for report in REPORTS:
        assert client.get(f"/data/result/{job_id_of(report)}").status_code == 200

    names = cached_files(app)
    assert len(names) == 2
    assert not any(name.endswith(f"-{job_id_of(REPORTS[0])}.json") for name in names), "the oldest report was kept"


def test_the_byte_bound_holds_across_page_views(app, client, as_role):
    """Compact, the three reports are about 117, 5 and 158 kB: the first two fit
    together, and the third only fits once the oldest has gone."""
    app.config["RESULT_CACHE_MAX_BYTES"] = 200_000
    as_role("visitor")

    for report in REPORTS:
        assert client.get(f"/data/result/{job_id_of(report)}").status_code == 200

    names = cached_files(app)
    total = sum(os.path.getsize(os.sep.join([results_dir(app), name])) for name in names)
    assert total <= 200_000
    assert sorted(name[len("20260806-104636-"):] for name in names) == sorted(f"{job_id_of(report)}.json" for report in REPORTS[1:])


def test_an_evicted_report_is_fetched_again_when_it_is_next_viewed(app, client, as_role, fake_mcrit):
    app.config["RESULT_CACHE_MAX_FILES"] = 1
    as_role("visitor")
    first, second = job_id_of(REPORTS[0]), job_id_of(REPORTS[1])

    client.get(f"/data/result/{first}")
    client.get(f"/data/result/{second}")
    again = client.get(f"/data/result/{first}")

    assert again.status_code == 200
    assert len(fetches_of(fake_mcrit, first)) == 2
    assert [name for name in cached_files(app) if name.endswith(f"-{first}.json")]


def test_a_download_whose_cached_file_vanishes_is_fetched_instead(app, client, as_role, fake_mcrit, monkeypatch):
    """A concurrent request can evict the report between download_result finding it
    and sending it. That is a cache miss, not a 404."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    monkeypatch.setattr("mcritweb.views.data.find_cached_result_filename", lambda app, job_id: f"20260806-104636-{job_id}.json")

    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert json.loads(response.data) == load("cross_compare.result")
    assert len(fetches_of(fake_mcrit, job_id)) == 1


def test_a_download_whose_cached_file_vanishes_after_the_check_is_fetched_instead(app, client, as_role, fake_mcrit, monkeypatch):
    """The narrower window: send_from_directory has checked the file is there, and it
    is evicted before the open, which raises a bare FileNotFoundError."""
    as_role("visitor")
    job_id = job_id_of("cross_compare")
    client.get(f"/data/result/{job_id}")

    def evicted_before_the_open(*args, **kwargs):
        raise FileNotFoundError(args[1])

    monkeypatch.setattr("mcritweb.views.data.send_from_directory", evicted_before_the_open)
    response = client.get(f"/data/result/{job_id}/download")

    assert response.status_code == 200
    assert json.loads(response.data) == load("cross_compare.result")
    assert len(fetches_of(fake_mcrit, job_id)) == 2


def test_a_trim_that_fails_does_not_fail_the_page(app, client, as_role, monkeypatch):
    """Trimming is bookkeeping. The report is already written and the page renders
    from memory, so an unreadable cache directory is logged, not a 500."""
    def unreadable(app):
        raise PermissionError("instance/cache/results")

    monkeypatch.setattr("mcritweb.views.data.trim_result_cache", unreadable)
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")

    assert client.get(f"/data/result/{job_id}").status_code == 200
    assert client.get(f"/data/result/{job_id}/download").status_code == 200
    assert len(cached_files(app)) == 1


# --- trim_result_cache on its own -----------------------------------------------------


def test_the_oldest_files_go_first(app):
    app.config["RESULT_CACHE_MAX_FILES"] = 2
    write_cached(app, "b-newest.json", 10, age=0)
    write_cached(app, "c-oldest.json", 10, age=20)
    write_cached(app, "a-middle.json", 10, age=10)

    trim_result_cache(app)

    assert cached_files(app) == ["a-middle.json", "b-newest.json"]


def test_the_byte_bound_evicts_until_the_total_fits(app):
    app.config["RESULT_CACHE_MAX_BYTES"] = 250
    write_cached(app, "oldest.json", 100, age=30)
    write_cached(app, "older.json", 100, age=20)
    write_cached(app, "newer.json", 100, age=10)
    write_cached(app, "newest.json", 100, age=0)

    trim_result_cache(app)

    assert cached_files(app) == ["newer.json", "newest.json"]


def test_within_both_bounds_nothing_is_evicted(app):
    app.config["RESULT_CACHE_MAX_BYTES"] = 200
    app.config["RESULT_CACHE_MAX_FILES"] = 2
    write_cached(app, "older.json", 100, age=10)
    write_cached(app, "newer.json", 100, age=0)

    trim_result_cache(app)

    assert cached_files(app) == ["newer.json", "older.json"]


def test_none_lifts_both_bounds(app):
    app.config["RESULT_CACHE_MAX_BYTES"] = None
    app.config["RESULT_CACHE_MAX_FILES"] = None
    for index in range(5):
        write_cached(app, f"{index}.json", 100, age=index)

    trim_result_cache(app)

    assert len(cached_files(app)) == 5


def test_a_file_another_trim_already_removed_counts_as_evicted(app, monkeypatch):
    """Two requests can trim at once and pick the same victim. The second must neither
    fail nor make up for the file it did not remove by evicting one more."""
    app.config["RESULT_CACHE_MAX_FILES"] = 2
    write_cached(app, "oldest.json", 10, age=20)
    write_cached(app, "middle.json", 10, age=10)
    write_cached(app, "newest.json", 10, age=0)
    real_remove = os.remove

    def remove_as_if_raced(path):
        real_remove(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr("mcritweb.views.data.os.remove", remove_as_if_raced)
    trim_result_cache(app)

    assert cached_files(app) == ["middle.json", "newest.json"]


def test_a_file_that_cannot_be_removed_is_skipped_rather_than_fatal(app, monkeypatch):
    """Windows refuses to remove a file another request has open. The cache write
    that triggered the trim must still succeed, and the next-oldest file goes instead."""
    app.config["RESULT_CACHE_MAX_FILES"] = 2
    write_cached(app, "open-elsewhere.json", 10, age=20)
    write_cached(app, "older.json", 10, age=10)
    write_cached(app, "newest.json", 10, age=0)
    real_remove = os.remove

    def refuse_the_open_one(path):
        if path.endswith("open-elsewhere.json"):
            raise PermissionError(path)
        real_remove(path)

    monkeypatch.setattr("mcritweb.views.data.os.remove", refuse_the_open_one)
    trim_result_cache(app)

    assert cached_files(app) == ["newest.json", "open-elsewhere.json"]


def test_the_defaults_bound_the_cache(app):
    """Unbounded is available, as None, but it is not what a deployment gets."""
    assert isinstance(app.config["RESULT_CACHE_MAX_BYTES"], int) and app.config["RESULT_CACHE_MAX_BYTES"] > 0
    assert isinstance(app.config["RESULT_CACHE_MAX_FILES"], int) and app.config["RESULT_CACHE_MAX_FILES"] > 0



# --- the configuration ----------------------------------------------------------------


def build_app(tmp_path, fake_mcrit, **overrides):
    """An app whose config this test chooses - the `app` fixture bakes its own in."""
    from mcritweb import create_app
    from mcritweb.db import ServerInfo, init_db

    instance_path = tmp_path / "configured-instance"
    instance_path.mkdir()
    config = {
        "DATABASE": str(tmp_path / "configured.sqlite"),
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
        "MCRIT_CLIENT_FACTORY": lambda **kwargs: fake_mcrit,
        "MCRIT_SERVER_PROBE": lambda: True,
    }
    config.update(overrides)
    application = create_app(config, instance_path=str(instance_path))
    with application.app_context():
        init_db()
        server_info = ServerInfo()
        server_info.url = "http://127.0.0.1:8000"
        server_info.operation_mode = "multi"
        server_info.registration_token = ""
        server_info.server_token = ""
        server_info.server_uuid = "test-uuid"
        server_info.server_version = "test"
        server_info.saveToDb()
    return application


@pytest.mark.parametrize("setting", ["many", "", "2.0", 2.5, -1, "-1", True, False, [1000]])
def test_a_bound_that_is_not_a_whole_number_falls_back_to_the_default(tmp_path, fake_mcrit, setting):
    """Checked once, when the app is built. Left to the trim, a value like these
    raised a TypeError after the report was written - a 500 on every first view."""
    from mcritweb import RESULT_CACHE_DEFAULTS

    application = build_app(tmp_path, fake_mcrit, RESULT_CACHE_MAX_BYTES=setting, RESULT_CACHE_MAX_FILES=setting)

    assert application.config["RESULT_CACHE_MAX_BYTES"] == RESULT_CACHE_DEFAULTS["RESULT_CACHE_MAX_BYTES"]
    assert application.config["RESULT_CACHE_MAX_FILES"] == RESULT_CACHE_DEFAULTS["RESULT_CACHE_MAX_FILES"]


@pytest.mark.parametrize("setting, expected", [("1000", 1000), (" 7 ", 7), (0, 0), (5, 5), (None, None)])
def test_a_whole_number_a_string_of_digits_or_none_is_taken_as_given(tmp_path, fake_mcrit, setting, expected):
    """Environment-driven config arrives as text, and "1000" is not ambiguous."""
    application = build_app(tmp_path, fake_mcrit, RESULT_CACHE_MAX_FILES=setting)

    assert application.config["RESULT_CACHE_MAX_FILES"] == expected


def test_a_bound_given_as_text_is_enforced(tmp_path, fake_mcrit):
    from werkzeug.security import generate_password_hash

    from mcritweb.db import UserInfo

    application = build_app(tmp_path, fake_mcrit, RESULT_CACHE_MAX_FILES="1")
    with application.app_context():
        user_info = UserInfo()
        user_info.username = "visitor"
        user_info.password = generate_password_hash("password")
        user_info.role = "visitor"
        user_info.apitoken = "apitoken-visitor"
        user_info.saveToDb()
        user_id = UserInfo.fromDb(username="visitor").user_id
    client = application.test_client()
    with client.session_transaction() as test_session:
        test_session["user_id"] = user_id

    for report in REPORTS[:2]:
        assert client.get(f"/data/result/{job_id_of(report)}").status_code == 200

    assert len(os.listdir(os.sep.join([application.instance_path, "cache", "results"]))) == 1


if __name__ == "__main__":
    unittest.main()
