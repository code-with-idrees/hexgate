"""The gateway: one request in, the best available provider out.

Responsibilities:
* canonicalise the client's wire format
* shape the prompt (optimizer)
* walk the failover plan, respecting circuit breakers and key cooldowns
* translate the response (and the stream) back to the client's dialect
* record telemetry and update health
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from hexgate.config import Settings
from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.core.routing import Candidate, Router
from hexgate.core.upstream import prepare
from hexgate.costs import estimate_cost, savings
from hexgate.optimizer import OptimizerReport, optimize
from hexgate.protocol.anthropic import (
    anthropic_request_to_openai,
    anthropic_response_to_openai,
    openai_response_to_anthropic,
    prompt_text,
)
from hexgate.protocol.openai import estimate_tokens, iter_sse, sse
from hexgate.protocol.responses import (
    chat_response_to_responses,
    openai_chunks_to_responses,
    responses_request_to_chat,
)
from hexgate.protocol.streams import (
    anthropic_events_to_openai,
    openai_chunks_to_anthropic,
    rewrite_anthropic_stream,
    rewrite_openai_stream,
    sse_error,
)
from hexgate.telemetry import RequestRecord, Telemetry

log = logging.getLogger("hexgate.gateway")

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 523, 524, 529}
MODEL_ERROR_HINTS = (
    "model",
    "not found",
    "does not exist",
    "no such",
    "unsupported",
    "invalid model",
    "deployment",
)


class GatewayError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        type: str = "api_error",
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = type
        self.detail = detail
        self.retry_after = retry_after

    def payload(self, protocol: str) -> dict[str, Any]:
        if protocol == "anthropic":
            return {"type": "error", "error": {"type": self.type, "message": self.message}}
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "code": self.status,
            }
        }

    def headers(self) -> dict[str, str]:
        return {"retry-after": str(int(self.retry_after))} if self.retry_after else {}


@dataclass(slots=True)
class CompleteResult:
    payload: dict[str, Any]
    record: RequestRecord
    status: int = 200


@dataclass(slots=True)
class StreamResult:
    iterator: AsyncIterator[bytes]
    media_type: str
    status: int
    headers: dict[str, str]


@dataclass(slots=True)
class _StreamContext:
    protocol: str
    requested: str
    candidate: Candidate
    attempts: int
    route: list[str]
    report: OptimizerReport
    started: float
    wall: float
    stats: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    ttft_ms: float | None = None


class Gateway:
    def __init__(
        self,
        settings: Settings,
        router: Router,
        http: httpx.AsyncClient,
        telemetry: Telemetry,
        health: HealthRegistry,
        keyring: KeyRing,
    ) -> None:
        self.settings = settings
        self.router = router
        self.http = http
        self.telemetry = telemetry
        self.health = health
        self.keyring = keyring

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------
    async def complete(
        self, protocol: str, requested_model: str, body: dict[str, Any]
    ) -> CompleteResult:
        started = time.perf_counter()
        canonical = self._canonicalize(protocol, body, requested_model)
        canonical, report = optimize(canonical, self.settings.optimizer)
        plan = self.router.explain(requested_model)
        route = [candidate.slug for candidate in plan.candidates]
        if not plan.candidates:
            self._record_failure(
                protocol, requested_model, None, 503, 0, route, self._no_route_message(plan)
            )
            raise GatewayError(503, self._no_route_message(plan), type="invalid_request_error")

        max_attempts = max(int(self.settings.routing.max_attempts or 1), 1)
        attempts = 0
        last_error = "no attempt made"
        last_status = 503

        for candidate in plan.candidates[:max_attempts]:
            if not self.health.available(candidate.slug):
                continue
            attempts += 1
            attempt_started = time.perf_counter()
            upstream = prepare(
                candidate,
                canonical,
                stream=False,
                provider=self.settings.providers.get(candidate.provider),
            )
            try:
                response = await self.http.post(
                    upstream.url,
                    json=upstream.json,
                    headers=upstream.headers,
                    timeout=self._timeout(),
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = 502
                self._penalise(candidate, 0, last_error)
                continue

            latency_ms = (time.perf_counter() - attempt_started) * 1000
            if response.status_code >= 400:
                text = response.text
                last_error = _upstream_message(text) or f"HTTP {response.status_code}"
                last_status = response.status_code
                retry_after = _retry_after(response)
                self._penalise(candidate, response.status_code, last_error, retry_after)
                if self._should_retry(response.status_code, text):
                    continue
                self._record_failure(
                    protocol,
                    requested_model,
                    candidate,
                    response.status_code,
                    attempts,
                    route,
                    last_error,
                )
                raise GatewayError(
                    _client_status(response.status_code),
                    last_error,
                    type=_error_type(response.status_code),
                    retry_after=retry_after,
                )

            try:
                data = response.json()
            except ValueError:
                last_error = "upstream returned invalid JSON"
                last_status = 502
                self._penalise(candidate, 502, last_error)
                continue

            payload = self._shape_response(protocol, candidate, data, requested_model, canonical)
            prompt_tokens, completion_tokens, cached_tokens = _usage(payload, protocol)
            cost = (
                estimate_cost(candidate.model, prompt_tokens, completion_tokens, cached_tokens)
                if self.settings.telemetry.estimate_costs
                else None
            )
            saved = (
                savings(candidate.model, prompt_tokens, completion_tokens, cached_tokens)
                if self.settings.telemetry.estimate_costs
                else None
            )
            record = RequestRecord(
                ts=time.time(),
                protocol=protocol,
                requested_model=requested_model,
                provider=candidate.provider,
                model=candidate.model,
                status=200,
                ok=True,
                stream=False,
                attempts=attempts,
                route=route,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost,
                saved_usd=saved,
                optimized_tokens=report.tokens_saved,
            )
            self.telemetry.add(record)
            self.health.record_success(candidate.slug, latency_ms)
            self.keyring.reward(candidate.provider, candidate.key_index)
            log.info(
                "%s %s -> %s (%dms, %d tokens)",
                protocol,
                requested_model,
                candidate.slug,
                record.latency_ms,
                prompt_tokens + completion_tokens,
            )
            return CompleteResult(payload=payload, record=record)

        message = (
            f"All {attempts} route(s) failed for '{requested_model}'. Last error: {last_error}"
            if attempts
            else (
                f"No healthy route for '{requested_model}': every candidate circuit is open. "
                "Wait for the cooldown or restart Hexgate."
            )
        )
        self._record_failure(protocol, requested_model, None, last_status, attempts, route, message)
        raise GatewayError(last_status if last_status >= 400 else 502, message)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def stream(
        self, protocol: str, requested_model: str, body: dict[str, Any]
    ) -> StreamResult:
        started = time.perf_counter()
        wall = time.time()
        canonical = self._canonicalize(protocol, body, requested_model)
        canonical, report = optimize(canonical, self.settings.optimizer)
        plan = self.router.explain(requested_model)
        route = [candidate.slug for candidate in plan.candidates]
        if not plan.candidates:
            self._record_failure(
                protocol, requested_model, None, 503, 0, route, self._no_route_message(plan)
            )
            raise GatewayError(503, self._no_route_message(plan), type="invalid_request_error")

        max_attempts = max(int(self.settings.routing.max_attempts or 1), 1)
        attempts = 0
        last_error = "no attempt made"
        last_status = 503
        prompt_tokens = estimate_tokens(prompt_text(canonical))

        for candidate in plan.candidates[:max_attempts]:
            if not self.health.available(candidate.slug):
                continue
            attempts += 1
            upstream = prepare(
                candidate,
                canonical,
                stream=True,
                provider=self.settings.providers.get(candidate.provider),
            )
            request = self.http.build_request(
                "POST",
                upstream.url,
                json=upstream.json,
                headers=upstream.headers,
                timeout=self._timeout(),
            )
            try:
                response = await self.http.send(request, stream=True)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = 502
                self._penalise(candidate, 0, last_error)
                continue

            if response.status_code >= 400:
                raw = await response.aread()
                await response.aclose()
                text = raw.decode("utf-8", errors="replace")
                last_error = _upstream_message(text) or f"HTTP {response.status_code}"
                last_status = response.status_code
                retry_after = _retry_after(response)
                self._penalise(candidate, response.status_code, last_error, retry_after)
                if self._should_retry(response.status_code, text):
                    continue
                self._record_failure(
                    protocol,
                    requested_model,
                    candidate,
                    response.status_code,
                    attempts,
                    route,
                    last_error,
                )
                raise GatewayError(
                    _client_status(response.status_code),
                    last_error,
                    type=_error_type(response.status_code),
                    retry_after=retry_after,
                )

            ctx = _StreamContext(
                protocol=protocol,
                requested=requested_model,
                candidate=candidate,
                attempts=attempts,
                route=route,
                report=report,
                started=started,
                wall=wall,
                stats={"prompt_tokens": prompt_tokens},
            )
            source = iter_sse(response.aiter_lines())
            translated = self._translate(
                protocol, candidate, response, source, requested_model, prompt_tokens, ctx.stats
            )
            headers = {
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
                "x-hexgate-route": candidate.slug,
                "x-hexgate-attempts": str(attempts),
            }
            return StreamResult(
                iterator=self._instrument(ctx, translated),
                media_type="text/event-stream; charset=utf-8",
                status=200,
                headers=headers,
            )

        message = (
            f"All {attempts} route(s) failed for '{requested_model}'. Last error: {last_error}"
            if attempts
            else (
                f"No healthy route for '{requested_model}': every candidate circuit is open. "
                "Wait for the cooldown or restart Hexgate."
            )
        )
        self._record_failure(protocol, requested_model, None, last_status, attempts, route, message)
        raise GatewayError(last_status if last_status >= 400 else 502, message)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _canonicalize(self, protocol: str, body: Any, requested_model: str) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise GatewayError(
                400, "Request body must be a JSON object", type="invalid_request_error"
            )
        if protocol == "anthropic":
            canonical = anthropic_request_to_openai(body)
        elif protocol == "responses":
            canonical = responses_request_to_chat(body)
        else:
            canonical = dict(body)
        messages = canonical.get("messages")
        if not isinstance(messages, list) or not messages:
            raise GatewayError(
                400, "messages must be a non-empty array", type="invalid_request_error"
            )
        if requested_model:
            canonical["model"] = requested_model
        return canonical

    def _shape_response(
        self,
        protocol: str,
        candidate: Candidate,
        data: dict[str, Any],
        requested_model: str,
        canonical: dict[str, Any],
    ) -> dict[str, Any]:
        if protocol == "responses":
            return chat_response_to_responses(data, requested_model=requested_model)
        if protocol == "anthropic":
            if candidate.api == "anthropic":
                payload = dict(data)
                payload["model"] = requested_model or payload.get("model", "")
                return payload
            return openai_response_to_anthropic(
                data,
                requested_model=requested_model,
                fallback_prompt=prompt_text(canonical),
            )
        if candidate.api == "anthropic":
            return anthropic_response_to_openai(data, requested_model=requested_model)
        payload = dict(data)
        payload["model"] = requested_model or payload.get("model", "")
        return payload

    async def _translate(
        self,
        protocol: str,
        candidate: Candidate,
        response: httpx.Response,
        source: AsyncIterator[tuple[str | None, dict[str, Any] | str]],
        requested_model: str,
        prompt_tokens: int,
        stats: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        try:
            if protocol == "anthropic":
                if candidate.api == "anthropic":
                    async for frame in rewrite_anthropic_stream(
                        source, model=requested_model, stats=stats
                    ):
                        yield frame
                else:
                    async for frame in openai_chunks_to_anthropic(
                        _dicts(source),
                        model=requested_model,
                        prompt_tokens=prompt_tokens,
                        stats=stats,
                    ):
                        yield frame
            elif protocol == "responses":
                if candidate.api == "anthropic":
                    normalized = anthropic_events_to_openai(
                        source,
                        model=requested_model,
                        prompt_tokens=prompt_tokens,
                        stats=stats,
                    )
                else:
                    normalized = rewrite_openai_stream(
                        _dicts(source), model=requested_model, stats=stats
                    )
                async for frame in openai_chunks_to_responses(
                    normalized,
                    model=requested_model,
                    prompt_tokens=prompt_tokens,
                    stats=stats,
                ):
                    yield frame
            elif candidate.api == "anthropic":
                async for frame in anthropic_events_to_openai(
                    source,
                    model=requested_model,
                    prompt_tokens=prompt_tokens,
                    stats=stats,
                ):
                    yield frame
            else:
                async for frame in rewrite_openai_stream(
                    _dicts(source), model=requested_model, stats=stats
                ):
                    yield frame
        finally:
            await response.aclose()

    async def _instrument(
        self, ctx: _StreamContext, body: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        try:
            async for frame in body:
                if ctx.ttft_ms is None:
                    ctx.ttft_ms = round((time.perf_counter() - ctx.started) * 1000, 1)
                yield frame
            if ctx.stats.get("error"):
                ctx.error = str(ctx.stats["error"])
        except (asyncio.CancelledError, GeneratorExit):
            ctx.error = "client disconnected"
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as SSE
            ctx.error = f"stream failed: {exc}"
            log.warning("stream failed on %s: %s", ctx.candidate.slug, exc)
            if ctx.protocol == "anthropic":
                yield sse(
                    {
                        "type": "error",
                        "error": {"type": "api_error", "message": ctx.error},
                    },
                    "error",
                )
            else:
                yield sse_error(ctx.error)
                yield sse("[DONE]")
        finally:
            self._finish_stream(ctx)

    def _finish_stream(self, ctx: _StreamContext) -> None:
        prompt_tokens = int(ctx.stats.get("prompt_tokens") or 0)
        completion_tokens = int(ctx.stats.get("completion_tokens") or 0)
        ok = ctx.error is None
        cost = (
            estimate_cost(ctx.candidate.model, prompt_tokens, completion_tokens)
            if ok and self.settings.telemetry.estimate_costs
            else None
        )
        saved = (
            savings(ctx.candidate.model, prompt_tokens, completion_tokens)
            if ok and self.settings.telemetry.estimate_costs
            else None
        )
        record = RequestRecord(
            ts=ctx.wall,
            protocol=ctx.protocol,
            requested_model=ctx.requested,
            provider=ctx.candidate.provider,
            model=ctx.candidate.model,
            status=200 if ok else 499,
            ok=ok,
            stream=True,
            attempts=ctx.attempts,
            route=ctx.route,
            latency_ms=round((time.perf_counter() - ctx.started) * 1000, 1),
            ttft_ms=ctx.ttft_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            saved_usd=saved,
            optimized_tokens=ctx.report.tokens_saved,
            error=ctx.error,
        )
        self.telemetry.add(record)
        if ok:
            self.health.record_success(ctx.candidate.slug, record.latency_ms)
            self.keyring.reward(ctx.candidate.provider, ctx.candidate.key_index)
        elif ctx.error and ctx.error.startswith("stream failed"):
            self.health.record_failure(ctx.candidate.slug, ctx.error)

    def _penalise(
        self,
        candidate: Candidate,
        status: int,
        message: str,
        retry_after: float | None = None,
    ) -> None:
        # A bad key is not a broken provider: cool the key, keep the route closed.
        if status in (401, 403) and candidate.key:
            self.keyring.penalize(candidate.provider, candidate.key_index, 300)
            return

        cooldown = self.settings.routing.cooldown_seconds
        if status == 429:
            cooldown = max(cooldown, min(retry_after or 120, 900))
        self.health.record_failure(candidate.slug, f"{status} {message}", cooldown=cooldown)
        if candidate.key and status == 429:
            self.keyring.penalize(
                candidate.provider, candidate.key_index, min(retry_after or 60, 600)
            )

    def _should_retry(self, status: int, text: str) -> bool:
        if status in RETRY_STATUS:
            return True
        if status in (401, 403):
            return True
        if status in (400, 404, 422):
            needle = text.lower()
            return any(hint in needle for hint in MODEL_ERROR_HINTS)
        return False

    def _record_failure(
        self,
        protocol: str,
        requested_model: str,
        candidate: Candidate | None,
        status: int,
        attempts: int,
        route: list[str],
        message: str,
        *,
        error_status: int | None = None,
    ) -> None:
        self.telemetry.add(
            RequestRecord(
                protocol=protocol,
                requested_model=requested_model,
                provider=candidate.provider if candidate else "",
                model=candidate.model if candidate else "",
                status=error_status or status,
                ok=False,
                attempts=max(attempts, 1),
                route=route,
                error=message[:1000],
            )
        )

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self.settings.routing.timeout,
            connect=self.settings.routing.connect_timeout,
            write=60.0,
            pool=10.0,
        )

    def _no_route_message(self, plan: Any) -> str:
        return f"No route available for '{plan.requested}'. {self._no_route_hint(plan)}"

    def _no_route_hint(self, plan: Any) -> str:
        if plan.skipped:
            reasons = "; ".join(f"{item['ref']}: {item['reason']}" for item in plan.skipped[:5])
            return f"Skipped: {reasons}."
        return (
            "Add a provider key in the Hexgate dashboard, or set a routing chain in "
            'config.toml (e.g. default_chain = ["groq/llama-3.3-70b-versatile"]).'
        )


async def _dicts(
    source: AsyncIterator[tuple[str | None, dict[str, Any] | str]],
) -> AsyncIterator[dict[str, Any]]:
    async for _event, payload in source:
        if isinstance(payload, dict):
            yield payload


def _usage(payload: dict[str, Any], protocol: str) -> tuple[int, int, int]:
    usage = payload.get("usage") or {}
    if protocol == "anthropic":
        return (
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            int(usage.get("cache_read_input_tokens") or 0),
        )
    if protocol == "responses":
        return (
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            0,
        )
    return (
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
    )


def _upstream_message(text: str) -> str:
    if not text:
        return ""
    try:
        import json

        data = json.loads(text)
    except (ValueError, TypeError):
        return text.strip()[:500]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")[:500]
        if isinstance(error, str):
            return error[:500]
        for key in ("message", "detail", "msg"):
            if data.get(key):
                return str(data[key])[:500]
    return text.strip()[:500]


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _client_status(status: int) -> int:
    if status in RETRY_STATUS:
        return 502
    if status in (401, 403):
        return 401
    return status


def _error_type(status: int) -> str:
    if status == 429:
        return "rate_limit_error"
    if status in (401, 403):
        return "authentication_error"
    if status in (400, 404, 422):
        return "invalid_request_error"
    if status == 503:
        return "overloaded_error"
    return "api_error"
