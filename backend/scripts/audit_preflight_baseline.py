"""
Pre-Flight and Baseline Audit Script for Stage 3.18.
Examines actual production state across:
1. Scheduled task registration & enabled state
2. Scheduler process & heartbeat
3. Production database & jobs
4. Publishing ledger & YouTube OAuth state
5. Recovery state
6. Storage consumption
7. Pending email notifications & QC failures
"""

import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WORKSPACE = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE, "backend", "studio.db")
TOKEN_PATH = os.path.join(WORKSPACE, "backend", "token.json")
LOCK_PATH = os.path.join(WORKSPACE, "backend", "studio_scheduler.lock")
IST_TZ = ZoneInfo("Asia/Kolkata")


def check_windows_task():
    task_name = "CrazzyDocsStudioScheduler"
    try:
        res = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            info = {}
            for line in lines:
                if ":" in line:
                    parts = line.split(":", 1)
                    k, v = parts[0].strip(), parts[1].strip()
                    if k in ("TaskName", "Status", "Scheduled Task State", "Next Run Time", "Last Run Time", "Author"):
                        info[k] = v
            return True, info
        else:
            return False, {"error": res.stderr.strip() or "Task not registered"}
    except Exception as e:
        return False, {"exception": str(e)}


def check_scheduler_process():
    if not os.path.exists(LOCK_PATH):
        return False, "Lockfile not present (scheduler idle)"
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pid = data.get("pid")
        if not pid:
            return False, "Lockfile present but invalid PID"
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True, f"Scheduler RUNNING (PID {pid}, acquired at {data.get('acquired_at')})"
        else:
            return False, f"Stale lockfile (PID {pid} not running)"
    except Exception as e:
        return False, f"Lock read error: {e}"


def check_oauth_token():
    if not os.path.exists(TOKEN_PATH):
        return False, "token.json missing"
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            tok = json.load(f)
        has_refresh = bool(tok.get("refresh_token"))
        has_token = bool(tok.get("token"))
        expiry = tok.get("expiry")
        return True, {
            "has_access_token": has_token,
            "has_refresh_token": has_refresh,
            "expiry": expiry,
            "scopes": tok.get("scopes", []),
        }
    except Exception as e:
        return False, f"Token read error: {e}"


def audit_db():
    if not os.path.exists(DB_PATH):
        return {"error": "studio.db missing"}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]

    db_stats = {"tables": tables}

    if "jobs" in tables:
        cur.execute("SELECT count(*) FROM jobs;")
        db_stats["total_jobs"] = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM jobs WHERE status = 'COMPLETED';")
        db_stats["completed_jobs"] = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM jobs WHERE status = 'BLOCKED_QC' OR status LIKE '%FAILED%';")
        db_stats["failed_or_blocked_qc"] = cur.fetchone()[0]

        cur.execute("SELECT id, topic_title, status, current_stage, actual_start_at, completed_at FROM jobs ORDER BY actual_start_at DESC LIMIT 3;")
        db_stats["recent_jobs"] = [
            {"id": r[0], "topic": r[1], "status": r[2], "stage": r[3], "start": r[4], "done": r[5]}
            for r in cur.fetchall()
        ]

    if "publishing_ledger" in tables:
        cur.execute("SELECT count(*) FROM publishing_ledger;")
        db_stats["total_publications"] = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM publishing_ledger WHERE visibility = 'public';")
        db_stats["public_publications"] = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM publishing_ledger WHERE visibility = 'private';")
        db_stats["private_publications"] = cur.fetchone()[0]

        cur.execute("SELECT id, topic_title, upload_status, visibility, youtube_video_id, upload_timestamp FROM publishing_ledger ORDER BY id DESC LIMIT 3;")
        db_stats["recent_publishing"] = [
            {"id": r[0], "topic": r[1], "status": r[2], "visibility": r[3], "video_id": r[4], "time": r[5]}
            for r in cur.fetchall()
        ]

    if "persistent_notifications" in tables:
        cur.execute("SELECT count(*) FROM persistent_notifications WHERE status = 'PENDING' OR status = 'FAILED';")
        db_stats["pending_failed_notifications"] = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM persistent_notifications WHERE status = 'SENT';")
        db_stats["sent_notifications"] = cur.fetchone()[0]

    conn.close()
    return db_stats


def check_storage():
    media_dir = os.path.join(WORKSPACE, "media")
    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk(media_dir):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except Exception:
                pass
    drive = WORKSPACE[:2]
    usage = shutil.disk_usage(drive)
    return {
        "media_storage_bytes": total_size,
        "media_storage_mb": round(total_size / (1024 * 1024), 2),
        "media_file_count": file_count,
        "drive_free_gb": round(usage.free / (1024**3), 2),
        "drive_total_gb": round(usage.total / (1024**3), 2),
    }


def main():
    print("=" * 60)
    print("CRAZZYDOCS PRE-FLIGHT AND BASELINE AUDIT (STAGE 3.18)")
    print("=" * 60)
    now_ist = datetime.now(IST_TZ)
    print(f"Audit Timestamp: {now_ist.isoformat()} (Asia/Kolkata)")
    print(f"Project Root: {WORKSPACE}")
    print()

    # 1. Scheduled Task
    has_task, task_info = check_windows_task()
    print("[1] WINDOWS TASK SCHEDULER:")
    print(f"    Registered: {'YES' if has_task else 'NO'}")
    if has_task:
        for k, v in task_info.items():
            print(f"    - {k}: {v}")
    else:
        print(f"    Details: {task_info}")
    print()

    # 2. Scheduler Process
    is_running, sched_msg = check_scheduler_process()
    print("[2] SCHEDULER PROCESS & LOCK:")
    print(f"    Running: {'YES' if is_running else 'NO'}")
    print(f"    Status: {sched_msg}")
    print()

    # 3. OAuth State
    has_tok, tok_info = check_oauth_token()
    print("[3] YOUTUBE OAUTH CREDENTIALS:")
    print(f"    Present: {'YES' if has_tok else 'NO'}")
    if has_tok:
        print(f"    - Has Access Token: {tok_info['has_access_token']}")
        print(f"    - Has Refresh Token: {tok_info['has_refresh_token']}")
        print(f"    - Expiry: {tok_info['expiry']}")
    else:
        print(f"    Details: {tok_info}")
    print()

    # 4. Database & Ledger
    db_data = audit_db()
    print("[4] PRODUCTION DATABASE & PUBLISHING LEDGER:")
    for k, v in db_data.items():
        if k not in ("recent_jobs", "recent_publishing"):
            print(f"    - {k}: {v}")
    if "recent_jobs" in db_data:
        print("    Recent Jobs:")
        for j in db_data["recent_jobs"]:
            print(f"      * [{j['id']}] {j['status']} | {j['topic']} | Start: {j['start']}")
    if "recent_publishing" in db_data:
        print("    Recent Publications:")
        for p in db_data["recent_publishing"]:
            print(f"      * [{p['id']}] {p['visibility']} | {p['status']} | ID: {p['video_id']} | {p['topic']}")
    print()

    # 5. Storage
    stor = check_storage()
    print("[5] STORAGE CONSUMPTION:")
    print(f"    Media Directory: {stor['media_storage_mb']} MB ({stor['media_file_count']} files)")
    print(f"    Drive Free Space: {stor['drive_free_gb']} GB / {stor['drive_total_gb']} GB")
    print("=" * 60)


if __name__ == "__main__":
    main()
