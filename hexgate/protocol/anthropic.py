"""Anthropic Messages <-> OpenAI Chat Completions translation.

Everything inside Hexgate is canonicalised to the OpenAI Chat Completions
shape, because it is the widest-supported wire format. These functions are pure
and side-effect free so they can be unit tested without a server.
"""

from __future__ import annotations

import json
from typing import Any

from hexgate.protocol.openai import estimate_tokens, new_id, parse_args, text_of, unix_now

ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS_DEFAULT = 8192

STOP_TO_ANTHROPIC = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
    None: "end_turn",
}

STOP_TO_OPENAI = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "pause_turn": "stop",
    "refusal": "content_filter",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return [{"type": "text", "text": str(content)}]


def _blocks_text(content: Any) -> str:
    return "".join(
        str(b.get("text", "")) for b in _blocks(content) if b.get("type") in {"text", "output_text"}
    )


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                chunks.append(str(block))
            elif block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            elif block.get("type") == "image":
                chunks.append("[image omitted in text tool result]")
            else:
                chunks.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(chunks)
    return str(content or "")


def _anthropic_image(block: dict[str, Any]) -> dict[str, Any] | None:
    source = block.get("source") or {}
    if source.get("type") == "base64":
        media = source.get("media_type") or "image/png"
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media};base64,{source.get('data', '')}"},
        }
    if source.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": source.get("url", "")}}
    return None


def _openai_parts(content: Any) -> list[dict[str, Any]]:
    """Convert Anthropic content blocks to OpenAI content parts."""
    parts: list[dict[str, Any]] = []
    for block in _blocks(content):
        if block.get("type") == "text":
            parts.append({"type": "text", "text": str(block.get("text", ""))})
        elif block.get("type") == "image":
            image = _anthropic_image(block)
            if image:
                parts.append(image)
    return parts


def _join_content(parts: list[dict[str, Any]]) -> Any:
    """Collapse text-only parts to a plain string (many providers prefer that)."""
    if not parts:
        return ""
    if all(p.get("type") == "text" for p in parts):
        return "".join(str(p.get("text", "")) for p in parts)
    return parts


# ---------------------------------------------------------------------------
# Anthropic request -> OpenAI request
# ---------------------------------------------------------------------------


def anthropic_request_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("model", "temperature", "top_p", "stream", "user"):
        if key in body and body[key] is not None:
            out[key] = body[key]

    system = body.get("system")
    system_text = _blocks_text(system) if not isinstance(system, str) else system
    messages: list[dict[str, Any]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in _blocks(content):
                kind = block.get("type")
                if kind == "text":
                    text_parts.append(str(block.get("text", "")))
                elif kind == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or new_id("call"),
                            "type": "function",
                            "function": {
                                "name": block.get("name") or "tool",
                                "arguments": json.dumps(
                                    block.get("input") or {}, separators=(",", ":")
                                ),
                            },
                        }
                    )
                # thinking blocks are dropped: most OpenAI-compatible providers
                # reject them and they add tokens without adding signal.
            entry: dict[str, Any] = {"role": "assistant"}
            entry["content"] = "".join(text_parts) if text_parts else ("" if tool_calls else "")
            if tool_calls:
                entry["tool_calls"] = tool_calls
            messages.append(entry)
            continue

        if role == "user":
            text_parts = []
            tool_messages: list[dict[str, Any]] = []
            for block in _blocks(content):
                kind = block.get("type")
                if kind == "text":
                    text_parts.append(str(block.get("text", "")))
                elif kind == "tool_result":
                    text = _tool_result_text(block.get("content"))
                    if block.get("is_error"):
                        text = f"[tool error] {text}"
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id") or "",
                            "content": text,
                        }
                    )
                elif kind == "image":
                    text_parts.append("")  # images handled below via parts
            parts = _openai_parts(content)
            if tool_messages:
                if parts:
                    messages.append({"role": "user", "content": _join_content(parts)})
                messages.extend(tool_messages)
            elif parts:
                messages.append({"role": "user", "content": _join_content(parts)})
            continue

    out["messages"] = messages

    max_tokens = body.get("max_tokens")
    if max_tokens:
        out["max_tokens"] = max_tokens
    if body.get("stop_sequences"):
        out["stop"] = list(body["stop_sequences"])[:4]
    if body.get("metadata", {}).get("user_id"):
        out["user"] = str(body["metadata"]["user_id"])

    tools = body.get("tools")
    if tools:
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
            if isinstance(tool, dict)
        ]
        out["tool_choice"] = _tool_choice_to_openai(body.get("tool_choice"))

    return out


def _tool_choice_to_openai(choice: Any) -> Any:
    if not isinstance(choice, dict):
        return "auto"
    kind = choice.get("type")
    if kind == "any":
        return "required"
    if kind == "tool" and choice.get("name"):
        return {"type": "function", "function": {"name": choice["name"]}}
    if kind == "none":
        return "none"
    return "auto"


# ---------------------------------------------------------------------------
# OpenAI request -> Anthropic request
# ---------------------------------------------------------------------------


