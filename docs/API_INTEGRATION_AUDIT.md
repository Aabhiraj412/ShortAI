# API Integration Audit (Stage 3.29, updated Stage 3.30)

> **Stage 3.30 updates (2026-11-20):** YouTube quota model corrected (see below and docs/STAGE_3_30_STATUS.md);
> YouTube Analytics and GNews are now implemented; Openverse is INTERMITTENT (live timeouts/504 on 2026-11-20);
> a configured key is reported UNVERIFIED until a real request succeeds. Rows below were revised accordingly.

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
| YouTube Data API v3 | upload, schedule, thumbnails, status verification | OAuth: `client_secrets.json` + token file (`youtube.upload`, `youtube.readonly`) | yes (both files) | connected. The 11-17..11-19 "quota exhausted" was CrazzyDocs' own local counter (1,600/upload, UTC day), not Google: Cloud Console showed 46/10,000 | yes | no | yes | general 10,000 units/day; `videos.insert` own bucket 100 calls/day (1 each); thumbnails.set 50; reset midnight PT | quota deferral waits for its bucket; never re-uploaded | 4 | none (no quota extension needed) |
| YouTube Analytics API | performance metrics | scope `yt-analytics.readonly` (same OAuth client) | scope not yet granted | – | **yes (3.30)** | no | daily collection once authorized | Analytics API quota | metrics recorded as unavailable, never zero | AUTHORIZATION_REQUIRED | one-time consent: Dashboard → API Providers → Authorize analytics |
| Pixabay | stock photos for topic-level assets | `PIXABAY_API_KEY` | yes | valid (200) | yes | no | yes (legacy asset stage) | 100 req / 60 s | other providers | 4 | none |
| Pexels | stock photos | `PEXELS_API_KEY` | **no** | – | yes (inactive without key) | no | yes when set | 200 req/h, 20,000/month | other providers | 2 | optional: add key |
| Openverse | CC photos for scene-level selection | none (anonymous) | n/a | **intermittent**: 2026-11-20 page 1 timeout/504, page 2 OK (20 results), probe timeout at 15 s | yes | yes | yes | anonymous rate limit; 6 h search cache (3.30) | Wikimedia / NASA / Internet Archive | INTERMITTENT | optional: registered Openverse client raises limits |
| Wikimedia Commons | CC/PD photos | none | n/a | 200 | yes | yes | yes | polite use | other providers | no key needed | none |
| Internet Archive | public-domain images | none | n/a | **connection failed** in probe; healthy during renders | yes | yes | yes | none published | other providers | 6 (intermittent) | none |
| NASA Image Library | space imagery | none | n/a | 200 | yes | yes | yes | none published | other providers | no key needed | none |
| Wikipedia REST + phys.org RSS | topic discovery, research | none | n/a | 200 / 200 | yes | yes | yes | polite use | other feeds | no key needed | none |
| Microsoft Edge neural TTS (`edge-tts`) | voice-over with emotion / rate / pitch modulation | none | n/a | working (every production 2026-11-16..18 completed TTS) | yes | yes | yes | unofficial free service | none: production fails at VOICEOVER | no key needed (single point of failure) | consider a paid TTS fallback only with approval |
| GNews | corroborated current-events discovery (>= 2 outlets) | `GNEWS_API_KEY` | yes | valid (top headlines OK, 2026-11-20) | **yes (3.30)** | no | yes (supplement only) | 100 req/day (budget 90), 6 h cache | Wikipedia / Phys.org discovery | 4 | none |
| SMTP (email) | cycle summaries and alerts | `CRAZZYDOCS_SMTP_*` | yes | not probed (would send mail) | yes | no | yes | provider dependent | dashboard notifications | 4 (assumed) | none |
| Illustrations, environments, icons, SFX, music bed, fonts | visuals and sound | none | n/a | local code / bundled OFL fonts | yes | yes | yes | – | – | 5 | none |
| AI image generation | – | – | – | – | **not implemented** (by design) | no | no | – | – | 1 | none |

## Findings

1. **No missing credential blocks production today.** Every provider production depends on is configured or
   needs no key.
2. **Openverse and Internet Archive are intermittently unavailable.** The pipeline already falls back to the other
   providers; no action is required.
3. **YouTube quota (corrected in 3.30):** the 11-17/18 "quota exhausted" deferrals came from CrazzyDocs' obsolete
   local estimate (1,600 units per upload, UTC day). Google counts uploads in their own 100-calls/day bucket and
   showed 46/10,000 general units. Four cycles a day use about 600 general units.
4. **`GNEWS_API_KEY`** was configured but never called in 3.29; Stage 3.30 implemented GNews discovery.
5. **`PEXELS_API_KEY` is the only implemented integration whose key is missing.** It is optional.

## Keys you could obtain (none are required for production)

| Variable | Provider | Why | Cost |
|---|---|---|---|
| `PEXELS_API_KEY` | Pexels | one more licensed photo source when Openverse times out | free |
| (Openverse client credentials) | Openverse | higher anonymous rate limit; not wired in code yet | free |
| YouTube quota increase | Google Cloud console | only needed if more than ~5 uploads a day are planned | free (application) |
