"""Streaming translation between SSE dialects.

All generators yield already-serialised SSE bytes and optionally mutate a
``stats`` dict so the gateway can record token usage without re-parsing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from hexgate.protocol.anthropic import STOP_TO_OPENAI
from hexgate.protocol.openai import chunk, estimate_tokens, sse, unix_now

OPENAI_TO_ANTHROPIC_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _record(stats: dict[str, Any] | None, **values: Any) -> None:
    if stats is None:
        return
    for key, value in values.items():
        if value is None:
            continue
        if (
            isinstance(value, int)
            and key in {"prompt_tokens", "completion_tokens"}
            and value == 0
            and stats.get(key)
        ):
            continue
        stats[key] = value


# ---------------------------------------------------------------------------
# OpenAI upstream -> Anthropic client
# ---------------------------------------------------------------------------


async def openai_chunks_to_anthropic(
    chunks: AsyncIterator[dict[str, Any]],
    *,
    model: str,
    prompt_tokens: int = 0,
    message_id: str | None = None,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    mid = message_id or f"msg_{uuid.uuid4().hex[:24]}"
    block = -1
    open_kind: str | None = None
    tools: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage: dict[str, Any] = {}
    text_chars = 0

    yield sse(
        {
            "type": "message_start",
            "message": {
                "id": mid,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": prompt_tokens, "output_tokens": 0},
            },
        },
        "message_start",
    )
    yield sse({"type": "ping"}, "ping")

    def stop(index: int) -> bytes:
        return sse({"type": "content_block_stop", "index": index}, "content_block_stop")

    async for raw in chunks:
        if not isinstance(raw, dict):
            continue
        error = raw.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            message = str(message or "upstream error")
            yield sse(
                {"type": "error", "error": {"type": "api_error", "message": message}},
                "error",
            )
            _record(stats, error=message)
            return
        if raw.get("usage"):
            usage = raw["usage"]
        for choice in raw.get("choices") or []:
            delta = choice.get("delta") or {}

            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                if open_kind != "thinking":
                    if block >= 0:
                        yield stop(block)
                    block += 1
                    open_kind = "thinking"
                    yield sse(
                        {
                            "type": "content_block_start",
                            "index": block,
                            "content_block": {"type": "thinking", "thinking": ""},
                        },
                        "content_block_start",
                    )
                yield sse(
                    {
                        "type": "content_block_delta",
                        "index": block,
                        "delta": {"type": "thinking_delta", "thinking": reasoning},
                    },
                    "content_block_delta",
                )

            text = delta.get("content")
            if isinstance(text, str) and text:
                if open_kind != "text":
                    if block >= 0:
                        yield stop(block)
                    block += 1
                    open_kind = "text"
                    yield sse(
                        {
                            "type": "content_block_start",
                            "index": block,
                            "content_block": {"type": "text", "text": ""},
                        },
                        "content_block_start",
                    )
                text_chars += len(text)
                yield sse(
                    {
                        "type": "content_block_delta",
                        "index": block,
                        "delta": {"type": "text_delta", "text": text},
                    },
                    "content_block_delta",
                )

            for call in delta.get("tool_calls") or []:
                index = call.get("index")
                if not isinstance(index, int):
                    index = len(tools)
                state = tools.setdefault(index, {"block": None, "id": "", "name": "", "buffer": []})
                if call.get("id"):
                    state["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    state["name"] = function["name"]
                fragment = function.get("arguments")

                if state["block"] is None:
                    if fragment:
                        state["buffer"].append(fragment)
                    if not state["name"]:
                        continue
                    if block >= 0:
                        yield stop(block)
                    block += 1
                    state["block"] = block
                    open_kind = "tool"
                    yield sse(
                        {
                            "type": "content_block_start",
                            "index": block,
                            "content_block": {
                                "type": "tool_use",
                                "id": state["id"] or f"toolu_{uuid.uuid4().hex[:20]}",
                                "name": state["name"],
                                "input": {},
                            },
                        },
                        "content_block_start",
                    )
                    for buffered in state["buffer"]:
                        yield sse(
                            {
                                "type": "content_block_delta",
                                "index": block,
                                "delta": {"type": "input_json_delta", "partial_json": buffered},
                            },
                            "content_block_delta",
                        )
                    state["buffer"] = []
                elif fragment:
                    yield sse(
                        {
                            "type": "content_block_delta",
                            "index": state["block"],
                            "delta": {"type": "input_json_delta", "partial_json": fragment},
                        },
                        "content_block_delta",
                    )

            reason = choice.get("finish_reason")
            if reason:
                finish = reason

    if block >= 0:
        yield stop(block)

    prompt = int(usage.get("prompt_tokens") or prompt_tokens or 0)
    completion = int(usage.get("completion_tokens") or estimate_tokens("x" * text_chars) or 0)
    stop_reason = OPENAI_TO_ANTHROPIC_STOP.get(finish, "end_turn")
    if (
        any(state.get("block") is not None for state in tools.values())
        and stop_reason == "end_turn"
    ):
        stop_reason = "tool_use"

    yield sse(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"input_tokens": prompt, "output_tokens": completion},
        },
        "message_delta",
    )
    yield sse({"type": "message_stop"}, "message_stop")

    _record(
        stats,
        prompt_tokens=prompt,
        completion_tokens=completion,
        stop_reason=stop_reason,
        text_chars=text_chars,
    )


# ---------------------------------------------------------------------------
# Anthropic upstream -> OpenAI client
# ---------------------------------------------------------------------------


async def anthropic_events_to_openai(
    events: AsyncIterator[tuple[str | None, dict[str, Any] | str]],
    *,
    model: str,
    prompt_tokens: int = 0,
    chunk_id: str | None = None,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    cid = chunk_id or f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = unix_now()
    started = False
    tool_index = -1
    finish: str | None = None
    usage: dict[str, Any] = {}
    text_chars = 0

    def emit(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
        return sse(
            chunk(
                model=model,
                delta=delta,
                chunk_id=cid,
                created=created,
                finish_reason=finish_reason,
            )
        )

    async for _event, data in events:
        if isinstance(data, str):
            continue
        kind = data.get("type")
        if kind == "message_start":
            message = data.get("message") or {}
            usage["prompt_tokens"] = int((message.get("usage") or {}).get("input_tokens") or 0)
            if not started:
                started = True
                yield emit({"role": "assistant", "content": ""})
        elif kind == "content_block_start":
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                tool_index += 1
                yield emit(
                    {
                        "tool_calls": [
                            {
                                "index": tool_index,
                                "id": block.get("id") or f"call_{uuid.uuid4().hex[:20]}",
                                "type": "function",
                                "function": {"name": block.get("name") or "tool", "arguments": ""},
                            }
                        ]
                    }
                )
        elif kind == "content_block_delta":
            delta = data.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    text_chars += len(text)
                    yield emit({"content": text})
            elif dtype == "input_json_delta":
                partial = str(delta.get("partial_json") or "")
                if partial:
                    yield emit(
                        {
                            "tool_calls": [
                                {"index": max(tool_index, 0), "function": {"arguments": partial}}
                            ]
                        }
                    )
            elif dtype == "thinking_delta":
                thinking = str(delta.get("thinking") or "")
                if thinking:
                    yield emit({"reasoning_content": thinking})
        elif kind == "message_delta":
            delta = data.get("delta") or {}
            finish = STOP_TO_OPENAI.get(delta.get("stop_reason"), "stop")
            if tool_index >= 0:
                finish = "tool_calls"
            out_tokens = (data.get("usage") or {}).get("output_tokens")
            if out_tokens:
                usage["completion_tokens"] = int(out_tokens)
        elif kind == "error":
            error = data.get("error") or {}
            yield sse({"error": error})
            yield sse("[DONE]")
            _record(stats, error=error.get("message") or "upstream error")
            return

    if not started:
        yield emit({"role": "assistant", "content": ""})

    prompt = int(usage.get("prompt_tokens") or prompt_tokens or 0)
    completion = int(usage.get("completion_tokens") or estimate_tokens("x" * text_chars) or 0)
    yield sse(
        chunk(
            model=model,
            delta={},
            chunk_id=cid,
            created=created,
            finish_reason=finish or "stop",
            usage={
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        )
    )
    yield sse("[DONE]")
    _record(
        stats,
        prompt_tokens=prompt,
        completion_tokens=completion,
        stop_reason=finish or "stop",
        text_chars=text_chars,
    )


# ---------------------------------------------------------------------------
# Same-protocol passthrough (with model rewrite so clients see what they asked for)
# ---------------------------------------------------------------------------


async def rewrite_openai_stream(
    chunks: AsyncIterator[dict[str, Any]],
    *,
    model: str,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    text_chars = 0
    usage: dict[str, Any] = {}
    finish: str | None = None
    seen_done = False
    async for raw in chunks:
        if isinstance(raw, str):
            if raw.strip() == "[DONE]":
                seen_done = True
                yield sse("[DONE]")
            continue
        raw["model"] = model
        error = raw.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            _record(stats, error=str(message or "upstream error"))
        if raw.get("usage"):
            usage = raw["usage"]
        for choice in raw.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str):
                text_chars += len(text)
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
        yield sse(raw)
    if not seen_done:
        # Some OpenAI-compatible gateways close the socket without the sentinel.
        # Agents wait for it, so always emit exactly one terminator.
        yield sse("[DONE]")
    _record(
        stats,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(
            usage.get("completion_tokens") or estimate_tokens("x" * text_chars) or 0
        ),
        stop_reason=finish or "stop",
        text_chars=text_chars,
    )


async def rewrite_anthropic_stream(
    events: AsyncIterator[tuple[str | None, dict[str, Any] | str]],
    *,
    model: str,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    text_chars = 0
    prompt_tokens = 0
    completion_tokens = 0
    stop_reason: str | None = None
    async for event, data in events:
        if isinstance(data, str):
            continue
        if data.get("type") == "message_start":
            message = data.setdefault("message", {})
            message["model"] = model
            prompt_tokens = int((message.get("usage") or {}).get("input_tokens") or 0)
        elif data.get("type") == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                text_chars += len(str(delta.get("text") or ""))
        elif data.get("type") == "message_delta":
            stop_reason = (data.get("delta") or {}).get("stop_reason") or stop_reason
            completion_tokens = int((data.get("usage") or {}).get("output_tokens") or 0)
        yield sse(data, data.get("type") or event)
    _record(
        stats,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens or estimate_tokens("x" * text_chars),
        stop_reason=stop_reason or "stop",
        text_chars=text_chars,
    )


def sse_error(message: str, *, kind: str = "api_error", status: int = 502) -> bytes:
    """OpenAI-style error frame for mid-stream failures."""
    return sse(
        {
            "error": {
                "message": message,
                "type": kind,
                "code": status,
            }
        }
    )


def dump_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
