from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hexgate.protocol.responses import (
    chat_response_to_responses,
    openai_chunks_to_responses,
    responses_request_to_chat,
)

# ---------------------------------------------------------------------------
# Request translation: Responses -> OpenAI chat
# ---------------------------------------------------------------------------


def test_responses_request_to_chat_simple_text():
    body = {
        "model": "gpt-5",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "be terse"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
        ],
        "stream": False,
    }
    out = responses_request_to_chat(body)

    assert out["model"] == "gpt-5"
    assert out["stream"] is False
    assert out["messages"][0] == {"role": "system", "content": "be terse"}
    assert out["messages"][1] == {"role": "user", "content": "hello"}


def test_responses_request_to_chat_function_call_and_output():
    body = {
        "model": "gpt-5",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": '{"cmd": "ls"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "file1.txt\nfile2.txt",
            },
            {"type": "message", "role": "user", "content": "done"},
        ],
    }
    out = responses_request_to_chat(body)

    assert out["messages"][0]["role"] == "assistant"
    assert out["messages"][0]["tool_calls"][0]["id"] == "call_1"
    assert out["messages"][0]["tool_calls"][0]["function"]["name"] == "exec_command"
    assert out["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "file1.txt\nfile2.txt",
    }
    assert out["messages"][2] == {"role": "user", "content": "done"}


def test_responses_request_to_chat_tools_and_tool_choice():
    body = {
        "model": "gpt-5",
        "input": [{"type": "message", "role": "user", "content": "hi"}],
        "tools": [
            {"type": "function", "function": {"name": "read", "parameters": {}}},
            {
                "type": "namespace",
                "tools": [{"type": "function", "function": {"name": "grep", "parameters": {}}}],
            },
        ],
        "tool_choice": "required",
        "stream": True,
    }
    out = responses_request_to_chat(body)

    assert out["stream"] is True
    assert out["tool_choice"] == "required"
    names = [t["function"]["name"] for t in out["tools"]]
    assert names == ["read", "grep"]


