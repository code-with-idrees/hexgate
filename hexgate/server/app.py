"""FastAPI application factory and server runner."""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from importlib.resources import files

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from hexgate import __version__
from hexgate.config import Settings, load_settings
from hexgate.core.gateway import Gateway
from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.core.routing import Router
from hexgate.secrets import SecretStore
from hexgate.server import metrics
from hexgate.server.admin_routes import router as admin_router
from hexgate.server.proxy_routes import router as proxy_router
from hexgate.server.security import SecurityMiddleware
from hexgate.telemetry import Telemetry

log = logging.getLogger("hexgate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Hexgate %s listening on %s", __version__, app.state.public_url)
    try:
        yield
    finally:
        await app.state.http.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    secret_store = SecretStore(settings.secrets_path)
    health = HealthRegistry(
        threshold=settings.routing.failure_threshold,
        cooldown=settings.routing.cooldown_seconds,
    )
    keyring = KeyRing()
    telemetry = Telemetry(settings.telemetry.retain)
    http = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(settings.routing.timeout, connect=settings.routing.connect_timeout),
        limits=httpx.Limits(max_connections=120, max_keepalive_connections=30),
        headers={"user-agent": f"hexgate/{__version__}"},
    )
    router = Router(settings, secret_store, health, keyring)
    gateway = Gateway(settings, router, http, telemetry, health, keyring)

    app = FastAPI(
        title="Hexgate",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    display_host = (
        "localhost" if settings.server.host in {"0.0.0.0", "::"} else settings.server.host
    )
    app.state.settings = settings
    app.state.secrets = secret_store
    app.state.health = health
    app.state.keyring = keyring
    app.state.telemetry = telemetry
    app.state.router = router
    app.state.gateway = gateway
    app.state.http = http
    app.state.started_at = time.time()
    app.state.public_url = f"http://{display_host}:{settings.server.port}"

    app.add_middleware(SecurityMiddleware)
    app.include_router(proxy_router)
    app.include_router(admin_router)

    ui_root = files("hexgate").joinpath("server/ui")
    app.mount("/assets", StaticFiles(directory=str(ui_root)), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(ui_root.joinpath("index.html")))

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(str(ui_root.joinpath("favicon.svg")), media_type="image/svg+xml")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": round(time.time() - app.state.started_at, 1),
        }

    @app.get("/metrics", include_in_schema=False)
    async def prometheus() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(telemetry, health, __version__),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.exception_handler(404)
    async def not_found(request: Request, _exc: Exception) -> JSONResponse:
        if request.url.path.startswith(("/api", "/v1")):
            return JSONResponse(
                {"error": {"message": "Not found", "type": "invalid_request_error"}},
                status_code=404,
            )
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app


def serve(settings: Settings | None = None, *, open_browser: bool | None = None) -> None:
    settings = settings or load_settings()
    app = create_app(settings)
    should_open = settings.server.open_browser if open_browser is None else open_browser
    if should_open:
        threading.Timer(1.2, webbrowser.open, args=[app.state.public_url]).start()
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level,
        access_log=False,
        timeout_keep_alive=30,
    )
