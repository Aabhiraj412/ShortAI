"""Stage 3.32C recommendation engine: orchestration under the Stage 3.32B control plane.

Gating (checked at the start of every run, fail closed):
* The action type ``recommendation.record`` must be a permitted registry entry (it is).
* The effective level of the ``recommendations`` category (min(global, category)) decides what happens:
    OFF        -> nothing runs, nothing is written.
    OBSERVE    -> analysis runs; findings are recorded as one audit observation; no recommendation is stored.
    RECOMMEND+ -> recommendations are created / updated / expired / superseded and every decision is audited.
* Any active kill switch (dashboard, sentinel file, startup config) pauses the engine: no analysis, no writes
  except one audit event saying the run was blocked.

The engine never executes anything, never changes autonomy levels or kill switches and never approves its own
recommendations: operator status changes require a human operator identity (validate_operator refuses
autonomy/system identities). Gemini wording is optional, off by default, and never required.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import ControlPlane, ControlPlaneError, PermissionDenied, safe_error, validate_operator
from backend.app.autonomy.recommendations import rules as rules_mod
from backend.app.autonomy.recommendations import scoring, signals as signals_mod
from backend.app.autonomy.recommendations.store import (
    ACKNOWLEDGED, APPROVED, REJECTED, RESOLVED, RecommendationStore, _ts,
)
from backend.app.autonomy.registry import Level

ENGINE_ACTOR = "recommendation-engine"
ACTION_TYPE = "recommendation.record"
CATEGORY = "recommendations"
DEFAULT_WINDOW_DAYS = 7
MIN_RUN_INTERVAL_SECONDS = 300
EXPLAINER_ENV = "CRAZZYDOCS_RECOMMENDATION_EXPLAINER"
MAX_EXPLANATIONS_PER_RUN = 3
STATUS_EVENTS = {ACKNOWLEDGED: "RECOMMENDATION_ACKNOWLEDGED", APPROVED: "RECOMMENDATION_APPROVED",
                 REJECTED: "RECOMMENDATION_REJECTED", RESOLVED: "RECOMMENDATION_RESOLVED"}


class GeminiExplainer:
    """Optional plain-language wording for a recommendation. Disabled unless explicitly enabled; failures are ignored.

    ``generate`` is injectable; in production it lazily uses the existing Gemini client factory. It is only called
    for at most MAX_EXPLANATIONS_PER_RUN new or materially changed recommendations, and the result is cached by an
    evidence fingerprint, so an unchanged recommendation is never explained twice."""

    def __init__(self, generate: Optional[Callable[[str], str]] = None):
        self._generate = generate

    def _default_generate(self, prompt: str) -> str:
        from backend.app.content.grounded_script_writer import default_client
        client, model = default_client()
        if client is None:
            raise RuntimeError("Gemini is not configured")
        resp = client.models.generate_content(model=model, contents=prompt)
        return str(getattr(resp, "text", "") or "")

    def explain(self, rec: Dict[str, Any]) -> str:
        prompt = ("Rewrite this operations finding for a non-technical operator in at most 3 sentences. Keep the facts, "
                  "keep uncertainty explicit (it is a hypothesis unless stated as confirmed), add nothing new.\n\n"
                  f"Title: {rec['title']}\nObservation: {rec['description']}\nHypothesis ({rec['hypothesis_strength']}): "
                  f"{rec['hypothesis']}\nSuggested action: {rec['action']}")
        return (self._generate or self._default_generate)(prompt).strip()[:1200]


class RecommendationEngine:
    def __init__(self, control_plane: ControlPlane, store: Optional[RecommendationStore] = None,
                 intelligence: Optional[Callable[[], Any]] = None,
                 schedule_provider: Optional[Callable[[], Dict[str, int]]] = None,
                 explainer: Optional[GeminiExplainer] = None, environ: Optional[Dict[str, str]] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 learning_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None):
        self.cp = control_plane
        self.store = store or RecommendationStore(control_plane.db_path)
        self.intelligence = intelligence or control_plane.intelligence
        self.schedule_provider = schedule_provider
        # Stage 3.32D outcomes (read-only; never creates tables). Injectable for tests.
        def _default_learning() -> Optional[Dict[str, Any]]:
            from backend.app.autonomy.self_healing.store import read_learning
            return read_learning(control_plane.db_path)
        self.learning_provider = learning_provider if learning_provider is not None else _default_learning
        env = os.environ if environ is None else environ
        self.explainer_enabled = (env.get(EXPLAINER_ENV, "").strip().lower() == "gemini")
        self.explainer = explainer if explainer is not None else (GeminiExplainer() if self.explainer_enabled else None)
        self.clock = clock or control_plane.clock
        self._run_lock = threading.Lock()

    # -- gating ---------------------------------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        cfg, err = self.cp._safe_config()
        ks = self.cp.kill_switches(cfg, err)
        level = self.cp._effective(cfg, CATEGORY)
        try:
            spec = registry.require_permitted(ACTION_TYPE)
            permitted = spec.category == CATEGORY
        except Exception:
            permitted = False
        if not permitted:
            mode, reason = "BLOCKED", f"{ACTION_TYPE} is not a permitted action type"
        elif ks.get("emergency_stop", ks["any_active"]):      # Remediation WS2/WS5: only an emergency stop blocks analysis
            mode, reason = "BLOCKED", f"emergency stop: kill switch active: {', '.join(ks['active'])}"
        elif level == Level.OFF:
            mode, reason = "OFF", "the recommendations autonomy level is OFF"
        elif level == Level.OBSERVE:
            mode, reason = "OBSERVE", "observations only (level OBSERVE)"
            # Remediation follow-up: storing a recommendation IS the policy-automatic action recommendation.record.
            # When the control plane's built-in policy covers it in the current control state, analysis stores its
            # recommendations (so their automatic follow-ups can run); no level is raised.
            try:
                state_name = ks.get("control_state")
                ok, why = self.cp.policy_allows(ACTION_TYPE, cfg, state_name) if state_name and not err else (False, "")
            except Exception:
                ok, why = False, ""
            if ok:
                mode, reason = "RECOMMEND", f"recommendations stored automatically: {why}"
        else:
            mode, reason = "RECOMMEND", "recommendations enabled"
        return {"mode": mode, "reason": reason, "level": level.name, "kill_switches": ks["active"],
                "configuration_error": err, "explainer": "gemini" if self.explainer_enabled else "off"}

    # -- run ------------------------------------------------------------------------------------------------
    def run(self, triggered_by: str = ENGINE_ACTOR, days: int = DEFAULT_WINDOW_DAYS, force: bool = False) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"ran": False, "mode": "BUSY", "reason": "another analysis is running"}
        try:
            return self._run(triggered_by, days, force)
        finally:
            self._run_lock.release()

    def _run(self, triggered_by: str, days: int, force: bool) -> Dict[str, Any]:
        now = self.clock()
        st = self.state()
        run_id = "rrun_" + secrets.token_hex(6)
        if st["mode"] == "OFF":
            return {"ran": False, "mode": "OFF", "reason": st["reason"], "state": st}
        if st["mode"] == "BLOCKED":
            self.cp._audit_now("RECOMMENDATION_RUN_BLOCKED", ENGINE_ACTOR, action_type=ACTION_TYPE, reason=st["reason"],
                               details={"triggered_by": triggered_by})
            return {"ran": False, "mode": "BLOCKED", "reason": st["reason"], "state": st}
        if not force:
            last = self.store.last_run()
            if last and last["started_at"] > _ts(now - timedelta(seconds=MIN_RUN_INTERVAL_SECONDS)):
                return {"ran": False, "mode": st["mode"], "reason": "analysed less than 5 minutes ago", "last_run": last}
        sig = signals_mod.collect(self.intelligence(), days=days, now=now, schedule_provider=self.schedule_provider,
                                  learning_provider=self.learning_provider)
        result = rules_mod.evaluate(sig)
        scored = []
        for f in result.findings:
            conf = scoring.confidence(f)
            scored.append((f, conf, scoring.priority(f, conf, now, days)))
        run: Dict[str, Any] = {"run_id": run_id, "started_at": _ts(now), "mode": st["mode"], "window_days": days,
                               "triggered_by": str(triggered_by)[:100], "findings": len(scored),
                               "insufficient": result.insufficient, "sources": {**sig.availability(), **{f"error:{k}": v for k, v in sig.errors.items()}}}
        if st["mode"] == "OBSERVE":
            self.cp.record_observation(CATEGORY, f"{len(scored)} potential recommendation(s) detected (level OBSERVE: not stored)",
                                       {"run_id": run_id, "by_category": _count(f.category for f, _, _ in scored),
                                        "titles": [f.title for f, _, _ in scored][:20]}, actor=ENGINE_ACTOR)
            run["finished_at"] = _ts(self.clock())
            self.store.record_run(run)
            return {"ran": True, "mode": "OBSERVE", "run": run, "findings": [f.title for f, _, _ in scored]}
        created = updated = suppressed = 0
        events: List[tuple] = []
        explain_ids: List[str] = []
        with self.store.tx() as conn:
            for f, conf, prio in sorted(scored, key=lambda x: -x[2]["score"]):
                res = self.store.upsert(conn, f, conf, prio, now, run_id)
                if res["action"] == "created":
                    created += 1
                    explain_ids.append(res["id"])
                    events.append(("RECOMMENDATION_CREATED", res["id"], None, "NEW", f.title,
                                   {"rule": f.rule_id, "priority": prio["label"], "confidence": conf["label"]}))
                elif res["action"] == "updated":
                    updated += 1
                    if res["material_change"]:
                        explain_ids.append(res["id"])
                        events.append(("RECOMMENDATION_UPDATED", res["id"], None, None, f.title,
                                       {"from": res["previous"], "to": {"priority_label": prio["label"], "confidence_label": conf["label"]}}))
                else:
                    suppressed += 1
                if f.supersedes and res["action"] in ("created", "updated"):
                    for s in self.store.supersede(conn, f.supersedes, res["id"], now):
                        events.append(("RECOMMENDATION_SUPERSEDED", s["id"], s["previous"], "SUPERSEDED", f"superseded by {res['id']}", {}))
            expired = self.store.expire_due(conn, now)
            for e in expired:
                events.append(("RECOMMENDATION_EXPIRED", e["id"], e["status"], "EXPIRED", "no longer detected", {}))
        run.update(created=created, updated=updated, suppressed=suppressed, expired=len(expired),
                   superseded=sum(1 for e in events if e[0] == "RECOMMENDATION_SUPERSEDED"), finished_at=_ts(self.clock()))
        self.store.record_run(run)
        for ev, rec_id, prev, new, reason, details in events:
            self.cp._audit_now(ev, ENGINE_ACTOR, request_id=rec_id, action_type=ACTION_TYPE, previous_state=prev,
                               new_state=new, reason=reason, details=details)
        self.cp._audit_now("RECOMMENDATION_RUN_COMPLETED", ENGINE_ACTOR, action_type=ACTION_TYPE,
                           reason=f"findings={len(scored)} created={created} updated={updated} expired={len(expired)}",
                           details={"run_id": run_id, "triggered_by": run["triggered_by"], "window_days": days})
        explained = self._explain(explain_ids[:MAX_EXPLANATIONS_PER_RUN]) if self.explainer else 0
        return {"ran": True, "mode": "RECOMMEND", "run": run, "explained": explained}

    def _explain(self, ids: List[str]) -> int:
        n = 0
        for rec_id in ids:
            try:
                rec = self.store.get(rec_id)
                fp = hashlib.sha256((rec["description"] + rec["hypothesis"] + rec["action"]).encode("utf-8")).hexdigest()
                text = self.explainer.explain(rec)
                if text:
                    self.store.set_explanation(rec_id, text, fp)
                    n += 1
            except Exception as exc:                       # wording is optional; recommendations stand without it
                self.cp._audit_now("RECOMMENDATION_EXPLAINER_FAILED", ENGINE_ACTOR, request_id=rec_id,
                                   action_type=ACTION_TYPE, reason=safe_error(exc))
        return n

    # -- operator actions -----------------------------------------------------------------------------------
    def set_status(self, rec_id: str, new_status: str, operator: str, reason: Optional[str] = None,
                   evidence: Optional[str] = None) -> Dict[str, Any]:
        op = validate_operator(operator)               # refuses autonomy / system identities: no self-approval
        out = self.store.transition(rec_id, new_status, op, self.clock(), reason=reason, evidence=evidence)
        if not out["unchanged"]:
            self.cp._audit_now(STATUS_EVENTS[new_status], op, request_id=rec_id, action_type=ACTION_TYPE,
                               previous_state=out["previous"], new_state=new_status,
                               reason=(reason or evidence or "").strip()[:500] or new_status.lower(),
                               details={"note": "status change only; no action is executed"})
        return out

    # -- reads ----------------------------------------------------------------------------------------------
    def overview(self, status: str = "open", category: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        now = self.clock()
        out: Dict[str, Any] = {"state": self.state(), "categories": rules_mod.CATEGORIES,
                               "note": "Recommendations are advice for the operator. Approving one records agreement only; "
                                       "nothing is executed. Executable approval requests are in Autonomy Control."}
        try:
            out["summary"] = self.store.summary(now)
            out["recommendations"] = self.store.list(status=status, category=category, limit=limit)
            out["last_run"] = self.store.last_run()
            out["database_available"] = True
        except ControlPlaneError:
            raise
        except Exception as exc:
            out.update(summary=None, recommendations=[], last_run=None, database_available=False, error=safe_error(exc))
        return out

    def detail(self, rec_id: str) -> Dict[str, Any]:
        rec = self.store.get(rec_id)
        rec["history"] = self.cp.audit_events(limit=100, request_id=rec_id)
        return rec


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


_engine: Optional[RecommendationEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> RecommendationEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            from backend.app.autonomy import control_plane as cpm
            _engine = RecommendationEngine(cpm.get_control_plane())
        return _engine
