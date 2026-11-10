"""
Test Suite for Stage 3.21B Publishing Ledger Cleanup & Clean Production Slate.
Validates:
1. SQLite database integrity.
2. Active publishing ledger clean slate (exactly 1 genuine record).
3. Publishing ledger archive completeness (253 records).
4. Archive classification breakdown (180 showcase, 46 test w/ media, 27 test w/o media).
5. Original ID preservation and disjointness (ID 32 preserved, never archived).
6. Recovery job cycle_1_20261111_0000 status (ABORTED_PRE_RESET).
7. Zero pending entries in active publishing queue.
8. Dashboard endpoints reflection and security.
9. Windows Task Scheduler Disabled invariant.
"""

import json
import os
import sqlite3
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if os.path.join(WORKSPACE_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "backend"))

DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")
BACKUPS_DIR = os.path.join(WORKSPACE_ROOT, "backend", "backups")

os.environ["ENABLE_EMBEDDED_SCHEDULER"] = "false"
from backend.app.main import app

client = TestClient(app)


def test_database_integrity():
    """Verify SQLite database integrity check returns 'ok'."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    result = cur.fetchone()[0]
    conn.close()
    assert result == "ok", f"Integrity check returned: {result}"


def test_active_publishing_ledger_clean_slate():
    """Verify active publishing ledger contains exactly 1 genuine private upload."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM publishing_ledger;")
    total = cur.fetchone()[0]
    assert total == 1, f"Expected exactly 1 record in active publishing_ledger, got {total}"

    cur.execute("SELECT id, topic_title, upload_status, visibility, youtube_video_id FROM publishing_ledger;")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 32, f"Active record ID is {row[0]}, expected 32"
    assert row[2] == "uploaded_private", f"Status is {row[2]}, expected 'uploaded_private'"
    assert row[3] == "private", f"Visibility is {row[3]}, expected 'private'"
    assert row[4] == "4DCas1e4vHI", f"YouTube ID is {row[4]}, expected '4DCas1e4vHI'"
    assert "Flight Attendant" in row[1]


def test_publishing_ledger_archive_completeness():
    """Verify publishing_ledger_archive contains all 253 archived historical records."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    total = cur.fetchone()[0]
    conn.close()
    assert total == 253, f"Expected 253 records in publishing_ledger_archive, got {total}"


def test_archive_classification_breakdown():
    """Verify breakdown: 180 showcase, 46 test w/ media, 27 test w/o media."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT original_classification, count(*) FROM publishing_ledger_archive GROUP BY original_classification;")
    counts = dict(cur.fetchall())
    conn.close()

    showcase_count = sum(v for k, v in counts.items() if "SHOWCASE" in k.upper())
    missing_media_count = sum(v for k, v in counts.items() if "MISSING" in k.upper())
    test_media_count = sum(v for k, v in counts.items() if "SHOWCASE" not in k.upper() and "MISSING" not in k.upper())

    assert showcase_count == 180, f"Expected 180 showcase fixtures, got {showcase_count}"
    assert missing_media_count == 27, f"Expected 27 test records missing media, got {missing_media_count}"
    assert test_media_count == 46, f"Expected 46 test records with media, got {test_media_count}"


def test_original_id_preservation_and_disjointness():
    """Verify IDs in archive are preserved (1-31, 33-254) and ID 32 is strictly excluded."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM publishing_ledger_archive;")
    archive_ids = set(r[0] for r in cur.fetchall())
    conn.close()

    assert len(archive_ids) == 253
    assert 32 not in archive_ids, "Record 32 MUST NOT exist in archive!"
    assert min(archive_ids) == 1
    assert max(archive_ids) == 254
    assert archive_ids | {32} == set(range(1, 255))


def test_recovery_job_status():
    """Verify orphan recovery job cycle_1_20261111_0000 was safely marked ABORTED_PRE_RESET."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT status, error_details, progress_pct FROM persistent_jobs WHERE id = 'cycle_1_20261111_0000';")
    row = cur.fetchone()
    conn.close()

    assert row is not None, "Job cycle_1_20261111_0000 missing from persistent_jobs"
    assert row[0] == "ABORTED_PRE_RESET", f"Job status is {row[0]}, expected ABORTED_PRE_RESET"
    assert "Stage 3.21B" in (row[1] or ""), f"Unexpected error_details: {row[1]}"
    assert row[2] == 0.0, f"Progress was not 0%: {row[2]}"


def test_zero_active_queue_pending():
    """Verify zero pending queue entries exist in active publishing ledger."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'pending_queue';")
    pending_count = cur.fetchone()[0]
    conn.close()
    assert pending_count == 0, f"Expected 0 pending queue records, found {pending_count}"


def test_dashboard_endpoints():
    """Verify dashboard API endpoints reflect clean slate and archive stats."""
    # Overview
    ov_res = client.get("/api/dashboard/overview")
    assert ov_res.status_code == 200
    ov_data = ov_res.json()
    assert ov_data["pipeline_stage"] == "IDLE"

    # Stats
    stats_res = client.get("/api/dashboard/stats?filter=all")
    assert stats_res.status_code == 200
    stats_data = stats_res.json()
    assert stats_data["active_pending_queue"] == 0
    assert stats_data["private_uploads"] == 1
    assert stats_data["archived_records_count"] == 253

    # History
    hist_res = client.get("/api/dashboard/history")
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    assert len(hist_data) == 1
    assert hist_data[0]["id"] == 32
    assert hist_data[0]["youtube_video_id"] == "4DCas1e4vHI"

    # Archive endpoint
    arch_res = client.get("/api/dashboard/archive?limit=10")
    assert arch_res.status_code == 200
    arch_data = arch_res.json()
    assert arch_data["summary"]["total_archived"] == 253
    assert len(arch_data["recent_archived"]) == 10


def test_scheduler_task_disabled_invariant():
    """Verify CrazzyDocs-Production-Scheduler is in Disabled state."""
    from backend.scripts.manage_windows_startup import query_task_details
    details = query_task_details("CrazzyDocs-Production-Scheduler")
    assert details["is_registered"] is True
    assert details["status"] == "Disabled", f"Scheduler task status is {details['status']}, expected 'Disabled'"
