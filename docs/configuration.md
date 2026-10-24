# Configuration reference

Hexgate reads configuration from `$HEXGATE_HOME/config.toml` (default `~/.hexgate`). API keys
are stored separately in `secrets.json`; they are never written to the TOML file.

## Precedence

Later sources win:

1. Built-in defaults (the dataclasses in `hexgate/config.py`)
2. Catalog defaults (`hexgate/data/providers.json`)
3. `config.toml`
4. `HEXGATE_*` environment variables (for the subset listed below)

Provider keys have their own order: environment variables named by the provider entry come
first, then the encrypted store. This lets `GROQ_API_KEY=... hexgate serve` override a stored
key for a debugging session.

Changes made in the dashboard are written to `config.toml` immediately and hot-applied to
routing, optimizer, providers and telemetry. Changing `[server]` requires a restart; the API
reports `restart_required: true` when that is the case.

## File layout

```text
~/.hexgate/
├── config.toml
├── secrets.json
└── server.log          # created by `hexgate launch`
```

## `[server]`

| Key | Default | Notes |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | Bind address. Keep it loopback unless you also change `auth.allow_remote`. |
| `port` | `8787` | |
| `open_browser` | `true` | Open the dashboard on `hexgate serve` |
| `log_level` | `"info"` | uvicorn log level |

## `[auth]`

| Key | Default | Notes |
| --- | --- | --- |
| `proxy_token` | `""` | Bearer token for `/v1/*`. Empty disables auth **on loopback only**. |
| `admin_token` | `""` | Bearer token for `/api/*` and `/metrics`. Empty disables auth on loopback. |
| `allow_remote` | `false` | When false, non-loopback clients get `403` before any route runs. |

> Docker note: the official image sets `HEXGATE_HOST=0.0.0.0` and
> `HEXGATE_ALLOW_REMOTE=true` because Docker NAT rewrites client addresses.
> Publish the port on `127.0.0.1` (`-p 127.0.0.1:8787:8787`) and set both tokens
> before exposing it anywhere else.

Tokens can be supplied as `Authorization: Bearer <token>`, `x-api-key: <token>`,
`x-hexgate-token: <token>`, or `?token=<token>`. Use a distinct admin token; the proxy token
cannot access the admin API.

The API returns masked tokens (`abcd…wxyz`). Writing a masked value back is ignored, so the
dashboard can round-trip the config without knowing the real secret.

## `[routing]`

| Key | Default | Notes |
| --- | --- | --- |
| `strategy` | `"quality"` | `quality`, `cheapest`, `fastest`, `round_robin` |
| `max_attempts` | `4` | Maximum candidates tried per request (including the first) |
| `timeout` | `120.0` | Read timeout, seconds |
| `connect_timeout` | `10.0` | TCP/TLS connect timeout, seconds |
| `cooldown_seconds` | `60.0` | Circuit-breaker cooldown |
| `failure_threshold` | `3` | Consecutive failures before a route opens |
| `default_chain` | `[]` | Ordered fallback for any model |
| `fable_chain` | `[]` | Used when the model name contains `fable` |
| `opus_chain` | `[]` | Used when the model name contains `opus` |
| `sonnet_chain` | `[]` | Used when the model name contains `sonnet` |
| `haiku_chain` | `[]` | Used when the model name contains `haiku` |

Chain entries are `provider/model` strings. A request that exactly matches a configured
provider id (for example `groq`) resolves to that provider's default model.

Strategy semantics:

- `quality` — declaration order; you decide.
- `cheapest` — lowest combined input+output price per million tokens. Unknown models sort last.
- `fastest` — lowest EWMA latency observed by the circuit breaker; untried routes sort last.
- `round_robin` — rotate the candidate list one step per request (fairness across free keys).

`max_attempts` applies after circuit filtering. Set it to `1` if you want strict "no retries"
behaviour and prefer the agent to decide.

## `[optimizer]`

| Key | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Master switch |
| `max_tool_result_chars` | `16000` | Longer tool results keep a head and tail with an explicit marker. `0` disables truncation |
| `dedupe_system` | `true` | Merge multiple system/developer prompts and remove duplicate paragraphs |
| `strip_empty_messages` | `true` | Drop empty user/tool turns (assistant turns are preserved) |
| `trim_trailing_whitespace` | `true` | Strip trailing spaces on each line |

