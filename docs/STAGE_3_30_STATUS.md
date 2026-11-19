# Stage 3.30 — Quota, Junk Cleaner, Retry Reliability, Recovery Display, API Integration: Status

Date: 2026-11-20 (IST). Every finding was traced in the running system, its database, logs or a real request.
Credential values were never printed, logged or stored.

---

## 0. YouTube API quota

### 0.1 Discrepancy root cause (9,900/10,000 vs Cloud Console 46/10,000)

The `9900/10000` figure came from **CrazzyDocs' own local counter**, not from Google:

* `PublishingDatabase.add_quota_consumption()` added a fixed **1,600 per upload** and **50 per thumbnail** to
  `youtube_quota_ledger`, keyed by the **UTC** date.
* `upload_short()` refused an upload when `used + 1650 > 10000`. 9,900 = 6 × 1,650: after the 6th upload of a
  UTC day every further upload was "deferred", whatever Google counted.
* Ledger evidence: `2026-11-16`, `11-17`, `11-18`, `11-19` = 9,900 each; earlier days 1,650.
* Causes, against the prompt's list: **obsolete method costs**, **combined buckets**, **wrong day boundary (UTC instead
  of Pacific)**, **a local estimate presented as a limit**. It counted no retries, chunks or dashboard polling
  (those were not counted at all), and no duplicate workers were involved.
* The dashboard pill read `ytStatus.quota` / `health.youtube_quota`, which no code produced, so it could never show
  a real figure; operators saw the value only in the deferral error.

**Production impact tonight:** `cycle_1_20261120_0000` rendered successfully at 00:25 IST, then its publishing
handoff failed 5/5 times on this false gate (the UTC date was still 11-19 = 9,900), ending FAILED at 00:43 IST
with the video preserved. Stage 3.30 re-arms it (§3).

### 0.2 Verified quota model (official sources, read 2026-11-20)

Sources: <https://developers.google.com/youtube/v3/determine_quota_cost>,
<https://developers.google.com/youtube/v3/revision_history>.

* 2025-12-04: `videos.insert` cost reduced from ~1,600 to ~100 units.
* 2026-06-01: `videos.insert` and `search.list` moved to **their own buckets**; all other methods remain in the
  shared bucket.
* 2026-06-03: `videos.batchGetStats` added, 1 unit in its own bucket (10,000/day).
* Daily quotas reset at **midnight Pacific Time** (PST = 13:30 IST, PDT = 12:30 IST).

| Method (used by CrazzyDocs) | Bucket | Cost |
|---|---|---|
| `videos.insert` (one call per upload; resumable chunks are one call) | videos.insert — 100 calls/day | 1 |
| `search.list` (only in a one-off Stage 3.21 script) | search.list — 100/day | 1 |
| `videos.list`, `channels.list`, `playlistItems.list`, `videos.getRating` | general — 10,000 units/day | 1 |
| `videos.update`, `thumbnails.set`, `videos.delete` | general | 50 |
| `videos.batchGetStats` (not used) | own bucket — 10,000/day | 1 |

The Cloud Console figures (General 46; Video Uploads 0/100; Search 0/100; Batch stats 0) match this model.

### 0.3 Request-level instrumentation and reconciliation

`backend/app/services/youtube_quota.py` (`QuotaLedger`), table `youtube_api_requests`: one row per request
**actually sent** (method, timestamp, Pacific day, bucket, documented cost + source, verified flag, purpose,
job id, video id, attempt, retry reason, outcome, HTTP status, error category, correlation id; `INSERT OR IGNORE` on
the correlation id makes replays idempotent). Local validation errors (no HTTP response) are not counted; a
resumable upload is one `videos.insert`. A Google `quotaExceeded` / `uploadLimitExceeded` response blocks **only
that bucket** until the next Pacific midnight (`youtube_quota_blocks`). Every call site is instrumented:
upload, upload verification, duplicate check, thumbnail, status reconciliation, schedule verification, reschedule,
metadata update, dashboard channel check, legacy worker thumbnail.

**Google-reported usage cannot be read** with the existing YouTube OAuth token (Cloud Monitoring needs a different
scope/credential). The dashboard says so and shows the local figure labelled "estimate"; nothing is presented as
Google-verified.

