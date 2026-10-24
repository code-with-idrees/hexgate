"""OpenAI Chat Completions helpers: canonical internal representation."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any


def new_id(prefix: str = "chatcmpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def unix_now() -> int:
    return int(time.time())


def text_of(content: Any) -> str:
    """Flatten OpenAI message content (string or multimodal parts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return str(content)


def image_part(url: str, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"url": url}
    if detail:
        payload["detail"] = detail
    return {"type": "image_url", "image_url": payload}


def chunk(
    *,
    model: str,
    delta: dict[str, Any],
    chunk_id: str,
    created: int | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created or unix_now(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def completion(
    *,
    model: str,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None,
    finish_reason: str,
    usage: dict[str, Any],
    response_id: str | None = None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "id": response_id or new_id(),
        "object": "chat.completion",
        "created": unix_now(),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def usage(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    *,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int((prompt_tokens or 0) + (completion_tokens or 0)),
    }
    if cached_tokens:
        payload["prompt_tokens_details"] = {"cached_tokens": int(cached_tokens)}
    if reasoning_tokens:
        payload["completion_tokens_details"] = {"reasoning_tokens": int(reasoning_tokens)}
    return payload


def estimate_tokens(text: str) -> int:
    """Rough fallback when a provider omits usage. ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"_raw": str(raw)}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def sse(payload: dict[str, Any] | str, event: str | None = None) -> bytes:
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n".encode()


async def iter_sse(
    lines: AsyncIterator[str],
) -> AsyncIterator[tuple[str | None, dict[str, Any] | str]]:
    """Parse an SSE byte stream into ``(event, data)`` pairs.

    Tolerates CRLF, comments, unknown fields and multi-line ``data:`` payloads.
    """
    event: str | None = None
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                payload: dict[str, Any] | str
                joined = "\n".join(data_lines)
                if joined.strip() == "[DONE]":
                    payload = "[DONE]"
                else:
                    try:
                        payload = json.loads(joined)
                    except json.JSONDecodeError:
                        payload = joined
                yield event, payload
            event = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        # ignore id:, retry:, etc.

    if data_lines:  # stream ended without a trailing blank line
        joined = "\n".join(data_lines)
        if joined.strip() != "[DONE]":
            try:
                yield event, json.loads(joined)
            except json.JSONDecodeError:
                yield event, joined
