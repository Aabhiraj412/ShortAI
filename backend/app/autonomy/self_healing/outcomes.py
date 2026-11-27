"""Outcome-driven self-healing: what actually happened to an incident, with evidence (operator-dependency reduction).

Six separate facts, never merged:

* ``diagnostic_succeeded``  a read-only diagnostic ran and its result was independently verified. This is NOT a
                             recovery and never makes an incident "recovered".
* ``recovery_approved``     an allow-listed recovery was approved (built-in policy, standing or explicit approval).
* ``recovery_executed``     that recovery ran through the control plane.
* ``recovery_verified``     its postcondition was independently VERIFIED (e.g. the staged file re-read and decoded).
* ``issue_resolved``        a verified recovery finished after the last occurrence, and the failure has not recurred.
* ``output_completed``      the affected production job reached COMPLETED (read from persistent_jobs). The scheduler's
                             own retry policy may have produced that; ``resolved_by`` says which, so a self-healing
                             action never takes credit for the scheduler's work.

``current`` separates live problems from history: an incident is current while its job is still running or its last
occurrence is recent and its output is not complete. Historical incidents are reported, never counted as blockers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

DIAG = "selfheal.diagnostic.run"
CURRENT_WINDOW = timedelta(hours=6)
JOB_ACTIVE = ("RUNNING", "QUEUED", "PENDING", "RETRY_PENDING", "RETRYING", "IN_PROGRESS")
STAGES = ("DETECTED", "DIAGNOSED", "RECOVERY_APPROVED", "RECOVERY_EXECUTED", "RECOVERY_VERIFIED", "ISSUE_RESOLVED",
          "OUTPUT_COMPLETED")
APPROVED_MODES = ("POLICY", "STANDING", "EXPLICIT")


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def assess(incident: Dict[str, Any], recoveries: List[Dict[str, Any]], job_status: Optional[str],
           now: datetime) -> Dict[str, Any]:
    evidence: List[str] = []
    diags = [r for r in recoveries if r.get("action_type") == DIAG]
    actions = [r for r in recoveries if r.get("action_type") != DIAG]
    diagnostic_ok = any(r.get("status") == "DIAGNOSED" and r.get("verification_result") == "VERIFIED" for r in diags)
    if diagnostic_ok:
        evidence.append("diagnostic completed and verified (not a recovery)")
    approved = [r for r in actions if str(r.get("approval_outcome") or "").upper() in APPROVED_MODES
                or r.get("status") in ("APPROVED", "EXECUTING", "VERIFYING", "RECOVERED")]
    if approved:
        evidence.append(f"{len(approved)} recovery action(s) approved: "
                        + ", ".join(sorted({str(r.get('approval_outcome') or r.get('status')) for r in approved})))
    executed = [r for r in actions if r.get("status") in ("RECOVERED", "FAILED", "VERIFYING")
                or r.get("started_at_utc")]
    if executed:
        evidence.append(f"{len(executed)} recovery action(s) executed: "
                        + ", ".join(sorted({str(r.get('action_type')) for r in executed})))
    last_seen = _parse(incident.get("last_seen_utc"))
    verified = [r for r in actions if r.get("status") == "RECOVERED" and r.get("verification_result") == "VERIFIED"]
    if verified:
        evidence.append(f"{len(verified)} recovery action(s) independently verified")
    resolved = False
    for r in verified:
        fin = _parse(r.get("finished_at_utc") or r.get("updated_at_utc"))
        if fin and last_seen and fin >= last_seen:
            resolved = True
            evidence.append(f"{r.get('action_type')} verified at {r.get('finished_at_utc')}; no recurrence since")
    if verified and not resolved:
        evidence.append("a verified recovery was followed by a recurrence: not resolved")
    output_completed = job_status == "COMPLETED"
    if incident.get("job_id"):
        evidence.append(f"production job {incident['job_id']}: {job_status or 'unknown'}")
    resolved_by = ("self-healing recovery" if resolved else
                   "scheduler (its own retry policy / a later attempt)" if output_completed else None)
    stage = ("OUTPUT_COMPLETED" if output_completed else "ISSUE_RESOLVED" if resolved else
             "RECOVERY_VERIFIED" if verified else "RECOVERY_EXECUTED" if executed else
             "RECOVERY_APPROVED" if approved else "DIAGNOSED" if diagnostic_ok else "DETECTED")
    recent = bool(last_seen and now - last_seen <= CURRENT_WINDOW)
    current = (job_status in JOB_ACTIVE) or (recent and not output_completed and not resolved)
    return {"diagnostic_succeeded": diagnostic_ok, "recovery_approved": bool(approved),
            "recovery_executed": bool(executed), "recovery_verified": bool(verified), "issue_resolved": resolved,
            "output_completed": output_completed, "resolved_by": resolved_by, "stage": stage, "current": current,
            "history_reason": None if current else (
                "production output completed" if output_completed else "resolved" if resolved else
                f"no occurrence for more than {int(CURRENT_WINDOW.total_seconds() // 3600)} h and the job is not active"),
            "evidence": evidence}


def summarize(assessed: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {"current": 0, "historical": 0, "by_stage": {s: 0 for s in STAGES},
           "diagnostics_succeeded": 0, "recoveries_approved": 0, "recoveries_executed": 0, "recoveries_verified": 0,
           "issues_resolved": 0, "outputs_completed": 0}
    for a in assessed:
        out["current" if a["current"] else "historical"] += 1
        out["by_stage"][a["stage"]] += 1
        out["diagnostics_succeeded"] += int(a["diagnostic_succeeded"])
        out["recoveries_approved"] += int(a.get("recovery_approved", False))
        out["recoveries_executed"] += int(a["recovery_executed"])
        out["recoveries_verified"] += int(a.get("recovery_verified", False))
        out["issues_resolved"] += int(a["issue_resolved"])
        out["outputs_completed"] += int(a["output_completed"])
    return out


def job_status_reader(db_path: str) -> Callable[[Optional[str]], Optional[str]]:
    """Read-only persistent_jobs status lookups (one connection per overview)."""
    import sqlite3
    from pathlib import Path
    cache: Dict[str, Optional[str]] = {}

    def read(job_id: Optional[str]) -> Optional[str]:
        if not job_id:
            return None
        if job_id not in cache:
            try:
                c = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
                try:
                    row = c.execute("SELECT status FROM persistent_jobs WHERE id=?", (job_id,)).fetchone()
                finally:
                    c.close()
                cache[job_id] = row[0] if row else None
            except Exception:
                cache[job_id] = None
        return cache[job_id]
    return read
