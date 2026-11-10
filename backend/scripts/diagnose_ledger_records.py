"""
Forensic Analysis of publishing_ledger in studio.db.
Stage 3.19A Diagnostics.
"""
import sqlite3
import os

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")

def analyze_ledger():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM publishing_ledger;")
    total_records = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'pending_queue';")
    pending_records = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status IN ('uploaded_public', 'uploaded_private', 'published');")
    uploaded_records = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status LIKE '%fail%';")
    failed_records = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status LIKE '%blocked%' OR upload_status LIKE '%qc%';")
    blocked_qc_records = cur.fetchone()[0]

    cur.execute("SELECT id, topic_title, video_path, thumbnail_path, upload_status, visibility, youtube_video_id, upload_timestamp FROM publishing_ledger;")
    all_rows = cur.fetchall()
    conn.close()

    test_run_records = 0
    historical_production_records = 0
    missing_artifacts = 0
    valid_artifacts = 0
    uncertain_status = 0
    duplicate_video_paths = {}

    for r in all_rows:
        lid, title, vpath, tpath, status, vis, ytid, ts = r
        vpath_str = str(vpath).lower() if vpath else ""
        title_str = str(title).lower() if title else ""

        # Test vs historical classification
        # Test runs explicitly use "test_" in path, or specific test topics
        is_test = any(x in vpath_str for x in ["test_", "test\\", "test/"]) or any(x in title_str for x in ["test", "interrupted", "demon core"])
        if is_test:
            test_run_records += 1
        else:
            historical_production_records += 1

        if vpath and os.path.exists(vpath):
            valid_artifacts += 1
            duplicate_video_paths[vpath] = duplicate_video_paths.get(vpath, 0) + 1
        else:
            missing_artifacts += 1

        # Uncertain status: pending_queue but has a youtube_video_id
        if status == "pending_queue" and ytid:
            uncertain_status += 1

    # Total duplicate records (records that point to a video path already pointed to by another record)
    total_duplicate_records = sum(cnt - 1 for cnt in duplicate_video_paths.values() if cnt > 1)

    print("=== PUBLISHING LEDGER FORENSIC BREAKDOWN ===")
    print(f"Total Records:                        {total_records}")
    print(f"Pending Records (pending_queue):      {pending_records}")
    print(f"Successfully Uploaded Records:        {uploaded_records}")
    print(f"Failed Records:                       {failed_records}")
    print(f"Blocked QC Records:                   {blocked_qc_records}")
    print(f"Duplicate Video References:           {total_duplicate_records}")
    print(f"Records with Valid Video on Disk:     {valid_artifacts}")
    print(f"Records with Missing Video on Disk:   {missing_artifacts}")
    print(f"Records Belonging to Test Runs:       {test_run_records}")
    print(f"Records Belonging to Historical Shows:{historical_production_records}")
    print(f"Uncertain YouTube Upload Status:      {uncertain_status}")
    print("\nPhysical Video File Reference Counts:")
    for vp, cnt in sorted(duplicate_video_paths.items(), key=lambda x: x[1], reverse=True):
        print(f"  [{cnt} records] -> {vp}")

if __name__ == "__main__":
    analyze_ledger()
