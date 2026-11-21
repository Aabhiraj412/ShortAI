# CrazzyDocs Autonomous Studio: Project Handover

**As of:** 2026-11-21 IST. Stage 3.35 (YouTube Analytics, autonomous learning and visual asset intelligence) is implemented and deployed in the commit that contains this file:
- the root cause of the repeated Analytics "disconnections" is fixed (an upload-client refresh erased the analytics scope from the shared token);
- analytics ingestion with a first-7-days window;
- a learning engine with proposals only;
- the provider audit by rendered usage;
- scene-level real-world footage with per-video asset manifests;
- a longer Edge TTS backoff.

See `docs/STAGE_3_35_IMPLEMENTATION_REPORT.md` and the other `STAGE_3_35_*` reports. Autopilot stays in OBSERVE; topic intelligence stays in shadow; public publishing is unchanged.

**Repository:** `D:\Agents\CrazzyDocs` (production checkout, branch `main`). GitHub: `Aabhiraj412/ShortAI`, branch `main`.

**Development worktree:** `.claude\worktrees\crazzy-docs-handover-b1a088`, branch `claude/crazzy-docs-handover-b1a088`, in step with `main`.

**Latest commit on `main` / `origin/main`:** the Stage 3.32H/I/J commit (on top of `ee6c8ce`, 3.32G). Run `git log -1` for the hash.

## 1. Architecture

### Production pipeline (Windows 11, local rendering)

| Process | Role |
|---|---|
| `run_scheduler_daemon.py` | Runs the active schedule (CUSTOM, 24 hourly cycles), dispatches one production job at a time (concurrency 1), applies the retry policy within each cycle's recovery window, and hands finished videos to the YouTube publishing queue. |
| `run_dashboard.py` | FastAPI dashboard and control API on `127.0.0.1:8000`, served from `backend/app/templates/dashboard.html`. |
| `startup_manager.py --monitor` | Relaunches the daemon and dashboard (at most 3 dashboard restarts per hour). |

All three run under `.venv\Scripts\pythonw.exe` from Task Scheduler (`CrazzyDocs-*` tasks, triggered at logon).

**Database:** `backend/studio.db` (SQLite, DELETE journal mode). Main tables:
- `persistent_jobs`, `production_lifecycle_events`;
- schedule and profiles: `studio_settings`, `production_schedule_profiles`;
- publishing: `publishing_ledger*`, `youtube_*`;
- topics: `topic_candidate_*`, `creative_identity_registry`.

### Stage 3.32 autonomy stack

Everything lives under `backend/app/autonomy/` and is **OFF by default**.

