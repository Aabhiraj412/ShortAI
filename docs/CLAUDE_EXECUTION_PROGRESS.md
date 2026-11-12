# Claude Execution Progress

Branch: `claude/crazzy-docs-verify-capabilities-ac634e` (worktree `.claude/worktrees/crazzy-docs-verify-capabilities-ac634e`).
Not merged into `main`. Live daemon, dashboard, database, media and YouTube were **not** modified or restarted by this work.
Last updated: 2026-11-13 IST (final independent safety audit + regression checks)

## Decision: **GO for integration** (conditional)

The branch is safe to integrate, on these conditions:
1. Use the documented procedure (section G).
2. Run it in an idle window with no job QUEUED, RUNNING or RETRYING, well before the next production cycle (`cycle_4`, 17:30 IST).
3. The user gives explicit approval for the merge and for stopping the two old processes. The running daemon (PID 2832) predates the restart endpoint, so it cannot be restarted gracefully.

No regression against the equivalent baseline. Every new safety test passes. The live DB and main media were unchanged by all test runs. Every remaining failure is pre-existing or caused by the worktree environment (details in F).

## Status by phase

| # | Phase | Status |
|---|---|---|
| 1 | Resolve 3 known test failures | **Done** |
| 2 | Test database isolation | **Done** (media isolation partial, see Remaining) |
| 3 | Protect YouTube publishing | **Done**. Live policy is already `manual_approval` (changed by the user on the dashboard, 02:45:50 IST). |
| 4 | Bounded retries | **Done** |
| 5 | Completion boundary at queue handoff | **Done** (also fixed a crash that blocked every queue insert) |
| 6 | Remove publication coupling | **Done in backend, scheduler and validation**. The publication-slot API and UI are still present but have no effect. |
| 7 | Stale-process diagnostics | **Done** (takes effect after the next restart) |
| 8 | Testing | Final results in F |
| 9 | Git integration | Procedure in G; **not executed** |
| A–G | Final independent safety audit + regression checks | **Done**; 9 defects found and fixed (list below) |

## Defects found and fixed during the audit

| # | Defect | Impact before fix | Fix |
|---|---|---|---|
| 1 | Metadata edit (title, description, tags, thumbnail) sent `status.privacyStatus` computed from the **local** ledger | If the ledger lagged YouTube, an edit could have set a live video back to private or dropped its `publishAt` | Metadata-only edits send `part="snippet"` with **no `status`**, so YouTube keeps privacy and `publishAt` by construction |
| 2 | Failed remote update set ledger `upload_status='failed'` | An uploaded or public video became eligible for re-approval, and its ledger state was corrupted | Status, visibility and schedule are left untouched; only `error_details` and `last_error` are recorded. `approve_and_upload` also refuses already-public videos |
| 3 | Reschedule or cancel trusted the local ledger | A scheduled video already published by YouTube (`publishAt` passed) could have been unpublished by a reschedule | YouTube's live `privacyStatus` is checked first. If the video is public, or its state can't be verified, the action is refused and nothing changes |
| 4 | `edit_and_schedule` wrote the new time locally before YouTube accepted it | A failed reschedule left the ledger showing a time YouTube never got | For an uploaded video, the new `publishAt` is stored only after YouTube accepts it |
| 5 | `publish_now`, `cancel_schedule` and `edit_and_schedule` defaulted to `confirm=True` | A caller that omitted the flag got an implicitly confirmed action | Defaults changed to `False` (the API models already defaulted to `False`) |
| 6 | `POST /api/publishing/verify-scheduled` had no localhost guard; 4 config routes (production-config, storage-config, storage-config/validate, storage/open-folder) were unguarded | Remote or DNS-rebinding clients could trigger YouTube calls and ledger promotion, change output directories, or open Explorer | `Depends(require_local_control)` on all of them; a test now asserts that **no** state-changing route is unguarded |
| 7 | The dashboard Approve button sent `{confirmed:true}`, but the API expects `confirm` | Approval always failed (422), and a misspelt confirmation field was otherwise silently ignored | Dashboard fixed. `ConfirmedActionRequest` returns a clear 422 naming `"confirm": true` for `confirmed`, `confirmation`, `approve` or `approved` |
| 8 | Retention checked for duplicate hashes **before** checking for queued, handoff-pending or preserved videos | A protected video could be classified as a quarantinable duplicate when an identical file was scanned first | Protection check moved ahead of the duplicate rule. `preserved_artifacts` paths are referenced directly, and `quarantine_files` re-reads the DB just before moving anything |
| 9 | (earlier audit round) Metadata edit of a public video sent `private`; Stage 14 called a missing `_save_checkpoint` | Unpublishing; every production run crashed after QC | Fixed in `e8e7084` and the audit changes |

