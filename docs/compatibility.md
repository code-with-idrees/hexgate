# Client compatibility

Hexgate exposes three wire protocols on one port:

| Surface | Endpoint | Clients |
| --- | --- | --- |
| Anthropic Messages | `POST /v1/messages` (+ `count_tokens`) | Claude Code, Anthropic SDKs |
| OpenAI Chat Completions | `POST /v1/chat/completions` | OpenCode, Aider, Cline, Continue, OpenAI SDKs |
| OpenAI Responses API | `POST /v1/responses` | Codex CLI custom providers (`wire_api = "responses"`) |
| Discovery | `GET /v1/models` | Clients that populate a model picker |

Both surfaces share the same router, provider catalog, retry policy, circuit
breakers, key vault and token optimizer. A request that arrives as Anthropic
can be served by an OpenAI-shaped provider, and vice versa; Hexgate translates
the request, the response, and every streaming event in between.

The internal canonical format is **OpenAI Chat Completions**, because it has the
widest provider support. Anthropic is the entry dialect for Claude Code and the
upstream dialect for Anthropic-compatible providers.

## Agent setup

### Claude Code

```bash
hexgate launch claude
```

or set the environment yourself:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_AUTH_TOKEN=hexgate-local   # your proxy token, if configured
export ANTHROPIC_API_KEY=hexgate-local
```

Claude Code sends a model such as `claude-sonnet-4-5`; Hexgate maps the
`sonnet` / `opus` / `haiku` / `fable` token to that tier's chain, so `/model`
still selects a meaningful route. Tool use, extended thinking output, images
and streaming are translated. Thinking **signatures** are not preserved across
providers (see [Field mapping](#field-mapping)).

### Codex CLI

Supported via the OpenAI Responses API at `POST /v1/responses`. Current Codex
CLI only speaks `wire_api = "responses"` for custom providers, and Hexgate now
proxies that wire format end to end — translating the Codex request shape into
canonical Chat Completions, routing it through the normal failover pipeline, and
translating the response back into Responses API SSE events.

```bash
hexgate launch codex
```

or configure `~/.codex/config.toml` yourself:

```toml
[model_providers.hexgate]
name = "Hexgate"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
env_key = "HEXGATE_API_KEY"
```

```bash
export HEXGATE_API_KEY="hexgate-local"
codex --model gpt-5
```

Model labels such as `claude-sonnet-4-5` are routing hints that resolve to your
tier chain; explicit `provider/model` routes go straight to a configured provider.
Tool calls and function-call outputs are translated both ways.

### OpenCode

OpenCode is configured through `opencode.json` using its OpenAI-compatible
provider. Point `baseURL` at `/v1` and choose any model label; the label is a
routing hint, not necessarily a real upstream id.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "hexgate": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Hexgate",
      "options": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "hexgate-local"
      },
      "models": {
        "claude-sonnet-4-5": { "name": "Hexgate Sonnet" },
        "claude-haiku-4-5": { "name": "Hexgate Haiku" }
      }
    }
  }
}
```

`hexgate launch opencode` starts the agent with `OPENAI_BASE_URL` and
`OPENAI_API_KEY` injected; use the config file above if OpenCode asks for a
provider explicitly.

### Aider

```bash
hexgate launch aider --model openai/hexgate-sonnet
```

`launch aider` adds `--openai-api-base` automatically. Without `--model`,
Aider's default model name is treated as a routing hint: it falls through to
the default chain.

### Cline, Continue, and other OpenAI-compatible clients

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8787/v1` |
| API key | `hexgate-local`, or your `auth.proxy_token` |
| Model | any label, or `provider/model` for a direct route |

If your client insists on a non-empty model list, open
`http://127.0.0.1:8787/v1/models`; it lists the tier chains you have configured
(`hexgate/sonnet`, `hexgate/default`, …) and every enabled provider model.

## Field mapping

### Anthropic request → OpenAI request

