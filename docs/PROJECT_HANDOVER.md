# CrazzyDocs Autonomous Studio: Project Handover

> **This is the authoritative engineering context.** Read it, not a fresh repository audit, before any milestone, and update §2, §3, §4 and §7 when the milestone ends. The execution policy is in the repository root `CLAUDE.md`. Verification commands are in §8. Per-stage detail lives in `docs/STAGE_*.md`.

**As of:** 2026-11-21 IST. Stage 3.35 (YouTube Analytics, autonomous learning and visual asset intelligence) is implemented and deployed in the commit that contains this file:
- the root cause of the repeated Analytics "disconnections" is fixed (an upload-client refresh erased the analytics scope from the shared token);
- analytics ingestion with a first-7-days window;
- a learning engine with proposals only;
- the provider audit by rendered usage;
- scene-level real-world footage with per-video asset manifests;
- a longer Edge TTS backoff.

See `docs/STAGE_3_35_IMPLEMENTATION_REPORT.md` and the other `STAGE_3_35_*` reports. Autopilot stays in OBSERVE; topic intelligence stays in shadow; public publishing is unchanged.

**Update 2026-11-27 IST: Stage 3.36 (Autopilot continuous production controller).** It is implemented, tested and deployed with Autopilot **OFF** (the default). The new `production` autonomy category is OFF; no level, standing approval, kill switch or `autonomy_startup.json` was changed. See `docs/STAGE_3_36_AUTOPILOT.md`.

**Update 2026-11-27 IST: Stage 3.37 (multi-pipeline production and resource orchestration).** It adds one shared, resource-aware coordinator over two logical queues: Shorts (the unchanged scheduler) and long-form (a new persisted, step-based queue). There is one heavyweight render-slot lease, and Shorts always comes first. Long-form is **OFF** by default and no long-form executor is installed. See `docs/STAGE_3_37_MULTI_PIPELINE_PRODUCTION.md`.

**Update 2026-11-27 IST: Remediation (stability and autonomy, before Stage 4.01).**
- **Dashboard stability (WS1):** Selector event loop, blocking handlers moved off the loop, resource cache, and the deploy-time startup race fixed. Committed and deployed: `7b4f632` (WS1 part 1). The startup race fix is in the worktree.
- **Autonomy (WS2):** three control states. Production is now RESTRICTED (a fixed, read-only policy set runs automatically), not blocked.
- **Recommendations and self-healing (WS5/WS6):** both run, and their policy actions execute automatically.
- **Topics (WS3):** the identity pre-check runs before enrichment; the ledger re-sighting fix.
- **Providers (WS4):** status built from production evidence.
- **Quality (WS7):** a creative feedback loop, plus a deferred Gemini retry.
- **Operator attention:** one feed.

Full report: `docs/REMEDIATION_STABILITY_AUTONOMY.md`.

**Update 2026-11-27 IST: Stage 4.01 (multi-channel foundation).** The execution gate passed. Added: a channel registry (`backend/app/channels/`), identities verified with `channels.list?mine=true`, isolated per-channel credentials, the source-to-localized map, and a dashboard Channels card. English (master) is verified and active. **Hindi is PENDING_VERIFICATION:** it has no authorization and no localization pipeline, and is not production-ready. See `docs/STAGE_4_01_MULTI_CHANNEL_FOUNDATION.md`.

**Update 2026-11-27 IST: Stage 4.02 (Hindi localization).** A completed English Short is adapted into natural Hindi (Gemini free tier, script QC), voiced (edge-tts hi-IN), rendered with Devanagari text through Lemo-Opuscar inside the shared render slot, and checked by Hindi QC. Output goes to `media/localizations/hi/<job>/`. Operator-enqueued from the Queue tab. **Never published automatically**; Hindi publishing stays blocked (channel unverified, not authorized). See `docs/STAGE_4_02_HINDI_LOCALIZATION.md`.

**Update 2026-11-27 IST: Operator-dependency reduction.** Emergency stop, the optional startup configuration and the execution gate are separate fields (a missing `autonomy_startup.json` is RESTRICTED, never shown as a kill switch; blocking behaviour unchanged). The Autopilot controller heartbeat continues while OFF. Attention items are reconciled with live state (topic-flag decision gone while both flags are ON; credentials with a working fallback are optional; creative decisions typed). Self-healing reports diagnosed / recovery executed / resolved / output completed and current vs history. Staged asset re-acquisition (bounded, verified, reversible) is policy-automatic in RESTRICTED. See `docs/OPERATOR_DEPENDENCY_REDUCTION.md`.

