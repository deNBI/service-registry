"""Timing for the tests that check how a cost grows with input size.

Absolute times depend on the machine, its load and coverage instrumentation,
so these tests compare the cost of a small and a large input. The cost is the
CPU time of the calling thread (time.thread_time), which does not grow while
other processes hold the CPU, as wall-clock time does. Each size is timed
several times, alternating small and large, and the fastest run of each
counts. The small input is run ``small_repeats`` times inside one timing
(the size factor), so both timings last about as long: load from other
processes on the same core (shared caches, hyperthreads) slows CPU time too,
and a short timing would find a quiet moment more often than a long one.

Limits: only work done on the calling thread counts (the code these tests
measure runs there), and the clock's resolution is about 1 microsecond on
Linux but can be about 15 ms on Windows, where small timings are noise.
"""

import gc
import time
from collections.abc import Callable

# The clock; a module attribute so the helper's own tests can replace it.
_clock = time.thread_time


def best_times(
    small: Callable[[], object],
    large: Callable[[], object],
    rounds: int = 5,
    small_repeats: int = 1,
) -> tuple[float, float]:
    """Return the fastest of ``rounds`` timings of ``small`` (per call) and of
    ``large``.

    The two alternate within each round; ``small`` runs ``small_repeats``
    times per timing. Garbage collection is paused while timing, so a
    collection triggered by earlier allocations is not charged to the call.
    """
    best_small = best_large = float("inf")
    for _ in range(rounds):
        best_small = min(best_small, cpu_time(small, small_repeats) / small_repeats)
        best_large = min(best_large, cpu_time(large))
    return best_small, best_large


def cpu_time(call: Callable[[], object], repeats: int = 1) -> float:
    """CPU time of ``repeats`` calls on this thread, with garbage collection
    paused."""
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        start = _clock()
        for _ in range(repeats):
            call()
        return _clock() - start
    finally:
        if was_enabled:
            gc.enable()
