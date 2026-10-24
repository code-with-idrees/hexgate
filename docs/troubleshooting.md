# Troubleshooting

Start here:

```bash
hexgate doctor
hexgate route <model>
curl http://127.0.0.1:8787/healthz
```

`doctor` checks paths, permissions, secrets, routing and whether the server is
running. `route` explains the exact candidate order and every skip reason
without sending a token. The dashboard's **Activity** view shows the last
request's upstream error, route and attempts.

## The error matrix

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `503 No route available for '<model>'` | No enabled provider, no chain, or every candidate was filtered | Add a key (auto-enables the provider) or set a chain |
| `503 No healthy route for '<model>'` | Every candidate's circuit is open | Wait for the cooldown, or fix the provider; restart clears in-memory state |
| `401 Invalid or missing bearer token.` | A proxy or admin token is configured and the client did not send it | Send the same token as `Authorization: Bearer`, `x-api-key`, `x-hexgate-token`, or `?token=` |
| `403 Remote access is disabled.` | The client is not loopback and `allow_remote` is false | Use `127.0.0.1`, an SSH tunnel, or consciously enable remote access |
| `502 All N route(s) failed…` | Upstreams returned errors, timed out, or sent invalid JSON | Read the last error; test the provider in the dashboard |
| `429 All N route(s) failed…` | Rate limits; keys and routes are cooling | Wait for `Retry-After`, add a second key, or add another provider to the chain |
| `400` returned immediately | The prompt or parameters are malformed, or the provider rejected them | Fix the request; Hexgate deliberately does not burn other providers on a bad prompt |
| `400`/`404`/`422` **with** a model hint | The routed model id is wrong for that provider | Set the provider's `default_model` or fix the chain entry |
| Secrets error on startup | Machine fingerprint changed or the master password differs | Restore the old password or `hexgate keys reset --all` and re-add keys |
| Dashboard is blank | Stale cached assets or a script error | Hard-refresh; check the browser console and `/healthz` |
| Streaming stops before the first token | Upstream died, or a proxy is buffering SSE | Check Activity for the upstream error; remove intercepting proxies |
| Cost shows `null` | The model is not in the price table | Expected; estimates are never fabricated |

## 503: no route

Open **Providers** in the dashboard. A provider is only routable when it is
enabled **and** has a key (unless it is a local server that does not need one).
Adding a key enables it automatically; toggling it off disables it again.

If the model is not a direct `provider/model` reference and no tier matches,
Hexgate falls back to `routing.default_chain`, then to enabled providers'
default models. If all of those are empty, there is nothing to route to.

```toml
[routing]
default_chain = ["groq/llama-3.3-70b-versatile", "cerebras/llama-3.3-70b"]
```

```bash
hexgate providers          # catalog plus key status
hexgate models             # every routable provider/model
hexgate route claude-sonnet-4-5 --json
```

The `503` body includes the first five skip reasons, for example
`groq/…: no API key`, `…: provider disabled` or `…: base URL not set`.

## 503: no healthy route

Circuit breakers are per `provider/model` and in memory. After
`routing.failure_threshold` consecutive failures a route is skipped for
`routing.cooldown_seconds`, then one half-open trial is allowed. If every
candidate is open you get this message.

- Fix the underlying provider (bad model id, revoked key, outage).
- Wait out the cooldown; a successful trial closes the circuit.
- Restarting the process clears all circuit and key-cooldown state.
- `hexgate route <model>` shows the candidate order; an open circuit is skipped
  at request time even though the route is still listed.

A `401`/`403` does **not** open a circuit. It cools only the key that was used,
so a second key or another provider can still answer.

## 401 and 403

Two different layers can produce these:

**Hexgate's own auth** returns `{"error":{"message":"Invalid or missing bearer token.","type":"authentication_error"}}`.
Set the token in your client:

```bash
export ANTHROPIC_AUTH_TOKEN=your-proxy-token
export ANTHROPIC_API_KEY=your-proxy-token
export OPENAI_API_KEY=your-proxy-token
```

The admin token is separate from the proxy token. A proxy token cannot read
`/api` or `/metrics`.

**The upstream provider** returns its own 401/403. That usually means the key is
wrong, expired, lacks access to the model, or is region-blocked. The dashboard's
provider test reports the upstream status and message. Hexgate retries the next
key or route, so a single bad key does not necessarily fail the request.

Remote clients get `403 Remote access is disabled…` before authentication is
even checked. The default bind is `127.0.0.1`; changing the host alone is not
enough:

```toml
[server]
host = "0.0.0.0"

[auth]
proxy_token = "long-random-value"
admin_token = "another-long-random-value"
allow_remote = true
```

Only do this on a trusted network, behind a reverse proxy or tunnel. Hexgate has
no TLS and no user accounts.

## 502: all routes failed

The final error string is the last upstream message, not a generic one. Common
variants:

| Last error | Meaning |
| --- | --- |
| `ConnectError` / `Connection refused` | Wrong base URL, or a local server is not running |
| `ReadTimeout` / `ConnectTimeout` | Provider is slow; raise `routing.timeout` or lower it and fail over faster |
| `upstream returned invalid JSON` | A proxy or captive portal returned HTML, or the base URL is wrong |
| `HTTP 404` + model hint | The model id does not exist at that provider |
| `HTTP 400` + model hint | Some providers use `400` for an unknown model |
| `HTTP 529` | Provider overloaded; retry policy already moved on |

