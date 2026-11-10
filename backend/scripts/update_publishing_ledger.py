import sqlite3
import shutil
import datetime
import os

WORKSPACE = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE, "backend", "studio.db")
BACKUP_DIR = os.path.join(WORKSPACE, "backend", "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# 1. Backup
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = os.path.join(BACKUP_DIR, f"studio_backup_pre_stage_3_14b_{timestamp}.db")
shutil.copy2(DB_PATH, backup_path)
print(f"Backed up studio.db to {backup_path}")

# 2. Update paths
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

# Canonical paths
cycle_4_video = os.path.join(WORKSPACE, "media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4")
cycle_4_thumb = os.path.join(WORKSPACE, "media", "productions", "prod_cycle_4_20261110_1800", "thumbnails", "thumb_cycle_4_20261110_1800.png")

vesna_video = os.path.join(WORKSPACE, "media", "productions", "prod_20261110_vesna_vulovic", "video", "showcase_vesna_vulovic_verified.mp4")
vesna_thumb = os.path.join(WORKSPACE, "media", "productions", "prod_20261110_vesna_vulovic", "thumbnails", "thumb_vesna_vulovic_verified.png")

# Verify canonical files exist on disk before updating DB
assert os.path.isfile(cycle_4_video), f"File missing: {cycle_4_video}"
assert os.path.isfile(cycle_4_thumb), f"File missing: {cycle_4_thumb}"
assert os.path.isfile(vesna_video), f"File missing: {vesna_video}"
assert os.path.isfile(vesna_thumb), f"File missing: {vesna_thumb}"

# Update cycle 4 rows (22, 23, 32)
cur.execute("""
    UPDATE publishing_ledger
    SET video_path = ?, thumbnail_path = ?
    WHERE id IN (22, 23, 32)
""", (cycle_4_video, cycle_4_thumb))
print(f"Updated cycle_4 rows: {cur.rowcount}")

# Update test integrity run rows (33, 34, 36, 37, 38, 40, 42) to canonical Vesna paths
cur.execute("""
    UPDATE publishing_ledger
    SET video_path = ?, thumbnail_path = ?
    WHERE id IN (33, 34, 36, 37, 38, 40, 42)
""", (vesna_video, vesna_thumb))
print(f"Updated vesna integrity run rows: {cur.rowcount}")

con.commit()

# Verify
cur.execute("SELECT id, topic_title, video_path, thumbnail_path, upload_status, youtube_video_id FROM publishing_ledger WHERE id IN (22, 23, 32, 33, 34, 36, 37, 38, 40, 42)")
rows = cur.fetchall()
for r in rows:
    print(r)

con.close()
print("Database update successfully committed.")
