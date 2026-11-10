"""
Tests the Persistent Job Manager:
- Idempotency & Duplicate Daily Prevention
- Checkpointing & Stage Resumption
- Heartbeat Stale Job Recovery
"""
import os
import sys
import time

BASE_DIR = r"D:\AI Arts\AI"
sys.path.append(os.path.join(BASE_DIR, "backend"))

from app.database.db import init_db, SessionLocal, JobModel
from app.workers.job_manager import job_manager

def test_idempotency():
    print("--- Test 1: Daily Job Idempotency ---", flush=True)
    test_date = "2026-11-10"
    job1, is_new1 = job_manager.get_or_create_daily_job(test_date)
    assert job1 is not None
    
    # Second call for same date must return existing job without creating duplicate
    job2, is_new2 = job_manager.get_or_create_daily_job(test_date)
    assert job1.id == job2.id
    assert is_new2 is False
    print(f"[PASS] Idempotency verified: Both calls returned Job {job1.id} (is_new={is_new1}, second is_new={is_new2})")

def test_checkpoint_and_resume():
    print("\n--- Test 2: Checkpointing ---", flush=True)
    test_id = f"test_check_{int(time.time())}"
    with SessionLocal() as s:
        j = JobModel(id=test_id, date_str="2026-11-10", status="PENDING")
        s.add(j)
        s.commit()
        
    job_manager.update_checkpoint(test_id, "SCRIPT", 25.0, "script", {"title": "Test Title", "word_count": 120})
    
    with SessionLocal() as s:
        j = s.query(JobModel).filter_by(id=test_id).first()
        assert j.current_stage == "SCRIPT"
        assert j.progress_pct == 25.0
        assert "Test Title" in j.checkpoint_data_json
        s.delete(j)
        s.commit()
    print("[PASS] Checkpoint persistence verified.")

def test_stale_recovery():
    print("\n--- Test 3: Stale Worker Recovery ---", flush=True)
    stale_id = f"test_stale_{int(time.time())}"
    from datetime import datetime, timedelta
    old_time = datetime.utcnow() - timedelta(minutes=10)
    
    with SessionLocal() as s:
        j = JobModel(id=stale_id, date_str="2026-11-10", status="RUNNING", heartbeat_at=old_time, retry_count=0)
        s.add(j)
        s.commit()
        
    recovered = job_manager.recover_stale_jobs()
    assert stale_id in recovered
    
    with SessionLocal() as s:
        j = s.query(JobModel).filter_by(id=stale_id).first()
        assert j.status == "RETRYING"
        assert j.retry_count == 1
        s.delete(j)
        s.commit()
    print("[PASS] Stale job recovery and retry count increment verified.")

if __name__ == "__main__":
    init_db()
    test_idempotency()
    test_checkpoint_and_resume()
    test_stale_recovery()
    print("\nALL JOB MANAGER TESTS PASSED!")
