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

import json
from typing import Any, Callable, Dict, List, Optional


GROUPS = ("security", "external_authorization", "creative_decision", "local_operational")
GROUP_OF_KIND = {"emergency_stop": "security", "approval": "security", "credential": "external_authorization",
                 "external_authorization": "external_authorization", "creative_decision": "creative_decision",
                 "decision": "local_operational", "failed_recovery": "local_operational"}


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

    optional: List[Dict[str, Any]] = []
    prov = _safe("providers", providers, unavailable) or {}
    for p in prov.get("providers") or []:
        st = p.get("state")
        if st in ("KEY_MISSING", "AUTHENTICATION_REQUIRED") and p.get("production"):
            fallback = str(p.get("fallback") or "").strip()
            item = {"kind": "credential", "title": f"{p.get('name')}: {st.lower().replace('_', ' ')}",
                    "why": p.get("state_reason") or p.get("purpose"),
                    "action": f"Add or re-authorize the credential ({p.get('env') or 'OAuth'}); "
                              f"until then: {fallback or 'no fallback'}."}
            # A missing credential whose work is covered by a working fallback does not stop anything: it is an
            # optional improvement, not a request (a missing credential is never a local infrastructure problem).
            if fallback and fallback.lower() not in ("no fallback", "none") and not p.get("required"):
                optional.append(dict(item, severity="info"))
            else:
                needs.append(dict(item, severity="medium"))

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
            needs.append({"kind": "creative_decision", "severity": "low", "title": f"Creative decision: {prop.get('id')}",
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
            needs.append({"kind": "external_authorization", "severity": "low",
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
    # Remediation closure: every request says which kind of matter it is, so external authorizations and creative
    # (human-judgement) decisions are never mixed with local operational blockers.
    for n in needs:
        n["group"] = GROUP_OF_KIND.get(n["kind"], "local_operational")
    groups = {g: sum(1 for n in needs if n["group"] == g) for g in GROUPS}
    return {"control_state": {k: control.get(k) for k in ("state", "reason")} if control else None,
            "needs_you": needs, "groups": groups, "optional_improvements": optional, "done_automatically": automatic[:30],
            "blocked": blocked[:30], "unavailable": unavailable,
            "rule": "You are asked only for credentials, approvals outside the automatic policy, human decisions and "
                    "recoveries that failed after their permitted attempts. Routine recoverable failures are handled "
                    "automatically and appear under done automatically or blocked, with the reason."}


FLAGS = ("production_query_rotation", "exhaustion_recovery")
MIN_RUNS_FOR_DECISION = 6


def topic_supply_decision(rows: List[Any], flags_on: Dict[str, bool]) -> Dict[str, Any]:
    """Pure evaluation of the last discovery runs (eligible_count, outcome, outcome_reason, flags_json).

    Reconciled against the live configuration: when both supply flags are ON, only runs that actually ran with both
    flags count (runs from before the activation are history, not a current blocker), and the operator is never asked
    to decide on flags that are already ON. A decision is listed only on enough current evidence."""
    both_on = all(flags_on.get(f) for f in FLAGS)
    if both_on:
        def ran_with_flags(r):
            try:
                fj = json.loads(r[3] or "{}")
            except (TypeError, ValueError):
                return False
            return all(fj.get(f) == "on" for f in FLAGS)
        rows = [r for r in rows if ran_with_flags(r)]
    low = sum(1 for r in rows if (r[0] or 0) <= 1)
    exhausted = sum(1 for r in rows if r[1] == "FAILED" and "integrity gate" in str(r[2] or ""))
    out = {"runs": len(rows), "runs_with_at_most_one_eligible": low, "exhaustion_failures": exhausted,
           "flags": {f: ("on" if flags_on.get(f) else "off") for f in FLAGS},
           "evaluated": "runs with both supply flags on" if both_on else "last discovery runs"}
    if len(rows) < MIN_RUNS_FOR_DECISION and both_on:
        out["status"] = f"collecting evidence with both flags on ({len(rows)} of {MIN_RUNS_FOR_DECISION} runs)"
        return out
    if not (rows and (low >= 6 or exhausted >= 2)):
        out["status"] = "healthy"
        return out
    evidence = (f"{low} of the last {len(rows)} discovery runs{' with both flags on' if both_on else ''} had at most "
                f"one eligible topic; {exhausted} failed the integrity gate (every topic already used)")
    if both_on:
        action = ("Topic supply is still low with query rotation and exhaustion recovery ON: decide whether to widen "
                  "the sources or categories (3.31 expanded_categories / gnews_candidates, operator decision).")
    else:
        off = [f for f in FLAGS if not flags_on.get(f)]
        action = ("Decide on the implemented but inactive 3.31C/D flag(s) " + " and ".join(off)
                  + " (docs/REMEDIATION_STABILITY_AUTONOMY.md, WS3).")
    out.update(decision_needed=True, status="low", evidence=evidence, action=action)
    return out


def _topic_supply() -> Dict[str, Any]:
    """Topic supply from the last discovery runs, reconciled with the live flag configuration."""
    import sqlite3
    from pathlib import Path
    from backend.app.core.paths import DATABASE_PATH
    from backend.app.content.topic_intelligence_flags import load_flags
    flags = load_flags()
    c = sqlite3.connect(Path(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT eligible_count, outcome, outcome_reason, flags_json FROM topic_discovery_runs "
                         "ORDER BY started_at DESC LIMIT 24").fetchall()
    finally:
        c.close()
    return topic_supply_decision(rows, {f: flags.is_on(f) for f in FLAGS})
