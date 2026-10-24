"""Per-route circuit breakers.

A route is ``provider/model``. Failures open the circuit for a cooldown, then a
single half-open trial decides whether traffic is allowed back. This is what
stops a dead provider from eating every request's timeout budget.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Circuit:
    slug: str
    calls: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str | None = None
    last_used: float = 0.0
    latency_ms: float = 0.0
    trial_in_flight: bool = False
    history: list[bool] = field(default_factory=list)

    def state(self, now: float) -> str:
        if self.open_until > now:
            return "open"
        if self.open_until:
            return "half-open"
        return "closed"

    @property
    def success_rate(self) -> float:
        sample = self.history[-20:]
        if not sample:
            return 1.0
        return sum(1 for ok in sample if ok) / len(sample)

    def as_dict(self, now: float) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "state": self.state(now),
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "success_rate": round(self.success_rate * 100, 1),
            "latency_ms": round(self.latency_ms, 1),
            "last_error": self.last_error,
            "last_used": self.last_used,
            "retry_in": max(round(self.open_until - now, 1), 0.0),
        }


class HealthRegistry:
    def __init__(self, *, threshold: int = 3, cooldown: float = 60.0) -> None:
        self.threshold = max(threshold, 1)
        self.cooldown = max(cooldown, 1.0)
        self._circuits: dict[str, Circuit] = {}
        self._lock = threading.Lock()

    def configure(self, *, threshold: int | None = None, cooldown: float | None = None) -> None:
        if threshold is not None:
            self.threshold = max(threshold, 1)
        if cooldown is not None:
            self.cooldown = max(cooldown, 1.0)

    def _get(self, slug: str) -> Circuit:
        circuit = self._circuits.get(slug)
        if circuit is None:
            circuit = Circuit(slug=slug)
            self._circuits[slug] = circuit
        return circuit

    def available(self, slug: str) -> bool:
        now = time.time()
        with self._lock:
            circuit = self._get(slug)
            if circuit.state(now) == "open":
                return False
            if circuit.state(now) == "half-open":
                if circuit.trial_in_flight:
                    return False
                circuit.trial_in_flight = True
            return True

    def state(self, slug: str) -> str:
        with self._lock:
            return self._get(slug).state(time.time())

    def can_try(self, slug: str) -> bool:
        """Non-mutating availability check, safe for planning."""
        with self._lock:
            return self._get(slug).state(time.time()) != "open"

    def latency(self, slug: str) -> float:
        with self._lock:
            return self._get(slug).latency_ms

    def record_success(self, slug: str, latency_ms: float) -> None:
        now = time.time()
        with self._lock:
            circuit = self._get(slug)
            circuit.calls += 1
            circuit.successes += 1
            circuit.consecutive_failures = 0
            circuit.open_until = 0.0
            circuit.last_used = now
            circuit.trial_in_flight = False
            circuit.history.append(True)
            circuit.history = circuit.history[-50:]
            circuit.latency_ms = (
                latency_ms
                if circuit.latency_ms == 0
                else circuit.latency_ms * 0.7 + latency_ms * 0.3
            )

    def record_failure(self, slug: str, error: str, *, cooldown: float | None = None) -> None:
        now = time.time()
        with self._lock:
            circuit = self._get(slug)
            circuit.calls += 1
            circuit.failures += 1
            circuit.consecutive_failures += 1
            circuit.last_error = error[:500]
            circuit.last_used = now
            circuit.trial_in_flight = False
            circuit.history.append(False)
            circuit.history = circuit.history[-50:]
            if circuit.consecutive_failures >= self.threshold:
                circuit.open_until = now + max(cooldown or self.cooldown, 1.0)

    def release_trial(self, slug: str) -> None:
        with self._lock:
            self._get(slug).trial_in_flight = False

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            circuits = list(self._circuits.values())
        return sorted(
            (c.as_dict(now) for c in circuits),
            key=lambda item: (item["state"] != "open", -item["calls"]),
        )

    def reset(self, slug: str | None = None) -> None:
        with self._lock:
            if slug:
                self._circuits.pop(slug, None)
            else:
                self._circuits.clear()