**Repository:** `D:\Agents\CrazzyDocs` (production checkout, branch `main`). GitHub: `Aabhiraj412/ShortAI`, branch `main`.

**Development worktree:** `.claude\worktrees\crazzy-docs-handover-b1a088`, branch `claude/crazzy-docs-handover-b1a088`, in step with `main`.

**Latest commit on `main` / `origin/main`:** run `git log -1`. As of 2026-11-27 it is the maintenance commit that follows `fed2845` (3.36).

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
| Email redesign (2026-11) | `services/email_templates/` (new), `email_monitoring_service.py`, `daily_publishing_summary.py`, `notification_service.py` | **Email notifications and reports redesigned.**<br>- **Design:** one shared design system (responsive table layout, inline styles, escaped content, plain-text twin, CID inline images).<br>- **Accuracy fixes:** YouTube state read from the publishing ledger (emails said "withheld" for uploaded/scheduled videos); fabricated QC defaults removed; day summary covers all 24 cycles (was cycles 1-4); daily report now covers the previous complete day (was 00:00-06:00 of the current day).<br>- **Unchanged:** triggers, dedup ledger, retry semantics, recipients, subject formats.<br>- **Details:** `docs/EMAIL_SYSTEM.md`; previews: `python backend/scripts/render_email_previews.py`. |
| 3.36 | `autonomy/production_controller.py`, `production_ownership.py`, `decision_engine.py`, `production_handlers.py`; hooks in `scheduler/scheduler.py`, `daemon_runtime.py` | **Autopilot continuous production controller.**<br>- **State machine:** OFF / STARTING / ON / STOPPING / BLOCKED, persisted and audited, separate from the autonomy mode (`production` category, min with global).<br>- **Where it runs:** one tick in the daemon recovery loop; not a new scheduler or worker.<br>- **Ownership:** taken only as EXECUTOR (APPROVED_EXECUTION plus standing approvals for `production.scheduled_cycle.dispatch` and `production.recovery.dispatch`). While Autopilot owns production, the unchanged cron jobs hand fired occurrences to it and it dispatches them through the same `run_cycle_generation`, one renderer at a time. On release, undispatched occurrences are replayed through the scheduler path.<br>- **Decisions:** the decision engine explains every proceed, wait or hold decision and covers bottlenecks, resources, quota and repeated failures.<br>- **Dashboard:** Autopilot tab → controller panel. |
| 3.37 | `scheduler/production_coordinator.py`, `scheduler/resource_monitor.py`; hooks in `scheduler.py` (Shorts worker lease), `daemon_runtime.py` (tick, 60 s throttle), `main.py` (manual-run lease, `/api/production/queues*`) | **Multi-pipeline production and resource orchestration.**<br>- **Queues:** Shorts = the existing scheduler and `persistent_jobs`; long-form = `production_queue_jobs` (operator-enqueued, checkpointed LIGHT/HEAVY steps, executor per kind, none installed).<br>- **Render slot:** `production_render_slots`, a CAS lease with ceiling `BENCHMARKED_MAX_HEAVY` = 1. Shorts never waits more than 600 s and is fail-open.<br>- **Admission:** CPU, available-RAM and disk thresholds; a heavy long-form step must end before the next Shorts start and needs no active Shorts job; light steps may overlap.<br>- **Fairness:** aging (+1 per 30 min waited); starvation flag after 6 h.<br>- **Recovery:** dead-owner claims are requeued at their checkpoint, bounded to 3 attempts.<br>- **Resources:** CPU, RAM, GPU (PDH) and disk, plus FFmpeg process count.<br>- **Dashboard:** Autopilot tab → *Production queues and resources*. |
| Remediation | `core/loop_diagnostics.py`, `core/dashboard_startup.py`, `scripts/run_dashboard.py`, `autonomy/control_plane.py` (control states, `POLICY_AUTOMATIC`), `autonomy/autopilot.py` (`auto_bridge`), `self_healing/engine.py`, `recommendations/engine.py`, `content/candidate_ledger.py`, `content/curiosity_topic_engine.py` (`identity_precheck`), `services/provider_audit.py` (`production_evidence`), `content/grounded_script_writer.py` (deferred round), `creative_intel/feedback.py`, `services/operator_attention.py` | **Stability and autonomy remediation.**<br>- **Dashboard:** Selector loop on Windows; read-only GETs in the threadpool; `/api/health/loop`.<br>- **Control states:** EMERGENCY_STOP / RESTRICTED / NORMAL. A missing startup config means RESTRICTED, never a silent block.<br>- **Automatic policy:** signed `policy:<STATE>` approvals for read-only diagnostics, reports, recommendation records, local notifications and self-healing diagnostics.<br>- **Recommendations:** automatic follow-ups, each run once, with a 6 h cooldown.<br>- **Topics:** identity pre-check before enrichment.<br>- **Providers:** state from production evidence.<br>- **Quality:** feedback loop at `/api/creative-intel/feedback`; one deferred Gemini round when all models fail transiently.<br>- **Attention:** `/api/operations/attention`. |
| 4.01 | `channels/registry.py`, `channels/status.py`; `YouTubeOAuthManager(record_connection_events=…)`; `/api/channels*`; dashboard Queue tab → Channels | **Multi-channel foundation.**<br>- **Tables:** `channel_registry`, `channel_events`, `content_localizations`.<br>- **English (`en`):** master; existing token, queue and analytics, unchanged.<br>- **Hindi (`hi`):** localized; own token `backend/channel_tokens/hi/token.json` (gitignored); never discovers topics.<br>- **Identity checks:** identity from `channels.list?mine=true` only; IDENTITY_CONFLICT and IDENTITY_CHANGED block the channel.<br>- **Rules:** only verified channels activate; the master cannot be deactivated here. |
| 4.02 | `localization/` (translator, script_qc, voice, render, qc, pipeline); coordinator LOCALIZATION queue; `/api/localizations*`; Queue tab → Hindi localization | **Hindi localization.** Steps translate (LIGHT), voice (LIGHT), render (HEAVY, shared slot, Shorts gaps only), qc (LIGHT). States PLANNED / IN_PROGRESS / WAITING (infrastructure, retried) / QC_REJECTED (creative, final) / FAILED / QC_PASSED. Retries reuse finished steps; retranslation archives the old attempt. No publishing or credential code in the package. |
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

