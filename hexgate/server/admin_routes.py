"""Admin API used by the dashboard and the CLI."""

from __future__ import annotations

import platform
import sys
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from hexgate import __version__
from hexgate.config import TIERS
from hexgate.server.state import refresh, settings_of

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    state = request.app.state
    settings = settings_of(request.app)
    configured = 0
    for provider in settings.enabled_providers():
        if not provider.requires_key or state.secrets.keys(provider.id, provider.env):
            configured += 1
    return {
        "version": __version__,
        "uptime_seconds": round(time.time() - state.started_at, 1),
        "home": str(settings.home),
        "config_path": str(settings.config_path),
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "local_url": f"http://{settings.server.host}:{settings.server.port}",
        },
        "security": {
            "proxy_auth": bool(settings.auth.proxy_token),
            "admin_auth": bool(settings.auth.admin_token),
            "allow_remote": settings.auth.allow_remote,
        },
        "strategy": settings.routing.strategy,
        "providers": {
            "total": len(settings.providers),
            "enabled": len(settings.enabled_providers()),
            "configured": configured,
        },
        "first_run": configured == 0,
        "stats": state.telemetry.stats(86_400),
        "recent": state.telemetry.recent(8),
        "circuits": state.health.snapshot()[:6],
        "optimizer": state.telemetry.stats(86_400)["optimized_tokens"],
    }


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    state = request.app.state
    settings = settings_of(request.app)
    circuits = {row["slug"].split("/")[0]: row for row in state.health.snapshot()}
    rows = []
    for provider in sorted(settings.providers.values(), key=lambda p: (p.tier, p.label.lower())):
        keys = state.secrets.masked(provider.id, provider.env)
        rows.append(
            {
                **provider.public(),
                "keys": keys,
                "key_count": len(keys),
                "configured": bool(keys) or not provider.requires_key,
                "circuit": circuits.get(provider.id),
            }
        )
    return {"providers": rows}


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, request: Request) -> dict[str, Any]:
    body = await _body(request)
    settings = settings_of(request.app)
    if provider_id not in settings.providers:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    allowed = {"enabled", "base_url", "api", "models", "default_model", "label", "headers"}
    patch = {k: v for k, v in body.items() if k in allowed}
    if "models" in patch and isinstance(patch["models"], str):
        patch["models"] = [line.strip() for line in patch["models"].splitlines() if line.strip()]
    if "api" in patch and patch["api"] not in {"openai", "anthropic"}:
        return JSONResponse({"error": "api must be 'openai' or 'anthropic'"}, status_code=400)
    refresh(request.app, {"providers": {provider_id: patch}})
    return {"ok": True, "provider": settings.providers[provider_id].public()}


@router.put("/providers/{provider_id}/keys")
async def set_provider_keys(provider_id: str, request: Request) -> dict[str, Any]:
    body = await _body(request)
    settings = settings_of(request.app)
    provider = settings.providers.get(provider_id)
    if provider is None:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    keys = body.get("keys", [])
    if isinstance(keys, str):
        keys = [keys]
    if not isinstance(keys, list):
        return JSONResponse({"error": "keys must be a list"}, status_code=400)
    cleaned = [str(k).strip() for k in keys if str(k).strip()]
    request.app.state.secrets.set_keys(provider_id, cleaned)
    if cleaned and not provider.enabled:
        # Adding a key is an explicit "use this provider" signal.
        refresh(request.app, {"providers": {provider_id: {"enabled": True}}})
    return {"ok": True, "keys": request.app.state.secrets.masked(provider_id, provider.env)}


@router.delete("/providers/{provider_id}/keys")
async def delete_provider_keys(provider_id: str, request: Request) -> dict[str, Any]:
    request.app.state.secrets.remove(provider_id)
    return {"ok": True, "keys": []}


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, request: Request) -> dict[str, Any]:
    settings = settings_of(request.app)
    provider = settings.providers.get(provider_id)
    if provider is None:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    if "{account_id}" in provider.base_url:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": 0,
            "detail": "Replace {account_id} in the Base URL with your Cloudflare account ID.",
        }
    if not provider.base_url:
        return {"ok": False, "status": 0, "latency_ms": 0, "detail": "Base URL is not set."}
    keys = request.app.state.secrets.keys(provider.id, provider.env)
    if provider.requires_key and not keys:
        return {"ok": False, "status": 0, "latency_ms": 0, "detail": "Add an API key first."}

    started = time.perf_counter()
    try:
        response = await request.app.state.http.get(
            f"{provider.base_url.rstrip('/')}/models",
            headers=_auth_headers(provider, keys[0] if keys else ""),
            timeout=httpx.Timeout(12.0, connect=6.0),
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"{type(exc).__name__}: {exc}",
        }

    latency = round((time.perf_counter() - started) * 1000, 1)
    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "latency_ms": latency,
            "detail": _message(response.text)[:400],
        }
    models = _models_from(response)
    return {
        "ok": True,
        "status": response.status_code,
        "latency_ms": latency,
        "detail": f"Reachable. {len(models)} models reported.",
        "models": models[:200],
    }


