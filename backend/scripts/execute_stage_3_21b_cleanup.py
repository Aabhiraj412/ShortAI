"""
Stage 3.21B Publishing Ledger Cleanup & Clean Production Slate Execution Script.
Executes online backup, archival table creation, atomic record archival,
recovery job handling, and post-cleanup verification.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import sys

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")
BACKUPS_DIR = os.path.join(WORKSPACE_ROOT, "backend", "backups")
DOCS_DIR = os.path.join(WORKSPACE_ROOT, "docs")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 60)
    print("STAGE 3.21B — PUBLISHING LEDGER CLEANUP & ARCHIVAL EXECUTION")
    print("=" * 60)
    
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUPS_DIR, f"studio_backup_pre_stage_3_21b_execution_{timestamp_str}.db")
    
    # -------------------------------------------------------------
    # 1. PRE-CHECKS & ONLINE BACKUP
    # -------------------------------------------------------------
    print("\n[STEP 1] Performing SQLite Online Backup...")
    assert os.path.exists(DB_PATH), f"Source database not found at {DB_PATH}"
    
    src_conn = sqlite3.connect(DB_PATH)
    src_cur = src_conn.cursor()
    
    src_cur.execute("PRAGMA integrity_check;")
    assert src_cur.fetchone()[0] == "ok", "Source DB integrity check failed!"
    
    # Check current row counts
    src_cur.execute("SELECT count(*) FROM publishing_ledger;")
    src_pub_count = src_cur.fetchone()[0]
    src_cur.execute("SELECT count(*) FROM persistent_jobs;")
    src_jobs_count = src_cur.fetchone()[0]
    print(f"Source DB initial records: publishing_ledger={src_pub_count}, persistent_jobs={src_jobs_count}")
    assert src_pub_count == 254, f"Expected 254 records in source DB, got {src_pub_count}"
    
    # Execute Online Backup API
    dst_conn = sqlite3.connect(backup_path)
    src_conn.backup(dst_conn)
    dst_conn.close()
    print(f"Online backup created at: {backup_path}")
    
    # Verify Backup File
    b_conn = sqlite3.connect(backup_path)
    b_cur = b_conn.cursor()
    b_cur.execute("PRAGMA integrity_check;")
    assert b_cur.fetchone()[0] == "ok", "Backup integrity check failed!"
    b_cur.execute("SELECT count(*) FROM publishing_ledger;")
    b_pub_count = b_cur.fetchone()[0]
    b_cur.execute("SELECT count(*) FROM persistent_jobs;")
    b_jobs_count = b_cur.fetchone()[0]
    assert b_pub_count == src_pub_count, f"Backup pub count mismatch: {b_pub_count} vs {src_pub_count}"
    assert b_jobs_count == src_jobs_count, f"Backup jobs count mismatch: {b_jobs_count} vs {src_jobs_count}"
    
    # Verify Genuine Record 32 in backup
    b_cur.execute("SELECT id, youtube_video_id, upload_status, visibility FROM publishing_ledger WHERE id = 32;")
    rec32 = b_cur.fetchone()
    assert rec32 == (32, "4DCas1e4vHI", "uploaded_private", "private"), f"Record 32 corrupted in backup: {rec32}"
    b_conn.close()
    
    backup_hash = sha256_file(backup_path)
    print(f"Backup verified successfully! SHA-256: {backup_hash}")
    
    # -------------------------------------------------------------
    # 2. BUILD ARCHIVE INVENTORY (253 RECORDS)
    # -------------------------------------------------------------
    print("\n[STEP 2] Loading Stage 3.21A inventory and classifying records...")
    arch_manifest_file = os.path.join(DOCS_DIR, "STAGE_3_21A_PROPOSED_ARCHIVE_LIST.json")
    with open(arch_manifest_file, "r", encoding="utf-8") as f:
        proposed_archive = json.load(f)
    
    assert len(proposed_archive) == 251, f"Expected 251 records from 3.21A manifest, got {len(proposed_archive)}"
    
    # Build classification map
    archive_items = {}
    for item in proposed_archive:
        cls = item.get("classification", "HISTORICAL_TEST")
        reason = item.get("archive_reason") or f"Historical {cls} archived during Stage 3.21B cleanup"
        archive_items[item["id"]] = {
            "classification": cls,
            "reason": reason,
        }
    
    # Add authorized records 253 & 254
    archive_items[253] = {
        "classification": "Test execution records with media",
        "reason": "Automated test artifact from Stage 3.10 resilience tests (re-run during Stage 3.20C)",
    }
    archive_items[254] = {
        "classification": "Test execution records with media",
        "reason": "Automated test artifact from Stage 3.10 resilience tests (re-run during Stage 3.20C)",
    }
    
    archive_ids = sorted(archive_items.keys())
    assert len(archive_ids) == 253, f"Expected 253 records to archive, got {len(archive_ids)}"
    assert 32 not in archive_ids, "CRITICAL ERROR: Genuine Record #32 is in archive list!"
    print(f"Archive list prepared: {len(archive_ids)} records (IDs 1-31, 33-254). Record #32 preserved.")
    
    # -------------------------------------------------------------
    # 3. CREATE ARCHIVE TABLE
    # -------------------------------------------------------------
    print("\n[STEP 3] Ensuring publishing_ledger_archive table exists...")
    src_cur.execute("""
        CREATE TABLE IF NOT EXISTS publishing_ledger_archive (
            id INTEGER PRIMARY KEY,
            topic_title TEXT NOT NULL,
            video_path TEXT NOT NULL,
            thumbnail_path TEXT,
            metadata_json TEXT NOT NULL,
            youtube_video_id TEXT,
            upload_status TEXT NOT NULL,
            visibility TEXT NOT NULL,
            video_hash TEXT NOT NULL,
            upload_timestamp TEXT,
            scheduled_timestamp TEXT,
            error_details TEXT,
            archived_at TEXT NOT NULL,
            original_status TEXT NOT NULL,
            archive_reason TEXT NOT NULL,
            original_classification TEXT NOT NULL,
            source_stage TEXT NOT NULL DEFAULT 'Stage 3.21B',
            inventory_reference TEXT NOT NULL DEFAULT 'STAGE_3_21A_LEDGER_INVENTORY'
        );
    """)
    src_conn.commit()
    print("publishing_ledger_archive table confirmed.")
    
    # -------------------------------------------------------------
    # 4. EXECUTE ARCHIVAL TRANSACTION
    # -------------------------------------------------------------
    print("\n[STEP 4] Executing Atomic Archival Transaction...")
    now_iso = datetime.now(timezone.utc).isoformat()
    
    try:
        src_conn.isolation_level = None  # Explicit transaction control
        src_cur.execute("BEGIN IMMEDIATE;")
        
        # Select all 253 source records
        placeholders = ",".join("?" for _ in archive_ids)
        src_cur.execute(f"SELECT * FROM publishing_ledger WHERE id IN ({placeholders}) ORDER BY id;", archive_ids)
        source_rows = src_cur.fetchall()
        assert len(source_rows) == 253, f"Expected 253 source rows, found {len(source_rows)}"
        
        # Column names of publishing_ledger
        src_cur.execute("PRAGMA table_info(publishing_ledger);")
        col_names = [r[1] for r in src_cur.fetchall()]
        
        inserted_count = 0
        for row in source_rows:
            row_dict = dict(zip(col_names, row))
            r_id = row_dict["id"]
            meta = archive_items[r_id]
            
            src_cur.execute("""
                INSERT INTO publishing_ledger_archive (
                    id, topic_title, video_path, thumbnail_path, metadata_json,
                    youtube_video_id, upload_status, visibility, video_hash,
                    upload_timestamp, scheduled_timestamp, error_details,
                    archived_at, original_status, archive_reason,
                    original_classification, source_stage, inventory_reference
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, 'Stage 3.21B', 'STAGE_3_21A_LEDGER_INVENTORY'
                );
            """, (
                r_id,
                row_dict["topic_title"],
                row_dict["video_path"],
                row_dict["thumbnail_path"],
                row_dict["metadata_json"],
                row_dict["youtube_video_id"],
                row_dict["upload_status"],
                row_dict["visibility"],
                row_dict["video_hash"],
                row_dict["upload_timestamp"],
                row_dict["scheduled_timestamp"],
                row_dict["error_details"],
                now_iso,
                row_dict["upload_status"],
                meta["reason"],
                meta["classification"],
            ))
            inserted_count += 1
            
        assert inserted_count == 253, f"Inserted {inserted_count} rows into archive, expected 253"
        print(f"Successfully staged {inserted_count} records into publishing_ledger_archive.")
        
        # Verify archive table count
        src_cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
        arch_table_count = src_cur.fetchone()[0]
        assert arch_table_count == 253, f"Expected 253 archive records, got {arch_table_count}"
        
        # Delete the 253 records from active publishing_ledger
        src_cur.execute(f"DELETE FROM publishing_ledger WHERE id IN ({placeholders});", archive_ids)
        deleted_count = src_cur.rowcount
        assert deleted_count == 253, f"Deleted {deleted_count} rows, expected 253"
        print(f"Successfully removed {deleted_count} records from active publishing_ledger.")
        
        # Verify active ledger contains exactly 1 record: ID 32
        src_cur.execute("SELECT count(*) FROM publishing_ledger;")
        remaining_count = src_cur.fetchone()[0]
        assert remaining_count == 1, f"Expected 1 record remaining in publishing_ledger, got {remaining_count}"
        
        src_cur.execute("SELECT id, topic_title, upload_status, visibility, youtube_video_id FROM publishing_ledger;")
        remaining_record = src_cur.fetchone()
        assert remaining_record[0] == 32, f"Remaining record is not #32: {remaining_record}"
        assert remaining_record[4] == "4DCas1e4vHI", f"Remaining record video ID mismatch: {remaining_record}"
        assert remaining_record[2] == "uploaded_private", f"Remaining record status mismatch: {remaining_record}"
        assert remaining_record[3] == "private", f"Remaining record visibility mismatch: {remaining_record}"
        print(f"Verified active ledger: exactly 1 genuine record preserved: {remaining_record}")
        
        src_cur.execute("COMMIT;")
        print("Archival transaction successfully COMMITTED.")
    except Exception as e:
        src_cur.execute("ROLLBACK;")
        print(f"CRITICAL ERROR in archival transaction: {e}. Transaction ROLLED BACK.")
        raise
    
    # -------------------------------------------------------------
    # 5. RECOVERY JOB HANDLING
    # -------------------------------------------------------------
    print("\n[STEP 5] Updating orphan recovery job cycle_1_20261111_0000...")
    src_cur.execute("SELECT id, status, current_stage, progress_pct, checkpoint_data_json FROM persistent_jobs WHERE id = 'cycle_1_20261111_0000';")
    job_row = src_cur.fetchone()
    print(f"Current recovery job state: {job_row}")
    if job_row:
        assert job_row[3] == 0.0, f"Recovery job progress is not 0%: {job_row[3]}"
        assert job_row[4] in ("{}", None), f"Recovery job has non-empty checkpoint: {job_row[4]}"
        
        src_cur.execute("""
            UPDATE persistent_jobs
            SET status = 'ABORTED_PRE_RESET',
                error_details = 'Cleanly aborted before Stage 3.21B publishing ledger reset; no production work was executed.',
                updated_at = ?
            WHERE id = 'cycle_1_20261111_0000';
        """, (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),))
        src_conn.commit()
        print("Recovery job cycle_1_20261111_0000 status updated to ABORTED_PRE_RESET.")
    else:
        print("Note: cycle_1_20261111_0000 not found in persistent_jobs.")
        
    # -------------------------------------------------------------
    # 6. POST-CLEANUP VERIFICATION
    # -------------------------------------------------------------
    print("\n[STEP 6] Post-cleanup database verification...")
    src_cur.execute("PRAGMA integrity_check;")
    assert src_cur.fetchone()[0] == "ok", "Post-cleanup integrity check failed!"
    
    src_cur.execute("SELECT count(*) FROM publishing_ledger;")
    final_pub_count = src_cur.fetchone()[0]
    src_cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    final_arch_count = src_cur.fetchone()[0]
    src_cur.execute("SELECT count(*) FROM persistent_jobs;")
    final_jobs_count = src_cur.fetchone()[0]
    
    print(f"Final Counts: publishing_ledger={final_pub_count}, publishing_ledger_archive={final_arch_count}, persistent_jobs={final_jobs_count}")
    assert final_pub_count == 1, f"Active publishing_ledger count != 1: {final_pub_count}"
    assert final_arch_count == 253, f"Archive count != 253: {final_arch_count}"
    assert final_jobs_count == src_jobs_count, f"Jobs count changed: {final_jobs_count} vs {src_jobs_count}"
    
    # -------------------------------------------------------------
    # 7. EXPORT SUMMARY ARTIFACT
    # -------------------------------------------------------------
    summary_data = {
        "execution_timestamp": datetime.now(timezone.utc).isoformat(),
        "backup_path": backup_path,
        "backup_sha256": backup_hash,
        "source_initial_count": src_pub_count,
        "archived_records_count": final_arch_count,
        "active_records_count": final_pub_count,
        "preserved_record": {
            "id": remaining_record[0],
            "topic_title": remaining_record[1],
            "upload_status": remaining_record[2],
            "visibility": remaining_record[3],
            "youtube_video_id": remaining_record[4],
        },
        "archive_breakdown": {
            "showcase_fixtures": 180,
            "test_records_with_media": 46,  # 44 from 3.21A + 2 authorized (253, 254)
            "test_records_missing_media": 27,
            "total_archived": 253,
        },
        "recovery_job_cycle_1_20261111_0000": "ABORTED_PRE_RESET",
        "database_integrity": "ok",
    }
    
    summary_path = os.path.join(DOCS_DIR, "STAGE_3_21B_POST_CLEANUP_SUMMARY.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print(f"Summary artifact written to: {summary_path}")
    
    src_conn.close()
    print("\n" + "=" * 60)
    print("STAGE 3.21B CLEANUP COMPLETED WITH 100% INTEGRITY")
    print("=" * 60)

if __name__ == "__main__":
    main()
