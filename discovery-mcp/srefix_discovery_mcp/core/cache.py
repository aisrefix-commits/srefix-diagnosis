from __future__ import annotations

import time
from typing import Any, Optional


class TTLCache:
    """Trivial in-memory TTL cache. Single-process; no eviction except on read."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts >= self.ttl:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.time(), val)

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> dict:
        now = time.time()
        return {
            "ttl_seconds": self.ttl,
            "entries": len(self._store),
            "ages": {k: round(now - ts, 1) for k, (ts, _) in self._store.items()},
        }
