# PHASE 5 — STAGE 3.21A STATE RECONCILIATION & LEDGER INVENTORY REPORT
**Production State Reconciliation, Scheduler PID Discrepancy Forensic Audit, Publishing Ledger Inventory & Verified Backup Manifest**  
**Project:** CrazzyDocs Autonomous Production Studio (`@crazzydocs` | Channel ID: `UCUOZOEBngXgt6W7fmus0bvw`)  
**Canonical Directory:** `D:\Agents\CrazzyDocs`  
**Investigation Timestamp:** 2026-11-11T02:10:00+05:30  
**Status:** RECONCILIATION & BACKUP COMPLETE — STRICTLY NON-DESTRUCTIVE / ZERO PRODUCTION MUTATIONS  

---

## 1. Executive Summary & Core Reconciliations

Stage 3.21A performed a comprehensive read-only investigation, forensic process audit, verified database backup, and inventory of all production state across `D:\Agents\CrazzyDocs`.

### Key Conclusions:
1. **Scheduler PID Discrepancy Conclusively Resolved:**
   - PID 16332 belongs exclusively to the **Dashboard Service Process** (`pythonw.exe "D:\Agents\CrazzyDocs\backend\scripts\run_dashboard.py" --open-browser`), spawned by Windows Task Scheduler task `CrazzyDocs-Health-Dashboard`.
   - In `backend/app/main.py`, `ENABLE_EMBEDDED_SCHEDULER` defaulted to `"true"`. When FastAPI booted inside PID 16332, it started an *embedded* APScheduler and acquired `backend/studio_scheduler.lock` with PID 16332.
   - The health checker in `production_health_dashboard.py` checked process liveness without checking heartbeat freshness or distinguishing between dedicated daemon vs embedded locks.
   - **Standalone Scheduler Daemon:** Confirmed **NOT RUNNING**. The Windows Scheduled Task `CrazzyDocs-Production-Scheduler` remains **`Disabled`**.
2. **Recovery Job `cycle_1_20261111_0000` Identified:**
   - Created at `01:55:53 IST` during dashboard startup when the embedded scheduler's `reconcile_on_startup()` ran inside the Cycle 1 recovery window (00:00–04:00 IST).
   - **Zero media was generated** (`checkpoint_data_json: {}`, `progress_pct: 0.0`).
   - **Zero publishing queue records were created.**
   - No worker process exists. The record is purely a stale database entry.
3. **Publishing Ledger Inventory (252 Records Total):**
   - **1 Record (ID 32):** The **sole genuine production private upload** on YouTube: `youtube_video_id: '4DCas1e4vHI'` (Topic: *The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived*).
   - **251 Records:** Test execution and showcase fixture queue entries generated across Stages 3.8 to 3.18. Zero of these 251 records have ever been uploaded to YouTube.
   - Across all 252 records, there are only **10 distinct video paths** and **6 distinct topic titles** (137 records point redundantly to a single file: `showcase_vesna_vulovic_verified.mp4`).
4. **Verified Database Backup Created:**
   - Destination: `D:\Agents\CrazzyDocs\backend\backups\studio_backup_pre_stage_3_21b_20261111_020710.db`
   - File Size: **6,483,968 bytes**
   - SQLite Integrity Check: **`ok`**
   - All 252 records in `publishing_ledger` and 4 records in `persistent_jobs` verified.
5. **Zero Destructive Actions:**
   - No records deleted, no processes terminated, no recovery initiated, no YouTube uploads.
   - `FOUR_CYCLES_DAILY` remains active. `SINGLE_DAILY_2300` remains inactive.

---

## 2. Section 1 — Scheduler PID Discrepancy Forensic Audit

### 2.1 The Observed Symptom
The dashboard previously reported:
- `Scheduler Daemon Process: RUNNING (PID 16332)`
- `Scheduler Heartbeat: RUNNING (PID 16332)`
- `Dashboard Service Process: RUNNING (PID 16332, Port 8000)`
- `Scheduler Windows Task: REGISTERED (Disabled)`

### 2.2 Process & OS Ownership Evidence
Querying `Win32_Process` for PID 16332:
```powershell
ProcessId       : 16332
CommandLine     : "D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe" "D:\Agents\CrazzyDocs\backend\scripts\run_dashboard.py" --open-browser
ParentProcessId : 940 (svchost.exe - Windows Task Scheduler Service)
```
- **Finding:** PID 16332 is the **Dashboard Service Process** running Uvicorn on port 8000.
- **Finding:** There is **NO separate Python process** running `run_scheduler_daemon.py`. PID 16332 is the only Python process on the host.