### 0.4 Call optimisation and measured impact

| Change | Before | After |
|---|---|---|
| Status reconciliation (every 5 min) | 1 `videos.list` **per scheduled video** (N × 288/day) | 1 per 50 videos (≤ 288/day) |
| Dashboard channel check cache | 300 s (≤ 288 `channels.list`/day) | 900 s (≤ 96/day) |
| Thumbnail already uploaded (same artifact, same video) | re-sent (50 units) | skipped |
| `videos.update` with no change | sent (50 units) | skipped after a 1-unit read of the current state |
| Upload pre-check | blocked after 6 uploads/UTC day | checks only the buckets it needs; Google's own exhaustion signal first |

Expected daily use with four cycles: 4 `videos.insert` calls (of 100) and roughly 4 × (50 thumbnail + ~4 reads)
+ ≤ 288 reconciliation + ≤ 96 dashboard ≈ **600 general units of 10,000**. **No quota extension is needed**; none
was requested.

### 0.5 Quota during exhaustion

Only the operation that needs an exhausted bucket waits. A quota deferral in the publishing handoff no longer
consumes a publishing attempt; it waits for the Pacific reset (Google-reported block) or re-checks in 15 min
(local estimate). The rendered video is never re-rendered or re-uploaded.

### 0.6 Dashboard

New **YouTube API Quota** card: per bucket (general, uploads, search, batch stats) used (estimate) / limit /
requests / status (available, estimate at limit, BLOCKED by Google with the deferred operation and reset time),
Pacific quota day, next reset in IST and PT, last request time, top consumers by method and purpose, and an
explicit "Google-reported usage: not available here" line. The header pill shows `Quota (est.): used/limit` for the
general bucket only.

---

## 0b. Junk Cleaner

### Lifecycle and deployed evidence

* "Junk Cleaner" is `media_lifecycle_manager.py` (category A–J classifier, quarantine/restore).
* **The only runtime caller** is `daily_report()` from the daily summary email — **read-only**. The last report
  (`logs/storage_audit_report.json`, 2026-11-18 08:08) scanned 6,765 files / 3.73 GB and flagged 1.97 GB as
  "quarantinable" — but **nothing was ever cleaned automatically**: `weekly_temp_quarantine()` and
  `monthly_audit()` are never scheduled; there is no quarantine manifest; the only quarantine folder
  (`media/quarantine/stage_3_21s`, 85 MB) was created by a manual Stage 3.21 script.
* Past deletion counts cannot be proven retrospectively because no run records existed; none are claimed.
* Latent risks found (never triggered, not invoked): `monthly_audit` would quarantine `CATEGORY_I_ORPHANED`, an
  alias of `CATEGORY_E_HISTORICAL_SHOWCASES`; `weekly_temp_quarantine` treats files with an unreadable age as
  eligible. Both remain unscheduled; the new cleaner does not use them.

### Policy (unchanged)

`backend/data/media_retention_policy.json`: `default_mode: dry_run`, **`automatic_deletion: false`**, deliverables
permanent, `regeneratable_intermediates` 30 days, `temporary_caches` 7 days, removal = **reversible quarantine**. No
retention period was invented or changed.

### New auditor (`backend/app/services/junk_cleaner.py`)

Uses real state (active / recoverable jobs, upload state, thumbnail verification), never age alone; a lease lock
(`logs/junk_cleaner.lock`) prevents concurrent runs; every run is recorded (`logs/junk_cleaner/runs.jsonl` +
one JSON per run: run id, trigger, mode, scanned, eligible, removed, skipped with reasons, errors, bytes, per
category, disk free before/after). Live mode only quarantines and is refused unless the policy allows automatic
deletion or an operator explicitly confirms. Scheduled: **daily dry-run 03:30 IST**; dashboard card "Junk Cleaner"
with a "Run dry-run now" button; health is HEALTHY only after a successful run in the last 26 h.

### Per-category behaviour and dry-run evidence (live media, 2026-11-20 00:5x IST)

