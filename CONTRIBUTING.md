# Contributing to Hexgate

Thanks for considering a contribution. Hexgate is deliberately small: four
runtime dependencies, no build step, no database. Please keep it that way.

## Set up

```bash
git clone https://github.com/code-with-idrees/hexgate.git
cd hexgate
python -m venv .venv
# Windows: .venv\Scripts\activate        macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Run the checks before opening a pull request:

```bash
ruff check hexgate tests
pytest -q
```

The suite is offline. Upstreams are faked with `httpx.MockTransport`, so no API
keys and no network access are needed. Add a test for every behaviour change;
protocol and retry logic without a test will not be merged.

## Where things live

```text
hexgate/
├── config.py          dataclasses, TOML load/save/patch, env overrides
├── secrets.py         AES-256-GCM vault
├── catalog.py         provider catalog loader
├── optimizer.py       deterministic prompt shaping
├── telemetry.py       bounded in-memory request records
├── costs.py           conservative price table
├── cli.py             command surface
├── core/
│   ├── routing.py     model → candidates, tiers, chains, strategies
│   ├── health.py      per-route circuit breakers
│   ├── keys.py        per-key cooldowns and rotation
│   ├── upstream.py    candidate → HTTP request
│   └── gateway.py     attempts, failover, streaming instrumentation
├── protocol/
│   ├── openai.py      canonical format and SSE parsing
│   ├── anthropic.py   request/response translation
│   └── streams.py     streaming translation in both directions
├── server/            FastAPI app, proxy/admin routes, security, metrics, UI
└── data/providers.json
```

Design rules that matter:

- **Canonical format is OpenAI Chat Completions.** Translate protocols at the
  edges, never inside routing or retry logic.
- **Retries are classified, not unconditional.** A malformed prompt must fail
  fast; a dead provider must not.
- **Provider state is in memory.** Circuit breakers and key cooldowns reset on
  restart. That is a feature, not a bug.
- **No silent data loss.** If a field cannot be translated, document it in
  [docs/compatibility.md](docs/compatibility.md) instead of dropping it quietly.

## Adding a provider

Providers are data. Add an entry to `hexgate/data/providers.json`:

```json
{
  "id": "example",
  "label": "Example AI",
  "api": "openai",
  "base_url": "https://api.example.ai/v1",
  "enabled": false,
  "tier": "free",
  "requires_key": true,
  "env": ["EXAMPLE_API_KEY"],
  "default_model": "example-chat",
  "models": ["example-chat"],
  "notes": "Free tier: 30 requests/minute.",
  "signup": "https://example.ai/keys",
  "color": "#8b9dff"
}
```

Rules:

- `id` is lowercase, stable, and used in `provider/model` slugs.
- `api` is `openai` or `anthropic`.
- `enabled` **must** be `false`. A new install never egresses by accident.
- `base_url` must include the API root (`/v1` for most OpenAI-compatible APIs).
- `requires_key: false` only for local servers that genuinely need no key.
- `models` should be short and verified; do not scrape a whole catalog.
- Add the provider to the grouped tables in [docs/providers.md](docs/providers.md).

Run `pytest tests/test_config.py tests/test_routing.py` and, if the provider has
unusual behaviour, add a routing or gateway test. Provider PRs without a working
endpoint will be asked for evidence (a redacted request/response is enough).

## Changing protocol translation

Add tests to `tests/test_protocol.py` for both directions, including:

- text-only, tool calls, images, thinking blocks;
- streaming frame order and terminal events;
- error frames and missing usage;
- fields that are intentionally dropped.

Update the mapping tables in [docs/compatibility.md](docs/compatibility.md) in
the same pull request. Compatibility claims are part of the interface.

## Style

- Python 3.11+, `from __future__ import annotations`, type hints on public
  functions.
- Ruff config lives in `pyproject.toml`; line length 100.
- Prefer standard library and the four existing runtime dependencies. A new
  dependency needs a strong justification.
- No build step for the UI: plain HTML/CSS/JS, no framework, no bundler.
- Keep comments about *why*, not *what*.

## Commits and pull requests

- One logical change per PR. Small PRs get reviewed faster.
- Write commit messages in the imperative mood: `fix: cap Retry-After at 900s`.
- Describe the user-visible effect and how you tested it.
- Do not include secrets, `secrets.json`, `.devhome/`, real API keys, or
  personal paths in commits or test fixtures.
- By contributing you agree that your work is licensed under the MIT License.

## Reporting bugs

Use the issue templates. Include `hexgate version`, OS, Python version,
`hexgate doctor`, the request shape with secrets removed, and the
`x-hexgate-request-id`. Security issues go through [SECURITY.md](SECURITY.md),
not the public tracker.

## Code of conduct

Be direct, be kind, assume good faith. Harassment or discrimination is not
welcome. The full policy is in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Maintainers may close issues that cannot be reproduced or that ask for help
violating a provider's terms.
