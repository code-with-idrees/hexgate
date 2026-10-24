from __future__ import annotations

import tomllib

from hexgate.config import apply_patch, dumps, load_settings, save_settings


def test_first_run_writes_config(settings, tmp_path):
    assert settings.config_path.exists()
    parsed = tomllib.loads(settings.config_path.read_text(encoding="utf-8"))
    assert parsed["server"]["port"] == 8787
    assert parsed["routing"]["strategy"] == "quality"


def test_roundtrip_preserves_chains(settings):
    settings.routing.default_chain = ["groq/llama-3.3-70b-versatile", "cerebras/llama-3.3-70b"]
    settings.routing.sonnet_chain = ["openrouter/anthropic/claude-sonnet-4.5"]
    save_settings(settings)
    reloaded = load_settings(settings.home, create=False)
    assert reloaded.routing.default_chain == settings.routing.default_chain
    assert reloaded.routing.sonnet_chain == settings.routing.sonnet_chain


def test_provider_overrides_roundtrip(settings):
    settings.providers["groq"].base_url = "https://example.test/v1"
    settings.providers["groq"].models = ["a", "b"]
    settings.providers["groq"].enabled = False
    save_settings(settings)
    reloaded = load_settings(settings.home, create=False)
    assert reloaded.providers["groq"].base_url == "https://example.test/v1"
    assert reloaded.providers["groq"].models == ["a", "b"]
    assert reloaded.providers["groq"].enabled is False
    # untouched catalog defaults still load
    assert reloaded.providers["cerebras"].base_url == "https://api.cerebras.ai/v1"


def test_env_overrides(settings, monkeypatch):
    monkeypatch.setenv("HEXGATE_PORT", "9555")
    monkeypatch.setenv("HEXGATE_STRATEGY", "fastest")
    monkeypatch.setenv("HEXGATE_CHAIN", "a/b, c/d")
    reloaded = load_settings(settings.home, create=False)
    assert reloaded.server.port == 9555
    assert reloaded.routing.strategy == "fastest"
    assert reloaded.routing.default_chain == ["a/b", "c/d"]


def test_tokens_are_masked_in_public_dict(settings):
    settings.auth.proxy_token = "supersecret1234"
    settings.auth.admin_token = "adminsecret9999"
    public = settings.to_dict()
    assert public["auth"]["proxy_token"] == "supe…1234"
    assert "adminsecret9999" not in str(public)
    raw = settings.to_dict(include_tokens=True)
    assert raw["auth"]["proxy_token"] == "supersecret1234"


def test_apply_patch_ignores_masked_tokens(settings):
    settings.auth.proxy_token = "realtoken123456"
    save_settings(settings)
    apply_patch(settings, {"auth": {"proxy_token": "real…3456"}})
    reloaded = load_settings(settings.home, create=False)
    assert reloaded.auth.proxy_token == "realtoken123456"


def test_apply_patch_persists_provider_fields(settings):
    apply_patch(
        settings,
        {
            "routing": {"strategy": "cheapest", "max_attempts": 7},
            "providers": {"groq": {"enabled": False, "default_model": "x"}},
        },
    )
    reloaded = load_settings(settings.home, create=False)
    assert reloaded.routing.strategy == "cheapest"
    assert reloaded.routing.max_attempts == 7
    assert reloaded.providers["groq"].enabled is False
    assert reloaded.providers["groq"].default_model == "x"


def test_toml_dumps_escapes_strings():
    text = dumps({"server": {"host": 'a"b', "port": 1}, "x": ["a", "b"]})
    parsed = tomllib.loads(text)
    assert parsed["server"]["host"] == 'a"b'
    assert parsed["x"] == ["a", "b"]
