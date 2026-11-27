"""Remediation section 4: one operator-attention feed instead of instructions repeated on every page.

Three lists, each built from state the backend already records (read-only; no new decision logic):

* ``needs_you``: only genuine interventions, each once, with the reason and the single action:
  - a credential or external authorization is missing (provider audit);
  - an approval-required action is waiting (control-plane PENDING requests; routine automatic actions never are);
  - a decision that needs human judgement (creative proposals marked ``requires_operator``; topic-supply flags);
  - a recovery that failed after its permitted attempts (control-plane executions FAILED);
  - an emergency stop is active.
* ``done_automatically``: policy/standing-approved executions with their verified outcome.
* ``blocked``: refused or blocked actions with the exact recorded reason.

Every source is optional: an unavailable one is listed under ``unavailable`` and never hides the others.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _safe(name: str, fn: Callable[[], Any], unavailable: List[str]) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"{name}: {type(exc).__name__}")
        return None


def attention(*, cp: Any = None, providers: Optional[Callable[[], Dict[str, Any]]] = None,
              creative: Optional[Callable[[], Dict[str, Any]]] = None,
              topic_supply: Optional[Callable[[], Dict[str, Any]]] = None,
              localizations: Optional[Callable[[], Dict[str, Any]]] = None) -> Dict[str, Any]:
    unavailable: List[str] = []
    if cp is None:
        from backend.app.autonomy.control_plane import get_control_plane
        cp = get_control_plane()
    if providers is None:
        from backend.app.services.provider_audit import summary as providers
    if creative is None:
        from backend.app.creative_intel.feedback import feedback_report as creative
    if topic_supply is None:
        topic_supply = _topic_supply
    if localizations is None:
        def localizations():
            from backend.app.localization.pipeline import overview
            return overview()
    needs: List[Dict[str, Any]] = []

    control = _safe("control_state", lambda: cp.control_state(), unavailable) or {}
    if control.get("state") == "EMERGENCY_STOP":
        needs.append({"kind": "emergency_stop", "severity": "high", "title": "Autonomous execution is stopped",
                      "why": control.get("reason"), "action": "Clear the kill switch in Autonomy Control when safe."})

    prov = _safe("providers", providers, unavailable) or {}
    for p in prov.get("providers") or []:
        st = p.get("state")
        if st in ("KEY_MISSING", "AUTHENTICATION_REQUIRED") and p.get("production"):
            needs.append({"kind": "credential", "severity": "medium", "title": f"{p.get('name')}: {st.lower().replace('_', ' ')}",
                          "why": p.get("state_reason") or p.get("purpose"),
                          "action": f"Add or re-authorize the credential ({p.get('env') or 'OAuth'}); "
                                    f"until then: {p.get('fallback') or 'no fallback'}."})

    pending = _safe("approvals", lambda: cp.list_requests("PENDING", 50), unavailable) or []
    if pending:
        kinds: Dict[str, int] = {}
        for r in pending:
            kinds[r["action_type"]] = kinds.get(r["action_type"], 0) + 1
        needs.append({"kind": "approval", "severity": "medium",
                      "title": f"{len(pending)} action(s) need your approval",
                      "why": ", ".join(f"{k} ×{v}" for k, v in sorted(kinds.items())),
                      "action": "Review them in Autonomy Control (they are outside the automatic policy)."})

    execs = _safe("executions", lambda: cp.list_executions(100), unavailable) or []
    failed = [e for e in execs if e.get("outcome") in ("FAILED", "TIMED_OUT", "UNVERIFIED")]
    if failed:
        needs.append({"kind": "failed_recovery", "severity": "high",
                      "title": f"{len(failed)} action(s) failed after their permitted attempts",
                      "why": "; ".join(f"{e.get('action_type')}: {str(e.get('error') or e.get('outcome'))[:80]}"
                                       for e in failed[:3]),
                      "action": "Inspect the failure; automatic retries are bounded and have stopped."})

    fb = _safe("creative", creative, unavailable) or {}
    for prop in fb.get("proposals") or []:
        if prop.get("requires_operator"):
            needs.append({"kind": "decision", "severity": "low", "title": f"Creative decision: {prop.get('id')}",
                          "why": prop.get("evidence"), "action": prop.get("action")})

    ts = _safe("topic_supply", topic_supply, unavailable) or {}
    if ts.get("decision_needed"):
        needs.append({"kind": "decision", "severity": "medium", "title": "Topic supply is running low",
                      "why": ts.get("evidence"), "action": ts.get("action")})

    # Stage 4.02: genuine Hindi localization blockers only (WAITING resolves itself and is not listed).
    locs = (_safe("localizations", localizations, unavailable) or {}).get("localizations") or []
    for l in locs:
        if l.get("status") == "FAILED":
            needs.append({"kind": "failed_recovery", "severity": "medium",
                          "title": f"Hindi localization failed: {l.get('source_job_id')}",
                          "why": l.get("error") or "technical failure after its attempts",
                          "action": "Retry it from the Hindi localization card once the cause is fixed."})
        elif l.get("status") == "QC_REJECTED":
            needs.append({"kind": "decision", "severity": "low",
                          "title": f"Hindi QC rejected {l.get('source_job_id')}",
                          "why": "; ".join(((l.get("qc") or {}).get("creative") or [])[:2]) or l.get("error"),
                          "action": "Retranslate it, or leave it (it never publishes)."})
    passed = [l for l in locs if l.get("status") == "QC_PASSED"]
    if passed:
        reasons = (passed[0].get("publishing") or {}).get("reasons") or []
        if reasons:
            needs.append({"kind": "decision", "severity": "low",
                          "title": f"{len(passed)} Hindi video(s) passed QC and wait for publishing authorization",
                          "why": "; ".join(reasons[:2]),
                          "action": "Verify the Hindi channel (Channels card); Hindi publishing is a separate milestone."})

    requests_by_id = {}
    for r in (_safe("approved", lambda: cp.list_requests(None, 200), unavailable) or []):
        requests_by_id[r["id"]] = r
    automatic = []
    for e in execs:
        req = requests_by_id.get(e.get("request_id")) or {}
        if req.get("approval_mode") in ("POLICY", "STANDING"):
            automatic.append({"at_utc": e.get("started_at_utc"), "action_type": e.get("action_type"),
                              "outcome": e.get("outcome"), "approval": req.get("approved_by"),
                              "reason": (req.get("reason") or "")[:160]})
    events = _safe("audit", lambda: cp.audit_events(200), unavailable) or []
    blocked = [{"at_utc": ev.get("ts_utc"), "event": ev.get("event_type"), "action_type": ev.get("action_type"),
                "reason": (ev.get("reason") or "")[:200]}
               for ev in events if ev.get("event_type") in ("EXECUTION_BLOCKED", "PROPOSAL_REFUSED", "BRIDGE_REFUSED",
                                                            "PROHIBITED_ACTION_BLOCKED", "SELF_HEALING_SCAN_BLOCKED",
                                                            "RECOMMENDATION_RUN_BLOCKED")]
    order = {"high": 0, "medium": 1, "low": 2}
    needs.sort(key=lambda n: order.get(n["severity"], 3))
    return {"control_state": {k: control.get(k) for k in ("state", "reason")} if control else None,
            "needs_you": needs, "done_automatically": automatic[:30], "blocked": blocked[:30],
            "unavailable": unavailable,
            "rule": "You are asked only for credentials, approvals outside the automatic policy, human decisions and "
                    "recoveries that failed after their permitted attempts. Routine recoverable failures are handled "
                    "automatically and appear under done automatically or blocked, with the reason."}


def _topic_supply() -> Dict[str, Any]:
    """Topic supply from the last discovery runs; flags a decision only on repeated exhaustion evidence."""
    import sqlite3
    from pathlib import Path
    from backend.app.core.paths import DATABASE_PATH
    c = sqlite3.connect(Path(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT eligible_count, outcome, outcome_reason FROM topic_discovery_runs "
                         "ORDER BY started_at DESC LIMIT 24").fetchall()
    finally:
        c.close()
    low = sum(1 for e, _o, _r in rows if (e or 0) <= 1)
    exhausted = sum(1 for _e, o, r in rows if o == "FAILED" and "integrity gate" in str(r or ""))
    out = {"runs": len(rows), "runs_with_at_most_one_eligible": low, "exhaustion_failures": exhausted}
    if rows and (low >= 6 or exhausted >= 2):
        out.update(decision_needed=True,
                   evidence=f"{low} of the last {len(rows)} discovery runs had at most one eligible topic; "
                            f"{exhausted} failed the integrity gate (every topic already used)",
                   action="Decide on the implemented but inactive 3.31C/D flags production_query_rotation and "
                          "exhaustion_recovery (docs/REMEDIATION_STABILITY_AUTONOMY.md, WS3).")
    return out
