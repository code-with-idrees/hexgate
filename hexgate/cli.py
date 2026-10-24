"""Hexgate command line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from hexgate import __version__
from hexgate.catalog import load_catalog
from hexgate.config import SECRETS_FILE, Settings, apply_patch, load_settings, save_settings
from hexgate.core.health import HealthRegistry
from hexgate.core.keys import KeyRing
from hexgate.core.routing import Router
from hexgate.secrets import SecretError, SecretStore, mask_key

AGENT_HINTS = {
    "claude": "Claude Code — npm i -g @anthropic-ai/claude-code",
    "opencode": "OpenCode — npm i -g opencode-ai",
    "aider": "Aider — pip install aider-chat",
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None) or "serve"

    handlers = {
        "serve": cmd_serve,
        "init": cmd_init,
        "doctor": cmd_doctor,
        "config": cmd_config,
        "keys": cmd_keys,
        "models": cmd_models,
        "route": cmd_route,
        "launch": cmd_launch,
        "env": cmd_env,
        "version": cmd_version,
        "providers": cmd_providers,
    }
    try:
        return handlers[command](args) or 0
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except SecretError as exc:
        print(f"secret store error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hexgate",
        description="A resilient local model gateway for coding agents.",
        epilog="Docs: https://github.com/code-with-idrees/hexgate",
    )
    parser.add_argument("--version", action="version", version=f"hexgate {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the gateway and dashboard")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-browser", action="store_true")

    sub.add_parser("init", help="create the config directory and default config")
    sub.add_parser("doctor", help="check the installation and print a report")
    sub.add_parser("version", help="print the version")
    sub.add_parser("providers", help="list providers and key status")

    models = sub.add_parser("models", help="list routable model slugs")
    models.add_argument("--json", action="store_true")

    route = sub.add_parser("route", help="explain how a model request would be routed")
    route.add_argument("model", nargs="?", default="sonnet")
    route.add_argument("--json", action="store_true")

    env = sub.add_parser("env", help="print environment variables for an agent")
    env.add_argument("--shell", choices=["bash", "powershell", "json"], default="bash")

    launch = sub.add_parser("launch", help="start a coding agent pointed at Hexgate")
    launch.add_argument(
        "agent", choices=sorted(AGENT_HINTS) + ["custom"], nargs="?", default="claude"
    )
    launch.add_argument("--bin", default=None, help="binary to run for agent=custom")
    launch.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the agent")

    config = sub.add_parser("config", help="inspect or export configuration")
    config.add_argument("action", choices=["show", "path", "export"], nargs="?", default="show")

    keys = sub.add_parser("keys", help="manage encrypted API keys")
    keys.add_argument(
        "action", choices=["list", "set", "remove", "reset"], nargs="?", default="list"
    )
    keys.add_argument("provider", nargs="?", help="provider id, e.g. groq")
    keys.add_argument("--key", dest="key_value", default=None, help="key value (otherwise prompt)")
    keys.add_argument("--all", action="store_true", help="for reset: remove every stored key")

    return parser


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from hexgate.server.app import serve

    settings = load_settings()
    if args.host:
        settings.server.host = args.host
    if args.port:
        settings.server.port = args.port
    print(f"hexgate {__version__}")
    print(f"state   {settings.home}")
    print(f"config  {settings.config_path}")
    configured = [
        p.id
        for p in settings.enabled_providers()
        if not p.requires_key or SecretStore(settings.secrets_path).keys(p.id, p.env)
    ]
    if configured:
        print(f"ready   {', '.join(configured)}")
    else:
        print("ready   no provider keys yet — open the dashboard to add one")
    display_host = (
        "localhost" if settings.server.host in {"0.0.0.0", "::"} else settings.server.host
    )
    print(f"dashboard http://{display_host}:{settings.server.port}")
    print()
    serve(settings, open_browser=not args.no_browser)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    settings = load_settings()
    save_settings(settings)
    print(f"created {settings.config_path}")
    print(f"secrets {settings.secrets_path} (created on first key)")
    print("next: hexgate serve")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"hexgate {__version__}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    settings = load_settings(create=False)
    store = SecretStore(settings.secrets_path)
    catalog = {entry["id"]: entry for entry in load_catalog()}
    rows = sorted(settings.providers.values(), key=lambda p: (p.tier, p.label.lower()))
    width = max(len(p.label) for p in rows) + 2
    print(f"{'PROVIDER'.ljust(width)}{'TIER'.ljust(12)}{'KEYS'.ljust(6)}DEFAULT MODEL")
    for provider in rows:
        keys = store.keys(provider.id, provider.env)
        status = f"{len(keys)}" if keys else ("n/a" if not provider.requires_key else "—")
        print(
            f"{provider.label.ljust(width)}{provider.tier.ljust(12)}{status.ljust(6)}"
            f"{provider.default_model or (catalog.get(provider.id, {}).get('default_model') or '')}"
        )
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    settings = load_settings(create=False)
    rows = []
    for provider in settings.enabled_providers():
        models = list(provider.models)
        if provider.default_model and provider.default_model not in models:
            models.insert(0, provider.default_model)
        for model in models:
            rows.append(
                {"slug": f"{provider.id}/{model}", "provider": provider.id, "tier": provider.tier}
            )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        print(f"{row['slug']}  ({row['tier']})")
    return 0


def _router(settings: Settings) -> Router:
    store = SecretStore(settings.secrets_path)
    health = HealthRegistry(
        threshold=settings.routing.failure_threshold, cooldown=settings.routing.cooldown_seconds
    )
    return Router(settings, store, health, KeyRing())


def cmd_route(args: argparse.Namespace) -> int:
    settings = load_settings(create=False)
    plan = _router(settings).explain(args.model)
    if args.json:
        print(json.dumps(plan.as_dict(), indent=2))
        return 0
    print(f"request : {plan.requested or '(empty)'}")
    print(f"tier    : {plan.tier or 'none'}")
    print(f"source  : {plan.source}")
    if not plan.candidates:
        print("no route — add a key or a chain entry")
        for item in plan.skipped[:8]:
            print(f"  skipped {item['ref']}: {item['reason']}")
        return 1
    for index, candidate in enumerate(plan.candidates, 1):
        print(f"  {index}. {candidate.slug}  (key {candidate.key_index + 1}, {candidate.api})")
    for item in plan.skipped[:8]:
        print(f"  skipped {item['ref']}: {item['reason']}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    settings = load_settings(create=False)
    values = _agent_env(settings)
    if args.shell == "json":
        print(json.dumps(values, indent=2))
        return 0
    for key, value in values.items():
        if args.shell == "powershell":
            print(f'$env:{key}="{value}"')
        else:
            print(f'export {key}="{value}"')
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    settings = load_settings()
    port = settings.server.port
    if not _server_up(settings):
        print(f"starting gateway on port {port}…")
        _spawn_server(settings)
        if not _wait_for_server(settings, timeout=12.0):
            print(
                "could not start the gateway; run `hexgate serve` in another terminal",
                file=sys.stderr,
            )
            return 1

    agent = args.agent
    binary = args.bin or agent
    env = {**os.environ, **_agent_env(settings)}
    extra = [item for item in (args.args or []) if item != "--"]

    if agent == "aider":
        extra = [f"--openai-api-base={env['OPENAI_BASE_URL']}", *extra]

    resolved = shutil.which(binary)
    if resolved is None:
        print(f"'{binary}' was not found on PATH.", file=sys.stderr)
        print(
            f"install: {AGENT_HINTS.get(agent, 'install the agent, then rerun')}", file=sys.stderr
        )
        print("environment ready:", file=sys.stderr)
        for key in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
            print(f"  {key}={env[key]}", file=sys.stderr)
        return 127

    print(f"launching {binary} through hexgate on port {port}")
    try:
        return subprocess.call([resolved, *extra], env=env)
    except FileNotFoundError:
        print(f"failed to launch {resolved}", file=sys.stderr)
        return 127


def cmd_config(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.action == "path":
        print(settings.config_path)
        return 0
    if args.action == "export":
        text = (
            settings.config_path.read_text(encoding="utf-8")
            if settings.config_path.exists()
            else ""
        )
        print(text, end="")
        return 0
    print(json.dumps(settings.to_dict(), indent=2))
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = SecretStore(settings.secrets_path)
    action = args.action or "list"

    if action == "list":
        data = store.load()
        if not data:
            print("no keys stored")
            return 0
        for provider_id, keys in sorted(data.items()):
            print(f"{provider_id}: {', '.join(mask_key(k) for k in keys)}")
        return 0

    if action == "set":
        if not args.provider:
            print("usage: hexgate keys set <provider>", file=sys.stderr)
            return 2
        provider = settings.providers.get(args.provider)
        if provider is None:
            print(f"unknown provider '{args.provider}' (see: hexgate providers)", file=sys.stderr)
            return 2
        value = args.key_value or getpass.getpass(f"API key for {provider.label}: ")
        if not value.strip():
            print("empty key, nothing saved", file=sys.stderr)
            return 2
        current = store.load().get(args.provider, [])
        store.set_keys(args.provider, [*current, value.strip()])
        if not provider.enabled:
            apply_patch(settings, {"providers": {args.provider: {"enabled": True}}})
            print(f"enabled {provider.label}")
        print(f"saved key {mask_key(value.strip())} for {provider.label}")
        return 0

    if action == "remove":
        if not args.provider:
            print("usage: hexgate keys remove <provider>", file=sys.stderr)
            return 2
        store.remove(args.provider)
        print(f"removed keys for {args.provider}")
        return 0

    if action == "reset":
        if not args.all:
            print("this deletes every stored key. Re-run with --all to confirm.", file=sys.stderr)
            return 2
        path = settings.home / SECRETS_FILE
        if path.exists():
            path.unlink()
        print("removed the encrypted key store")
        return 0

    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(create=False)
    store = SecretStore(settings.secrets_path)
    print("hexgate doctor")
    print("=" * 60)
    checks: list[tuple[str, str, bool]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, detail, ok))
        mark = "ok  " if ok else "warn"
        print(f"[{mark}] {name}: {detail}")

    check("version", True, f"{__version__} on Python {platform.python_version()}")
    check("platform", True, platform.platform())
    check("home", settings.home.exists(), str(settings.home))
    check("config", settings.config_path.exists(), str(settings.config_path))

    keys: dict[str, list[str]] = {}
    try:
        keys = store.load()
        check("secrets", True, f"decrypted {len(keys)} provider key set(s)")
    except SecretError as exc:
        check("secrets", False, str(exc))

    configured = 0
    for provider in settings.enabled_providers():
        if not provider.requires_key or keys.get(provider.id):
            configured += 1
    check(
        "providers",
        configured > 0,
        f"{configured} ready of {len(settings.enabled_providers())} enabled",
    )

    plan = _router(settings).explain("sonnet")
    if plan.candidates:
        check("routing", True, f"sonnet -> {plan.candidates[0].slug}")
    else:
        check(
            "routing",
            False,
            "no route for sonnet; set a chain or add a key",
        )

    up = _server_up(settings)
    check(
        "server",
        True,
        f"running on port {settings.server.port}"
        if up
        else "not running (start with `hexgate serve`)",
    )

    print("=" * 60)
    warnings = [item for item in checks if not item[2]]
    if warnings:
        print(f"{len(warnings)} check(s) need attention.")
        return 0
    print("all checks passed.")
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _agent_env(settings: Settings) -> dict[str, str]:
    host = settings.server.host
    host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    base = f"http://{host}:{settings.server.port}"
    token = settings.auth.proxy_token or "hexgate-local"
    return {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_AUTH_TOKEN": token,
        "OPENAI_BASE_URL": f"{base}/v1",
        "OPENAI_API_BASE": f"{base}/v1",
        "OPENAI_API_KEY": token,
    }


def _health_url(settings: Settings) -> str:
    host = settings.server.host
    host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{host}:{settings.server.port}/healthz"


def _server_up(settings: Settings) -> bool:
    try:
        with urllib.request.urlopen(_health_url(settings), timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_for_server(settings: Settings, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_up(settings):
            return True
        time.sleep(0.25)
    return False


def _spawn_server(settings: Settings) -> subprocess.Popen:
    log_path = settings.home / "server.log"
    settings.home.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "hexgate",
        "serve",
        "--no-browser",
        "--port",
        str(settings.server.port),
    ]
    kwargs: dict[str, object] = {"stdout": handle, "stderr": handle, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
    print(f"logs: {log_path}")
    return process


def installed_path() -> Path:
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    raise SystemExit(main())