@router.get("/providers/{provider_id}/models")
async def discover_models(provider_id: str, request: Request, save: bool = False) -> dict[str, Any]:
    settings = settings_of(request.app)
    provider = settings.providers.get(provider_id)
    if provider is None:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    if not provider.base_url:
        return JSONResponse({"error": "Base URL is not set"}, status_code=400)
    keys = request.app.state.secrets.keys(provider.id, provider.env)
    if provider.requires_key and not keys:
        return JSONResponse({"error": "Add an API key first"}, status_code=400)
    try:
        response = await request.app.state.http.get(
            f"{provider.base_url.rstrip('/')}/models",
            headers=_auth_headers(provider, keys[0] if keys else ""),
            timeout=httpx.Timeout(15.0, connect=6.0),
        )
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
    if response.status_code >= 400:
        return JSONResponse(
            {"error": _message(response.text)[:400], "status": response.status_code},
            status_code=502,
        )
    models = _models_from(response)
    if save and models:
        refresh(request.app, {"providers": {provider_id: {"models": models[:200]}}})
    return {"models": models, "saved": bool(save and models)}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@router.get("/routes/plan")
async def route_plan(request: Request, model: str = "") -> dict[str, Any]:
    settings = settings_of(request.app)
    plan = request.app.state.router.explain(model).as_dict()
    return {"plan": plan, "chains": settings.chains(), "strategy": settings.routing.strategy}


@router.get("/routes/chains")
async def route_chains(request: Request) -> dict[str, Any]:
    settings = settings_of(request.app)
    return {"chains": settings.chains(), "routing": _routing_dict(settings)}


@router.put("/routes/chains")
async def update_chains(request: Request) -> dict[str, Any]:
    body = await _body(request)
    patch: dict[str, Any] = {}
    chains = body.get("chains")
    if isinstance(chains, dict):
        for tier in (*TIERS, "default"):
            if tier in chains:
                value = chains[tier]
                if isinstance(value, str):
                    value = [
                        line.strip()
                        for line in value.replace(",", "\n").splitlines()
                        if line.strip()
                    ]
                patch[f"{tier}_chain"] = list(value or [])
    routing = body.get("routing")
    if isinstance(routing, dict):
        patch.update(routing)
    settings = refresh(request.app, {"routing": patch})
    return {"ok": True, "chains": settings.chains(), "routing": _routing_dict(settings)}


@router.get("/routes/models")
async def route_models(request: Request) -> dict[str, Any]:
    settings = settings_of(request.app)
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for provider in settings.enabled_providers():
        candidates = list(provider.models)
        if provider.default_model and provider.default_model not in candidates:
            candidates.insert(0, provider.default_model)
        for model in candidates:
            slug = f"{provider.id}/{model}"
            if slug in seen:
                continue
            seen.add(slug)
            models.append({"slug": slug, "provider": provider.id, "tier": provider.tier})
    return {"models": models}


# ---------------------------------------------------------------------------
# Health, requests, stats
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    settings = settings_of(request.app)
    return {
        "circuits": state.health.snapshot(),
        "keys": state.keyring.snapshot(),
        "threshold": settings.routing.failure_threshold,
        "cooldown_seconds": settings.routing.cooldown_seconds,
    }


@router.post("/health/reset")
async def reset_health(request: Request) -> dict[str, Any]:
    body = await _body(request, optional=True)
    route = body.get("route") if isinstance(body, dict) else None
    request.app.state.health.reset(route)
    return {"ok": True}


