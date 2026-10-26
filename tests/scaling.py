"""Timing for the tests that check how a cost grows with input size.

Absolute times depend on the machine, its load and coverage instrumentation,
so these tests compare the cost of a small and a large input. The cost is the
CPU time of the calling thread (time.thread_time), which does not grow while
other processes hold the CPU, as wall-clock time does. Each size is timed
several times, alternating small and large, and the fastest run of each
counts, so cache or frequency effects of a burst of load on one run do not
change the ratio.
"""

import gc
import time
from collections.abc import Callable


def best_times(
    small: Callable[[], object], large: Callable[[], object], rounds: int = 5
) -> tuple[float, float]:
    """Return the fastest of ``rounds`` timings of ``small`` and of ``large``.

    The two calls alternate within each round. Garbage collection is paused
    while a call is timed, so a collection triggered by earlier allocations
    is not charged to it.
    """
    best_small = best_large = float("inf")
    for _ in range(rounds):
        best_small = min(best_small, _time(small))
        best_large = min(best_large, _time(large))
    return best_small, best_large


def _time(call: Callable[[], object]) -> float:
    gc.collect()
    gc.disable()
    try:
        start = time.thread_time()
        call()
        return time.thread_time() - start
    finally:
        gc.enable()
