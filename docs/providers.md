# Provider catalog

Hexgate ships with **41 providers** as data, not code. The catalog lives in
[`hexgate/data/providers.json`](../hexgate/data/providers.json) and is loaded at startup,
so adding a provider is a JSON edit, not a Python change.

Every provider is **disabled until you opt in**. Adding a key in the dashboard (or with
`hexgate keys set`) enables it automatically. That means a fresh install never sends a
request anywhere by accident.

## Model references

A model reference is always `provider-id/model-id`:

```text
groq/llama-3.3-70b-versatile
openrouter/anthropic/claude-sonnet-4.5
nvidia_nim/meta/llama-3.3-70b-instruct
ollama/qwen2.5-coder
```

Model IDs are passed through verbatim. Vendor prefixes such as `meta-llama/` or
`accounts/fireworks/models/` are part of the ID, so keep the slashes.

If a provider publishes models that change over time, open its card in **Providers** and
click **Fetch models**. The result is written to `config.toml` so the dropdown stays useful
offline.

## Tiers

| Tier | What it means |
| --- | --- |
| `free` | No card, permanent free tier |
| `trial` | Credits expire |
| `paid` | Metered or subscription |
| `local` | Localhost inference |
| `custom` | Your own endpoint |

The tier only affects the *implicit* ordering when you have not configured a chain.
Explicit chains and direct `provider/model` references always win.

## Free (14)

No card required, or a permanent free tier. These are the best starting point for a coding agent.

