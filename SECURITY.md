# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | Yes |
| < 1.0 | No |

Only the latest 1.x release receives security fixes.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting:

<https://github.com/code-with-idrees/hexgate/security/advisories/new>

Include:

- affected version and OS;
- a minimal reproduction (config shape, request, expected vs actual);
- impact: what an attacker gains;
- whether the gateway was exposed beyond loopback.

You will get an acknowledgement as soon as possible, usually within a few days.
Once a fix is ready, the advisory is published with credit unless you ask to
remain anonymous. Please give us a reasonable window before public disclosure.

## Threat model

Hexgate is a **local developer tool**, not a multi-tenant service.

It assumes:

- the machine and your user account are trusted;
- the gateway listens on loopback, or on a network you control;
- provider API keys are valuable and must not leak to clients or disk in clear
  text;
- prompts and completions are sensitive and must not leave the machine except to
  the providers you configure.

It does **not** provide:

- TLS termination;
- user accounts, roles, or per-user quotas;
- isolation between clients that share the same proxy token;
- protection from a compromised local account (it can read the config, process
  memory, and your shell history);
- audit logging suitable for compliance.

## Controls

- **Loopback by default.** `/v1`, `/api` and `/metrics` reject non-loopback
  clients with `403` unless `auth.allow_remote = true`. `/healthz` and the
  favicon stay public. The Docker image sets `HEXGATE_ALLOW_REMOTE=true`
  because Docker NAT makes even host clients non-loopback; the Compose file
  publishes the port on `127.0.0.1` only.
- **Separate tokens.** `auth.proxy_token` guards proxy traffic;
  `auth.admin_token` guards the admin API and metrics. A proxy token cannot read
  the vault or change configuration.
- **Constant-time comparison.** Tokens are compared with
  `hmac.compare_digest`. Accepted forms: `Authorization: Bearer`,
  `x-api-key`, `x-hexgate-token`, `?token=` (last resort for clients that cannot
  set headers; URLs may end up in logs).
- **Encrypted keys.** AES-256-GCM with a scrypt-derived key, random 96-bit nonce
  per write, and an authenticated envelope. The default key material is the
  machine fingerprint; set `HEXGATE_MASTER_PASSWORD` for a portable vault.
  `secrets.json` is written atomically with `0600` where the OS supports it.
- **No key exfiltration.** The admin API returns masked previews only. Keys are
  attached to upstream requests by the gateway, never accepted from clients.
- **Header hygiene.** Responses carry CSP, `nosniff`, `DENY` framing,
  `no-referrer` and COOP. The dashboard has no inline scripts and no remote
  assets.
- **Bounded resources.** Request records are capped by `telemetry.retain`;
  timeouts, attempt counts and `Retry-After` cooldowns are bounded.
- **No telemetry.** Hexgate does not phone home. Outbound traffic is limited to
  the providers you enable.

## Known non-issues

These are expected behaviour, not vulnerabilities:

- Anyone who can read `secrets.json` **and** reproduce the machine fingerprint
  (or knows the master password) can decrypt keys. That is the threat model of
  local encryption.
- A loopback client without a configured token has full access. Set
  `auth.proxy_token` and `auth.admin_token` if other local users or processes
  are untrusted.
- The gateway forwards prompts to the provider you route to. That is its job.
- Provider error messages are returned to the client; they may contain model
  names or request ids.
- `?token=` appears in proxy logs and browser history by design; prefer headers.
- Denial of service from a local process is out of scope. There is no per-client
  rate limit.

## Hardening checklist

If you enable remote access:

1. Bind behind a TLS reverse proxy or an SSH/VPN tunnel. Never expose plain
   HTTP to the internet.
2. Set long random `proxy_token` and `admin_token` values, different from each
   other.
3. Restrict source addresses at the firewall or reverse proxy.
4. Set `HEXGATE_MASTER_PASSWORD` so the vault is not tied to the host.
5. Rotate provider keys if the host is shared or has been compromised.
6. Keep request retention short if prompts are sensitive.

## Dependencies

Runtime dependencies are `fastapi`, `uvicorn`, `httpx` and `cryptography`.
Dependabot is enabled for security updates. If you find a vulnerability in a
dependency, report it upstream as well; we will pin or patch as needed.

## Safe harbour

We will not pursue legal action against researchers who:

- test against their own installation;
- avoid privacy violations, data destruction, and service disruption;
- report findings privately and give us time to fix them.