| Category | Location | Rule | Result |
|---|---|---|---|
| Final MP4 | `productions/<job>/video/*.mp4` | permanent deliverable | retained (39 files, 251 MB) |
| Intermediate clips | `lemo_film/`, `render_work/`, `pop_info_film/*.mp4` | job terminal, final MP4 present, ≥ 30 days | retained: all younger than 30 days (670, 863 MB); 14 belong to the running job |
| Final audio | `audio/` | permanent | retained (92, 58 MB) |
| Temporary TTS segments | `narration/` | as intermediates | retained: younger than 30 days (460, 68 MB) |
| Final thumbnail | `thumbnails/*.png`, `*_youtube_1280x720.jpg` | permanent | retained (38, 65 MB) |
| Thumbnail variants | `thumbnails/variants/` | final thumbnail uploaded **and remotely verified**, ≥ 30 days | retained (114, 186 MB) |
| Downloaded photos/clips | `assets/` | source binaries with provenance; no per-asset cache policy | retained (2,162, 440 MB) |
| Scene graphics | `lemo_film/*.png/jpg` | as intermediates | retained: younger than 30 days (291, 65 MB) |
| Browser artifacts | `*.html/js/fonts` in film folders | as intermediates | retained (42, 1 MB) |
| FFmpeg temp | `media/temp_render/` | temporary caches, 7 days | none present |
| Manifests / reports / provenance | `manifests/`, `metadata/`, `reports/`, `*.json` … | protected | retained (983, 17 MB) |
| Cache | `media/asset_cache/` | 7 days | retained: younger than 7 days (7, 1 MB) |
| Test renders | `media/test_renders/` | not covered by policy | retained (928, 1.76 GB) |
| Source / user media | `media/source/` | protected | retained |
| Quarantine | `media/quarantine/` | recovery area | retained (93, 85 MB) |

Totals: **5,933 files, 3.88 GB scanned; 0 eligible; 0 removed; disk unchanged (157.26 GB free).** The first
intermediates become eligible under the existing policy around 2026-12-10.

**Not performed:** no destructive or quarantine cleanup — the policy forbids automatic deletion, and nothing is
eligible yet. Decisions for you (§18): whether to allow automatic quarantine of eligible intermediates, and whether
test renders (1.76 GB) should get a policy.

---

## 1. Stage 3.29 dependency status

Verified, not assumed: `705b404` (code), `00216b4` / `874f5cb` (report) on `origin/main`; dashboard and daemon both
running source revision `01e566d5ffe839fc` (Deployment: OK) before this stage; 30–58 s duration, thumbnail
pipeline, parity check, provider inventory and dashboard startup present. Stage 3.30 builds on them and
re-implements none of them.

## 2. Retry classification root cause

`retry_policy.record_failure()`: when a rendered video existed it **overwrote the real cause** with
`POST_RENDER_FAILURE` and set `retry_allowed = False`; `rearm_if_recoverable()` refused POST_RENDER_FAILURE; the
operator path allowed only `TRANSIENT, RENDER_FAILURE, DETERMINISTIC, PERMANENT`. So a recoverable QC failure after
rendering (e.g. `cycle_3_20261116_1200`, 49.20 s under the old rule) became permanently ineligible at the moment it
was recorded.

Fix: POST_RENDER_FAILURE stays the phase, the real cause decides — TRANSIENT backs off and retries,
DETERMINISTIC (QC rules, `"qc failure"` now a deterministic marker) waits for a code/config change or operator
acknowledgement, PERMANENT stays blocked. The unlimited-until-deadline policy, backoff and cooldowns are unchanged;
no attempt cap was added; deadlines are never extended.

**No re-render, no re-upload:** a post-render retry carries `reuse_rendered_video`; the orchestrator raises
`"Preserved video cannot be reused under the current rules; not re-rendered"` instead of rendering again; retries
are refused for jobs with a queue entry or YouTube id.

Also fixed (found tonight): a **stale graceful-stop request** (set 2026-11-18 11:58 IST, never consumed because no
job ran) halted `cycle_1_20261120_0000` at 00:13:47. A stop request now applies only to the production running
when it was requested; an older request is cleared and recorded. The job had already recovered itself at 00:14:38.

## 3. Retry re-evaluation

