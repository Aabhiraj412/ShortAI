# DASHBOARD ACCESS & OPERATIONS GUIDE — STAGE 3.20
**Project:** CrazzyDocs Autonomous Production Studio (`@crazzydocs` | Channel ID: `UCUOZOEBngXgt6W7fmus0bvw`)  
**URL:** `http://127.0.0.1:8000/dashboard` or `http://localhost:8000/`  

---

## 1. Quick Access

Whenever the dashboard service is running, navigate in any modern web browser to:
- **Primary:** `http://127.0.0.1:8000/dashboard`
- **Root Alias:** `http://127.0.0.1:8000/` (automatically redirects to `/dashboard`)

Upon system login, Windows Task Scheduler automatically starts `run_dashboard.py --open-browser`, which awaits server readiness and launches the default browser automatically.

---

## 2. Dashboard Interface Overview

The CrazzyDocs production dashboard features an obsidian dark theme optimized for continuous 24/7 monitoring.

### 2.1 System Overview Card
- **Channel Identity:** CrazzyDocs (`@crazzydocs` | ID: `UCUOZOEBngXgt6W7fmus0bvw`)
- **Active Schedule Profile:** `FOUR_CYCLES_DAILY` (Asia/Kolkata)
- **Active Cycle Hours:** `00:00`, `06:00`, `12:00`, `18:00 IST`
- **Pipeline Status:** Displays `IDLE`, `GENERATING`, `PUBLISHING`, or `RECOVERING`.
- **Scheduler Heartbeat:** Real-time indicator verifying daemon liveness.

### 2.2 Statistics & Publishing Metrics
- **Lifetime Published:** Total count of verified YouTube public shorts.
- **24-Hour Success Rate:** Ratio of successful to failed uploads over the rolling 24h window.
- **Queue Backlog:** Pending publications and retry queues.
- **Time Window Filter:** Toggle metrics across 24 Hours, 7 Days, 30 Days, or All Time.

### 2.3 Subsystem Health Matrix
Provides instant green/yellow/red status indicators for:
- **Database Subsystem:** SQLite connection, WAL mode integrity, schema validation.
- **YouTube API & OAuth:** Token expiry, refresh capability, rate-limit headroom.
- **FFmpeg / FFprobe:** Codec support, hardware acceleration status.
- **File Workspace:** Available disk storage, permissions, directory hierarchy.

### 2.4 Publishing History & Audit Ledger
- Real-time tabular feed of all published videos, video IDs, titles, privacy status, upload timestamps, and YouTube URLs.
- **Security Invariant:** All OAuth tokens, refresh tokens, and client secrets are strictly redacted across all API responses.

---

## 3. Manual Startup & Diagnostic Controls

If you ever wish to run the dashboard manually in an interactive terminal:

```powershell
cd D:\Agents\CrazzyDocs
.\.venv\Scripts\python.exe backend/scripts/run_dashboard.py --open-browser
```

### Options:
- `--port 8000`: Specify alternative port (default: 8000)
- `--host 127.0.0.1`: Bind address (default: 127.0.0.1)
- `--open-browser`: Automatically open default web browser once ASGI server responds with HTTP 200
- `--reload`: Hot-reload Python code changes (for development use)

---

## 4. Troubleshooting & FAQ

### Port 8000 in Use?
The runner script includes automated duplicate detection. If `run_dashboard.py` detects an active CrazzyDocs dashboard on port 8000, it opens your browser to the running instance and exits cleanly without port collisions.

### Browser Did Not Open?
1. Open any browser manually and navigate to `http://127.0.0.1:8000/dashboard`.
2. Check `D:\Agents\CrazzyDocs\logs\dashboard_service.log` for details.