## A. Publish paths
`test_publishing_entry_points.py` inventories every `upload_short(`, `videos().insert(`, `allow_public_bypass=True`, `make_public_now=True` and `execute_upload=True` call site. Adding a new path fails the test until it has been reviewed.

Only two user actions can make a video public:
- **Publish Now**: confirmed, localhost only.
- **A user-picked publication time**: the video is uploaded private with `publishAt`.

The background queue never uploads (`execute_upload=False`), and stored `automatic` policy values are inert.

## B–E. Retry, lifecycle, decoupling and restart
Unchanged from the previous round. The tests are `test_recovery_safety_audit`, `test_production_lifecycle_order`, `test_scheduler_decoupling_audit` and `test_daemon_controlled_restart`.

- A restart request is rejected while any job is QUEUED, RUNNING or RETRYING.
- The daemon exits only at a safe point, and the monitor relaunches it.

## Regression checks requested 2026-11-13 (`tests/test_final_safety_regressions.py`, 80 tests, all mocked)

**1. Existing public-video preservation: all pass**
- Editing a public video's title, description, tags or thumbnail sends a snippet-only update with no `status`. The ledger stays `uploaded_public` / `public`.
- A failed metadata update (public or scheduled video) leaves the YouTube ID, status, visibility and `publishAt` unchanged. The video cannot then be re-approved.
- Rescheduling or cancelling an already-public video is rejected, and no `videos().update` call is made. This covers two further cases:
  - the ledger still says *scheduled* but YouTube reports *public*;
  - YouTube's state cannot be verified.
- A scheduled video keeps its `publishAt` through metadata edits and through a failed reschedule. Only an explicit, confirmed reschedule changes it, and only after YouTube accepts.

**2. Publishing-queue integrity: all pass**
- A QC-approved video is queued exactly once:
  - repeated queueing returns the same row;
  - a handoff retry with the same bytes returns the same row even after upload.
- Metadata edits never create rows.
- Retrying a thumbnail or a schedule never calls `videos().insert` again.
- A row with an existing YouTube ID never re-uploads. This was tested for 6 statuses × 4 actions (approve, schedule, publish now, edit).
- Publish Now and cancel require `confirm is True`. A missing flag, `False` or the string `"true"` are rejected.
- Scheduling requires a valid **future** timestamp. Empty, blank, malformed and past values are rejected, and the ledger is untouched.
- No background bypass is possible:
  - the engine refuses unapproved rows;
  - no application code passes `auto_policy_authorized=True`;
  - a stored legacy `automatic` value still resolves to `manual_approval`.

**3. API security: all pass**
- Every state-changing route in the app requires localhost: an inventory test over all `APIRoute`s found 0 unguarded.
- Every publication write route returns 403 for a remote client (`203.0.113.7`) and for a loopback client with a foreign `Host` header (DNS rebinding). No YouTube call is made and the ledger is unchanged.
- A missing or `false` confirmation returns 400, 403, 409 or 422 on all 6 confirmation endpoints:
  - publish-now, cancel-schedule, schedule;
  - approve, disconnect;
  - publishing policy.
- A misspelt confirmation field (`confirmed`, `confirmation` or `approved`) returns a 422 whose message names `"confirm": true`. This was checked on all 6 endpoints.
- Read-only endpoints work from localhost: queue list, item, publishing status, deployment.
- The authorized dashboard flow works end to end: edit → approve (private upload, once) → schedule → cancel → publish now → verify-scheduled.