Runs in the scheduler's recovery loop for FAILED jobs, only within the **original** recovery deadline (expired jobs
are not even probed or modified). Post-render jobs are re-armed only if the preserved video passes the *current*
rules (`post_render_reusable`, ffprobe) and, for deterministic causes, the code revision changed; otherwise the
reason they stay blocked is recorded once per revision in the attempt history. Quota-only publishing failures
(every recorded handoff failure was a quota deferral) are re-armed as HANDOFF_PENDING with the attempt count
corrected — `cycle_1_20261120_0000` qualifies (5/5 quota deferrals, no YouTube id; queue row 315 reused by video
hash). Production and publishing retries stay separate.

## 4. Recovery-window display root cause

`dashboard.html` declared **two functions named `fmtDuration`**: one taking minutes (schedule tables), one taking
seconds (Production Monitor). JavaScript function declarations hoist; the later (seconds) one replaced the first,
so `fmtDuration(240)` rendered 240 **minutes** as "4 min". Backend storage is consistent: API
`recovery_duration_minutes = 240`, scheduler `recovery_window_hours = 4.0`, persisted deadlines correct (12:00 →
16:00 IST). No schedule or deadline was changed.

## 5. Four-hour display verification

Now `fmtMinutes(240)` = "4 hours", `fmtMinutes(90)` = "1 hour 30 minutes", `fmtSeconds(9900)` = "2 hours 45
minutes", cooldowns "3 min 20 s" — executed with Node.js against the real dashboard source in the tests, plus a
guard test that fails if any dashboard function is declared twice.

## 6. Historical Cycle 3

`cycle_3_20261116_1200`: FAILED (stage THUMBNAIL), error preserved verbatim
(`[POST_RENDER_FAILURE] ValueError: QC Failure: Duration 49.20s is outside Shorts 50-58s window.`), deadline
2026-11-16 16:00 IST passed, preserved MP4 present (4.99 MB), attempt history intact, no queue entry, no upload.
It is **not** recovered, re-rendered, uploaded or modified (test pins this with its real data). The 30–58 s rule
applies to future jobs only.

## 7. YouTube Analytics OAuth changes

Upload scopes are unchanged (`youtube.upload`, `youtube.readonly`) because google-auth sends the requested scopes on
every refresh: adding an ungranted scope there would break uploads. The stored token currently grants only those
two scopes. Analytics uses the token only when `yt-analytics.readonly` is granted; the reauthorization is an
**incremental consent on the same OAuth client and channel** (existing scopes + analytics), started by you from the
dashboard (API Providers card → "Authorize analytics"). No new project, client, secret or channel.

## 8. Analytics API test results

Client `backend/app/services/youtube_analytics.py` (Top-videos report, `dimensions=video`, `sort=-views`,
≤ 50 ids per query; core metrics views, engagedViews, estimatedMinutesWatched, averageViewDuration,
averageViewPercentage; engagement metrics likes, comments, shares, subscribersGained requested separately because
not every top-videos variant supports them). Storage `video_performance` (one snapshot per video per day; daily for
28 days, then weekly). Statuses available / available_zero / not_yet_populated / unavailable / request_failed —
missing never becomes 0. Unit tests pass. **A real authenticated request has not been made**: it needs your
one-time consent (§17).

## 9. Pixabay