@router.get("/requests")
async def requests_list(
    request: Request,
    limit: int = 100,
    ok: bool | None = None,
    provider: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    rows = request.app.state.telemetry.recent(
        limit=min(max(limit, 1), 500), ok=ok, provider=provider, query=q
    )
    return {"requests": rows, "count": len(rows)}


@router.delete("/requests")
async def clear_requests(request: Request) -> dict[str, Any]:
    request.app.state.telemetry.clear()
    return {"ok": True}


@router.get("/stats")
async def stats(request: Request, window: int = 86_400) -> dict[str, Any]:
    return request.app.state.telemetry.stats(max(window, 0))


# ---------------------------------------------------------------------------
# Playground
# ---------------------------------------------------------------------------


@router.post("/playground")
async def playground(request: Request) -> dict[str, Any]:
    body = await _body(request)
    model = str(body.get("model") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    system = str(body.get("system") or "").strip()
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if prompt:
        messages.append({"role": "user", "content": prompt})
    if not messages:
        return JSONResponse({"error": "Provide a prompt"}, status_code=400)

    payload: dict[str, Any] = {
        "messages": messages,
        "max_tokens": int(body.get("max_tokens") or 512),
    }
    if body.get("temperature") is not None:
        payload["temperature"] = float(body["temperature"])

    try:
        result = await request.app.state.gateway.complete("openai", model, payload)
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI
        message = getattr(exc, "message", str(exc))
        return JSONResponse({"error": message}, status_code=getattr(exc, "status", 500))

    data = result.payload
    choices = data.get("choices") or [{}]
    message = choices[0].get("message") or {} if choices else {}
    text = message.get("content") or ""
    if isinstance(text, list):
        text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
    if not text and message.get("reasoning_content"):
        text = message["reasoning_content"]
    return {
        "text": text,
        "tool_calls": message.get("tool_calls") or [],
        "usage": data.get("usage") or {},
        "record": result.record.as_dict(),
        "route": result.record.slug,
    }


# ---------------------------------------------------------------------------
# Config & diagnostics
# ---------------------------------------------------------------------------


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    settings = settings_of(request.app)
    return {
        "config": settings.to_dict(),
        "catalog": _catalog(),
    }


@router.put("/config")
async def put_config(request: Request) -> dict[str, Any]:
    body = await _body(request)
    settings = refresh(request.app, body)
    return {
        "ok": True,
        "config": settings.to_dict(),
        "restart_required": any(key in body for key in ("server",)),
    }


@router.get("/config/export", response_class=PlainTextResponse)
async def export_config(request: Request) -> PlainTextResponse:
    settings = settings_of(request.app)
    text = settings.config_path.read_text(encoding="utf-8") if settings.config_path.exists() else ""
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    state = request.app.state
    settings = settings_of(request.app)
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "home": str(settings.home),
        "config_exists": settings.config_path.exists(),
        "config_path": str(settings.config_path),
        "secrets_path": str(settings.secrets_path),
        "secrets_present": settings.secrets_path.exists(),
        "providers_with_keys": len(state.secrets.provider_ids()),
        "proxy_auth": bool(settings.auth.proxy_token),
        "admin_auth": bool(settings.auth.admin_token),
        "allow_remote": settings.auth.allow_remote,
        "optimizer": settings.optimizer.enabled,
        "retain": settings.telemetry.retain,
        "uptime_seconds": round(time.time() - state.started_at, 1),
        "http_proxy": bool(__import__("os").environ.get("HTTPS_PROXY")),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _body(request: Request, *, optional: bool = False) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {} if optional else {}
    return data if isinstance(data, dict) else {}


def _routing_dict(settings: Any) -> dict[str, Any]:
    r = settings.routing
    return {
        "strategy": r.strategy,
        "max_attempts": r.max_attempts,
        "timeout": r.timeout,
        "connect_timeout": r.connect_timeout,
        "cooldown_seconds": r.cooldown_seconds,
        "failure_threshold": r.failure_threshold,
    }


def _auth_headers(provider: Any, key: str) -> dict[str, str]:
    headers: dict[str, str] = {"accept": "application/json"}
    if provider.api == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["authorization"] = f"Bearer {key}"
    if provider.headers:
        headers.update(provider.headers)
    return headers


def _models_from(response: httpx.Response) -> list[str]:
    try:
        data = response.json()
    except ValueError:
        return []
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    models: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            identifier = row.get("id") or row.get("name") or row.get("model")
            if identifier:
                models.append(str(identifier))
        elif isinstance(row, str):
            models.append(row)
    return sorted(set(models))


def _message(text: str) -> str:
    try:
        import json

        data = json.loads(text)
    except (ValueError, TypeError):
        return text.strip()
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")
        if isinstance(error, str):
            return error
        for key in ("message", "detail", "msg"):
            if data.get(key):
                return str(data[key])
    return text.strip()


def _catalog() -> list[dict[str, Any]]:
    from hexgate.catalog import load_catalog

    return [
        {key: entry.get(key) for key in ("id", "label", "tier", "signup", "docs", "notes", "api")}
        for entry in load_catalog()
    ]