**4. Retention safety: all pass**
The retention tests cover five kinds of protected file:
- a post-render failed video with preserved artifacts;
- a `HANDOFF_PENDING` video;
- a `RETRY_SCHEDULED` video whose path exists **only** in `preserved_artifacts`;
- a video referenced by a `pending_queue` ledger entry;
- a video with an existing YouTube ID (in a ledger row marked `failed`).

Each is classified protected, even when an identical copy was seen first. `monthly_audit(dry_run=False)` and `weekly_temp_quarantine(dry_run=False)` leave all five on disk while unprotected duplicates exist and the files are 60 days old. `quarantine_files` refuses even a stale "unprotected" classification for each of them.

## F. Test integrity

**Equivalent selections.**
- **Baseline** = `702c610` code plus the test-isolation infrastructure only (conftest, paths/db repointing, the two portable test files), in a detached worktree.
- **Branch** = this worktree.

Both are run from a worktree with the same conftest and the same skip rules.

| Run | Total | Passed | Failed | Skipped |
|---|---|---|---|---|
| Baseline | 430 | 250 | 7 | 173 |
| Branch (final) | 593 | 416 | 4 | 173 |

**Per-test comparison on the 426 common tests**

| Change | Count | Tests |
|---|---|---|
| Pass → pass | 246 | |
| Skip → skip | 173 | |
| Fail → fail | 4 | All pre-existing, see below |
| Fail → pass | 3 | `test_unified_schedule_editor` #02 and #21; `test_stage_3_23_4_publication_recovery::test_schedule_propagation…` |
| Pass → fail | **0** | An intermediate run showed `test_stage_3_23_4_compliance::test_secret_redaction…` getting 403, because the guarded publication-schedule GET was called with TestClient's non-local `testclient` client |

The `test_secret_redaction` test now calls the routes as the local dashboard does, and it also asserts that a non-local client gets 403. No assertion was removed.

**Tests that exist on only one side**
- **Baseline only (4)**: tests of behaviour the directive removed (2-hour publication buffer, short-gap and overlap slot validation, automatic-authorised cycle publishing). They were replaced by tests that assert the new contract.
- **Branch only (167)**: all new tests, all passing:

| Test file | Tests |
|---|---|
| `test_final_safety_regressions` | 80 |
| `test_recovery_safety_audit` | 19 |
| `test_retry_policy` | 16 |
| `test_publishing_entry_points` | 15 |
| `test_deployment_diagnostics` | 13 |
| `test_completion_boundary` | 5 |
| `test_daemon_controlled_restart` | 5 |
| `test_publishing_safety` | 5 |
| `test_production_lifecycle_order` | 3 |
| `test_scheduler_decoupling_audit` | 2 |
| Replacement contract tests in 4 legacy modules | 4 |

