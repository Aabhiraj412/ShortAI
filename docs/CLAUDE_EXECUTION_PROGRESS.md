# Claude Execution Progress

| Phase | Status | Notes |
|---|---|---|
| 0 Takeover & safety | Done (partial) | Shell access confirmed 2026-11-13. Checkpoint commit `21e6fd8` on branch `claude/crazzy-docs-verify-capabilities-ac634e` snapshots main's uncommitted tree (excl. media/, backups/, backend/backups/). Publishing policy decision still pending. |
| 1 Audit production system | Done | See `CLAUDE_PROJECT_AUDIT.md` §5. |
| 2 Dispatch / worker fix | In progress | Fixed import crash in `lifecycle_events.py` (`settings.DATABASE_URL` did not exist → `scheduler.py`/`main.py` failed to import on disk). Lifecycle events no longer write to the production DB in TEST context. Live daemon/dashboard NOT restarted; they still run pre-18:18 code. |
| 3 Remove coupling | Not started | |
| 4 Publishing queue | Not started | Publishing policy currently `automatic` — user decision pending. |
| 5 Completion boundary | Not started | |
| 6 Dashboard | Not started | |
| 7 Diagnostics | Not started | |
| 8 Tests | In progress | pytest works. 430 tests collect, 0 errors (was 20). Subset run: 54 passed, 3 failed (see below). Full suite not yet run. |
| 9 Live verification | Not started | |

## Known test failures (previously masked by the import error)
- `test_unified_schedule_editor.py::test_02_one_cycle_custom_schedule` — `'unmapped' == 'inherited'`
- `test_unified_schedule_editor.py::test_21_unified_draft_validate_missing_and_duplicate_mapping` — `True is False`
- `test_user_controlled_publishing.py::test_user_controlled_schedule_reschedule_cancel_and_publish_now` — **not DB-isolated**: uses the project `backend/studio.db`. Run from the main checkout it would read/write the LIVE database. Do not run it there until isolated.

## Environment hazards
- `.venv\Lib\site-packages\crazzydocs.pth` puts `D:\Agents\CrazzyDocs` on `sys.path`, so `import backend` resolves to the main checkout from any directory. When testing a worktree, set `PYTHONPATH=<worktree root>` first, or tests run main's code against the live `studio.db`.
- Dashboard runs without `--reload`; on-disk edits do not affect live processes until restart.

Last updated: 2026-11-13 IST
