"""Model resolution and failover planning.

A client asks for ``claude-sonnet-4-5`` (or ``hexgate/sonnet``, or an explicit
``provider/model``). The router turns that into an ordered list of concrete
provider candidates, honouring tier chains, key rotation, circuit state and the
configured routing strategy.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from hexgate.catalog import tier_order
from hexgate.config import Settings
from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.costs import price_for
from hexgate.secrets import SecretStore

TIER_KEYWORDS = ("fable", "opus", "sonnet", "haiku")
STRATEGIES = ("quality", "fastest", "cheapest", "round_robin")


@dataclass(slots=True)
class Candidate:
    provider: str
    model: str
    api: str
    base_url: str
    key: str | None
    key_index: int = 0

    @property
    def slug(self) -> str:
        return f"{self.provider}/{self.model}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "slug": self.slug,
            "api": self.api,
            "key_index": self.key_index + 1,
        }


@dataclass(slots=True)
class Plan:
    requested: str
    tier: str | None
    source: str
    candidates: list[Candidate]
    skipped: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "tier": self.tier,
            "source": self.source,
            "candidates": [c.as_dict() for c in self.candidates],
            "skipped": self.skipped,
        }


class Router:
    def __init__(
        self,
        settings: Settings,
        secret_store: SecretStore,
        health: HealthRegistry,
        keyring: KeyRing,
    ) -> None:
        self.settings = settings
        self.secrets = secret_store
        self.health = health
        self.keyring = keyring
        self._rr_lock = threading.Lock()
        self._rr_cursor = 0

    # -- public ----------------------------------------------------------
    def plan(self, requested: str) -> list[Candidate]:
        return self.explain(requested).candidates

    def explain(self, requested: str) -> Plan:
        requested = (requested or "").strip()
        skipped: list[dict[str, str]] = []
        refs: list[str] = []
        source = "default"
        tier: str | None = None

        direct = self.split_target(requested)
        if direct is not None:
            source = "direct"
            refs = [f"{direct[0]}/{direct[1]}"]
        else:
            tier = classify(requested)
            chains = self.settings.chains()
            if tier and chains.get(tier):
                source = "tier"
                refs.extend(chains[tier])
            refs.extend(chains.get("default") or [])
            if not refs:
                source = "implicit"
                refs = self._implicit_refs(requested)

        candidates = self._dedupe(self._build(refs, skipped))
        if not candidates:
            return Plan(requested, tier, "none" if refs else "implicit", [], skipped)

        ready = [c for c in candidates if self.health.can_try(c.slug)]
        ordered = self._order(ready or candidates)
        return Plan(requested, tier, source, ordered, skipped)

    def split_target(self, ref: str) -> tuple[str, str] | None:
        """``provider/model`` where ``provider`` is a configured provider id."""
        ref = (ref or "").strip()
        if not ref:
            return None
        ids = sorted(self.settings.providers, key=len, reverse=True)
        for provider_id in ids:
            if ref == provider_id:
                provider = self.settings.providers[provider_id]
                model = provider.default_model or (provider.models[0] if provider.models else "")
                return (provider_id, model) if model else None
            prefix = f"{provider_id}/"
            if ref.startswith(prefix):
                model = ref[len(prefix) :].strip()
                if model:
                    return provider_id, model
        return None

    # -- internals -------------------------------------------------------
    def _build(self, refs: list[str], skipped: list[dict[str, str]]) -> list[Candidate]:
        out: list[Candidate] = []
        for ref in refs:
            split = self.split_target(ref)
            if split is None:
                skipped.append({"ref": ref, "reason": "unknown provider prefix"})
                continue
            provider_id, model = split
            out.extend(self._candidates(provider_id, model, skipped))
        return out

    def _candidates(
        self, provider_id: str, model: str, skipped: list[dict[str, str]]
    ) -> list[Candidate]:
        provider = self.settings.providers.get(provider_id)
        if provider is None:
            skipped.append({"ref": f"{provider_id}/{model}", "reason": "provider not in catalog"})
            return []
        if not provider.enabled:
            skipped.append({"ref": f"{provider_id}/{model}", "reason": "provider disabled"})
            return []
        base_url = (provider.base_url or "").strip()
        if not base_url:
            skipped.append({"ref": f"{provider_id}/{model}", "reason": "base URL not set"})
            return []

        keys = self.secrets.keys(provider_id, provider.env) if provider.requires_key else []
        if provider.requires_key and not keys:
            skipped.append({"ref": f"{provider_id}/{model}", "reason": "no API key"})
            return []
        if not keys:
            keys = [""]

        out: list[Candidate] = []
        for index in self.keyring.order(provider_id, len(keys)):
            out.append(
                Candidate(
                    provider=provider_id,
                    model=model,
                    api=provider.api,
                    base_url=base_url.rstrip("/"),
                    key=keys[index],
                    key_index=index,
                )
            )
        return out

    def _implicit_refs(self, requested: str) -> list[str]:
        """Zero-config fallback: use enabled providers' declared default models."""
        bare = requested if requested and self.split_target(requested) is None else ""
        providers = sorted(
            self.settings.enabled_providers(),
            key=lambda p: (tier_order(p.tier), p.id),
        )
        refs: list[str] = []
        if bare:
            needle = bare.lower()
            for provider in providers:
                for model in provider.models:
                    if model.lower() == needle:
                        refs.append(f"{provider.id}/{model}")
                        break
            if refs:
                return refs
        for provider in providers:
            model = provider.default_model or (provider.models[0] if provider.models else "")
            if not model:
                continue
            refs.append(f"{provider.id}/{model}")
        return refs

    def _dedupe(self, candidates: list[Candidate]) -> list[Candidate]:
        seen: set[tuple[str, str, int]] = set()
        out: list[Candidate] = []
        for candidate in candidates:
            key = (candidate.provider, candidate.model, candidate.key_index)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
        return out

    def _order(self, candidates: list[Candidate]) -> list[Candidate]:
        strategy = (self.settings.routing.strategy or "quality").lower()
        if strategy == "fastest":
            return sorted(candidates, key=lambda c: self.health.latency(c.slug) or 9_999)
        if strategy == "cheapest":

            def cost(candidate: Candidate) -> float:
                price = price_for(candidate.model)
                if price is None:
                    return 9_999
                return price.input + price.output

            return sorted(candidates, key=cost)
        if strategy == "round_robin":
            with self._rr_lock:
                offset = self._rr_cursor % max(len(candidates), 1)
                self._rr_cursor = (self._rr_cursor + 1) % 1_000_000
            return candidates[offset:] + candidates[:offset]
        return candidates


def classify(model: str) -> str | None:
    needle = (model or "").lower()
    if needle.startswith("hexgate/"):
        needle = needle[len("hexgate/") :]
    for tier in TIER_KEYWORDS:
        if tier in needle:
            return tier
    return None