| Stage | Module | Role |
|---|---|---|
| 3.32A | `services/operational_intelligence.py` | Read-only analytics (production health, failures, renderer, topics, API, recovery). Feeds everything below. |
| 3.32B | `autonomy/control_plane.py`, `registry.py`, `handlers.py` | The **only** path to any autonomous action: closed action registry (8 permitted, 26 prohibited); levels OFF / OBSERVE / RECOMMEND / APPROVED_EXECUTION (global × category); signed, hash-bound approvals; 3 kill switches; hash-chained append-only audit. |
| 3.32C | `autonomy/recommendations/` | Rule-based recommendations (28 rules), advice only. |
| 3.34 | `core/process_utils.py`, `core/fs_utils.py`, `media/downloader.py`, `services/deployment_diagnostics.py`, `rendering/ffmpeg_geometry.py` | **Controlled production validation and reliability.** Exited processes are no longer counted as alive (worker stall recovery, daemon status); oversize/retry/temp-file defects behind WinError 32 in downloads; bounded retries for transient file locks; live worker stage instead of the coarse checkpoint; shadow topic evidence persisted; final videos delivered as limited-range BT.709. One live scheduled cycle observed (heartbeat at most 27 s, peak about 2.9 GB). |
| 3.35 | `services/youtube_analytics.py`, `services/youtube_publishing_engine.py`, `creative_intel/learning.py`, `media/footage.py`, `rendering/pop_info/scene_footage.py`, `asset_manifest.py`, `services/tts_fallback.py` | **Analytics, learning and footage.**<br>- **OAuth:** the shared token's scope list only grows on refresh; analytics refreshes in memory with only `yt-analytics.readonly`; cross-process token lock; persisted auth states.<br>- **Ingestion:** lifetime-to-date and first-7-days metrics (retention, traffic sources).<br>- **Learning:** `learning_records` / `learning_runs`; claims need at least 30 first-7-days videos; proposals are never applied.<br>- **Footage:** Pixabay / Wikimedia / NASA (+ Pexels with a key) scene footage, gated on licence and relevance, with a qualifier-conflict check; per-video `manifests/asset_manifest_<job>.json`; `CRAZZYDOCS_FOOTAGE=off` disables it.<br>- **Providers:** states by rendered usage.<br>- **TTS:** SAPI voice fallback, off by default. |
| 3.32F (completion) | `community/publishing.py`, `youtube_posts.py`, `service.py`, dashboard | **Community publishing workflow.**<br>- **Authorization and queue:** publication authorization separate from approval; a local queue for timed publication (re-checks every gate; cancels when content changes).<br>- **Failures:** bounded retries (5 / 30 min, 3 attempts); `REQUIRES_REAUTHORIZATION` on refused credentials (fixes a stuck-row defect); restart recovery; uncertain outcomes never retried.<br>- **State:** one workflow state per draft (DRAFT, VALIDATED, APPROVED, QUEUED, PUBLISHING, PUBLISHED, and others).<br>- **Notifications:** approved-awaiting, manual handoff required, published, failed, authentication.<br>- **Dashboard:** schedule-editor load fix and a publishing queue.<br>- **Status:** API publishing remains blocked (posts API pilot; no insert scope). Details: `docs/STAGE_3_32F_COMMUNITY_PUBLISHING_*.md`. |
| 3.35b | `content/curiosity_topic_engine.py` | **Topic discovery rate limiting.** Wikimedia answered HTTP 429 to unpaced discovery bursts, and throttled candidates were recorded as INSUFFICIENT_EVIDENCE. That drained the pool, and 7 cycles on 2026-11-21 failed the integrity gate. Fix: `WikimediaGate` paces requests at 1 per second, honours Retry-After, makes at most 2 retries and waits at most 180 s per run. Throttled candidates are `PROVIDER_FAILURE` (not judged), and request statistics are recorded per run. Details: `docs/STAGE_3_35B_TOPIC_DISCOVERY_RATE_LIMIT.md`. |
| 3.31K | `content/demand_signals.py`, `topic_scoring_v2.py`, `expanded_categories.py` | **Demand-led topic intelligence.** Wikimedia most-read + pageview velocity and Google Trends daily searches (provenance in `topic_demand_signals`); eight-signal scoring v2 with historical performance as one bounded signal (5 %, neutral until analytics exist); Cinema & Entertainment, Media & Internet Culture, Sports & Major Events (politics stays out of scope). Flags `demand_signals`, `scoring_v2` (off/shadow/on), `expanded_categories` (off/on). Details: `docs/STAGE_3_31K_DEMAND_LED_TOPIC_INTELLIGENCE.md`. |
| 3.32 (roadmap) | `creative_intel/` | **Video creative intelligence.** 14 craft dimensions from the script, rendered Pop Info plan, subtitles and the rendered MP4; one `creative_records` row per job linked to its YouTube video ID (for 3.35); controlled `hook_type` experiments. Hourly `creative_evaluation_tick` (skips while a job is active). Details: `docs/STAGE_3_32_CREATIVE_INTELLIGENCE.md`. |
| 3.33 | `scripts/benchmark_renderers_333.py`, `docs/benchmarks/` | **Rendering architecture decision:** keep Lemo-Opuscar + Pop Info; no migration. Details: `docs/STAGE_3_33_RENDERING_ARCHITECTURE_DECISION.md`. |
| 3.32H | `rendering/ffmpeg_geometry.py`, `core/health_monitoring.py`, `scripts/startup_manager.py` | **Production reliability.** FFmpeg padding root cause fixed (odd 855-row viewport vs yuv420p chroma rounding in `pad`): even geometry everywhere, geometry errors now DETERMINISTIC, bounded stderr, partial-output cleanup. Monitor: liveness `/api/health/live` and readiness `/api/health/ready` probes, HEALTHY/DEGRADED/UNAVAILABLE/UNHEALTHY with thresholds and hysteresis, restarts only when unhealthy AND no dashboard process, persisted probe ledger. |
| 3.32I | `autonomy/activation.py` | **Phased activation** OFF → OBSERVE → RECOMMEND → RESTRICTED_EXECUTION → EXPANSION, one step at a time, readiness re-checked, authorization phrase for RECOMMEND+, never grants standing approvals or creates `autonomy_startup.json`. Observation cycle via the existing scheduler (`autopilot_tick`). |
| 3.32J | `autonomy/reports.py` | **Daily and weekly operational reports** from persisted data: observed facts, calculated metrics (with formulas), rule-based interpretations (no AI), missing data listed. Details for H/I/J: `docs/STAGE_3_32HIJ_RELIABILITY_ACTIVATION_REPORTING.md`. |
| 3.32G | `autonomy/autopilot.py`, `control_plane.py`, `registry.py` | **Controlled Autopilot.** Centralized action policy with three tiers: autonomous-eligible (5), approval required (3), permanently gated (28, now including explicit external communication and financial actions). Deterministic recommendation → approval-request bridge. Execution leases with owner process and heartbeat: a timed-out thread keeps its lease until it ends, and a lease is only reclaimed when its owner process has ended. Circuit breaker, retry backoff. Read-only Autopilot tab and API. Details: `docs/STAGE_3_32G_AUTOPILOT.md`. |
| 3.32D | `autonomy/self_healing/` | Detect, classify (6 classes), diagnose and plan, then propose allow-listed actions (diagnostics, provider availability check, staged re-download). Every action is approved, executed through the control plane and verified twice. Never retries or re-renders jobs. |

