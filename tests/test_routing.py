from __future__ import annotations

from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.core.routing import Router, classify

from .conftest import add_provider


def make_router(settings, store, *, threshold: int = 3) -> Router:
    return Router(
        settings,
        store,
        HealthRegistry(threshold=threshold, cooldown=30),
        KeyRing(),
    )


def test_classify_tiers():
    assert classify("claude-sonnet-4-5") == "sonnet"
    assert classify("claude-3-5-haiku-20241022") == "haiku"
    assert classify("hexgate/opus") == "opus"
    assert classify("claude-opus-4-1") == "opus"
    assert classify("gpt-4o-mini") is None
    assert classify("") is None


def test_direct_target_parses_provider_and_model(settings, store):
    add_provider(settings, store, "groq", model="llama-3.3-70b-versatile")
    router = make_router(settings, store)
    plan = router.explain("groq/llama-3.3-70b-versatile")
    assert plan.source == "direct"
    assert [c.slug for c in plan.candidates] == ["groq/llama-3.3-70b-versatile"]


def test_direct_target_with_nested_model_path(settings, store):
    add_provider(settings, store, "openrouter", model="anthropic/claude-sonnet-4.5")
    router = make_router(settings, store)
    plan = router.explain("openrouter/anthropic/claude-sonnet-4.5")
    assert plan.candidates[0].provider == "openrouter"
    assert plan.candidates[0].model == "anthropic/claude-sonnet-4.5"


def test_tier_chain_then_default(settings, store):
    add_provider(settings, store, "groq", model="fast")
    add_provider(settings, store, "cerebras", model="fallback")
    settings.routing.sonnet_chain = ["groq/fast"]
    settings.routing.default_chain = ["cerebras/fallback"]
    router = make_router(settings, store)
    plan = router.explain("claude-sonnet-4-5")
    assert plan.source == "tier"
    assert [c.slug for c in plan.candidates] == ["groq/fast", "cerebras/fallback"]


def test_missing_key_is_skipped_with_reason(settings, store):
    add_provider(settings, store, "groq", model="m", key="sk-1")
    add_provider(settings, store, "cerebras", model="m", key="")
    settings.routing.default_chain = ["groq/m", "cerebras/m"]
    router = make_router(settings, store)
    plan = router.explain("anything")
    assert [c.slug for c in plan.candidates] == ["groq/m"]
    assert any(
        item["ref"] == "cerebras/m" and item["reason"] == "no API key" for item in plan.skipped
    )


def test_disabled_provider_is_skipped(settings, store):
    provider = add_provider(settings, store, "groq", model="m")
    provider.enabled = False
    settings.routing.default_chain = ["groq/m"]
    router = make_router(settings, store)
    plan = router.explain("m")
    assert plan.candidates == []
    assert plan.skipped[0]["reason"] == "provider disabled"


def test_multiple_keys_produce_multiple_candidates(settings, store):
    add_provider(settings, store, "groq", model="m", key="")
    store.set_keys("groq", ["a", "b"])
    settings.routing.default_chain = ["groq/m"]
    plan = make_router(settings, store).explain("m")
    assert len(plan.candidates) == 2
    assert {c.key_index for c in plan.candidates} == {0, 1}
    assert {c.key for c in plan.candidates} == {"a", "b"}


def test_cheapest_strategy_orders_by_price(settings, store):
    add_provider(settings, store, "groq", model="claude-sonnet-4-5")
    add_provider(settings, store, "cerebras", model="gpt-4o-mini")
    settings.routing.strategy = "cheapest"
    settings.routing.default_chain = ["groq/claude-sonnet-4-5", "cerebras/gpt-4o-mini"]
    plan = make_router(settings, store).explain("m")
    assert plan.candidates[0].slug == "cerebras/gpt-4o-mini"


def test_open_circuit_is_deprioritised(settings, store):
    add_provider(settings, store, "groq", model="m")
    add_provider(settings, store, "cerebras", model="m")
    settings.routing.default_chain = ["groq/m", "cerebras/m"]
    router = make_router(settings, store, threshold=1)
    router.health.record_failure("groq/m", "500")
    plan = router.explain("m")
    assert [c.slug for c in plan.candidates] == ["cerebras/m"]


def test_zero_config_uses_enabled_defaults(settings, store):
    for provider in settings.providers.values():
        provider.enabled = provider.id == "groq"
    add_provider(settings, store, "groq", model="llama-3.3-70b-versatile")
    plan = make_router(settings, store).explain("claude-sonnet-4-5")
    assert plan.source == "implicit"
    assert plan.candidates[0].slug == "groq/llama-3.3-70b-versatile"


def test_bare_model_prefers_provider_that_declares_it(settings, store):
    for provider in settings.providers.values():
        provider.enabled = provider.id in {"groq", "cerebras"}
    add_provider(settings, store, "groq", model="nothing")
    add_provider(settings, store, "cerebras", model="gpt-4o-mini")
    plan = make_router(settings, store).explain("gpt-4o-mini")
    assert plan.candidates[0].provider == "cerebras"
    assert plan.candidates[0].model == "gpt-4o-mini"


def test_unknown_model_falls_back_to_implicit_defaults(settings, store):
    for provider in settings.providers.values():
        provider.enabled = False
    add_provider(settings, store, "groq", model="m")
    plan = make_router(settings, store).explain("nope/model")
    assert plan.candidates[0].slug == "groq/m"
