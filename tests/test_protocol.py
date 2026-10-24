from __future__ import annotations

import json

from hexgate.protocol.anthropic import (
    anthropic_request_to_openai,
    anthropic_response_to_openai,
    openai_request_to_anthropic,
    openai_response_to_anthropic,
    prompt_text,
)
from hexgate.protocol.openai import iter_sse
from hexgate.protocol.streams import (
    anthropic_events_to_openai,
    openai_chunks_to_anthropic,
    rewrite_openai_stream,
)


def collect_sse(frames: list[bytes]) -> str:
    return b"".join(frames).decode()


# ---------------------------------------------------------------------------
# request translation
# ---------------------------------------------------------------------------


def test_anthropic_request_to_openai_full():
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "system": "be terse",
        "stop_sequences": ["STOP"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading"},
                    {"type": "tool_use", "id": "tu1", "name": "read", "input": {"path": "a.py"}},
                    {"type": "thinking", "thinking": "hmm"},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}],
            },
        ],
        "tools": [
            {"name": "read", "description": "read a file", "input_schema": {"type": "object"}}
        ],
        "tool_choice": {"type": "any"},
    }
    out = anthropic_request_to_openai(body)

    assert out["messages"][0] == {"role": "system", "content": "be terse"}
    user = out["messages"][1]
    assert user["role"] == "user"
    assert user["content"][0] == {"type": "text", "text": "look"}
    assert user["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"

    assistant = out["messages"][2]
    assert assistant["content"] == "reading"
    assert assistant["tool_calls"][0]["id"] == "tu1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}

    tool_message = out["messages"][3]
    assert tool_message == {"role": "tool", "tool_call_id": "tu1", "content": "ok"}

    assert out["tools"][0]["function"]["name"] == "read"
    assert out["tool_choice"] == "required"
    assert out["stop"] == ["STOP"]
    assert out["max_tokens"] == 100


def test_anthropic_tool_error_result_is_labelled():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": "boom", "is_error": True}
                ],
            }
        ]
    }
    out = anthropic_request_to_openai(body)
    assert out["messages"][0]["content"].startswith("[tool error]")


def test_openai_request_to_anthropic_merges_and_converts():
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "tool", "tool_call_id": "c2", "content": "more"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
        ],
        "tool_choice": "required",
    }
    out = openai_request_to_anthropic(body)
    assert out["system"] == "sys"
    assert out["max_tokens"] == 64
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][1]["role"] == "assistant"
    assert out["messages"][1]["content"][0]["type"] == "tool_use"
    assert out["messages"][1]["content"][0]["input"] == {"x": 1}
    # consecutive tool results collapse into one user turn with two blocks
    assert out["messages"][2]["role"] == "user"
    assert [block["type"] for block in out["messages"][2]["content"]] == [
        "tool_result",
        "tool_result",
    ]
    assert out["tool_choice"] == {"type": "any"}