**Kill switches:**
- dashboard switch (`autonomy_config`);
- sentinel file `backend/data/AUTONOMY_DISABLED`;
- startup switch, active unless `backend/data/autonomy_startup.json` contains `{"autonomy_execution":"enabled"}`. It is read once at process start and fails closed.

**Feature flags (Stage 3.31):** set in `backend/data/creative_policy_config.json` under `topic_intelligence`:
- `candidate_ledger`: on;
- `category_classifier_v2`: shadow.

## 2. Completed stages and commits (2026-11-20)

| Commit | Stage |
|---|---|
| `211c04d`, `22c23fe` | 3.30: quota model, retry reliability; quota re-arm limited to the recovery window |
| `589c2b5`, `7f50a55`, `e14efc5` | 3.31A–D: topic intelligence behind flags, plus an attempts-count fix |
| `4ae426b` | 3.32A: Operational Intelligence and the Operations tab |
| `ba1cc0c` | 3.32B: Autonomy Control Plane and the Autonomy Control tab |
| `c4e1cf5` | Named production schedule profiles |
| `5b2a4ad` | 3.32C: Recommendation Engine and the Recommendations tab |
| `d2e0928` | 3.32D: Self-Healing Operations and the Self-Healing tab |
| `710b8c5` | 3.32D final security audit fixes, plus 99 security tests |
| `687c156` | 3.32D re-verification: F9 (exact media Content-Type) and F10 (embedded private IPv4 in IPv6), plus 11 tests |
| `89ea49b` | **3.32E: Community post draft automation**: `backend/app/community/`, Community Posts tab, `community_draft_tick` scheduler job, 75 tests, 6 docs. **Deployed** (dashboard 16:42, daemon 17:34 IST); automation OFF |
| `97b7990` (dev branch only, not pushed) | 3.32E: `image_not_blank` validator wording fix plus its regression test |
| (uncommitted, dev worktree) | 3.32E: **timezone-aware Community Publishing Schedule**: `community/schedule.py` (IANA presets and list, DST rules, scheduling decision), `max_auto_attempts`, no retroactive run on enabling, 2 new read-only routes (18 total), dashboard panel, 52 new tests |

**Test baseline at `710b8c5`:** 1208 passed, 5 failed, 173 skipped. The 5 failures are pre-existing (section 4).

**Docs for each stage:** `docs/STAGE_3_32*.md`. The latest audit is `docs/STAGE_3_32D_FINAL_SECURITY_AUDIT.md`.

## 3. Current production state

