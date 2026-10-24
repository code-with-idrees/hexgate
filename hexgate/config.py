"""Configuration loading, validation and persistence.

Resolution order (later wins):

1. Built-in defaults
2. Provider catalog defaults (``hexgate/data/providers.json``)
3. ``$HEXGATE_HOME/config.toml``
4. ``HEXGATE_*`` environment variables

Everything is a plain dataclass so the whole config can be serialised to JSON
for the Admin UI and back without a second schema.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "hexgate"
DEFAULT_PORT = 8787
CONFIG_FILE = "config.toml"
SECRETS_FILE = "secrets.json"

TIERS = ("fable", "opus", "sonnet", "haiku")

_HEADER = """\
# Hexgate configuration
# Docs: https://github.com/code-with-idrees/hexgate/blob/main/docs/configuration.md
#
# Model references use "provider/model-id", e.g.
#   "nvidia_nim/meta/llama-3.3-70b-instruct"
#   "openrouter/anthropic/claude-sonnet-4.5"
#
# API keys live in secrets.json (encrypted), never here.
"""


def home_dir() -> Path:
    """Return the Hexgate state directory, honouring ``$HEXGATE_HOME``."""
    raw = os.environ.get("HEXGATE_HOME")
    return Path(raw).expanduser().resolve() if raw else Path.home() / ".hexgate"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    open_browser: bool = True
    log_level: str = "info"


@dataclass(slots=True)
class AuthConfig:
    """Bearer tokens protecting the gateway.

    Empty values disable auth. ``allow_remote`` must be true before the server
    will accept non-loopback connections, even with a token configured.
    """

    proxy_token: str = ""
    admin_token: str = ""
    allow_remote: bool = False


@dataclass(slots=True)
class RoutingConfig:
    strategy: str = "quality"  # quality | cheapest | fastest | round_robin
    max_attempts: int = 4
    timeout: float = 120.0
    connect_timeout: float = 10.0
    cooldown_seconds: float = 60.0
    failure_threshold: int = 3
    default_chain: list[str] = field(default_factory=list)
    fable_chain: list[str] = field(default_factory=list)
    opus_chain: list[str] = field(default_factory=list)
    sonnet_chain: list[str] = field(default_factory=list)
    haiku_chain: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OptimizerConfig:
    """Request-shaping that cuts token spend without touching the prompt's meaning."""

    enabled: bool = True
    max_tool_result_chars: int = 16_000
    dedupe_system: bool = True
    strip_empty_messages: bool = True
    trim_trailing_whitespace: bool = True


@dataclass(slots=True)
class TelemetryConfig:
    retain: int = 500
    estimate_costs: bool = True


@dataclass(slots=True)
class ProviderConfig:
    id: str
    label: str
    api: str = "openai"  # openai | anthropic
    base_url: str = ""
    enabled: bool = True
    models: list[str] = field(default_factory=list)
    default_model: str = ""
    tier: str = "custom"  # free | trial | paid | local | subscription | custom
    signup: str = ""
    docs: str = ""
    notes: str = ""
    requires_key: bool = True
    env: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    color: str = "#7c9cff"

    @property
    def slug(self) -> str:
        return self.id

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Settings:
    home: Path
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    # ---- derived -------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_FILE

    @property
    def secrets_path(self) -> Path:
        return self.home / SECRETS_FILE

    def enabled_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers.values() if p.enabled]

    def chains(self) -> dict[str, list[str]]:
        r = self.routing
        return {
            "default": list(r.default_chain),
            "fable": list(r.fable_chain),
            "opus": list(r.opus_chain),
            "sonnet": list(r.sonnet_chain),
            "haiku": list(r.haiku_chain),
        }

    def to_dict(self, include_tokens: bool = False) -> dict[str, Any]:
        data = {
            "home": str(self.home),
            "server": asdict(self.server),
            "auth": asdict(self.auth),
            "routing": asdict(self.routing),
            "optimizer": asdict(self.optimizer),
            "telemetry": asdict(self.telemetry),
            "providers": {pid: p.public() for pid, p in self.providers.items()},
        }
        if not include_tokens:
            data["auth"]["proxy_token"] = _mask(data["auth"]["proxy_token"])
            data["auth"]["admin_token"] = _mask(data["auth"]["admin_token"])
        return data


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_settings(home: Path | None = None, *, create: bool = True) -> Settings:
    home = (home or home_dir()).expanduser().resolve()
    if create:
        home.mkdir(parents=True, exist_ok=True)

    settings = Settings(home=home)
    settings.providers = _default_providers()

    raw: dict[str, Any] = {}
    path = home / CONFIG_FILE
    if path.exists():
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - surfaced in CLI
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    _apply_file(settings, raw)
    _apply_env(settings)
    if create and not path.exists():
        save_settings(settings)
    return settings


def _default_providers() -> dict[str, ProviderConfig]:
    from hexgate.catalog import load_catalog

    providers: dict[str, ProviderConfig] = {}
    for entry in load_catalog():
        providers[entry["id"]] = ProviderConfig(**entry)
    return providers