## 2. Completed stages and commits

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

**Since 2026-11-20** (newest first):

| Commit | Stage |
|---|---|
| (4.02 commit, see `git log`) | **4.02** Hindi localization pipeline (translate, voice, render in the shared slot, Hindi QC). Publishing disabled. `docs/STAGE_4_02_HINDI_LOCALIZATION.md` |
| `9ce835b` | **4.01** Multi-channel foundation (English verified, Hindi PENDING_VERIFICATION) |
| (remediation commit, see `git log`) | **Remediation WS1 (part 2) to WS7 and the operator-attention feed** |
| `7b4f632` | **Remediation WS1**: dashboard event loop (deployed 2026-11-27 04:34 IST) |
| `13e846b` | **3.37** Multi-pipeline production and resource coordinator (deployed; long-form OFF, no executor) |
| `fed2845` | **3.36** Autopilot continuous production controller (deployed; Autopilot OFF) |
| `3596f5d`, `1db7e03`, `0361710` | 3.31 formal 48-hour shadow observation and its final report (`docs/STAGE_3_31_SHADOW_OBSERVATION.md` §9) |
| `f6c1d34` … `68c1c0c` | Email redesign plus fixes (`docs/EMAIL_SYSTEM.md`) |
| `fb1e050`, `417076c` | 3.32F Community publishing workflow (API publishing stays OFF) |
| `b641567` | 3.35b Wikimedia pacing in topic discovery |
| `4275621` | 3.35 Analytics reliability, learning, footage |
| `050bb13` | 3.34 production validation and reliability |
| `2bae573` | 3.31–3.33 demand-led topic intelligence, creative intelligence, rendering decision |
| `0c0f31c`, `523010a`, `09ffe30` | 3.32H–J reliability, phased activation, reports |
| `ee6c8ce` | 3.32G Controlled Autopilot |

**Test baseline at `710b8c5` (historical):** 1208 passed, 5 failed, 173 skipped. The 5 failures are pre-existing (section 4).

