"""Tiny in-process TTL cache for read-only analytics (audit I-8/D7).

The analytics endpoints recompute several SQL aggregates on every request.
The numbers only change when the data changes, so a short TTL (60-300s per
the audit) keeps repeated dashboard loads cheap. Entries live in the process;
nothing external is added as a dependency (audit rule: no new dependencies).

A TTL of zero disables caching entirely - conftest.py sets
`ANALYTICS_CACHE_SECONDS=0`, so no test can ever read a stale response and
the real deployment is the only place where caching is active.
"""
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, seconds: float | Callable[[], float] = 0.0) -> None:
        # A callable provider lets tests and the configuration change the TTL
        # without rebuilding the cache object.
        self._provider = seconds if callable(seconds) else (lambda: seconds)
        self._store: dict[Any, tuple[float, Any]] = {}

    @property
    def seconds(self) -> float:
        return float(self._provider())

    @property
    def _store(self) -> dict:  # pragma: no cover - typing helper only
        return self.__dict__.setdefault("_items", {})

    @_store.setter
    def _store(self, value: dict) -> None:
        self.__dict__["_items"] = value

    def get(self, key: Any) -> tuple[Any, bool]:
        ttl = self.seconds
        if ttl <= 0:
            return None, False
        entry = self._store.get(key)
        if entry is None:
            return None, False
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None, False
        return value, True

    def put(self, key: Any, value: Any) -> Any:
        ttl = self.seconds
        if ttl <= 0:
            return value
        if len(self._store) >= 64:
            # Defensive bound: a handful of analytic keys is expected; the
            # oldest entry is dropped rather than growing without limit.
            oldest_key = min(self._store, key=lambda item: self._store[item][0])
            self._store.pop(oldest_key, None)
        self._store[key] = (time.monotonic() + ttl, value)
        return value

    async def wrap(self, key: Any, factory: Callable[[], Any]) -> Any:
        """Await ``factory()`` unless a fresh entry exists for ``key``."""
        value, found = self.get(key)
        if found:
            return value
        return self.put(key, await factory())
