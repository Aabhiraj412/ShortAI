# Render Fix, Simple Scheduler, Automatic Private Scheduling, Dashboard

Branch: `claude/scheduler-redesign-render-fix`, based on main `234394c`. **Not merged, not deployed.**
Live database, media, YouTube, scheduler and dashboard were not modified. The live policy is still `manual_approval`.

## 1. Lemo-Opuscar root cause

**The generated film page never set `window.READY`.** Lemo-Opuscar (`core/render/page.mjs`) waits up to 180 s for `window.READY === true` before rendering any frame. The page template in `backend/app/rendering/lemo_opuscar_film_builder.py` defined `window.DUR` and `window.render(t)`, ended with `window.render(0);`, and had no readiness line at all. Every render of the Lemo-Opuscar path could only time out.

Affected productions:
- `cycle_1_20261113_0000`;
- `cycle_2_20261113_0600` (3 attempts);
- `cycle_3_20261113_1200` (3 attempts, failed while this work was in progress).

Classification against the requested list: **(5) incorrect readiness event**, plus a second defect in category 8:
- **14 of 20 beats were `.mp4` clips placed in `<img>` elements.** These are broken images (`naturalWidth` 0), so even with READY those frames would have been blank.
- Browser launch, navigation, network, fonts and FFmpeg were all fine.

### Reproduction evidence (on a copy of `cycle_2_20261113_0600/lemo_film`; the original is untouched)

| Run | Result |
|---|---|
| Original page, library `still.mjs` | Exit 1 after the full 180 s wait. Console shows "no console errors so far"; no page errors and no failed requests. `window.READY` is `undefined`. |
| Probe of original page | `READY` undefined after 15 s; 14/20 `<img>` broken (all the `.mp4` assets); 6 images loaded. |
| Probe of fixed page | `READY` true after **1.6 s**; 20/20 assets loaded (6 images, 14 videos); `ASSET_STATUS.failed = []`. |
| Fixed page, `video.mjs --fps 30 --workers 1 --size 1080x1920` | Exit 0 in 352 s. |
| Production FFmpeg mux | Exit 0 in 63 s. Output 1080x1920, 30 fps, 54.38 s, with audio. |
| Frame check | Every sampled beat has content; video beats move (mean pixel difference 5.9 over 0.5 s). |

### Fix
- The page shows video beats with `<video>`. `render(t)` seeks deterministically and returns a promise, which the renderer awaits.
- All assets load in parallel with a 30 s bound per asset. A failed asset becomes a procedural card, so no frame is blank.
- The page then waits for `document.fonts.ready`, draws frame 0, and sets `window.READY = true` exactly once. `window.ASSET_STATUS` reports any failures.
- The FILM JSON is escaped, so a caption cannot end the `<script>` element.

### Render recovery
- **`StageArtifactCache`** (`orchestration/stage_artifact_cache.py`) stores the script, narration (TTS), media acquisition and storyboard per production. A retry reuses them when the inputs and every referenced file (by size) are unchanged; otherwise it regenerates. Topic and research were already reused from the checkpoint.
- **Bounded in-stage render retries** (`RENDER_ATTEMPTS = 2`) reuse the same prepared inputs. Failure raises `RenderFailure`.
- **`RENDER_FAILURE` classification:**
  - if every attempt was a readiness failure, it is deterministic: the job is terminal and its render inputs (lemo_film, voiceover, stage cache) are preserved, with no whole-pipeline retries spent;
  - other render failures are retried, and because of the cache only the render runs again.
- **Retention** protects preserved render-input folders file by file.

## 2. Worker and scheduler reliability
- **Worker registry:** at most one live worker per job in a process. A **heartbeat pump** refreshes `heartbeat_at` every 30 s during long stages; the render alone takes about 6 minutes with no checkpoint.
- **Stall detection:** a job is stalled only when its claimed worker is provably gone **and** its heartbeat is more than 180 s old. "Gone" means the thread finished in this process, or the owning PID is dead (checked with `OpenProcess`, never `os.kill`).
- **No false alarms:** the 5-minute `WORKER_STALLED` false alarm is gone.
- **No duplicates:** a live worker is never duplicated, however old its heartbeat looks.

