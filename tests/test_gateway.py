from __future__ import annotations

import json

import httpx
import pytest

from hexgate.core.gateway import GatewayError
from hexgate.protocol.openai import iter_sse, sse

from .conftest import add_provider, completion_body


def openai_json(text: str = "hello", model: str = "test-model") -> httpx.Response:
    return httpx.Response(200, json=completion_body(text, model))


def sse_response(*frames: bytes, model: str = "test-model") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b"".join(frames),
    )


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------


async def test_complete_happy_path_rewrites_model_and_records(settings, store, gateway_factory):
    add_provider(settings, store, "solo", base_url="http://solo.test/v1")
    settings.routing.default_chain = ["solo/test-model"]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return openai_json("hi")

    gateway, telemetry, health, keyring = gateway_factory(handler)
    result = await gateway.complete(
        "openai",
        "claude-sonnet-4-5",
        {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "yo"}]},
    )

    assert result.payload["model"] == "claude-sonnet-4-5"
    assert result.payload["choices"][0]["message"]["content"] == "hi"
    assert seen[0]["model"] == "test-model"
    assert "stream" not in seen[0]

    record = telemetry.recent()[0]
    assert record["ok"] is True
    assert record["provider"] == "solo"
    assert record["prompt_tokens"] == 11
    assert record["attempts"] == 1
    assert record["route"] == ["solo/test-model"]
    assert health.state("solo/test-model") == "closed"


async def test_complete_fails_over_after_server_error(settings, store, gateway_factory):
    add_provider(settings, store, "bad", base_url="http://bad.test/v1")
    add_provider(settings, store, "good", base_url="http://good.test/v1")
    settings.routing.default_chain = ["bad/test-model", "good/test-model"]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "bad.test":
            return httpx.Response(500, json={"error": {"message": "kaboom"}})
        return openai_json("rescued")

    gateway, telemetry, health, _ = gateway_factory(handler)
    result = await gateway.complete(
        "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
    )

    assert calls == ["bad.test", "good.test"]
    assert result.payload["choices"][0]["message"]["content"] == "rescued"
    record = telemetry.recent()[0]
    assert record["attempts"] == 2
    assert record["provider"] == "good"
    assert health.state("bad/test-model") == "open"


async def test_complete_rotates_api_keys_on_401(settings, store, gateway_factory):
    add_provider(settings, store, "multi", key="")
    store.set_keys("multi", ["sk-first", "sk-second"])
    settings.routing.default_chain = ["multi/test-model"]
    auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        auth_headers.append(auth)
        if auth.endswith("sk-first"):
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        return openai_json("second key")

    gateway, telemetry, health, keyring = gateway_factory(handler)
    result = await gateway.complete(
        "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
    )

    assert auth_headers == ["Bearer sk-first", "Bearer sk-second"]
    assert result.payload["choices"][0]["message"]["content"] == "second key"
    # A bad key must not open the route circuit.
    assert health.state("multi/test-model") == "closed"
    assert keyring.snapshot()[0]["provider"] == "multi"
    assert telemetry.recent()[0]["attempts"] == 2


async def test_complete_does_not_retry_plain_bad_request(settings, store, gateway_factory):
    add_provider(settings, store, "strict", base_url="http://strict.test/v1")
    settings.routing.default_chain = ["strict/test-model"]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "temperature out of range"}})

    gateway, telemetry, _, _ = gateway_factory(handler)
    with pytest.raises(GatewayError) as caught:
        await gateway.complete(
            "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
        )

    assert calls == 1
    assert caught.value.status == 400
    assert caught.value.type == "invalid_request_error"
    assert "temperature" in caught.value.message
    assert telemetry.recent()[0]["ok"] is False


async def test_complete_retries_model_not_found_then_succeeds(settings, store, gateway_factory):
    add_provider(settings, store, "a", base_url="http://a.test/v1")
    add_provider(settings, store, "b", base_url="http://b.test/v1")
    settings.routing.default_chain = ["a/test-model", "b/test-model"]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "a.test":
            return httpx.Response(404, json={"error": {"message": "The model does not exist"}})
        return openai_json("ok")

    gateway, _, _, _ = gateway_factory(handler)
    result = await gateway.complete(
        "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
    )

    assert calls == ["a.test", "b.test"]
    assert result.payload["choices"][0]["message"]["content"] == "ok"


