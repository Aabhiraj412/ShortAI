"""Stage 3.21H: Cleanup of test records from content_ledger and obsolete files.
"""
import sqlite3
import json
import os
import sys

PROJECT_ROOT = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(PROJECT_ROOT, "backend", "studio.db")
TOPIC_HISTORY_PATH = os.path.join(PROJECT_ROOT, "backend", "app", "content", "topic_history.json")

def cleanup_content_ledger():
    print(f"Connecting to database: {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()

    # Find test rows
    cursor.execute("SELECT topic_id, topic_title, created_at FROM content_ledger WHERE topic_id LIKE 'topic_test_%'")
    test_rows = cursor.fetchall()
    print(f"Found {len(test_rows)} test rows in content_ledger:")
    for r in test_rows:
        print(f"  {r[0]} | {r[1]} | {r[2]}")

    if test_rows:
        cursor.execute("DELETE FROM content_ledger WHERE topic_id LIKE 'topic_test_%'")
        db.commit()
        print(f"Deleted {cursor.rowcount} test rows from content_ledger.")

    # Check remaining genuine rows
    cursor.execute("SELECT topic_id, topic_title, created_at FROM content_ledger")
    remaining = cursor.fetchall()
    print(f"\nRemaining {len(remaining)} genuine rows in content_ledger:")
    for r in remaining:
        print(f"  {r[0]} | {r[1]} | {r[2]}")

    db.close()
    return [r[0] for r in remaining]

def sync_topic_history(genuine_topic_ids):
    if not os.path.exists(TOPIC_HISTORY_PATH):
        print(f"Topic history file not found: {TOPIC_HISTORY_PATH}")
        return

    with open(TOPIC_HISTORY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    original_count = len(data) if isinstance(data, list) else len(data.get("history", []))
    print(f"\nTopic history entries before sync: {original_count}")

    if isinstance(data, list):
        filtered = [item for item in data if not str(item.get("topic_id", "")).startswith("topic_test_")]
        with open(TOPIC_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2)
        print(f"Topic history entries after sync: {len(filtered)}")
    elif isinstance(data, dict) and "history" in data:
        filtered = [item for item in data["history"] if not str(item.get("topic_id", "")).startswith("topic_test_")]
        data["history"] = filtered
        with open(TOPIC_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Topic history entries after sync: {len(filtered)}")

def delete_obsolete_files():
    targets = [
        os.path.join(PROJECT_ROOT, "thumbnails", "output", "stick_sample_thumb.png"),
        os.path.join(PROJECT_ROOT, "logs", "ffmpeg_stick_render.log"),
        os.path.join(PROJECT_ROOT, "logs", "test_publishing.db"),
        os.path.join(PROJECT_ROOT, "logs", "test_stage_3_12_publishing.db"),
        os.path.join(PROJECT_ROOT, "logs", "test_notifications.jsonl"),
        os.path.join(PROJECT_ROOT, "logs", "_test_notif_dedup.jsonl"),
    ]
    print("\nChecking obsolete files for deletion:")
    for p in targets:
        if os.path.exists(p):
            size = os.path.getsize(p)
            os.remove(p)
            print(f"  DELETED: {p} ({size:,} bytes)")
        else:
            print(f"  NOT FOUND: {p}")

if __name__ == "__main__":
    genuine_ids = cleanup_content_ledger()
    sync_topic_history(genuine_ids)
    delete_obsolete_files()