## 3. Scheduler architecture
- **One authoritative schedule:** `control_center.schedule.active`, the same record the daemon loads. `services/schedule_service.py` and `/api/schedule` compute the view, the upcoming cycles, the current window and the next cycle **from the current clock on every call**.
- **A cycle has:**
  - an id and a name;
  - a start time (HH:MM);
  - a recovery duration (30–1,440 min);
  - a target publish time (HH:MM; the next-day rollover is derived);
  - an enabled flag and weekdays.
- **The target publish time is used only for YouTube `publishAt`.** It never moves the start, shortens recovery or blocks completion; tests prove this both ways.
- **Midnight:** a cycle still inside its recovery window after midnight keeps its occurrence date and job ID (`ProductionCycle.occurrence_start`). Previously it was re-dated to today, which orphaned its recovery. Upcoming, current and next cycle all include it.
- **Presets are editable:** an edited preset is saved as CUSTOM. Overlapping recovery windows produce a warning, because production capacity runs them one at a time. An explicitly empty weekday list is invalid.
- **Removed from active execution and from the active UI:**
  - publication slots and mappings, unmapped slots;
  - Pub Prep and Pub Recovery;
  - overlap policy;
  - wait-for-slot.

  The legacy publication-slot APIs remain, local-only with no effect and no UI.

### Why the dashboard showed a four-cycle schedule and wrong next cycle
- **The schedule did change.** At 03:56:02 and 03:56:05 IST, `POST /api/control-center/unified-schedule/validate` and then `/activate` came from 127.0.0.1, the dashboard. They replaced the one-cycle schedule (`70795aff65e6`) with four cycles (`d3550fbd996b`), which the daemon loaded at 03:56:12.
- **Obsolete fields:** the old Production Scheduler tab merged a separate legacy publication-slot store into its display (slots, mappings, Pub Prep/Recovery).
- **Wrong "next cycle", for four separate reasons:**
  - the Overview "Next Production" was computed **once at page load** (`refreshControlAndCycles` was not on the refresh interval), with a made-up `00:00 IST` fallback;
  - labels were hardcoded as "FOUR_CYCLES_DAILY";
  - "Next YouTube Publish" read the legacy slot store;
  - the status email's "next cycle" was **hardcoded by hour of day**.

  All of these now come from `/api/schedule`, refreshed every 15 s.

## 4. Dashboard
- **Tabs:** Overview · Production Schedule · Publishing Queue · *Technical:* Mode & Policy · Production & Storage Config · Processes & Logs.
- **Overview:**
  - cards for operating mode, scheduler, next production cycle (with countdown and target publish), active production, Publishing Queue count, last successful production, and YouTube connection;
  - the **Production Monitor** (stage, progress, last activity, current error, recovery: next retry and attempts);
  - today's cycles;
  - technical details collapsed.
- **Production Schedule:**
  - a table with Cycle, Production Start, Recovery Duration, Target Publish Time, Days and Status;
  - Add, Edit (modal), Delete, Enable/Disable, presets, Save Draft, Validate and Activate (with confirmation);
  - upcoming production;
  - technical revision details collapsed.
- **Policy selector:** offers `manual_approval` and `automatic_private_scheduled`. The removed legacy `automatic` value is no longer offered, or shown as healthy.
- **Verified in a real browser** against an isolated instance: worktree code, a throwaway database, TEST context and no OAuth token. I added a cycle, disabled one, validated (warnings displayed), confirmed and activated (version 3). The statuses updated (Producing, Next, Disabled, next-day labels), every overview value matched the seed data, and none of the legacy terms appear in the rendered page.

## 5. Publishing workflow (policy `automatic_private_scheduled`)
The steps are:

`QC_PASSED → PUBLISHING_QUEUE_ENTRY_CREATED → upload PRIVATE with publishAt = cycle target (video ID persisted right after insert) → custom thumbnail → verify remote (private + matching publishAt) → YOUTUBE_UPLOAD_CONFIRMED → PUBLICATION_SCHEDULE_CONFIRMED → PRODUCTION_CYCLE_COMPLETED`

The scheduler stores the target per occurrence in UTC (`target_publication_at_utc`) and displays it in IST.

**Edge cases:**
- **Target passed, or less than 15 minutes away:** `NEEDS_PUBLICATION_TIME`. Nothing is uploaded, nothing is ever published early, and the ledger notes the reason.
- **A video with the same title is already on the channel:** `NEEDS_REVIEW`. It is never rescheduled.
- **An already-public video:** untouched.

