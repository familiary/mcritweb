#!/usr/bin/python
"""BoundedMemo: the least-recently-used store the result and comparison pages memoize in."""

import threading

from flask import Flask

from mcritweb.views.memo import BoundedMemo, app_memo, clear_app_memos


def test_a_miss_computes_and_a_hit_does_not():
    memo = BoundedMemo(2)
    calls = []
    assert memo.get("a", lambda: calls.append(1) or "A") == "A"
    assert memo.get("a", lambda: calls.append(1) or "B") == "A"
    assert calls == [1]


def test_the_least_recently_used_entry_is_evicted_first():
    memo = BoundedMemo(2)
    memo.store("a", 1)
    memo.store("b", 2)
    memo.lookup("a")
    memo.store("c", 3)
    assert memo.lookup("b") is None
    assert memo.lookup("a") == 1
    assert memo.lookup("c") == 3
    assert len(memo) == 2


def test_the_bound_can_be_a_weight():
    memo = BoundedMemo(10, weigh=len)
    memo.store("a", "x" * 6)
    memo.store("b", "x" * 6)
    assert memo.lookup("a") is None
    assert memo.lookup("b") == "x" * 6
    memo.store("huge", "x" * 11)
    assert memo.lookup("huge") is None
    assert memo.lookup("b") == "x" * 6


def test_storing_a_key_again_replaces_its_weight():
    memo = BoundedMemo(10, weigh=len)
    memo.store("a", "x" * 6)
    memo.store("a", "x" * 3)
    memo.store("b", "x" * 7)
    assert memo.lookup("a") == "x" * 3
    assert memo.lookup("b") == "x" * 7


def test_lookup_tells_a_stored_none_from_a_miss():
    memo = BoundedMemo(2)
    missing = object()
    memo.store("a", None)
    assert memo.lookup("a", missing) is None
    assert memo.lookup("b", missing) is missing


def test_the_bound_holds_under_concurrent_stores():
    memo = BoundedMemo(16)

    def store_many(offset):
        for index in range(500):
            memo.store((offset, index), index)
            memo.lookup((offset, index // 2))

    threads = [threading.Thread(target=store_many, args=(offset,)) for offset in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(memo) == 16


def test_each_app_has_its_own_memos_and_they_can_all_be_cleared():
    first, second = Flask("first"), Flask("second")
    app_memo(first, "results", 4).store("a", 1)
    assert app_memo(second, "results", 4).lookup("a") is None
    assert app_memo(first, "results", 4) is app_memo(first, "results", 4)
    app_memo(first, "other", 4).store("b", 2)
    clear_app_memos(first)
    assert app_memo(first, "results", 4).lookup("a") is None
    assert app_memo(first, "other", 4).lookup("b") is None
