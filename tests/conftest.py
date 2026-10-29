from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hexgate.config import ProviderConfig, Settings, load_settings
from hexgate.core.gateway import Gateway
from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.core.routing import Router
from hexgate.secrets import SecretStore
from hexgate.telemetry import Telemetry


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("HEXGATE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HEXGATE_PROXY_TOKEN", raising=False)
    monkeypatch.delenv("HEXGATE_ADMIN_TOKEN", raising=False)
    cfg = load_settings()
    cfg.server.port = 8799
    cfg.server.host = "127.0.0.1"
    cfg.telemetry.retain = 50
    # Tests start from a clean slate: no provider is enabled until a test
    # explicitly opts in. Production installs ship free/local providers
    # enabled by default for a zero-config local path.
    for provider in cfg.providers.values():
        provider.enabled = False
    return cfg


@pytest.fixture
def store(settings: Settings) -> SecretStore:
    return SecretStore(settings.secrets_path)


def add_provider(
    settings: Settings,
    store: SecretStore,
    provider_id: str,
    *,
    api: str = "openai",
    base_url: str = "http://upstream.test/v1",
    model: str = "test-model",
    key: str = "sk-test",
) -> ProviderConfig:
    provider = ProviderConfig(
        id=provider_id,
        label=provider_id.title(),
        api=api,
        base_url=base_url,
        enabled=True,
        default_model=model,
        models=[model],
        requires_key=True,
        tier="free",
        env=[],
    )
    settings.providers[provider_id] = provider
    if key:
        existing = store.load()
        store.set_keys(provider_id, [*existing.get(provider_id, []), key])
    return provider


@pytest.fixture
async def gateway_factory(settings: Settings, store: SecretStore):
    created: list[httpx.AsyncClient] = []

    def build(handler) -> tuple[Gateway, Telemetry, HealthRegistry, KeyRing]:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        created.append(client)
        health = HealthRegistry(threshold=1, cooldown=60)
        keyring = KeyRing()
        router = Router(settings, store, health, keyring)
        telemetry = Telemetry(50)
        gateway = Gateway(settings, router, client, telemetry, health, keyring)
        return gateway, telemetry, health, keyring

    yield build

    for client in created:
        await client.aclose()


def completion_body(text: str = "hello", model: str = "test-model") -> dict[str, Any]:
    return {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def sse(payload: dict[str, Any] | str) -> bytes:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {body}\n\n".encode()