| Anthropic | Canonical OpenAI | Notes |
| --- | --- | --- |
| `model` | `model` | Replaced with the routed candidate's model before egress |
| `max_tokens` | `max_tokens` | Forwarded as-is |
| `temperature`, `top_p`, `stream` | same | `top_k` has no OpenAI equivalent and is dropped |
| `system` (string or text blocks) | `role: "system"` message | Multiple text blocks are concatenated |
| `messages[].content` text | `content` string | Consecutive text is joined |
| `image` block (base64 or URL) | `image_url` part | Data URLs preserve the media type |
| assistant `text` blocks | assistant `content` | |
| assistant `tool_use` | `tool_calls[]` | `input` is JSON-encoded into `function.arguments` |
| user `tool_result` | `role: "tool"` | `tool_use_id` becomes `tool_call_id`; `is_error` prefixes the text |
| `stop_sequences` | `stop` | Truncated to 4 entries |
| `metadata.user_id` | `user` | |
| `tools[].input_schema` | `tools[].function.parameters` | |
| `tool_choice: any` | `required` | |
| `tool_choice: tool` | named function | |
| `thinking` blocks in history | *dropped* | Most OpenAI providers reject them |

Ignored on this path: `top_k`, `cache_control`, `thinking` / `budget_tokens`,
`anthropic-beta` headers, `container`, `mcp_servers`, `context_management`,
`service_tier` and any block type Hexgate does not recognise.

### OpenAI request → Anthropic request

| OpenAI | Anthropic | Notes |
| --- | --- | --- |
| `system` and `developer` messages | `system` | Joined with blank lines |
| `max_tokens` / `max_completion_tokens` | `max_tokens` | Default 8192 when neither is set |
| `temperature`, `top_p`, `stream` | same | |
| user text parts | `text` blocks | |
| `image_url` (data or remote) | `image` block | `detail` is ignored |
| assistant `reasoning_content` | `thinking` block | Empty signature |
| assistant `tool_calls` | `tool_use` blocks | Arguments parsed as JSON; invalid JSON becomes `{}` |
| `role: "tool"` | user `tool_result` | Consecutive tool results merge into one user turn |
| `stop` | `stop_sequences` | Truncated to 4 entries |
| `tools[].function` | Anthropic tool | `parameters` becomes `input_schema` |
| `tool_choice: required` | `any` | |
| `tool_choice: none` | `auto` | Anthropic has no "none"; this is a semantic loss |

Ignored on this path: `n`, `seed`, `response_format`, `logprobs`,
`top_logprobs`, `presence_penalty`, `frequency_penalty`,
`parallel_tool_calls`, `stream_options`, `reasoning_effort`, `modalities`,
`audio`, `prediction`, `store`, `metadata`, `service_tier`, and legacy
`functions` / `function_call`. Only the first choice is translated.

### Response mapping

| OpenAI response | Anthropic response |
| --- | --- |
| `choices[0].message.content` | `text` block |
| `reasoning_content` / `reasoning` | `thinking` block (signature `""`) |
| `tool_calls` | `tool_use` blocks |
| `finish_reason: stop` | `stop_reason: end_turn` |
| `length` | `max_tokens` |
| `tool_calls` / `function_call` | `tool_use` |
| `content_filter` | `end_turn` |
| `usage.prompt_tokens` | `usage.input_tokens` |
| `usage.completion_tokens` | `usage.output_tokens` |
| `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens` |

The reverse mapping uses the same table with these differences: `stop_reason:
stop_sequence` becomes `finish_reason: stop`; `reasoning` blocks become
`reasoning_content`; and an Anthropic response with tool blocks always gets
`finish_reason: tool_calls`.

If a provider omits usage, Hexgate estimates it at roughly **4 characters per
token** and marks the record as estimated. Response ids are normalised to
`msg_…` or `chatcmpl-…` so SDKs accept them.

## Streaming

### OpenAI upstream → Anthropic client

```text
message_start → ping → content_block_start (thinking|text|tool_use)
  → content_block_delta (thinking_delta|text_delta|input_json_delta)
  → content_block_stop → … → message_delta → message_stop
```