def test_responses_request_to_chat_drops_unknown_items():
    body = {
        "model": "gpt-5",
        "input": [
            {"type": "reasoning", "id": "r1"},
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "web_search_call", "id": "w1"},
        ],
    }
    out = responses_request_to_chat(body)
    assert out["messages"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Non-streaming response: OpenAI chat -> Responses
# ---------------------------------------------------------------------------


def test_chat_response_to_responses_text():
    payload = {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello world"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    out = chat_response_to_responses(payload, requested_model="gpt-5")

    assert out["object"] == "response"
    assert out["model"] == "gpt-5"
    assert out["status"] == "completed"
    assert out["output"][0] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hello world"}],
        "status": "completed",
    }
    assert out["usage"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


def test_chat_response_to_responses_tool_calls():
    payload = {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "exec_command", "arguments": '{"cmd":"ls"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
    }
    out = chat_response_to_responses(payload, requested_model="gpt-5")

    fc = out["output"][0]
    assert fc["type"] == "function_call"
    assert fc["call_id"] == "call_1"
    assert fc["name"] == "exec_command"
    assert fc["arguments"] == '{"cmd":"ls"}'
    assert fc["status"] == "completed"


# ---------------------------------------------------------------------------
# Streaming: OpenAI chunks -> Responses SSE
# ---------------------------------------------------------------------------


def _openai_chunk(content: str, *, model="test-model", usage=None) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    }
    if content:
        chunk["choices"][0]["delta"]["content"] = content
    if usage is not None:
        chunk["usage"] = usage
    return chunk


async def _collect(async_iter) -> list[bytes]:
    out: list[bytes] = []
    async for item in async_iter:
        out.append(item)
    return out


@pytest.mark.asyncio
async def test_openai_chunks_to_responses_streaming():
    chunks = [
        _openai_chunk("Hello"),
        _openai_chunk(" world"),
        _openai_chunk("", usage={"completion_tokens": 7, "prompt_tokens": 11, "total_tokens": 18}),
    ]

    async def source():
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n".encode()

    frames = await _collect(openai_chunks_to_responses(source(), model="gpt-5", prompt_tokens=11))
    text = b"".join(frames).decode()

    assert "event: response.created" in text
    assert "event: response.output_item.added" in text
    assert "event: response.output_text.delta" in text
    assert "event: response.output_item.done" in text
    assert "event: response.completed" in text

    # The two text deltas should appear.
    deltas = [segment for segment in text.split("\n") if segment.startswith("data: ")]
    delta_texts = []
    for segment in deltas:
        try:
            payload = json.loads(segment[6:])
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "response.output_text.delta":
            delta_texts.append(payload.get("delta", ""))
    assert delta_texts == ["Hello", " world"]

    completed = [segment for segment in text.split("\n") if segment.startswith("data: ")]
    for segment in completed:
        try:
            payload = json.loads(segment[6:])
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "response.completed":
            assert payload["response"]["usage"]["total_tokens"] == 18
            break
    else:
        raise AssertionError("response.completed not found")


@pytest.mark.asyncio
async def test_openai_chunks_to_responses_empty():
    frames = await _collect(
        openai_chunks_to_responses(_empty_source(), model="gpt-5", prompt_tokens=0)
    )
    text = b"".join(frames).decode()
    assert "event: response.completed" in text
    assert "event: response.created" in text


async def _empty_source():
    if False:
        yield b""  # pragma: no cover - makes this an async generator


# ---------------------------------------------------------------------------
# End-to-end: gateway /v1/responses through MockTransport
# ---------------------------------------------------------------------------


def _upstream_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    assert body["model"] == "gpt-5"
    assert any(m.get("role") == "user" for m in body["messages"])
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-xyz",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-5",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "pong"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


@pytest.mark.asyncio
async def test_gateway_responses_complete(settings, gateway_factory):
    gateway, _, _, _ = gateway_factory(_upstream_handler)
    # Add a provider with a default chain so the router finds a route.
    from hexgate.config import ProviderConfig
    from hexgate.secrets import SecretStore

    store = SecretStore(settings.secrets_path)
    provider = ProviderConfig(
        id="testprov",
        label="Test Provider",
        api="openai",
        base_url="http://upstream.test/v1",
        enabled=True,
        default_model="gpt-5",
        models=["gpt-5"],
        requires_key=True,
        env=[],
    )
    settings.providers["testprov"] = provider
    store.set_keys("testprov", ["sk-test"])
    settings.routing.default_chain = ["testprov/gpt-5"]

    body = {
        "model": "gpt-5",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ping"}]}
        ],
        "stream": False,
    }
    result = await gateway.complete("responses", "gpt-5", body)

    assert result.payload["object"] == "response"
    assert result.payload["model"] == "gpt-5"
    assert result.payload["output"][0]["content"][0]["text"] == "pong"
    assert result.payload["usage"]["total_tokens"] == 5
    assert result.record.ok is True
    assert result.record.protocol == "responses"


@pytest.mark.asyncio
async def test_gateway_responses_stream(settings, gateway_factory):
    async def stream_handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            f"data: {json.dumps(_openai_chunk('streaming'))}\n\n",
            f"data: {json.dumps(_openai_chunk(' response'))}\n\n",
            "data: [DONE]\n\n",
        ]
        return httpx.Response(200, content="".join(chunks).encode())

    gateway, _, _, _ = gateway_factory(stream_handler)

    from hexgate.config import ProviderConfig
    from hexgate.secrets import SecretStore

    store = SecretStore(settings.secrets_path)
    provider = ProviderConfig(
        id="testprov",
        label="Test Provider",
        api="openai",
        base_url="http://upstream.test/v1",
        enabled=True,
        default_model="gpt-5",
        models=["gpt-5"],
        requires_key=True,
        env=[],
    )
    settings.providers["testprov"] = provider
    store.set_keys("testprov", ["sk-test"])
    settings.routing.default_chain = ["testprov/gpt-5"]

    body = {
        "model": "gpt-5",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "stream"}],
            }
        ],
        "stream": True,
    }
    result = await gateway.stream("responses", "gpt-5", body)

    frames = await _collect(result.iterator)
    text = b"".join(frames).decode()
    assert "event: response.created" in text
    assert "event: response.output_text.delta" in text
    assert "event: response.completed" in text
    # No error frame should appear in a successful stream.
    assert "event: response.failed" not in text