**Docs for each stage:** `docs/STAGE_3_32*.md`. The latest audit is `docs/STAGE_3_32D_FINAL_SECURITY_AUDIT.md`.

## 3. Current production state (2026-11-27 03:00 IST)

| Item | State |
|---|---|
| Daemon | PID 14348, started 04:34 IST by a controlled restart; code `7b4f632` (3.37 + WS1). 24 cycles plus Community, Autopilot-observation, creative, analytics and junk-cleaner jobs. |
| Dashboard | PID 17252, relaunched 04:35 IST by the monitor; code `7b4f632`. The Task Scheduler launch died on the startup-status race (fixed in the worktree, not yet deployed). Selector loop. Liveness 3 ms, CPU 0.3 % of a core. |
| Monitor | `startup_manager.py --monitor`, PID 7688. |
| Schedule | Active revision `ccf0bd04f159`, CUSTOM 24 hourly cycles, 45-min recovery windows, concurrency 1. |
| Operating mode / publishing policy | PRODUCTION / `automatic_private_scheduled` |
| Autonomy | Global OBSERVE (categories OBSERVE; `community_drafts` and `production` OFF). The startup kill switch is active (no `autonomy_startup.json`). |
| Autopilot controller (3.36) | OFF. The scheduler owns production. |
| Production coordinator (3.37) | Long-form queue disabled (`long_form_enabled: false`), not paused, no executor; heavy render slots = 1. Shorts workers lease the slot (fail-open). **4.02:** LOCALIZATION queue (`localization_enabled: true`, runs only operator-enqueued Hindi localizations, in Shorts gaps). The active schedule profile is read from the coordinator's own database; HEAVY admission fails closed when the next Shorts start is unknown. |
| Topic intelligence flags | `candidate_ledger` on; `production_query_rotation` and `exhaustion_recovery` **on** since 2026-11-27 05:45 IST (operator approved); `category_classifier_v2`, `demand_signals` and `scoring_v2` in shadow (3.31 report: do not activate v2 as-is). Backup of the previous file: `CrazzyDocs_backups\predeploy_remediation_20261127_054108\creative_policy_config.before_flags.json`. |
| Community drafts | Automation ON (daily draft); API publishing OFF (pilot access and scope missing). |
| Junk cleaner | Daily dry-run at 03:30 IST; live quarantine only with an explicit operator confirmation and no active job (§4). |
| Uncommitted user changes (keep them) | `backend/app/content/topic_history.json`, `backend/data/creative_policy_config.json`; untracked `backups/`, `backend/backups/`, `media/` |
| Control state | RESTRICTED (no `autonomy_startup.json`; **not** an emergency stop). The automatic policy runs: reports, recommendation recording, read-only diagnostics, local notifications, self-healing diagnostics, read-only provider re-checks and (since the operator-dependency reduction) staged asset re-acquisition. Production dispatch, publishing, configuration and credentials stay outside it. |
| Latest backup | `D:\Agents\CrazzyDocs_backups\predeploy_stage337_ws1_20261127_043307` (DB integrity ok; the token file is not included) |

## 4. Outstanding bugs and risks

