"""
Phase C audit: production lifecycle order and independence from publishing.

START -> GENERATION -> RENDER -> QC -> PUBLISHING QUEUE INSERT -> PRODUCTION COMPLETED.
Production must not upload, schedule, wait for metadata editing, a publication time,
YouTube processing or public release.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "orchestration" / "crazzydocs_master_orchestrator.py"


def _function_source(name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} not found")


def test_pipeline_stage_order():
    body = _function_source("execute_production_pipeline")
    markers = [
        "Stage 1 & 2 & 3: Topic Discovery",           # generation
        "[STAGE 8] Calibrating expressive narration",  # generation (narration)
        "Stage 9: Video Rendering",                    # render
        "STAGE_11_QC",                                 # QC
        "BLOCKED_QC",                                  # integrity gate (terminal on failure)
        "requires_queue_handoff = (",
        "self.complete_queue_handoff(job_id)",         # queue insert -> COMPLETED
        "Stage 13: Canonical Notification",
    ]
    positions = [body.find(m) for m in markers]
    assert all(p >= 0 for p in positions), dict(zip(markers, positions))
    assert positions == sorted(positions), dict(zip(markers, positions))


def test_production_jobs_are_not_completed_before_handoff():
    body = _function_source("execute_production_pipeline")
    stage12 = body[body.find("requires_queue_handoff = ("):body.find("Stage 14 (pre-notification)")]
    # COMPLETED is only set in the non-production branch of Stage 12.
    assert 'if requires_queue_handoff:\n                            job.current_stage = "PUBLISHING_QUEUE_HANDOFF"' in stage12
    handoff = _function_source("complete_queue_handoff")
    assert handoff.find("self._insert_queue_entry(handoff)") < handoff.find('job.status = "COMPLETED"')
    assert handoff.find('"PUBLISHING_QUEUE_ENTRY_CREATED"') < handoff.find('"PRODUCTION_CYCLE_COMPLETED"')


def test_production_never_uploads_schedules_or_waits_for_user():
    forbidden = [
        "upload_short(", "edit_and_schedule(", "publish_now(", "approve_and_upload(",
        "update_or_reschedule_remote_video(", "process_publishing_queue(", "verify_scheduled_publications(",
        "scheduled_timestamp=", "wait_for_slot", "compute_next_publication_slot",
    ]
    for fn in ("execute_production_pipeline", "complete_queue_handoff", "_insert_queue_entry"):
        body = _function_source(fn)
        hits = [f for f in forbidden if f in body]
        assert hits == [] or hits == ["scheduled_timestamp="], (fn, hits)
    insert = _function_source("_insert_queue_entry")
    assert 'upload_status="pending_queue"' in insert
    assert 'visibility="private"' in insert
    assert "scheduled_timestamp=None" in insert