`PIXABAY_API_KEY` (exact name; `PIXABAY_API` is not used anywhere). Implemented before 3.30 for photos; now with a
24 h response cache (required by Pixabay's terms; the key is never cached or logged), a local guard below
100 requests/60 s, 60 s cooldown after HTTP 429, error categories (invalid_key, rate_limited, timeout, connection,
empty) and video search. Selection still goes through license verification and scene relevance scoring (poor
matches rejected, never random). Live: search OK (5 results, approved Pixabay License, provenance kept), image
download 110 KB JPEG 1280×720 verified; health probe OK (590 ms). An immediate re-download of the same CDN image
returned 429 — the pipeline downloads each asset once.

## 10. GNews

`GNEWS_API_KEY` (exact name). New `backend/app/content/gnews_client.py`: top headlines / search, 6 h cache,
persisted daily budget 90 of 100, stale (> 3 days) and duplicate-URL removal, same-event clustering, and a
candidate only for events reported by **≥ 2 distinct outlets**, labelled `multi_source_corroborated_headline` with
every source URL and date and lower reliability (7.5). It supplements Wikipedia / Phys.org discovery; any GNews
problem yields no candidates and never blocks a cycle. Live: top headlines OK (10 articles, newest 2026-11-19, e.g.
Nature); health probe OK.

## 11. Openverse

Integration verified, not rewritten (license filter `cc0,pdm,by`, creator / license / attribution kept). Added
pagination and a 6 h response cache. Live tonight from this network: page 1 of `science` **timed out / HTTP 504
twice**, page 2 returned 20 results, health probe timed out at 15 s → **INTERMITTENT**, not unavailable (consistent
with your successful Postman test). Fallback to Wikimedia / NASA / Internet Archive works. Public-Domain-Mark
(`pdm`) results are rejected by the license verifier as unrecognised (safe; accepting PDM is a policy choice, §18).

## 12. Wikimedia Commons

Verified: identifiable User-Agent (`CrazzyDocsStudio/1.0 (...)`), scene-specific search, source URL, creator,
license and attribution kept (sample: ESO artist's impression, CC BY 4.0, APPROVED), download 114 KB JPEG
1280×960 verified, 6 h search cache added. No API key exists or is required.

## 13. Provider health dashboard

API Providers card: status (incl. INTERMITTENT, AUTHORIZATION_REQUIRED, UNVERIFIED — a populated variable alone is
never "healthy"), credential set / missing / not required / authorization required, last success, last failure
with category, rate-limited flag, usable by production, fallback; "Run health check" runs cheap read-only probes
(Gemini, Pixabay, GNews, Openverse, Wikimedia, NASA, Internet Archive, edge-tts) at most every 10 min per provider;
refreshing never calls a provider; the YouTube Data API is never probed (quota). Analytics state and metrics appear
in the same card.

## 14. Test results

Full suite (`pytest backend/tests`): **865 passed, 4 failed, 173 skipped** (Stage 3.29: 792 passed, 5 failed).
The 4 failures are the long-standing baseline (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`,
2 × `test_youtube_branding`); no new failure. The timing-sensitive `test_slow_youtube_status_...` passed in this run.

New test files (all pass): `test_youtube_quota_330.py` (11: verified costs/buckets, Pacific reset + IST, only sent
requests counted, idempotent replay, no false 9,900 block, exhaustion blocks one bucket, batching 60 → 2 calls,
unchanged video not updated, thumbnail not resent), `test_junk_cleaner_330.py` (9: dry-run touches nothing, every
safety rule, verified-thumbnail gate, live refused without policy/confirmation, quarantine idempotent, concurrent run
skipped, persisted runs and health, DB unavailable), `test_retry_classification_330.py` (19: recoverable / permanent /
deterministic / transient post-render, config and code corrections, cooldown, deadline, restart persistence,
re-evaluation, expired / uploaded / queued jobs untouched, no re-render guard, historical Cycle 3, quota-only handoff
re-arm, quota deferral uses no attempt), `test_recovery_display_330.py` (4, executes the real dashboard JS in Node),
`test_stale_stop_request_330.py` (3), `test_youtube_analytics_330.py` (7), `test_pixabay_gnews_330.py` (17),
`test_provider_audit_329.py` (+3 probe tests).

Existing tests changed deliberately (behaviour required by this stage, assertions kept strict):
`test_retry_policy_unlimited::test_post_render_failure_is_never_rerun` (a transient post-render cause now retries
with the video reused; a permanent one stays terminal), `test_stage_3_17_deployment::test_thumbnail_upload_handling`
(verified quota accounting instead of the obsolete 1,650), `test_provider_audit_329::test_status_classes` (GNews and
Analytics now implemented; a configured key is UNVERIFIED until a request succeeds).

## 15. Commit

COMMIT_PLACEHOLDER

## 16. Deployment

DEPLOY_PLACEHOLDER

## 17. Remaining credentials / authorizations

| What | Where | Needed for |
|---|---|---|
| One-time consent for `yt-analytics.readonly` | Dashboard → API Providers → "Authorize analytics" | YouTube Analytics collection |
| `PEXELS_API_KEY` (optional) | `.env.local` | an extra photo source |

Already configured and verified live: `GEMINI_API_KEY`, `PIXABAY_API_KEY`, `GNEWS_API_KEY`, YouTube OAuth
(upload + readonly), SMTP.

## 18. Outstanding blockers and decisions

BLOCKERS_PLACEHOLDER