async def test_complete_translates_anthropic_upstream_for_openai_client(
    settings, store, gateway_factory
):
    add_provider(settings, store, "native", api="anthropic", base_url="http://native.test/v1")
    settings.routing.default_chain = ["native/claude-3-5-sonnet"]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet",
                "content": [
                    {"type": "thinking", "thinking": "consider"},
                    {"type": "text", "text": "answer"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    gateway, _, _, _ = gateway_factory(handler)
    result = await gateway.complete(
        "openai",
        "sonnet",
        {
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            "max_tokens": 32,
        },
    )

    assert captured["path"] == "/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"]
    assert captured["body"]["system"] == "be brief"
    assert captured["body"]["messages"][0]["role"] == "user"

    message = result.payload["choices"][0]["message"]
    assert message["content"] == "answer"
    assert message["reasoning_content"] == "consider"
    assert result.payload["model"] == "sonnet"
    assert result.payload["usage"]["prompt_tokens"] == 4


async def test_complete_passthrough_for_anthropic_client_and_upstream(
    settings, store, gateway_factory
):
    add_provider(settings, store, "claude", api="anthropic", base_url="http://claude.test/v1")
    settings.routing.default_chain = ["claude/claude-sonnet-4-5"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    gateway, _, _, _ = gateway_factory(handler)
    result = await gateway.complete(
        "anthropic",
        "claude-sonnet-4-5",
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert result.payload["type"] == "message"
    assert result.payload["content"][0]["text"] == "hi"


async def test_complete_network_error_returns_502(settings, store, gateway_factory):
    add_provider(settings, store, "offline", base_url="http://offline.test/v1")
    settings.routing.default_chain = ["offline/test-model"]

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway, telemetry, _, _ = gateway_factory(handler)
    with pytest.raises(GatewayError) as caught:
        await gateway.complete(
            "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
        )

    assert caught.value.status == 502
    assert "ConnectError" in caught.value.message
    assert telemetry.recent()[0]["ok"] is False


async def test_complete_without_route_is_503(settings, gateway_factory):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no upstream should be called")

    gateway, telemetry, _, _ = gateway_factory(handler)
    with pytest.raises(GatewayError) as caught:
        await gateway.complete(
            "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
        )

    assert caught.value.status == 503
    assert caught.value.type == "invalid_request_error"
    assert "No route" in caught.value.message
    assert telemetry.recent()[0]["status"] == 503


async def test_complete_open_circuit_skips_route(settings, store, gateway_factory):
    add_provider(settings, store, "flaky", base_url="http://flaky.test/v1")
    settings.routing.default_chain = ["flaky/test-model"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down"}})

    gateway, _, health, _ = gateway_factory(handler)
    with pytest.raises(GatewayError):
        await gateway.complete(
            "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
        )
    assert health.state("flaky/test-model") == "open"

    with pytest.raises(GatewayError) as caught:
        await gateway.complete(
            "openai", "sonnet", {"messages": [{"role": "user", "content": "hi"}]}
        )
    assert caught.value.status == 503
    assert "No healthy route" in caught.value.message


async def test_complete_rejects_missing_messages(settings, gateway_factory):
    gateway, _, _, _ = gateway_factory(lambda request: openai_json())
    with pytest.raises(GatewayError) as caught:
        await gateway.complete("openai", "sonnet", {"model": "sonnet"})
    assert caught.value.status == 400
    assert "messages" in caught.value.message


async def test_optimizer_shapes_request_before_sending(settings, store, gateway_factory):
    add_provider(settings, store, "shaped", base_url="http://shaped.test/v1")
    settings.routing.default_chain = ["shaped/test-model"]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return openai_json()

    gateway, telemetry, _, _ = gateway_factory(handler)
    await gateway.complete(
        "openai",
        "sonnet",
        {
            "messages": [
                {"role": "system", "content": "rule one"},
                {"role": "system", "content": "rule one"},
                {"role": "user", "content": "go  \n"},
            ]
        },
    )

    assert captured["messages"][0] == {"role": "system", "content": "rule one"}
    assert captured["messages"][1]["content"] == "go\n"
    assert telemetry.recent()[0]["optimized_tokens"] >= 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def test_stream_openai_to_openai_rewrites_and_terminates(settings, store, gateway_factory):
    add_provider(settings, store, "streamer", base_url="http://stream.test/v1")
    settings.routing.default_chain = ["streamer/test-model"]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return sse_response(
            sse(
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": {"content": "Hel"}}],
                }
            ),
            sse(
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": {"content": "lo"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                }
            ),
        )

    gateway, telemetry, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "openai",
        "sonnet",
        {"model": "sonnet", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert result.status == 200
    assert result.headers["x-hexgate-route"] == "streamer/test-model"
    assert result.headers["cache-control"] == "no-cache"
    assert captured["stream"] is True

    frames = [frame async for frame in result.iterator]
    text = b"".join(frames).decode()
    assert '"model":"sonnet"' in text
    assert '"content":"Hel"' in text
    assert text.rstrip().endswith("data: [DONE]")
    assert text.count("data: [DONE]") == 1

    record = telemetry.recent()[0]
    assert record["stream"] is True
    assert record["ok"] is True
    assert record["prompt_tokens"] == 5
    assert record["completion_tokens"] == 2
    assert telemetry.stats()["streams"] == 1


async def test_stream_openai_upstream_to_anthropic_client(settings, store, gateway_factory):
    add_provider(settings, store, "oss", base_url="http://oss.test/v1")
    settings.routing.default_chain = ["oss/test-model"]

    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            sse({"choices": [{"delta": {"content": "Hello"}}]}),
            sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            sse("[DONE]"),
        )

    gateway, telemetry, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "anthropic",
        "claude-sonnet-4-5",
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    frames = [frame async for frame in result.iterator]
    text = b"".join(frames).decode()

    assert text.startswith("event: message_start")
    assert '"text_delta"' in text
    assert '"stop_reason":"end_turn"' in text
    assert "event: message_stop" in text
    assert "data: [DONE]" not in text
    assert telemetry.recent()[0]["ok"] is True


async def test_stream_anthropic_upstream_to_openai_client(settings, store, gateway_factory):
    add_provider(settings, store, "claude", api="anthropic", base_url="http://claude.test/v1")
    settings.routing.default_chain = ["claude/claude-sonnet-4-5"]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return sse_response(
            sse(
                {
                    "type": "message_start",
                    "message": {
                        "id": "m",
                        "model": "claude-sonnet-4-5",
                        "usage": {"input_tokens": 3},
                    },
                },
                "message_start",
            ),
            sse(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "yo"},
                },
                "content_block_delta",
            ),
            sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                },
                "message_delta",
            ),
        )

    gateway, telemetry, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "openai",
        "sonnet",
        {"model": "sonnet", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    frames = [frame async for frame in result.iterator]
    text = b"".join(frames).decode()

    assert captured["model"] == "claude-sonnet-4-5"
    assert captured["max_tokens"] == 8192
    assert captured["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    assert '"content":"yo"' in text
    assert '"finish_reason":"stop"' in text
    assert text.rstrip().endswith("data: [DONE]")
    record = telemetry.recent()[0]
    assert record["stream"] is True
    assert record["prompt_tokens"] == 3


async def test_stream_anthropic_passthrough_rewrites_model(settings, store, gateway_factory):
    add_provider(settings, store, "claude", api="anthropic", base_url="http://claude.test/v1")
    settings.routing.default_chain = ["claude/claude-sonnet-4-5"]

    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            sse(
                {
                    "type": "message_start",
                    "message": {
                        "id": "m",
                        "model": "claude-sonnet-4-5",
                        "usage": {"input_tokens": 1},
                    },
                },
                "message_start",
            ),
            sse({"type": "message_stop"}),
        )

    gateway, _, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "anthropic",
        "claude-opus-4-1",
        {
            "model": "claude-opus-4-1",
            "max_tokens": 8,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    frames = [frame async for frame in result.iterator]
    text = b"".join(frames).decode()
    assert '"model":"claude-opus-4-1"' in text
    assert "event: message_start" in text


async def test_stream_fails_over_on_error_status(settings, store, gateway_factory):
    add_provider(settings, store, "bad", base_url="http://bad.test/v1")
    add_provider(settings, store, "good", base_url="http://good.test/v1")
    settings.routing.default_chain = ["bad/test-model", "good/test-model"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bad.test":
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return sse_response(sse({"choices": [{"delta": {"content": "ok"}}]}), sse("[DONE]"))

    gateway, telemetry, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "openai", "sonnet", {"stream": True, "messages": [{"role": "user", "content": "hi"}]}
    )
    frames = [frame async for frame in result.iterator]

    assert result.headers["x-hexgate-attempts"] == "2"
    assert b"ok" in b"".join(frames)
    assert telemetry.recent()[0]["attempts"] == 2


async def test_stream_without_route_is_503(settings, gateway_factory):
    gateway, _, _, _ = gateway_factory(lambda request: openai_json())
    with pytest.raises(GatewayError) as caught:
        await gateway.stream(
            "openai", "sonnet", {"stream": True, "messages": [{"role": "user", "content": "hi"}]}
        )
    assert caught.value.status == 503


async def test_stream_error_frame_is_emitted_for_client(settings, store, gateway_factory):
    add_provider(settings, store, "broken", base_url="http://broken.test/v1")
    settings.routing.default_chain = ["broken/test-model"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                {"error": {"message": "upstream exploded", "type": "server_error", "code": 500}}
            ),
        )

    gateway, telemetry, _, _ = gateway_factory(handler)
    result = await gateway.stream(
        "anthropic", "sonnet", {"stream": True, "messages": [{"role": "user", "content": "hi"}]}
    )
    frames = [frame async for frame in result.iterator]
    text = b"".join(frames).decode()

    events = [event async for event in iter_sse(_lines(frames))]
    assert any(event == "error" for event, _payload in events)
    assert "upstream exploded" in text
    assert telemetry.recent()[0]["ok"] is False


async def _lines(frames: list[bytes]):
    for frame in frames:
        for line in frame.decode().splitlines(keepends=True):
            yield line
