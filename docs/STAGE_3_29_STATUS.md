# Stage 3.29 — Production Reliability and Quality: Status

Date: 2026-11-19/20 (IST). Every finding below was reproduced against the running system, its logs and
database, or a real render; prior reports were not taken as evidence.

## 1. Dashboard auto-start: root cause and fix

**Evidence** (startup logs, logon of 2026-11-14 15:53 and 2026-11-16 15:28):

* Three launchers start at logon: `CrazzyDocs-Health-Dashboard` (`run_dashboard.py --open-browser`),
  `CrazzyDocs-Startup-Manager` (`--boot`, which also launches a dashboard) and `CrazzyDocs-Health-Monitoring`
  (`--monitor`, which relaunches a dashboard it thinks is down). They raced; the losers died with
  `[Errno 10048] address already in use`.
* A cold start (imports, DB checks, first health probe) took ≈28 s. The browser was only opened if the server
  became healthy within 30 s (`run_dashboard.py`) or 25 s (startup manager), so on a slow logon **no browser
  window opened at all**, although the server came up.
* The monitor treated "port not answering yet" as a crash and started another instance.

**Fix** (`backend/app/core/dashboard_startup.py`, `run_dashboard.py`, `startup_manager.py`):

* a cross-process **server lock** (`logs/dashboard_server.lock`): exactly one process serves; a second launcher
  logs `duplicate_launch_skipped` and only waits for health;
* the browser opens **only after `/api/dashboard/health` answers HEALTHY**, with a bounded wait of 180 s;
* an atomic **once-per-boot browser marker** (`logs/dashboard_browser_markers/`), so at most one tab per logon
  whichever launcher wins;
* the monitor no longer restarts a dashboard whose lock is held (still starting);
* a persistent **startup status** (`logs/dashboard_startup_status.json`, last 20 phases) shown as the
  **Startup** pill in the dashboard header and in `GET /api/control-center/deployment` → `dashboard_startup`.
* No credentials are on any command line or in these logs (the tasks pass only the script path).

**Task Scheduler configuration** (unchanged by this stage; all four tasks: logon trigger for the interactive
user, `Limited` run level, `MultipleInstances = IgnoreNew`, no execution time limit, runs on battery,
`WakeToRun = False`, working directory `D:\Agents\CrazzyDocs`, interpreter `.venv\Scripts\pythonw.exe`):

| Task | Arguments |
|---|---|
| CrazzyDocs-Health-Dashboard | `backend\scripts\run_dashboard.py --open-browser` |
| CrazzyDocs-Startup-Manager | `backend\scripts\startup_manager.py --boot` |
| CrazzyDocs-Health-Monitoring | `backend\scripts\startup_manager.py --monitor` |
| CrazzyDocs-Production-Scheduler | `backend\scripts\run_scheduler_daemon.py` |

The production activation state (the active schedule revision and whether production is enabled) lives in
the database and was not touched.

**Manual recovery**

1. Check: `http://127.0.0.1:8000/api/dashboard/health` and `logs\dashboard_startup_status.json`.
2. If the dashboard is down: `Start-ScheduledTask -TaskName CrazzyDocs-Health-Dashboard`
   (safe to repeat: a running instance holds the lock, and a duplicate exits).
3. If a stale process holds port 8000 but does not answer: find it with
   `Get-CimInstance Win32_Process | ? CommandLine -match run_dashboard`, stop that PID, then step 2.
4. The scheduler daemon is separate: never stop it while a job is RUNNING (Control Center → Deployment).

**Verified live (2026-11-20 00:08 IST, after deploying 705b404):** the old dashboard (PID 8120) was stopped and
`Start-ScheduledTask CrazzyDocs-Health-Dashboard` started PID 8828; `/api/dashboard/health` answered HTTP 200
after 14 s; the status log shows `healthy` and then `browser_opened` (one marker for this boot). Then
`Start-ScheduledTask CrazzyDocs-Startup-Manager` (result 0) launched no second dashboard or daemon and recorded
`browser_already_opened_this_boot`: no second tab. Not verified: an actual reboot/logon (the logon path runs the
same code).

