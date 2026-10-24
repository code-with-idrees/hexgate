"""Turn a routing candidate into a concrete HTTP request for its provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hexgate.config import ProviderConfig
from hexgate.core.routing import Candidate
from hexgate.protocol.anthropic import ANTHROPIC_VERSION, openai_request_to_anthropic


@dataclass(slots=True)
class UpstreamRequest:
    url: str
    headers: dict[str, str]
    json: dict[str, Any]
    api: str


def prepare(
    candidate: Candidate,
    body: dict[str, Any],
    *,
    stream: bool,
    provider: ProviderConfig | None = None,
) -> UpstreamRequest:
    payload = dict(body)
    payload["model"] = candidate.model
    if stream:
        payload["stream"] = True
    else:
        payload.pop("stream", None)

    accept = "text/event-stream" if stream else "application/json"
    extra = dict(provider.headers) if provider and provider.headers else {}

    if candidate.api == "anthropic":
        anthropic_body = openai_request_to_anthropic(payload)
        headers = {
            "content-type": "application/json",
            "accept": accept,
            "anthropic-version": ANTHROPIC_VERSION,
            **extra,
        }
        if candidate.key:
            headers["x-api-key"] = candidate.key
        return UpstreamRequest(
            url=_join(candidate.base_url, "messages"),
            headers=headers,
            json=anthropic_body,
            api="anthropic",
        )

    headers = {
        "content-type": "application/json",
        "accept": accept,
        **extra,
    }
    if candidate.key:
        headers["authorization"] = f"Bearer {candidate.key}"
    return UpstreamRequest(
        url=_join(candidate.base_url, "chat/completions"),
        headers=headers,
        json=payload,
        api="openai",
    )


def _join(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith(f"/{suffix}"):
        return base
    return f"{base}/{suffix}"
