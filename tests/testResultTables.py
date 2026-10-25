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


class _TableRows(HTMLParser):
    """The header texts and the text of every body cell of the first table fed to it.
    Cells are collected per table rather than per `<tr>`, as in `_MatchTableAudit`, and
    cut into rows by the header width."""

    def __init__(self):
        super().__init__()
        self.headers, self.cells = [], []
        self._depth, self._done, self._cell, self._header = 0, False, None, None

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth == 1 and tag == "th":
            self._header = []
            # a header spanning two columns owns two cells per row; the second one has
            # no header of its own and is named after the first with a "+"
            self._span = int(dict(attrs).get("colspan") or 1)
        elif self._depth == 1 and tag == "td":
            self._cell = []

    def handle_data(self, data):
        for bucket in (self._header, self._cell):
            if bucket is not None:
                bucket.append(data)

    def handle_endtag(self, tag):
        if self._done:
            return
        if tag == "table":
            self._depth -= 1
            self._done = self._depth == 0
        elif tag == "th" and self._header is not None:
            name = " ".join("".join(self._header).split())
            self.headers.append(name)
            self.headers.extend([name + "+"] * (self._span - 1))
            self._header = None
        elif tag == "td" and self._cell is not None:
            self.cells.append(" ".join("".join(self._cell).split()))
            self._cell = None


def column(response, anchor, header):
    """One column of the match table below `id="<anchor>"`, top to bottom."""
    markup = response.get_data(as_text=True).partition(f'id="{anchor}"')[2]
    parser = _TableRows()
    parser.feed(markup[markup.index("<table"):])
    width = len(parser.headers)
    assert width and len(parser.cells) % width == 0, f"the table below {anchor} is not rectangular"
    index = parser.headers.index(header)
    return [parser.cells[row + index] for row in range(0, len(parser.cells), width)]


def offsets(response, anchor="function-matches", header="Offset"):
    return [int(value, 16) for value in column(response, anchor, header)]


def test_no_icon_in_a_match_header_is_left_open(client, as_role):
    """The family and sample headers opened their icon's `<i>` and never closed it."""
    as_role("visitor")
    for context, response in every_view(client):
        for table in match_tables(response.get_data(as_text=True)):
            assert "th" not in table["unclosed"], f"{context} leaves an <i> open in a header cell"


def test_a_sorted_function_table_is_sorted_across_its_pages(client, as_role):
    """The aggregated function table is paginated by the server. A sort that ran on the
    page would order each page by itself; this one has to order the list, so page two
    starts where page one ended and the pages together are the sorted list."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")

    def rows(query):
        response = client.get(f"/data/result/{job_id}?{query}")
        sizes = [int(value) for value in column(response, "function-matches", "Bytes")]
        return list(zip(sizes, column(response, "function-matches", "Function ID")))

    unsorted = [row for page in range(1, 5) for row in rows(f"funl=250&funp={page}")]
    assert [size for size, _ in unsorted] != sorted(size for size, _ in unsorted), (
        "the captured report is in size order already, so this proves nothing"
    )

    ascending = [row for page in range(1, 5) for row in rows(f"funl=250&funp={page}&funpsort=num_bytes&funpasc=true")]
    assert sorted(function_id for _, function_id in ascending) == sorted(function_id for _, function_id in unsorted), (
        "the sorted pages do not hold every row exactly once"
    )
    assert [size for size, _ in ascending] == sorted(size for size, _ in unsorted)

    descending = rows("funl=10&funp=2&funpsort=num_bytes&funpasc=false")
    assert [size for size, _ in descending] == sorted((size for size, _ in unsorted), reverse=True)[10:20]


def test_an_unknown_sort_column_leaves_the_page_as_it_is(client, as_role):
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    plain = client.get(f"/data/result/{job_id}?funl=10&funp=2")
    bogus = client.get(f"/data/result/{job_id}?funl=10&funp=2&funpsort=no_such_column&funpasc=false")
    assert offsets(bogus) == offsets(plain)


def test_an_unsorted_page_aggregates_only_its_own_functions(client, as_role, monkeypatch):
    """Without a sort the aggregated table keeps #194's page-only aggregation; only a
    sort, which has to see every row, aggregates the whole report."""
    from mcrit.storage.MatchingResult import MatchingResult

    aggregated = []
    original = MatchingResult.getAggregatedFunctionMatches

    def spy(self, *args, **kwargs):
        rows = original(self, *args, **kwargs)
        aggregated.append(len(rows))
        return rows

    monkeypatch.setattr(MatchingResult, "getAggregatedFunctionMatches", spy)
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")

    client.get(f"/data/result/{job_id}?funl=10&funp=2")
    assert aggregated and max(aggregated) <= 10, f"an unsorted page aggregated {aggregated} functions"

    aggregated.clear()
    client.get(f"/data/result/{job_id}?funl=10&funp=2&funpsort=offset")
    assert max(aggregated) > 10, "a sorted page did not see the whole report"


def test_the_family_table_sorts_on_its_own(client, as_role):
    """`result_compare_all.html` has three paginated tables; each sorts through its own
    parameters, so sorting the families leaves the function table alone."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    plain = client.get(f"/data/result/{job_id}")
    ascending = client.get(f"/data/result/{job_id}?fampsort=family_name&fampasc=true")
    descending = client.get(f"/data/result/{job_id}?fampsort=family_name&fampasc=false")

    names = column(ascending, "family-matches", "Family")
    assert len(names) > 1
    assert [name.lower() for name in names] == sorted(name.lower() for name in names)
    assert column(descending, "family-matches", "Family") == names[::-1]
    assert offsets(ascending) == offsets(plain)


def test_a_sort_link_starts_again_at_page_one_and_keeps_the_rest(client, as_role):
    """The sort link of a header goes back to page 1 - the row at the top of page 3 is
    somewhere else once the order changes - and keeps the other tables' state."""
    as_role("visitor")
    job_id = job_id_of("matches_for_sample")
    page = client.get(f"/data/result/{job_id}?famp=1&funl=10&funp=3&filter_function_min_score=50").get_data(as_text=True)
    links = re.findall(r'class="table-sort" href="([^"]*funpsort=offset[^"]*)"', page)
    assert links, "the offset header carries no sort link"
    link = links[0].replace("&amp;", "&")
    assert "funp=1" in link and "funp=3" not in link
    assert "funpasc=true" in link and "funl=10" in link
    assert "#function-matches" in link
