#!/usr/bin/python
"""Workers starting together end up with one `SECRET_KEY` between them.

gunicorn without `--preload` runs the app factory in every worker, so on a first start
they all look for `instance/secret_key` at the same moment. They used to each find it
missing, each generate a key and each write theirs over the others'. The file ended up
with one key, but every other worker kept signing with its own, so a session or CSRF
token issued by one worker was rejected by the next - until a restart. A worker could
also open the file between another's create and write, read it as empty, and replace
the key that worker was already using.
"""

import errno
import fcntl
import logging
import multiprocessing
import os
import stat

import pytest

from mcritweb import secret_key
from mcritweb.secret_key import FILENAME, load_or_create_secret_key

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

WORKERS = 8
ROUNDS = 40


def _no_hard_links(source, destination):
    """os.link on vfat, exFAT and some SMB and FUSE mounts."""
    raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), source, None, destination)


def _race(instance_paths, barrier, results, hard_links=True):
    """One worker: in every round, wait for the others, then all ask for the key at once."""
    if not hard_links:
        # a spawned process of its own, so this changes nothing outside it
        os.link = _no_hard_links
    for instance_path in instance_paths:
        barrier.wait(timeout=60)
        results.put((instance_path, load_or_create_secret_key(instance_path)))


def _race_workers(instance_paths, hard_links=True):
    """{instance_path: [the key each worker got]} from WORKERS real processes."""
    # spawn rather than fork: the test process may have threads of its own by now
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(WORKERS)
    results = context.Queue()
    workers = [context.Process(target=_race, args=(instance_paths, barrier, results, hard_links)) for _ in range(WORKERS)]
    for worker in workers:
        worker.start()
    keys = {instance_path: [] for instance_path in instance_paths}
    try:
        for _ in range(WORKERS * len(instance_paths)):
            instance_path, key = results.get(timeout=120)
            keys[instance_path].append(key)
    finally:
        for worker in workers:
            worker.join(timeout=60)
            if worker.is_alive():
                worker.terminate()
    assert all(worker.exitcode == 0 for worker in workers), [worker.exitcode for worker in workers]
    return keys


