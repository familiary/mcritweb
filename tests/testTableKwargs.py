#!/usr/bin/python
"""Every table macro has to accept the kwargs its callers pass through.

`_table_base` (templates/table/table.html) forwards the caller's `**kwargs` to *both*
the row macro and the header macro. A Jinja macro that never mentions `kwargs` rejects
an unexpected keyword argument outright, so a caller who passes anything the header does
not declare gets

    TypeError: macro 'function_header' takes no keyword argument '...'

and the whole page 500s. `sample_header` and `family_header` already carry the two-line
absorber for exactly this - somebody hit it before - which is what makes the other four
an inconsistency rather than a design choice.

This is the mechanical half of issue #53, which asks for row rendering to be steerable
by data the caller passes in. That mechanism cannot be built on macros that reject the
data, and #56's marking half and #45 both go through here. What the convention should
*look like* is a design decision and is deliberately not made here.
"""

import logging
import unittest

import pytest

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


#: macro -> (fixture attribute holding rows, extra kwargs the macro genuinely needs)
TABLES = {
    "family_table": ("_families", {}),
    "sample_table": ("_samples", {}),
    "function_table": ("_functions", {}),
    "minisample_table": ("_samples", {}),
    "job_table": (None, {"families_by_id": {}, "samples_by_id": {}}),
    "minijob_table": (None, {"families_by_id": {}, "samples_by_id": {}}),
}


def rows_for(backend, attribute):
    if attribute is None:
        return backend.getQueueData()[:1]
    return list(getattr(backend, attribute).values())[:1]


@pytest.mark.parametrize("macro", sorted(TABLES))
def test_a_table_macro_tolerates_a_kwarg_it_does_not_know(app, fake_mcrit, macro):
    """The header runs only when the table has rows, so an empty table hides this."""
    attribute, extra = TABLES[macro]
    rows = rows_for(fake_mcrit, attribute)
    assert rows, f"no fixture rows for {macro}"

    declared = ", ".join(f"{name}={name}" for name in extra)
    source = "{%% from 'table/table.html' import %s %%}{{ %s(rows, %s some_future_kwarg='x') }}" % (
        macro, macro, (declared + ", ") if declared else "")

    with app.test_request_context("/"):
        app.jinja_env.from_string(source).render(rows=rows, **extra)


@pytest.mark.parametrize("macro", sorted(TABLES))
def test_the_ordinary_call_still_renders(app, fake_mcrit, macro):
    """The absorber must not change what a caller passing nothing extra gets."""
    attribute, extra = TABLES[macro]
    rows = rows_for(fake_mcrit, attribute)

    declared = ", ".join(f"{name}={name}" for name in extra)
    source = "{%% from 'table/table.html' import %s %%}{{ %s(rows%s) }}" % (
        macro, macro, (", " + declared) if declared else "")

    with app.test_request_context("/"):
        rendered = app.jinja_env.from_string(source).render(rows=rows, **extra)

    assert "<table" in rendered
    assert "some_future_kwarg" not in rendered, "the absorber must discard, not print"


def test_every_header_macro_is_covered_by_this_file():
    """A ratchet: a new table macro added to table.html has to appear here, or the
    next caller to pass it something finds out in production."""
    import pathlib
    import re

    source = (pathlib.Path(__file__).parent.parent / "mcritweb" / "templates" / "table" / "table.html").read_text()
    declared = set(re.findall(r"\{%\s*macro\s+(\w+_table)\(", source))

    assert declared - set(TABLES) == set(), f"table macros with no kwargs coverage: {declared - set(TABLES)}"


if __name__ == "__main__":
    unittest.main()