Savings are reported per rule on each request record and aggregated in the dashboard and
`/metrics`.

## `[telemetry]`

| Key | Default | Notes |
| --- | --- | --- |
| `retain` | `500` | Number of request records kept in memory |
| `estimate_costs` | `true` | Compute cost/savings from the static price table |

Telemetry is intentionally lossy. Set `retain` to `0` to disable records (metrics still work).

## `[providers.<id>]`

Any field of a catalog entry can be overridden per provider. Only the overrides are written to
`config.toml`, so upgrading the catalog does not require rewriting your config.

| Key | Type | Notes |
| --- | --- | --- |
| `label` | string | Display name |
| `enabled` | bool | A provider must be enabled to be routed to. Adding a key enables it. |
| `api` | `"openai"` or `"anthropic"` | Wire protocol of the upstream |
| `base_url` | string | Usually ends in `/v1`; Hexgate appends `chat/completions` or `messages` |
| `models` | list of strings | Model IDs exactly as the provider expects |
| `default_model` | string | Target when the reference is just the provider id, and used by implicit routing |
| `tier` | string | `free`, `trial`, `paid`, `local`, `custom` |
| `requires_key` | bool | When false, an empty key is allowed |
| `env` | list of strings | Environment variables checked before the encrypted store |
| `headers` | table | Extra headers (for example OpenRouter attribution) |
| `signup`, `docs`, `notes`, `color` | strings | Display metadata only |

Example:

```toml
[providers.groq]
enabled = true
default_model = "llama-3.3-70b-versatile"
models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

[providers.custom]
label = "Homelab vLLM"
enabled = true
api = "openai"
base_url = "http://192.168.1.20:8000/v1"
models = ["qwen3-coder-30b"]
default_model = "qwen3-coder-30b"
requires_key = false
```

`base_url` is joined intelligently: if it already ends in `/chat/completions` or `/messages`, it
is used as-is. This supports providers that publish full endpoint URLs.

## Environment variables

| Variable | Effect |
| --- | --- |
| `HEXGATE_HOME` | State directory (default `~/.hexgate`) |
| `HEXGATE_HOST` | Override `server.host` |
| `HEXGATE_PORT` | Override `server.port` |
| `HEXGATE_LOG_LEVEL` | Override `server.log_level` |
| `HEXGATE_PROXY_TOKEN` | Override `auth.proxy_token` |
| `HEXGATE_ADMIN_TOKEN` | Override `auth.admin_token` |
| `HEXGATE_ALLOW_REMOTE` | `1/true/yes/on` enables `auth.allow_remote` |
| `HEXGATE_STRATEGY` | Override `routing.strategy` |
| `HEXGATE_CHAIN` | Comma-separated default chain, e.g. `groq/llama-3.3-70b-versatile,cerebras/llama-3.3-70b` |
| `HEXGATE_MASTER_PASSWORD` | Use a portable passphrase for the secret vault instead of the machine fingerprint |

Provider keys are read from the variables listed in each catalog entry (`GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `NVIDIA_NIM_API_KEY`, …). They are not cached in the config file.

## Secrets

`secrets.json` is a small JSON envelope:

```json
{
  "v": 1,
  "alg": "AES-256-GCM",
  "nonce": "base64…",
  "data": "base64…"
}
```

The plaintext is `{"providers": {"groq": ["gsk_…"]}}`. The AES key is derived with scrypt from
`HEXGATE_MASTER_PASSWORD` when set, otherwise from `platform.node()`, `platform.machine()` and
the MAC address. Consequences:

- Copying `secrets.json` to another machine will fail to decrypt unless you also use the same
  `HEXGATE_MASTER_PASSWORD`.
- Changing the machine hostname or MAC address has the same effect.
- Recovery is `hexgate keys reset --all`, then re-enter keys.

If you run Hexgate in Docker, set `HEXGATE_MASTER_PASSWORD` so a new container can read the
vault on a mounted volume.

## Reset and export

```bash
hexgate config show          # effective config as JSON (tokens masked)
hexgate config path          # path to config.toml
hexgate config export        # raw TOML
hexgate keys reset --all     # delete the vault (asks for --all)
```

`GET /api/config/export` returns the raw TOML for backup. There is no import endpoint; restore
the file directly and restart (or `PUT /api/config` with a JSON patch for partial changes).
