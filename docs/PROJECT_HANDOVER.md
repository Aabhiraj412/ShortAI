# CrazzyDocs Autonomous Studio: Project Handover

**As of:** 2026-11-20, evening IST (Stage 3.32E implemented, uncommitted).

**Repository:** `D:\Agents\CrazzyDocs` (production checkout, branch `main`). GitHub: `Aabhiraj412/ShortAI`, branch `main`.

**Development worktree:** `.claude\worktrees\crazzy-docs-handover-b1a088`, branch `claude/crazzy-docs-handover-b1a088`, at `687c156` plus the uncommitted Stage 3.32E work.

**Latest commit:** `687c156`, on `main` and `origin/main`.

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
| (uncommitted, worktree `crazzy-docs-handover-b1a088`) | **3.32E: Community post draft automation**: `backend/app/community/`, 16 `/api/community` routes, Community Posts tab, `community_draft_tick` scheduler job, 75 tests, 6 docs (`docs/STAGE_3_32E_*.md`). Not deployed, not activated |

**Test baseline at `710b8c5`:** 1208 passed, 5 failed, 173 skipped. The 5 failures are pre-existing (section 4).

**Docs for each stage:** `docs/STAGE_3_32*.md`. The latest audit is `docs/STAGE_3_32D_FINAL_SECURITY_AUDIT.md`.

## 3. Current production state

| Item | State |
|---|---|
| Restart at 15:23:59 IST | The computer was restarted from the Start menu under the user's account (System event 1074). At logon the scheduled tasks relaunched everything from the files on disk (`687c156`). No backup was taken for this implicit deployment. |
| Daemon | PID 5244 (started 15:24:33 IST), code `687c156` (loaded revision `72f4aceffb72cf74`). |
| Dashboard | PID 5252 (started 15:24:33 IST), code `687c156`. The 3.32D audit fixes (F1–F10) are therefore **live**. Stage 3.32E is not committed, so it is not running. |
| Monitor | PID 5260 (`startup_manager.py --monitor`), running again since the 15:24 restart. |
| Schedule | Active revision `ccf0bd04f159`, CUSTOM 24 cycles, named profile "24/7", daemon in sync. |
| Jobs | 45 completed, 12 failed, 40 recovery-window-expired, 1 aborted. |
| Autonomy | All levels OFF; startup kill switch active (no `autonomy_startup.json`); 0 requests, 0 executions. |
| Self-healing | BLOCKED (startup kill switch), level OFF; its six `selfheal_*` tables exist and are empty. |
| Community drafts (3.32E) | Not deployed. The live database has no `community_*` tables. |
| Uncommitted user changes (keep them) | `backend/app/content/topic_history.json`, `backend/data/creative_policy_config.json`; untracked `backups/`, `backend/backups/`, `media/` |
| Latest backup | `D:\Agents\CrazzyDocs_backups\deploy_5b2a4ad_20261120_111917` |

## 4. Outstanding bugs and risks

| # | Item | Notes |
|---|---|---|
| 1 | **FFmpeg padding defect** | In `rendering/explainer_animation_engine.py` (Priority B adaptation, scale then pad), "Padded dimensions cannot be smaller than input". It is deterministic, but the retry policy labels it TRANSIENT, so it burns recovery windows. A code fix is needed; not started. |
| 2 | **WinError 32** | File locks on `assets/acquired/temp_*.tmp` during acquisition. Resource contention; root cause not identified. |
| 3 | **QC duration failures** | Duration 49.2 s, outside the 50–58 s window; seen after rendering. |
| 4 | **High recovery-window expiry rate** | 40 of 98 jobs; YouTube daily quota exhaustion also delays publishing. |
| 5 | **Monitor not running** | After the reboot; needs investigating or relaunching (operator decision). |
| 6 | **5 known test failures** | `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two in `test_youtube_branding`, and the timing-sensitive `test_slow_youtube_status_does_not_block_other_dashboard_requests`. |
| 7 | **DNS rebinding** | Could expose the CSRF token through a read (pre-existing; writes still need an allow-listed Origin). A Host-header allow-list is the suggested fix. |
| 8 | **Stage 3.31 flags** | Their final state still awaits the user's approval. |

## 5. Pending roadmap

Detail: `docs/STAGE_3_32_IMPLEMENTATION_PLAN.md`.

1. **Deploy `710b8c5` to the dashboard**: the exact next task (section 7).
2. **Fix the FFmpeg padding defect** (bug 1). This is the largest source of lost cycles.
3. **3.32E**: Community posts in DRAFT mode. **Implemented and tested, awaiting commit authorization.** Activation needs a dashboard restart (API and tab), a daemon restart (scheduler job, separate approval), then enabling it in Community Posts → Schedule settings. See `docs/STAGE_3_32E_IMPLEMENTATION.md` §7.
4. **3.32G**: controlled autopilot rollout. Phased activation (OBSERVE, then RECOMMEND, then standing diagnostics only) with weekly reports and kill-switch drills. **Each gate needs user approval.**
5. **3.32F**: Community API publishing. **BLOCKED** on YouTube posts API access, a separate `youtube.force-ssl` consent, and 30 clean days of 3.32E.

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

The 15:24 restart already put `687c156` (3.32D plus the audit fixes) into production, so the old "deploy `710b8c5`" task is done in effect. It had no pre-deployment backup; take one before the next deployment.

**Next: commit Stage 3.32E, then deploy it.** Each step needs the user's explicit authorization.

1. **Commit and push** the 3.32E work from worktree `crazzy-docs-handover-b1a088` (fast-forward `main`, push, verify that `ls-remote` matches).
2. **Back up** `studio.db` (SQLite online backup and integrity check), the configuration files and the git state, into a new `D:\Agents\CrazzyDocs_backups\deploy_<sha>_<timestamp>` folder.
3. **Restart the dashboard** (API and Community Posts tab):
   - between :10 and :50, with no active job;
   - check the PID's command line, stop only that PID, wait at least 12 s, then run `Start-ScheduledTask CrazzyDocs-Health-Dashboard`;
   - the monitor is running now and may relaunch it itself, so check for a duplicate.
4. **Restart the scheduler daemon** to load the `community_draft_tick` job. This needs **separate approval**, because the daemon runs production; same idle and timing checks.
5. **Verify:**
   - `/api/community` returns `configured: false`;
   - the live database has no `community_*` tables until the first save;
   - the production schedule `ccf0bd04f159` is unchanged;
   - autonomy is OFF and self-healing is BLOCKED.
6. **Activation** is a separate decision: in Community Posts, try Generate now, then enable automatic generation at 10:00 Asia/Kolkata. See `docs/STAGE_3_32E_IMPLEMENTATION.md` §7.
