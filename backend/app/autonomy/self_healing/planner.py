"""Recovery planner: decides, for one diagnosed incident, what (if anything) may be proposed.

It answers, from recorded state only:
  is recovery possible? is it safe? transient or deterministic? would another attempt help? is the job inside its
  recovery window? is a worker executing it? is it already being recovered (by the existing scheduler or by
  self-healing)? is a resource lock held? which resources are needed? what does it cost? does it need approval?

It never schedules anything. Retrying, resuming or re-rendering a job belongs to the existing scheduler / retry policy
and the operator; such steps appear only as *operator-only* items naming the prohibited registry entry.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import payload_hash
from backend.app.autonomy.self_healing import classification as C
from backend.app.autonomy.self_healing.evidence import SCHEDULER_RECOVERY_STATUSES, EvidenceSource

DIAG = "selfheal.diagnostic.run"
PROVIDER_RETRY = "selfheal.provider.retry_readonly"
REACQUIRE = "selfheal.asset.reacquire_staged"
MAX_DIAGNOSTICS = 3
DIAGNOSTIC_REUSE_HOURS = 24
MAX_RECOVERIES_PER_INCIDENT = 6          # proposals of any kind, ever, for one incident
MAX_FAILED_REVERSIBLE_PER_INCIDENT = 2   # failed recovery actions before the planner stops proposing them

ALLOWED_ASSET_HOSTS = ("upload.wikimedia.org", "commons.wikimedia.org", "images-assets.nasa.gov", "images-api.nasa.gov",
                       "archive.org", "api.openverse.org", "live.staticflickr.com", "farm1.staticflickr.com",
                       "farm2.staticflickr.com", "farm3.staticflickr.com", "farm4.staticflickr.com",
                       "farm5.staticflickr.com", "farm6.staticflickr.com", "farm7.staticflickr.com",
                       "farm8.staticflickr.com", "farm9.staticflickr.com")
PROVIDER_WORDS = {"openverse": "Openverse", "wikimedia": "Wikimedia Commons", "commons": "Wikimedia Commons",
                  "nasa": "NASA Image Library", "internet archive": "Internet Archive", "archive.org": "Internet Archive"}
URL_RX = re.compile(r"https://[^\s<>\"'\\)\]]{4,490}")
QUERY_RX = re.compile(r"[^A-Za-z0-9 ,.'()&:-]")

DIAGNOSTICS_BY_SUBTYPE = {
    "ffmpeg_filter": ("validate_ffmpeg_filter", "collect_log_context"),
    "media_dimensions": ("validate_ffmpeg_filter", "inspect_media_metadata"),
    "file_lock": ("inspect_file_locks", "collect_log_context"),
    "disk": ("check_disk_space",),
    "memory": ("collect_log_context",),
    "file_handles": ("collect_log_context",),
    "corrupt_media": ("inspect_media_metadata", "collect_log_context"),
    "missing_asset": ("inspect_media_metadata", "collect_log_context"),
    "metadata": ("collect_log_context",),
}
COSTS = {
    DIAG: {"seconds": 5, "network_requests": 0, "youtube_quota_units": 0, "gemini_calls": 0, "disk_mb": 1},
    PROVIDER_RETRY: {"seconds": 20, "network_requests": 1, "youtube_quota_units": 0, "gemini_calls": 0, "disk_mb": 0},
    REACQUIRE: {"seconds": 30, "network_requests": 1, "youtube_quota_units": 0, "gemini_calls": 0, "disk_mb": 25},
}
RESOURCES = {
    DIAG: ["local read-only file access", "database read"],
    PROVIDER_RETRY: ["network (one read-only HTTPS request)", "provider rate budget"],
    REACQUIRE: ["network (one HTTPS download, max 25 MB)", "disk (staging folder)"],
}


def resource_keys(incident: Dict[str, Any], action_type: str, payload: Dict[str, Any]) -> List[str]:
    """Locks an execution must hold. Every action locks its job (or incident); recovery actions also lock the
    specific external resource, so two executions never work on the same resource at once."""
    keys = [f"job:{incident['job_id']}" if incident.get("job_id") else f"incident:{incident['id']}"]
    if action_type == PROVIDER_RETRY:
        keys.append(f"provider:{payload.get('provider')}")
    elif action_type == REACQUIRE:
        keys.append("asset:" + hashlib.sha256(str(payload.get("asset_url")).encode()).hexdigest()[:24])
    return keys


def evidence_text(diagnosis: Dict[str, Any], job: Dict[str, Any]) -> str:
    ev = (diagnosis or {}).get("evidence") or {}
    parts = [ev.get("error_message") or "", job.get("error_details") or ""]
    parts += [h.get("reason") or "" for h in job.get("failure_history") or []]
    parts += [ln.get("text") or "" for ln in ev.get("log_context") or []]
    parts += [((job.get("progress") or {}).get("error") or {}).get("summary") or ""]
    return "\n".join(parts)


def allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and not p.username and not p.password and (p.port in (None, 443)) and any(
        host == h or host.endswith("." + h) for h in ALLOWED_ASSET_HOSTS)


def urls_in_evidence(text: str) -> List[str]:
    out = []
    for m in URL_RX.finditer(text or ""):
        u = m.group(0).rstrip(".,;:")
        if allowed_url(u) and u not in out:
            out.append(u)
    return out[:3]


def provider_in(text: str) -> Optional[str]:
    low = (text or "").lower()
    for word, name in PROVIDER_WORDS.items():
        if word in low:
            return name
    return None


def clean_query(text: Optional[str]) -> Optional[str]:
    q = re.sub(r"\s+", " ", QUERY_RX.sub(" ", text or "")).strip()[:100].strip()
    return q if len(q) >= 2 else None


def _ineffective(learning: Sequence[Dict[str, Any]], signature: str, action_type: str) -> Optional[str]:
    for row in learning:
        if row.get("signature") != signature or row.get("action_type") != action_type:
            continue
        attempts, ok = int(row.get("attempts") or 0), int(row.get("succeeded") or 0)
        recurred = int(row.get("recurred_after_success") or 0)
        if attempts >= 2 and ok == 0:
            return f"learning: {action_type} failed on all {attempts} earlier attempts for this signature"
        if action_type != DIAG and ok >= 2 and recurred >= ok:
            return (f"learning: {action_type} verified {ok} times for this signature but the failure came back each "
                    "time; it is no longer recommended")
    return None


def _operator_only(cl: Dict[str, Any], window: Dict[str, Any], job: Dict[str, Any]) -> List[Dict[str, Any]]:
    k, sub = cl.get("failure_class"), cl.get("subtype")
    items: List[Dict[str, Any]] = []

    def add(prohibited: str, text: str, why: str) -> None:
        spec = registry.REGISTRY.get(prohibited)
        items.append({"prohibited_action_type": prohibited, "allowed_for_autonomy": False,
                      "registry_description": spec.description if spec else None, "suggestion": text, "why": why})

    if k == C.DETERMINISTIC:
        add("recovery.job.retry", "Do not retry this job unchanged: the same inputs and code fail the same way.",
            "Deterministic failure; a retry would repeat it.")
        if sub in ("ffmpeg_filter", "media_dimensions"):
            add("render.video.rerender", "A code change to how the filter dimensions are computed is needed before "
                "re-rendering; review the diagnostic findings.", "Re-rendering is high-impact and operator-only.")
        elif sub == "qc":
            add("quality_checks.bypass", "Review script length / pacing; the QC rule stays unchanged.", "QC is never bypassed.")
        elif sub == "integrity_gate":
            add("duplicate_detection.bypass", "Widen the topic pool; duplicate detection stays unchanged.",
                "Duplicate detection is never bypassed.")
    elif k == C.TRANSIENT:
        if window.get("inside") and job.get("status") in ("FAILED",):
            add("recovery.job.retry", "If the provider has recovered, the operator may request a retry through the "
                "existing operator retry flow.", "Job retries are operator / scheduler decisions.")
    elif k == C.RESOURCE:
        if sub == "disk":
            add("production.data.delete", "Free disk space (operator); self-healing never deletes production data.",
                "Deleting production data is prohibited.")
        elif sub == "external_quota":
            add("production.schedule.change", "Wait for the quota reset; a quota-extension request needs operator approval.",
                "Schedules and quota requests are operator-only.")
        elif sub == "file_lock":
            add("render.worker.restart", "If the lock recurs, identify the process holding the file; workers are never "
                "restarted automatically.", "Worker restarts are prohibited.")
    elif k == C.CONFIGURATION:
        if sub == "oauth_scope":
            add("oauth.scopes.change", "Review whether the workflow needs the scope; OAuth scopes are never changed "
                "automatically.", "OAuth changes are prohibited.")
        else:
            add("production.config.modify", "Correct the configuration or credentials (operator).",
                "Configuration and credential changes are prohibited.")
    elif k == C.DATA:
        add("production.asset.replace", "Replacing an asset inside the job is an operator decision; a staged copy can be "
            "prepared for review.", "Production assets are never replaced automatically.")
    return items


class RecoveryPlanner:
    def plan(self, incident: Dict[str, Any], diagnosis: Dict[str, Any], job: Dict[str, Any], *, store: Any,
             learning: Sequence[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
        cl = (diagnosis or {}).get("classification") or incident.get("classification") or {}
        corr = (diagnosis or {}).get("correlation") or {}
        klass, sub = cl.get("failure_class", C.UNKNOWN), cl.get("subtype", "")
        window = EvidenceSource.recovery_window(job, now) if job.get("found") else {"known": False, "inside": None}
        worker = EvidenceSource.worker_active(job, now) if job.get("found") else {"active": False, "status": None}
        scheduler_recovering = job.get("status") in SCHEDULER_RECOVERY_STATUSES
        retry_useful: Dict[str, Any]
        if klass == C.DETERMINISTIC or corr.get("likely_deterministic"):
            retry_useful = {"useful": False, "reason": "deterministic: another attempt with the same inputs would fail the same way"}
        elif klass == C.TRANSIENT:
            retry_useful = {"useful": True, "reason": "temporary cause; retries are the existing retry policy's job"}
        elif klass in (C.RESOURCE, C.CONFIGURATION, C.DATA):
            retry_useful = {"useful": False, "reason": f"{klass.lower()} problem: a retry helps only after it is fixed"}
        else:
            retry_useful = {"useful": None, "reason": "unknown: insufficient evidence"}

        unsafe: List[str] = []
        if worker.get("active"):
            unsafe.append(f"a worker is executing this job now (status {worker.get('status')}); nothing is proposed "
                          "until it stops")

        candidates: List[Dict[str, Any]] = []
        if sub in ("no_error_text", "deadline_outcome"):
            unsafe_free_note = ("no error text was recorded, so there is nothing to diagnose" if sub == "no_error_text" else
                                "the recovery window ended; this is an outcome note, and any underlying error is its own "
                                "incident")
            return {"checks": {"failure_class": klass, "certainty": cl.get("certainty"), "recovery_possible": False,
                               "recovery_possible_by_self_healing": False, "safe": True, "retry_useful": retry_useful,
                               "inside_recovery_window": window, "worker_active": worker,
                               "already_recovering": {"by_scheduler": scheduler_recovering, "job_status": job.get("status"),
                                                      "by_self_healing": False},
                               "locks_held": [], "requires_operator_approval": True},
                    "actions": [], "operator_only": [], "unsafe_reasons": [], "status_hint": "NO_ACTION",
                    "summary": f"Unknown failure: {unsafe_free_note}.", "planned_at_utc": now.isoformat()}
        diags = list(DIAGNOSTICS_BY_SUBTYPE.get(sub, ()))
        if klass in (C.UNKNOWN, C.TRANSIENT) and "collect_log_context" not in diags:
            diags.append("collect_log_context")
        if "generate_diagnostic_report" not in diags:
            diags.append("generate_diagnostic_report")
        for d in diags[:MAX_DIAGNOSTICS]:
            candidates.append({"action_type": DIAG, "payload": {"incident_id": incident["id"], "diagnostic": d},
                               "kind": "diagnostic", "purpose": f"Read-only diagnostic: {d.replace('_', ' ')}."})
        text = evidence_text(diagnosis, job)
        reversible_ok = (window.get("inside") is True and not worker.get("active") and not scheduler_recovering)
        why_not_reversible = []
        if window.get("inside") is not True:
            why_not_reversible.append("the job is outside its recovery window (or the window is unknown)")
        if scheduler_recovering:
            why_not_reversible.append(f"the existing scheduler is already recovering the job (status {job.get('status')})")
        if klass == C.TRANSIENT and sub in ("provider", "connection", "http_5xx", "timeout"):
            prov, query = provider_in(text), clean_query(job.get("topic_title"))
            if prov and query:
                candidates.append({"action_type": PROVIDER_RETRY, "payload": {"incident_id": incident["id"], "provider": prov,
                                                                              "query": query},
                                   "kind": "reversible", "purpose": f"Confirm {prov} answers a read-only search again.",
                                   "blocked": None if reversible_ok else "; ".join(why_not_reversible) or None})
        if klass in (C.TRANSIENT, C.DATA) or (klass == C.RESOURCE and sub == "file_lock"):
            for url in urls_in_evidence(text)[:1]:
                candidates.append({"action_type": REACQUIRE, "payload": {"incident_id": incident["id"], "asset_url": url},
                                   "kind": "reversible", "purpose": "Re-download the failed asset into the staging folder and "
                                   "validate it (it is not placed into the job).",
                                   "blocked": None if reversible_ok else "; ".join(why_not_reversible) or None})

        actions: List[Dict[str, Any]] = []
        since = (now - timedelta(hours=DIAGNOSTIC_REUSE_HOURS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        previous = store.recoveries_for(incident["id"])
        limit_reached = len(previous) >= MAX_RECOVERIES_PER_INCIDENT
        failed_reversible = sum(1 for r in previous if r["action_type"] != DIAG and r["status"] == "FAILED")
        for c in candidates:
            spec = registry.require_permitted(c["action_type"])
            payload = registry.validate_payload(spec, c["payload"])
            p_hash = payload_hash(spec.action_type, payload)
            keys = resource_keys(incident, spec.action_type, payload)
            blocked = c.get("blocked")
            if unsafe:
                blocked = unsafe[0]
            if not blocked and limit_reached:
                blocked = f"recovery limit reached for this incident ({MAX_RECOVERIES_PER_INCIDENT} recoveries)"
            if not blocked and c["kind"] == "reversible" and failed_reversible >= MAX_FAILED_REVERSIBLE_PER_INCIDENT:
                blocked = f"{failed_reversible} recovery actions already failed for this incident; no further attempts"
            if not blocked:
                blocked = _ineffective(learning, incident["signature"], spec.action_type)
            if not blocked:
                held = store.held_locks(keys, now)
                if held:
                    blocked = f"resource lock held by {held[0]['owner']} ({held[0]['resource_key']})"
            if not blocked:
                with store.read() as conn:
                    if store.open_recovery(conn, incident["id"], spec.action_type, p_hash):
                        blocked = "already proposed (an open recovery exists for this exact action)"
                    elif spec.action_type == DIAG and store.recent_completed(conn, incident["signature"], spec.action_type,
                                                                            p_hash, since):
                        blocked = f"already completed for this incident within {DIAGNOSTIC_REUSE_HOURS} h"
            actions.append({
                "action_type": spec.action_type, "payload": payload, "payload_hash": p_hash, "kind": c["kind"],
                "purpose": c["purpose"], "risk": spec.risk, "requires_approval": True, "approval": spec.approval,
                "approval_note": ("standing or explicit operator approval" if spec.approval == registry.APPROVAL_STANDING_ALLOWED
                                  else "explicit operator approval for this request"),
                "timeout_seconds": spec.timeout_seconds, "retry_limit": spec.retry_limit,
                "resources": RESOURCES[spec.action_type], "expected_cost": COSTS[spec.action_type],
                "resource_locks": keys, "blocked": blocked,
                "preconditions": list(spec.preconditions)})
        proposable = [a for a in actions if not a["blocked"]]
        reversible = [a for a in proposable if a["kind"] == "reversible"]
        operator_only = _operator_only(cl, window, job)
        checks = {
            "failure_class": klass, "certainty": cl.get("certainty"),
            "recovery_possible": bool(reversible) or (klass == C.TRANSIENT and retry_useful["useful"] is True),
            "recovery_possible_by_self_healing": bool(reversible),
            "safe": not unsafe,
            "retry_useful": retry_useful,
            "inside_recovery_window": window,
            "worker_active": worker,
            "already_recovering": {"by_scheduler": scheduler_recovering, "job_status": job.get("status"),
                                   "by_self_healing": any(a["blocked"] and "already proposed" in a["blocked"] for a in actions)},
            "locks_held": [a["blocked"] for a in actions if a["blocked"] and a["blocked"].startswith("resource lock")],
            "requires_operator_approval": True,
        }
        queued = [a for a in actions if a["blocked"] and a["blocked"].startswith("already proposed")]
        if unsafe:
            status_hint = "UNSAFE"
        elif proposable:
            status_hint = "PROPOSABLE"
        elif queued:
            status_hint = "ALREADY_PROPOSED"
        else:
            status_hint = "NO_ACTION"
        summary = (f"{klass.title()} failure ({cl.get('certainty', 'WEAK').lower()} evidence). "
                   + (f"{len(proposable)} action(s) can be proposed for approval. " if proposable else
                      f"{len(queued)} action(s) already in the approval queue. " if queued else
                      "No action can be proposed now. ")
                   + ("Job retries remain with the existing scheduler and operator." if klass != C.DETERMINISTIC
                      else "Retrying unchanged would repeat the failure."))
        return {"checks": checks, "actions": actions, "operator_only": operator_only, "unsafe_reasons": unsafe,
                "status_hint": status_hint, "summary": summary, "planned_at_utc": now.isoformat()}
