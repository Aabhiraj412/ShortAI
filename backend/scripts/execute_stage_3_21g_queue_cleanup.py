import sqlite3
import os
import json
from datetime import datetime

WORKSPACE = r"D:\Agents\CrazzyDocs"
db_path = os.path.join(WORKSPACE, "backend", "studio.db")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. Update any lingering test RUNNING jobs to ABORTED_TEST
cur.execute("""
    UPDATE persistent_jobs
    SET status = 'ABORTED_TEST', error_details = 'Aborted test process during emergency containment'
    WHERE status = 'RUNNING' AND id LIKE 'test_%'
""")
aborted_count = cur.rowcount
print(f"Updated {aborted_count} lingering test job(s) to ABORTED_TEST")

# 2. Ensure test_queue_incident_audit table exists
cur.execute("""
    CREATE TABLE IF NOT EXISTS test_queue_incident_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ledger_id INTEGER,
        topic_title TEXT,
        video_path TEXT,
        upload_status TEXT,
        visibility TEXT,
        video_hash TEXT,
        upload_timestamp TEXT,
        incident_reason TEXT,
        archived_at TEXT
    )
""")

# 3. Fetch all active test records from publishing_ledger
cur.execute("SELECT * FROM publishing_ledger WHERE video_path LIKE '%test_%'")
rows = cur.fetchall()
print(f"Found {len(rows)} test records in publishing_ledger to archive and remove:")

for r in rows:
    d = dict(r)
    print(f"  - Record #{d['id']}: {d['video_path']} ({d['upload_timestamp']})")
    cur.execute("""
        INSERT INTO test_queue_incident_audit (
            ledger_id, topic_title, video_path, upload_status, visibility,
            video_hash, upload_timestamp, incident_reason, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        d["id"],
        d["topic_title"],
        d["video_path"],
        d["upload_status"],
        d["visibility"],
        d["video_hash"],
        d["upload_timestamp"],
        "Stage 3.21G Containment: Test job executed without ExecutionContext guard",
        datetime.utcnow().isoformat() + "Z",
    ))

# 4. Remove these records from publishing_ledger
cur.execute("DELETE FROM publishing_ledger WHERE video_path LIKE '%test_%'")
deleted_count = cur.rowcount
print(f"Deleted {deleted_count} test records from publishing_ledger")

conn.commit()

# 5. Verification queries
cur.execute("SELECT COUNT(*) FROM publishing_ledger")
active_ledger = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM publishing_ledger_archive")
archived_ledger = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM test_queue_incident_audit")
incident_audit = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM persistent_jobs WHERE status = 'RUNNING'")
running_jobs = cur.fetchone()[0]

print("\n=== POST-CLEANUP VERIFICATION ===")
print(f"Active publishing ledger: {active_ledger}")
print(f"Archived publishing ledger: {archived_ledger}")
print(f"Test queue incident audit records: {incident_audit}")
print(f"Active RUNNING jobs: {running_jobs}")

conn.close()
