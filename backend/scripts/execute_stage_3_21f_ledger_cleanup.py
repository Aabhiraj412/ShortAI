"""
Stage 3.21F: Local Publishing Ledger Cleanup Script.
Safely backs up SQLite database, removes record #32 (YouTube ID 4DCas1e4vHI),
records minimal audit event, and verifies post-cleanup counts.
"""

import os
import sys
import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(r"D:\Agents\CrazzyDocs")
DB_PATH = WORKSPACE / "backend" / "studio.db"
BACKUPS_DIR = WORKSPACE / "backend" / "backups"
AUDIT_JSON_PATH = WORKSPACE / "docs" / "STAGE_3_21F_LEDGER_CLEANUP_AUDIT.json"

def sha256_file(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def main():
    print("=================================================================")
    print("STAGE 3.21F — LOCAL PUBLISHING LEDGER CLEANUP")
    print("=================================================================")

    assert DB_PATH.exists(), f"Database not found at {DB_PATH}"
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Inspect original state
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA integrity_check;")
    chk = cur.fetchone()[0]
    assert chk == "ok", f"Integrity check failed: {chk}"

    cur.execute("SELECT count(*) FROM publishing_ledger;")
    active_count = cur.fetchone()[0]
    assert active_count == 1, f"Expected 1 active record, found {active_count}"

    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    archive_count = cur.fetchone()[0]
    assert archive_count == 253, f"Expected 253 archive records, found {archive_count}"

    cur.execute("SELECT * FROM publishing_ledger WHERE id = 32;")
    row32 = cur.fetchone()
    assert row32 is not None, "Record #32 not found in publishing_ledger!"

    cur.execute("PRAGMA table_info(publishing_ledger);")
    cols = [c[1] for c in cur.fetchall()]
    row32_dict = dict(zip(cols, row32))
    assert row32_dict["youtube_video_id"] == "4DCas1e4vHI", f"Mismatch video_id: {row32_dict['youtube_video_id']}"
    print(f"Verified Record #32: ID={row32_dict['id']}, VideoID={row32_dict['youtube_video_id']}, Title='{row32_dict['topic_title']}'")

    # 2. Create verified SQLite Online Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS_DIR / f"studio_backup_pre_stage_3_21f_ledger_cleanup_{ts}.db"
    print(f"\nCreating SQLite online backup: {backup_path}...")
    backup_conn = sqlite3.connect(backup_path)
    conn.backup(backup_conn)
    backup_conn.close()

    # Verify backup integrity
    backup_conn = sqlite3.connect(backup_path)
    b_cur = backup_conn.cursor()
    b_cur.execute("PRAGMA integrity_check;")
    assert b_cur.fetchone()[0] == "ok", "Backup DB integrity check failed!"
    b_cur.execute("SELECT count(*) FROM publishing_ledger;")
    assert b_cur.fetchone()[0] == 1, "Backup active count mismatch!"
    b_cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    assert b_cur.fetchone()[0] == 253, "Backup archive count mismatch!"
    backup_conn.close()

    backup_sha = sha256_file(backup_path)
    backup_size = backup_path.stat().st_size
    print(f"Backup verified: {backup_path.name} ({backup_size} bytes, SHA-256: {backup_sha})")

    # 3. Create minimal audit table and record event
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deleted_video_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_ledger_id INTEGER NOT NULL,
            youtube_video_id TEXT NOT NULL,
            topic_title TEXT NOT NULL,
            visibility TEXT NOT NULL,
            video_hash TEXT NOT NULL,
            deleted_from_youtube_at TEXT NOT NULL,
            removed_from_ledger_at TEXT NOT NULL,
            deletion_reason TEXT NOT NULL,
            original_metadata_json TEXT
        );
    """)

    now_iso = datetime.now(timezone.utc).isoformat()
    deletion_reason = "Operator authorized permanent deletion of incorrectly titled/mismatched YouTube Short (Stage 3.21F)"

    cur.execute("""
        INSERT INTO deleted_video_audit (
            original_ledger_id, youtube_video_id, topic_title, visibility,
            video_hash, deleted_from_youtube_at, removed_from_ledger_at,
            deletion_reason, original_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        row32_dict["id"],
        row32_dict["youtube_video_id"],
        row32_dict["topic_title"],
        row32_dict["visibility"],
        row32_dict["video_hash"],
        "2026-11-11T03:58:53.000000+05:30",
        now_iso,
        deletion_reason,
        row32_dict["metadata_json"]
    ))

    # 4. Remove record #32 from publishing_ledger
    print("\nRemoving record #32 from active publishing_ledger...")
    cur.execute("DELETE FROM publishing_ledger WHERE id = 32;")
    assert cur.rowcount == 1, f"Expected 1 deleted row, got {cur.rowcount}"
    conn.commit()
    print("Record #32 deleted from publishing_ledger.")

    # 5. Verify post-cleanup database state
    cur.execute("SELECT count(*) FROM publishing_ledger;")
    post_active = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    post_archive = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM deleted_video_audit;")
    post_audit = cur.fetchone()[0]

    print("\n--- Post-Cleanup Database Counts ---")
    print(f"  Active publishing_ledger: {post_active}")
    print(f"  Archived historical records: {post_archive}")
    print(f"  Deleted video audit records: {post_audit}")

    assert post_active == 0, f"Expected 0 active records, found {post_active}"
    assert post_archive == 253, f"Expected 253 archive records, found {post_archive}"
    assert post_audit >= 1, "Audit record missing!"

    cur.execute("PRAGMA integrity_check;")
    assert cur.fetchone()[0] == "ok", "Post-cleanup database integrity check failed!"
    conn.close()

    # 6. Save JSON audit report
    audit_payload = {
        "cleanup_timestamp": now_iso,
        "sqlite_backup_path": str(backup_path),
        "sqlite_backup_size_bytes": backup_size,
        "sqlite_backup_sha256": backup_sha,
        "deleted_record": row32_dict,
        "reason": deletion_reason,
        "post_cleanup_state": {
            "active_publishing_ledger_count": post_active,
            "historical_archive_count": post_archive,
            "deleted_video_audit_count": post_audit,
            "active_pending_queue": 0,
            "verified_private_uploads": 0,
            "public_uploads": 0
        }
    }
    with open(AUDIT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(audit_payload, f, indent=2)
    print(f"Wrote audit report to {AUDIT_JSON_PATH}")

if __name__ == "__main__":
    main()
