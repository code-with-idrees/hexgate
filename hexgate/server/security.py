"""ASGI security middleware: loopback guard, bearer auth, response hardening."""

from __future__ import annotations

import hmac
import ipaddress
import json
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PUBLIC_PATHS = {"/healthz", "/favicon.svg"}

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


def is_loopback(host: str) -> bool:
    if not host:
        return True
    if host in {"testclient", "localhost", "unix"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        app_state = scope["app"].state
        settings = app_state.settings
        path = scope.get("path", "/")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if path.startswith("/api") or path.startswith("/v1") or path == "/metrics":
            client = scope.get("client")
            host = client[0] if client else ""
            if not settings.auth.allow_remote and not is_loopback(host):
                await self._deny(
                    scope,
                    receive,
                    send,
                    403,
                    "Remote access is disabled. Set auth.allow_remote=true.",
                )
                return

            required = (
                settings.auth.admin_token
                if path.startswith("/api") or path == "/metrics"
                else settings.auth.proxy_token
            )
            if required and not self._token_ok(scope, headers, required):
                await self._deny(scope, receive, send, 401, "Invalid or missing bearer token.")
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    out.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _token_ok(scope: Scope, headers: dict[str, str], required: str) -> bool:
        candidates: list[str] = []
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            candidates.append(auth[7:].strip())
        for name in ("x-api-key", "x-hexgate-token"):
            if headers.get(name):
                candidates.append(headers[name].strip())
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        for value in query.get("token", []):
            candidates.append(value.strip())
        return any(hmac.compare_digest(value, required) for value in candidates if value)

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "authentication_error"}}).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
