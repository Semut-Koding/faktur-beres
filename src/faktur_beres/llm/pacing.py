"""Time for the LLM client: a clock tests replace, the per-model throttle, and backoff.

Durations are integer nanoseconds or milliseconds. Only the real clock turns them into seconds, at
the ``time.sleep`` call.
"""

import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

NS_PER_MS = 1_000_000
NS_PER_MINUTE = 60_000 * NS_PER_MS
DEFAULT_RPM = 5  # the safe limit when no number is configured (M3.2)

BACKOFF_BASE_MS = 2_000
BACKOFF_CAP_MS = 60_000
JITTER_MS = 1_000


class Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    def sleep_ns(self, ns: int) -> None: ...

    def now(self) -> datetime:
        """Wall-clock time in UTC, for the audit."""
        ...


class SystemClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def sleep_ns(self, ns: int) -> None:
        if ns > 0:
            time.sleep(ns / 1_000_000_000)

    def now(self) -> datetime:
        return datetime.now(UTC)


class Throttle:
    """Keeps at least 60/RPM seconds between the starts of two requests to the same model."""

    def __init__(self, rpm: Mapping[str, int], clock: Clock) -> None:
        if any(per_minute < 1 for per_minute in rpm.values()):
            raise ValueError("RPM harus minimal 1")
        self._gap_ns = {model: _gap(per_minute) for model, per_minute in rpm.items()}
        self._last_start: dict[str, int] = {}
        self._clock = clock

    def gap_ns(self, model: str) -> int:
        return self._gap_ns.get(model, _gap(DEFAULT_RPM))

    def wait(self, model: str) -> int:
        """Sleep until ``model`` may be called again, mark the start; return the ns waited."""
        waited = 0
        last = self._last_start.get(model)
        if last is not None:
            ready = last + self.gap_ns(model)
            now = self._clock.monotonic_ns()
            if now < ready:
                waited = ready - now
                self._clock.sleep_ns(waited)
        self._last_start[model] = self._clock.monotonic_ns()
        return waited


def _gap(per_minute: int) -> int:
    return -(-NS_PER_MINUTE // per_minute)  # rounded up


def backoff_ms(retry: int, rng: random.Random) -> int:
    """Wait before retry number ``retry`` (1, 2, 3): 2 s, 4 s, 8 s, ... up to 60 s, plus 0–1 s."""
    doubled = BACKOFF_BASE_MS << max(retry - 1, 0)
    return min(BACKOFF_CAP_MS, doubled) + rng.randint(0, JITTER_MS)


def jitter_ms(rng: random.Random) -> int:
    return rng.randint(0, JITTER_MS)
