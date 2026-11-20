"""Stage 3.32B autonomy assessment: observe and recommend through the control plane.

Runs only when an operator asks (dashboard button) or when a future stage calls it; there is no background loop.
It reads the Stage 3.32A daily summary (read-only) and, depending on each category's effective level:

* OBSERVE   -> records observations (anomalies) in the audit trail;
* RECOMMEND -> proposes ``recommendation.record``, ``report.operational.generate`` and, for high-severity
               anomalies, ``operator.notify`` into the approval queue (duplicates collapse by payload);
* APPROVED_EXECUTION with a standing approval -> the control plane executes those proposals, with every gate.

It never proposes anything outside the registry, and the registry refuses prohibited types regardless.
"""

from __future__ import annotations

from typing import Any, Dict

from backend.app.autonomy.control_plane import ControlPlane, ControlPlaneError, safe_error
from backend.app.autonomy.registry import CATEGORIES, Level

ENGINE_ACTOR = "autonomy-engine"
MAX_OBSERVATIONS = 20
MAX_RECOMMENDATIONS = 10


def run_assessment(cp: ControlPlane, date: str = None) -> Dict[str, Any]:
    perms = cp.permissions()
    eff = {c: Level.parse(v) for c, v in perms["effective_levels"].items()}
    if all(v == Level.OFF for v in eff.values()):
        return {"ran": False, "reason": "autonomy is OFF for every category", "observations": 0, "proposals": []}
    summary = cp.intelligence().daily_summary(date)
    out: Dict[str, Any] = {"ran": True, "date": summary.get("date"), "health": summary.get("health"),
                           "observations": 0, "proposals": [], "errors": []}
    anomalies = list(summary.get("anomalies") or [])[:MAX_OBSERVATIONS]
    if eff["diagnostics"] >= Level.OBSERVE:
        for a in anomalies:
            r = cp.record_observation("diagnostics", str(a.get("text", ""))[:300],
                                      {"severity": a.get("severity"), "date": summary.get("date")}, actor=ENGINE_ACTOR)
            out["observations"] += 1 if r.get("recorded") else 0

    def submit(action_type: str, payload: Dict[str, Any], reason: str, evidence: Dict[str, Any]) -> None:
        try:
            res = cp.submit(action_type, payload, reason=reason, evidence=evidence, proposed_by=ENGINE_ACTOR)
            req = res["proposal"]["request"]
            out["proposals"].append({"request_id": req["id"], "action_type": action_type, "status": req["status"],
                                     "duplicate": res["proposal"]["duplicate"],
                                     "executed": bool(res["execution"] and res["execution"].get("executed"))})
        except ControlPlaneError as exc:
            out["errors"].append({"action_type": action_type, "error": safe_error(exc)})

    if eff["recommendations"] >= Level.RECOMMEND:
        for text in list(summary.get("recommendations") or [])[:MAX_RECOMMENDATIONS]:
            text = str(text)[:2000]
            submit("recommendation.record", {"title": text[:150], "text": text, "evidence_ref": f"daily_summary:{summary.get('date')}"},
                   "Stage 3.32A daily summary recommendation", {"date": summary.get("date"), "health": summary.get("health")})
    if eff["reporting"] >= Level.RECOMMEND and summary.get("date"):
        submit("report.operational.generate", {"date": summary["date"]}, "Daily operational report",
               {"health": summary.get("health"), "anomalies": len(anomalies)})
    if eff["notifications"] >= Level.RECOMMEND:
        high = [a for a in anomalies if a.get("severity") == "high"]
        if high:
            message = "\n".join(f"- {a.get('text', '')}" for a in high)[:2000]
            submit("operator.notify", {"subject": f"{len(high)} high-severity anomal{'y' if len(high) == 1 else 'ies'} on {summary.get('date')}",
                                       "message": message, "severity": "high"},
                   "High-severity anomalies in the daily summary", {"date": summary.get("date"), "count": len(high)})
    cp._audit_now("ASSESSMENT_COMPLETED", ENGINE_ACTOR, reason=f"observations={out['observations']} proposals={len(out['proposals'])}",
                  details={"date": out.get("date"), "health": out.get("health"),
                           "levels": {c: eff[c].name for c in CATEGORIES}})
    return out
