from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from hexgate import __version__
from hexgate.server.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_dashboard_is_served_with_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Hexgate" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_healthz_and_favicon(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["version"] == __version__

    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert "svg" in favicon.headers["content-type"]


def test_metrics_render_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "hexgate_info{" in response.text
    assert 'hexgate_requests_total{outcome="ok"}' in response.text
    assert 'hexgate_request_duration_ms{quantile="0.95"}' in response.text


def test_unknown_paths_return_json_errors(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Not found"

    page = client.get("/nope")
    assert page.status_code == 404
    assert page.json() == {"error": "Not found"}


# ---------------------------------------------------------------------------
# Proxy endpoints
# ---------------------------------------------------------------------------


def test_models_endpoint_lists_nothing_before_configuration(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["data"] == []
    assert body["has_more"] is False


def test_models_endpoint_lists_enabled_provider_models(settings, client):
    settings.providers["groq"].enabled = True
    response = client.get("/v1/models")
    ids = [row["id"] for row in response.json()["data"]]
    assert any(model.startswith("groq/") for model in ids)


def test_openai_completion_without_a_route_returns_503(client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "No route" in error["message"]


def test_anthropic_message_without_a_route_returns_anthropic_error(client):
    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"


def test_invalid_json_is_rejected(client):
    response = client.post(
        "/v1/chat/completions", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert "JSON" in response.json()["error"]["message"]


def test_count_tokens_estimates(client):
    response = client.post(
        "/v1/messages/count_tokens",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "hello world"}]},
    )
    assert response.status_code == 200
    assert response.json()["input_tokens"] > 0


def test_playground_requires_a_prompt(client):
    response = client.post("/api/playground", json={"model": "sonnet"})
    assert response.status_code == 400
    assert "prompt" in response.json()["error"].lower()


def test_playground_surfaces_gateway_errors(client):
    response = client.post("/api/playground", json={"model": "sonnet", "prompt": "hi"})
    assert response.status_code == 503
    assert "No route" in response.json()["error"]


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


def test_overview_reports_first_run(client):
    body = client.get("/api/overview").json()
    assert body["version"] == __version__
    assert body["providers"]["total"] == 41
    assert body["providers"]["configured"] == 0
    assert body["first_run"] is True
    assert body["security"] == {"proxy_auth": False, "admin_auth": False, "allow_remote": False}
    assert "stats" in body and "recent" in body


def test_provider_key_lifecycle(client):
    response = client.put("/api/providers/groq/keys", json={"keys": ["gsk-abcdef123456"]})
    assert response.status_code == 200
    assert response.json()["keys"] == ["gsk-ab…3456"]

    rows = client.get("/api/providers").json()["providers"]
    groq = next(row for row in rows if row["id"] == "groq")
    assert groq["key_count"] == 1
    assert groq["configured"] is True
    assert groq["keys"] == ["gsk-ab…3456"]

    overview = client.get("/api/overview").json()
    assert overview["providers"]["configured"] == 1
    assert overview["first_run"] is False

    assert client.delete("/api/providers/groq/keys").status_code == 200
    rows = client.get("/api/providers").json()["providers"]
    groq = next(row for row in rows if row["id"] == "groq")
    assert groq["key_count"] == 0


def test_keys_for_unknown_provider_is_404(client):
    assert client.put("/api/providers/nope/keys", json={"keys": ["x"]}).status_code == 404


def test_enable_provider_and_patch_validation(client):
    response = client.patch("/api/providers/groq", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["provider"]["enabled"] is True

    bad = client.patch("/api/providers/groq", json={"api": "grpc"})
    assert bad.status_code == 400

    assert client.patch("/api/providers/ghost", json={"enabled": True}).status_code == 404


def test_routing_chains_are_saved_and_planned(client):
    client.patch("/api/providers/groq", json={"enabled": True})
    client.put("/api/providers/groq/keys", json={"keys": ["gsk-test"]})

    response = client.put(
        "/api/routes/chains",
        json={"chains": {"sonnet": "groq/llama-3.3-70b-versatile, groq/llama-3.1-8b-instant"}},
    )
    assert response.status_code == 200
    assert response.json()["chains"]["sonnet"] == [
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
    ]

    plan = client.get("/api/routes/plan", params={"model": "claude-sonnet-4-5"}).json()["plan"]
    assert plan["source"] == "tier"
    assert plan["tier"] == "sonnet"
    assert plan["candidates"][0]["slug"] == "groq/llama-3.3-70b-versatile"

    direct = client.get("/api/routes/plan", params={"model": "groq/llama-3.1-8b-instant"}).json()[
        "plan"
    ]
    assert direct["source"] == "direct"
    assert direct["candidates"][0]["provider"] == "groq"


def test_route_models_and_health_endpoints(client):
    models = client.get("/api/routes/models").json()["models"]
    assert models == []

    client.patch("/api/providers/groq", json={"enabled": True})
    models = client.get("/api/routes/models").json()["models"]
    assert any(row["slug"].startswith("groq/") for row in models)

    health = client.get("/api/health").json()
    assert health["circuits"] == []
    assert health["threshold"] >= 1

    assert client.post("/api/health/reset").json() == {"ok": True}


def test_requests_and_stats_round_trip(client):
    client.post(
        "/v1/chat/completions",
        json={"model": "sonnet", "messages": [{"role": "user", "content": "hi"}]},
    )

    rows = client.get("/api/requests").json()
    assert rows["count"] == 1
    assert rows["requests"][0]["ok"] is False
    assert rows["requests"][0]["status"] == 503

    stats = client.get("/api/stats").json()
    assert stats["requests"] == 1
    assert stats["errors"] == 1

    filtered = client.get("/api/requests", params={"ok": "true"}).json()
    assert filtered["count"] == 0

    assert client.delete("/api/requests").json() == {"ok": True}
    assert client.get("/api/requests").json()["count"] == 0


def test_config_get_put_and_export(client):
    body = client.get("/api/config").json()
    assert body["config"]["server"]["port"] == 8799
    assert len(body["catalog"]) == 41
    assert body["config"]["auth"]["proxy_token"] == ""

    updated = client.put("/api/config", json={"routing": {"strategy": "fastest"}})
    assert updated.status_code == 200
    assert updated.json()["config"]["routing"]["strategy"] == "fastest"

    exported = client.get("/api/config/export")
    assert exported.status_code == 200
    assert "[routing]" in exported.text
    assert 'strategy = "fastest"' in exported.text


def test_diagnostics_reports_paths(client, settings):
    body = client.get("/api/diagnostics").json()
    assert body["version"] == __version__
    assert body["config_exists"] is True
    assert body["config_path"] == str(settings.config_path)
    assert body["proxy_auth"] is False
    assert body["optimizer"] is True


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_proxy_token_guards_v1_and_admin_token_guards_api(settings):
    settings.auth.proxy_token = "proxy-secret"
    settings.auth.admin_token = "admin-secret"
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401

        good = client.get("/v1/models", headers={"authorization": "Bearer proxy-secret"})
        assert good.status_code == 200

        anthropic_style = client.get("/v1/models", headers={"x-api-key": "proxy-secret"})
        assert anthropic_style.status_code == 200

        assert client.get("/api/overview").status_code == 401
        # A proxy token must not unlock the admin API.
        assert (
            client.get(
                "/api/overview", headers={"authorization": "Bearer proxy-secret"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/overview", headers={"authorization": "Bearer admin-secret"}
            ).status_code
            == 200
        )
        assert (
            client.get("/metrics", headers={"authorization": "Bearer admin-secret"}).status_code
            == 200
        )
        assert client.get("/healthz").status_code == 200


async def test_remote_clients_are_denied_by_default(settings):
    settings.auth.allow_remote = False
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 51234))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://hexgate.test") as client:
            denied = await client.get("/api/overview")
            assert denied.status_code == 403
            assert "Remote access" in denied.json()["error"]["message"]

            public = await client.get("/healthz")
            assert public.status_code == 200
    finally:
        await app.state.http.aclose()


async def test_remote_clients_allowed_when_enabled(settings):
    settings.auth.allow_remote = True
    settings.auth.proxy_token = "tok"
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 51234))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://hexgate.test") as client:
            denied = await client.get("/v1/models")
            assert denied.status_code == 401
            allowed = await client.get("/v1/models", headers={"authorization": "Bearer tok"})
            assert allowed.status_code == 200
    finally:
        await app.state.http.aclose()
