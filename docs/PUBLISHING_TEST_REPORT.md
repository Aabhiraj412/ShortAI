# Publishing Test Report

## 1. Automated Test Execution

Command run after all code edits:

```powershell
$env:PYTHONPATH = "D:\Agents\CrazzyDocs;D:\Agents\CrazzyDocs\backend"
D:\Agents\CrazzyDocs\.venv\Scripts\pytest.exe `
  D:\Agents\CrazzyDocs\backend\tests\test_user_controlled_publishing.py `
  D:\Agents\CrazzyDocs\backend\tests\test_stage_3_23_control_center.py `
  D:\Agents\CrazzyDocs\backend\tests\test_unified_schedule_editor.py `
  D:\Agents\CrazzyDocs\backend\tests\test_dynamic_production_schedule.py `
  D:\Agents\CrazzyDocs\backend\tests\test_stage_3_22_integrations.py `
  D:\Agents\CrazzyDocs\backend\tests\test_stage_3_9cd_scheduler.py -q
```

**Result:** `62 passed, 2 warnings in 37.84s` (0 failed, 0 skipped).

## 2. Verified Scenarios (`test_user_controlled_publishing.py`)

- IST-to-UTC conversion (`Asia/Kolkata` -> UTC `...Z`) and rejection of past timestamps.
- Production scheduler registers `gen_{cycle_id}` and `youtube_status_reconciliation_job`, with zero `pub_{cycle_id}` cron jobs.
- Cross-process DB revision reload (`reload_profile_from_db_if_changed()`) reloads APScheduler jobs when SQLite `ACTIVE_PROFILE_KEY` changes.
- Mocked YouTube API end-to-end flow: `EDIT & SCHEDULE` -> `Reschedule` (via `videos().update` without duplicate `insert`) -> `CANCEL SCHEDULE` -> `PUBLISH NOW`.
- Thumbnail upload failure preserves `youtube_video_id` immediately so retrying does not create duplicate YouTube videos.
- FastAPI Publishing Queue endpoints (`/api/publishing/queue`, `/api/publishing/queue/{id}/schedule`) return expected HTTP 200 / 400 responses.