| Item | State |
|---|---|
| Restart at 15:23:59 IST | The computer was restarted from the Start menu under the user's account (System event 1074). At logon the scheduled tasks relaunched everything from the files on disk (`687c156`). |
| Daemon | PID 13216, started 17:34:26 IST by a controlled restart at a safe point; code `89ea49b` (loaded revision `5171904dce2ca340`). 24 cycles plus the `Community Draft Check` job, which returns `blocked` while automation is OFF. |
| Dashboard | PID 3992, started 16:42:50 IST through `Start-ScheduledTask CrazzyDocs-Health-Dashboard`, because the monitor had reached its 3-relaunches-per-hour cap; code `89ea49b`. |
| Monitor | PID 5260 (`startup_manager.py --monitor`), running again since the 15:24 restart. |
| Schedule | Active revision `ccf0bd04f159`, CUSTOM 24 cycles, named profile "24/7", daemon in sync. |
| Jobs | 45 completed, 12 failed, 40 recovery-window-expired, 1 aborted. |
| Autonomy | All levels OFF; startup kill switch active (no `autonomy_startup.json`); 0 requests, 0 executions. |
| Self-healing | BLOCKED (startup kill switch), level OFF; its six `selfheal_*` tables exist and are empty. |
| Community drafts (3.32E) | **Automation ON**, switched on by the user in the dashboard at 19:43 IST on 2026-11-20 (`operator:Abhiraj`): daily at **20:00 Asia/Kolkata**. Today's 20:00 run is skipped because today already has a draft; the first automatic draft is due 2026-11-21 20:00 IST. `cdraft_00eeba9275a0990d` was **approved** by the user at 19:43 IST and is not published. |
| Uncommitted user changes (keep them) | `backend/app/content/topic_history.json`, `backend/data/creative_policy_config.json`; untracked `backups/`, `backend/backups/`, `media/` |
| Latest backups | `D:\Agents\CrazzyDocs_backups\deploy_89ea49b_scheduler_20261120_173044` (before the daemon restart) and `deploy_89ea49b_dashboard_20261120_163917` |

## 4. Outstanding bugs and risks

