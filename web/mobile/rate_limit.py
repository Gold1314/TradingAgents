"""Per-user rate limiting for the LLM-consuming v2 run endpoint.

A deliberately small, dependency-free fixed-window limiter keyed by
``user_id``. Two windows are enforced together (hour + day) so one account
can't drain LLM credits. Exceeding either returns the number of seconds the
caller must wait (surfaced by the router as HTTP 429 + ``Retry-After``).

In-memory + lock-guarded — correct for a single instance. The
:class:`RateLimiter` interface is intentionally minimal so it can be swapped
for a Redis/Supabase-backed implementation later without touching callers:
just provide another object with the same :meth:`check_and_consume` signature.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

HOUR = 3600
DAY = 86400


@dataclass
class _Window:
    start: float
    count: int


@dataclass
class RateLimiter:
    """Fixed-window per-user limiter (hour + day)."""

    per_hour: int
    per_day: int
    _hour: Dict[str, _Window] = field(default_factory=dict)
    _day: Dict[str, _Window] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _check_window(
        self, store: Dict[str, _Window], user_id: str, limit: int, span: float, now: float
    ) -> Tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for one window.

        Does not mutate the counter — consumption happens only once both windows
        agree (see :meth:`check_and_consume`).
        """
        if limit <= 0:  # 0/negative means "unlimited" for that window
            return True, 0
        win = store.get(user_id)
        if win is None or (now - win.start) >= span:
            return True, 0
        if win.count < limit:
            return True, 0
        retry = int(span - (now - win.start)) + 1
        return False, max(retry, 1)

    def _consume_window(
        self, store: Dict[str, _Window], user_id: str, span: float, now: float
    ) -> None:
        win = store.get(user_id)
        if win is None or (now - win.start) >= span:
            store[user_id] = _Window(start=now, count=1)
        else:
            win.count += 1

    def check_and_consume(self, user_id: str) -> Optional[int]:
        """Consume one unit of quota for ``user_id``.

        Returns ``None`` when allowed, or the ``Retry-After`` seconds when the
        hourly or daily quota is exhausted (nothing is consumed in that case).
        """
        now = time.time()
        with self._lock:
            ok_h, retry_h = self._check_window(self._hour, user_id, self.per_hour, HOUR, now)
            ok_d, retry_d = self._check_window(self._day, user_id, self.per_day, DAY, now)
            if not ok_h or not ok_d:
                return max(retry_h, retry_d)
            self._consume_window(self._hour, user_id, HOUR, now)
            self._consume_window(self._day, user_id, DAY, now)
            return None

    def reset(self, user_id: Optional[str] = None) -> None:
        """Clear counters (test/admin helper)."""
        with self._lock:
            if user_id is None:
                self._hour.clear()
                self._day.clear()
            else:
                self._hour.pop(user_id, None)
                self._day.pop(user_id, None)
