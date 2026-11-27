# Free AI providers for script writing (2026-11-28)

## Why

On 2026-11-27 the Gemini free tier's per-model daily caps were exceeded: Gemini 3.8 Flash 35/20 RPD and Gemini 3.6 Flash 33/20 RPD (Gemma 4 26B 31/14.4K, Gemma 4 31B 33/14.4K). After that, every production fell back to the extractive script. The extractive script measured 13 points lower overall (storytelling 25 vs 79).

## Providers evaluated (official documentation, read 2026-11-28)

| Provider | Free API? | Free limits (as documented) | Decision |
|---|---|---|---|
| **Groq** | Yes (Free plan) | `openai/gpt-oss-120b` and `openai/gpt-oss-20b`: 30 RPM, 1,000 RPD, 8K TPM, 200K TPD each ([rate-limits](https://console.groq.com/docs/rate-limits); the tab label was not confirmed in the extracted page). Both are "Production" models. JSON object mode is available on all models; strict `json_schema` on gpt-oss. OpenAI-compatible: `https://api.groq.com/openai/v1`. Headers: `x-ratelimit-limit/remaining/reset-requests` (RPD), `-tokens` (TPM), `retry-after` on 429. | **Integrated as the first alternate, disabled by default.** The key exists (free plan confirmed by live headers). It is used only once `CRAZZYDOCS_LLM_ALTERNATES` names it. Benchmarked (below). |
| Cloudflare Workers AI | Yes | 10,000 Neurons/day, reset 00:00 UTC. On Workers Free, going over **fails with an error (not billed)**. Text generation 300 RPM. gpt-oss-120b costs 31,818 Neurons per M input and 68,182 per M output tokens, so a script is roughly 250–300 Neurons, about 35 scripts/day. | Evaluated; **not integrated**. It needs an account id + API token and a live integration test. The REST/OpenAI-compatible endpoint is not on the pages read. Good candidate for the next step. |
| OpenRouter | `:free` models only | 20 RPM. 50 requests/day with fewer than 10 credits ever purchased, 1,000/day otherwise. Extra per-IP burst limits. A negative credit balance can cause errors even on free models. | Not integrated: 50 RPD is too low to rely on, and model availability varies. |
| Mistral | No | "Free mode … doesn't include API access. Creating API keys and using the API require billing activation." | Rejected: no zero-cost API. |
| SambaNova | Unverified | The rate-limits page returned 404; no free-tier page could be found. | Rejected until it can be verified. |
| xAI (Grok) | No | No free API allowance stated; paid prices only. | Rejected (comparison only). |

## How it works

- **Selection** (`backend/app/services/llm_providers.py`):
  - The order is the existing Gemini chain (`GEMINI_MODEL` → `GEMINI_FALLBACK_MODELS`), then the alternates named in `CRAZZYDOCS_LLM_ALTERNATES`, for example `groq:openai/gpt-oss-120b`.
  - Gemini stays primary unless `CRAZZYDOCS_LLM_PRIMARY=groq` is set explicitly.
  - Nothing is enabled automatically.
- **No paid model, ever:**
  - Each provider has a verified free-model allow-list, and any other model is refused.
  - Groq's paid Developer plan has higher limits. If a response's `x-ratelimit-limit-requests` is above the free RPD, the provider is marked `not_free_plan` and is not used again until an operator clears the mark.
- **Quota tracking:**
  - The tracker keeps per `provider:model` counts: RPM, TPM, RPD and TPD.
  - It also stores the provider's rate-limit headers, quota failures (never counted as requests) and cooldowns.
  - State lives in `logs/llm_providers/quota_state.json`; this is runtime state, not configuration.
- **429 handling:**
  - A 429 is never retried. The model cools down until the provider's reset:
    - `retry-after`, or
    - `x-ratelimit-reset-requests`, or
    - for Gemini's per-day quota, the next Pacific midnight + 5 min.
  - Overloads and timeouts keep the bounded retry (4 s, 12 s), then a 60 → 180 → 600 s cooldown.
  - The writer's single deferred round may retry an overloaded model, but never a quota-exhausted one.
- **Never blocks:**
  - Models in cooldown are skipped without a request.
  - When nothing is usable, the writer immediately uses the labelled extractive fallback.
- **Contracts unchanged:**
  - The same prompt, five-stage JSON, validators and quality evaluation apply to every provider.
  - `script_source` names the provider, e.g. `llm:groq:openai/gpt-oss-120b`.
  - Successful scripts are still cached per production by `StageArtifactCache`, so a retry does not regenerate them.
- **Dashboard:**
  - API Providers → "AI script providers" shows the order, whether each target is usable, the requests used today against the limit, and any cooldown.
  - `GET /api/llm-providers/status` returns the same data; key values are never included.

## Benchmark

`backend/scripts/benchmark_llm_providers.py --targets gemini:<model>,groq:openai/gpt-oss-120b --topics 3 --env-file <.env.local>`

The script reads existing research dossiers (read-only) and uses the production prompt and validators. It records JSON compliance, acceptance, quality scores, latency, tokens and headers to `logs/llm_benchmark/`, separate from configuration.

Runs on 2026-11-28, 02:50–03:40 IST, on 5 recent production topics (Hibiscus Rising, a pneumatic-muscle paper, Toy Story, NSA encryption systems, Guiengola). The key in `.env.local` is on Groq's **free plan**: live header `x-ratelimit-limit-requests: 1000` equals the documented free RPD.

| Target | Final run (strict JSON schema, 5,000-token cap, production retry) | All runs, accepted scripts | Median latency | Overall quality (mean) |
|---|---|---|---|---|
| `groq:openai/gpt-oss-20b` | 3 of 3 executed accepted (2 skipped by the TPM guard) | 9 | ~3.5 s | **83.9** (n=9) |
| `groq:openai/gpt-oss-120b` | 4 of 4 executed accepted (1 skipped by the TPM guard) | 9 | ~6.5 s | 78.5 (n=9) |
| `gemini:gemma-4-31b-it` | HTTP 503 (overloaded) on 3 of 4 attempts | 1 | 90 s | 88.1 (n=1) |
| `gemini:gemini-3.8-flash` | HTTP 429, daily quota until 13:35 IST | 0 | — | — |

**Reference from production history (different topics):**
- Gemini model scripts: 89.7 overall.
- Extractive fallback: 76.2.

**What the numbers mean:**
- **Quality.** Groq scripts score between extractive and Gemini.
- **Factual accuracy.** Every accepted Groq script passed the production validators: five stages, length, every number present in the research, and no copied 9-word run.
- **Rejected drafts.** Two drafts were rejected for copied text and fixed on the retry.
- **Measured problems, both fixed:**
  - About 20 % of answers in plain JSON object mode failed with `json_validate_failed` (truncated by reasoning at a 3,000-token cap).
  - With strict `json_schema`, a 5,000-token cap and invalid JSON treated as a rejected draft (one retry), the failure rate in the final run was 0 of 7.
- **Throughput.** A script call uses about 2,300–4,600 tokens, so 8K TPM allows about 1–2 calls per minute. The tracker reserves 4,500 tokens per call and skips the model rather than exceeding the limit. Production makes at most about 2 calls an hour.
- **Not measured:**
  - Hook strength separately (the overall score only).
  - Hindi translation quality.
  - A same-topic Gemini Flash comparison, because the quota is exhausted until 13:35 IST.

**Verdict:** Groq is integrated, verified against the real API and benchmarked. **Enabled on 2026-11-28 (operator approved)** as the backup writer after the Gemini chain: `CRAZZYDOCS_LLM_ALTERNATES=groq:openai/gpt-oss-20b,groq:openai/gpt-oss-120b` in `.env.local`. Gemini stays primary. Configuration is read from the process environment, then `.env.local`.

## To enable Groq (operator)

`GROQ_API_KEY` is already in `.env.local` (free plan). To use Groq when Gemini is exhausted:
1. Add `CRAZZYDOCS_LLM_ALTERNATES=groq:openai/gpt-oss-20b,groq:openai/gpt-oss-120b` to `.env.local`.
2. Restart through the deploy procedure.

Optionally, re-run the benchmark against Gemini Flash after 13:35 IST for a same-topic comparison.

Hindi translation (`localization/translator.py`) still uses only its own Gemini/Gemma chain. Groq Hindi quality has not been evaluated.
