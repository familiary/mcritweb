#!/usr/bin/python
"""The match tables the result pages build from `templates/table/match_row.html`.

Jinja renders a macro called with the wrong number of arguments as nothing at all,
rather than raising. A table that lost its cells that way still answers 200 and still
says everything the page's headings say, so these tests look at the tables themselves:
their shape, the markup of their cells, and the values passed into them.

Every page is rendered from the captured reports in tests/fixtures/, through the real
dispatch in `data.result()`.
"""

import re
from html.parser import HTMLParser

import pytest
from fixtureData import job_id_of
from testResultPages import CorpusWithEveryMatchedFunction

from mcritweb.db import UserColumnSettings


@pytest.fixture
def fake_mcrit():
    """The captured corpus with every matched function resolvable, so that the
    `?samid=` and `?funid=` views render instead of landing on result_corrupted.html."""
    return CorpusWithEveryMatchedFunction()


class _MatchTableAudit(HTMLParser):
    """Measures every table on a rendered page: its cells, and how wide it is.

    `columns` weights the header cells by `colspan`, because `famlib_header` covers the
    two direct-score columns with one `Direct` header and the two frequency ones with
    one `Frequency`. That makes it the number of body cells a row owes, which is what
    the width check compares against.

    `unclosed` lists the `<i>` elements a cell opened and did not close before the cell
    ended. The icons in these cells are empty `<i>` elements, so one left open swallows
    whatever the cell says after it.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._open = []
        self._cell = None
        self._header_text = None
        self.tables = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            self._open.append({"match_rows": 0, "th": 0, "td": 0, "columns": 0, "headers": [], "unclosed": []})
        elif self._open:
            table = self._open[-1]
            if tag == "tr" and "background-color" in (attributes.get("style") or ""):
                # every match row carries its score colour; the surrounding widget
                # tables (input samples, method statistics, pagination) do not
                table["match_rows"] += 1
            elif tag in ("th", "td"):
                table[tag] += 1
                if tag == "th":
                    colspan = attributes.get("colspan") or "1"
                    table["columns"] += int(colspan) if colspan.isdigit() else 1
                    self._header_text = []
                self._cell = {"tag": tag, "open_i": 0}
            elif tag == "i" and self._cell is not None:
                self._cell["open_i"] += 1

    def handle_data(self, data):
        if self._header_text is not None:
            self._header_text.append(data)

    def handle_endtag(self, tag):
        if tag == "i" and self._cell is not None:
            self._cell["open_i"] -= 1
        elif tag in ("th", "td") and self._open:
            if self._cell is not None and self._cell["open_i"] > 0:
                self._open[-1]["unclosed"].append(tag)
            self._cell = None
            if tag == "th" and self._header_text is not None:
                self._open[-1]["headers"].append("".join(self._header_text).strip())
                self._header_text = None
        elif tag == "table" and self._open:
            self.tables.append(self._open.pop())


def match_tables(markup):
    """Every table in `markup` that holds match rows."""
    audit = _MatchTableAudit()
    audit.feed(markup)
    audit.close()
    return [table for table in audit.tables if table["match_rows"]]


def assert_match_tables_are_rectangular(response, context):
    """Every match table on the page has header cells, body cells, and as many body
    cells as its header has columns, times its rows.

    The width check catches a column on one side of a table and not the other, which is
    the shape an arity slip in a `match_row.html` call takes. Counted per table rather
    than per row because `result_compare_all.html`'s library table opens its row with
    `<tr ...></tr>`, so its cells sit outside the row element. That markup predates the
    macros and is kept.
    """
    tables = match_tables(response.get_data(as_text=True))
    assert tables, f"{context} rendered no match table at all"
    for table in tables:
        assert table["th"], f"{context} rendered a match table with no header cells"
        assert table["td"], f"{context} rendered a match table with no body cells"
        assert table["td"] == table["columns"] * table["match_rows"], (
            f"{context} rendered a match table {table['columns']} columns wide with "
            f"{table['td']} cells over {table['match_rows']} rows"
        )


#: The reports whose result page carries match tables. Cross compare and unique blocks
#: render templates of their own.
REPORTS_WITH_MATCH_TABLES = ("matches_for_sample", "matches_for_sample_vs", "matches_for_query")

#: (query parameter, the heading of the view it reaches). `result_compare_family.html`,
#: `result_compare_sample.html` and `result_compare_function.html` are reachable only
#: through these filters.
FILTERED_VIEWS = [
    ("famid", "All Matches in Family"),
    ("samid", "Sample Match Statistics"),
    ("funid", "Matches for Function"),
]

#: How many ids to try per report. Every attempt renders a whole report.
CANDIDATES_PER_REPORT = 3


def result_pages(client):
    """(context, response) for every report's own page."""
    for report in REPORTS_WITH_MATCH_TABLES:
        response = client.get(f"/data/result/{job_id_of(report)}")
        assert response.status_code == 200, f"{report} did not render"
        yield report, response