def test_openai_image_data_url_to_anthropic():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZZZ"}},
                ],
            }
        ]
    }
    out = openai_request_to_anthropic(body)
    image = out["messages"][0]["content"][1]
    assert image["type"] == "image"
    assert image["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "ZZZ"}


# ---------------------------------------------------------------------------
# response translation
# ---------------------------------------------------------------------------


def test_openai_response_to_anthropic_with_tools():
    response = {
        "id": "chatcmpl-1",
        "model": "upstream",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"path": "a"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }
    out = openai_response_to_anthropic(response, requested_model="claude-sonnet-4-5")
    assert out["type"] == "message"
    assert out["model"] == "claude-sonnet-4-5"
    assert out["stop_reason"] == "tool_use"
    assert out["content"][0]["type"] == "tool_use"
    assert out["content"][0]["input"] == {"path": "a"}
    assert out["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "cache_read_input_tokens": 4,
    }


def test_openai_length_finish_maps_to_max_tokens():
    response = {
        "choices": [{"message": {"content": "cut"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    out = openai_response_to_anthropic(response)
    assert out["stop_reason"] == "max_tokens"


def test_anthropic_response_to_openai_thinking_and_tools():
    response = {
        "id": "msg_1",
        "model": "claude",
        "content": [
            {"type": "thinking", "thinking": "plan"},
            {"type": "text", "text": "doing"},
            {"type": "tool_use", "id": "tu", "name": "f", "input": {"a": 1}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 9, "output_tokens": 4},
    }
    out = anthropic_response_to_openai(response, requested_model="hexgate/sonnet")
    message = out["choices"][0]["message"]
    assert message["content"] == "doing"
    assert message["reasoning_content"] == "plan"
    assert message["tool_calls"][0]["id"] == "tu"
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 9
    assert out["model"] == "hexgate/sonnet"


def test_empty_anthropic_content_still_has_a_block():
    out = openai_response_to_anthropic({"choices": [{"message": {"content": None}}]})
    assert out["content"][0] == {"type": "text", "text": ""}


def test_prompt_text_flattens_system_and_messages():
    text = prompt_text(
        {
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ]
        }
    )
    assert "rules" in text and "hello" in text


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


async def test_openai_stream_to_anthropic_events():
    async def chunks():
        yield {"choices": [{"delta": {"role": "assistant", "content": "Hel"}}]}
        yield {"choices": [{"delta": {"content": "lo"}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    stats: dict = {}
    frames = [
        frame
        async for frame in openai_chunks_to_anthropic(
            chunks(), model="m", prompt_tokens=5, stats=stats
        )
    ]
    text = collect_sse(frames)
    assert text.startswith("event: message_start")
    assert '"text_delta"' in text
    assert '"stop_reason":"end_turn"' in text
    assert "event: message_stop" in text
    assert stats["completion_tokens"] >= 1


async def test_openai_stream_to_anthropic_tool_calls():
    async def chunks():
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "read", "arguments": '{"path"'},
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "a"}'}}]}}
            ]
        }
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}

    frames = [frame async for frame in openai_chunks_to_anthropic(chunks(), model="m")]
    text = collect_sse(frames)
    assert '"type":"tool_use"' in text
    assert '"partial_json"' in text
    assert '"stop_reason":"tool_use"' in text


async def test_anthropic_stream_to_openai():
    async def events():
        yield (
            "message_start",
            {
                "type": "message_start",
                "message": {"id": "msg_1", "model": "m", "usage": {"input_tokens": 6}},
            },
        )
        yield (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        yield (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi"},
            },
        )
        yield "content_block_stop", {"type": "content_block_stop", "index": 0}
        yield (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 2},
            },
        )
        yield "message_stop", {"type": "message_stop"}

    stats: dict = {}
    frames = [
        frame
        async for frame in anthropic_events_to_openai(events(), model="hexgate/sonnet", stats=stats)
    ]
    text = collect_sse(frames)
    assert '"role":"assistant"' in text
    assert '"content":"Hi"' in text
    assert '"finish_reason":"stop"' in text
    assert text.rstrip().endswith("data: [DONE]")
    assert stats["prompt_tokens"] == 6
    assert stats["completion_tokens"] == 2


async def test_rewrite_openai_stream_rewrites_model_and_usage():
    async def chunks():
        yield {
            "id": "x",
            "model": "upstream",
            "choices": [{"delta": {"content": "yo"}, "finish_reason": None}],
        }
        yield {
            "id": "x",
            "model": "upstream",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
        yield "[DONE]"

    stats: dict = {}
    frames = [
        frame async for frame in rewrite_openai_stream(chunks(), model="requested", stats=stats)
    ]
    text = collect_sse(frames)
    assert '"model":"requested"' in text
    assert '"model":"upstream"' not in text
    assert stats["completion_tokens"] == 1
    assert text.count("data: [DONE]") == 1


# ---------------------------------------------------------------------------
# sse parsing
# ---------------------------------------------------------------------------


async def test_iter_sse_handles_crlf_and_comments():
    async def lines():
        for line in [
            "event: ping\r",
            'data: {"a": 1}\r',
            "\r",
            ": comment\r",
            "data: [DONE]\r",
            "\r",
        ]:
            yield line

    events = [event async for event in iter_sse(lines())]
    assert events[0] == ("ping", {"a": 1})
    assert events[1][1] == "[DONE]"