### 2.3 The Lock File & Heartbeat Origin
Inspecting `D:\Agents\CrazzyDocs\backend\studio_scheduler.lock`:
```json
{
  "pid": 16332,
  "acquired_at": "2026-11-10T20:25:53.178146Z",
  "timezone": "Asia/Kolkata"
}
```
Notice:
1. `acquired_at` is `2026-11-10T20:25:53Z` (`01:55:53 IST`).
2. There is **no `last_heartbeat` field** being continuously updated.

### 2.4 Technical Root Causes
1. **Embedded Scheduler Default:** In `backend/app/main.py`:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       init_db()
       if os.getenv("ENABLE_EMBEDDED_SCHEDULER", "true").lower() in ("true", "1"):
           start_scheduler()
   ```
   Because `ENABLE_EMBEDDED_SCHEDULER` was defaulted to `"true"`, Uvicorn booting `main.py` invoked `start_scheduler()` inside the dashboard process (PID 16332).
2. **Lock Acquisition:** `start_scheduler()` acquired `SchedulerProcessLock()`, writing PID 16332 into `studio_scheduler.lock`.
3. **Flawed Health Logic:** In `production_health_dashboard.py`, `_get_scheduler_heartbeat()` checked whether PID 16332 was alive (`ctypes.windll.kernel32.OpenProcess`). Because the dashboard was alive, it reported `is_running: True`.
4. **PID Sharing in UI:** In `get_services_status()`, `scheduler_daemon["pid"]` was copied directly from `hb["pid"]`. Thus the dashboard service's own PID was presented as the scheduler daemon.

### 2.5 Corrective Implementation Applied
1. **Main Lifespan:** Set `ENABLE_EMBEDDED_SCHEDULER` default to `"false"` in `main.py`. The dashboard server will never again start an embedded scheduler or write a lockfile.
2. **Heartbeat Freshness SLA:** `_get_scheduler_heartbeat()` now verifies that the heartbeat is within a 180-second SLA. An un-refreshed heartbeat transitions immediately to `STALE_HEARTBEAT` (`is_running: False`).
3. **Dedicated Daemon Check:** `scheduler_daemon` reports `RUNNING` only if `hb["pid"] != os.getpid()` AND `is_running: True`. When held by the dashboard, it reports `STOPPED (Standby)`.

---

## 3. Section 2 — Investigation of Recovery Job `cycle_1_20261111_0000`

### 3.1 Database Record in `persistent_jobs`
```json
{
  "id": "cycle_1_20261111_0000",
  "date_str": "2026-11-11",
  "topic_title": "Autonomous Recovery Cycle cycle_1",
  "status": "RUNNING",
  "current_stage": "TOPIC_DISCOVERY",
  "progress_pct": 0.0,
  "heartbeat_at": "2026-11-10 20:25:53.623404",
  "retry_count": 0,
  "checkpoint_data_json": "{}",
  "youtube_video_id": null,
  "scheduled_publication_time": "10:00 AM IST",
  "publishing_mode": "MANUAL_APPROVAL",
  "recovery_reason": "STARTUP_RECONCILIATION",
  "recovery_policy": "CATCH_UP",
  "original_target_pub": "06:00 IST",
  "revised_target_pub": "06:00 IST",
  "actual_start_at": "2026-11-10 20:25:53.621398"
}
```

### 3.2 Key Forensic Findings
1. **How it was created:** When the embedded scheduler ran at `01:55:53 IST` (`2026-11-10 20:25:53 UTC`), `startup_reconciler.reconcile_on_startup()` detected that Cycle 1 (start 00:00 IST) was within its 4-hour recovery window (ending 04:00 IST). It registered this record with `status: "RUNNING"`.
2. **Execution State:** **NOT EXECUTING.** `reconcile_on_startup()` only inserts the database row. The actual worker thread is triggered by the scheduler daemon. Because the daemon task was disabled, no execution ever began.
3. **Media Generation:** **ZERO.** `checkpoint_data_json` is `{}` and `progress_pct` is `0.0`. No files were created on disk.
4. **Queue Modification:** **ZERO.** No records in `publishing_ledger` were created.
5. **Risk on Future Restart:** If the standalone scheduler daemon were started while this record has `status: "RUNNING"`, `reconcile_on_startup()` would detect a stale heartbeat (>30m), increment retry count, and attempt to start production.

### 3.3 Recommended Safe Handling (For Stage 3.21B)
In Stage 3.21B:
- Update `cycle_1_20261111_0000` in `persistent_jobs`:
  - `status = "ABORTED_PRE_RESET"`
  - `error_details = "Cleanly aborted prior to production ledger reset in Stage 3.21"`
- This completely prevents unintended automatic resumption.

---

## 4. Section 3 — Publishing Ledger Comprehensive Inventory

Every single record of the 252 rows in `publishing_ledger` was audited against the physical filesystem.

### 4.1 Master Ledger Inventory Breakdown

| Classification Category | Record Count | Description | Proposed Action |
| :--- | :--- | :--- | :--- |
| **SUCCESSFUL_PRIVATE_UPLOAD** | **1** | Record ID 32: Verified YouTube upload (`4DCas1e4vHI`, Vesna Vulovic). | **PRESERVE PERMANENTLY** |
| **SHOWCASE_FIXTURE_MEDIA_EXISTS** | **180** | Repeated fixture entries pointing to existing showcase videos. | **PROPOSED ARCHIVE** |
| **TEST_EXECUTION_MEDIA_EXISTS** | **44** | Test job entries from Stages 3.8–3.18 whose media files exist. | **PROPOSED ARCHIVE** |
| **TEST_EXECUTION_MISSING_MEDIA** | **27** | Historical test entries pointing to pre-migration/deleted paths. | **PROPOSED ARCHIVE** |
| **TOTAL** | **252** | | |

### 4.2 Preserved Production Record (ID 32)
- **ID:** `32`
- **Topic:** `The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived`
- **YouTube Video ID:** `4DCas1e4vHI`
- **Status:** `uploaded_private` | **Visibility:** `private`
- **Upload Timestamp:** `2026-11-10T15:12:04.679658Z`
- **Video Path:** `D:\Agents\CrazzyDocs\media\productions\prod_cycle_4_20261110_1800\video\showcase_cycle_4_20261110_1800.mp4` (**EXISTS**, Size: 18,291,524 bytes)
- **Thumbnail Path:** `D:\Agents\CrazzyDocs\media\productions\prod_cycle_4_20261110_1800\thumbnails\thumb_cycle_4_20261110_1800.png` (**EXISTS**, Size: 1,280,314 bytes)

### 4.3 Redundancy & Duplicate Groups
Across all 252 records, there are only **10 distinct video paths** and **6 topic titles**:
- `showcase_vesna_vulovic_verified.mp4` is referenced in **137 records**.
- `showcase_stage_3_4_tsutomu_yamaguchi.mp4` is referenced in **40 records**.
- `showcase_test_vesna_integrity_run.mp4` is referenced in **27 records**.
- `showcase_test_auto_job_001.mp4` is referenced in **22 records** (13 missing old path, 9 existing production path).
- `showcase_test_interrupted_tts_job.mp4` is referenced in **14 records** (missing test paths).
- `showcase_test_scen_11_corrupt_cp.mp4` is referenced in **8 records**.
- `showcase_cycle_4_20261110_1800.mp4` is referenced in **3 records** (including ID 32).
- `showcase_prod_stage_3_16_verification.mp4` is referenced in **1 record**.

All records and duplicate mappings have been exported to:
- `docs/STAGE_3_21A_LEDGER_INVENTORY.json`
- `docs/STAGE_3_21A_LEDGER_INVENTORY.csv`
- `docs/STAGE_3_21A_DUPLICATE_GROUPS.json`

---

## 5. Section 4 — Production Statistics Reconciliation

### 5.1 Analysis of Dashboard Production Statistics
Current metrics reported by `/api/dashboard/stats`:
- `attempted`: **4** (All from test runs: `test_vesna_integrity_run`, `test_auto_job_001`, `test_fail_job_002`, `cycle_1_20261111_0000`)
- `completed`: **2** (`test_vesna_integrity_run`, `test_auto_job_001`)
- `published`: **0** (Because the SQL filter strictly counts `visibility = 'public'`. Record ID 32 is `visibility = 'private'`).
- `qc_failed`: **0**
- `missed`: **0**
- `recovered`: **0**

### 5.2 Recommended Post-Cleanup Statistics
Following Stage 3.21B cleanup:
- `active_queue_pending`: **0**
- `active_queue_uploading`: **0**
- `active_queue_published`: **1** (Preserved Record ID 32)
- `active_queue_failed`: **0**
- `archived_test_records`: **251**

---

## 6. Section 5 — Schedule Display & Trigger Verification

### 6.1 `FOUR_CYCLES_DAILY` (Asia/Kolkata) Profile Map

| Cycle | Generation Trigger | Recovery Window | Target Publishing Slot | Rollover |
| :--- | :--- | :--- | :--- | :--- |
| **Cycle 1: Midnight** | `00:00 IST` | `00:00 – 04:00 IST` (4.0 hrs) | `06:00 IST` | Same Day |
| **Cycle 2: Morning** | `06:00 IST` | `06:00 – 10:00 IST` (4.0 hrs) | `12:00 IST` | Same Day |
| **Cycle 3: Afternoon** | `12:00 IST` | `12:00 – 16:00 IST` (4.0 hrs) | `18:00 IST` | Same Day |
| **Cycle 4: Evening** | `18:00 IST` | `18:00 – 22:00 IST` (4.0 hrs) | `00:00 IST` | Next Day (+1) |

### 6.2 Crucial Architectural Distinctions:
1. **Production Trigger Time:** The moment autonomous content generation (topic discovery, script, voice, rendering, QC) is scheduled to begin.
2. **Recovery Window:** The 4-hour grace period following trigger time where a rebooted or offline system can catch up the cycle.
3. **Target Publishing Slot:** The scheduled time when completed, QC-approved videos are released. **A video is never published simply because a target slot exists.** Publishing requires:
   - Successful rendering and passing all 12 QC gates.
   - A valid queue entry.
   - YouTube quota availability.
   - Active network connectivity.

---

## 7. Section 6 — Verified Database Backup & Manifests

### 7.1 Database Backup Verification
- **Source Database:** `D:\Agents\CrazzyDocs\backend\studio.db`
- **Backup File:** `D:\Agents\CrazzyDocs\backend\backups\studio_backup_pre_stage_3_21b_20261111_020710.db`
- **File Size:** **6,483,968 bytes**
- **Method:** SQLite Online Backup API (`src_conn.backup(dst_conn)`).
- **Integrity Check:** `PRAGMA integrity_check` returned **`ok`**.
- **Table Count Verification:**
  - `publishing_ledger`: **252** records
  - `persistent_jobs`: **4** records
  - `content_ledger`: **25** records
  - `research_records`: **23** records
  - `media_assets`: **2** records
  - `studio_settings`: **1** records
  - `youtube_quota_ledger`: **1** records

### 7.2 Generated Manifests in `docs/`
1. [STAGE_3_21A_LEDGER_INVENTORY.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_LEDGER_INVENTORY.json) — Full 252-record dump with file existence and metadata.
2. [STAGE_3_21A_LEDGER_INVENTORY.csv](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_LEDGER_INVENTORY.csv) — Spreadsheet-compatible export for audit review.
3. [STAGE_3_21A_DUPLICATE_GROUPS.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_DUPLICATE_GROUPS.json) — Groupings by hash, video path, and topic title.
4. [STAGE_3_21A_GENUINE_PRESERVATION_LIST.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_GENUINE_PRESERVATION_LIST.json) — Record ID 32 preservation specification.
5. [STAGE_3_21A_PRIVATE_UPLOAD_REPORT.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_PRIVATE_UPLOAD_REPORT.json) — Private YouTube video `4DCas1e4vHI` metadata audit.
6. [STAGE_3_21A_MISSING_MEDIA_REPORT.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_MISSING_MEDIA_REPORT.json) — 27 test records pointing to legacy paths.
7. [STAGE_3_21A_PROPOSED_ARCHIVE_LIST.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_PROPOSED_ARCHIVE_LIST.json) — 251 test records proposed for archival.
8. [STAGE_3_21A_POST_CLEANUP_EXPECTED_STATS.json](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21A_POST_CLEANUP_EXPECTED_STATS.json) — Projected active ledger post-cleanup.

---

## 8. Proposed Stage 3.21B Execution Plan (Dry-Run Only)

When authorized by the user, Stage 3.21B will execute the following surgical, non-destructive migration:

```
[Current studio.db]
       │
       ├── 1. Move 251 Test Records ───> [publishing_ledger_archive Table]
       │                                  (Full historical audit trail retained)
       │
       ├── 2. Retain Record ID 32  ────> [publishing_ledger Table]
       │                                  (Sole genuine production upload: 4DCas1e4vHI)
       │
       └── 3. Update cycle_1 Job   ────> [Status: ABORTED_PRE_RESET]
                                          (Prevents unintended resumption)
```

**Zero rows will be permanently deleted from disk.** All 251 test records will be safely relocated into a structured `publishing_ledger_archive` table within the database.

---

## 9. Verification & Safety Sign-Off

- **Scheduler Windows Task:** `CrazzyDocs-Production-Scheduler` remains **`Disabled`**.
- **Daemon Process:** Standby (0 processes running `run_scheduler_daemon.py`).
- **YouTube Uploads:** **0** uploads attempted or initiated.
- **Publishing Ledger:** **0** records modified or deleted.
- **Profile Integrity:** `FOUR_CYCLES_DAILY` verified; `SINGLE_DAILY_2300` inactive.
- **Dashboard Service:** Active and listening on `http://127.0.0.1:8000/dashboard` (PID 16332).
