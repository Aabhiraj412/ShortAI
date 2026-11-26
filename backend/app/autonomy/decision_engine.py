"""Stage 3.36 operational decision engine: a pure function from an operational snapshot to one explained decision.

It decides whether production should proceed, which eligible job is next (and why every other candidate was
rejected), and detects bottlenecks, resource pressure, quota constraints and repeated failures, with corrective
recommendations and operator escalations. It never acts: the controller executes a decision only in the EXECUTOR
role (APPROVED_EXECUTION + standing approvals); OBSERVE records it, RECOMMEND turns escalations into proposals.

Snapshot (plain data, built by ``production_controller.gather_snapshot``; every field optional):

    now_utc                datetime (aware)
    operating_mode         "PRODUCTION" | "GAMING" | "IDLE" | "MAINTENANCE"
    production_control     "IDLE" | "RUNNING" | "PAUSED" | "STOP_REQUESTED"
    max_concurrency        int (Control Center production concurrency, 1 on this laptop)
    active_jobs            [{job_id, status, stage, heartbeat_utc, worker_alive, stalled, started_utc, deadline_utc}]
    dispatching            [job_id]  (inside run_cycle_generation in the daemon right now)
    deferred               [{cycle_id, job_id, scheduled_start_ist, recovery_deadline_ist}]  (fired by the schedule)
    job_status             {job_id: status}  (existing rows of the deferred occurrences)
    recovery_candidates    [{job_id, status, due, next_retry_utc, deadline_utc, reason}]
    already_dispatched     [job_id]  (Autopilot already dispatched this occurrence once)
    recent_terminal        [{job_id, status, classification, error}]  newest first
    pending_handoffs       int
    resources              {safe, cpu_used_percent, memory_used_percent, reason}
    disk_free_gb           float | None
    quota                  {uploads_blocked, reason, remaining_estimate, daily_limit}
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DISPATCH_SCHEDULED = "DISPATCH_SCHEDULED"
DISPATCH_RECOVERY = "DISPATCH_RECOVERY"
WAIT = "WAIT"          # something legitimate is in progress or production is paused by the operator
HOLD = "HOLD"          # unsafe to start now (resources); back off and re-evaluate
IDLE = "IDLE"          # nothing is eligible

MIN_DISK_FREE_GB = 3.0
REPEATED_FAILURE_THRESHOLD = 3
SAME_CLASS_WINDOW = 5
WINDOW_RISK_FRACTION = 0.8
FAILED_TERMINAL = ("FAILED", "BLOCKED_QC", "RECOVERY_WINDOW_EXPIRED", "MISSED")


def _dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _now(s: Dict[str, Any]) -> datetime:
    return _dt(s.get("now_utc")) or datetime.now(timezone.utc)


def _failure_class(job: Dict[str, Any]) -> str:
    err = str(job.get("error") or "").upper()
    for marker, cls in (("CHECK_A_TOPIC_NARRATION", "QC_TOPIC_NARRATION"), ("FFMPEG", "RENDERER"),
                        ("RENDER", "RENDERER"), ("TTS", "VOICEOVER"), ("VOICE", "VOICEOVER"),
                        ("DOWNLOAD", "ASSET_DOWNLOAD"), ("ASSET", "ASSET_DOWNLOAD"), ("SCRIPT", "SCRIPT_VALIDATION"),
                        ("QUOTA", "QUOTA"), ("RECOVERY WINDOW", "RECOVERY_WINDOW")):
        if marker in err:
            return cls
    if job.get("status") == "BLOCKED_QC":
        return "QUALITY_VALIDATION"
    if job.get("status") == "RECOVERY_WINDOW_EXPIRED":
        return "RECOVERY_WINDOW"
    return str(job.get("classification") or "UNCLASSIFIED")


_CORRECTIVE = {
    "QC_TOPIC_NARRATION": "Narration repeatedly misses the topic's keywords (QC check A). Review the script prompt / "
                          "narration keyword coverage; QC itself is never bypassed.",
    "QUALITY_VALIDATION": "Quality validation keeps blocking videos. Review the QC reports of the listed jobs.",
    "RENDERER": "The renderer failed repeatedly. Run the read-only diagnostics (FFmpeg filter, disk, file locks).",
    "VOICEOVER": "Voiceover generation failed repeatedly. Check the TTS provider status and rate limits.",
    "ASSET_DOWNLOAD": "Asset downloads failed repeatedly. Check provider availability (Self-Healing provider check).",
    "SCRIPT_VALIDATION": "Script validation failed repeatedly. Review the script generator's recent outputs.",
    "RECOVERY_WINDOW": "Cycles keep expiring before they finish. Check production duration against the recovery window.",
    "QUOTA": "YouTube quota keeps blocking uploads. Uploads wait for the Pacific-midnight reset; no retry is forced.",
}


def decide(s: Dict[str, Any]) -> Dict[str, Any]:
    now = _now(s)
    reasons: List[str] = []
    considered: List[Dict[str, Any]] = []
    bottlenecks: List[Dict[str, Any]] = []
    recommendations: List[Dict[str, Any]] = []
    escalations: List[Dict[str, Any]] = []
    quota_constraints: List[Dict[str, Any]] = []
    outcome, selected = IDLE, None

    # -- repeated failures (independent of what happens next) ------------------------------------------------
    recent = list(s.get("recent_terminal") or [])
    consecutive = 0
    for j in recent:
        if j.get("status") in FAILED_TERMINAL:
            consecutive += 1
        else:
            break
    classes: Dict[str, List[str]] = {}
    for j in recent[:SAME_CLASS_WINDOW]:
        if j.get("status") in FAILED_TERMINAL:
            classes.setdefault(_failure_class(j), []).append(j.get("job_id"))
    repeated = None
    if consecutive >= REPEATED_FAILURE_THRESHOLD:
        repeated = {"consecutive_failures": consecutive, "jobs": [j.get("job_id") for j in recent[:consecutive]]}
        escalations.append({"id": "repeated_failures", "severity": "high",
                            "text": f"{consecutive} consecutive productions ended without a video "
                                    f"({', '.join(repeated['jobs'][:5])}). Operator review needed."})
    for cls, ids in classes.items():
        if len(ids) >= REPEATED_FAILURE_THRESHOLD:
            recommendations.append({"id": f"failure_class:{cls}", "severity": "medium",
                                    "text": _CORRECTIVE.get(cls, f"Repeated {cls} failures: review the listed jobs."),
                                    "jobs": ids, "action_type": "report.operational.generate"})

    # -- quota ------------------------------------------------------------------------------------------------
    q = s.get("quota") or {}
    if q.get("uploads_blocked"):
        quota_constraints.append({"id": "uploads_blocked", "detail": q.get("reason") or "upload quota exhausted",
                                  "effect": "renders continue; the existing queue handoff waits for the quota reset "
                                            "(no upload retry is forced)"})
    if int(s.get("pending_handoffs") or 0) >= 3:
        bottlenecks.append({"id": "upload_handoff_backlog",
                            "detail": f"{s.get('pending_handoffs')} rendered videos wait for the publishing-queue "
                                      f"handoff"})

    # -- global gates -----------------------------------------------------------------------------------------
    mode = s.get("operating_mode") or "PRODUCTION"
    ctrl = s.get("production_control") or "IDLE"
    gate = None
    if mode != "PRODUCTION":
        gate = (WAIT, f"operating mode is {mode}; production is deferred (the scheduler would defer too)")
    elif ctrl in ("PAUSED", "STOP_REQUESTED"):
        gate = (WAIT, f"production control is {ctrl} (operator)")

    # -- single renderer ---------------------------------------------------------------------------------------
    active = list(s.get("active_jobs") or [])
    live = [j for j in active if j.get("status") in ("RUNNING", "RETRYING", "PAUSED") and j.get("worker_alive")]
    stalled = [j for j in active if j.get("status") == "RUNNING" and not j.get("worker_alive") and j.get("stalled")]
    quiet = [j for j in active if j.get("status") == "RUNNING" and not j.get("worker_alive") and not j.get("stalled")]
    maximum = max(1, int(s.get("max_concurrency") or 1))
    if len(live) > maximum:
        escalations.append({"id": "multiple_active_workers", "severity": "critical",
                            "text": f"{len(live)} production workers are active (limit {maximum}): "
                                    f"{', '.join(j['job_id'] for j in live)}"})
    for j in live:
        dl, st = _dt(j.get("deadline_utc")), _dt(j.get("started_utc"))
        if dl and st and dl > st and (now - st) >= (dl - st) * WINDOW_RISK_FRACTION:
            bottlenecks.append({"id": f"window_risk:{j['job_id']}",
                                "detail": f"{j['job_id']} is still at {j.get('stage') or '?'} with "
                                          f"{max(0, int((dl - now).total_seconds() // 60))} min of its recovery window left"})
    for j in quiet:
        bottlenecks.append({"id": f"quiet_worker:{j['job_id']}",
                            "detail": f"{j['job_id']} is RUNNING without a live worker in view but its heartbeat is "
                                      f"recent; it is NOT reclaimed until the worker is verified gone and the "
                                      f"heartbeat is stale"})
    busy = live or list(s.get("dispatching") or []) or quiet
    if gate is None and busy:
        names = [j["job_id"] for j in live] or list(s.get("dispatching") or []) or [j["job_id"] for j in quiet]
        stage = (live[0].get("stage") if live else None) or "dispatching"
        gate = (WAIT, f"single renderer: {', '.join(names)} is in progress ({stage}); the next job starts only after "
                      f"it reaches a safe state")

    # -- resources ---------------------------------------------------------------------------------------------
    res = s.get("resources") or {}
    pressure = None
    disk = s.get("disk_free_gb")
    if res and res.get("safe") is False:
        pressure = {"detail": res.get("reason"), "cpu": res.get("cpu_used_percent"), "ram": res.get("memory_used_percent")}
    if disk is not None and disk < MIN_DISK_FREE_GB:
        pressure = dict(pressure or {}, disk_free_gb=disk,
                        detail=((pressure or {}).get("detail") or "") + f" Free disk {disk:.1f} GB < {MIN_DISK_FREE_GB} GB.")
        escalations.append({"id": "disk_low", "severity": "high",
                            "text": f"Free disk space is {disk:.1f} GB; production is held until space is freed "
                                    f"(Autopilot never deletes data)."})
    if gate is None and pressure:
        gate = (HOLD, f"resource pressure: {pressure.get('detail', '').strip()} Backing off.")

    # -- candidates --------------------------------------------------------------------------------------------
    already = set(s.get("already_dispatched") or [])
    statuses = s.get("job_status") or {}
    cands: List[Dict[str, Any]] = []
    for d in s.get("deferred") or []:
        jid = d.get("job_id")
        st = statuses.get(jid)
        dl = _dt(d.get("recovery_deadline_ist"))
        c = {"kind": "scheduled", "job_id": jid, "cycle_id": d.get("cycle_id"), "status": st,
             "scheduled_start_ist": d.get("scheduled_start_ist"), "deadline": dl}
        if dl is not None and dl <= now:
            considered.append(dict(c, accepted=False, reason="recovery window expired before it could start"))
        elif jid in already:
            considered.append(dict(c, accepted=False, reason="already dispatched once by Autopilot (never twice)"))
        elif st is not None and st not in ("QUEUED",):
            considered.append(dict(c, accepted=False, reason=f"job already exists with status {st}; the recovery "
                                                             f"path handles it"))
        else:
            cands.append(c)
    for r in s.get("recovery_candidates") or []:
        dl = _dt(r.get("deadline_utc"))
        c = {"kind": "recovery", "job_id": r.get("job_id"), "status": r.get("status"), "deadline": dl,
             "reason_detail": r.get("reason")}
        if dl is not None and dl <= now:
            considered.append(dict(c, accepted=False, reason="recovery window expired (the scheduler expires it)"))
        elif not r.get("due"):
            considered.append(dict(c, accepted=False, reason=f"retry not due until {r.get('next_retry_utc') or '?'} "
                                                             f"(existing backoff)"))
        else:
            cands.append(c)
    cands.sort(key=lambda c: (c["deadline"] or datetime.max.replace(tzinfo=timezone.utc), c["kind"] != "recovery"))

    if gate is not None:
        outcome = gate[0]
        reasons.append(gate[1])
        for c in cands:
            considered.append(dict(c, accepted=False, reason=f"not now: {gate[1]}"))
    elif cands:
        selected = cands[0]
        outcome = DISPATCH_RECOVERY if selected["kind"] == "recovery" else DISPATCH_SCHEDULED
        reasons.append(f"{selected['job_id']} is eligible and has the earliest recovery deadline"
                       + (f" ({selected['deadline'].isoformat()})" if selected["deadline"] else ""))
        considered.append(dict(selected, accepted=True, reason="selected"))
        for c in cands[1:]:
            considered.append(dict(c, accepted=False, reason=f"waits for {selected['job_id']} (single renderer)"))
    else:
        reasons.append("no eligible production job: nothing fired by the schedule is waiting and no recovery is due")

    for c in considered:
        if isinstance(c.get("deadline"), datetime):
            c["deadline"] = c["deadline"].isoformat()
    if selected and isinstance(selected.get("deadline"), datetime):
        selected = dict(selected, deadline=selected["deadline"].isoformat())

    for j in stalled:
        recommendations.append({"id": f"stalled:{j['job_id']}", "severity": "medium",
                                "text": f"{j['job_id']} has no live worker (process verified gone) and a stale "
                                        f"heartbeat; the existing recovery path resumes it from its checkpoint.",
                                "action_type": None})

    decision = {
        "outcome": outcome, "proceed": outcome in (DISPATCH_SCHEDULED, DISPATCH_RECOVERY), "selected": selected,
        "considered": considered, "reasons": reasons, "bottlenecks": bottlenecks, "resource_pressure": pressure,
        "quota_constraints": quota_constraints, "repeated_failures": repeated, "recommendations": recommendations,
        "escalations": escalations,
    }
    decision["explanation"] = explain(decision)
    decision["signature"] = signature(decision)
    return decision


def explain(d: Dict[str, Any]) -> str:
    parts = [f"{d['outcome']}: " + "; ".join(d.get("reasons") or [])]
    rejected = [c for c in d.get("considered") or [] if not c.get("accepted")]
    if rejected:
        parts.append("rejected: " + "; ".join(f"{c.get('job_id')} ({c.get('reason')})" for c in rejected[:5]))
    if d.get("bottlenecks"):
        parts.append("bottlenecks: " + "; ".join(b["detail"] for b in d["bottlenecks"][:3]))
    if d.get("quota_constraints"):
        parts.append("quota: " + "; ".join(q["detail"] for q in d["quota_constraints"]))
    if d.get("escalations"):
        parts.append("escalate: " + "; ".join(e["text"] for e in d["escalations"][:3]))
    return " | ".join(parts)[:1500]


def signature(d: Dict[str, Any]) -> str:
    core = {"o": d["outcome"], "s": (d.get("selected") or {}).get("job_id"), "r": d.get("reasons"),
            "c": sorted((c.get("job_id") or "", c.get("reason") or "") for c in d.get("considered") or []),
            "b": sorted(b["id"] for b in d.get("bottlenecks") or []),
            "e": sorted(e["id"] for e in d.get("escalations") or []),
            "q": sorted(q["id"] for q in d.get("quota_constraints") or [])}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:24]
