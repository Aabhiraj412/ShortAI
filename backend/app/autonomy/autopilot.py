"""Stage 3.32G: Controlled Autopilot - one lifecycle over the existing autonomy components.

    3.32A Operational Intelligence --> 3.32C Recommendations --+
    3.32D Self-Healing (diagnosis, plans) ---------------------+--> 3.32B Control Plane --> approved execution
    3.32E Community drafts / 3.32F publishing readiness ---------> shown here, governed by their own gates

Nothing here is a second control system: every proposal goes through ``ControlPlane.propose`` and every execution
through ``ControlPlane.execute`` (levels, kill switches, signed approvals, leases, circuit breaker, audit). This
module adds:

* a deterministic RECOMMENDATION -> ACTION bridge: a recommendation (rule id) maps to a fixed set of registered,
  permitted, low-impact follow-up actions whose payloads are built from rule-generated fields only. AI-written text
  (the optional Gemini explanation) is never used in a payload and never selects an action;
* the centralized action policy (tiers) and a single read-only Autopilot overview built from persisted state.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.autonomy import registry
from datetime import timedelta

from backend.app.autonomy.control_plane import (EMERGENCY_STOP, ControlPlane, ControlPlaneError, NotFound,
                                                PermissionDenied, payload_hash, validate_operator)
from backend.app.autonomy.registry import Level

BRIDGE_ACTOR = "recommendation-bridge"          # reserved prefix: no human can approve as this identity

# rule-id prefix -> allowed follow-up actions. Payloads use only deterministic rule fields; never free text from AI.
_REPORT = ("report.operational.generate", lambda rec: {"days": 7})
_OVERVIEW = ("diagnostic.readonly.run", lambda rec: {"diagnostic": "ops_overview", "days": 7})
_SUMMARY = ("diagnostic.readonly.run", lambda rec: {"diagnostic": "ops_daily_summary"})
_NOTIFY = ("operator.notify", lambda rec: {"subject": _clip(f"[{rec['severity']}] {rec['title']}", 120),
                                            "message": _clip(f"{rec['problem']} Rule {rec['rule_id']}; see the "
                                                             f"Recommendation Center ({rec['id']}).", 2000),
                                            "severity": rec["severity"] if rec["severity"] in ("low", "medium", "high")
                                            else ("high" if rec["severity"] == "critical" else "info")})
BRIDGE: Tuple[Tuple[str, Tuple[Tuple[str, Callable[[Dict[str, Any]], Dict[str, Any]]], ...]], ...] = (
    ("prod.", (_OVERVIEW, _REPORT, _NOTIFY)),
    ("recovery.", (_OVERVIEW, _REPORT, _NOTIFY)),
    ("render.", (_OVERVIEW, _REPORT, _NOTIFY)),
    ("quality.", (_REPORT, _NOTIFY)),
    ("topic.", (_REPORT, _NOTIFY)),
    ("api.", (_SUMMARY, _NOTIFY)),
)
BRIDGEABLE_STATUSES = ("NEW", "ACKNOWLEDGED", "APPROVED")
AUTO_BRIDGE_LIMIT = 4                 # automatic follow-ups executed per observation cycle
AUTO_BRIDGE_COOLDOWN_HOURS = 6        # the same action + payload runs at most once per window


def _clip(text: str, n: int) -> str:
    t = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def bridge_options(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The follow-up actions a recommendation may become. Empty when the rule has no mapping."""
    for prefix, actions in BRIDGE:
        if str(rec.get("rule_id") or "").startswith(prefix):
            out = []
            for action_type, build in actions:
                spec = registry.REGISTRY[action_type]
                out.append({"action_type": action_type, "category": spec.category, "risk": spec.risk,
                            "tier": registry.policy_tier(spec), "payload": registry.validate_payload(spec, build(rec))})
            return out
    return []


def action_policy() -> Dict[str, Any]:
    entries = [registry.policy_entry(s) for s in registry.REGISTRY.values()]
    tiers: Dict[str, List[str]] = {}
    for e in entries:
        tiers.setdefault(e["tier"], []).append(e["action_type"])
    return {
        "entries": sorted(entries, key=lambda e: (e["tier"], e["action_type"])),
        "tiers": {k: sorted(v) for k, v in tiers.items()},
        "separately_gated_outside_autonomy": [
            {"id": "community.draft.generate (Stage 3.32E)", "gate": "Community schedule: enabled flag, pause, "
             "Community kill switch and global sentinel; drafts only, never published; operator approval required"},
            {"id": "community.post.publish_api (Stage 3.32F)", "gate": "PublishingService: OFF by default, needs a fresh "
             "API_AVAILABLE capability check, an APPROVED draft whose content matches the approval, and an explicit "
             "per-post confirmation. Never reachable from autonomy (youtube.community.publish is PROHIBITED)"},
        ],
        "never_automatic": ["youtube.community.publish", "youtube.video.publish", "external.communication.send",
                            "production.data.delete", "oauth.credentials.change", "oauth.scopes.change",
                            "external.financial.action", "git.destructive", "autonomy.self_policy.change",
                            "autonomy.code.execute"],
    }


