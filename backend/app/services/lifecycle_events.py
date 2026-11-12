"""
Structured Production Lifecycle Event Logger for CrazzyDocs.
Records UTC & Asia/Kolkata timestamps, cycle/job IDs, execution state, and errors.
"""
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.core.paths import DATABASE_PATH

logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
DB_PATH = str(DATABASE_PATH)


def _is_test_context() -> bool:
    return (
        os.getenv("CRAZZYDOCS_EXECUTION_CONTEXT", "").upper() == "TEST"
        or os.getenv("CRAZZYDOCS_TEST_MODE") == "1"
    )


def _resolve_db(db_path: Optional[str]) -> Optional[str]:
    """An explicit db_path wins. Without one, tests never touch the production DB."""
    if db_path:
        return db_path
    if _is_test_context():
        return None
    return DB_PATH

ALLOWED_EVENT_TYPES = {
    "CYCLE_DUE",
    "CYCLE_DISPATCH_STARTED",
    "CYCLE_JOB_CREATED",
    "CYCLE_JOB_ENQUEUED",
    "WORKER_CLAIMED",
    "WORKER_STARTED",
    "ORCHESTRATOR_STARTED",
    "PIPELINE_STAGE_CHANGED",
    "VIDEO_GENERATED",
    "QC_PASSED",
    "PUBLISHING_QUEUE_ENTRY_CREATED",
    "PRODUCTION_CYCLE_COMPLETED",
    "CYCLE_DISPATCH_FAILED",
    "WORKER_STALLED",
    "CYCLE_RECOVERED",
}


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            timestamp_ist TEXT NOT NULL,
            cycle_id TEXT,
            job_id TEXT,
            event_type TEXT NOT NULL,
            execution_state TEXT NOT NULL,
            details_json TEXT,
            error_details TEXT
        )
    """)


def record_lifecycle_event(
    event_type: str,
    execution_state: str,
    *,
    cycle_id: Optional[str] = None,
    job_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    error_details: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST_TZ)
    ts_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_ist = now_ist.isoformat()
    target_db = _resolve_db(db_path)
    if target_db is not None:
        try:
            conn = sqlite3.connect(target_db, timeout=15.0)
            try:
                with conn:
                    _ensure_table(conn)
                    conn.execute(
                        """INSERT INTO production_lifecycle_events
                           (timestamp_utc, timestamp_ist, cycle_id, job_id, event_type, execution_state, details_json, error_details)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ts_utc,
                            ts_ist,
                            cycle_id,
                            job_id,
                            event_type,
                            execution_state,
                            json.dumps(details or {}, default=str),
                            error_details,
                        ),
                    )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[LIFECYCLE_EVENT] Failed to persist %s: %s", event_type, exc)

    return {
        "timestamp_utc": ts_utc,
        "timestamp_ist": ts_ist,
        "cycle_id": cycle_id,
        "job_id": job_id,
        "event_type": event_type,
        "execution_state": execution_state,
        "details": details or {},
        "error_details": error_details,
    }


def list_lifecycle_events(
    limit: int = 50,
    cycle_id: Optional[str] = None,
    job_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    target_db = _resolve_db(db_path)
    if target_db is None:
        return []
    try:
        conn = sqlite3.connect(target_db, timeout=15.0)
        try:
            _ensure_table(conn)
            query = (
                "SELECT id, timestamp_utc, timestamp_ist, cycle_id, job_id, event_type, "
                "execution_state, details_json, error_details FROM production_lifecycle_events "
            )
            clauses = []
            params: List[Any] = []
            if cycle_id:
                clauses.append("cycle_id = ?")
                params.append(cycle_id)
            if job_id:
                clauses.append("job_id = ?")
                params.append(job_id)
            if clauses:
                query += "WHERE " + " AND ".join(clauses) + " "
            query += "ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    return [
        {
            "id": r[0],
            "timestamp_utc": r[1],
            "timestamp_ist": r[2],
            "cycle_id": r[3],
            "job_id": r[4],
            "event_type": r[5],
            "execution_state": r[6],
            "details": json.loads(r[7] or "{}"),
            "error_details": r[8],
        }
        for r in rows
    ]