def _fresh_instances(tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / f"round{index}"
        path.mkdir()
        paths.append(str(path))
    return paths


def _assert_one_key_per_instance(keys):
    split = {path: len(set(got)) for path, got in keys.items() if len(set(got)) > 1}
    assert not split, f"{len(split)} of {len(keys)} rounds ended with workers holding different keys: {split}"
    for path, got in keys.items():
        with open(os.path.join(path, FILENAME)) as key_file:
            assert key_file.read() == got[0], "the workers agree on a key that is not the one on disk"
        assert os.listdir(path) == [FILENAME], f"left behind in the instance folder: {os.listdir(path)}"


def test_the_key_file_is_private_from_creation(tmp_path):
    load_or_create_secret_key(str(tmp_path))
    mode = stat.S_IMODE(os.stat(tmp_path / FILENAME).st_mode)
    assert mode == 0o600, oct(mode)


def test_a_second_call_returns_the_same_key(tmp_path):
    first = load_or_create_secret_key(str(tmp_path))
    assert load_or_create_secret_key(str(tmp_path)) == first
    assert (tmp_path / FILENAME).read_text() == first


def test_no_staging_files_are_left_behind(tmp_path):
    load_or_create_secret_key(str(tmp_path))
    load_or_create_secret_key(str(tmp_path))
    assert os.listdir(tmp_path) == [FILENAME]


def test_workers_starting_together_share_one_key(tmp_path):
    _assert_one_key_per_instance(_race_workers(_fresh_instances(tmp_path, ROUNDS)))


def test_workers_starting_together_share_one_key_over_an_empty_file(tmp_path):
    """An empty file left by older code or a crash is replaced once, not once per worker."""
    instance_paths = _fresh_instances(tmp_path, ROUNDS)
    for path in instance_paths:
        with open(os.path.join(path, FILENAME), "w"):
            pass
    _assert_one_key_per_instance(_race_workers(instance_paths))


def test_losing_the_race_returns_the_winners_key(tmp_path, monkeypatch):
    """Another worker publishes between this one finding no file and publishing its
    own. Whatever the timing on a real machine, this is the moment that matters."""
    winner = "a" * 64
    real_link = os.link

    def other_worker_wins(source, destination):
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as key_file:
            key_file.write(winner)
        return real_link(source, destination)

    monkeypatch.setattr(secret_key.os, "link", other_worker_wins)
    assert load_or_create_secret_key(str(tmp_path)) == winner
    assert (tmp_path / FILENAME).read_text() == winner
    assert os.listdir(tmp_path) == [FILENAME], "the losing worker left its staged key behind"


def test_an_empty_file_replaced_meanwhile_is_not_replaced_again(tmp_path, monkeypatch):
    """Two workers find the same empty file; the other one replaces it first."""
    (tmp_path / FILENAME).write_text("")
    winner = "b" * 64
    real_flock = fcntl.flock

    def other_worker_replaces_it_first(descriptor, operation):
        (tmp_path / "other").write_text(winner)
        os.replace(tmp_path / "other", tmp_path / FILENAME)
        return real_flock(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", other_worker_replaces_it_first)
    assert load_or_create_secret_key(str(tmp_path)) == winner
    assert (tmp_path / FILENAME).read_text() == winner
    assert os.listdir(tmp_path) == [FILENAME]


@pytest.mark.parametrize("error", [errno.EPERM, errno.ENOTSUP], ids=["EPERM", "ENOTSUP"])
def test_a_filesystem_without_hard_links_still_gets_a_key(tmp_path, monkeypatch, error):
    """vfat and exFAT refuse a hard link with EPERM, some SMB and FUSE mounts with
    ENOTSUP. The key used to be written in place, which works there, so publishing
    with os.link alone turned a first start on one into a start that fails."""
    def refuse(source, destination):
        raise OSError(error, os.strerror(error), source, None, destination)

    monkeypatch.setattr(secret_key.os, "link", refuse)
    key = load_or_create_secret_key(str(tmp_path))
    assert len(key) == 64
    assert (tmp_path / FILENAME).read_text() == key
    assert stat.S_IMODE(os.stat(tmp_path / FILENAME).st_mode) == 0o600
    assert os.listdir(tmp_path) == [FILENAME], "the staged key was left behind"
    assert load_or_create_secret_key(str(tmp_path)) == key


def test_workers_starting_together_share_one_key_without_hard_links(tmp_path):
    """There the key file exists, empty, before the key is in it, and a worker finding
    it like that must not take it for a leftover to replace."""
    _assert_one_key_per_instance(_race_workers(_fresh_instances(tmp_path, ROUNDS), hard_links=False))


def test_a_key_file_replaced_before_it_was_locked_is_not_claimed(tmp_path, monkeypatch):
    """Without hard links: another worker finds the file this one has just created,
    still empty, and replaces it in the moment before this one locks it to write."""
    winner = "c" * 64
    real_flock = fcntl.flock

    def other_worker_replaces_it_first(descriptor, operation):
        (tmp_path / "other").write_text(winner)
        os.replace(tmp_path / "other", tmp_path / FILENAME)
        return real_flock(descriptor, operation)

    monkeypatch.setattr(secret_key.os, "link", _no_hard_links)
    monkeypatch.setattr(fcntl, "flock", other_worker_replaces_it_first)
    assert load_or_create_secret_key(str(tmp_path)) == winner
    assert (tmp_path / FILENAME).read_text() == winner
    assert os.listdir(tmp_path) == [FILENAME]


def test_without_hard_links_the_key_is_whole_and_locked_when_it_appears(tmp_path, monkeypatch):
    """Without hard links the key is renamed into place. By then it must be complete,
    or a worker reading the file takes it for empty; and the instance folder must be
    locked, or a worker that finds no file publishes its own key over this one."""
    path = tmp_path / FILENAME
    real_replace = os.replace
    seen = []

    def check_then_replace(source, destination, *args, **kwargs):
        seen.append(len(open(source).read()))
        folder = os.open(tmp_path, os.O_RDONLY)
        try:
            fcntl.flock(folder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append("lock was free")
        except BlockingIOError:
            seen.append("locked")
        finally:
            os.close(folder)
        seen.append(path.exists())
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(secret_key.os, "link", _no_hard_links)
    monkeypatch.setattr(secret_key.os, "replace", check_then_replace)
    key = load_or_create_secret_key(str(tmp_path))
    assert seen == [64, "locked", False]
    assert path.read_text() == key


def test_without_hard_links_nothing_is_written_into_the_key_file(tmp_path, monkeypatch):
    """Two workers without hard links went wrong when one created the key file empty
    and filled it in, and another found it empty meanwhile and replaced it. The first
    then wrote its key into a file no longer on the path, and a third worker that had
    opened that file before the replacement read this key from it and used it, while
    everyone else used the winner's. Whatever the timing, that needs the key file to be
    opened for writing; it must only ever arrive by rename."""
    path = str(tmp_path / FILENAME)
    real_open = os.open
    writes = []

    def note_writes(file, flags, *args, **kwargs):
        if os.fspath(file) == path and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            writes.append(file)
        return real_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(secret_key.os, "link", _no_hard_links)
    monkeypatch.setattr(secret_key.os, "open", note_writes)
    key = load_or_create_secret_key(str(tmp_path))
    assert writes == []
    assert (tmp_path / FILENAME).read_text() == key
