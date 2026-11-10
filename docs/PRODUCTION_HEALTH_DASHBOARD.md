# CrazzyDocs Production Health Dashboard

**Location:** `backend/app/services/production_health_dashboard.py`  
**CLI Tool:** `backend/scripts/show_production_dashboard.py`  
**Execution:** `python backend/scripts/show_production_dashboard.py`  
**Status:** Live & Operational in Production Environment  

---

## 1. Purpose & Architecture

The Production Health Dashboard provides an instant, consolidated operational overview of the autonomous studio without requiring operators to inspect raw logs or parse database files.

It queries real operational state from:
1. SQLite Production Database (`studio.db`): `persistent_jobs`, `publishing_ledger`, `persistent_notifications`.
2. Windows Process Lockfile (`studio_scheduler.lock`).
3. Network Connectivity Service (`network_connectivity_service`).
4. Active Schedule Profile (`schedule_profiles.py`).

---

## 2. Dashboard Metrics Specification

| Metric | Source | Description |
|---|---|---|
| **Overall Health** | Computed | `HEALTHY`, `DEGRADED` (failed QC / offline notifications), or `CRITICAL` (disconnected / stale lock). |
| **Recovery Status** | `persistent_jobs` | `HEALTHY`, `RECOVERING`, or `BLOCKED`. |
| **Network Status** | Socket / DNS Probes | `CONNECTED`, `DEGRADED` (some providers unreachable), or `DISCONNECTED`. |
| **Scheduler State** | `studio_scheduler.lock` & Win32 API | `RUNNING` (with live PID), `IDLE` (unlocked), or `STALE_LOCK` (PID dead). |
| **Current Cycle** | Active Profile & Wall-Clock | Active daily production window with start/end in IST. |
| **Next Production** | Active Profile | Exact wall-clock timestamp of the next production cycle in IST. |
| **Pipeline Stage** | `persistent_jobs` | Active execution stage (e.g. `VOICEOVER (55%)`) or `IDLE`. |
| **Last Production** | `persistent_jobs` | Topic title and timestamp of most recently completed Short. |
| **Last Public Upload** | `publishing_ledger` | YouTube video ID, topic title, and upload timestamp of last public Short. |
| **Publishing Queue** | `publishing_ledger` | Total counts for `Pending`, `Uploaded`, and `Failed`. |
| **Failed QC Jobs** | `persistent_jobs` | Total blocked jobs with error reasons. |
| **Email Alerts** | `persistent_notifications` | Count of `Sent`, `Pending`, and `Failed` notifications. |

---

## 3. Sample Operational Output

```text
+----------------------------------------------------------------------------+
| CRAZZYDOCS PRODUCTION HEALTH DASHBOARD | 2026-11-11T00:20:47 IST |
+----------------------------------------------------------------------------+
| Overall System Health:  [HEALTHY   ]  Recovery: [HEALTHY   ]            |
| Network Connectivity:   [CONNECTED ]  Scheduler: [IDLE      ]           |
+----------------------------------------------------------------------------+
| Current Active Cycle:   Cycle 1: Midnight Production -> Morning 06:00 IST Target (00:00–06:00 IST) |
| Next Production (IST):  2026-11-11 06:00:00 IST (Cycle 2: Morning Production -> Noon 12:00 IST Target) |
| Current Pipeline Stage: IDLE                                               |
+----------------------------------------------------------------------------+
| Last Production:        The Flight Attendant Who Fell 33,000 Feet With     |
| Last Public Upload:     None (Awaiting Scheduled Window)                   |
+----------------------------------------------------------------------------+
| Publishing Queue:       Pending: 127 | Uploaded: 1 | Failed: 0                 |
| Failed QC Jobs:         0 failed/blocked                                    |
| Email Alerts:           Sent: 0 | Pending: 0 | Failed: 0                    |
+----------------------------------------------------------------------------+
```

---

## 4. Invariants & Health Rules

- **No Mocked Data:** All numbers, dates, and statuses reflect real physical files, active sockets, and database records.
- **IST Timezone:** All timestamps are displayed in `Asia/Kolkata` (IST) with explicit offsets.
- **Zero API Polling:** The dashboard queries local state and does not consume external YouTube Data API quota units.