| # | Item | Notes |
|---|---|---|
| 1 | **FFmpeg padding defect** | **Fixed in 3.32H** (root cause: odd 860×855 viewport; `pad` rounds to 854 under yuv420p). Errors of this kind are now DETERMINISTIC. Watch the Rendering panel for any new FFmpeg failure after deployment. |
| 2 | **WinError 32** | File locks on `assets/acquired/temp_*.tmp` during acquisition. Resource contention; root cause not identified. |
| 3 | **QC duration failures** | Duration 49.2 s, outside the 50–58 s window; seen after rendering. |
| 4 | **High recovery-window expiry rate** | 40 of 98 jobs; YouTube daily quota exhaustion also delays publishing. |
| 5 | **Monitor false unhealthy** | **Fixed in 3.32H** (2 s timeout on a ~2 s aggregate endpoint). The monitor now uses liveness/readiness probes and persists every decision. |
| 6 | **5 known test failures** | `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two in `test_youtube_branding`, and the timing-sensitive `test_slow_youtube_status_does_not_block_other_dashboard_requests`. |
| 7 | **DNS rebinding** | Could expose the CSRF token through a read (pre-existing; writes still need an allow-listed Origin). A Host-header allow-list is the suggested fix. |
| 8 | **Stage 3.31 flags** | Their final state still awaits the user's approval. |

## 5. Pending roadmap

Detail: `docs/STAGE_3_32_IMPLEMENTATION_PLAN.md`.

1. **Deploy `710b8c5` to the dashboard**: the exact next task (section 7).
2. **Fix the FFmpeg padding defect** (bug 1). This is the largest source of lost cycles.
3. **3.32E**: Community posts in DRAFT mode. `89ea49b` is deployed with automation OFF. The timezone-aware schedule (US presets, DST) is implemented and tested, awaiting commit authorization; deploying it needs a dashboard **and** a daemon restart. Then the operator picks a timezone and time and enables automation. See `docs/STAGE_3_32E_IMPLEMENTATION.md` §7 and `docs/STAGE_3_32E_SCHEDULING.md`.
3a. **3.32F**: YouTube Community publishing. **Committed (`16b8628`) and deployed.** The read-only capability audit found `PILOT_ACCESS_REQUIRED` for project `crazzydocs-youtube` (`posts.list`: 404 Method not found); in addition, the token lacks a `posts.insert` scope. API publishing stays OFF and fail-closed; the manual handoff works. See `docs/STAGE_3_32F_PUBLISHING.md`.
4. **3.32G**: Controlled Autopilot. **Deployed.**
5. **3.32H/I/J**: reliability fixes, phased activation, operational reports. **Implemented and deployed.** Activation beyond OBSERVE needs explicit authorization (see section 7).
6. **3.35**: Analytics OAuth fix, analytics ingestion, learning (proposals only), provider audit and scene footage. **Implemented and deployed.** Next: **3.36** (not started). Learning claims unlock at 30 first-7-days videos.
7. **3.32F activation**: **BLOCKED** on Google approving the project for the posts API pilot (or general availability, planned for 2027) and on a `youtube.force-ssl` re-consent; see `docs/STAGE_3_32F_PUBLISHING.md` §9.

## 6. Critical safety constraints

**Git and code**
- Push tested milestones directly to GitHub `main`: no PRs, fast-forward only, never force-push, never `git reset` or `git clean`.
- Commit only when the user asks.
- Never commit `.env.local`, databases, media, tokens or credentials. Scan every diff for secrets.

**Production**
- Never start, stop, restart or reschedule the daemon, scheduler or workers without explicit approval. Never create cycles, launch overlapping workers, bypass recovery deadlines, change retry policies, or bypass duplicate or quality checks.
- Dashboard-only restarts:
  - check that the PID's command line contains `run_dashboard.py`;
  - stop only that PID and wait at least 12 s;
  - then run `Start-ScheduledTask CrazzyDocs-Health-Dashboard`;
  - do this between :10 and :50 past the hour, with no active job.

**Autonomy**
- Never activate autonomy, raise levels, grant standing approvals, create `autonomy_startup.json` or remove a kill switch without explicit approval.

**Data and credentials**
- Do not modify `topic_history.json`, `creative_policy_config.json`, schedules, OAuth credentials or scopes, or `.env.local`.
- Do not publish videos or Community posts.
- Do not create new GCP projects, OAuth clients or channel connections, and do not request Analytics write scopes.
- Never write to the live database from tests. `conftest.py` redirects tests to a throwaway database.
- Read production read-only (`file:...?mode=ro`). Back up to a **new** timestamped folder before any deployment.

**Testing**
- Run tests with Windows-style `PYTHONPATH` pointing at the intended checkout: `W='D:\...\worktree'; PYTHONPATH="$W;$W\backend;$W\backend\tests"`.
- Run the full suite (about 13–19 minutes) in the background.

## 7. Exact next task

**Stage 3.35 is complete. Next milestone: Stage 3.36** (not started). Readiness:
- Analytics ingestion runs daily at 07:15 IST.
- Learning claims unlock when 30 videos have first-7-days metrics (expected around 2026-11-28).
- Footage composition is measured per video (Ops → *Visual composition*).
- Open items are in `docs/STAGE_3_35_KNOWN_ISSUES.md`.

**Decisions waiting for the operator (each needs explicit approval):**

1. **Topic intelligence.** After about 48 h of `demand_signals: shadow` / `scoring_v2: shadow` (enabled at this deployment), review Ops → *Topic intelligence*. Then decide on `expanded_categories: on`, `demand_signals: on` and `scoring_v2: on`, in that order.
2. **Creative experiments.** Optionally run a `hook_type` experiment. The craft data shows the hook is the weakest dimension (mean about 45/100).
3. **Autopilot.** It is in OBSERVE since 2026-11-21 01:23 IST. RECOMMEND needs `AUTHORIZE RECOMMEND` once readiness passes (no earlier than about 2026-11-23 01:23 IST).
4. **Learning proposals (3.35).** Review Ops → *YouTube Analytics and learning* once the data is sufficient. Every proposal needs explicit authorization; none is applied automatically.
5. **Optional:** add `PEXELS_API_KEY` for a second footage library. Enable the SAPI voice fallback (`CRAZZYDOCS_TTS_FALLBACK=sapi`) only if a robotic voice is preferable to a skipped cycle.

Never authorized by any of this: public YouTube publishing, external communications, OAuth changes, destructive operations, financial actions and destructive git operations.

Outside these stages, still open:
- WinError 32 file locks during acquisition;
- QC duration failures;
- YouTube quota pressure;
- Community API publishing waits for Google pilot access and a `youtube.force-ssl` re-consent;
- `test_stage_3_19_creative_identity_e2e.py` hardcodes the main checkout path (it trips the worktree isolation guard when run in a focused selection).