class Autopilot:
    def __init__(self, control_plane: ControlPlane, *, recommendations: Optional[Any] = None,
                 self_healing: Optional[Any] = None, community: Optional[Any] = None,
                 publishing: Optional[Any] = None):
        self.cp = control_plane
        self._recs, self._heal, self._community, self._publishing = recommendations, self_healing, community, publishing

    # -- lazily resolved components (tests inject their own) ------------------------------------------------
    def recs(self):
        if self._recs is None:
            from backend.app.autonomy.recommendations.engine import get_engine
            self._recs = get_engine()
        return self._recs

    # -- bridge ---------------------------------------------------------------------------------------------
    def propose_from_recommendation(self, rec_id: str, action_type: str, operator: str) -> Dict[str, Any]:
        """Turn one open recommendation into ONE approval request for a mapped, permitted action. The control plane
        then applies every gate (RECOMMEND level for the category, payload validation, dedupe). It never executes."""
        op = validate_operator(operator)
        if not isinstance(rec_id, str) or not re.fullmatch(r"rec_[0-9a-f]{16}", rec_id):
            raise NotFound("recommendation not found")
        rec = self.recs().detail(rec_id)
        if rec.get("status") not in BRIDGEABLE_STATUSES:
            raise ControlPlaneError(f"recommendation is {rec.get('status')}; only open recommendations can be proposed")
        options = {o["action_type"]: o for o in bridge_options(rec)}
        if action_type not in options:
            self.cp._audit_now("BRIDGE_REFUSED", f"operator:{op}", action_type=str(action_type)[:80],
                               reason=f"{action_type} is not a mapped follow-up of rule {rec.get('rule_id')}")
            raise PermissionDenied(f"{action_type} is not an allowed follow-up for rule {rec.get('rule_id')}; "
                                   f"allowed: {', '.join(options) or 'none'}")
        opt = options[action_type]
        return self.cp.propose(action_type, opt["payload"],
                               reason=_clip(f"Follow-up of recommendation {rec_id} ({rec.get('rule_id')}): "
                                            f"{rec.get('title')}", 480),
                               evidence={"recommendation_id": rec_id, "rule_id": rec.get("rule_id"),
                                         "requested_by": f"operator:{op}", "bridge": "deterministic rule mapping"},
                               proposed_by=BRIDGE_ACTOR)

    # -- automatic bridge (Remediation WS5) -------------------------------------------------------------------
    def auto_bridge(self, *, limit: int = AUTO_BRIDGE_LIMIT) -> Dict[str, Any]:
        """Execute the policy-automatic follow-ups of open recommendations, without an operator click.

        * Only actions the control plane's built-in policy covers in the current control state (read-only
          diagnostics, reports; ``operator.notify`` only for high/critical recommendations). Everything else is
          classified ``approval_required`` (still proposable by the operator) or ``informational`` (no mapping).
        * Each (recommendation, action) runs at most once (persisted ledger), and the same action + payload at most
          once per ``AUTO_BRIDGE_COOLDOWN_HOURS`` across recommendations, so repeated findings cannot loop.
        * Execution goes through ``ControlPlane.submit`` (every gate, signed policy approval, handler verification,
          audit); the outcome or the exact blocked reason is stored in the ledger.
        """
        cs = self.cp.control_state()
        out: Dict[str, Any] = {"control_state": cs["state"], "executed": 0, "blocked": 0, "skipped_duplicate": 0,
                               "approval_required": 0, "informational": 0, "results": []}
        if cs["state"] == EMERGENCY_STOP:
            out["reason"] = cs["reason"]
            return out
        cfg, _err = self.cp._safe_config()
        self._ensure_bridge_ledger()
        recs = self.recs().overview(status="open", limit=100).get("recommendations") or []
        now = self.cp._ts(self.cp._now())
        cutoff = self.cp._ts(self.cp._now() - timedelta(hours=AUTO_BRIDGE_COOLDOWN_HOURS))
        for rec in recs:
            if rec.get("status") not in BRIDGEABLE_STATUSES:
                continue
            options = bridge_options(rec)
            if not options:
                out["informational"] += 1
                continue
            for opt in options:
                a = opt["action_type"]
                if a == "operator.notify" and rec.get("severity") not in ("high", "critical"):
                    continue                     # routine findings are shown in the dashboard, not pushed to the inbox
                ok, why = self.cp.policy_allows(a, cfg, cs["state"])
                if not ok:
                    out["approval_required"] += 1
                    continue
                if out["executed"] + out["blocked"] >= limit:
                    return out
                p_hash = payload_hash(a, opt["payload"])
                with self.cp._tx() as conn:
                    recent = conn.execute("SELECT 1 FROM autonomy_auto_bridge WHERE action_type=? AND payload_hash=? "
                                          "AND at_utc>=? LIMIT 1", (a, p_hash, cutoff)).fetchone()
                    cur = conn.execute("INSERT OR IGNORE INTO autonomy_auto_bridge (rec_id, action_type, payload_hash, "
                                       "outcome, reason, at_utc) VALUES (?,?,?,?,?,?)",
                                       (rec["id"], a, p_hash, "CLAIMED" if not recent else "SKIPPED_COOLDOWN",
                                        "" if not recent else "same action and payload ran recently", now))
                if cur.rowcount != 1 or recent:
                    out["skipped_duplicate"] += 1
                    continue
                try:
                    res = self.cp.submit(a, opt["payload"],
                                         reason=_clip(f"Automatic follow-up of recommendation {rec['id']} "
                                                      f"({rec.get('rule_id')}): {rec.get('title')}", 480),
                                         evidence={"recommendation_id": rec["id"], "rule_id": rec.get("rule_id"),
                                                   "bridge": "automatic policy follow-up"},
                                         proposed_by=BRIDGE_ACTOR)
                    ex = res.get("execution") or {}
                    outcome = "EXECUTED" if ex.get("executed") else ("BLOCKED" if ex else "NOT_EXECUTED")
                    reason = ex.get("reason") or ex.get("status") or res["proposal"]["request"]["status"]
                    request_id = res["proposal"]["request"]["id"]
                except ControlPlaneError as exc:
                    outcome, reason, request_id = "BLOCKED", str(exc)[:300], None
                with self.cp._tx() as conn:
                    conn.execute("UPDATE autonomy_auto_bridge SET outcome=?, reason=?, request_id=? WHERE rec_id=? "
                                 "AND action_type=?", (outcome, str(reason)[:300], request_id, rec["id"], a))
                out["executed" if outcome == "EXECUTED" else "blocked"] += 1
                out["results"].append({"recommendation_id": rec["id"], "action_type": a, "outcome": outcome,
                                       "reason": str(reason)[:200], "request_id": request_id})
        return out

    def _ensure_bridge_ledger(self) -> None:
        with self.cp._tx() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS autonomy_auto_bridge (rec_id TEXT NOT NULL, action_type TEXT NOT NULL, "
                         "payload_hash TEXT NOT NULL, request_id TEXT, outcome TEXT NOT NULL, reason TEXT, "
                         "at_utc TEXT NOT NULL, PRIMARY KEY (rec_id, action_type))")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_bridge_action ON autonomy_auto_bridge (action_type, payload_hash, at_utc)")

    def auto_bridge_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            self._ensure_bridge_ledger()
            with self.cp._read() as conn:
                return [dict(r) for r in conn.execute("SELECT * FROM autonomy_auto_bridge ORDER BY at_utc DESC LIMIT ?",
                                                      (int(limit),)).fetchall()]
        except Exception:
            return []

    # -- overview -------------------------------------------------------------------------------------------
    def overview(self) -> Dict[str, Any]:
        """Everything the Autopilot view shows, read from persisted state only (no action, no write except the
        control plane's own expiry maintenance)."""
        st = self.cp.status()
        perms = self.cp.permissions()
        safety = self.cp.safety_state()
        cfg, cfg_err = self.cp._safe_config()
        ks = self.cp.kill_switches(cfg, cfg_err)
        global_level = Level.parse(cfg.get("global_level", "OFF")).name if not cfg_err else "OFF"
        state = ks.get("control_state")
        categories = {}
        for cat in registry.CATEGORIES:
            eff = self.cp._effective(cfg, cat)
            blocked = []
            if ks["execution_gate_closed"]:
                blocked.append(ks["execution_gate"])
            if eff < Level.APPROVED_EXECUTION:
                blocked.append(f"effective level {eff.name} (execution needs APPROVED_EXECUTION)")
            automatic = [a for a, s in registry.REGISTRY.items() if not s.prohibited and s.category == cat and state
                         and not cfg_err and self.cp.policy_allows(a, cfg, state)[0]]
            categories[cat] = {"level": Level.parse(cfg.get("category_levels", {}).get(cat, "OFF")).name,
                               "effective": eff.name, "can_observe": eff >= Level.OBSERVE,
                               "can_recommend": eff >= Level.RECOMMEND,
                               "can_execute": eff >= Level.APPROVED_EXECUTION and not ks["execution_gate_closed"],
                               "automatic_actions": automatic,      # run under the built-in policy despite `blocked`
                               "blocked_reasons": blocked}
        pending = self.cp.list_requests("PENDING", 50)
        approved = self.cp.list_requests("APPROVED", 50)
        executions = self.cp.list_executions(50)
        events = self.cp.audit_events(60)
        rejected = [e for e in events if e.get("event_type") in ("REJECTED", "CANCELLED", "EXECUTION_BLOCKED",
                                                                  "PROPOSAL_REFUSED", "BRIDGE_REFUSED", "PROHIBITED_ACTION_BLOCKED")]
        # Kill switches block EXECUTION only; observation and proposals continue at their level. Reporting OFF
        # whenever any switch is active (the startup switch always is until autonomy_startup.json exists) hid an
        # active OBSERVE/RECOMMEND phase, so the mode is the global level and execution is reported separately.
        mode = global_level
        mode_note = (ks["execution_gate"] if ks["execution_gate_closed"] and global_level != "OFF"
                     else ("autonomy is off" if global_level == "OFF" else ""))
        try:
            control = self.cp.control_state(cfg, cfg_err)
            control.pop("kill_switches", None)
        except Exception as exc:  # noqa: BLE001
            control = {"state": EMERGENCY_STOP, "reason": f"control state unavailable ({type(exc).__name__})"}
        out = {
            "control_state": control, "automatic_follow_ups": self.auto_bridge_history(30),
            "mode": mode, "mode_note": mode_note, "global_level": global_level, "configuration_error": cfg_err,
            "execution_possible": any(c["can_execute"] for c in categories.values()),
            "kill_switches": ks, "categories": categories, "standing_approvals": perms.get("standing_approvals", {}),
            "pending_approvals": pending, "approved_awaiting_execution": approved,
            "executions": executions,
            "execution_counts": _count([x.get("outcome") for x in executions]),
            "recent_decisions": events[:40], "refused_or_blocked": rejected[:20],
            "safety": safety, "audit_chain": self.cp.verify_audit_chain(),
            "policy": action_policy(), "request_counts": st.get("requests_by_status") or {},
        }
        out["recommendations"] = self._recommendations_view()
        out["self_healing"] = self._component("self_healing")
        out["community"] = self._component("community")
        out["publishing"] = self._component("publishing")
        return out

    def _recommendations_view(self) -> Dict[str, Any]:
        try:
            ov = self.recs().overview(status="open", limit=50)
        except Exception as exc:                                    # noqa: BLE001 - shown, never raised
            return {"available": False, "error": type(exc).__name__}
        recs = ov.get("recommendations") or ov.get("items") or []
        return {"available": True, "state": ov.get("state"),
                "open": [{"id": r["id"], "rule_id": r.get("rule_id"), "title": r.get("title"),
                          "severity": r.get("severity"), "priority_label": r.get("priority_label"),
                          "status": r.get("status"),
                          "follow_ups": [{k: o[k] for k in ("action_type", "category", "risk", "tier")}
                                         for o in bridge_options(r)]} for r in recs[:30]]}

    def _component(self, name: str) -> Dict[str, Any]:
        try:
            if name == "self_healing":
                eng = self._heal
                if eng is None:
                    from backend.app.autonomy.self_healing.engine import get_engine
                    eng = get_engine()
                ov = eng.overview(limit=5)
                return {"available": True, "state": ov.get("state"), "stats": ov.get("stats")}
            if name == "community":
                svc = self._community
                if svc is None:
                    from backend.app.community.service import get_service
                    svc = get_service()
                st = svc.schedule_status()
                return {"available": True, "enabled": st["config"].get("enabled"), "state": st.get("state"),
                        "next_run_utc": st.get("next_run_utc"), "timezone": st.get("timezone"),
                        "kill_switches": st.get("kill_switches")}
            pub = self._publishing
            if pub is None:
                from backend.app.community.publishing import PublishingService
                from backend.app.community.service import get_service
                pub = PublishingService(get_service())
            ps = pub.status()
            cap = ps.get("capability") or {}
            return {"available": True, "capability": cap.get("outcome"), "enabled": ps.get("publishing_enabled"),
                    "api_publishing_ready": ps.get("api_publishing_ready"),
                    "awaiting": len(ps.get("approved_awaiting") or []), "attention": len(ps.get("attention") or [])}
        except Exception as exc:                                    # noqa: BLE001
            return {"available": False, "error": type(exc).__name__}


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


_instance: Optional[Autopilot] = None


def get_autopilot() -> Autopilot:
    global _instance
    if _instance is None:
        from backend.app.autonomy.control_plane import get_control_plane
        _instance = Autopilot(get_control_plane())
    return _instance
