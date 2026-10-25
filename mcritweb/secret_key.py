"""A stable, random `SECRET_KEY` for deployments that never configured one.

`SECRET_KEY` signs the session cookie, and the session cookie is the entire proof
of who a caller is. With the historical default of `'dev'`, anyone who has read this
source - it is a public repository - can mint a cookie that says `role: admin` and
skip authentication altogether. Every other defence in the application, CSRF tokens
included, sits behind that one signature.

So when the operator has not set a key, generate one and keep it. Keeping it matters
as much as generating it: a fresh key per process would log every user out on each
restart, and break outright across the multiple workers a WSGI server runs.

Those workers are also why creating it has to be atomic. gunicorn without `--preload`
runs the app factory in every worker, so on a first start several processes look for
the file at once. Each must come away with the one key that ends up on disk - a worker
holding a key of its own would reject every session and CSRF token the others issue,
until the next restart. So a new key is written in full to a private file first and
published with `os.link`, which fails rather than overwrite when another process won.

An explicit key in `instance/config.py` still wins. That remains the right answer for
a multi-host deployment, where all hosts must share one key and a per-host file
cannot provide that.
"""

import contextlib
import os
import secrets
import tempfile

#: Historical default. Its presence in the config means nobody has set a key.
INSECURE_DEFAULT = "dev"

#: Lives beside `mcritweb.sqlite`, which is already git-ignored and already holds
#: the backend token - so this adds no new class of secret to the instance folder.
FILENAME = "secret_key"


def load_or_create_secret_key(instance_path):
    """Return the persisted key for this instance, creating it on first call."""
    path = os.path.join(instance_path, FILENAME)
    key = _read_key(path)
    if key:
        return key
    staged, key = _stage_new_key(instance_path)
    try:
        while True:
            try:
                os.link(staged, path)
                return key
            except FileExistsError:
                pass
            existing = _read_key(path)
            if existing:
                return existing
            # an empty file this code no longer writes, but older code or a crash
            # mid-write could have left behind. None means it vanished meanwhile;
            # either way, go round and publish again
            if existing == "" and _replace_if_still_empty(path, staged):
                return key
    finally:
        # gone already if it replaced an empty file
        with contextlib.suppress(FileNotFoundError):
            os.unlink(staged)


def _read_key(path):
    """The stripped content of the key file, or None when there is no file."""
    try:
        with open(path) as key_file:
            return key_file.read().strip()
    except FileNotFoundError:
        return None


def _stage_new_key(instance_path):
    """Write a new key in full to a uniquely named file beside the real one.

    mkstemp creates it with O_EXCL and mode 0o600, so it is private from the moment it
    exists - no chmod after the fact - and the fsync means a crash cannot publish an
    empty key."""
    key = secrets.token_hex(32)
    descriptor, staged = tempfile.mkstemp(prefix=f".{FILENAME}.", dir=instance_path)
    try:
        with os.fdopen(descriptor, "w") as key_file:
            key_file.write(key)
            key_file.flush()
            os.fsync(key_file.fileno())
    except BaseException:
        os.unlink(staged)
        raise
    return staged, key


def _replace_if_still_empty(path, staged):
    """Swap `staged` in for an empty key file, unless another process got there first.

    Processes that find the empty file queue on a lock on it. The first one through
    replaces it; the rest then find that `path` names a different file than the one
    they locked, and read the key from that instead. os.replace is atomic, so anyone
    not queueing sees either the empty file or the whole key, never a part of it.

    fcntl is POSIX-only, so it is imported here rather than at the top: only this
    rare path needs it, and the module stays importable everywhere else."""
    import fcntl

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            if not os.path.samestat(os.stat(path), os.fstat(descriptor)):
                return False
        except FileNotFoundError:
            return False
        if os.read(descriptor, 4096).strip():
            return False
        os.replace(staged, path)
        return True
    finally:
        os.close(descriptor)
