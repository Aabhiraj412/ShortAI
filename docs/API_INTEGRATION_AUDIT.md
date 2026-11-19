# API Integration Audit (Stage 3.29)

Date: 2026-11-19 (IST). Sources: the codebase (every outbound host and environment variable), `.env.local`
(names only; **no value was printed, logged or copied**), the OAuth files' existence, provider-health records
written by the photo pipeline, and a one-off read-only probe of each public endpoint (HTTP status only).
Live view: dashboard → **API Providers** card, or `GET /api/providers/health` (local only; never returns values).

Status classes: **1** not implemented · **2** implemented, key missing · **3** configured but invalid ·
**4** configured and healthy · **5** not required (done locally) · **6** unavailable or restricted.
"No key needed" means implemented against an anonymous public API.

| Provider | Purpose | Credential (env var) | Configured | Valid / probe 2026-11-19 | Called by code | Test renders | Production | Limits | Failure behaviour → fallback | Class | Action |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Gemini API | script writing; chain `gemini-3.8-flash` → `gemini-3.6-flash` | `GEMINI_API_KEY` | yes | valid (models list 200); 503/504 on 3.8-flash seen 2026-11-16, recovered via 3.6-flash | yes | yes | yes | free-tier RPM/RPD caps | next model, then labelled extractive script | 4 | none |
| YouTube Data API v3 | upload, schedule, thumbnails, status verification | OAuth: `client_secrets.json` + token file (`youtube.upload`, `youtube.readonly`) | yes (both files) | connected; **daily quota exhausted 2026-11-17** while 24 hourly cycles ran | yes | no | yes | 10,000 units/day; upload ≈1,600, thumbnail 50 | upload stays `HANDOFF_PENDING`, never re-uploaded | 4 (quota-bound) | keep ≤ 5 uploads/day or request a quota increase in Google Cloud |
| YouTube Analytics API | performance metrics | scope `yt-analytics.readonly` | – | – | **no** | no | no | – | – | 1 | only if analytics are wanted (needs new OAuth consent) |
| Pixabay | stock photos for topic-level assets | `PIXABAY_API_KEY` | yes | valid (200) | yes | no | yes (legacy asset stage) | 100 req / 60 s | other providers | 4 | none |
| Pexels | stock photos | `PEXELS_API_KEY` | **no** | – | yes (inactive without key) | no | yes when set | 200 req/h, 20,000/month | other providers | 2 | optional: add key |
| Openverse | CC photos for scene-level selection | none (anonymous) | n/a | **504 Gateway Timeout** (probe and 6 of 14 render calls) | yes | yes | yes | anonymous rate limit | Wikimedia / NASA / Internet Archive | 6 (intermittent) | optional: registered Openverse client raises limits |
| Wikimedia Commons | CC/PD photos | none | n/a | 200 | yes | yes | yes | polite use | other providers | no key needed | none |
| Internet Archive | public-domain images | none | n/a | **connection failed** in probe; healthy during renders | yes | yes | yes | none published | other providers | 6 (intermittent) | none |
| NASA Image Library | space imagery | none | n/a | 200 | yes | yes | yes | none published | other providers | no key needed | none |
| Wikipedia REST + phys.org RSS | topic discovery, research | none | n/a | 200 / 200 | yes | yes | yes | polite use | other feeds | no key needed | none |
| Microsoft Edge neural TTS (`edge-tts`) | voice-over with emotion / rate / pitch modulation | none | n/a | working (every production 2026-11-16..18 completed TTS) | yes | yes | yes | unofficial free service | none: production fails at VOICEOVER | no key needed (single point of failure) | consider a paid TTS fallback only with approval |
| GNews | news topics | `GNEWS_API_KEY` | yes | key valid (200) | **no code uses it** | no | no | 100 req/day | – | 1 | remove the key or implement GNews discovery |
| SMTP (email) | cycle summaries and alerts | `CRAZZYDOCS_SMTP_*` | yes | not probed (would send mail) | yes | no | yes | provider dependent | dashboard notifications | 4 (assumed) | none |
| Illustrations, environments, icons, SFX, music bed, fonts | visuals and sound | none | n/a | local code / bundled OFL fonts | yes | yes | yes | – | – | 5 | none |
| AI image generation | – | – | – | – | **not implemented** (by design) | no | no | – | – | 1 | none |

## Findings

1. **No missing credential blocks production today.** Every provider production depends on is configured or
   needs no key.
2. **Openverse and Internet Archive are intermittently unavailable.** The pipeline already falls back to the other
   providers; no action is required.
3. **YouTube quota** is the real constraint. On 2026-11-17 an hourly schedule exhausted the 10,000-unit daily
   quota, and seven cycles failed at the publishing step. The current schedule (one cycle a day) is far below
   the quota.
4. **`GNEWS_API_KEY` is configured but never called.** A missing key does not mean missing code, and the reverse
   holds too: a configured key does not mean the integration exists.
5. **`PEXELS_API_KEY` is the only implemented integration whose key is missing.** It is optional.

## Keys you could obtain (none are required for production)

| Variable | Provider | Why | Cost |
|---|---|---|---|
| `PEXELS_API_KEY` | Pexels | one more licensed photo source when Openverse times out | free |
| (Openverse client credentials) | Openverse | higher anonymous rate limit; not wired in code yet | free |
| YouTube quota increase | Google Cloud console | only needed if more than ~5 uploads a day are planned | free (application) |