def _apply_file(settings: Settings, raw: dict[str, Any]) -> None:
    for section in ("server", "auth", "routing", "optimizer", "telemetry"):
        values = raw.get(section) or {}
        if not isinstance(values, dict):
            continue
        target = getattr(settings, section)
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)

    for pid, values in (raw.get("providers") or {}).items():
        if not isinstance(values, dict):
            continue
        base = settings.providers.get(pid)
        if base is None:
            base = ProviderConfig(id=pid, label=values.get("label", pid))
            settings.providers[pid] = base
        for key, value in values.items():
            if hasattr(base, key):
                setattr(base, key, value)


def _apply_env(settings: Settings) -> None:
    settings.server.host = os.environ.get("HEXGATE_HOST", settings.server.host)
    settings.server.port = _env_int("HEXGATE_PORT", settings.server.port)
    settings.server.log_level = os.environ.get("HEXGATE_LOG_LEVEL", settings.server.log_level)
    settings.auth.proxy_token = os.environ.get("HEXGATE_PROXY_TOKEN", settings.auth.proxy_token)
    settings.auth.admin_token = os.environ.get("HEXGATE_ADMIN_TOKEN", settings.auth.admin_token)
    settings.auth.allow_remote = _env_bool("HEXGATE_ALLOW_REMOTE", settings.auth.allow_remote)
    if os.environ.get("HEXGATE_STRATEGY"):
        settings.routing.strategy = os.environ["HEXGATE_STRATEGY"]
    if os.environ.get("HEXGATE_CHAIN"):
        settings.routing.default_chain = _split_chain(os.environ["HEXGATE_CHAIN"])


def _split_chain(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised when configuration cannot be parsed or written."""


def save_settings(settings: Settings) -> Path:
    settings.home.mkdir(parents=True, exist_ok=True)
    path = settings.home / CONFIG_FILE
    payload = {
        "server": asdict(settings.server),
        "auth": asdict(settings.auth),
        "routing": asdict(settings.routing),
        "optimizer": asdict(settings.optimizer),
        "telemetry": asdict(settings.telemetry),
        "providers": {
            pid: _provider_overrides(p)
            for pid, p in sorted(settings.providers.items())
            if _has_overrides(p)
        },
    }
    body = _HEADER + "\n" + dumps(payload)
    path.write_text(body, encoding="utf-8")
    _restrict(path)
    return path


def _has_overrides(provider: ProviderConfig) -> bool:
    from hexgate.catalog import catalog_entry

    if catalog_entry(provider.id) is None:
        return True
    return bool(_provider_overrides(provider))


def _provider_overrides(provider: ProviderConfig) -> dict[str, Any]:
    from hexgate.catalog import catalog_entry

    base = catalog_entry(provider.id) or {}
    out: dict[str, Any] = {}
    for key in ("label", "enabled", "api", "base_url", "models", "default_model"):
        value = getattr(provider, key)
        if base.get(key) != value:
            out[key] = value
    return out


def _restrict(path: Path) -> None:
    with contextlib.suppress(OSError):  # pragma: no cover - exotic filesystems
        path.chmod(0o600)


def dumps(data: dict[str, Any]) -> str:
    """Minimal TOML emitter for the known config schema."""
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for name, table in tables.items():
        if not table:
            continue
        lines.append("")
        _emit_table(lines, name, table)
    return "\n".join(lines).rstrip() + "\n"


def _emit_table(lines: list[str], name: str, table: dict[str, Any]) -> None:
    lines.append(f"[{name}]")
    nested: dict[str, dict[str, Any]] = {}
    for key, value in table.items():
        if isinstance(value, dict):
            nested[key] = value
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    for key, value in nested.items():
        lines.append("")
        _emit_table(lines, f"{name}.{key}", value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return _toml_value(str(value))


# ---------------------------------------------------------------------------
# Mutation helpers (used by the Admin API)
# ---------------------------------------------------------------------------

_PROTECTED_AUTH = {"proxy_token", "admin_token"}


def apply_patch(settings: Settings, patch: dict[str, Any]) -> Settings:
    """Merge a JSON patch into settings, persist, and reload from disk."""
    if not isinstance(patch, dict):
        raise ConfigError("patch must be an object")

    for section in ("server", "routing", "optimizer", "telemetry", "auth"):
        values = patch.get(section)
        if not isinstance(values, dict):
            continue
        target = getattr(settings, section)
        for key, value in values.items():
            if not hasattr(target, key):
                continue
            if section == "auth" and key in _PROTECTED_AUTH and _looks_masked(value):
                continue
            setattr(target, key, value)

    providers = patch.get("providers") or {}
    if isinstance(providers, dict):
        for pid, values in providers.items():
            if not isinstance(values, dict):
                continue
            provider = settings.providers.get(pid)
            if provider is None:
                continue
            for key, value in values.items():
                if hasattr(provider, key) and key != "id":
                    setattr(provider, key, value)

    save_settings(settings)
    return load_settings(settings.home, create=False)


def _looks_masked(value: Any) -> bool:
    return isinstance(value, str) and ("…" in value or "•" in value)


def ensure_dirs(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
