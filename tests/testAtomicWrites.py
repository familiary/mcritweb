#!/usr/bin/python
"""`write_atomically` - the writer both caches share.

`cache_result` and `create_match_diagram` write into directories other requests are
reading from, and a diagram takes long enough to render that a reader can catch a
partial one - which would then be served, and browser-cached, as a truncated image.
Both therefore write elsewhere and rename the finished file into place. See issue #68.

What is easy to lose while doing that: the permissions the in-place writes produced,
and the guarantee that the unfinished file is somewhere nobody can ask for.
"""

import logging
import os
import unittest

import pytest

from mcritweb.views.data import incomplete_cache_path, write_atomically

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


def diagrams_dir(app):
    return os.sep.join([app.instance_path, "cache", "diagrams"])


def mode_of_a_plain_write(directory):
    """The permissions master's in-place `open(path, "w")` left a cache file with.

    Probed rather than written down: it is 0666 minus whatever umask the process
    happens to have - 0644 in a default deployment - and the claim under test is
    "the same as an ordinary write here", not "0644".
    """
    probe = os.sep.join([directory, "probe"])
    with open(probe, "w"):
        pass
    mode = os.stat(probe).st_mode & 0o777
    os.remove(probe)
    return mode


def test_the_finished_file_carries_what_was_written_to_it(app):
    write_atomically(app, diagrams_dir(app), "written.txt", lambda fout: fout.write("done"), "w")

    with open(os.sep.join([diagrams_dir(app), "written.txt"])) as fin:
        assert fin.read() == "done"
    assert os.listdir(diagrams_dir(app)) == ["written.txt"], "the write left something else behind"
    assert os.listdir(incomplete_cache_path(app)) == [], "the temporary file outlived the write"


def test_a_cached_file_keeps_the_permissions_an_in_place_write_gave_it(app):
    """tempfile.mkstemp creates at 0600 because it is built for secrets, and
    os.replace carries that mode over - so moving to an atomic write silently
    narrows every cached report and diagram from the 0644 `open(path, "w")` and
    `image.save(path)` produced. Neither is a secret, both are derived from a report
    the app already serves, and a deployment that lets nginx serve instance/cache
    directly, or reads it as a second uid, stops working.
    """
    write_atomically(app, diagrams_dir(app), "written.txt", lambda fout: fout.write("done"), "w")

    path = os.sep.join([diagrams_dir(app), "written.txt"])
    assert oct(os.stat(path).st_mode & 0o777) == oct(mode_of_a_plain_write(diagrams_dir(app)))


def test_the_unfinished_file_is_not_put_in_the_directory_that_is_served(app):
    """`diagram_file` serves any name under cache/diagrams, so a temporary file there
    is fetchable under its own name for as long as it exists - the whole render
    window, and after a SIGKILL forever, with nothing to clean it up. That is the
    partial file this function exists to keep from being served, in a new shape.
    """
    listings = []

    def write(fout):
        listings.append(sorted(os.listdir(diagrams_dir(app))))
        fout.write("done")

    write_atomically(app, diagrams_dir(app), "written.txt", write, "w")

    assert listings == [[]], f"an unfinished file was sitting in the served directory: {listings}"


def test_a_write_that_dies_half_way_through_leaves_nothing_behind(app):
    """Not just no file under the final name - no temporary file either. A cache
    directory that grows a leaked file per failed write is its own problem."""
    def write(fout):
        fout.write("half a file")
        fout.flush()
        raise OSError("no space left on device")

    with pytest.raises(OSError):
        write_atomically(app, diagrams_dir(app), "written.txt", write, "w")

    assert os.listdir(diagrams_dir(app)) == []
    assert os.listdir(incomplete_cache_path(app)) == []


def test_a_text_write_does_not_rewrite_the_newlines(app):
    """What goes in is what lands on disk, byte for byte.

    The report cache is written in text mode and served back verbatim as the raw
    result, so a newline the writer did not ask for makes the served file stop
    matching the bytes it claims to be. Python's text mode does that translation on
    any platform whose line ending is not LF - Windows, in practice.

    Honest limit: on Linux, where text mode translates nothing, this passes with or
    without the fix, so CI cannot see the regression it guards. It is here so the
    invariant is written down and so the check exists where it does bite.
    """
    body = "first" + chr(10) + "second" + chr(10)
    write_atomically(app, diagrams_dir(app), "written.txt", lambda fout: fout.write(body), "w")

    with open(os.sep.join([diagrams_dir(app), "written.txt"]), "rb") as fin:
        on_disk = fin.read()

    assert on_disk == body.encode(), "the cache rewrote the newlines it was handed"
    assert (chr(13) + chr(10)).encode() not in on_disk, "CRLF where the writer wrote LF"


if __name__ == "__main__":
    unittest.main()
