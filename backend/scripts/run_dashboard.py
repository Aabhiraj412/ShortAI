"""
Local Operator Browser Dashboard Launcher for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.19 Production Standard.

Starts the FastAPI server locally at http://127.0.0.1:8000/dashboard
Decoupled from the production scheduler to ensure observing metrics
never starts, stops, or interferes with autonomous production.
"""

import os
import sys

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if os.path.join(WORKSPACE_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "backend"))

# Decouple scheduler from dashboard viewer
os.environ["ENABLE_EMBEDDED_SCHEDULER"] = "false"

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    host = "127.0.0.1"

    print("=" * 72)
    print("      CRAZZYDOCS AUTONOMOUS STUDIO — PRODUCTION HEALTH DASHBOARD")
    print("=" * 72)
    print(f"  Access URL:        http://{host}:{port}/dashboard")
    print(f"  API Docs:          http://{host}:{port}/docs")
    print(f"  Scheduler Mode:    Decoupled (Reads lockfile and database state)")
    print(f"  Schedule Profile:  FOUR_CYCLES_DAILY (Persisted & Active)")
    print(f"  Stop Command:      Press Ctrl+C to close dashboard")
    print("=" * 72)

    uvicorn.run("app.main:app", host=host, port=port, log_level="info", reload=False)
