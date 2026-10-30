"""Small TTL cache used for the overview (30 s) and the map (10 s) per tenant,
and a fixed-window counter for the login rate limit."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, Hashable, Optional, Tuple


class TtlCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._items: Dict[Hashable, Tuple[float, Any]] = {}

    def get(self, key: Hashable) -> Optional[Any]:
        """Fresh value or None."""
        item = self._items.get(key)
        if item and item[0] > time.monotonic():
            return item[1]
        return None

    def get_stale(self, key: Hashable) -> Optional[Any]:
        """Last value even when expired (used when a neighbor is down)."""
        item = self._items.get(key)
        return item[1] if item else None

    def put(self, key: Hashable, value: Any) -> None:
        self._items[key] = (time.monotonic() + self.ttl, value)

    def expire(self, key: Hashable) -> None:
        """Mark one entry stale while keeping its value (used by the tests)."""
        item = self._items.get(key)
        if item:
            self._items[key] = (0.0, item[1])

    def clear(self) -> None:
        self._items.clear()


class RateCounter:
    """Counts hits per key in a sliding one-minute window."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._hits: Dict[str, list] = defaultdict(list)

    def hit(self, key: str) -> bool:
        """Record a hit; True when the key is still under the limit."""
        now = time.monotonic()
        hits = [t for t in self._hits[key] if t > now - 60]
        hits.append(now)
        self._hits[key] = hits
        return len(hits) <= self.limit