## 2. Production vs test quality: root cause

**Exact divergence:** `active_visual_engine()` (`backend/app/rendering/pop_info/pipeline.py`) reads the feature
flag `CRAZZYDOCS_VISUAL_ENGINE`. It is set in neither the process environment nor `.env.local`, so every
scheduled production took the **legacy branch** (`LemoOpuscarFilmBuilder`), while all approved test videos were
rendered by `render_pop_info_test_videos.py` with the Pop Info engine. Both report "renderer lemo-opuscar", so the
difference was invisible in the dashboard.

Evidence: the contact sheet of production `cycle_3_20261116_1200` shows the legacy look — dark slates, text
clipped off-screen, a "100%" badge on every scene, the same hexagon motif repeated — while the test renders
show illustrated environments, kinetic type and selected photos.

Second divergence, inside the flag branch itself: production passed topic-level `photos` and no `key_entities`,
and did not request scene-level photo selection, unlike the test script. Fixed: both now take their options
from one constant, `render_parity.APPROVED_RENDER_OPTIONS`.

**Parity check:** `render_parity.parity_report()` compares the production configuration with the approved one
(renderer, engine, engine revision, scene photo selection, illustrated environments, visual quality gate,
resolution, fps, loudness, duration window). Every production logs it at the render stage, it is written to
each render manifest, and the dashboard's Creative Engine card shows **Parity: MATCH / MISMATCH** with the
differences. Today production reports a mismatch (`visual_engine: legacy`), deliberately: switching engines is
your decision (§11).

**Render manifest:** every render now writes `manifests/render_manifest_<job>.json` with renderer, engine and
revision, the full configuration and flag source, parity, script fingerprint (SHA-256, word count, source
model), asset provenance, scene types, visual modes, creative direction, quality gate results, the measured
duration and the thumbnail validation.

## 3. Configuration differences (production today vs approved test renders)

| Setting | Production (flag unset) | Approved tests / production-path runs |
|---|---|---|
| visual engine | legacy film builder | pop_info_v1 (revision 3.28) |
| scene-level photo selection | no (topic photos only) | yes (Openverse / Wikimedia / NASA / IA, licensed) |
| illustrated environments | no | yes |
| visual quality gate | not run | run (overall and per-dimension scores) |
| key entities to the planner | no | yes |
| renderer, resolution, fps, loudness | lemo-opuscar, 1080×1920, 30, −14 LUFS | identical |

## 4. Duration standard

30–58 s inclusive, everywhere, measured on the **final MP4** with ffprobe (`duration_standards.duration_accepted`,
rounded to the millisecond, no tolerance). Applied in the orchestrator's QC (it now probes the final file instead
of trusting the timeline), `qc_service`, the creative production policy, the visual quality gate, the control
centre, the script generator's word budget (69–133 words) and the dashboard's duration field (30–58).
Previously the window was 50–58 s, and `cycle_3_20261116` and `cycle_8_20261118` failed QC at 49.2 s.
Tests: 29.9 rejected; 30, 45, 50, 58 accepted; 58.1 rejected — both on numbers and on real MP4s.

## 5. Thumbnails: root cause and fix

**Findings** (YouTube API plus visual inspection of all 27 channel videos):

* Every `thumbnails().set` call had succeeded, but the uploaded image was the 9:16 portrait design, which YouTube
  pillarboxes in 16:9 surfaces. The portrait PNGs were 1–3.5 MB, so some exceeded the 2 MB limit.
* "Verified" was hard-coded: nothing checked what YouTube actually served, and no state was persisted.
* Several images were off-topic (a graffiti tram for a CO₂ story).
* The **Shorts feed** image (`oar2`) is a frame chosen by YouTube; the Data API cannot set it for Shorts.

**Fix** (`backend/app/services/thumbnail_pipeline.py`, `youtube_publishing_engine.py`):

