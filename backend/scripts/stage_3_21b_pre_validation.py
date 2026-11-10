"""
Stage 3.21B Pre-Cleanup Validation Script.
Validates database integrity, record counts, inventory alignment, and invariants.
"""

import hashlib
import json
import os
import sqlite3
import sys

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")
DOCS_DIR = os.path.join(WORKSPACE_ROOT, "docs")

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    print(f"=== STAGE 3.21B PRE-CLEANUP VALIDATION ===")
    print(f"Database Path: {DB_PATH} (Exists: {os.path.exists(DB_PATH)})")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 1. Database Integrity
    cur.execute("PRAGMA integrity_check;")
    integrity = cur.fetchone()[0]
    print(f"Database Integrity Check: {integrity}")
    assert integrity == "ok", f"Integrity check failed: {integrity}"
    
    # 2. Tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Existing Tables ({len(tables)}): {tables}")
    
    # 3. Counts
    cur.execute("SELECT count(*) FROM publishing_ledger;")
    pub_count = cur.fetchone()[0]
    print(f"publishing_ledger Total Records: {pub_count}")
    
    cur.execute("SELECT count(*) FROM persistent_jobs;")
    jobs_count = cur.fetchone()[0]
    print(f"persistent_jobs Total Records: {jobs_count}")
    
    # 4. Check Genuine Private Upload Record #32
    cur.execute("SELECT id, topic_title, upload_status, visibility, youtube_video_id, scheduled_timestamp, upload_timestamp FROM publishing_ledger WHERE id = 32;")
    rec32 = cur.fetchone()
    print(f"Genuine Private Upload Record #32: {rec32}")
    assert rec32 is not None, "Record 32 missing from publishing_ledger!"
    assert rec32[4] == "4DCas1e4vHI", f"Record 32 video ID mismatch: {rec32[4]}"
    assert rec32[2] == "uploaded_private", f"Record 32 status mismatch: {rec32[2]}"
    assert rec32[3] == "private", f"Record 32 visibility mismatch: {rec32[3]}"
    
    # 5. Check Recovery Job cycle_1_20261111_0000
    cur.execute("SELECT id, job_id, status, progress_percent, current_stage, error_message, checkpoint_data FROM persistent_jobs WHERE job_id = 'cycle_1_20261111_0000';")
    rec_job = cur.fetchone()
    print(f"Recovery Job cycle_1_20261111_0000: {rec_job}")
    
    # 6. Check Inventory Files Alignment
    inv_file = os.path.join(DOCS_DIR, "STAGE_3_21A_LEDGER_INVENTORY.json")
    arch_file = os.path.join(DOCS_DIR, "STAGE_3_21A_PROPOSED_ARCHIVE_LIST.json")
    pres_file = os.path.join(DOCS_DIR, "STAGE_3_21A_GENUINE_PRESERVATION_LIST.json")
    
    with open(inv_file, "r", encoding="utf-8") as f:
        inventory = json.load(f)
    with open(arch_file, "r", encoding="utf-8") as f:
        proposed_archive = json.load(f)
    with open(pres_file, "r", encoding="utf-8") as f:
        preservation = json.load(f)
        
    print(f"Inventory Records Count: {len(inventory)}")
    print(f"Proposed Archive Count: {len(proposed_archive)}")
    print(f"Preservation Count: {len(preservation)}")
    
    assert len(inventory) == pub_count, f"Inventory count {len(inventory)} != DB count {pub_count}"
    assert len(proposed_archive) == 251, f"Expected 251 proposed archive records, got {len(proposed_archive)}"
    assert len(preservation) == 1, f"Expected 1 preservation record, got {len(preservation)}"
    assert preservation[0]["id"] == 32, f"Preservation record is not 32: {preservation[0]}"
    
    # Check that all IDs in proposed archive exist in DB and none is 32
    arch_ids = set(r["id"] for r in proposed_archive)
    assert 32 not in arch_ids, "CRITICAL: Record 32 is in proposed archive list!"
    
    cur.execute("SELECT id FROM publishing_ledger;")
    db_ids = set(r[0] for r in cur.fetchall())
    
    assert db_ids == (arch_ids | {32}), "DB IDs do not perfectly match archive IDs + {32}!"
    print("Alignment check: 100% PERFECT MATCH between Stage 3.21A inventory and current database.")
    
    # 7. Check Stage 3.21A backup integrity
    backup_3_21a = os.path.join(WORKSPACE_ROOT, "backend", "backups", "studio_backup_pre_stage_3_21b_20261111_020710.db")
    print(f"Stage 3.21A Backup: {backup_3_21a} (Exists: {os.path.exists(backup_3_21a)})")
    if os.path.exists(backup_3_21a):
        bconn = sqlite3.connect(backup_3_21a)
        bcur = bconn.cursor()
        bcur.execute("PRAGMA integrity_check;")
        bintegrity = bcur.fetchone()[0]
        bconn.close()
        bhash = sha256_file(backup_3_21a)
        print(f"Stage 3.21A Backup Integrity: {bintegrity}, SHA-256: {bhash}")
        assert bintegrity == "ok", "Stage 3.21A backup integrity is not ok!"
    
    conn.close()
    print("=== PRE-CLEANUP VALIDATION PASSED ===")

if __name__ == "__main__":
    main()