| # | Item | Notes |
|---|---|---|
| 1 | **FFmpeg padding defect** | **Fixed in 3.32H** (root cause: odd 860×855 viewport; `pad` rounds to 854 under yuv420p). Errors of this kind are now DETERMINISTIC. Watch the Rendering panel for any new FFmpeg failure after deployment. |
| 2 | **WinError 32** | File locks on `assets/acquired/temp_*.tmp` during acquisition. Resource contention; root cause not identified. |
| 3 | **QC duration failures** | Duration 49.2 s, outside the 50–58 s window; seen after rendering. |
| 4 | **High recovery-window expiry rate** | 40 of 98 jobs; YouTube daily quota exhaustion also delays publishing. |
| 5 | **Monitor false unhealthy** | **Fixed in 3.32H** (2 s timeout on a ~2 s aggregate endpoint). The monitor now uses liveness/readiness probes and persists every decision. |
| 6 | **Known test failures (baseline, 2026-11-27: 4; full suite at 4.02: 1981 passed, 4 failed, 174 skipped)** | `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two in `test_youtube_branding`. The timing-sensitive `test_slow_youtube_status_does_not_block_other_dashboard_requests` can also fail intermittently. |
| 7 | **DNS rebinding** | Could expose the CSRF token through a read (pre-existing; writes still need an allow-listed Origin). A Host-header allow-list is the suggested fix. |
| 8 | **Stage 3.31 flags** | Their final state still awaits the user's approval. |
| 10 | **3.37 long-form executor missing** | The queue, admission and recovery are in place, but no long-form pipeline (renderer or step executor) exists. Long-form jobs wait with "no executor registered". Executor contract: `docs/STAGE_3_37_MULTI_PIPELINE_PRODUCTION.md` §8. |
| 11 | **3.37 rendering concurrency not benchmarked** | Heavy concurrency is fixed at 1. Raising it needs a concurrent-render benchmark in an operator-scheduled window with no production job (stage doc §6). Long-form steps must honour `should_yield()`; a step that overruns its estimate can overlap a Shorts render after the 600 s wait (recorded as `SHORTS_OVERLAP`). |
| 17 | **Hindi channel** | PENDING_VERIFICATION: the operator must connect the Hindi channel's own Google account (Channels card). The 4.02 localization pipeline exists and produces QC-checked Hindi MP4s locally; **publishing stays blocked** until the channel is verified, ACTIVE and Hindi publishing is explicitly authorized (a separate milestone). Limits: translation uses the Gemini free tier (it waits on quota) and the voice uses edge-tts (needs the network); no offline Hindi translation or voice is installed (operator decision). |
| 18 | **Autopilot heartbeat warning** | **Fixed** (operator-dependency reduction): the controller skipped its heartbeat while Autopilot was OFF, so the dashboard claimed "scheduler daemon down". |
| 12 | **Topic supply** | `production_query_rotation` and `exhaustion_recovery` were **activated on 2026-11-27 at 05:45 IST with operator approval** (daemon restarted, PID 4692). Watch Ops → *Topic intelligence* for eligible counts and integrity-gate failures over the next 24 h. Revert: remove both keys from `creative_policy_config.json` and restart the daemon. |
| 13 | **Gemini free tier** | 429 and 503/504 errors cause about 80 % fallback scripts, which score 13 points lower. The deferred round is measured by the feedback loop and is not claimed as a fix. |
| 14 | **Dashboard CPU** | Root cause fixed: the one-minute health poll walked the whole media tree (7 CPU-s per poll, growing) and ran a full integrity check. Both are now cached. Confirm after deploying: `worker_top_stacks` in `/api/health/loop`, and dashboard CPU below 5 %. |
| 16 | **Intermittent test** | `test_daemon_does_not_exit_while_render_in_progress` fails about 1 in 30 runs, on both pre- and post-remediation code. It is timing-sensitive (the 3.36 Autopilot first tick takes about 250 ms). Report it when it fails; do not count it as a baseline failure. |
| 15 | **Gemini quota** | 429 runs from about 01:00 IST until the daily reset (13:30 IST). The deferred round now runs only for overload (5xx/408). The model-script share is still about 20 %; measure it in `/api/creative-intel/feedback`. |
| 9 | **Junk cleaner reclaims almost nothing yet** | By design: intermediates are kept for 30 days, and production intermediates only date from mid-November. Test renders (about 1.8 GB) and downloaded assets have no retention rule (kept). The media tree grows by about 10k files a day. |

## 5. Pending roadmap

Detail: `docs/STAGE_3_32_IMPLEMENTATION_PLAN.md`.

1. **Deploy `710b8c5` to the dashboard**: the exact next task (section 7).
2. **Fix the FFmpeg padding defect** (bug 1). This is the largest source of lost cycles.
3. **3.32E**: Community posts in DRAFT mode. `89ea49b` is deployed with automation OFF. The timezone-aware schedule (US presets, DST) is implemented and tested, awaiting commit authorization; deploying it needs a dashboard **and** a daemon restart. Then the operator picks a timezone and time and enables automation. See `docs/STAGE_3_32E_IMPLEMENTATION.md` §7 and `docs/STAGE_3_32E_SCHEDULING.md`.
3a. **3.32F**: YouTube Community publishing. **Committed (`16b8628`) and deployed.** The read-only capability audit found `PILOT_ACCESS_REQUIRED` for project `crazzydocs-youtube` (`posts.list`: 404 Method not found); in addition, the token lacks a `posts.insert` scope. API publishing stays OFF and fail-closed; the manual handoff works. See `docs/STAGE_3_32F_PUBLISHING.md`.
4. **3.32G**: Controlled Autopilot. **Deployed.**
5. **3.32H/I/J**: reliability fixes, phased activation, operational reports. **Implemented and deployed.** Activation beyond OBSERVE needs explicit authorization (see section 7).
6. **3.35**: Analytics OAuth fix, analytics ingestion, learning (proposals only), provider audit and scene footage. **Implemented and deployed.**
6a. **3.36**: Autopilot continuous production controller. **Implemented, tested and deployed with Autopilot OFF.** Activation steps: `docs/STAGE_3_36_AUTOPILOT.md` §12. Learning claims unlock at 30 first-7-days videos.
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

**Operator-dependency reduction** is implemented (`docs/OPERATOR_DEPENDENCY_REDUCTION.md`). The remaining operator items are genuine: creative decisions, the Hindi channel connection, optional Pexels key, Autopilot enablement (optional) and Community API access (external).

**Stage 4.02 (Hindi localization)** is implemented. Operator actions: use the Queue tab → *Hindi localization* card to localize completed Shorts. Hindi publishing is a separate milestone; it needs the Hindi channel verified first (Channels card). See `docs/STAGE_4_02_HINDI_LOCALIZATION.md` §9.

**Remediation:** deploy the remediation commit once its full suite matches the baseline (same procedure, :15–:50 IST, no active job, new backup). Then:
- verify `/api/operations/attention` and the control state RESTRICTED;
- verify the first `policy:RESTRICTED` executions;
- verify the dashboard's long-run CPU.

Operator decisions: an optional hook experiment (WS7 proposal). The topic-supply flags were approved and activated on 2026-11-27; review their effect after 24 h.

**Stage 3.37 is complete** (long-form OFF). Next: build the long-form pipeline as an executor (stage doc §8). Then the operator decides on `long_form_enabled` and, separately, on a concurrent-render benchmark before any heavy concurrency above 1.

**Stage 3.36 is complete (deployed, Autopilot OFF).** Enabling it is an operator decision: production level OBSERVE, then Start. Execution additionally needs `autonomy_startup.json`, APPROVED_EXECUTION and standing approvals; see `docs/STAGE_3_36_AUTOPILOT.md` §12. Earlier readiness notes:
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

## 8. Verification commands

```bash
# tests against the dev worktree (Git Bash; Windows-style PYTHONPATH, otherwise imports resolve to the main checkout)
W='D:\Agents\CrazzyDocs\.claude\worktrees\crazzy-docs-handover-b1a088'
PYTHONPATH="$W;$W\\backend;$W\\backend\\tests" /d/Agents/CrazzyDocs/.venv/Scripts/python.exe -m pytest backend/tests/<file>.py -q -p no:cacheprovider
# full suite: about 23 min, run it in the background; baseline = 4 failures (section 4, item 6)

