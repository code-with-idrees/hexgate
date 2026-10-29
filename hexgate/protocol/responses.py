"""OpenAI Responses API translation layer.

Codex CLI speaks the OpenAI Responses API (``POST /v1/responses``) and nothing else:
the old ``wire_api = "chat`` escape hatch has been removed upstream. Hexgate accepts
that wire format, translates it to canonical OpenAI Chat Completions, routes it
through the normal failover / circuit-breaker / key-rotation pipeline, then translates
the response back into Responses API SSE events so Codex CLI works unchanged.

Only the subset Codex CLI actually uses is implemented: message inputs, function calls,
function-call outputs, streaming text deltas, and the terminal ``response.completed``
event. Anything Codex does not require is deliberately omitted.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any


def new_id(prefix: str = "resp") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def unix_now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Request: Responses API -> canonical OpenAI Chat Completions
# ---------------------------------------------------------------------------


def text_of_content(content: Any) -> str:
    """Flatten a Responses ``content`` array (or string) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in {"input_text", "text"}:
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif kind == "input_image":
                url = part.get("image_url") or ""
                parts.append(f"[image:{url}]")
        return "".join(parts)
    return str(content)


def responses_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    """Convert a Codex Responses request body to an OpenAI chat-completions body."""
    if not isinstance(body, dict):
        raise ValueError("Responses request body must be a JSON object")

    messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    input_items = body.get("input")
    if isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")

            if kind == "message":
                role = item.get("role") or "user"
                # Responses puts system-level instructions under "developer"; map to system.
                if role == "developer":
                    role = "system"
                messages.append({"role": role, "content": text_of_content(item.get("content"))})

            elif kind == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                name = item.get("name") or ""
                arguments = item.get("arguments") or "{}"
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                )

            elif kind == "function_call_output":
                call_id = item.get("call_id") or item.get("id") or ""
                output = item.get("output")
                if output is None:
                    output = ""
                elif not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    }
                )

            elif kind == "custom_tool_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                name = item.get("name") or item.get("tool", {}).get("name", "custom_tool")
                input_data = item.get("input")
                arguments = (
                    input_data
                    if isinstance(input_data, str)
                    else json.dumps(input_data or {}, ensure_ascii=False)
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                )

            elif kind == "reasoning":
                # Reasoning items carry no client-visible text; skip them here.
                continue

            # Unknown item types are dropped rather than corrupting the request.

    top_tools = body.get("tools")
    if isinstance(top_tools, list):
        for tool in top_tools:
            if not isinstance(tool, dict):
                continue
            kind = tool.get("type")
            if kind == "function":
                tools.append(tool)
            elif kind == "namespace":
                for child in tool.get("tools") or []:
                    if isinstance(child, dict) and child.get("type") == "function":
                        tools.append(child)

    chat: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "stream": bool(body.get("stream")),
    }

    if tools:
        chat["tools"] = tools
    tc = body.get("tool_choice")
    if isinstance(tc, str):
        chat["tool_choice"] = tc if tc in {"none", "required", "auto"} else "auto"
    elif isinstance(tc, dict):
        chat["tool_choice"] = tc

    return chat


# ---------------------------------------------------------------------------
# Response: OpenAI Chat Completions -> Responses API (non-streaming)
# ---------------------------------------------------------------------------


def chat_response_to_responses(
    payload: dict[str, Any],
    *,
    requested_model: str,
    response_id: str | None = None,
) -> dict[str, Any]:
    """Convert a non-streaming OpenAI chat response to a Responses API object."""
    rid = response_id or new_id()
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []

    output: list[dict[str, Any]] = []

    if content:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
                "status": "completed",
            }
        )

    for call in tool_calls:
        fn = call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name") or "",
                "arguments": fn.get("arguments") or "{}",
                "status": "completed",
            }
        )

    usage = payload.get("usage") or {}
    return {
        "id": rid,
        "object": "response",
        "created": unix_now(),
        "model": requested_model or payload.get("model") or "",
        "output": output,
        "status": "completed",
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(payload: Any, event: str | None = None) -> bytes:
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n".encode()


def responses_created(response_id: str) -> bytes:
    return _sse(
        {"type": "response.created", "response": {"id": response_id}},
        "response.created",
    )


def responses_output_item_added(item: dict[str, Any]) -> bytes:
    return _sse(
        {"type": "response.output_item.added", "item": item},
        "response.output_item.added",
    )


def responses_output_text_delta(delta: str) -> bytes:
    return _sse(
        {"type": "response.output_text.delta", "delta": delta},
        "response.output_text.delta",
    )


def responses_output_item_done(item: dict[str, Any]) -> bytes:
    return _sse(
        {"type": "response.output_item.done", "item": item},
        "response.output_item.done",
    )


def responses_completed(response_id: str, usage: dict[str, Any] | None = None) -> bytes:
    return _sse(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": usage
                or {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            },
        },
        "response.completed",
    )


def responses_failed(response_id: str, message: str) -> bytes:
    return _sse(
        {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "error": {"code": "server_error", "message": message},
            },
        },
        "response.failed",
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Streaming: OpenAI chunks -> Responses API SSE events
# ---------------------------------------------------------------------------


async def _extract_data_frames(source: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed JSON objects from ``data:`` SSE lines in a byte stream."""
    async for raw in source:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        for segment in line.split("\n"):
            segment = segment.strip()
            if segment.startswith("data: "):
                payload = segment[6:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue


async def openai_chunks_to_responses(
    source: AsyncIterator[bytes],
    *,
    model: str,
    prompt_tokens: int,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Translate an OpenAI Chat Completions SSE byte stream into Responses API events.

    ``source`` yields raw SSE frames (e.g. ``data: {...}\\n\\n``) from any OpenAI-shaped
    upstream, already normalized to OpenAI chat-chunk form by the gateway.
    """
    response_id = new_id()
    yield responses_created(response_id)

    item_emitted = False
    item_id = f"msg_{uuid.uuid4().hex[:12]}"
    accumulated = ""
    completion_tokens = 0

    async for chunk in _extract_data_frames(source):
        choices = chunk.get("choices") or []
        if not choices:
            # A chunk may carry only usage (e.g. a final non-streaming-looking chunk).
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                completion_tokens = int(usage.get("completion_tokens") or 0)
            continue
        delta = choices[0].get("delta") or {}
        content_delta = delta.get("content")
        if isinstance(content_delta, str) and content_delta:
            if not item_emitted:
                item_emitted = True
                yield responses_output_item_added(
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": item_id,
                        "content": [{"type": "output_text", "text": ""}],
                        "status": "in_progress",
                    }
                )
            accumulated += content_delta
            yield responses_output_text_delta(content_delta)

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            completion_tokens = int(usage.get("completion_tokens") or 0)

    yield responses_output_item_done(
        {
            "type": "message",
            "role": "assistant",
            "id": item_id,
            "content": [{"type": "output_text", "text": accumulated}],
            "status": "completed",
        }
    )

    out_tokens = completion_tokens or estimate_tokens(accumulated)
    yield responses_completed(
        response_id,
        usage={
            "input_tokens": int(prompt_tokens or 0),
            "output_tokens": out_tokens,
            "total_tokens": int(prompt_tokens or 0) + out_tokens,
        },
    )
    _record(
        stats,
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=out_tokens,
    )


def _record(stats: dict[str, Any] | None, **values: Any) -> None:
    if stats is None:
        return
    for key, value in values.items():
        if value is None:
            continue
        stats[key] = value
