#!/usr/bin/python
"""Nothing on a request path writes to stdout.

Two prints sat on routes a reader hits in normal use. `data.match_functions` printed the
entire 1-vs-1 match report - both function entries, minhashes and picblockhashes
included - on every `/data/matches/function/<a>/<b>` view. `MatchReportRenderer`'s
`processReport`, which `data.py` calls to draw the match diagram, printed a line of
statistics on every result page that carries one.

Neither is a small thing at production scale: under gunicorn stdout is the container
log, so this is unbounded volume, per request, of data the operator did not ask for and
in one case would rather not have archived.

The prints in `db.py` are deliberate and stay: migrations run once, at startup, before a
logger is configured, and the operator needs to see them.
"""

import ast
import pathlib

import pytest
from fixtureData import job_id_of

VIEWS = pathlib.Path(__file__).resolve().parent.parent / "mcritweb" / "views"


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    """The diagram is only drawn for a report that has one, so this needs the
    captured corpus rather than the strict fake's empty shapes."""
    return corpus_mcrit

# MatchReportRenderer doubles as a command line tool - `python MatchReportRenderer.py`
# renders a report to the terminal. Printing is the entire point of all four, and
# none of them is reachable from a view: `printInfo` is called only by `main`, and
# `renderText` and `_getSampleMatchScores` only by `printInfo`. setup.py declares no
# console_scripts, so this half of the module is dead in a deployment and alive only for
# whoever is debugging one.
PRINTS_ON_PURPOSE = {
    "MatchReportRenderer.py": {"main", "printInfo", "renderText", "_getSampleMatchScores"},
}


def _enclosing_function(tree, node):
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if candidate.lineno <= node.lineno <= (candidate.end_lineno or node.lineno):
                yield candidate.name


@pytest.mark.parametrize("module", sorted(VIEWS.glob("*.py")), ids=lambda p: p.name)
def test_no_view_module_prints(module):
    tree = ast.parse(module.read_text(encoding="utf8"))
    allowed = PRINTS_ON_PURPOSE.get(module.name, set())

    offenders = [
        f"{module.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        and not (allowed & set(_enclosing_function(tree, node)))
    ]

    assert not offenders, f"print() on a request path: {', '.join(offenders)}"


def test_rendering_a_result_page_writes_nothing_to_stdout(client, as_role, capsys):
    """The behavioural half. The static check above cannot see a print that arrives
    through a helper, and this route is the one that actually regressed."""
    as_role("visitor")
    capsys.readouterr()

    response = client.get(f"/data/result/{job_id_of('matches_for_sample')}")

    assert response.status_code == 200
    assert capsys.readouterr().out == "", "a page view should not narrate itself to stdout"
