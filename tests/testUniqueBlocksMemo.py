#!/usr/bin/python
"""The Unique Blocks page stops rebuilding the block cover on every render - issue #184.

The cover behind the YARA rule is a pure function of a finished job's report and the
rule parameters, and it is the expensive part of the page, yet it used to be rebuilt
for each page of the block table and each reload. It is now memoized per job and
parameter set, and what these tests pin is that this changes the cost of a render and
nothing about its output: a changed parameter is a different entry, the rule carries
today's date, and the first render of a report fetched from the backend is the same as
every later render of its cached copy - which it was not before, because the two
iterated the report in different key orders.
"""

import datetime
import logging
import re
import types
import unittest

import pytest
from fixtureData import job_id_of, load
from mcrit.storage import UniqueBlocksResult as unique_blocks_module
from mcrit.storage.UniqueBlocksResult import UniqueBlocksResult

from mcritweb.views import data
from mcritweb.views.memo import app_memo

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)


@pytest.fixture
def fake_mcrit(corpus_mcrit):
    return corpus_mcrit


@pytest.fixture
def covers(monkeypatch):
    """Every generateBlockCover call made from here on, as its keyword arguments."""
    calls = []
    original = UniqueBlocksResult.generateBlockCover

    def counting(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(UniqueBlocksResult, "generateBlockCover", counting)
    return calls


@pytest.fixture
def result_url(client, as_role):
    as_role("visitor")
    url = f"/data/result/{job_id_of('unique_blocks')}"
    client.get(url)
    return url


def cover_memo(app):
    return app_memo(app, "unique_blocks_cover", data.YARA_COVER_MEMO_ENTRIES)


def rule_of(response):
    body = response.data.decode()
    start = body.index("rule mcrit_")
    return body[start:body.index("</textarea>", start)]


def block_rows(response):
    """The pichashes of the block table, in the order they were rendered."""
    return re.findall(r'<td valign="middle" scope="row" class="id">(0x[0-9a-f]+)</td>', response.data.decode())


def test_paging_and_reloading_build_the_cover_once(client, covers, result_url):
    for query in ["", "?tab=blocks", "?tab=blocks&blkp=2", "?tab=yara", "?tab=blocks&min_score=50", ""]:
        assert client.get(result_url + query).status_code == 200

    assert len(covers) == 1, f"{len(covers)} block covers for one set of rule parameters"


@pytest.mark.parametrize(
    "name, value",
    [("min_ins", 30), ("max_ins", 5), ("min_bytes", 64), ("max_bytes", 20), ("required_per_sample", 2)],
)
def test_every_cover_parameter_is_part_of_the_key(client, covers, result_url, name, value):
    default = client.get(result_url)
    changed = client.get(f"{result_url}?{name}={value}")
    default_again = client.get(result_url)

    assert changed.status_code == 200
    assert [call[name] for call in covers] == [data.YARA_RULE_DEFAULTS[name], value], "served a cover built for other parameters"
    assert rule_of(default_again) == rule_of(default)


def test_a_changed_parameter_changes_the_rule(client, result_url):
    default = client.get(result_url)
    bounded = client.get(f"{result_url}?min_ins=30")

    assert rule_of(default) != rule_of(bounded)


def test_the_condition_shares_the_cover_but_still_changes_the_rule(client, covers, result_url):
    """condition_required is applied when the rule is rendered, not by the cover."""
    default = client.get(result_url)
    narrowed = client.get(f"{result_url}?condition_required=3")

    assert "3 of them" in rule_of(narrowed)
    assert "3 of them" not in rule_of(default)
    assert len(covers) == 1


def test_the_stored_cover_cannot_be_changed_by_a_request(client, result_url, monkeypatch):
    """Every request asking for these parameters is handed the same cover."""
    used = []
    original = data.build_yara_rule

    def recording(*args, **kwargs):
        rule, cover = original(*args, **kwargs)
        used.append(cover)
        return rule, cover

    monkeypatch.setattr(data, "build_yara_rule", recording)
    client.get(result_url)
    client.get(f"{result_url}?tab=blocks&blkp=2")

    assert used[0] is used[1], "the second render did not reuse the cover"
    with pytest.raises(TypeError):
        used[0]["has_rule"] = False
    with pytest.raises(AttributeError):
        used[0]["block_hashes"].append("0x0")


def test_the_rule_carries_the_date_it_was_rendered_on(client, result_url, monkeypatch):
    """renderRule stamps the date, so the rule itself is rendered every time - a
    memoized one would be copied out with the day it was first built on."""

    class Tomorrow(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2099, 1, 2, tzinfo=tz)

    client.get(result_url)
    # only the name mcrit's module reads, not datetime.datetime for the whole process
    monkeypatch.setattr(unique_blocks_module, "datetime", types.SimpleNamespace(datetime=Tomorrow, UTC=datetime.UTC))
    response = client.get(result_url)

    assert "date = &#34;2099-01-02&#34;" in rule_of(response)


def test_the_first_render_of_a_fetched_report_is_the_same_as_every_later_one(client, as_role):
    """The first render used to iterate the report in the backend's key order and every
    later one the cached copy's sorted order. The cover and the ties in the block table
    follow that order, so the first view of a job could show a different rule than
    every view after it - and a memoized cover would have kept that first answer."""
    as_role("visitor")
    url = f"/data/result/{job_id_of('unique_blocks')}?tab=blocks&min_ins=4"

    renders = [client.get(url) for _ in range(3)]

    assert rule_of(renders[0]) == rule_of(renders[1]) == rule_of(renders[2])
    assert block_rows(renders[0]) == block_rows(renders[1]) == block_rows(renders[2])


def test_the_block_table_is_ordered_by_score_then_pichash(client, result_url):
    response = client.get(f"{result_url}?tab=blocks&min_score=50&min_block_length=5")

    blocks = load("unique_blocks.result")["unique_blocks"]
    filtered = [(pichash, block) for pichash, block in blocks.items() if block["score"] >= 50 and block["length"] >= 5]
    expected = [pichash for pichash, _ in sorted(filtered, key=lambda x: (-x[1]["score"], x[0]))][:100]
    assert block_rows(response) == expected


def test_the_route_keeps_the_memo_bounded(app, client, result_url):
    for required in range(1, 2 * data.YARA_COVER_MEMO_ENTRIES):
        client.get(f"{result_url}?required_per_sample={required}")

    assert len(cover_memo(app)) == data.YARA_COVER_MEMO_ENTRIES


def test_a_backend_reset_forgets_the_covers(app, client, as_role, fake_mcrit, monkeypatch):
    """A reset restarts the backend's job ids, so a cover memoized by job id is stale."""
    as_role("visitor")
    client.get(f"/data/result/{job_id_of('unique_blocks')}")
    assert len(cover_memo(app)) == 1

    monkeypatch.setattr(fake_mcrit, "respawn", lambda: None, raising=False)
    as_role("admin")
    response = client.post("/admin/reset_server", data={"reset_server": "RESET"})

    assert response.status_code == 302
    assert len(cover_memo(app)) == 0


if __name__ == "__main__":
    unittest.main()
