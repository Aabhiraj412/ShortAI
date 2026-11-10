import sqlite3
import os
import json

WORKSPACE = r"D:\Agents\CrazzyDocs"
db_path = os.path.join(WORKSPACE, "backend", "studio.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== PUBLISHING LEDGER ROWS ===")
cur.execute("SELECT id, topic_title, upload_status, visibility, video_path, upload_timestamp FROM publishing_ledger ORDER BY id")
rows = cur.fetchall()
print(f"Total active rows: {len(rows)}")
for r in rows:
    print(dict(r))

print("\n=== PERSISTENT JOBS TABLE INFO ===")
cur.execute("PRAGMA table_info(persistent_jobs)")
cols = [c[1] for c in cur.fetchall()]
print("Columns in persistent_jobs:", cols)

cur.execute("SELECT * FROM persistent_jobs ORDER BY rowid DESC LIMIT 10")
for r in cur.fetchall():
    print(dict(r))

print("\n=== RECORD #276 CHECK ===")
for t in ["publishing_ledger", "publishing_ledger_archive", "deleted_video_audit", "persistent_jobs"]:
    try:
        cur.execute(f"SELECT * FROM {t} WHERE id = 276")
        res = cur.fetchall()
        print(f"In {t}:", [dict(x) for x in res])
    except Exception as e:
        print(f"Error checking {t}: {e}")

conn.close()
