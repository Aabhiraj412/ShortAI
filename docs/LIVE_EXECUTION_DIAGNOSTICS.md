# Live Execution Diagnostics Report

**Date:** 2026-11-12  
**Workspace:** `D:\Agents\CrazzyDocs`

## Current System State

Live job states queried from the database at session time (most recent 10, descending):

```
cycle_4_20261112_1730: RUNNING | VOICEOVER | name 'Path' is not defined
cycle_4_20261112_1800: FAILED | STORY_DEVELOPMENT | [WinError 32] The process cannot access the file because it
cycle_3_20261112_1200: COMPLETED | COMPLETED | QC Failure: Production video duration is too short (43.14s <
cycle_2_20261112_0600: RECOVERY_WINDOW_EXPIRED | RECOVERY_WINDOW_EXPIRED | Recovery window expired at 2026-11-12T10:00:00+05:30; reconc
cycle_1_20261112_0000: RECOVERY_WINDOW_EXPIRED | RECOVERY_WINDOW_EXPIRED | Recovery window expired at 2026-11-12T04:00:00+05:30; reconc
cycle_1_20261111_0000: ABORTED_PRE_RESET | TOPIC_DISCOVERY | Cleanly aborted before Stage 3.21B publishing ledger reset;
test_fail_job_002: FAILED | TOPIC_DISCOVERY | Simulated upstream API outage
test_auto_job_001: COMPLETED | COMPLETED | None
test_vesna_integrity_run: COMPLETED | COMPLETED | [Errno 2] No such file or directory: 'D:\Agents\CrazzyDocs
```

## Analysis

| Cycle | Status | Note |
|-------|--------|------|
| cycle_4 (17:30) | RUNNING/VOICEOVER | Stalled — `NameError: name 'Path' is not defined` at line 819 of `crazzydocs_master_orchestrator.py`. Fixed by adding `from pathlib import Path`. |
| cycle_4 (18:00) | FAILED/STORY_DEVELOPMENT | File lock error `[WinError 32]` — file in use by another process during story dev stage. |
| cycle_3 (12:00) | COMPLETED | QC failed (video too short: 43.14 s). Video did not enter Publishing Queue. |
| cycle_2 (06:00) | RECOVERY_WINDOW_EXPIRED | Recovery window closed at 10:00 IST. |
| cycle_1 (00:00) | RECOVERY_WINDOW_EXPIRED | Recovery window closed at 04:00 IST. |
| cycle_1 (11-11) | ABORTED_PRE_RESET | Cleanly aborted before Stage 3.21B reset. |

## Query Used

```powershell
cd D:\Agents\CrazzyDocs
$env:PYTHONPATH = "D:\Agents\CrazzyDocs;D:\Agents\CrazzyDocs\backend"
.\.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0,'backend')
from app.database.db import SessionLocal, JobModel
db = SessionLocal()
jobs = db.query(JobModel).order_by(JobModel.created_at.desc()).limit(10).all()
for j in jobs:
    print(f'{j.id}: {j.status} | {j.current_stage} | {j.error_details[:60] if j.error_details else None}')
db.close()
"
```
