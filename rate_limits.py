"""Limits that protect one small server from a single busy or abusive caller (Phase 5 plan, §6, step 5.4).

    Bundle requests per organization     30 a minute
    Bundle requests without a login      120 a minute per IP address (well above a full tester run)
    Bedrock calls per organization       200 a day (UTC); cached answers don't count

Counters live in memory. That's correct with our single gunicorn worker (wsgi.py); more workers
would need shared storage. They're not for billing, and a deploy resets them.

Flask-Limiter (as the plan suggested) checks limits before our login code has worked out whether a
request belongs to a company or to pod-test, so the bundle routes call these limiters themselves,
after that's known.
"""
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Hashable

ORG_BUNDLES_PER_MINUTE = 30
POD_TEST_BUNDLES_PER_MINUTE = 120
BEDROCK_CALLS_PER_DAY = 200
# Distinct callers remembered at once; the quietest are forgotten first
MAX_KEYS = 10_000


class RateLimiter:
    """At most `limit` events per `window` seconds for each key, over a sliding window."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = MAX_KEYS):
        self.limit = limit
        self.window = window
        self.enabled = True
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[Hashable, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: Hashable) -> float | None:
        """Count one event. None if it's allowed; otherwise the seconds until one would be."""
        if not self.enabled:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits.pop(key, None) or deque()
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            # Re-inserted last, so the dict stays ordered from least to most recently active
            self._hits[key] = hits
            if len(hits) >= self.limit:
                return max(0.0, hits[0] + self.window - now)
            hits.append(now)
            while len(self._hits) > self._max_keys:
                del self._hits[next(iter(self._hits))]
            return None

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


class DailyQuota:
    """At most `limit` uses per key per UTC day."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.time, max_keys: int = MAX_KEYS):
        self.limit = limit
        self.enabled = True
        self._clock = clock
        self._max_keys = max_keys
        self._used: dict[Hashable, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def take(self, key: Hashable) -> bool:
        """Use one. False when today's are used up."""
        if not self.enabled:
            return True
        today = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            day, used = self._used.pop(key, (today, 0))
            if day != today:
                used = 0
            allowed = used < self.limit
            self._used[key] = (today, used + 1 if allowed else used)
            while len(self._used) > self._max_keys:
                del self._used[next(iter(self._used))]
            return allowed

    def clear(self) -> None:
        with self._lock:
            self._used.clear()


org_bundles = RateLimiter(ORG_BUNDLES_PER_MINUTE)
pod_test_bundles = RateLimiter(POD_TEST_BUNDLES_PER_MINUTE)
bedrock_calls = DailyQuota(BEDROCK_CALLS_PER_DAY)


def set_enabled(enabled: bool) -> None:
    """Tests turn the limits off, since one test process makes thousands of requests from 127.0.0.1."""
    for limiter in (org_bundles, pod_test_bundles, bedrock_calls):
        limiter.enabled = enabled
        limiter.clear()
