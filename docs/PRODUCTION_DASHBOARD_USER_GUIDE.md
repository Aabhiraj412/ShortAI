# CrazzyDocs Shorts Studio — Production Health Dashboard User Guide

**Project:** CrazzyDocs Autonomous YouTube Shorts Studio  
**Canonical Directory:** `D:\Agents\CrazzyDocs\`  
**Dashboard Access URL:** `http://127.0.0.1:8000/dashboard`  
**Host Binding:** Localhost Only (`127.0.0.1`) — Zero Public Exposure  

---

## 1. Quick Start

To launch the Production Health Dashboard without interfering with any running scheduler:

```powershell
cd D:\Agents\CrazzyDocs
.\.venv\Scripts\python.exe backend\scripts\run_dashboard.py
```

### Accessing the Interface
Open your web browser and navigate to:
```text
http://127.0.0.1:8000/dashboard
```

To stop the dashboard:
Press `Ctrl+C` in the terminal running `run_dashboard.py`.  
*Stopping the dashboard server or closing the browser window never stops or interrupts the background production scheduler.*

---

## 2. Dashboard Sections & Interpretation

### A. Top Overview Cards
- **Current Production Cycle:** Displays the active cycle window (e.g., `Cycle 1: Midnight Production -> Morning 06:00 IST Target (00:00–06:00 IST)`).
- **Next Production Run:** Exact timestamp in Asia/Kolkata (IST) when the next automated generation job will trigger.
- **Target Publishing Slot:** Exact timestamp in Asia/Kolkata (IST) when the publishing queue job will release the approved Short to YouTube.
- **Pipeline Stage:** Current real-time state of the video generation pipeline (`IDLE`, `TOPIC_DISCOVERY`, `SCRIPTWRITING`, `VOICEOVER`, `RENDERING`, `QUALITY_CONTROL`, `PUBLISHING`).

### B. 10-Stage Pipeline Lifecycle Visualizer
Displays the sequential stages of the autonomous production studio:
```text
[01 Topic Research] -> [02 Script Generation] -> [03 Narration] -> [04 Visual Assets]
-> [05 Thumbnail] -> [06 Rendering] -> [07 Quality Control] -> [08 Publishing Queue]
-> [09 YouTube Publishing] -> [10 Completed]
```
- **Active Stage:** Highlighted in glowing yellow with animated pulse.
- **Completed Stages:** Rendered in emerald green.
- **Failed Stages:** Rendered in crimson with error details.

### C. Production Performance Statistics
Provides operational metrics across selectable time intervals:
- **Tabs:** `[All Time]`, `[Last 30 Days]`, `[Last 7 Days]`, `[Today]`.
- **Productions Attempted:** Total jobs initiated.
- **Completed Productions:** Deliverables that successfully cleared all rendering and QC checks.
- **Publicly Published:** Verified YouTube Shorts uploads.
- **QC Blocked / Failed:** Deliverables caught by Quality Gate or Content Integrity Gate.
- **Missed Cycles:** Slots that expired due to extended machine shutdown.
- **Recovered Cycles:** Checkpointed productions successfully resumed and completed.

### D. System Runtime & Subsystem Health Grid
- **Scheduler Heartbeat:** Displays process state (`RUNNING`, `IDLE`, or `STALE_LOCK`) and active PID.
- **Windows Task Scheduler:** Status of logon automation (`OPTED-OUT / NOT REGISTERED` or `REGISTERED`).
- **Database Health:** SQLite WAL mode integrity check status and table count.
- **Network & YouTube API:** Internet and Google API reachability (`CONNECTED`, `DEGRADED`, or `DISCONNECTED`).
- **Crash Recovery Guard:** Number of active checkpoints and recovery state.
- **Disk Storage Free:** Available disk space on drive `D:` and current size of the `media/` folder.
- **Daily Email Summary Service:** Delivery schedule (`06:00 IST`) and pending alert count.
- **Publishing Ledger Safety:** Total ledger records and idempotency status.

### E. Publishing History Table
Shows the most recent 25 videos recorded in the publishing ledger:
- Production ID, Topic Title, Status (`pending_queue`, `uploaded_public`, `uploaded_private`), Visibility (`public` vs `private`), YouTube Video ID, and Timestamp.
- *Sensitive OAuth tokens, refresh tokens, and client secrets are strictly redacted and never displayed.*

### F. Recovery & Checkpoint Resumption History
Displays crash recovery incidents, retry counts, recovered stages, and whether operator attention is required.

### G. Recent Operational Activity Feed
Chronological audit log of pipeline status transitions and publishing events formatted in Asia/Kolkata (IST).

---

## 3. System Health Status Meanings

| Health Status | Badge | Description & Recommended Action |
|---|---|---|
| **HEALTHY** | Emerald Badge | All systems nominal. Network connected, database integrity verified, lockfile clean, zero QC failures. |
| **DEGRADED** | Amber Badge | Non-fatal anomaly detected: One or more QC jobs blocked, email delivery pending retry, or external network provider latency. System continues operating safely. |
| **CRITICAL** | Crimson Badge | Fatal condition: Network disconnected, database corrupt, or stale lockfile from an ungraceful crash. Automatic production halts at safety gates until cleared. |

---

## 4. Troubleshooting

1. **Dashboard says "Failed to update dashboard" in browser console:**
   - Verify that `run_dashboard.py` is actively running in a terminal.
   - Confirm port 8000 is not blocked by Windows Firewall or another local process.
2. **Scheduler state shows `IDLE`:**
   - This is normal when the studio is between scheduled cycles (e.g. between 00:00 and 06:00 IST).
   - If a cycle was expected to run, inspect `logs/` or check `backend/studio_scheduler.lock`.
3. **Database Health shows `DEGRADED`:**
   - Run `python backend/scripts/audit_preflight_baseline.py` to inspect WAL files and perform integrity check.
