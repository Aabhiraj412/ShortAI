"""Read-only access to the production evidence self-healing needs.

* Failure occurrences come from Stage 3.32A (``OperationalIntelligence.bundle`` + ``_failures``): the same rows, the
  same normalization, the same cache. Nothing is re-derived.
* Per-job detail (status, checkpoint recovery state, attempt history, lifecycle events, renderer, progress record)
  is read through a ``mode=ro`` / ``query_only`` SQLite connection and the production progress store. Bounded.

Nothing here writes anything, opens production media files or touches the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.app.autonomy.self_healing import classification as cls_mod

MAX_EVENTS_PER_JOB = 40
ACTIVE_STATUSES = ("RUNNING", "RETRYING")
SCHEDULER_RECOVERY_STATUSES = ("QUEUED", "RETRY_SCHEDULED", "HANDOFF_PENDING", "RETRYING", "PAUSED", "PENDING")
CHECKPOINT_KEYS = ("recovery_expiration_at", "recovery_start_at", "production_start_at", "worker_id", "error_classification",
                   "last_error_type", "last_failure_at_utc", "retry_terminal", "requires_change", "retry_policy",
                   "next_retry_at_utc", "recovery_attempt_count", "current_pipeline_stage", "final_cycle_status",
                   "failure_code_revision")


def _parse(ts: Any) -> Optional[datetime]:
    from backend.app.services.operational_intelligence import _parse_ts
    return _parse_ts(ts)


class EvidenceSource:
    def __init__(self, intelligence: Callable[[], Any], db_path: str):
        self.intelligence = intelligence
        self.db_path = str(db_path)

    # -- failures ---------------------------------------------------------------------------------------------
    def failures(self, days: int) -> Dict[str, Any]:
        """{"failures": [...], "jobs": {id: status}, "available": bool, "error": str|None}. Each failure carries its
        self-healing signature and whether the job later completed (``recovered_later``)."""
        try:
            oi = self.intelligence()
            bundle = oi.bundle(days)
            if not bundle.get("db_available"):
                return {"failures": [], "jobs": {}, "available": False, "error": bundle.get("db_error") or "database unavailable"}
            rows = oi._failures(bundle)
        except Exception as exc:
            return {"failures": [], "jobs": {}, "available": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        status = {j["id"]: j.get("status") for j in bundle.get("jobs", [])}
        out = []
        for f in rows:
            msg = f.get("message") or ""
            out.append({**f, "signature": cls_mod.signature_of(msg, f.get("cause") or ""),
                        "recovered_later": status.get(f.get("job_id")) == "COMPLETED"})
        return {"failures": out, "jobs": status, "available": True, "error": None}

    # -- per-job detail ---------------------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def job(self, job_id: Optional[str]) -> Dict[str, Any]:
        """Job state for planning and diagnosis. Missing pieces are listed in ``missing`` (never invented)."""
        out: Dict[str, Any] = {"job_id": job_id, "found": False, "missing": []}
        if not job_id:
            out["missing"].append("job id (failure not linked to a job)")
            return out
        try:
            conn = self._connect()
        except Exception as exc:
            out["missing"].append(f"job record ({type(exc).__name__})")
            return out
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "persistent_jobs" in tables:
                row = conn.execute("SELECT id, status, current_stage, retry_count, topic_title, heartbeat_at, error_details, "
                                   "checkpoint_data_json, created_at, updated_at, completed_at, recovery_reason "
                                   "FROM persistent_jobs WHERE id=?", (job_id,)).fetchone()
            else:
                row = None
            if row is None:
                out["missing"].append("job record")
            else:
                d = dict(row)
                try:
                    cp = json.loads(d.pop("checkpoint_data_json") or "{}")
                except ValueError:
                    cp = {}
                    out["missing"].append("checkpoint (unparseable)")
                cs = cp.get("cycle_state") or {}
                out.update(found=True, status=d["status"], stage=d["current_stage"], retry_count=d["retry_count"],
                           topic_title=d["topic_title"], heartbeat_at=d["heartbeat_at"],
                           error_details=(d["error_details"] or "")[:4000], created_at=d["created_at"],
                           updated_at=d["updated_at"], completed_at=d["completed_at"], recovery_reason=d["recovery_reason"],
                           cycle_state={k: cs.get(k) for k in CHECKPOINT_KEYS if k in cs},
                           attempt_history=[{k: a.get(k) for k in ("attempt", "kind", "failed_at_utc", "classification",
                                                                   "error_type", "message", "outcome", "code_revision")}
                                            for a in (cs.get("attempt_history") or [])[-20:] if isinstance(a, dict)],
                           failure_history=[{"reason": str(h.get("reason", ""))[:600], "timestamp": h.get("timestamp")}
                                            for h in (cs.get("failure_history") or [])[-20:] if isinstance(h, dict)],
                           state_transitions=[t for t in (cs.get("state_transitions") or [])[-20:] if isinstance(t, dict)])
                if not cs.get("recovery_expiration_at"):
                    out["missing"].append("recovery deadline (not recorded in the checkpoint)")
            if "production_lifecycle_events" in tables:
                ev = conn.execute("SELECT timestamp_utc, event_type, details_json, error_details FROM production_lifecycle_events "
                                  "WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, MAX_EVENTS_PER_JOB)).fetchall()
                out["events"] = [{"at": e["timestamp_utc"], "type": e["event_type"],
                                  "details": (e["details_json"] or "")[:300], "error": (e["error_details"] or "")[:300]}
                                 for e in reversed(ev)]
            else:
                out["missing"].append("lifecycle events")
            if "creative_identity_registry" in tables:
                r = conn.execute("SELECT visual_engine, renderer_config_json, script_source, category FROM creative_identity_registry "
                                 "WHERE job_id=? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
                if r:
                    try:
                        rc = json.loads(r["renderer_config_json"] or "{}")
                    except ValueError:
                        rc = {}
                    out["renderer"] = {"visual_engine": r["visual_engine"], "script_source": r["script_source"],
                                       "category": r["category"],
                                       "config": {k: rc.get(k) for k in list(rc)[:12]} if isinstance(rc, dict) else {}}
                else:
                    out["missing"].append("renderer record")
        except sqlite3.Error as exc:
            out["missing"].append(f"job detail ({type(exc).__name__})")
        finally:
            conn.close()
        out["progress"] = self.progress(job_id)
        if out["progress"] is None:
            out["missing"].append("production progress record")
        return out

    def progress(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            store = self.intelligence()._progress_store()
        except Exception:
            return None
        rec = ((store or {}).get("productions") or {}).get(job_id)
        if not isinstance(rec, dict):
            return None
        err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
        return {"overall_status": rec.get("overall_status"), "current_stage": rec.get("current_stage"),
                "assets": rec.get("assets") if isinstance(rec.get("assets"), dict) else None,
                "rendering": rec.get("rendering") if isinstance(rec.get("rendering"), dict) else None,
                "error": {"stage": err.get("stage"), "summary": str(err.get("summary") or "")[:3000],
                          "retry_possible": err.get("retry_possible"), "safe_to_resume": err.get("safe_to_resume")}
                if err else None,
                "event_log_path": rec.get("event_log_path"),
                "last_updated": rec.get("last_updated")}

    @staticmethod
    def worker_active(job: Dict[str, Any], now: datetime, heartbeat_seconds: int = 300) -> Dict[str, Any]:
        """Evidence that a worker is executing the job now: the job is RUNNING/RETRYING, or its heartbeat is fresh."""
        status = job.get("status")
        hb = _parse(job.get("heartbeat_at"))
        fresh = bool(hb and (now - hb).total_seconds() <= heartbeat_seconds)
        return {"active": status in ACTIVE_STATUSES or (fresh and status not in ("COMPLETED", "FAILED", "RECOVERY_WINDOW_EXPIRED")),
                "status": status, "heartbeat_at": job.get("heartbeat_at"), "heartbeat_fresh": fresh}

    @staticmethod
    def recovery_window(job: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        raw = (job.get("cycle_state") or {}).get("recovery_expiration_at")
        dl = _parse(raw)
        if dl is None:
            return {"known": False, "inside": None, "deadline": raw, "minutes_left": None}
        left = (dl - now).total_seconds() / 60.0
        return {"known": True, "inside": left > 0, "deadline": dl.isoformat(), "minutes_left": round(left, 1)}
