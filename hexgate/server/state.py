"""Shared runtime state helpers for the admin API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from hexgate.config import Settings, apply_patch


def settings_of(app: FastAPI) -> Settings:
    return app.state.settings


def refresh(app: FastAPI, patch: dict[str, Any]) -> Settings:
    """Apply a config patch and hot-reload every component that caches settings."""
    current = app.state.settings
    updated = apply_patch(current, patch)

    # Mutate in place: Router and Gateway hold a reference to this object.
    current.server = updated.server
    current.auth = updated.auth
    current.routing = updated.routing
    current.optimizer = updated.optimizer
    current.telemetry = updated.telemetry
    current.providers = updated.providers

    app.state.health.configure(
        threshold=current.routing.failure_threshold,
        cooldown=current.routing.cooldown_seconds,
    )
    app.state.telemetry.resize(current.telemetry.retain)
    return current
