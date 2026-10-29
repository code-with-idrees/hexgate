"""Proxy endpoints: what coding agents actually talk to."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from hexgate.core.gateway import GatewayError
from hexgate.protocol.anthropic import anthropic_request_to_openai, prompt_text
from hexgate.protocol.openai import estimate_tokens

log = logging.getLogger("hexgate.proxy")

router = APIRouter()

TIERS = ("fable", "opus", "sonnet", "haiku")


@router.post("/v1/messages")
async def anthropic_messages(request: Request):
    return await _handle(request, "anthropic")


@router.post("/v1/chat/completions")
async def openai_chat(request: Request):
    return await _handle(request, "openai")


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_error("Body must be JSON", "invalid_request_error"), status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(
            _error("Body must be an object", "invalid_request_error"), status_code=400
        )
    canonical = anthropic_request_to_openai(body)
    return JSONResponse({"input_tokens": estimate_tokens(prompt_text(canonical))})


@router.post("/v1/responses")
async def openai_responses(request: Request):
    return await _handle(request, "responses")


@router.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    chains = settings.chains()
    ids: list[str] = []
    for tier in TIERS:
        if chains.get(tier):
            ids.append(f"hexgate/{tier}")
    if chains.get("default"):
        ids.append("hexgate/default")
    for provider in settings.enabled_providers():
        models: list[str] = []
        if provider.default_model:
            models.append(provider.default_model)
        for model in provider.models:
            if model not in models:
                models.append(model)
        for model in models:
            ids.append(f"{provider.id}/{model}")

    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    for model_id in ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        data.append(
            {
                "id": model_id,
                "object": "model",
                "type": "model",
                "created": 0,
                "owned_by": model_id.split("/")[0],
                "display_name": model_id,
            }
        )
    return JSONResponse({"object": "list", "data": data, "has_more": False})


async def _handle(request: Request, protocol: str):
    state = request.app.state
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            _error("Request body must be valid JSON", "invalid_request_error"), status_code=400
        )
    if not isinstance(body, dict):
        return JSONResponse(
            _error("Request body must be a JSON object", "invalid_request_error"), status_code=400
        )

    requested = str(body.get("model") or "")
    stream = bool(body.get("stream"))
    try:
        if stream:
            result = await state.gateway.stream(protocol, requested, body)
            return StreamingResponse(
                result.iterator,
                status_code=result.status,
                media_type=result.media_type,
                headers=result.headers,
            )
        result = await state.gateway.complete(protocol, requested, body)
        headers = {
            "x-hexgate-route": result.record.slug,
            "x-hexgate-attempts": str(result.record.attempts),
            "x-hexgate-request-id": result.record.id,
        }
        return JSONResponse(result.payload, headers=headers)
    except GatewayError as exc:
        log.warning("%s %s failed: %s", protocol, requested, exc.message)
        return JSONResponse(exc.payload(protocol), status_code=exc.status, headers=exc.headers())
    except Exception as exc:  # noqa: BLE001 - never leak stack traces to clients
        log.exception("unhandled gateway error")
        return JSONResponse(
            _error(f"Gateway error: {type(exc).__name__}", "api_error"), status_code=500
        )


def _error(message: str, kind: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": kind, "code": 400}}
