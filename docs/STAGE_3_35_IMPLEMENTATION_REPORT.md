# Stage 3.35: implementation report

**YouTube Analytics, autonomous learning and visual asset intelligence.** Companion reports:
- `STAGE_3_35_OAUTH_RELIABILITY_REPORT.md`
- `STAGE_3_35_ANALYTICS_REPORT.md`
- `STAGE_3_35_PROVIDER_AUDIT.md`
- `STAGE_3_35_VISUAL_ASSET_AUDIT.md`
- `STAGE_3_35_KNOWN_ISSUES.md`

## 1. Summary

| Part | Outcome |
|---|---|
| A. Analytics OAuth | **Root cause found and fixed.** The upload client's hourly refresh rewrote the shared token's scope list and erased `yt-analytics.readonly`. The grant itself was always valid, so no re-consent is needed. |
| A. Ingestion | Daily lifetime-to-date snapshots plus a fixed first-7-days window (core, engagement, retention, traffic sources). Idempotent, budgeted, with a sync state. A missing value is never stored as 0. |
| A. Learning | `creative_intel/learning.py`: per-video learning records, robust statistics and proposals that are never applied. Claims need at least 30 first-7-days videos (currently 1). |
| B. Providers | Stage 3.35 provider states, judged by **rendered usage**, not by key presence or request success. |
| C. Footage | Scene-level real-world footage (Pixabay / Wikimedia / NASA, plus Pexels with a key), per-video asset manifests and composition tracking, all in the one renderer. |
| D. Voice | Edge TTS reliability measured at 99.86 % after retries. Longer backoff. An optional SAPI fallback, off by default. |
| E. Dashboard | Ops cards: *YouTube Analytics and learning*, *Content providers*, *Visual composition*. |
| G. Tests | 56 new tests; the full suite has no new failures. |

## 2. Changes by file

| File | Change |
|---|---|
| `services/youtube_publishing_engine.py` | **Token handling:**<br>- the token scope list grows on refresh and is replaced only by a completed consent (`merge_token_scopes`);<br>- a cross-process `_TokenFileLock`;<br>- `record_granted_scope`;<br>- the refresh token is preserved.<br>**Logging:** the OAuth library logger is at WARNING, so authorization codes are no longer logged. |
| `services/youtube_analytics.py` | **Credentials:**<br>- analytics-only in-memory credentials that never write the token file;<br>- bounded retries;<br>- auth states `CONNECTED` / `REFRESHING` / `EXPIRED` / `REVOKED` / `AUTHENTICATION_REQUIRED` / `ERROR`, persisted with their history;<br>- the grant is verified, not trusted.<br>**Collection:**<br>- a first-7-days collector with retention and traffic summaries;<br>- a sync state;<br>- a call budget of 120 per run;<br>- an additive `window_kind` column. |
| `creative_intel/learning.py` (new) | `learning_records` and `learning_runs`; medians, winsorised means and outlier flags; Spearman correlations with permutation p-values; confidence labels; proposals with evidence, sample size, expected benefit and limitations; `applied: false`, `requires_authorization: true`; an input hash for reproducibility. |
| `scheduler/scheduler.py` | The daily 07:15 IST analytics job runs a learning refresh after collection. |
| `media/footage.py` (new) | Footage providers, a licence allow-list, the relevance gate (subject coverage plus qualifier conflict), quality scoring, a cached download (80 MB cap), validation (ffprobe, resolution, decode, black frame) and 9:16 frame extraction. |
| `rendering/pop_info/scene_footage.py` (new) | Which scenes take footage, filmable subject and query extraction, parallel search, dedupe, fallback and a per-scene report. |
| `rendering/pop_info/asset_manifest.py` (new) | Scene classes, composition, per-video manifest and recent-video audit. |
| `scene_planner.py`, `film_builder.py`, `engine.js` | Footage scenes are full-bleed and still. Frames are copied into the film; the engine loads and draws the current frame, plus the previous scene's during transitions. |
| `pipeline.py`, `render_parity.py`, orchestrator | `select_footage` is an approved render option (`CRAZZYDOCS_FOOTAGE`, default on). `footage_selection.json` and the manifest are written for every render. |
| `services/provider_audit.py` | Stage 3.35 states (`CONFIGURED`, `HEALTHY`, `DEGRADED`, `RATE_LIMITED`, `AUTHENTICATION_REQUIRED`, `KEY_MISSING`, `DISABLED`, `NOT_USED`, `UNKNOWN`) and `rendered_usage_last_20`. |
| `services/expressive_tts_engine.py`, `services/tts_fallback.py` (new) | Connection-error backoff of 2, 5 and 10 s. SAPI fallback only when `CRAZZYDOCS_TTS_FALLBACK=sapi`; segments that use it are flagged `voice_fallback`. |
| `main.py`, `templates/dashboard.html` | The intelligence API adds `analytics`, `providers` and `visual`; three Ops cards. |

## 3. Voiceover reliability (Part D)

| Measure | Value |
|---|---|
| Edge TTS segment syntheses on record | 691 |
| Retries needed | 5 |
| Final failures | 1 (network outage, 2026-11-17) |
| First-try success | 99.3 % |
| Success after retries | **99.86 %** |

**Single point of failure: yes,** for neural voice. The connection-error backoff is now 2, 5 and 10 s, so short outages are absorbed.

**Free fallback options evaluated:**
- **Windows SAPI** (built in, offline): 0.66 s to synthesize 6.5 s of audio. It works, but the voice is robotic and below the channel's standard. **Implemented, off by default.**
- **Local neural engines** (for example Piper): would need a new runtime and a model download. Not added in this stage.

A failed voiceover still fails the cycle visibly instead of publishing a degraded voice, unless the operator opts in.

## 4. Safety

- **Scopes and consent:** no scope change and no consent flow; the minimum `yt-analytics.readonly` is unchanged. No credential or token was logged or committed.
- **Production:** the 24-cycle schedule was not changed. No extra production cycles and no overlapping workers; previews ran only while no job was active.
- **Publishing:** nothing published. Approval and publication gates are untouched.
- **Flags and Autopilot:** topic flags unchanged (shadow and off); Autopilot stays in OBSERVE; learning proposals are never applied automatically.
- **Paid services:** no mandatory paid API.
- **Git:** no destructive Git operation.
- **User files:** user-owned files, including the log that holds old OAuth codes, were left untouched.

## 5. Tests

`test_analytics_learning_footage_335.py` has 56 tests:
- **OAuth:** scope merge, lock, rotation, revocation, expiry, concurrency and restart.
- **Analytics:** availability statuses, first-7-days window, idempotency, budget, retention and traffic.
- **Learning:** thresholds, outliers, Spearman, confidence, proposals and reproducibility.
- **Footage:** providers, licences, relevance (including the wind-turbine and abstract-query regressions), validation, extraction, end to end with dedupe and cache, fallback.
- **Manifest and composition**, provider states, TTS backoff and fallback, API keys, render parity.

Full suite: see `STAGE_3_32E_TEST_RESULTS.md` §13.