def filtered_views(client, param, heading):
    """Every (context, response) among the candidate ids that reaches the `param` view.

    The ids come off the rendered overview rather than being written out here: they are
    properties of the captured corpus, and regenerating the fixtures would leave written
    out ones pointing at nothing while the test still passed.
    """
    pattern = re.compile(r"[?&]" + param + r"=(-?\d+)")
    for report, overview in result_pages(client):
        job_id = job_id_of(report)
        for value in list(dict.fromkeys(pattern.findall(overview.get_data(as_text=True))))[:CANDIDATES_PER_REPORT]:
            response = client.get(f"/data/result/{job_id}?{param}={value}")
            assert response.status_code == 200, f"{report} ?{param}={value} errored"
            if heading in response.get_data(as_text=True):
                yield f"{report} ?{param}={value}", response


def every_view(client):
    yield from result_pages(client)
    for param, heading in FILTERED_VIEWS:
        yield from filtered_views(client, param, heading)


def test_every_match_table_is_rectangular(client, as_role):
    as_role("visitor")
    seen = set()
    for context, response in every_view(client):
        assert_match_tables_are_rectangular(response, context)
        seen.add(context.split(" ?")[-1].split("=")[0] if "?" in context else "overview")

    assert seen == {"overview", "famid", "samid", "funid"}, f"only reached the views {sorted(seen)}"


def test_no_icon_in_a_match_cell_is_left_open(client, as_role):
    """The library and unique cells of the aggregated function table used to close
    their `<i>` only on the green branch, leaving it open on the red one."""
    as_role("visitor")
    for context, response in every_view(client):
        for table in match_tables(response.get_data(as_text=True)):
            assert "td" not in table["unclosed"], f"{context} leaves an <i> open in a body cell"


def function_match_table(response, context):
    """The one match table below the `function-matches` heading."""
    markup = response.get_data(as_text=True)
    _before, anchor, after = markup.partition('id="function-matches"')
    assert anchor, f"{context} has no function-matches heading"
    tables = match_tables(after)
    assert len(tables) == 1, f"{context} has {len(tables)} match tables below the heading, expected one"
    return tables[0]


def test_the_one_vs_one_page_leaves_out_the_column_it_cannot_answer(client, as_role):
    """`data.result()` hands `result_compare_vs.html` and `result_compare_sample.html`
    the same stored column list, `result_function_sample_filtered_table`, and the vs
    page renders one column fewer on purpose: a match against a single other sample says
    nothing about whether a function is unique across the corpus.

    Dropping `unique_match_known=False` from both vs call sites keeps the table
    rectangular and grows it by a real column - a second `Uniq` header and a cell on
    every row - which no shape check can see. So the two pages are compared.
    """
    as_role("visitor")
    stored_columns = UserColumnSettings._default_settings["result_function_sample_filtered_table"]["active"]

    versus = client.get(f"/data/result/{job_id_of('matches_for_sample_vs')}")
    filtered = list(filtered_views(client, "samid", "Sample Match Statistics"))

    assert versus.status_code == 200
    assert filtered, "no captured report reached the sample-filtered view to compare against"
    versus_headers = function_match_table(versus, "the 1-vs-1 page")["headers"]
    for context, response in filtered:
        filtered_headers = function_match_table(response, context)["headers"]
        assert all(filtered_headers), f"{context} rendered a nameless function column"
        assert len(filtered_headers) == len(stored_columns), (
            f"{context} rendered {len(filtered_headers)} of the {len(stored_columns)} stored columns"
        )
        dropped = [header for header in filtered_headers if header not in versus_headers]
        assert dropped == ["Uniq"], f"the 1-vs-1 page should leave out Uniq of {filtered_headers}, it left out {dropped}"
        assert versus_headers == [header for header in filtered_headers if header != "Uniq"]


#: Sentinel totals for the score tooltips, unlike any total the captured reports have.
SENTINEL_DIVISORS = {"matchable": 4321.5, "nonlibrary": 1234.25}


def test_the_score_tooltips_divide_by_the_page_s_divisors(client, as_role, monkeypatch):
    """`famlib_row` takes the divisors as a parameter, since a macro does not see the
    page's context. Called without them, or with something else in their place, the
    tooltips would state a fraction over some other total."""
    import mcritweb.views.data

    monkeypatch.setattr(mcritweb.views.data, "recover_score_divisors", lambda matching_result: dict(SENTINEL_DIVISORS))
    as_role("visitor")
    families = list(filtered_views(client, "famid", "All Matches in Family"))
    samples = list(filtered_views(client, "samid", "Sample Match Statistics"))
    assert families and samples
    for context, response in [*result_pages(client), *families, *samples]:
        page = response.get_data(as_text=True)
        if not match_tables(page.partition('id="function-matches"')[0]):
            continue
        assert "Weighted Direct Score: &#10;Bytes:" in page, f"{context} has no direct score tooltip"
        tooltips = re.findall(r'data-hint="([^"]*Score[^"]*)"', page)
        for tooltip in tooltips:
            if "Library Excluded" in tooltip:
                assert "/ 1234.25 " in tooltip, f"{context}: {tooltip}"
            elif "Unique Matching" not in tooltip:
                assert "/ 4321.50 " in tooltip, f"{context}: {tooltip}"
