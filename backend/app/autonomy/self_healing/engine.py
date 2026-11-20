"""Stage 3.32D self-healing engine: orchestration under the Stage 3.32B control plane.

Gating (re-checked at every scan and every operator action; fail closed), effective level of ``self_healing``:
  any kill switch -> BLOCKED: no scan, no proposal, no execution (one audit event); everything stays readable.
  OFF             -> nothing runs, nothing is written.
  OBSERVE         -> failures are detected, classified, diagnosed and planned (stored as incidents = observations);
                     nothing is proposed or executed.
  RECOMMEND       -> additionally proposes allow-listed actions into the 3.32B approval queue.
  APPROVED_EXECUTION -> proposals with a valid approval may run, through ``ControlPlane.execute`` only
                     (registry, prohibition, levels, kill switches, signed approval, payload hash, daily limits,
                     claim, timeout, retry limit, verification). Standing approvals apply to LOW-risk diagnostics only;
                     recovery actions need an explicit approval for every request.

The engine identity (``self-healing-engine``) is reserved: it can never approve, reject, execute as an operator or
change permissions. It never calls the scheduler, workers, renderers, publishing, OAuth or configuration. Recovery
success means "the action was executed AND independently verified"; it never marks a production job completed.
There is no background loop: scans run when an operator (or a future, separately approved caller) asks.
"""
from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import (
    APPROVED as CP_APPROVED, CANCELLED as CP_CANCELLED, COMPLETED as CP_COMPLETED, EXECUTING as CP_EXECUTING,
    EXPIRED as CP_EXPIRED, FAILED as CP_FAILED, PENDING as CP_PENDING, REJECTED as CP_REJECTED,
    Conflict, ControlPlane, ControlPlaneError, NotFound, PermissionDenied, safe_error, validate_operator,
)
from backend.app.autonomy.registry import Level
from backend.app.autonomy.self_healing import classification as C
from backend.app.autonomy.self_healing import store as S
from backend.app.autonomy.self_healing.diagnostics import DiagnosticEngine
from backend.app.autonomy.self_healing.evidence import EvidenceSource
from backend.app.autonomy.self_healing.planner import DIAG, RecoveryPlanner
from backend.app.autonomy.self_healing.services import inside, sha256_file

ENGINE_ACTOR = "self-healing-engine"
CATEGORY = "self_healing"
HISTORY_DAYS = 7
DETECT_DAYS = 2
MIN_SCAN_INTERVAL_SECONDS = 300
MAX_NEW_PER_SCAN = 50
MAX_REPLAN_PER_SCAN = 30
MAX_NOTIFICATIONS_PER_SCAN = 5
MAX_NOTIFICATIONS_PER_DAY = 24
NOTIFY_COOLDOWN_HOURS = 6
EXPLAINER_ENV = "CRAZZYDOCS_SELFHEAL_EXPLAINER"
MAX_EXPLANATIONS_PER_SCAN = 2
TS_FMT = S.TS_FMT
CP_TO_STATUS = {CP_PENDING: S.AWAITING_APPROVAL, CP_APPROVED: S.APPROVED, CP_EXECUTING: S.EXECUTING}


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _norm_ts(value: Any) -> Optional[str]:
    from backend.app.services.operational_intelligence import _parse_ts
    dt = _parse_ts(value)
    return _ts(dt) if dt else None


def severity_of(cl: C.Classification, distinct_jobs: int, occurrences: int) -> str:
    if (cl.failure_class == C.DETERMINISTIC and distinct_jobs >= 3) or cl.subtype in ("disk", "memory"):
        return "critical"
    if cl.failure_class in (C.DETERMINISTIC, C.CONFIGURATION) or cl.subtype == "file_handles":
        return "high"
    if cl.failure_class in (C.RESOURCE, C.DATA, C.UNKNOWN) or occurrences >= 3:
        return "medium"
    return "low"


class DiagnosticExplainer:
    """Optional plain-language wording of diagnostic findings. Off unless CRAZZYDOCS_SELFHEAL_EXPLAINER=gemini.
    It never decides or executes anything; failures are ignored."""

    def __init__(self, generate: Optional[Callable[[str], str]] = None):
        self._generate = generate

    def _default_generate(self, prompt: str) -> str:
        from backend.app.content.grounded_script_writer import default_client
        client, model = default_client()
        if client is None:
            raise RuntimeError("Gemini is not configured")
        return str(getattr(client.models.generate_content(model=model, contents=prompt), "text", "") or "")

    def explain(self, incident: Dict[str, Any]) -> str:
        d = incident.get("diagnosis") or {}
        prompt = ("Explain this production failure diagnosis to a non-technical operator in at most 3 sentences. "
                  "Keep every uncertainty explicit; do not suggest actions; add no facts.\n\n"
                  f"Class: {incident['failure_class']} ({incident['certainty']})\n"
                  f"Root cause status: {(d.get('root_cause') or {}).get('status')}: {(d.get('root_cause') or {}).get('statement')}\n"
                  "Findings:\n" + "\n".join(f"- {f.get('text')}" for f in (d.get("findings") or [])[:8]))
        return (self._generate or self._default_generate)(prompt).strip()[:1200]