* a dedicated **1280×720 JPEG upload artifact** composed from the topic's own focal photo and headline (full-bleed
  subject + headline panel; portrait over a blurred fill; or a text card), stepping JPEG quality down until
  < 2 MB; the 9:16 design is kept for the video;
* validation (size, dimensions, format, bytes) **before** upload;
* upload only after the video ID exists, with `thumbnails().set` on that **existing ID** (never a re-upload);
* **remote verification**: the served thumbnail is compared by perceptual hash with the artifact, retried after
  ≥ 120 s, failing after 6 attempts; the result is persisted in `thumbnail_state`;
* the queue shows **Thumb: verified / pending / failed**; a missing or failed thumbnail keeps the row from being
  READY;
* the same OAuth scopes as before (`youtube.upload`, `youtube.readonly`); no broader scope was needed.

Existing videos were **not** changed. Their 16:9 thumbnails can be replaced with `retry_thumbnail` on each
existing ID (50 quota units each) if you want that; it was not done here.

## 6. API inventory and missing credentials

Full audit: `docs/API_INTEGRATION_AUDIT.md`; live view: dashboard **API Providers** card /
`GET /api/providers/health` (values are never returned). Summary: Gemini, YouTube Data API, Pixabay, SMTP are
configured and healthy; Wikimedia, NASA, Wikipedia, phys.org and edge-tts need no key; Openverse and Internet
Archive are intermittently unavailable (the pipeline falls back); YouTube Analytics and GNews discovery are not
implemented (`GNEWS_API_KEY` is set but unused); `PEXELS_API_KEY` is the only missing key, and it is optional.

## 7. Test results

Full suite (`pytest backend/tests`): **792 passed, 5 failed, 173 skipped** (Stage 3.28 baseline: 738 passed,
4 failed, 173 skipped). New for 3.29: 54 tests (dashboard startup incl. a real cross-process lock, duration window
incl. real MP4 probes, parity and manifests, orphaned-job reconciliation, thumbnail pipeline, provider audit,
planner fixes).

