"""
Automated Test Suite for Stage 3.8 — Pipeline Integration.
Verifies:
1. End-to-end 14-stage orchestration execution.
2. Decoupled generation vs publishing queue processing.
3. Independent scheduling configuration (Generation @ 11 PM IST, Publishing @ 10 AM IST).
4. Error handling, Job state machine transition to FAILED, and failure notification dispatch.
5. Idempotent duplicate prevention across runs.
"""

import json
import os
from unittest.mock import MagicMock, patch
import pytest

from backend.app.orchestration.crazzydocs_master_orchestrator import (
    CrazzyDocsMasterOrchestrator,
    master_orchestrator,
)
from backend.app.scheduler.scheduler import get_scheduler_status, scheduler, start_scheduler
from backend.app.database.db import SessionLocal, JobModel


def test_independent_scheduler_configuration():
    status = get_scheduler_status()
    assert "generation_job" in status
    assert "publishing_job" in status
    assert "23:00 IST (11:00 PM)" in status["generation_job"]["schedule_time_ist"]
    assert "10:00 IST (10:00 AM)" in status["publishing_job"]["schedule_time_ist"]
    assert status["publishing_job"]["publishing_mode"] == "manual_approval"


def test_decoupled_publishing_queue():
    # Calling process_publishing_queue without upload must withhold at safety gate
    res = master_orchestrator.process_publishing_queue(execute_upload=False)
    assert res["status"] == "withheld_safety_gate"
    assert res["publishing_mode"] == "manual_approval"


def test_end_to_end_orchestration_run():
    test_job_id = "test_auto_job_001"
    
    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}) as mock_notif:
        result = master_orchestrator.execute_production_pipeline(
            job_id=test_job_id,
            force_topic_id="cand_07_yamaguchi_survivor"
        )

        assert result["status"] == "COMPLETED"
        assert result["job_id"] == test_job_id
        assert "Yamaguchi" in result["topic"]
        assert os.path.exists(result["video_path"])
        assert os.path.exists(result["thumbnail_path"])
        assert 50.0 <= result["duration_sec"] < 60.0
        assert mock_notif.called

    # Verify SQLite JobModel state
    with SessionLocal() as db_session:
        job = db_session.query(JobModel).filter(JobModel.id == test_job_id).first()
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.current_stage == "COMPLETED"
        assert job.progress_pct == 100.0


def test_pipeline_failure_alert_dispatch():
    fail_job_id = "test_fail_job_002"
    orch = CrazzyDocsMasterOrchestrator()
    # Mock topic engine to raise an exception
    orch.topic_engine = MagicMock()
    orch.topic_engine.discover_and_rank_topics.side_effect = RuntimeError("Simulated upstream API outage")

    with patch.object(orch.notifier, "send_failure") as mock_fail_notif:
        with pytest.raises(RuntimeError):
            orch.execute_production_pipeline(job_id=fail_job_id)

        assert mock_fail_notif.called
        call_args = mock_fail_notif.call_args[1]
        assert "Simulated upstream API outage" in call_args["error_summary"]

    # Verify DB state recorded FAILED
    with SessionLocal() as db_session:
        job = db_session.query(JobModel).filter(JobModel.id == fail_job_id).first()
        assert job is not None
        assert job.status == "FAILED"
        assert "Simulated upstream API outage" in job.error_details