For a local provider such as Ollama:

```bash
ollama serve
curl http://127.0.0.1:11434/v1/models
```

The provider `base_url` must include the API root. Ollama uses
`http://127.0.0.1:11434/v1`; a bare host or a trailing `/chat/completions` will
produce 404s. LM Studio and llama.cpp follow the same rule.

## 429 and key cooldowns

A `429` cools the route for `max(cooldown_seconds, min(Retry-After, 900))` and
the key for up to 600 seconds. `Retry-After` may be seconds or an HTTP date.

- Add a second key to the same provider: the next request rotates to it.
- Add a different provider to the chain for real redundancy.
- Check for an accidental retry loop in the agent: Hexgate caps attempts at
  `routing.max_attempts`, but the agent may be issuing new turns.

Key cooldowns are visible in **Providers** and in `GET /api/providers`.

## Secrets cannot be decrypted

```
Could not decrypt secrets.json. The machine fingerprint changed, or
HEXGATE_MASTER_PASSWORD is different.
```

The vault key is derived from `HEXGATE_MASTER_PASSWORD` if set, otherwise from
a machine fingerprint. Copying `secrets.json` to another machine without the
password will fail by design.

Options:

1. Set the same `HEXGATE_MASTER_PASSWORD` that was used to create the file.
2. Restore the old machine context (renamed user, changed host id).
3. Start over: `hexgate keys reset --all`, then re-add keys.

Back up `secrets.json` only together with the password; the file alone is
useless (and that is the point). See [configuration.md](configuration.md#secrets).

## Port already in use

```
[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8787)
```

Find and stop the other process:

```powershell
Get-NetTCPConnection -LocalPort 8787 | Select-Object OwningProcess
Get-Process -Id <pid>
```

```bash
lsof -i :8787
```

Or choose another port:

```bash
hexgate serve --port 8790
```

Remember to point clients at the new port. `hexgate launch` reads the port from
config, not from a one-off `--port` flag.

## Streaming hangs or arrives all at once

- Hexgate forwards SSE frame by frame; it never buffers a whole completion.
- A corporate proxy, VPN, or antivirus TLS inspector between the gateway and the
  provider may buffer `text/event-stream`. Test the provider directly with
  `curl -N`.
- Claude Code consumes Anthropic SSE. Hexgate emits a complete event sequence for
  a successful turn; if the upstream sends an error frame mid-stream, Hexgate
  forwards `event: error` and closes, which is how the Anthropic API behaves.
- OpenAI clients wait for `[DONE]`; Hexgate synthesises one if the upstream
  closes without it.
- If only the **first** token is slow, that is provider latency/TTFT, visible in
  the request record. Try the `fastest` strategy or a different chain order.

## Windows, WSL, and containers

- **WSL → Windows host**: `127.0.0.1` inside WSL is the Linux VM, not Windows.
  Use the Windows host IP (`ip route | grep default`), bind Hexgate to
  `0.0.0.0`, set `allow_remote = true`, and set a proxy token.
- **Windows → WSL**: use the WSL distro IP, which changes on restart; a port
  proxy (`netsh interface portproxy`) is more stable.
- **Docker**: `-p 8787:8787` publishes the port; the container must bind
  `0.0.0.0` (the image sets `HEXGATE_HOST=0.0.0.0`). A local provider on the
  host is not `127.0.0.1` from inside the container — use
  `host.docker.internal` (Docker Desktop) or the host gateway.
- **Firewall**: Windows may prompt on first bind. Loopback-only traffic does not
  need an inbound rule; remote access does.
- **Path length / permissions**: keep `HEXGATE_HOME` on a normal local disk.
  OneDrive-synced folders can lock `secrets.json` during atomic replace.

## The dashboard loads but actions fail

- The UI calls `/api/*`. If you set `auth.admin_token`, the dashboard asks for it
  and stores it in memory; a hard refresh clears it.
- `403` means remote access is off. `401` means the admin token is missing or
  wrong.
- If the page is stale after an upgrade, hard-refresh (`Ctrl+Shift+R`) to drop
  cached JS/CSS. There is no build step and no service worker.

## Wrong provider answered

Check the `x-hexgate-route` response header, the **Activity** row, or:

```bash
curl -s http://127.0.0.1:8787/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"hi"}]}' \
  -D -
```

`x-hexgate-attempts` greater than 1 means failover happened. The response
`model` is rewritten to what the client asked for, so it is not evidence of the
upstream.

## Token savings are zero

The optimizer only removes waste: trailing whitespace, empty turns, duplicate
system paragraphs, and oversized tool output. Short, clean prompts legitimately
save nothing. Check per-rule numbers in **Activity** or `GET /api/stats`. If you
disabled `[optimizer].enabled`, nothing is applied.

## Still stuck

Collect:

1. `hexgate version`, OS, Python version.
2. `hexgate doctor` output (it masks secrets).
3. The exact request shape **with keys and prompts removed**.
4. `x-hexgate-request-id` and the matching Activity row.
5. The upstream status/message, not just "it failed".

Then open an issue using the bug template. For security problems, follow
[SECURITY.md](../SECURITY.md) instead of filing publicly.
