"""One operation per invoice at a time, and the upload rate limit.

The API, its background jobs and the inbox poller share one ``GuardedRunner`` in one process, so a
lock per invoice is enough: a decision, a vendor reply, a retry and the run after an upload never
overlap for the same invoice.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from faktur_beres.db.models import Decision
from faktur_beres.graph.runner import IncomingReply, Runner, Stop


class GuardedRunner(Runner):
    def __init__(self, inner: Runner) -> None:
        super().__init__(inner.store, inner.files, inner.graph)
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._active: dict[str, int] = defaultdict(int)

    def reserve(self, invoice_id: str) -> bool:
        """Claim the invoice for a background job; False when something is already under way."""
        with self._guard:
            if self._active[invoice_id]:
                return False
            self._active[invoice_id] += 1
            return True

    def release(self, invoice_id: str) -> None:
        with self._guard:
            self._active[invoice_id] = max(0, self._active[invoice_id] - 1)

    def busy(self, invoice_id: str) -> bool:
        with self._guard:
            return self._active[invoice_id] > 0

    @contextmanager
    def _held(self, invoice_id: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks[invoice_id]
            self._active[invoice_id] += 1
        try:
            with lock:
                yield
        finally:
            self.release(invoice_id)

    def run(self, invoice_id: str) -> Stop:
        with self._held(invoice_id):
            return super().run(invoice_id)

    def decide(
        self, invoice_id: str, decision: Decision, *, decided_by: str, note: str | None = None
    ) -> Stop:
        with self._held(invoice_id):
            return super().decide(invoice_id, decision, decided_by=decided_by, note=note)

    def reply(self, invoice_id: str, reply: IncomingReply) -> Stop:
        with self._held(invoice_id):
            return super().reply(invoice_id, reply)

    def retry(self, invoice_id: str) -> Stop:
        with self._held(invoice_id):
            return super().retry(invoice_id)


class RateLimit:
    """At most ``limit`` events per ``window`` seconds per key, counted in memory."""

    def __init__(
        self, limit: int, window: float = 3600.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self._window:
                events.popleft()
            if len(events) >= self._limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:  # forget keys that have gone quiet
                for other in [k for k, v in self._events.items() if not v]:
                    del self._events[other]
            return True
