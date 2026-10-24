"""In-memory request telemetry with bounded memory usage.

The buffer is intentionally small and lossy: this is an operations dashboard,
not an audit log. Set ``telemetry.retain`` to trade memory for history.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RequestRecord:
    id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:16]}")
    ts: float = field(default_factory=time.time)
    protocol: str = "openai"
    requested_model: str = ""
    provider: str = ""
    model: str = ""
    status: int = 200
    ok: bool = True
    stream: bool = False
    attempts: int = 1
    route: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    saved_usd: float | None = None
    optimized_tokens: int = 0
    error: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.provider}/{self.model}" if self.provider else self.model

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["slug"] = self.slug
        data["total_tokens"] = self.prompt_tokens + self.completion_tokens
        return data


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * fraction)), len(ordered) - 1)
    return round(ordered[index], 1)


class Telemetry:
    def __init__(self, retain: int = 500) -> None:
        self._records: deque[RequestRecord] = deque(maxlen=max(retain, 0))
        self._lock = threading.Lock()
        self.started_at = time.time()

    # -- writes ----------------------------------------------------------
    def add(self, record: RequestRecord) -> RequestRecord:
        with self._lock:
            self._records.append(record)
        return record

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def resize(self, retain: int) -> None:
        with self._lock:
            records = list(self._records)[-max(retain, 0) :] if retain else []
            self._records = deque(records, maxlen=max(retain, 0))

    # -- reads -----------------------------------------------------------
    def recent(
        self,
        limit: int = 100,
        *,
        ok: bool | None = None,
        provider: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records)
        records.reverse()
        needle = (query or "").lower()
        out: list[dict[str, Any]] = []
        for record in records:
            if ok is not None and record.ok is not ok:
                continue
            if provider and record.provider != provider:
                continue
            if (
                needle
                and needle
                not in (
                    f"{record.requested_model} {record.provider} {record.model} {record.error or ''}"
                ).lower()
            ):
                continue
            out.append(record.as_dict())
            if len(out) >= max(limit, 1):
                break
        return out

    def stats(self, window_seconds: float = 86_400, buckets: int = 24) -> dict[str, Any]:
        now = time.time()
        cutoff = now - window_seconds if window_seconds > 0 else 0.0
        with self._lock:
            records = [r for r in self._records if r.ts >= cutoff]

        total = len(records)
        errors = sum(1 for r in records if not r.ok)
        latencies = [r.latency_ms for r in records if r.ok and r.latency_ms > 0]
        prompt_tokens = sum(r.prompt_tokens for r in records)
        completion_tokens = sum(r.completion_tokens for r in records)
        cached = sum(r.cached_tokens for r in records)
        cost = sum(r.cost_usd or 0.0 for r in records)
        saved = sum(r.saved_usd or 0.0 for r in records)
        optimized = sum(r.optimized_tokens for r in records)

        by_provider: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        for record in records:
            prov = by_provider.setdefault(
                record.provider or "unknown",
                {
                    "provider": record.provider or "unknown",
                    "requests": 0,
                    "errors": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "latency_ms": 0.0,
                },
            )
            prov["requests"] += 1
            prov["errors"] += 0 if record.ok else 1
            prov["tokens"] += record.prompt_tokens + record.completion_tokens
            prov["cost_usd"] += record.cost_usd or 0.0
            prov["latency_ms"] += record.latency_ms

            key = record.slug or "unknown"
            model = by_model.setdefault(
                key,
                {"slug": key, "requests": 0, "tokens": 0, "cost_usd": 0.0, "errors": 0},
            )
            model["requests"] += 1
            model["tokens"] += record.prompt_tokens + record.completion_tokens
            model["cost_usd"] += record.cost_usd or 0.0
            model["errors"] += 0 if record.ok else 1

        for entry in by_provider.values():
            entry["avg_latency_ms"] = round(entry["latency_ms"] / max(entry["requests"], 1), 1)
            entry.pop("latency_ms", None)
            entry["cost_usd"] = round(entry["cost_usd"], 6)

        timeline = _timeline(records, now, window_seconds, buckets)

        return {
            "window_seconds": window_seconds,
            "requests": total,
            "ok": total - errors,
            "errors": errors,
            "error_rate": round(errors / total * 100, 1) if total else 0.0,
            "streams": sum(1 for r in records if r.stream),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cached_tokens": cached,
            "optimized_tokens": optimized,
            "cost_usd": round(cost, 6),
            "saved_usd": round(saved, 6),
            "latency": {
                "p50": _percentile(latencies, 0.5),
                "p95": _percentile(latencies, 0.95),
                "avg": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            },
            "providers": sorted(by_provider.values(), key=lambda x: -x["requests"]),
            "models": sorted(by_model.values(), key=lambda x: -x["requests"])[:10],
            "timeline": timeline,
        }


def _timeline(
    records: list[RequestRecord], now: float, window: float, buckets: int
) -> list[dict[str, Any]]:
    buckets = max(buckets, 1)
    width = max(window / buckets, 1.0)
    start = now - width * buckets
    out = [
        {"t": round(start + width * i, 3), "requests": 0, "errors": 0, "tokens": 0}
        for i in range(buckets)
    ]
    for record in records:
        index = int((record.ts - start) // width)
        index = min(max(index, 0), buckets - 1)
        out[index]["requests"] += 1
        out[index]["errors"] += 0 if record.ok else 1
        out[index]["tokens"] += record.prompt_tokens + record.completion_tokens
    return out
