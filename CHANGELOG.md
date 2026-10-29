# Changelog

All notable changes to Hexgate are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 1.2.0

### Added

- **Twelve agent launchers.** `hexgate launch` now supports `claude`, `codex`,
  `opencode`, `aider`, `cline`, `continue`, `pi`, `hermes`, `deepseek`,
  `grok-build`, `muse-code` and `vscode-chat` (plus `custom --bin`). Each
  prints install hints and injects the right base URL / API key env vars.
- **Zero-config local models.** Free-tier providers (OpenRouter, Groq, NVIDIA
  NIM, Google AI Studio, Cerebras, Mistral, SiliconFlow, Chutes, SambaNova,
  Alibaba DashScope, GitHub Models, Cloudflare, Hugging Face, LLM7) and all
  local providers (Ollama, LM Studio, llama.cpp, vLLM) ship enabled by default.
  Install Ollama, run `hexgate serve`, and you have a coding-agent gateway with
  no API key required.
- **Demo scripts.** `scripts/demo_failover.sh` (Linux/macOS) and
  `scripts/demo_failover.ps1` (Windows) walk through a provider failure and
  show Hexgate failing over to the next route — no real API keys needed.

### Changed

- `hexgate launch` error output now shows all injected env vars and agent
  install hints for every supported agent.
- Provider catalog JSON: free and local entries default to `enabled: true`.

## [1.1.0] - 2026-10-29

### Added

- **Codex CLI support.** New `POST /v1/responses` endpoint speaks the OpenAI
  Responses API wire format (`wire_api = "responses"`). Codex CLI requests are
  translated into canonical Chat Completions, routed through the normal tier /
  failover / circuit-breaker / key-rotation pipeline, and translated back into
  Responses API SSE events (`response.created`, `output_text.delta`,
  `output_item.done`, `response.completed`). `hexgate launch codex` prints a
  ready-to-paste `~/.codex/config.toml` snippet.

### Changed

- `Gateway._canonicalize` accepts a `responses` protocol alongside `anthropic`
  and `openai`; Responses request bodies are translated to chat before routing.
- Non-streaming Responses responses are shaped from chat completions via
  `chat_response_to_responses`; streaming Responses events are produced by
  `openai_chunks_to_responses`.
- CLI `AGENT_HINTS` now includes `codex`; `launch codex` starts the gateway and
  prints the Codex config snippet.
- Version bumped to 1.1.0.

## [1.0.0] - 2026-10-24

Initial public release.

### Added

- **One local endpoint, two protocols.** Anthropic Messages and OpenAI Chat
  Completions, translated in both directions at the edge.
- **Streaming translation.** Anthropic ↔ OpenAI SSE state machines covering
  text, thinking, tool calls, usage and error frames, with a synthesised
  `[DONE]` when an upstream closes without one.
- **Provider catalog.** 41 providers as JSON data, all disabled by default;
  adding a key enables the provider automatically.
- **Routing.** Direct `provider/model` references, `sonnet`/`opus`/`haiku`/`fable`
  tier chains, a default chain, and a zero-config implicit fallback. Strategies:
  `quality`, `cheapest`, `fastest`, `round_robin`.
- **Reliability.** Classified retries, per-route circuit breakers with a
  half-open trial, per-key rotation and cooldowns, `Retry-After` support, and
  bounded timeouts.
- **Route planner.** `hexgate route` and the dashboard explain candidate order
  and skip reasons before traffic is sent.
- **Token optimizer.** Trailing-whitespace trimming, empty-turn removal,
  duplicate system paragraph removal, and head/tail truncation of oversized tool
  output, with per-rule savings.
- **Encrypted vault.** AES-256-GCM, scrypt key derivation, machine-bound by
  default, optional `HEXGATE_MASTER_PASSWORD`, atomic `0600` writes, masked API
  responses.
- **Dashboard.** Overview, Providers, Routing, Activity, Playground, Models and
  Settings in three static files, with no build step.
- **Admin API.** Provider and key management, connection tests, model discovery,
  route plans, request records, stats, config patches and diagnostics under
  `/api`.
- **Observability.** Prometheus `/metrics`, bounded request telemetry, latency
  and TTFT, token counts, conservative cost/savings estimates, and
  `x-hexgate-*` response headers.
- **CLI.** `serve`, `init`, `keys`, `providers`, `models`, `route`, `doctor`,
  `config`, `env`, `launch` and `version`, with launchers for Claude Code,
  OpenCode, Aider and custom binaries.
- **Security defaults.** Loopback-only binding, separate proxy/admin tokens,
  constant-time comparison, remote-access opt-in, CSP and hardening headers.
- **Operations.** Dockerfile, Compose file, POSIX and PowerShell installers, CI
  for Linux and Windows on Python 3.11–3.13, and offline tests with mocked
  upstreams.
- **Documentation.** Architecture, configuration, providers, compatibility,
  troubleshooting, security policy and contribution guide.

### Known limitations

- The OpenAI Responses API (`/v1/responses`) is not proxied, so Codex CLI is not
  supported.
- Embeddings, batches, files, image generation and audio endpoints are out of
  scope.
- `count_tokens` is a ~4-characters-per-token estimate.
- Anthropic prompt caching (`cache_control`) and thinking signatures are dropped
  during translation.
- Only one choice (`n = 1`) is represented.
- Request telemetry and circuit state are in memory and reset on restart.

[1.0.0]: https://github.com/code-with-idrees/hexgate/releases/tag/v1.0.0
