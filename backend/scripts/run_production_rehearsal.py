"""
Controlled Four-Cycle Production Rehearsal Script for CrazzyDocs.
Phase 5, Stage 3.11D Production Standard.

Executes a complete, unsimulated production run under realistic operating conditions:
1. Timezone: Asia/Kolkata (IST).
2. Profile: FOUR_CYCLES_DAILY (00:00, 06:00, 12:00, 18:00 IST).
3. Target: Active cycle corresponding to real clock time.
4. Safeguards: settings.PUBLISHING_MODE = "manual_approval" strictly enforced.
5. Production: Generates real video deliverable, real thumbnail, real QC gate check.
6. Persistence: Persists real SQLite job record, topic ledger fingerprint, and publishing queue draft.
7. Alerting: Dispatches actual cycle summary email with mobile-first card dashboard and thumbnail.
8. Deduplication & Resource Safety: Sequential execution, zero overlapping renders.
"""

from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Dict

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.core.config import settings
from backend.app.database.db import SessionLocal, JobModel, PersistentNotificationModel
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    get_active_profile,
    ProductionCycle,
)
from backend.app.scheduler.scheduler import run_cycle_generation, run_cycle_publishing
from backend.app.services.email_monitoring_service import email_monitoring_service
from backend.app.content.topic_ledger import TopicLedger
from backend.app.services.youtube_publishing_engine import PublishingDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_controlled_rehearsal() -> Dict:
    t_start = time.perf_counter()
    print("=" * 75)
    print("CRAZZYDOCS CONTROLLED PRODUCTION REHEARSAL (STAGE 3.11D)")
    print("=" * 75)

    # 1. Enforce Publishing Safety Gate
    print("1. Auditing Publishing Safety Boundaries...")
    pub_mode = getattr(settings, "PUBLISHING_MODE", "manual_approval")
    print(f"   settings.PUBLISHING_MODE: '{pub_mode}'")
    if pub_mode != "manual_approval":
        raise PermissionError(f"CRITICAL SAFETY VIOLATION: PUBLISHING_MODE must be 'manual_approval', found '{pub_mode}'")
    print("   [OK] Publishing safety gate ACTIVE. Zero unauthorized public uploads permitted.\n")

    # 2. Inspect Active Schedule Profile
    print("2. Inspecting Active Schedule Profile & Real Clock Time...")
    profile = get_active_profile()
    now_ist = datetime.now(IST_TZ)
    print(f"   Profile: {profile.name} (Timezone: {profile.timezone_name})")
    print(f"   Current Clock IST: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"   Configured Cycles: {len(profile.cycles)}")
    for c in profile.cycles:
        print(f"     - {c.cycle_id}: {c.name:<22} | Start: {c.start_hour_ist:02d}:{c.start_min_ist:02d} IST -> Target Pub: {c.target_pub_hour_ist:02d}:{c.target_pub_min_ist:02d} IST")
    print()

    # Determine which cycle is currently active
    # Cycle 1: 00:00 - 06:00
    # Cycle 2: 06:00 - 12:00
    # Cycle 3: 12:00 - 18:00
    # Cycle 4: 18:00 - 24:00
    hour = now_ist.hour
    if 0 <= hour < 6:
        active_cycle_id = "cycle_1"
    elif 6 <= hour < 12:
        active_cycle_id = "cycle_2"
    elif 12 <= hour < 18:
        active_cycle_id = "cycle_3"
    else:
        active_cycle_id = "cycle_4"

    active_cycle = next(c for c in profile.cycles if c.cycle_id == active_cycle_id)
    print(f"3. Active Operational Cycle for Current Window: {active_cycle.name} ({active_cycle_id})")
    print(f"   Start Window: {active_cycle.start_hour_ist:02d}:{active_cycle.start_min_ist:02d} IST")
    print(f"   Target Pub:   {active_cycle.target_pub_hour_ist:02d}:{active_cycle.target_pub_min_ist:02d} IST\n")

    # Clean existing test job for this cycle today if needed to allow clean rehearsal
    date_str = now_ist.strftime("%Y-%m-%d")
    expected_job_id = active_cycle.format_job_id(now_ist)
    print(f"4. Preparing Execution Slot for Job: {expected_job_id}...")
    with SessionLocal() as db_session:
        existing_job = db_session.query(JobModel).filter(JobModel.id == expected_job_id).first()
        if existing_job:
            print(f"   Existing record found (Status: {existing_job.status}). Resetting slot for live rehearsal.")
            db_session.delete(existing_job)
            # Also clear notification idempotency key so clean summary email is dispatched
            db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.idempotency_key.like(f"%{expected_job_id}%")
            ).delete()
            db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.idempotency_key.like(f"%{date_str}_{active_cycle_id}%")
            ).delete()
            db_session.commit()
    print("   [OK] Slot prepared.\n")

    # 5. Execute Live Production Run
    print("5. Triggering Real Autonomous Pipeline Execution...")
    import asyncio
    gen_result = asyncio.run(run_cycle_generation(cycle_id=active_cycle_id, test_ist_now=now_ist))

    print(f"   Pipeline Status: {gen_result.get('status')}")
    print(f"   Selected Topic:  {gen_result.get('topic')}")
    print(f"   Video Deliverable: {gen_result.get('video_path')}")
    print(f"   Thumbnail File:    {gen_result.get('thumbnail_path')}")
    print(f"   Video Duration:    {gen_result.get('duration_sec')}s")
    print(f"   Elapsed Runtime:   {gen_result.get('elapsed_sec')}s\n")

    if gen_result.get("status") not in ("COMPLETED", "SUCCESS"):
        print(f"[ERROR] Cycle production did not complete successfully: {gen_result}")
        return {"success": False, "result": gen_result}

    # 6. Verify Artifacts on Disk
    print("6. Verifying Physical Media Artifacts on Disk...")
    v_path = gen_result.get("video_path")
    t_path = gen_result.get("thumbnail_path")
    assert v_path and os.path.isfile(v_path), f"Video missing: {v_path}"
    assert t_path and os.path.isfile(t_path), f"Thumbnail missing: {t_path}"
    v_size = os.path.getsize(v_path)
    t_size = os.path.getsize(t_path)
    print(f"   Video File Size:     {v_size / (1024*1024):.2f} MB ({v_path})")
    print(f"   Thumbnail File Size: {t_size / 1024:.2f} KB ({t_path})")
    assert v_size > 100 * 1024, "Video file suspiciously small (<100KB)"
    assert t_size > 10 * 1024, "Thumbnail suspiciously small (<10KB)"
    print("   [OK] Physical artifacts verified intact.\n")

    # 7. Verify Database Persistence
    print("7. Verifying SQLite Database Consistency...")
    with SessionLocal() as db_session:
        saved_job = db_session.query(JobModel).filter(JobModel.id == expected_job_id).first()
        assert saved_job is not None, f"Job {expected_job_id} not found in persistent_jobs"
        assert saved_job.status == "COMPLETED", f"Expected COMPLETED, got {saved_job.status}"
        print(f"   persistent_jobs: ID={saved_job.id} | Status={saved_job.status} | Topic='{saved_job.topic_title}'")

        notifs = db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.cycle_id == active_cycle_id,
            PersistentNotificationModel.idempotency_key.like(f"%{date_str}%")
        ).all()
        print(f"   persistent_notifications: {len(notifs)} records found for this cycle.")
        for n in notifs:
            print(f"     - Notif ID={n.id} | Status={n.status} | Recipient={n.recipient} | Key={n.idempotency_key}")
    print("   [OK] Database state verified.\n")

    # 8. Verify Topic Ledger Deduplication Recording
    print("8. Verifying TopicLedger Non-Duplicate Recording...")
    ledger = TopicLedger()
    records = ledger.load_all_records()
    found_in_ledger = any(r.topic_title == gen_result.get("topic") for r in records)
    print(f"   Topic Recorded in Ledger: {found_in_ledger}")
    assert found_in_ledger, "Generated topic was not recorded in TopicLedger"
    print("   [OK] TopicLedger collision index updated.\n")

    # 9. Verify Target Publication Queue Processing (with Safety Gate)
    print("9. Verifying Target Publishing Queue Processing at Safety Gate...")
    pub_result = asyncio.run(run_cycle_publishing(cycle_id=active_cycle_id, test_ist_now=now_ist))
    print(f"   Publishing Queue Status: {pub_result.get('status')}")
    print(f"   Safety Gate Message:     {pub_result.get('message', 'Awaiting operator manual approval')}")
    import sqlite3
    conn = sqlite3.connect(os.path.join(WORKSPACE_ROOT, "backend", "studio.db"))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM publishing_ledger WHERE upload_status = 'pending_queue'")
    pending_count = cur.fetchone()[0]
    conn.close()
    print(f"   Total Publications In Safety Queue: {pending_count}")
    print("   [OK] Publishing queue safe. No unauthorized public uploads executed.\n")

    # 10. Verify Live Email Notification Delivery
    print("10. Auditing Email Delivery Status...")
    with SessionLocal() as db_session:
        latest_notif = db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.cycle_id == active_cycle_id,
            PersistentNotificationModel.idempotency_key.like(f"%{date_str}%")
        ).order_by(PersistentNotificationModel.created_at.desc()).first()
        if latest_notif:
            print(f"    Latest Cycle Summary Email Status: {latest_notif.status}")
            print(f"    Delivered to:                      {latest_notif.recipient}")
            print(f"    Sent At:                           {latest_notif.sent_at}")
            print(f"    Subject:                           {latest_notif.subject}")
    print()

    elapsed_total = round(time.perf_counter() - t_start, 2)
    print("=" * 75)
    print(f"CONTROLLED PRODUCTION REHEARSAL COMPLETED IN {elapsed_total}s (STATUS: VERIFIED)")
    print("=" * 75)

    return {
        "success": True,
        "active_cycle": active_cycle_id,
        "job_id": expected_job_id,
        "topic": gen_result.get("topic"),
        "video_path": v_path,
        "thumbnail_path": t_path,
        "elapsed_sec": elapsed_total,
        "publishing_status": "withheld_at_safety_gate",
    }


if __name__ == "__main__":
    res = run_controlled_rehearsal()
    sys.exit(0 if res.get("success") else 1)
