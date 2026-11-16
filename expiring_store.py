"""In-memory key-value store that forgets entries, so memory stays bounded.

Bundles, their archives and test sessions are created by requests that need no login, so
without limits anyone could fill the server's memory. Each store removes an entry once it is
older than `ttl` seconds, and removes the oldest entries first whenever it holds more than
`max_items` entries or more than `max_bytes` (as measured by `size_of`).

Entries expire by age from when they were stored, so insertion order is also expiry order
and expired entries are always at the front. Sizes are measured again on every insert,
because a bundle grows after it is stored, while it's processed in the background.
"""
import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, Iterator, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class ExpiringStore(Generic[K, V]):
    def __init__(self, ttl: float, max_items: int, max_bytes: int | None = None,
                 size_of: Callable[[V], int] | None = None,
                 on_evict: Callable[[K, V], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if ttl <= 0 or max_items <= 0:
            raise ValueError("ttl and max_items must be positive")
        if (max_bytes is None) != (size_of is None):
            raise ValueError("max_bytes and size_of go together")
        self.ttl = ttl
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._size_of = size_of
        self._on_evict = on_evict
        self._clock = clock
        self._lock = threading.RLock()
        # key -> (stored_at, value), oldest first
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            # A replaced entry starts a new lifetime and moves to the back
            self._entries.pop(key, None)
            self._entries[key] = (self._clock(), value)
            self._purge()

    def add(self, key: K, value: V) -> bool:
        """Store value only if key is absent (or expired). True if it was stored."""
        with self._lock:
            if self._live(key) is not None:
                return False
            self[key] = value
            return True

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            entry = self._live(key)
            return default if entry is None else entry[1]

    def __getitem__(self, key: K) -> V:
        with self._lock:
            entry = self._live(key)
            if entry is None:
                raise KeyError(key)
            return entry[1]

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return self._live(key) is not None  # type: ignore[arg-type]

    def pop(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            entry = self._entries.pop(key, None)
            return default if entry is None else entry[1]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._expire()
            return len(self._entries)

    def __iter__(self) -> Iterator[K]:
        with self._lock:
            self._expire()
            return iter(list(self._entries))

    def _live(self, key: K) -> tuple[float, V] | None:
        entry = self._entries.get(key)
        if entry is not None and self._clock() - entry[0] >= self.ttl:
            self._evict(key)
            return None
        return entry

    def _expire(self) -> None:
        now = self._clock()
        while self._entries:
            key, (stored_at, _) = next(iter(self._entries.items()))
            if now - stored_at < self.ttl:
                break
            self._evict(key)

    def _purge(self) -> None:
        self._expire()
        while len(self._entries) > self.max_items:
            self._evict(next(iter(self._entries)))
        if self.max_bytes is not None and self._size_of is not None:
            size_of = self._size_of
            total = sum(size_of(value) for _, value in self._entries.values())
            # Never evict the entry just stored, even if it alone is over the limit
            while total > self.max_bytes and len(self._entries) > 1:
                key = next(iter(self._entries))
                total -= size_of(self._entries[key][1])
                self._evict(key)

    def _evict(self, key: K) -> None:
        _, value = self._entries.pop(key)
        if self._on_evict is not None:
            self._on_evict(key, value)
