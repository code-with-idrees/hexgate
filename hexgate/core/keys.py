"""API-key rotation with per-key cooldowns.

Providers rate-limit per key. Rotating on failure means a second key can keep
the turn alive while the first one cools down, instead of surfacing a 429 to the
coding agent.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class KeyRing:
    def __init__(self) -> None:
        self._cursor: dict[str, int] = {}
        self._cooldown: dict[tuple[str, int], float] = {}
        self._failures: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def order(self, provider: str, count: int) -> list[int]:
        """Return key indexes in the order they should be tried."""
        if count <= 0:
            return []
        now = time.time()
        with self._lock:
            start = self._cursor.get(provider, 0) % count
            indexes = [(start + offset) % count for offset in range(count)]
            usable = [i for i in indexes if self._cooldown.get((provider, i), 0.0) <= now]
            cooled = [i for i in indexes if self._cooldown.get((provider, i), 0.0) > now]
            ordered = usable + cooled
            self._cursor[provider] = (start + 1) % count
            return ordered

    def penalize(self, provider: str, index: int, seconds: float) -> None:
        with self._lock:
            until = time.time() + max(seconds, 1.0)
            current = self._cooldown.get((provider, index), 0.0)
            self._cooldown[(provider, index)] = max(current, until)
            self._failures[(provider, index)] = self._failures.get((provider, index), 0) + 1

    def reward(self, provider: str, index: int) -> None:
        with self._lock:
            self._cooldown.pop((provider, index), None)
            self._failures[(provider, index)] = 0

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = []
            for (provider, index), until in sorted(self._cooldown.items()):
                rows.append(
                    {
                        "provider": provider,
                        "key_index": index + 1,
                        "cooling": until > now,
                        "retry_in": max(round(until - now, 1), 0.0),
                        "failures": self._failures.get((provider, index), 0),
                    }
                )
            return rows