**Failure handling:**
- Thumbnail and scheduling are retried independently. A scheduling-only update doesn't re-send the thumbnail, and an existing YouTube ID is never uploaded again.
- A publishing failure sets the job to `HANDOFF_PENDING`. It retries only the publishing step, with backoff, using its own counter (`publish_attempts`, maximum 5). It never consumes production attempts. The rendered video and the ledger entry are preserved.

**Under `manual_approval`** (the live setting), behaviour is unchanged: videos wait in the Publishing Queue.

## 6. Database migrations
None. New state lives in existing JSON columns (`cycle_state.target_publication_at_utc`, `publishing`, `publish_attempts`, `preserved_artifacts`) and in per-production cache files. `automatic_private_scheduled` is a new allowed value of the existing policy setting.

## 7. Tests
All YouTube calls are mocked, databases are temporary, and there is no OAuth. The real-browser render test uses the pinned Lemo-Opuscar runtime.

| Run | Total | Passed | Failed | Skipped |
|---|---|---|---|---|
| Integrated baseline (`234394c`) | 593 | 416 | 4 | 173 |
| This branch (final) | 645 | 468 | 4 | 173 |

Per-test comparison on the 591 tests in both runs: 414 pass → pass, 173 skip → skip, 4 fail → fail, **0 newly failing**. The 4 remaining failures fail on the baseline too: the CleanSlateRenderer integration test, two dashboard title/favicon tests, and the YouTube icon file that isn't present in a worktree. No module loaded from another checkout.

**New tests:**
- `test_render_and_worker_reliability` (17), including a real headless-browser render;
- `test_schedule_and_auto_publish` (35).

**Tests updated** for intentional contract changes, with every scenario kept:
- presets are editable;
- recovery is 30–1,440 min;
- overlap is a warning;
- the target is shown in previews;
- the old slot UI is replaced;
- the daemon loop moved to `daemon_runtime`.

During every run, the live DB was unchanged (excluding the daemon's own heartbeat keys) and main `media/` had 0 changes.

## 8. Remaining limitations
- **Nothing is deployed.** The live daemon still runs the code without the READY fix, so **today's `cycle_4` (18:00 IST) will fail the same way** unless this branch is integrated before then.
- The legacy publication-slot backend modules and APIs still exist, with no UI and no effect. Removing them is a follow-up.
- The real YouTube upload, scheduling and processing path is tested only with mocks. YouTube processing delays surface as `SCHEDULE_PENDING` and are retried. The first live run should be watched.
- Rendering takes about 6 minutes plus about 1 minute of muxing on this machine.
- About 15 media modules still hardcode `D:\Agents\CrazzyDocs`, which affects worktree tests only.

## 9. Activation steps (each needs your approval)
1. **Merge:** main has no new commits since `234394c` and its working tree is clean apart from the 5 unrelated files, so this is a plain `git merge --ff-only refs/heads/claude/scheduler-redesign-render-fix`. Back up `studio.db` with the online backup API first.
2. **Restart:**
   - Daemon: with no job running, `POST /api/control-center/deployment/restart {"confirm": true}`. This works now, because the running daemon (15036) has the endpoint. The monitor relaunches it.
   - Dashboard: `Stop-Process -Id 23556; Start-ScheduledTask -TaskName "CrazzyDocs-Health-Dashboard"`.
   - Check that `/api/control-center/deployment` shows both as CURRENT.
3. **Schedule:** review the active schedule in the Production Schedule tab. It is currently the four-cycle schedule activated at 03:56 (00/06/12/18, targets 06/12/18/00). Change it there if needed.
4. **Automatic private scheduling** (optional):
   - reconnect YouTube (OAuth is currently DISCONNECTED; interactive sign-in by you);
   - confirm quota;
   - choose `automatic_private_scheduled` under Mode & Policy, which requires confirmation.

   Until then, videos wait in the Publishing Queue under `manual_approval`.
5. **First production after deploy:** watch the Production Monitor. Expect READY within seconds, a render of about 6 minutes, then either a queue entry (manual) or a confirmed private upload with publishAt (automatic).

## 10. Readiness for controlled production
**Ready for a controlled deployment, with the steps above and a watched first cycle.**
- **Render fix:** proven on real production artifacts with the real renderer.
- **Scheduling and publishing:** state transitions verified with isolated data and mocked YouTube. The UI was verified in a browser.
- **Not yet proven:** an end-to-end run on the live daemon, and a real YouTube upload. Both need your authorization.