**Failing on both sides (not regressions)**
- `test_stage_3_8_integration::test_end_to_end_orchestration_run`: the test pipeline uses `CleanSlateRenderer`, and the Lemo-Opuscar-only renderer gate blocks it.
- `test_production_dashboard_browser::test_dashboard_html_page_served` and `test_youtube_branding::test_dashboard_html_contains_branding_elements`: the tests expect a title and favicon link that the committed `dashboard.html` (snapshot `21e6fd8`, identical to main's working tree) does not contain. They would fail on main too.
- `test_youtube_branding::test_source_youtube_icon_exists`: `media/` is gitignored, so the icon file isn't present in a worktree. It exists in main.

**Skips (173, identical on both sides)**

| Reason | Tests |
|---|---|
| Hardcode the main checkout path `D:\Agents\CrazzyDocs` | 152 |
| Live production DB audits | 17 |
| Pre-existing "Stage 3.4 artifact cleaned up" | 4 |

- **Main checkout path (152)**: these tests would read or write the production media tree from a worktree. They run in the main checkout itself.
- **Live production DB audits (17)**: all of `test_stage_3_21b` and `3_21c`, plus 2 tests in `3_17`. They assert live row counts, so they belong in a read-only snapshot script.

The earlier figure of 191 dropped to 173 after making `dashboard_browser` (8) and `youtube_branding` (5) portable and allowlisting the read-only `windows_startup_configuration` (5).

**Live DB fingerprint.**
- **Whole-DB content hash**: it changed between 02:13 and 03:03. The cause is the user's own policy change at 02:45:50, made from the live dashboard (`PUT /api/control-center/publishing/policy` from 127.0.0.1; change history shows `automatic → manual_approval`). No test makes real HTTP calls.
- **Per-table read-only fingerprint**: saved after that change (03:03), 19 tables. It was **unchanged** after the final branch run.
- **`studio.db` mtime**: still 02:45:50.

**Main media tree.** 327 files before and after, with 0 changes in path, size or mtime.

**Module origins.** Collection and session end abort if any `backend.*` **or `app.*`** module loads from another checkout. The `app.*` check was added in this round, because `app.main` imports `app.services.*`. Final run: no violations. The worktree has no `token.json` or `client_secrets.json`, so even an unmocked call could not reach YouTube.

## G. Integration readiness

**Commits on the branch** (on top of main `1baa37a`)

| Commit | Content |
|---|---|
| `21e6fd8` | Checkpoint snapshot of main's 157 uncommitted files (byte-identical to main's working tree) |
| `702c610` | Lifecycle events: DB path import fix |
| `e0affb4` | Test isolation from the production DB and checkout |
| `e8e7084` | User-controlled publishing, bounded retries, queue-handoff completion, stale-code diagnostics |
| *(audit commit)* | Audit fixes 1–8 above, `daemon_runtime` refactor, guarded routes, retention ordering, the 5 audit test modules and this report |

**Changed files.** Run `git diff --stat 21e6fd8..HEAD` for the full list. In brief:
- **Application code**: `main.py`, `control_center_service`, `youtube_service`, `youtube_publishing_engine`, the orchestrator, `scheduler`, `reconciliation_service`, `media_lifecycle_manager`, `lifecycle_events` and `paths`/`db` repointing (about 10 modules).
- **New modules**: `retry_policy`, `daemon_runtime`, `code_revision`, `deployment_diagnostics`.
- **Other**: `dashboard.html`, `run_scheduler_daemon.py`, `startup_manager.py`, plus tests.