class SelfHealingEngine:
    def __init__(self, control_plane: ControlPlane, store: Optional[S.SelfHealingStore] = None,
                 intelligence: Optional[Callable[[], Any]] = None, explainer: Optional[DiagnosticExplainer] = None,
                 environ: Optional[Dict[str, str]] = None, clock: Optional[Callable[[], datetime]] = None):
        self.cp = control_plane
        self.store = store or S.SelfHealingStore(control_plane.db_path)
        self.intelligence = intelligence or control_plane.intelligence
        env = os.environ if environ is None else environ
        self.explainer_enabled = env.get(EXPLAINER_ENV, "").strip().lower() == "gemini"
        self.explainer = explainer if explainer is not None else (DiagnosticExplainer() if self.explainer_enabled else None)
        self.clock = clock or control_plane.clock
        self.diagnostics = DiagnosticEngine()
        self.planner = RecoveryPlanner()
        self._scan_lock = threading.Lock()
        self._op_lock = threading.Lock()

    def _now(self) -> datetime:
        n = self.clock()
        return n if n.tzinfo else n.replace(tzinfo=timezone.utc)

    def evidence(self) -> EvidenceSource:
        return EvidenceSource(self.intelligence, self.cp.db_path)

    # -- gating ---------------------------------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        cfg, err = self.cp._safe_config()
        ks = self.cp.kill_switches(cfg, err)
        level = self.cp._effective(cfg, CATEGORY)
        notif = self.cp._effective(cfg, "notifications")
        try:
            permitted = all(registry.require_permitted(a).category == CATEGORY
                            for a in (DIAG, "selfheal.provider.retry_readonly", "selfheal.asset.reacquire_staged"))
        except Exception:
            permitted = False
        if not permitted:
            mode, reason = "BLOCKED", "self-healing action types are not permitted registry entries"
        elif ks["any_active"]:
            mode, reason = "BLOCKED", f"kill switch active: {', '.join(ks['active'])}"
        elif level == Level.OFF:
            mode, reason = "OFF", "the self_healing autonomy level is OFF"
        elif level == Level.OBSERVE:
            mode, reason = "OBSERVE", "detect, classify and diagnose only (level OBSERVE)"
        elif level == Level.RECOMMEND:
            mode, reason = "RECOMMEND", "proposals go to the approval queue; nothing executes"
        else:
            mode, reason = "APPROVED_EXECUTION", "approved, allow-listed actions may execute through the control plane"
        grants = {a: g for a, g in (cfg.get("standing_approvals") or {}).items() if a.startswith("selfheal.")}
        return {"mode": mode, "reason": reason, "level": level.name, "kill_switches": ks["active"],
                "configuration_error": err, "notifications_level": notif.name, "standing_approvals": grants,
                "explainer": "gemini" if self.explainer_enabled else "off", "enabled": mode in ("OBSERVE", "RECOMMEND",
                                                                                                "APPROVED_EXECUTION")}

    # -- scan -----------------------------------------------------------------------------------------------
    def scan(self, triggered_by: str = ENGINE_ACTOR, force: bool = False) -> Dict[str, Any]:
        if not self._scan_lock.acquire(blocking=False):
            return {"ran": False, "mode": "BUSY", "reason": "another scan is running"}
        try:
            return self._scan(str(triggered_by)[:100], force)
        finally:
            self._scan_lock.release()

    def _scan(self, triggered_by: str, force: bool) -> Dict[str, Any]:
        now = self._now()
        st = self.state()
        if st["mode"] == "OFF":
            return {"ran": False, "mode": "OFF", "reason": st["reason"], "state": st}
        if st["mode"] == "BLOCKED":
            self.cp._audit_now("SELF_HEALING_SCAN_BLOCKED", ENGINE_ACTOR, reason=st["reason"],
                               details={"triggered_by": triggered_by})
            return {"ran": False, "mode": "BLOCKED", "reason": st["reason"], "state": st}
        last = self.store.last_scan()
        if not force and last and last["started_at_utc"] > _ts(now - timedelta(seconds=MIN_SCAN_INTERVAL_SECONDS)):
            return {"ran": False, "mode": st["mode"], "reason": "scanned less than 5 minutes ago", "last_scan": last}
        propose = st["mode"] in ("RECOMMEND", "APPROVED_EXECUTION")
        run: Dict[str, Any] = {"run_id": "shs_" + secrets.token_hex(6), "started_at": _ts(now), "mode": st["mode"],
                               "triggered_by": triggered_by, "new_failures": 0, "incidents_created": 0,
                               "incidents_updated": 0, "proposals": 0, "executed": 0, "replanned": 0,
                               "notifications": {"sent": 0, "queued": 0, "deduplicated": 0, "rate_limited": 0,
                                                 "suppressed_level": 0}, "errors": [], "explanations": 0}
        self._notify_budget = MAX_NOTIFICATIONS_PER_SCAN
        run["synced"] = self.sync(run=run)
        src = self.evidence()
        hist = src.failures(HISTORY_DAYS)
        if not hist["available"]:
            run["errors"].append(f"failure history unavailable: {hist['error']}")
            run["finished_at"] = _ts(self._now())
            self.store.record_scan(run)
            return {"ran": True, **run}
        cursor = self.store.last_cursor()
        floor = _ts(now - timedelta(days=DETECT_DAYS))
        since = max(floor, _ts(datetime.strptime(cursor, TS_FMT).replace(tzinfo=timezone.utc) - timedelta(minutes=5))) if cursor else floor
        failures = [dict(f, at_norm=_norm_ts(f.get("at"))) for f in hist["failures"]]
        new = sorted([f for f in failures if f["at_norm"] and f["at_norm"] >= since], key=lambda f: f["at_norm"])
        run["new_failures"] = len(new)
        history = [{"signature": f["signature"], "job_id": f.get("job_id"), "at": f["at_norm"], "stage": f.get("stage"),
                    "recovered_later": f.get("recovered_later"), "message": f.get("message")} for f in failures]
        new_cursor = cursor
        explained = 0
        for f in new[:MAX_NEW_PER_SCAN]:
            try:
                created, inc = self._ingest(f, history, now, propose, run)
                if self.explainer and explained < MAX_EXPLANATIONS_PER_SCAN and created and inc.get("diagnosis"):
                    try:
                        text = self.explainer.explain(inc)
                        with self.store.tx() as conn:
                            self.store.set_incident(conn, inc["id"], now, explanation=text)
                        explained += 1
                    except Exception:
                        pass                                      # wording is optional, never required
            except Exception as exc:                              # one bad record never stops the scan
                run["errors"].append(f"{f.get('job_id')}: {safe_error(exc)}")
            new_cursor = max(new_cursor or "", f["at_norm"])
        run["explanations"] = explained
        if propose:
            self._replan_open(history, now, run)
        self._notify_expirations(new, now, run)
        if run.get("awaiting_new"):
            n = run["awaiting_new"]
            self._notify("awaiting_approval", "awaiting_approval", f"{n} self-healing request{'s' if n != 1 else ''} await approval",
                         f"{n} new self-healing request(s) are waiting in the approval queue (Self-Healing tab).", "info", now, run)
        run["cursor"] = new_cursor
        run["finished_at"] = _ts(self._now())
        try:
            run["pruned"] = self.store.prune(now)
        except Exception as exc:
            run["errors"].append(f"prune: {safe_error(exc)}")
        self.store.record_scan(run)
        self.cp._audit_now("SELF_HEALING_SCAN_COMPLETED", ENGINE_ACTOR,
                           reason=f"new_failures={run['new_failures']} created={run['incidents_created']} "
                                  f"proposals={run['proposals']}",
                           details={k: run[k] for k in ("run_id", "mode", "triggered_by", "incidents_updated",
                                                         "executed", "notifications", "errors")})
        return {"ran": True, **run}

    def _ingest(self, f: Dict[str, Any], history: List[Dict[str, Any]], now: datetime, propose: bool,
                run: Dict[str, Any]) -> Any:
        msg = f.get("message") or ""
        job = self.evidence().job(f.get("job_id"))
        full = job.get("error_details") if job.get("found") and C.normalize(job.get("error_details") or "") == C.normalize(msg) else msg
        cl = C.classify(full or msg, error_type=f.get("type") or "", cause=f.get("cause") or "",
                        history=[h for h in history if h["at"] and h["at"] <= f["at_norm"]])
        cl.signature = f["signature"]                       # keep the 3.32A-derived signature (same template + cause)
        rec = cl.recurrence
        sev = severity_of(cl, rec.get("jobs", 0), rec.get("occurrences", 0))
        with self.store.tx() as conn:
            res = self.store.upsert_incident(conn, dedupe_key=f"{f.get('job_id') or '-'}|{cl.signature}",
                                             signature=cl.signature, template=cl.template, cause=f.get("cause") or "",
                                             job_id=f.get("job_id"), stage=f.get("stage"), error_type=cl.error_type,
                                             message=full or msg, at=f["at_norm"], failure_class=cl.failure_class,
                                             certainty=cl.certainty, classification=cl.to_dict(), severity=sev, now=now)
            inc = res["incident"]
            if res["created"] or res.get("new_occurrence"):
                self.store.mark_recurrence(conn, cl.signature, f["at_norm"])
        if not res["created"] and not res.get("new_occurrence"):
            return False, inc
        run["incidents_created" if res["created"] else "incidents_updated"] += 1
        if res["created"]:
            self.cp._audit_now("SELF_HEALING_INCIDENT_DETECTED", ENGINE_ACTOR, reason=f"{cl.failure_class}: {cl.template}"[:300],
                               details={"incident_id": inc["id"], "job_id": inc.get("job_id"), "class": cl.failure_class,
                                        "certainty": cl.certainty, "signature": cl.signature})
        with self.store.tx() as conn:
            self.store.set_incident(conn, inc["id"], now, status=S.DIAGNOSING)
        diagnosis = self.diagnostics.diagnose(inc, cl, job, history, now)
        prev = (inc.get("diagnosis") or {}).get("action_findings") or {}
        diagnosis["action_findings"] = prev
        inc = dict(inc, diagnosis=diagnosis)
        plan = self.planner.plan(inc, diagnosis, job, store=self.store, learning=self.store.learning(), now=now)
        status = S.UNSAFE if plan["status_hint"] == "UNSAFE" else S.DIAGNOSED
        with self.store.tx() as conn:
            self.store.set_incident(conn, inc["id"], now, diagnosis=diagnosis, plan=plan, status=status)
        inc = dict(inc, plan=plan, status=status)
        self._notify_incident(inc, cl, now, run)          # incident alerts first: they matter more than queue updates
        if propose and plan["status_hint"] == "PROPOSABLE":
            self._propose(inc, plan, now, run)
        self._refresh_incident(inc["id"], now)             # open requests keep showing; a recurrence re-opens it
        return res["created"], self.store.get_incident(inc["id"])

    def _replan_open(self, history: List[Dict[str, Any]], now: datetime, run: Dict[str, Any]) -> None:
        """Incidents left DIAGNOSED / UNSAFE (e.g. a worker was active) are re-planned when nothing is open for them."""
        for inc in self.store.list_incidents(status="open", limit=200):
            if run["replanned"] >= MAX_REPLAN_PER_SCAN:
                break
            if inc["status"] not in (S.DIAGNOSED, S.UNSAFE) or not inc.get("diagnosis"):
                continue
            if any(r["status"] in S.OPEN_RECOVERY for r in self.store.recoveries_for(inc["id"])):
                continue
            job = self.evidence().job(inc.get("job_id"))
            plan = self.planner.plan(inc, inc["diagnosis"], job, store=self.store, learning=self.store.learning(), now=now)
            status = S.UNSAFE if plan["status_hint"] == "UNSAFE" else S.DIAGNOSED
            with self.store.tx() as conn:
                self.store.set_incident(conn, inc["id"], now, plan=plan, status=status)
            run["replanned"] += 1
            if plan["status_hint"] == "PROPOSABLE":
                self._propose(dict(inc, plan=plan, status=status), plan, now, run)

    def _propose(self, inc: Dict[str, Any], plan: Dict[str, Any], now: datetime, run: Dict[str, Any]) -> None:
        for a in plan["actions"]:
            if a["blocked"]:
                continue
            spec = registry.require_permitted(a["action_type"])
            with self.store.tx() as conn:
                if self.store.open_recovery(conn, inc["id"], spec.action_type, a["payload_hash"]):
                    continue
            evidence = {"incident_id": inc["id"], "job_id": inc.get("job_id"), "class": inc["failure_class"],
                        "certainty": inc["certainty"], "signature": inc["signature"], "template": inc["template"][:200],
                        "purpose": a["purpose"], "resource_locks": a["resource_locks"]}
            try:
                res = self.cp.submit(spec.action_type, a["payload"], reason=f"Self-healing: {a['purpose']}"[:480],
                                     evidence=evidence, proposed_by=ENGINE_ACTOR)
            except ControlPlaneError as exc:
                run["errors"].append(f"propose {spec.action_type}: {safe_error(exc)}")
                continue
            req = res["proposal"]["request"]
            status = CP_TO_STATUS.get(req["status"], S.RECOVERY_PROPOSED)
            with self.store.tx() as conn:
                if self.store.open_recovery(conn, inc["id"], spec.action_type, a["payload_hash"]):
                    continue
                self.store.create_recovery(conn, incident=inc, action_type=spec.action_type, payload=a["payload"],
                                           payload_hash=a["payload_hash"], request_id=req["id"],
                                           preconditions={"planner_checks": plan["checks"], "required": a["preconditions"],
                                                          "resource_locks": a["resource_locks"]},
                                           approval_requirement=spec.approval, timeout_seconds=spec.timeout_seconds,
                                           retry_limit=spec.retry_limit, status=S.RECOVERY_PROPOSED, now=now,
                                           reason=a["purpose"])
                rid = conn.execute("SELECT id FROM selfheal_recoveries WHERE request_id=? ORDER BY created_at_utc DESC LIMIT 1",
                                   (req["id"],)).fetchone()[0]
                self.store.update_recovery(conn, rid, now, status=status)
            run["proposals"] += 1
            if res.get("execution"):
                run["executed"] += 1
            if status == S.AWAITING_APPROVAL:                 # aggregated into one notification per scan
                run["awaiting_new"] = run.get("awaiting_new", 0) + 1
        self.sync(run=run)
        self._refresh_incident(inc["id"], now)

    # -- reconciliation with the control plane ---------------------------------------------------------------
    def sync(self, run: Optional[Dict[str, Any]] = None) -> int:
        """Mirror each open recovery's approval request; finalize, verify and learn from finished ones."""
        changed = 0
        now = self._now()
        try:
            self.cp.run_maintenance()
        except Exception:
            pass
        for rec in self.store.open_recoveries_with_requests():
            try:
                req = self.cp.get_request(rec["request_id"])
            except NotFound:
                with self.store.tx() as conn:
                    self.store.update_recovery(conn, rec["id"], now, status=S.CANCELLED, final_status=S.CANCELLED,
                                               status_reason="approval request no longer exists")
                changed += 1
                continue
            except Exception:
                continue
            changed += self._apply_request_state(rec, req, now, run)
        return changed

    def _apply_request_state(self, rec: Dict[str, Any], req: Dict[str, Any], now: datetime,
                             run: Optional[Dict[str, Any]]) -> int:
        s = req["status"]
        approval = req.get("approval_mode") or rec.get("approval_outcome")
        if s in CP_TO_STATUS:
            new = CP_TO_STATUS[s]
            if new == rec["status"] and approval == rec.get("approval_outcome"):
                return 0
            with self.store.tx() as conn:
                self.store.update_recovery(conn, rec["id"], now, expect_status=S.OPEN_RECOVERY, status=new,
                                           approval_outcome=approval)
            self._refresh_incident(rec["incident_id"], now)
            return 1
        if s in (CP_REJECTED, CP_CANCELLED, CP_EXPIRED):
            final = S.EXPIRED if s == CP_EXPIRED else S.CANCELLED
            outcome = "REJECTED" if s == CP_REJECTED else ("EXPIRED" if s == CP_EXPIRED else "CANCELLED")
            with self.store.tx() as conn:
                if not self.store.update_recovery(conn, rec["id"], now, expect_status=S.OPEN_RECOVERY, status=final,
                                                  final_status=final, approval_outcome=outcome, finished_at_utc=_ts(now),
                                                  status_reason=req.get("decision_reason") or outcome.lower()):
                    return 0
                rec2 = self.store.get_recovery(rec["id"], conn)
                inc = self.store.get_incident(rec["incident_id"], conn)
                if inc:
                    self.store.record_outcome(conn, rec2, inc, "NOT_RUN", {}, now)
            self._audit_finish(rec2, req)
            self._refresh_incident(rec["incident_id"], now)
            return 1
        if s in (CP_COMPLETED, CP_FAILED):
            return self._finalize(rec, req, now, run)
        return 0

    def _executions(self, request_id: str) -> List[Dict[str, Any]]:
        with self.cp._read() as conn:
            return [dict(r) for r in conn.execute("SELECT attempt, started_at_utc, finished_at_utc, outcome, error FROM "
                                                  "autonomy_executions WHERE request_id=? ORDER BY attempt", (request_id,))]

    def independent_check(self, rec: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        """Second, engine-level verification of a finished action, from its stored result (no re-execution)."""
        result = body.get("result") or {}
        action, payload = rec["action_type"], rec.get("payload") or {}
        base = self.cp.autonomy_dir / "self_healing"
        try:
            if action == DIAG:
                ok = result.get("diagnostic") == payload.get("diagnostic") and result.get("status") == "ok"
                if ok and payload.get("diagnostic") == "generate_diagnostic_report":
                    data = result.get("data") or {}
                    p = Path(str(data.get("path", "")))
                    ok = inside(p, [base / "reports"]) and p.is_file() and sha256_file(p) == data.get("sha256")
                return {"ok": bool(ok), "check": "diagnostic result consistent" + (" and report intact" if
                                                                                  payload.get("diagnostic") == "generate_diagnostic_report" else "")}
            if action == "selfheal.asset.reacquire_staged":
                p = Path(str(result.get("path", "")))
                ok = inside(p, [base / "staging"]) and p.is_file() and sha256_file(p) == result.get("sha256")
                return {"ok": bool(ok), "check": "staged file still present and unchanged"}
            if action == "selfheal.provider.retry_readonly":
                ok = result.get("ok") is True and isinstance(result.get("count"), int)
                return {"ok": bool(ok), "check": "provider response recorded as parsed"}
        except OSError as exc:
            return {"ok": False, "check": "independent check could not read the artifact", "error": type(exc).__name__}
        return {"ok": False, "check": "no independent check exists for this action type"}

    def _finalize(self, rec: Dict[str, Any], req: Dict[str, Any], now: datetime, run: Optional[Dict[str, Any]]) -> int:
        body = req.get("execution_result") or {}
        verification = body.get("verification") or {}
        verdict = verification.get("verdict") if isinstance(verification, dict) else None
        execs = self._executions(req["id"])
        with self.store.tx() as conn:
            if not self.store.update_recovery(conn, rec["id"], now, expect_status=S.OPEN_RECOVERY, status=S.VERIFYING,
                                              attempts=len(execs), started_at_utc=execs[0]["started_at_utc"] if execs else None):
                return 0
        if req["status"] == CP_COMPLETED and verdict == S.VERIFIED:
            second = self.independent_check(rec, body)
            if second["ok"]:
                vres, final = S.VERIFIED, (S.DIAGNOSED if rec["action_type"] == DIAG else S.RECOVERED)
            else:
                vres, final = S.VERIFY_FAILED, S.FAILED
            verification = dict(verification, independent_check=second)
        elif req["status"] == CP_COMPLETED:
            vres, final = S.UNVERIFIED, S.FAILED          # completed without a verification verdict: never a success
        else:
            vres = verdict if verdict in (S.VERIFY_FAILED, S.UNVERIFIED) else (S.VERIFY_FAILED if verification else None)
            final = S.FAILED
        timed_out = any(e.get("outcome") == "TIMED_OUT" for e in execs)
        reason = body.get("error") or ("verified" if final in (S.RECOVERED, S.DIAGNOSED) else "verification failed")
        if timed_out:
            reason = f"timed out: {reason}"
        exec_result = "SUCCEEDED" if req["status"] == CP_COMPLETED else ("TIMED_OUT" if timed_out else "FAILED")
        with self.store.tx() as conn:
            self.store.update_recovery(conn, rec["id"], now, status=final, final_status=final, verification_result=vres,
                                       verification=verification, execution_result=body,
                                       finished_at_utc=(execs[-1]["finished_at_utc"] if execs else None) or _ts(now),
                                       approval_outcome=req.get("approval_mode") or rec.get("approval_outcome"),
                                       status_reason=str(reason)[:500])
            rec2 = self.store.get_recovery(rec["id"], conn)
            inc = self.store.get_incident(rec["incident_id"], conn)
            if inc:
                findings = (body.get("result") or {}).get("data") if rec["action_type"] == DIAG else body.get("result")
                self.store.record_outcome(conn, rec2, inc, exec_result, findings or {}, now)
                if rec["action_type"] == DIAG and final == S.DIAGNOSED:
                    diag = inc.get("diagnosis") or {}
                    af = dict(diag.get("action_findings") or {})
                    af[(rec.get("payload") or {}).get("diagnostic", "diagnostic")] = {"at": _ts(now), "recovery_id": rec["id"],
                                                                                    "data": findings}
                    diag["action_findings"] = af
                    self.store.set_incident(conn, inc["id"], now, diagnosis=diag)
        self._audit_finish(rec2, req)
        self._refresh_incident(rec["incident_id"], now)
        if inc and rec["action_type"] != DIAG:
            if final == S.RECOVERED:
                self._notify("recovered:" + rec["id"], "recovery_succeeded", "Self-healing recovery verified",
                             f"{rec['action_type']} for incident {inc['id']} was executed and verified. The production job "
                             "itself is not marked complete by this.", "info", now, run)
            else:
                self._notify("failed:" + rec["id"], "recovery_failed", "Self-healing recovery failed",
                             f"{rec['action_type']} for incident {inc['id']} ended {final} ({vres}): {str(reason)[:300]}",
                             "medium", now, run)
        return 1

    def _audit_finish(self, rec: Optional[Dict[str, Any]], req: Dict[str, Any]) -> None:
        if not rec:
            return
        self.cp._audit_now("SELF_HEALING_RECOVERY_FINISHED", ENGINE_ACTOR, request_id=req["id"], action_type=rec["action_type"],
                           new_state=rec.get("final_status"), reason=str(rec.get("status_reason") or "")[:300],
                           details={"recovery_id": rec["id"], "incident_id": rec["incident_id"],
                                    "verification_result": rec.get("verification_result"),
                                    "approval_outcome": rec.get("approval_outcome")})

    def _refresh_incident(self, incident_id: str, now: datetime) -> None:
        recs = self.store.recoveries_for(incident_id)
        inc = self.store.get_incident(incident_id)
        if not recs or inc is None:
            return
        open_ = [r["status"] for r in recs if r["status"] in S.OPEN_RECOVERY]
        order = (S.EXECUTING, S.VERIFYING, S.APPROVED, S.AWAITING_APPROVAL, S.RECOVERY_PROPOSED)
        # Only results that finished after the latest occurrence describe the current state: if the failure came
        # back after a recovery, the incident is open again (the recurrence is recorded for learning).
        current = [r for r in recs if r["status"] in S.FINAL_RECOVERY
                   and (r.get("finished_at_utc") or r["updated_at_utc"]) >= inc["last_seen_utc"]]
        finals = [r["status"] for r in current]
        reversible_final = [r["status"] for r in current if r["action_type"] != DIAG]
        if not open_ and not current:
            return                                         # keep the diagnosis status (DIAGNOSED / UNSAFE)
        if open_:
            status = next(s for s in order if s in open_)
        elif S.RECOVERED in finals:
            status = S.RECOVERED
        elif S.FAILED in reversible_final:
            status = S.FAILED
        elif all(s == S.EXPIRED for s in finals):
            status = S.EXPIRED
        elif all(s in (S.CANCELLED, S.EXPIRED) for s in finals):
            status = S.CANCELLED
        elif S.DIAGNOSED in finals:
            status = S.DIAGNOSED
        else:
            status = S.FAILED if S.FAILED in finals else S.DIAGNOSED
        with self.store.tx() as conn:
            self.store.set_incident(conn, incident_id, now, status=status)

    # -- operator actions (human identities only; every one passes the control plane) ------------------------
    def _rec_or_404(self, recovery_id: str) -> Dict[str, Any]:
        if not isinstance(recovery_id, str) or not recovery_id.startswith("rcv_") or len(recovery_id) > 20:
            raise NotFound("unknown recovery id")
        rec = self.store.get_recovery(recovery_id)
        if rec is None:
            raise NotFound("unknown recovery id")
        if not rec.get("request_id"):
            raise Conflict("this recovery has no approval request")
        return rec

    def approve(self, recovery_id: str, operator: str, payload_hash: str, reason: str = "") -> Dict[str, Any]:
        op = validate_operator(operator)
        rec = self._rec_or_404(recovery_id)
        res = self.cp.approve(rec["request_id"], op, payload_hash, reason)
        self.sync()
        return {"recovery": self.store.get_recovery(recovery_id), "request": res["request"]}

    def reject(self, recovery_id: str, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        rec = self._rec_or_404(recovery_id)
        res = self.cp.reject(rec["request_id"], op, reason)
        self.sync()
        return {"recovery": self.store.get_recovery(recovery_id), "request": res["request"]}

    def execute(self, recovery_id: str, operator: str) -> Dict[str, Any]:
        """Execute one APPROVED recovery through ControlPlane.execute, after re-checking job and resource
        preconditions. Refused preconditions mark the recovery UNSAFE and cancel its request (by the operator)."""
        op = validate_operator(operator)
        with self._op_lock:
            rec = self._rec_or_404(recovery_id)
            now = self._now()
            inc = self.store.get_incident(rec["incident_id"])
            if inc is None:
                raise NotFound("incident not found")
            refusal = None
            if rec["action_type"] != DIAG and inc.get("job_id"):
                job = self.evidence().job(inc["job_id"])
                if not job.get("found"):
                    refusal = "job state could not be read (fail closed)"
                elif EvidenceSource.worker_active(job, now)["active"]:
                    refusal = f"a worker is executing the job (status {job.get('status')})"
                elif job.get("status") in ("QUEUED", "RETRY_SCHEDULED", "HANDOFF_PENDING", "RETRYING"):
                    refusal = f"the existing scheduler is recovering the job (status {job.get('status')})"
            if refusal is None:
                from backend.app.autonomy.self_healing.planner import resource_keys
                held = [h for h in self.store.held_locks(resource_keys(inc, rec["action_type"], rec.get("payload") or {}), now)
                        if h["owner"] != rec["request_id"]]
                if held:
                    refusal = f"resource lock held by {held[0]['owner']}"
            if refusal:
                try:
                    self.cp.cancel(rec["request_id"], op, f"self-healing preconditions no longer valid: {refusal}"[:480])
                except ControlPlaneError:
                    pass
                with self.store.tx() as conn:
                    self.store.update_recovery(conn, recovery_id, now, expect_status=S.OPEN_RECOVERY, status=S.UNSAFE,
                                               final_status=S.UNSAFE, finished_at_utc=_ts(now), status_reason=refusal)
                self.cp._audit_now("SELF_HEALING_RECOVERY_UNSAFE", op, request_id=rec["request_id"],
                                   action_type=rec["action_type"], reason=refusal, details={"recovery_id": recovery_id})
                self._refresh_incident(rec["incident_id"], now)
                return {"executed": False, "status": S.UNSAFE, "reason": refusal, "recovery": self.store.get_recovery(recovery_id)}
            with self.store.tx() as conn:
                self.store.update_recovery(conn, recovery_id, now, expect_status=(S.APPROVED,), status=S.EXECUTING)
            res = self.cp.execute(rec["request_id"], actor=f"operator:{op}")
            self.sync()
            return {"executed": bool(res.get("executed")), "status": res.get("status"), "reason": res.get("reason") or res.get("error"),
                    "recovery": self.store.get_recovery(recovery_id)}

    # -- notifications ----------------------------------------------------------------------------------------
    def _notify(self, key: str, kind: str, subject: str, message: str, severity: str, now: datetime,
                run: Optional[Dict[str, Any]]) -> None:
        counts = (run or {}).get("notifications") if run else None
        if not self.store.notification_allowed(key, NOTIFY_COOLDOWN_HOURS, now):
            if counts is not None:
                counts["deduplicated"] += 1
            return
        if getattr(self, "_notify_budget", MAX_NOTIFICATIONS_PER_SCAN) <= 0 or \
                self.store.notifications_since(now - timedelta(hours=24)) >= MAX_NOTIFICATIONS_PER_DAY:
            if counts is not None:
                counts["rate_limited"] += 1
            return
        cfg, err = self.cp._safe_config()
        if self.cp._effective(cfg, "notifications") < Level.RECOMMEND:
            if counts is not None:                          # notification category is OFF/OBSERVE: nothing is queued
                counts["suppressed_level"] += 1
            return
        try:
            res = self.cp.submit("operator.notify", {"subject": subject[:120], "message": message[:2000],
                                                     "severity": severity if severity in ("info", "low", "medium", "high") else "high"},
                                 reason=f"Self-healing notification ({kind})", evidence={"kind": kind, "key": key[:120]},
                                 proposed_by=ENGINE_ACTOR)
        except ControlPlaneError as exc:
            if counts is not None:
                counts.setdefault("errors", []).append(safe_error(exc))
            return
        executed = bool(res.get("execution") and res["execution"].get("executed"))
        self.store.record_notification(key, kind, "delivered" if executed else "queued_for_approval", now)
        self._notify_budget = getattr(self, "_notify_budget", MAX_NOTIFICATIONS_PER_SCAN) - 1
        if counts is not None:
            counts["sent" if executed else "queued"] += 1

    def _notify_incident(self, inc: Dict[str, Any], cl: C.Classification, now: datetime, run: Dict[str, Any]) -> None:
        if inc.get("severity") == "critical":
            self._notify("critical:" + inc["signature"], "critical_failure", f"Critical failure: {cl.failure_class.lower()}",
                         f"{inc['template'][:300]} (job {inc.get('job_id')}, {cl.certainty.lower()} evidence)", "high", now, run)
        rec = cl.recurrence or {}
        if cl.failure_class == C.DETERMINISTIC and rec.get("occurrences", 0) >= C.RECURRENCE_MIN_OCCURRENCES:
            self._notify("deterministic:" + inc["signature"], "repeated_deterministic",
                         "Repeated deterministic error",
                         f"{inc['template'][:300]} occurred {rec.get('occurrences')} times across {rec.get('jobs')} jobs; "
                         "retrying unchanged will not help.", "high", now, run)

    def _notify_expirations(self, new: List[Dict[str, Any]], now: datetime, run: Dict[str, Any]) -> None:
        jobs = sorted({f.get("job_id") for f in new if f.get("job_id") and (f.get("cause") == "recovery_window_expired"
                                                                             or f.get("class") == "EXPIRED")})
        for j in jobs[:5]:
            self._notify("expired:" + j, "recovery_window_expired", "Recovery window expired",
                         f"Job {j} reached the end of its recovery window without completing.", "medium", now, run)

    # -- reads --------------------------------------------------------------------------------------------------
    def overview(self, limit: int = 50) -> Dict[str, Any]:
        out: Dict[str, Any] = {"state": self.state(), "database_available": True,
                               "note": "Self-healing diagnoses failures and runs only allow-listed, approved, verified "
                                       "actions. Job retries, re-renders, restarts and configuration stay with the "
                                       "existing scheduler and the operator."}
        try:
            self.sync()
            out.update(stats=self.store.stats(), incidents=self.store.list_incidents("open", limit),
                       awaiting=self.store.list_recoveries("open", 100), history=self.store.list_recoveries("history", 50),
                       learning=self.store.learning(50), last_scan=self.store.last_scan())
        except Exception as exc:
            out.update(database_available=False, error=safe_error(exc), stats={}, incidents=[], awaiting=[], history=[],
                       learning=[], last_scan=None)
        return out

    def incident_detail(self, incident_id: str) -> Dict[str, Any]:
        if not isinstance(incident_id, str) or not incident_id.startswith("inc_") or len(incident_id) > 20:
            raise NotFound("unknown incident id")
        self.sync()
        inc = self.store.get_incident(incident_id)
        if inc is None:
            raise NotFound("unknown incident id")
        recs = self.store.recoveries_for(incident_id)
        audit = []
        for r in recs:
            if r.get("request_id"):
                audit += self.cp.audit_events(limit=50, request_id=r["request_id"])
        with self.store.read() as conn:
            related = self.store.signature_history(conn, inc["signature"])
        return {"incident": inc, "recoveries": recs, "audit": sorted(audit, key=lambda e: e["seq"]),
                "related_incidents": [r for r in related if r["id"] != incident_id]}

    def learning_summary(self) -> Dict[str, Any]:
        """Read-only outcome data for the Stage 3.32C recommendation engine."""
        return {"learning": self.store.learning(200), "deterministic": self.store.deterministic_signatures(),
                "stats": self.store.stats()}


_instance: Optional[SelfHealingEngine] = None
_lock = threading.Lock()


def get_engine() -> SelfHealingEngine:
    global _instance
    with _lock:
        if _instance is None:
            from backend.app.autonomy.control_plane import get_control_plane
            _instance = SelfHealingEngine(get_control_plane())
        return _instance
