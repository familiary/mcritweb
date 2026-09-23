#!/usr/bin/python
"""The supported Python version is stated in four places. They have to agree.

`README.md` told readers "Python 3.8+" while CI tested 3.11 upwards, `ruff.toml` linted
against py311, and no installable `mcrit` supports anything older.

The floor is **inherited, not intrinsic**. Nothing in mcritweb's own source needs 3.11 -
there is no `match`, no `except*`, no `tomllib`, no `datetime.UTC`, and `ruff.toml`
deliberately ignores UP006/UP007/UP045 so the annotation style stays pre-3.9. What makes
3.8 unusable is the dependency: `mcrit` has declared `>=3.11` since v1.5.0, and the pin
here is `mcrit>=1.5.3`, so pip finds no satisfiable release below 3.11 and fails at
resolution.

Without a `python_requires`, what the reader gets for following the README is that
resolver error - which names neither Python nor the version they need. Declaring the
floor turns it into the sentence pip exists to print.

These tests read the sources rather than restating them, so the next bump has to move
them together. One of them reads the *installed* mcrit's own metadata, so the claim
above is checked against mcrit rather than merely repeated here.
"""

import pathlib
import re
from importlib import metadata

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOOR = (3, 11)


def _version(text):
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def _read(*parts):
    path = ROOT.joinpath(*parts)
    assert path.is_file(), f"{path.relative_to(ROOT)} is gone - this test names the wrong file now"
    return path.read_text(encoding="utf8")


def test_setup_py_declares_the_floor():
    # tolerant of quoting and of an upper bound: mcrit itself shipped ">=3.11,<3.13"
    # once, and a version of that here must read as declared-but-different rather than
    # as not-declared-at-all, which is the more misleading failure.
    declaration = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", _read("setup.py"))
    assert declaration, "setup.py declares no python_requires, so pip cannot say what is wrong"

    lower_bound = re.search(r">=\s*([\d.]+)", declaration.group(1))
    assert lower_bound, f"python_requires={declaration.group(1)!r} states no lower bound"
    assert _version(lower_bound.group(1)) == FLOOR


def test_the_readme_does_not_promise_a_version_that_cannot_work():
    promises = re.findall(r"Python (\d+\.\d+)\+", _read("README.md"))

    assert promises, "the README no longer states a Python version at all"
    for promised in promises:
        assert _version(promised) >= FLOOR, \
            f"README offers Python {promised}+, which cannot install mcrit"


def test_the_contributor_guide_agrees_with_the_readme():
    """AGENTS.md described the discrepancy while it existed. It has to stop saying so
    once it is fixed, or the next reader re-opens a closed question."""
    guide = _read("AGENTS.md")
    stale = re.findall(r"README states Python (\d+\.\d+)\+", guide)

    for claim in stale:
        assert _version(claim) >= FLOOR, \
            f"AGENTS.md still reports the README as saying Python {claim}+"


def test_ruff_lints_against_the_version_that_is_supported():
    target = re.search(r"target-version\s*=\s*['\"]py(\d)(\d+)['\"]", _read("ruff.toml"))
    assert target, "ruff.toml sets no target-version"
    assert (int(target.group(1)), int(target.group(2))) == FLOOR


def test_ci_runs_the_oldest_version_that_is_promised():
    """A floor nothing tests is a guess: the matrix is the only evidence that the oldest
    supported version actually works.

    Asserts the floor appears in *some* matrix rather than that every matrix starts
    there - a later job that exercises only the newest Python is a reasonable thing to
    add, and it should not fail a test about the README.

    Read with a regex rather than a YAML parser: pyyaml is not a dependency here, and
    taking one on so a test can read one line would be a poor trade. Both list styles
    are matched so a reformat of the workflow does not read as "no matrix at all".
    """
    workflow = _read(".github", "workflows", "test.yml")
    inline = re.findall(r"python-version:\s*\[([^\]]+)\]", workflow)
    block = re.findall(r"python-version:\s*\n((?:\s*-\s*['\"]?[\d.]+['\"]?\s*\n)+)", workflow)

    tested = {_version(v) for entry in inline + block for v in re.findall(r"\d+\.\d+", entry)}
    assert tested, "no job pins a python-version matrix"
    assert min(tested) == FLOOR, f"CI's oldest Python is {min(tested)}, the declared floor is {FLOOR}"


def test_the_installed_mcrit_is_what_imposes_the_floor():
    """The evidence for the floor, read from mcrit rather than asserted here.

    This is the only test in the file that looks outside this repository, and it is the
    one that makes the others more than a self-consistent story: if a future mcrit
    lowered its own floor, this would fail and the whole question would be reopened
    deliberately rather than by drift.
    """
    requires = metadata.metadata("mcrit").get("Requires-Python")
    assert requires, "the installed mcrit declares no Requires-Python"

    lower_bound = re.search(r">=\s*([\d.]+)", requires)
    assert lower_bound, f"mcrit's Requires-Python is {requires!r}, with no lower bound to inherit"
    assert _version(lower_bound.group(1)) == FLOOR, \
        f"mcrit now requires {requires!r}; this repository's floor was inherited from it"


def test_the_mcrit_pin_stays_above_the_first_release_that_declared_the_floor():
    """v1.5.0 is the first mcrit release to declare `>=3.11` (v1.4.3 and earlier declare
    nothing). A pin below that would allow a release with no floor at all, and the
    reasoning in this file's docstring would stop being true."""
    pin = re.search(r"['\"]mcrit>=([\d.]+)['\"]", _read("setup.py"))

    assert pin, "the mcrit pin moved; re-check what Python the oldest allowed release needs"
    assert _version(pin.group(1)) >= (1, 5, 0)


if __name__ == "__main__":
    pytest.main([__file__])