The 4 baseline failures are unchanged (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`,
2 × `test_youtube_branding`). The fifth, `test_slow_youtube_status_does_not_block_other_dashboard_requests`, is a
timing test with a 1.0 s budget for a cold `/api/dashboard/health` call: on a clean HEAD checkout the call takes
0.81 s; in this worktree 1.0–1.1 s because of 438 KB of local progress-store data from the validation renders.
With those files moved aside it passed 2 of 3 runs. It is load- and data-sensitive, not caused by 3.29 code, and the
assertion was not changed.

## 8. Production-path videos vs test and legacy videos

Four production-path renders through `master_orchestrator.execute_production_pipeline` (execution context
VALIDATION, the real topic discovery, Gemini, edge-tts, photo providers, QC, integrity gate and thumbnail stages),
with `CRAZZYDOCS_VISUAL_ENGINE=pop_info_v1` set **only in that process**. Nothing was uploaded or published.

| Job | Topic | Duration (final MP4) | Visual gate | QC / integrity | Script |
|---|---|---|---|---|---|
| val_prodpath_1_20261119_225550 | Jensen Huang | 55.87 s | – | not reached: the run stopped after rendering on a hard-coded ffprobe path (fixed) | Gemini |
| val_prodpath_1_20261119_232000 | Cognitive bias | 56.37 s | 82.7 pass | pass / 15 checks | extractive fallback |
| val_prodpath_2_20261119_233422 | Royal Gloucestershire Hussars | 54.67 s | 86.2 pass | pass / 15 checks | Gemini 3.6-flash |
| val_prodpath_1_20261119_234349 | Antimicrobial resistance | 54.83 s | 82.7 pass | pass / 15 checks | extractive fallback |

All report parity MATCH. Contact sheets match the approved test renders: illustrated environments chosen per topic
(hospital, museum gallery, laboratory), kinetic typography, spotlight/callout/process/timeline scenes, licensed
photos with on-screen attribution, branded ending. The legacy production (`cycle_3_20261116_1200`) by contrast shows
dark slates, clipped text and a repeated "100%" badge.

Defects found in these renders and fixed in this stage (with tests): "US$141.9" cut to "US$141" (decimal split), a
15 s static payoff card (long-sentence split), a stray quote mark in `own "subjective reality` (unbalanced quotes),
an incomplete manifest (scene types now read from the plan on disk). Remaining weaknesses, not fixed: occasional
odd icon choices (a rhino for "infections in humans"), weak process-node labels ("Sometimes"), and low hook scores
(38–64 of 100) when the script is the extractive fallback rather than Gemini.

Files: `media/productions/val_prodpath_*` in the Stage 3.29 worktree (`video/showcase_*.mp4`,
`manifests/render_manifest_*.json`, `thumbnails/*_youtube_1280x720.jpg`).

## 9. Commit

`705b404` feat: Stage 3.29 production reliability and quality — pushed to `origin/main` (fast-forward from `d8a893f`, verified with `git ls-remote`). This report is committed on top of it.

## 10. Deployment

* Backup: `D:\Agents\CrazzyDocs_backups\pre_stage_3_29_20261119_234409` (online SQLite backup of
  `backend\studio.db`, `git bundle --all`, config).
* **Dashboard: deployed** — PID 8828, loaded revision = source on disk, not stale.
* **Scheduler daemon: NOT restarted** — PID 13400 still runs the 3.28 code. The restart endpoint reports
  `safe:false` because two rows look active: the orphan `cycle_10_20261118_0900` (RETRYING, last heartbeat
  2026-11-18 03:35 UTC) and `cycle_1_20261120_0000` (RUNNING since 00:00 IST). Evidence that nothing is executing:
  daemon CPU 0.00 s over 8 s, no child processes, no progress entry and no workspace for cycle_1, stage unchanged
  since creation. cycle_1 is blocked by the orphan, which is exactly the bug the new reconciler fixes. Restarting
  would bypass the safety interlock, so it waits for your approval (§11).
* Production activation, schedule (four cycles, revision `4a2e16f244df`, changed by you at 23:13 IST), timezone,
  concurrency and renderer were not changed. The Pop Info engine is **not** activated for production.

## 11. Blockers and decisions for you

1. **Daemon restart (urgent).** Until the daemon runs the new reconciler, the dead orphan
   `cycle_10_20261118_0900` holds production capacity and every cycle is deferred and expires — cycle_1 of today
   (recovery deadline 04:00 IST) included. Needs your OK to stop PID 13400 past the interlock (the monitor relaunches
   it on the new code within ~30 s; the reconciler then closes the orphan and starts cycle_1).
2. **Engine activation.** Production still renders with the legacy builder. Activating Pop Info means adding
   `CRAZZYDOCS_VISUAL_ENGINE=pop_info_v1` to `.env.local` and restarting the daemon — only after you have watched the
   production-path videos and approved them.
3. **Sleep/logon.** All tasks have `WakeToRun = False` and start at logon; a sleeping or logged-off PC misses cycles.
4. **YouTube quota.** Four cycles a day is ≈ 6,600 units with thumbnails, inside 10,000; more needs a quota increase.
5. **Shorts feed thumbnail** (`oar2`) cannot be set through the API for Shorts; only the 16:9 surfaces use ours.
6. **Existing videos' thumbnails** were left as they are; replacing them with the new 1280×720 artifacts is
   optional (`retry_thumbnail`, 50 units per video).

## 12. API keys

No key is required for production today. Optional:

| Env var (`.env.local`) | Provider | Benefit | Cost |
|---|---|---|---|
| `PEXELS_API_KEY` | Pexels | one more licensed photo source when Openverse times out | free |
| — (Google Cloud console) | YouTube Data API quota increase | only if more than ~5 uploads a day are planned | free application |
| `GNEWS_API_KEY` (already set) | GNews | unused today: either remove it or implement GNews topic discovery | free tier |
