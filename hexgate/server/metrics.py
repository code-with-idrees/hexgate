"""Prometheus text exposition."""

from __future__ import annotations

from hexgate.core.health import HealthRegistry
from hexgate.telemetry import Telemetry


def render(telemetry: Telemetry, health: HealthRegistry, version: str) -> str:
    stats = telemetry.stats(window_seconds=0)
    lines: list[str] = [
        "# HELP hexgate_info Build information.",
        "# TYPE hexgate_info gauge",
        f'hexgate_info{{version="{version}"}} 1',
        "# HELP hexgate_requests_total Requests handled, by outcome.",
        "# TYPE hexgate_requests_total counter",
        f'hexgate_requests_total{{outcome="ok"}} {stats["ok"]}',
        f'hexgate_requests_total{{outcome="error"}} {stats["errors"]}',
        "# HELP hexgate_tokens_total Tokens processed, by direction.",
        "# TYPE hexgate_tokens_total counter",
        f'hexgate_tokens_total{{direction="prompt"}} {stats["prompt_tokens"]}',
        f'hexgate_tokens_total{{direction="completion"}} {stats["completion_tokens"]}',
        "# HELP hexgate_cost_usd_total Estimated spend in USD.",
        "# TYPE hexgate_cost_usd_total counter",
        f"hexgate_cost_usd_total {stats['cost_usd']:.6f}",
        "# HELP hexgate_saved_usd_total Estimated savings versus a frontier baseline.",
        "# TYPE hexgate_saved_usd_total counter",
        f"hexgate_saved_usd_total {stats['saved_usd']:.6f}",
        "# HELP hexgate_request_duration_ms Request latency quantiles.",
        "# TYPE hexgate_request_duration_ms gauge",
        f'hexgate_request_duration_ms{{quantile="0.5"}} {stats["latency"]["p50"]}',
        f'hexgate_request_duration_ms{{quantile="0.95"}} {stats["latency"]["p95"]}',
        "# HELP hexgate_circuit_state Circuit state per route (1=closed, 0.5=half-open, 0=open).",
        "# TYPE hexgate_circuit_state gauge",
    ]
    for circuit in health.snapshot():
        value = {"closed": 1.0, "half-open": 0.5, "open": 0.0}.get(circuit["state"], 1.0)
        route = circuit["slug"].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'hexgate_circuit_state{{route="{route}"}} {value}')
    return "\n".join(lines) + "\n"
