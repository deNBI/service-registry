"""tests/scaling.py: the timing helper used by the scaling tests."""

import gc

import pytest

from tests import scaling


def test_best_times_returns_the_fastest_thread_cpu_time_of_each_size(monkeypatch):
    clock = iter([0.0, 5.0, 10.0, 30.0, 40.0, 42.0, 50.0, 70.0, 80.0, 83.0, 90.0, 99.0])
    monkeypatch.setattr(scaling.time, "thread_time", lambda: next(clock))
    # small runs take 5, 2, 3 and large runs take 20, 20, 9
    assert scaling.best_times(lambda: None, lambda: None, rounds=3) == (2.0, 9.0)


def test_best_times_alternates_small_and_large():
    calls = []
    scaling.best_times(lambda: calls.append("s"), lambda: calls.append("l"), rounds=3)
    assert calls == ["s", "l", "s", "l", "s", "l"]


def test_garbage_collection_is_paused_only_while_timing():
    seen = []
    scaling.best_times(lambda: seen.append(gc.isenabled()), lambda: None, rounds=2)
    assert (seen, gc.isenabled()) == ([False, False], True)


def test_garbage_collection_is_resumed_when_the_call_raises():
    def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        scaling.best_times(boom, lambda: None)
    assert gc.isenabled() is True