def openai_request_to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    system_chunks: list[str] = []
    messages: list[dict[str, Any]] = []

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")

        if role in {"system", "developer"}:
            text = text_of(content)
            if text:
                system_chunks.append(text)
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id") or "",
                "content": [{"type": "text", "text": text_of(content)}],
            }
            _merge_message(messages, "user", [block])
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                blocks.append({"type": "thinking", "thinking": reasoning, "signature": ""})
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                        blocks.append({"type": "text", "text": str(part.get("text", ""))})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or new_id("toolu"),
                        "name": function.get("name") or "tool",
                        "input": parse_args(function.get("arguments")),
                    }
                )
            if blocks:
                _merge_message(messages, "assistant", blocks)
            continue

        # user (or anything else treated as user)
        blocks = _to_anthropic_blocks(content)
        if blocks:
            _merge_message(messages, "user", blocks)

    out: dict[str, Any] = {
        "model": body.get("model"),
        "max_tokens": int(
            body.get("max_tokens") or body.get("max_completion_tokens") or MAX_TOKENS_DEFAULT
        ),
        "messages": messages,
    }
    if system_chunks:
        out["system"] = "\n\n".join(system_chunks)
    for key in ("temperature", "top_p", "stream"):
        if body.get(key) is not None:
            out[key] = body[key]
    if body.get("stop"):
        stop = body["stop"]
        out["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)[:4]
    if body.get("tools"):
        tools = []
        for tool in body["tools"]:
            function = (tool or {}).get("function") or {}
            if not function.get("name"):
                continue
            tools.append(
                {
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        if tools:
            out["tools"] = tools
            out["tool_choice"] = _tool_choice_to_anthropic(body.get("tool_choice"))
    return out


def _merge_message(messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": list(blocks)})


def _tool_choice_to_anthropic(choice: Any) -> dict[str, Any]:
    if isinstance(choice, str):
        if choice == "required":
            return {"type": "any"}
        if choice == "none":
            return {"type": "auto"}
        return {"type": "auto"}
    if isinstance(choice, dict):
        if choice.get("type") == "function":
            name = (choice.get("function") or {}).get("name")
            if name:
                return {"type": "tool", "name": name}
        if choice.get("type") in {"auto", "any", "tool"}:
            return choice
    return {"type": "auto"}


def _to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"text", "input_text"}:
            text = str(part.get("text", ""))
            if text:
                blocks.append({"type": "text", "text": text})
        elif part.get("type") in {"image_url", "input_image"}:
            url = (part.get("image_url") or {}).get("url") or part.get("image_url") or ""
            block = _image_url_to_anthropic(str(url))
            if block:
                blocks.append(block)
    return blocks


def _image_url_to_anthropic(url: str) -> dict[str, Any] | None:
    if not url:
        return None
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = header[5:].split(";")[0] or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def openai_response_to_anthropic(
    response: dict[str, Any],
    *,
    requested_model: str | None = None,
    fallback_prompt: str = "",
) -> dict[str, Any]:
    choices = response.get("choices") or [{}]
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []

    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        content.append({"type": "thinking", "thinking": reasoning, "signature": ""})

    text = text_of(message.get("content"))
    if text:
        content.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls") or []
    for call in tool_calls:
        function = call.get("function") or {}
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id") or new_id("toolu"),
                "name": function.get("name") or "tool",
                "input": parse_args(function.get("arguments")),
            }
        )

    if not content:
        content.append({"type": "text", "text": ""})

    usage_raw = response.get("usage") or {}
    prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
    completion_tokens = int(usage_raw.get("completion_tokens") or 0)
    if not prompt_tokens and fallback_prompt:
        prompt_tokens = estimate_tokens(fallback_prompt)
    if not completion_tokens:
        completion_tokens = estimate_tokens(text)

    finish = choice.get("finish_reason")
    return {
        "id": _anthropic_id(response.get("id")),
        "type": "message",
        "role": "assistant",
        "model": requested_model or response.get("model") or "",
        "content": content,
        "stop_reason": STOP_TO_ANTHROPIC.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cache_read_input_tokens": int(
                (usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            ),
        },
    }


def anthropic_response_to_openai(
    response: dict[str, Any], *, requested_model: str | None = None
) -> dict[str, Any]:
    blocks = response.get("content") or []
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            text_chunks.append(str(block.get("text", "")))
        elif kind == "thinking":
            thinking_chunks.append(str(block.get("thinking", "")))
        elif kind == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or new_id("call"),
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "tool",
                        "arguments": json.dumps(block.get("input") or {}, separators=(",", ":")),
                    },
                }
            )

    usage_raw = response.get("usage") or {}
    usage = {
        "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
        "completion_tokens": int(usage_raw.get("output_tokens") or 0),
        "total_tokens": int(usage_raw.get("input_tokens") or 0)
        + int(usage_raw.get("output_tokens") or 0),
    }
    if usage_raw.get("cache_read_input_tokens"):
        usage["prompt_tokens_details"] = {
            "cached_tokens": int(usage_raw["cache_read_input_tokens"])
        }

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_chunks) if text_chunks else None,
    }
    if thinking_chunks:
        message["reasoning_content"] = "".join(thinking_chunks)
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_reason = response.get("stop_reason")
    return {
        "id": _openai_id(response.get("id")),
        "object": "chat.completion",
        "created": unix_now(),
        "model": requested_model or response.get("model") or "",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls"
                if tool_calls
                else STOP_TO_OPENAI.get(stop_reason, "stop"),
            }
        ],
        "usage": usage,
    }


def _anthropic_id(value: Any) -> str:
    raw = str(value or "")
    if raw.startswith("msg_"):
        return raw
    return f"msg_{raw[-24:] or new_id('')}"


def _openai_id(value: Any) -> str:
    raw = str(value or "")
    if raw.startswith("chatcmpl-"):
        return raw
    return f"chatcmpl-{raw[-24:] or new_id('')}"


def prompt_text(body: dict[str, Any]) -> str:
    """Concatenate prompt text for token estimation fallbacks."""
    chunks: list[str] = []
    system = body.get("system") or body.get("system_prompt")
    if isinstance(system, str):
        chunks.append(system)
    elif system:
        chunks.append(_blocks_text(system))
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            chunks.append(text_of(message.get("content")))
    return "\n".join(chunks)