Relative to main HEAD `1baa37a`, the diff is that same content plus `21e6fd8` (main's own uncommitted work).

**Database migrations.** None destructive, and no schema changes to existing tables.
- **New table**: `production_lifecycle_events`, created lazily on the first event.
- **New settings keys**: `control_center.daemon.deployment` and `control_center.daemon.restart_requested`.
- **Unchanged**: legacy `PUB_SCHEDULE_*`, `target_publish_time` and the stored policy value, all kept verbatim. No historical rows are touched.

**Configuration.** None required in production. `CRAZZYDOCS_DATABASE_PATH` and `CRAZZYDOCS_TOOLS_DIR` are test and worktree overrides only; when unset, the defaults are the existing paths.

**Risks**

| Risk | Likelihood | Mitigation |
|---|---|---|
| Old daemon (PID 2832) cannot use the restart endpoint, so it has to be killed | Certain, one-time | Only when idle: verified read-only that no job is QUEUED, RUNNING or RETRYING, and well before 17:30 IST |
| Monitor (PID 1668) relaunch conditions | Low | Requires PRODUCTION mode and the Windows task enabled. Verify the new PID within about 30 s, or start it manually |
| `app.*` and `backend.app.*` loaded as two module copies in the dashboard | Pre-existing | Both copies use the same DB file; no shared in-process state is relied on |
| Publication-slot UI/APIs still present | Low | They have no effect on production or publishing and are local-only |
| Media modules still hardcode `D:\Agents\CrazzyDocs` | None in production | Affects worktree tests only |
| Per-stage resume not implemented (retries re-run TTS and media acquisition) | Medium cost, no safety impact | Retries are bounded (3) |

**Merge procedure** (requires approval; not executed)
1. Re-run the comparison script and confirm main's dirty files are still byte-identical to `21e6fd8`. If any differ, stop and re-snapshot.
2. Back up the DB using SQLite's online backup API, not a file copy (the daemon holds it open), for example `sqlite3.connect('…/studio.db').backup(sqlite3.connect('studio.pre-merge.db'))`.
3. In `D:\Agents\CrazzyDocs`, run `git checkout -- .` to revert the 51 modified tracked files. Nothing is lost, because the same content is committed in `21e6fd8`.
4. Move aside the 106 untracked files that `21e6fd8` adds (`git diff --name-only --diff-filter=A 1baa37a 21e6fd8`), for example into a dated backup folder. Otherwise `--ff-only` refuses to overwrite them. Main has 111 untracked files: these 106, plus 5 that are not in the branch and must stay untouched (3 under `backend/backups/pre_stage_3_21m_20261111_142016/`, 1 under `backups/`, 1 under `media/thumbnail/`). Do **not** run `git clean`.
5. Run `git merge --ff-only refs/heads/claude/crazzy-docs-verify-capabilities-ac634e`.
6. Confirm that `git status` lists only those 5 untracked files. Then compare the moved-aside files with their tracked versions; they should be identical.

**Controlled restart** (requires approval)
1. Confirm read-only that no job is `QUEUED`, `RUNNING` or `RETRYING`, and that it is not within an hour of 17:30 IST.
2. Stop the old daemon: `taskkill /PID 2832 /F`. This is safe only while it is idle. The new code has a graceful path for every later restart.
3. Wait at most 30 s for monitor 1668 to relaunch it, and confirm the new PID.
4. Restart the dashboard (PID 11476) the same way.
5. Check `GET /api/control-center/deployment`: daemon and dashboard should be `CURRENT`, with the loaded revision equal to the on-disk revision.

**Rollback**
1. Run `git reset --hard 1baa37a` in main.
2. Restore main's previous uncommitted state with `git checkout 21e6fd8 -- .` followed by `git reset --mixed 1baa37a`.
3. If needed, restore `studio.pre-merge.db`; the only new rows are lifecycle events and the two settings keys.
4. Restart both processes as above.

## Phase notes (from earlier rounds)
- **Isolation**: `paths.DATABASE_PATH` honours `CRAZZYDOCS_DATABASE_PATH`. The conftest:
  - creates a throwaway DB;
  - guards `sqlite3.connect` against any production `studio.db`;
  - pins `backend.__path__`;
  - skips live-DB and main-path tests in worktrees.

  Destructive cleanup was removed from `3_10` and `3_13a`. Earlier runs, before the isolation work, did leave `test_auto_job_001` and `test_fail_job_002` in live `persistent_jobs`. They also overwrote two obsolete test renders in main media.
- **Retries**:
  - each failed attempt increments `retry_count` once;
  - PERMANENT errors are terminal at once;
  - TRANSIENT errors back off 2, 4, 8, 16, 30 min (capped), with `max_retries` 3;
  - a failure after render is terminal and keeps its preserved video;
  - the reconciler no longer resurrects `FAILED` jobs.
- **Completion boundary**: `VIDEO_GENERATED → QC_PASSED → PUBLISHING_QUEUE_ENTRY_CREATED → PRODUCTION_CYCLE_COMPLETED`.
  - The insert is idempotent by video hash.
  - A handoff failure becomes `HANDOFF_PENDING`, and only the handoff is retried.
- **Decoupling**: production validation and scheduling no longer depend on publication slots or times. Legacy values are preserved read-only.
- **Diagnostics**: a source revision fingerprint, a `STALE CODE` banner, and a confirmed restart that is rejected while any job is in progress.

## Remaining issues / next steps
1. **Approval:** integrate into `main` and do the controlled restart (section G).
2. Remove the publication-slot subsystem and its dashboard UI. It has no effect today; check the removal in the browser.
3. Media isolation: about 15 app modules and 20 tests still hardcode `D:\Agents\CrazzyDocs`.
4. Make the live pipeline resumable per stage.
5. Turn the live-DB audit tests into a read-only snapshot audit script.
6. Fix `test_stage_3_8_integration` for the Lemo-Opuscar-only renderer policy. Update the dashboard title and favicon tests, or the template, so they agree.
7. Consolidate the `app.*` / `backend.app.*` double import in `main.py`.
