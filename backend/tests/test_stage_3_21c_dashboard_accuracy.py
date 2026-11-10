"""
Test Suite for Stage 3.21C Dashboard Data Accuracy Audit & Reconciliation.
Validates:
1. Exact SQL counts match dashboard metrics.
2. Publishing Ledger Safety reporting (1 Active | 253 Archived).
3. Separation of genuine production statistics from test runs.
4. Recovery history reporting of cycle_1_20261111_0000 as ABORTED.
5. Active publishing history strictly separated from archive history.
6. Safety invariants (Scheduler disabled, 0 public uploads).
"""

import os
import sqlite3
import sys
import pytest
from fastapi.testclient import TestClient

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if os.path.join(WORKSPACE_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "backend"))

DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")

os.environ["ENABLE_EMBEDDED_SCHEDULER"] = "false"
from backend.app.main import app

client = TestClient(app)


def test_live_database_accuracy_counts():
    """Verify live database query returns exact clean slate counts."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM publishing_ledger;")
    active_pub = cur.fetchone()[0]
    assert active_pub == 0, f"Expected 0 active records, found {active_pub}"

    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    arch_pub = cur.fetchone()[0]
    assert arch_pub == 253, f"Expected 253 archived records, found {arch_pub}"

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'pending_queue';")
    pending = cur.fetchone()[0]
    assert pending == 0, f"Expected 0 pending queue records, found {pending}"

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'uploaded_private';")
    private_cnt = cur.fetchone()[0]
    assert private_cnt == 0, f"Expected 0 private uploads, found {private_cnt}"

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status IN ('uploaded_public', 'published');")
    public_cnt = cur.fetchone()[0]
    assert public_cnt == 0, f"Expected 0 public uploads, found {public_cnt}"

    conn.close()


def test_publishing_ledger_safety_reporting():
    """Verify dashboard health endpoint reports dynamic safety counts instead of stale '202'."""
    res = client.get("/api/dashboard/health")
    assert res.status_code == 200
    data = res.json()
    assert "publishing_ledger_safety" in data
    safety = data["publishing_ledger_safety"]
    assert safety["status"] == "IDEMPOTENT"
    assert safety["active_count"] == 0
    assert safety["archive_count"] == 253
    assert safety["display_text"] == "IDEMPOTENT (0 Active | 253 Archived)"


def test_genuine_vs_test_production_metrics():
    """Verify production statistics separate genuine production attempts from test runs."""
    res = client.get("/api/dashboard/stats?filter=all")
    assert res.status_code == 200
    data = res.json()

    assert data["test_jobs_count"] == 3
    assert data["genuine_attempted"] == 1  # cycle_1_20261111_0000
    assert data["genuine_completed"] == 0  # 0 genuine productions completed
    assert data["private_uploads"] == 0
    assert data["public_uploads"] == 0
    assert data["active_pending_queue"] == 0
    assert data["archived_showcase_count"] == 180
    assert data["archived_records_count"] == 253


def test_aborted_recovery_job_display():
    """Verify recovery endpoint displays cycle_1_20261111_0000 with clear ABORTED explanation."""
    res = client.get("/api/dashboard/recovery")
    assert res.status_code == 200
    items = res.json()

    cycle_job = next((j for j in items if j["job_id"] == "cycle_1_20261111_0000"), None)
    assert cycle_job is not None, "Job cycle_1_20261111_0000 missing from recovery list"
    assert "ABORTED — No production executed" in cycle_job["status"]
    assert "Cleanly Aborted" in cycle_job["recovered_stage"]
    assert cycle_job["operator_intervention_needed"] is False
    assert cycle_job["retry_count"] == 0


def test_active_vs_archived_history_separation():
    """Verify active publishing history is strictly separated from archived records."""
    # Active history: 0 records after Stage 3.21F cleanup
    hist_res = client.get("/api/dashboard/history")
    assert hist_res.status_code == 200
    active_items = hist_res.json()
    assert len(active_items) == 0

    # Archive history: contains the 253 archived records
    arch_res = client.get("/api/dashboard/archive?limit=50")
    assert arch_res.status_code == 200
    arch_data = arch_res.json()
    assert arch_data["summary"]["total_archived"] == 253
    assert arch_data["summary"]["showcase_fixtures"] == 180
    assert len(arch_data["recent_archived"]) == 50

    # Ensure no active ID is in the archive
    arch_ids = [a["id"] for a in arch_data["recent_archived"]]
    assert 32 not in arch_ids


def test_scheduler_disabled_invariant():
    """Verify scheduler task remains Disabled."""
    from backend.scripts.manage_windows_startup import query_task_details
    details = query_task_details("CrazzyDocs-Production-Scheduler")
    assert details["is_registered"] is True
    assert details["status"] == "Disabled"