# live state (read-only)
curl -s http://127.0.0.1:8000/api/control-center/deployment      # daemon/dashboard revision, active job, restart safety
curl -s http://127.0.0.1:8000/api/autopilot/controller           # Autopilot state, ownership, decisions
curl -s http://127.0.0.1:8000/api/junk-cleaner/status            # cleaner health and last run
sqlite3 "file:D:/Agents/CrazzyDocs/backend/studio.db?mode=ro" "select id,status,current_stage from persistent_jobs order by created_at desc limit 5"
```

- **Deploy:** follow the memory/procedure in §6:
  1. verified backup to a new `CrazzyDocs_backups\predeploy_*` folder;
  2. `git merge --ff-only` in `D:\Agents\CrazzyDocs`, then push;
  3. daemon: `POST /api/control-center/deployment/restart {"confirm": true}` between :15 and :50 with no active job;
  4. dashboard: stop the verified `run_dashboard.py` PID, wait at least 12 s, `Start-ScheduledTask CrazzyDocs-Health-Dashboard`;
  5. check `/api/control-center/deployment` = OK.
- **Junk cleaner** (from `D:\Agents\CrazzyDocs`, live paths):
  `JunkCleaner(root=Path(r"D:\Agents\CrazzyDocs"), db_path=r"D:\Agents\CrazzyDocs\backend\studio.db").run(trigger="operator", live=False)`.
  - Live quarantine needs `live=True, confirm=True` and no active job.
  - `restore(run_id)` undoes a live run from its manifest (it never overwrites).