| Provider | ID | Default model | Key env | Notes |
| --- | --- | --- | --- | --- |
| [OpenRouter](https://openrouter.ai/keys) | `openrouter` | `openrouter/auto` | `OPENROUTER_API_KEY` | One key, hundreds of models, including a rotating set of free ones. Best starting point. |
| [NVIDIA NIM](https://build.nvidia.com/settings/api-keys) | `nvidia_nim` | `meta/llama-3.3-70b-instruct` | `NVIDIA_NIM_API_KEY`, `NVIDIA_API_KEY` | Generous free developer credits. Model IDs look like vendor/model. |
| [Groq](https://console.groq.com/keys) | `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | Extremely fast inference. Great as the first hop for cheap tool-calling turns. |
| [Google AI Studio](https://aistudio.google.com/apikey) | `google` | `gemini-2.5-flash` | `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Free tier with long context. Uses Google's OpenAI-compatible surface. |
| [Cerebras](https://cloud.cerebras.ai/) | `cerebras` | `llama-3.3-70b` | `CEREBRAS_API_KEY` | Wafer-scale inference, very high tokens/second. Free tier is rate limited. |
| [Mistral](https://console.mistral.ai/) | `mistral` | `devstral-small-latest` | `MISTRAL_API_KEY` | Devstral is tuned for agentic coding. A free experimentation tier exists. |
| [SiliconFlow](https://cloud.siliconflow.com/account/ak) | `siliconflow` | `Qwen/Qwen3-32B` | `SILICONFLOW_API_KEY` | Chinese-hosted open models with a free tier on selected IDs. |
| [Chutes](https://chutes.ai/app) | `chutes` | `deepseek-ai/DeepSeek-V3` | `CHUTES_API_KEY` | Decentralised GPU network with a free quota. |
| [SambaNova](https://cloud.sambanova.ai/apis) | `sambanova` | `Meta-Llama-3.3-70B-Instruct` | `SAMBANOVA_API_KEY` | Fast inference with a free developer tier. |
| [Alibaba DashScope](https://www.alibabacloud.com/help/en/model-studio/get-api-key) | `alibaba` | `qwen3-coder-plus` | `DASHSCOPE_API_KEY`, `ALIBABA_CLOUD_API_KEY` | International endpoint shown. Mainland accounts should switch to dashscope.aliyuncs.com. |
| [GitHub Models](https://github.com/settings/tokens) | `github_models` | `openai/gpt-4o-mini` | `GITHUB_MODELS_TOKEN`, `GITHUB_TOKEN` | Free with a GitHub account and rate limits. Needs a PAT with models:read. |
| [Cloudflare Workers AI](https://dash.cloudflare.com/profile/api-tokens) | `cloudflare` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | `CLOUDFLARE_API_TOKEN` | Replace {account_id} in the Base URL with your Cloudflare account ID. |
| [Hugging Face Router](https://huggingface.co/settings/tokens) | `huggingface` | `Qwen/Qwen3-Coder-480B-A35B-Instruct` | `HUGGINGFACE_API_KEY`, `HF_TOKEN` | Routes to multiple inference partners under one token. |
| [LLM7.io](https://dash.llm7.io/) | `llm7` | `default` | `LLM7_API_KEY` | Free community gateway; the key is optional but raises limits. |

## Trial (2)

Free credits or a time-limited trial. Great for evaluation, not for a daily driver.

| Provider | ID | Default model | Key env | Notes |
| --- | --- | --- | --- | --- |
| [Cohere](https://dashboard.cohere.com/api-keys) | `cohere` | `command-r-plus-08-2024` | `COHERE_API_KEY` | Trial keys are rate limited but free. |
| [Weights & Biases Inference](https://wandb.ai/settings) | `wandb` | `openai/gpt-oss-20b` | `WANDB_API_KEY` | Free during the preview period. |

## Paid (20)

Metered or subscription billing. Frequently cheaper per token than a frontier API.

| Provider | ID | Default model | Key env | Notes |
| --- | --- | --- | --- | --- |
| [DeepSeek](https://platform.deepseek.com/api_keys) | `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` | Very low cost per token with solid coding results. |
| [OpenAI](https://platform.openai.com/api-keys) | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` | Billed separately from a ChatGPT subscription. Use an API key here. |
| [Anthropic](https://console.anthropic.com/settings/keys) | `anthropic` | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` | Native Anthropic protocol. Useful as a last-resort fallback or for exact parity tests. |
| [xAI (Grok)](https://console.x.ai/) | `xai` | `grok-3-mini` | `XAI_API_KEY` | Grok Code Fast is priced for agent loops. |
| [Together AI](https://api.together.ai/settings/api-keys) | `together` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | `TOGETHER_API_KEY` | Wide open-weight catalog with OpenAI-compatible tool calling. |
| [DeepInfra](https://deepinfra.com/dash/api_keys) | `deepinfra` | `meta-llama/Llama-3.3-70B-Instruct` | `DEEPINFRA_API_KEY` | Cheap open-weight hosting with per-token billing. |
| [Fireworks AI](https://fireworks.ai/account/api-keys) | `fireworks` | `accounts/fireworks/models/llama-v3p3-70b-instruct` | `FIREWORKS_API_KEY` | Model IDs include the accounts/fireworks/models prefix. |
| [Novita AI](https://novita.ai/settings/key-management) | `novita` | `meta-llama/llama-3.3-70b-instruct` | `NOVITA_API_KEY` | Low-cost inference with a free credit on signup. |
| [Nebius AI Studio](https://studio.nebius.ai/) | `nebius` | `meta-llama/Llama-3.3-70B-Instruct` | `NEBIUS_API_KEY` | European-hosted open models; free tier available at times. |
| [Featherless AI](https://featherless.ai/account/api-keys) | `featherless` | `Qwen/Qwen2.5-Coder-32B-Instruct` | `FEATHERLESS_API_KEY` | Flat-rate access to a large open-weight catalog. |
| [Moonshot (Kimi)](https://platform.moonshot.ai/console/api-keys) | `moonshot` | `kimi-k2-0905-preview` | `MOONSHOT_API_KEY`, `KIMI_API_KEY` | Kimi K2 is strong at long-horizon agent work. |
| [MiniMax](https://platform.minimax.io/user-center/basic-information/interface-key) | `minimax` | `MiniMax-M2` | `MINIMAX_API_KEY` | OpenAI-compatible endpoint. Check regional host if you signed up in CN. |
| [Z.ai (GLM)](https://z.ai/manage-apikey/apikey-list) | `zai` | `glm-4.6` | `ZAI_API_KEY`, `ZHIPU_API_KEY` | The Coding Plan is personal-use only; use a pay-as-you-go key for shared machines. |
| [Azure OpenAI](https://portal.azure.com/) | `azure_openai` | `—` | `AZURE_OPENAI_API_KEY` | Set Base URL to https://<resource>.openai.azure.com/openai/v1 and use your deployment name as the model. |
| [Vercel AI Gateway](https://vercel.com/dashboard/ai-gateway) | `vercel` | `openai/gpt-4o-mini` | `AI_GATEWAY_API_KEY`, `VERCEL_AI_GATEWAY_API_KEY` | Unified billing across vendors; useful for cost tracking. |
| [OpenCode Zen](https://opencode.ai/auth) | `opencode_zen` | `—` | `OPENCODE_API_KEY` | Curated coding models behind one key; model IDs vary, use Fetch models. |
| [Scaleway](https://console.scaleway.com/iam/api-keys) | `scaleway` | `llama-3.3-70b-instruct` | `SCW_SECRET_KEY` | EU-hosted inference with data residency options. |
| [Baseten](https://app.baseten.co/settings/api_keys) | `baseten` | `—` | `BASETEN_API_KEY` | Model IDs are deployment-specific; discover them from the provider. |
| [Poolside](https://platform.poolside.ai/) | `poolside` | `—` | `POOLSIDE_API_KEY` | Laguna models are purpose-built for code agents. |
| [Ollama Cloud](https://ollama.com/settings/keys) | `ollama_cloud` | `qwen3-coder:480b` | `OLLAMA_API_KEY` | Hosted versions of Ollama models; local Ollama is a separate provider. |

## Local (4)

Runs on your machine. No key, no quota, no data leaving the box.

| Provider | ID | Default model | Key env | Notes |
| --- | --- | --- | --- | --- |
| [Ollama (local)](https://ollama.com/download) | `ollama` | `llama3.1` | — | Runs on your machine. No key, no quota, no data leaving the box. |
| [LM Studio (local)](https://lmstudio.ai/) | `lmstudio` | `—` | — | Start the local server in LM Studio, then Fetch models to populate the list. |
| [llama.cpp (local)](https://github.com/ggml-org/llama.cpp) | `llamacpp` | `—` | — | Run llama-server with --jinja so tool calls survive the round trip. |
| [vLLM (local)](https://docs.vllm.ai/) | `vllm` | `—` | — | Point at any vLLM OpenAI-compatible deployment, local or remote. |

## Custom (1)

Bring your own OpenAI-compatible endpoint (a private gateway, a university cluster, anything).

| Provider | ID | Default model | Key env | Notes |
| --- | --- | --- | --- | --- |
| Custom endpoint | `custom` | `—` | — | Any OpenAI-compatible server. Set the Base URL, optional key, then Fetch models. |

## Adding a provider without touching the catalog

The `custom` entry is a blank slot you can point anywhere. In the dashboard, open
**Providers > Custom endpoint**, set the Base URL (for example
`http://192.168.1.20:8000/v1`), add an optional key, and either type model IDs one per
line or click **Fetch models**.

The same thing without the dashboard is a short TOML edit:

```toml
[providers.custom]
label = "Homelab vLLM"
enabled = true
api = "openai"
base_url = "http://192.168.1.20:8000/v1"
models = ["qwen3-coder-30b"]
default_model = "qwen3-coder-30b"
requires_key = false
```

For a permanent addition that shows up for everyone, add an entry to
`hexgate/data/providers.json` and send a pull request. Keep `enabled` false so existing
installs are unaffected.

## Notes on specific providers

- **Azure OpenAI** — set the Base URL to `https://<resource>.openai.azure.com/openai/v1`
  and use the *deployment name* as the model ID, not the underlying model name.
- **Cloudflare Workers AI** — copy your account ID into the Base URL where it says
  `{account_id}`. Testing the provider will tell you if you forgot.
- **GitHub Models** — needs a personal access token with `models:read`. The built-in
  `GITHUB_TOKEN` from Actions is not always sufficient.
- **NVIDIA NIM / Together / Fireworks / Nebius** — keep the full vendor prefix in the
  model ID (`meta/llama-3.3-70b-instruct`, `accounts/fireworks/models/...`).
- **Local servers** — enable the provider, click **Test**, then **Fetch models**. If the
  card says *connection refused*, the server is not running on the expected port.
- **Ollama Cloud vs Ollama** — they are separate entries with separate keys and hosts.
- **Anthropic** — this is the only cloud provider that speaks the Anthropic wire
  protocol natively. Hexgate translates the other direction for everyone else.

Free-tier limits are set by each provider and change without notice. A 429 is not a bug;
Hexgate will cool the key and try the next route in your chain.