- Tool-call fragments are buffered until a function name is known, then
  replayed as `input_json_delta` events.
- **Parallel tool calls are emitted sequentially.** Each tool gets its own
  content block; Anthropic clients accept the blocks, but interleaved arrival
  order is not preserved.
- An upstream `{"error": …}` frame becomes an Anthropic `event: error` and a
  final `message_stop` is omitted, matching Anthropic's behaviour.
- A missing `usage` block is estimated from the number of text characters.

### Anthropic upstream → OpenAI client

```text
role chunk → content / reasoning_content / tool_calls deltas
  → final chunk with usage and finish_reason → data: [DONE]
```

- `thinking_delta` becomes `reasoning_content`.
- Each `tool_use` block starts a new `tool_calls` entry; `input_json_delta`
  fragments are forwarded unchanged as `function.arguments`.
- Exactly one `[DONE]` is emitted, even if the upstream closes without one.
- An Anthropic `event: error` becomes an OpenAI error frame, followed by
  `[DONE]`.

### Same-protocol streams

Same-protocol traffic is passed through event by event with one change: the
`model` field is rewritten back to the model the client requested, so fallback
routes are invisible to the agent. A missing `[DONE]` is synthesised. Unknown
Anthropic event types are re-emitted with their original `event:` name; SSE
comment lines are not forwarded.

## What is not proxied

| Endpoint | Status | Why |
| --- | --- | --- |
| `/v1/responses` (OpenAI Responses API) | **Supported** | Codex CLI custom providers use `wire_api = "responses"`; Hexgate translates this into Chat Completions for routing and back into Responses SSE events |
| `/v1/embeddings` | Not supported | Not used by coding agents; would need per-provider dimensions |
| `/v1/images/generations` | Not supported | Not a chat completion |
| `/v1/audio/*` | Not supported | Not a chat completion |
| `/v1/files`, `/v1/batches` | Not supported | Stateful provider-specific APIs |
| Anthropic `/v1/complete` (legacy) | Not supported | Superseded by Messages |
| Anthropic `count_tokens` | **Estimated** | ~4 chars/token; no provider tokenizer is bundled |
| Prompt caching (`cache_control`) | Dropped | Byte-exact cache keys cannot survive translation |
| Thinking signatures | Dropped | Provider-specific and unverifiable |
| `top_k` on OpenAI providers | Dropped | No equivalent field |
| `n > 1` on Anthropic providers | First choice only | Anthropic returns one completion per request |

These are deliberate limits, not partial implementations. Codex CLI is bridged
via the supported `/v1/responses` endpoint above.

## Authentication and headers

- Anthropic clients normally send `x-api-key`; OpenAI clients send
  `Authorization: Bearer`. Hexgate accepts either, plus `x-hexgate-token` and
  `?token=` for clients that cannot set headers.
- The proxy token protects `/v1/*`. The admin token protects `/api/*` and
  `/metrics`. They are separate.
- Client headers other than authentication are not forwarded upstream. Provider
  authentication is attached by Hexgate from the vault, never from the client.
- Responses include `x-hexgate-route`, `x-hexgate-attempts` and
  `x-hexgate-request-id` on non-streaming requests. Streaming responses expose
  the same information in the dashboard and request records.

## Troubleshooting a client

1. `curl http://127.0.0.1:8787/healthz` must return `ok`.
2. `curl http://127.0.0.1:8787/v1/models` must list at least one model. An empty
   list means no provider is enabled.
3. `hexgate route <model>` shows the exact plan, including skipped circuits.
4. If the client gets a `400`, the request is malformed upstream and is
   deliberately **not** retried; check the provider's message in the dashboard.
5. If the client gets a `503`, no route exists: add a key or set a chain.
6. If streaming stops at `message_start`, the upstream died mid-stream; the
   request record contains the error and the circuit will open after repeated
   failures.

See [troubleshooting.md](troubleshooting.md) for the full matrix.
