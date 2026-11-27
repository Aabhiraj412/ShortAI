"""Stage 3.32B Autonomy Control Plane: permissions, kill switches, approval queue, execution and audit.

Every autonomous operation must pass through ``ControlPlane``:

* ``record_observation``  - allowed when the category's effective level is OBSERVE or higher.
* ``propose``             - allowed at RECOMMEND or higher; creates a PENDING approval request.
* ``approve`` / ``reject`` / ``cancel`` - human operators only; approval is bound to the exact payload hash
  and signed with a local key, so a status edited directly in the database is not a valid approval.
* ``execute``             - only at APPROVED_EXECUTION, only with no kill switch active, only for an APPROVED,
  unexpired, correctly signed request whose payload still hashes to the approved hash. The request is claimed
  atomically (one conditional UPDATE), runs with a time limit and bounded retries, and is verified.

Fail-closed rules: an unreadable database, configuration, key or kill-switch source means "no execution".
Production is never touched: no function here calls the scheduler, workers, publishing, OAuth or configuration.

Audit events are append-only: the application only INSERTs, database triggers refuse UPDATE and DELETE, and each
event is hash-chained to the previous one so any later edit is detectable (``verify_audit_chain``).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from backend.app.autonomy import registry
from backend.app.autonomy.registry import (
    APPROVAL_STANDING_ALLOWED, CATEGORIES, Level, ProhibitedActionError, UnknownActionError,
)

logger = logging.getLogger(__name__)

# Approval-request states.
PENDING, APPROVED, REJECTED, EXPIRED = "PENDING", "APPROVED", "REJECTED", "EXPIRED"
EXECUTING, COMPLETED, FAILED, CANCELLED = "EXECUTING", "COMPLETED", "FAILED", "CANCELLED"
STATES = (PENDING, APPROVED, REJECTED, EXPIRED, EXECUTING, COMPLETED, FAILED, CANCELLED)
OPEN_STATES = (PENDING, APPROVED, EXECUTING)
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    PENDING: (APPROVED, REJECTED, EXPIRED, CANCELLED),
    APPROVED: (EXECUTING, REJECTED, EXPIRED, CANCELLED, PENDING),   # PENDING: payload changed, approval invalidated
    EXECUTING: (COMPLETED, FAILED, APPROVED),                       # APPROVED: claim released before the handler ran
    REJECTED: (), EXPIRED: (), COMPLETED: (), FAILED: (), CANCELLED: (),
}

RESERVED_ACTOR_PREFIXES = ("autonomy", "standing-approval", "system", "engine", "control-plane", "recommendation",
                           "self-healing", "selfheal", "autopilot")
# Separators are ignored when matching reserved identities, so "Self.Healing", "self healing" or "re-commendation"
# cannot impersonate a machine identity.
_RESERVED_NORMALIZED = tuple(sorted({re.sub(r"[ ._@-]", "", p) for p in RESERVED_ACTOR_PREFIXES}))
OPERATOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}$")
MAX_EVIDENCE_BYTES = 8000
MAX_REASON = 500
STARTUP_ENV = "CRAZZYDOCS_AUTONOMY"

# -- Remediation WS2: three control states --------------------------------------------------------------------
# EMERGENCY_STOP  a dashboard kill switch, the sentinel file, CRAZZYDOCS_AUTONOMY=off, or an unreadable
#                 configuration: no autonomous execution at all. Independent of the startup configuration.
# RESTRICTED      no operator startup configuration (absent, invalid, or not "enabled"): only the fixed
#                 POLICY_AUTOMATIC[RESTRICTED] set runs (read-only diagnostics, reports, recording recommendations,
#                 local notifications, read-only provider re-checks). Everything else is blocked as before.
# NORMAL          the operator-created startup configuration enables execution: the NORMAL policy set runs
#                 automatically; every other action still needs its standing or explicit approval and level.
# Observation, reporting and the scheduler's own production are never governed by these states.
NORMAL, RESTRICTED, EMERGENCY_STOP = "NORMAL", "RESTRICTED", "EMERGENCY_STOP"
CONTROL_STATES = (NORMAL, RESTRICTED, EMERGENCY_STOP)
POLICY_APPROVER = "policy"
# Built-in, code-defined approvals: minimum effective category level per action and state. Only LOW/READ_ONLY
# actions with local, verifiable handlers, plus one staged recovery in NORMAL. Never production dispatch,
# publishing, configuration, credentials, restarts or deletion (those are not even permitted actions).
POLICY_AUTOMATIC: Dict[str, Dict[str, str]] = {
    RESTRICTED: {
        "report.operational.generate": "OBSERVE",
        "recommendation.record": "OBSERVE",
        "diagnostic.readonly.run": "OBSERVE",
        "operator.notify": "OBSERVE",
        "selfheal.diagnostic.run": "OBSERVE",
        "selfheal.provider.retry_readonly": "OBSERVE",
    },
    NORMAL: {
        "report.operational.generate": "OBSERVE",
        "recommendation.record": "OBSERVE",
        "diagnostic.readonly.run": "OBSERVE",
        "operator.notify": "OBSERVE",
        "selfheal.diagnostic.run": "OBSERVE",
        "selfheal.provider.retry_readonly": "OBSERVE",
        "selfheal.asset.reacquire_staged": "RECOMMEND",
    },
    EMERGENCY_STOP: {},
}
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomy_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS autonomy_approval_requests (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    risk TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_mode TEXT,
    approved_by TEXT,
    approved_at_utc TEXT,
    approved_payload_hash TEXT,
    approval_signature TEXT,
    decided_by TEXT,
    decided_at_utc TEXT,
    decision_reason TEXT,
    execution_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    execution_result_json TEXT,
    claim_token TEXT,
    claimed_at_utc TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_req_status_expiry ON autonomy_approval_requests (status, expires_at_utc);
CREATE INDEX IF NOT EXISTS idx_autonomy_req_type_created ON autonomy_approval_requests (action_type, created_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uq_autonomy_req_open_dedupe ON autonomy_approval_requests (dedupe_key)
    WHERE status IN ('PENDING', 'APPROVED', 'EXECUTING');
CREATE TABLE IF NOT EXISTS autonomy_audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT,
    action_type TEXT,
    previous_state TEXT,
    new_state TEXT,
    reason TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_audit_request ON autonomy_audit_events (request_id);
CREATE INDEX IF NOT EXISTS idx_autonomy_audit_ts ON autonomy_audit_events (ts_utc);
CREATE TRIGGER IF NOT EXISTS trg_autonomy_audit_no_update BEFORE UPDATE ON autonomy_audit_events
BEGIN SELECT RAISE(ABORT, 'autonomy audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_autonomy_audit_no_delete BEFORE DELETE ON autonomy_audit_events
BEGIN SELECT RAISE(ABORT, 'autonomy audit events are append-only'); END;
CREATE TABLE IF NOT EXISTS autonomy_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    claim_token TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    outcome TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    verification_json TEXT,
    executed_by TEXT NOT NULL,
    UNIQUE (request_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_autonomy_exec_type_started ON autonomy_executions (action_type, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_autonomy_exec_started ON autonomy_executions (started_at_utc);
CREATE TABLE IF NOT EXISTS autonomy_execution_leases (
    resource_key TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    owner_token TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    acquired_at_utc TEXT NOT NULL,
    heartbeat_at_utc TEXT NOT NULL
);
"""
# Stage 3.32G execution safety
LEASE_HEARTBEAT_SECONDS = 15        # the owner refreshes its lease while the handler thread is alive
LEASE_STALE_SECONDS = 120           # a lease is stale after this long without a heartbeat...
#                                     ...but it is only reclaimed when its owner process is no longer running.
BREAKER_THRESHOLD = 3               # consecutive failed attempts of one action type open its circuit breaker
BREAKER_COOLDOWN = timedelta(minutes=60)
FAILED_OUTCOMES = ("FAILED", "TIMED_OUT", "VERIFY_FAILED", "INTERRUPTED")


def _default_pid_alive(pid: int) -> bool:
    """True when a process with this id is running (Windows: OpenProcess + STILL_ACTIVE; elsewhere: signal 0). Fail safe: True."""
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259                              # STILL_ACTIVE
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True
SCHEMA_VERSION = 1


class ControlPlaneError(Exception):
    """Base error; ``code`` maps to an HTTP status in the API layer."""
    code = 400


class PermissionDenied(ControlPlaneError):
    code = 403


class NotFound(ControlPlaneError):
    code = 404


class Conflict(ControlPlaneError):
    code = 409


class Unavailable(ControlPlaneError):
    code = 503


class TransientActionError(Exception):
    """Raised by a handler for a failure that may succeed on retry."""


class PermanentActionError(Exception):
    """Raised by a handler for a failure that must not be retried."""


# -- helpers --------------------------------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(action_type: str, payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json({"action_type": action_type, "payload": payload}).encode("utf-8")).hexdigest()


_SECRET_KEY_RE = re.compile(r"(secret|token|password|passwd|api[_-]?key|credential|authorization|cookie)", re.I)
_SECRET_VALUE_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|ya29\.[0-9A-Za-z_\-.]+|sk-[A-Za-z0-9]{20,}|"
                              r"(?:key|token|secret|password)=[^\s&]+)", re.I)


def redact(value: Any, depth: int = 0) -> Any:
    """Drop secret-looking keys, mask secret-looking values and bound sizes before anything is stored or returned."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 100:
                out["_truncated"] = True
                break
            out[str(k)[:100]] = "[redacted]" if _SECRET_KEY_RE.search(str(k)) else redact(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:100]]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[redacted]", value[:4000])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value), depth + 1)


def safe_error(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}")[:500]


def validate_operator(operator: Any) -> str:
    """A human operator identity. Reserved (machine) identities may never approve or change permissions."""
    if not isinstance(operator, str) or not OPERATOR_RE.match(operator.strip()):
        raise PermissionDenied("operator must be 1-64 characters: letters, digits, space, . _ @ -")
    op = operator.strip()
    if op.lower().startswith(RESERVED_ACTOR_PREFIXES) or re.sub(r"[ ._@-]", "", op.lower()).startswith(_RESERVED_NORMALIZED):
        raise PermissionDenied("autonomy and system identities cannot approve actions or change permissions")
    return op


def _clean_reason(reason: Any, required: bool = True) -> str:
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        raise ControlPlaneError("reason must be a string")
    reason = reason.strip()
    if required and not reason:
        raise ControlPlaneError("a reason is required")
    if len(reason) > MAX_REASON:
        raise ControlPlaneError(f"reason is longer than {MAX_REASON} characters")
    return redact(reason)


@dataclass
class HandlerContext:
    request_id: str
    action_type: str
    autonomy_dir: Path
    now: datetime
    control_plane: "ControlPlane"
    intelligence: Callable[[], Any]


class ControlPlane:
    def __init__(self, db_path: Optional[str] = None, data_dir: Optional[str] = None, *,
                 autonomy_dir: Optional[str] = None, sentinel_path: Optional[str] = None,
                 startup_config_path: Optional[str] = None, environ: Optional[Dict[str, str]] = None,
                 handlers: Optional[Dict[str, Any]] = None, intelligence: Optional[Callable[[], Any]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 pid_alive: Optional[Callable[[int], bool]] = None,
                 retry_backoff: Optional[Callable[[int], float]] = None,
                 automatic_policy: Optional[Dict[str, Dict[str, str]]] = None):
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
        self.db_path = str(db_path)
        self.data_dir = Path(data_dir)
        self.autonomy_dir = Path(autonomy_dir) if autonomy_dir else self.data_dir / "autonomy"
        self.sentinel_path = Path(sentinel_path) if sentinel_path else self.data_dir / "AUTONOMY_DISABLED"
        self.startup_config_path = Path(startup_config_path) if startup_config_path else self.data_dir / "autonomy_startup.json"
        self.clock = clock
        if handlers is None:
            from backend.app.autonomy.handlers import HANDLERS
            handlers = HANDLERS
        for action_type in handlers:                      # a handler may only exist for a permitted action type
            registry.require_permitted(action_type)
        self.handlers = dict(handlers)
        if intelligence is None:
            def intelligence():
                from backend.app.services.operational_intelligence import get_service
                return get_service()
        self.intelligence = intelligence
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self.pid_alive = pid_alive or _default_pid_alive
        self.retry_backoff = retry_backoff or (lambda attempt: min(2.0 * attempt, 10.0))
        # Remediation WS2: the built-in automatic policy (production default; tests of the explicit approval
        # machinery pass an empty policy).
        self.automatic_policy = POLICY_AUTOMATIC if automatic_policy is None else automatic_policy
        # Kill switch C is latched once, at process start (fail closed: missing or invalid -> active).
        self.startup_kill_switch = self._evaluate_startup(os.environ if environ is None else environ)

    # -- time ---------------------------------------------------------------------------------------------
    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime(TS_FMT)

    # -- database -----------------------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):            # never create a database
            raise Unavailable("database not available; autonomous execution stays blocked")
        uri = Path(self.db_path).resolve().as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    conn.executescript(SCHEMA)
                    self._schema_ready = True
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM autonomy_config WHERE key='schema_version'").fetchone() is None:
                self._write_config(conn, "schema_version", SCHEMA_VERSION, "system")

    # -- audit --------------------------------------------------------------------------------------------
    def _audit(self, conn: sqlite3.Connection, event_type: str, actor: str, *, request_id: Optional[str] = None,
               action_type: Optional[str] = None, previous_state: Optional[str] = None,
               new_state: Optional[str] = None, reason: str = "", details: Optional[Dict[str, Any]] = None) -> None:
        last = conn.execute("SELECT event_hash FROM autonomy_audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = last[0] if last else "GENESIS"
        record = {"ts_utc": self._ts(self._now()), "event_type": event_type, "actor": str(actor)[:100],
                  "request_id": request_id, "action_type": (str(action_type)[:100] if action_type else None),
                  "previous_state": previous_state, "new_state": new_state, "reason": redact(str(reason or ""))[:MAX_REASON],
                  "details_json": canonical_json(redact(details or {}))}
        event_hash = hashlib.sha256((prev_hash + canonical_json(record)).encode("utf-8")).hexdigest()
        conn.execute("INSERT INTO autonomy_audit_events (ts_utc, event_type, actor, request_id, action_type, previous_state, "
                     "new_state, reason, details_json, prev_hash, event_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (record["ts_utc"], event_type, record["actor"], request_id, record["action_type"], previous_state,
                      new_state, record["reason"], record["details_json"], prev_hash, event_hash))

    def _audit_now(self, event_type: str, actor: str, **kw: Any) -> bool:
        """Audit outside another transaction. If the database is unavailable the event goes to a local fallback
        file, so audit history is never silently discarded."""
        try:
            with self._tx() as conn:
                self._audit(conn, event_type, actor, **kw)
            return True
        except Exception as exc:
            self._fallback_audit(event_type, actor, kw, exc)
            return False

    def _fallback_audit(self, event_type: str, actor: str, kw: Dict[str, Any], exc: BaseException) -> None:
        try:
            self.autonomy_dir.mkdir(parents=True, exist_ok=True)
            line = canonical_json({"ts_utc": self._ts(self._now()), "event_type": event_type, "actor": actor,
                                   "fields": redact(kw), "database_error": safe_error(exc)})
            with open(self.autonomy_dir / "audit_fallback.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            logger.exception("Autonomy audit fallback write failed for %s", event_type)

    def verify_audit_chain(self) -> Dict[str, Any]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM autonomy_audit_events ORDER BY seq").fetchall()
        prev = "GENESIS"
        for row in rows:
            record = {k: row[k] for k in ("ts_utc", "event_type", "actor", "request_id", "action_type",
                                          "previous_state", "new_state", "reason", "details_json")}
            expected = hashlib.sha256((prev + canonical_json(record)).encode("utf-8")).hexdigest()
            if row["prev_hash"] != prev or row["event_hash"] != expected:
                return {"ok": False, "events": len(rows), "first_bad_seq": row["seq"]}
            prev = row["event_hash"]
        return {"ok": True, "events": len(rows), "first_bad_seq": None}

    # -- configuration ------------------------------------------------------------------------------------
    def _default_config(self) -> Dict[str, Any]:
        return {"global_level": "OFF", "category_levels": {c: "OFF" for c in CATEGORIES},
                "standing_approvals": {}, "dashboard_kill_switch": {"active": False}}

    def _load_config(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        cfg = self._default_config()
        for row in conn.execute("SELECT key, value_json FROM autonomy_config"):
            if row["key"] in cfg:
                cfg[row["key"]] = json.loads(row["value_json"])
        Level.parse(cfg["global_level"])
        if not isinstance(cfg["category_levels"], dict) or not isinstance(cfg["standing_approvals"], dict):
            raise ValueError("invalid autonomy configuration")
        for c in CATEGORIES:
            Level.parse(cfg["category_levels"].get(c, "OFF"))
        return cfg

    def _write_config(self, conn: sqlite3.Connection, key: str, value: Any, actor: str) -> None:
        conn.execute("INSERT INTO autonomy_config (key, value_json, updated_at_utc, updated_by) VALUES (?,?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at_utc=excluded.updated_at_utc, "
                     "updated_by=excluded.updated_by, version=autonomy_config.version+1",
                     (key, canonical_json(value), self._ts(self._now()), actor))

    def _safe_config(self) -> Tuple[Dict[str, Any], Optional[str]]:
        """Configuration for gating. Any failure -> everything OFF and the dashboard switch treated as active."""
        try:
            with self._read() as conn:
                return self._load_config(conn), None
        except Exception as exc:
            cfg = self._default_config()
            cfg["dashboard_kill_switch"] = {"active": True, "fail_closed": True}
            return cfg, safe_error(exc)

    @staticmethod
    def _effective(cfg: Dict[str, Any], category: str) -> Level:
        try:
            return min(Level.parse(cfg["global_level"]), Level.parse(cfg["category_levels"].get(category, "OFF")))
        except Exception:
            return Level.OFF

    # -- kill switches ------------------------------------------------------------------------------------
    def _evaluate_startup(self, environ: Dict[str, str]) -> Dict[str, Any]:
        env = environ.get(STARTUP_ENV)
        if env is not None and env.strip().lower() in ("off", "disabled", "0", "false", "kill", "no"):
            return {"active": True, "emergency": True, "detail": f"{STARTUP_ENV}={env.strip()} at process start"}
        try:
            data = json.loads(self.startup_config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"active": True, "detail": "startup config file absent: autonomous execution disabled by default"}
        except Exception as exc:
            return {"active": True, "detail": f"startup config unreadable ({type(exc).__name__}): fail closed"}
        if not isinstance(data, dict) or data.get("autonomy_execution") != "enabled":
            return {"active": True, "detail": "startup config does not set autonomy_execution to \"enabled\""}
        return {"active": False, "detail": "startup config enables autonomous execution"}

    def kill_switches(self, cfg: Optional[Dict[str, Any]] = None, cfg_error: Optional[str] = None) -> Dict[str, Any]:
        if cfg is None:
            cfg, cfg_error = self._safe_config()
        dash = cfg.get("dashboard_kill_switch") or {}
        dashboard = {"active": bool(dash.get("active", True)),
                     "detail": (f"database or configuration unavailable: fail closed ({cfg_error})" if cfg_error
                                else (f"activated by {dash.get('by')} at {dash.get('at')}: {dash.get('reason', '')}"
                                      if dash.get("active") else "not activated from the dashboard")),
                     "changed_by": dash.get("by"), "changed_at_utc": dash.get("at")}
        try:
            present = os.path.lexists(self.sentinel_path)
            sentinel = {"active": present, "detail": (f"sentinel file present: {self.sentinel_path.name}" if present
                                                      else "sentinel file absent")}
        except Exception as exc:
            sentinel = {"active": True, "detail": f"sentinel check failed ({type(exc).__name__}): fail closed"}
        switches = {"dashboard": dashboard, "sentinel_file": sentinel, "startup_config": dict(self.startup_kill_switch)}
        active = [name for name, s in switches.items() if s["active"]]
        emergency = bool(dashboard["active"] or sentinel["active"] or switches["startup_config"].get("emergency"))
        state = EMERGENCY_STOP if emergency else (RESTRICTED if switches["startup_config"]["active"] else NORMAL)
        return {"any_active": bool(active), "active": active, "switches": switches,
                "emergency_stop": emergency, "control_state": state,
                "sentinel_path": str(self.sentinel_path), "startup_config_path": str(self.startup_config_path)}

    def control_state(self, cfg: Optional[Dict[str, Any]] = None, cfg_error: Optional[str] = None) -> Dict[str, Any]:
        """The current control state with the reason and what may run automatically (Remediation WS2)."""
        if cfg is None:
            cfg, cfg_error = self._safe_config()
        ks = self.kill_switches(cfg, cfg_error)
        state = ks["control_state"]
        stops = [n for n in ks["active"] if n != "startup_config" or ks["switches"]["startup_config"].get("emergency")]
        reason = {EMERGENCY_STOP: "emergency stop: " + ", ".join(stops),
                  RESTRICTED: "restricted autonomy: " + ks["switches"]["startup_config"]["detail"],
                  NORMAL: "normal autonomy: " + ks["switches"]["startup_config"]["detail"]}[state]
        automatic = {}
        for action, need in self.automatic_policy.get(state, {}).items():
            ok, why = self.policy_allows(action, cfg, state)
            automatic[action] = {"minimum_level": need, "active": ok, "detail": why}
        return {"state": state, "reason": reason, "automatic_actions": automatic,
                "emergency_stop": ks["emergency_stop"], "configuration_error": cfg_error, "kill_switches": ks}

    def policy_allows(self, action_type: str, cfg: Dict[str, Any], state: str) -> Tuple[bool, str]:
        """Whether the built-in policy approval covers this action now (state + minimum category level)."""
        need = self.automatic_policy.get(state, {}).get(action_type)
        if need is None:
            return False, f"{action_type} is not automatic in the {state} control state"
        spec = registry.REGISTRY.get(action_type)
        if spec is None or spec.prohibited:
            return False, "not a permitted action"
        level = self._effective(cfg, spec.category)
        if level < Level.parse(need):
            return False, f"{spec.category} autonomy level is {level.name}; automatic execution needs {need}"
        return True, f"automatic under the {state} policy ({spec.category} level {level.name})"

    def set_dashboard_kill_switch(self, active: bool, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        try:
            with self._tx() as conn:
                cfg = self._load_config(conn)
                prev = bool((cfg.get("dashboard_kill_switch") or {}).get("active"))
                value = {"active": bool(active), "by": op, "at": self._ts(self._now()), "reason": reason}
                self._write_config(conn, "dashboard_kill_switch", value, op)
                self._audit(conn, "KILL_SWITCH_ACTIVATED" if active else "KILL_SWITCH_DEACTIVATED", op,
                            previous_state="ACTIVE" if prev else "INACTIVE", new_state="ACTIVE" if active else "INACTIVE",
                            reason=reason, details={"switch": "dashboard"})
        except Exception as exc:
            if not active:
                raise Unavailable("could not deactivate the kill switch: the database is unavailable") from exc
            # Fail closed: activation must succeed even without the database.
            try:
                self.sentinel_path.parent.mkdir(parents=True, exist_ok=True)
                self.sentinel_path.write_text(f"activated by {op}: {reason}\n", encoding="utf-8")
            except Exception:
                logger.exception("Could not write the autonomy sentinel file")
            self._fallback_audit("KILL_SWITCH_ACTIVATED", op, {"switch": "dashboard->sentinel_file", "reason": reason}, exc)
            return {"active": True, "persisted_to": "sentinel_file", "kill_switches": self.kill_switches()}
        return {"active": bool(active), "persisted_to": "database", "kill_switches": self.kill_switches()}

    # -- permissions --------------------------------------------------------------------------------------
    def set_level(self, scope: str, level: Any, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        try:
            new = Level.parse(level)
        except ValueError as exc:
            raise ControlPlaneError(str(exc)) from exc
        if scope != "global" and scope not in CATEGORIES:
            raise ControlPlaneError(f"unknown scope; one of global, {', '.join(CATEGORIES)}")
        with self._tx() as conn:
            cfg = self._load_config(conn)
            if scope == "global":
                prev = cfg["global_level"]
                self._write_config(conn, "global_level", new.name, op)
            else:
                prev = cfg["category_levels"].get(scope, "OFF")
                levels = dict(cfg["category_levels"])
                levels[scope] = new.name
                self._write_config(conn, "category_levels", levels, op)
            self._audit(conn, "PERMISSION_CHANGED", op, previous_state=str(prev), new_state=new.name, reason=reason,
                        details={"scope": scope})
        return self.permissions()

    def grant_standing_approval(self, action_type: str, operator: str, reason: str, *, max_per_day: int,
                                valid_hours: int) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        spec = self._require_permitted_audited(action_type, op, "grant_standing_approval")
        if spec.approval != APPROVAL_STANDING_ALLOWED:
            raise PermissionDenied(f"{action_type} requires an explicit approval for every request; standing approval is not allowed")
        if isinstance(max_per_day, bool) or not isinstance(max_per_day, int) or not 1 <= max_per_day <= spec.daily_limit:
            raise ControlPlaneError(f"max_per_day must be between 1 and {spec.daily_limit}")
        if isinstance(valid_hours, bool) or not isinstance(valid_hours, int) or not 1 <= valid_hours <= 720:
            raise ControlPlaneError("valid_hours must be between 1 and 720")
        now = self._now()
        grant = {"grant_id": "sa_" + secrets.token_hex(6), "granted_by": op, "granted_at_utc": self._ts(now),
                 "expires_at_utc": self._ts(now + timedelta(hours=valid_hours)), "max_per_day": max_per_day, "reason": reason}
        with self._tx() as conn:
            cfg = self._load_config(conn)
            grants = dict(cfg["standing_approvals"])
            prev = grants.get(action_type)
            grants[action_type] = grant
            self._write_config(conn, "standing_approvals", grants, op)
            self._audit(conn, "PERMISSION_CHANGED", op, action_type=action_type,
                        previous_state="STANDING_APPROVAL" if prev else "NONE", new_state="STANDING_APPROVAL",
                        reason=reason, details={"change": "standing_approval_granted", "grant": grant})
        return self.permissions()

    def revoke_standing_approval(self, action_type: str, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        registry.lookup(action_type)
        with self._tx() as conn:
            cfg = self._load_config(conn)
            grants = dict(cfg["standing_approvals"])
            prev = grants.pop(action_type, None)
            if prev is None:
                raise NotFound("no standing approval for this action type")
            self._write_config(conn, "standing_approvals", grants, op)
            self._audit(conn, "PERMISSION_CHANGED", op, action_type=action_type, previous_state="STANDING_APPROVAL",
                        new_state="NONE", reason=reason, details={"change": "standing_approval_revoked",
                                                                  "grant_id": prev.get("grant_id")})
        return self.permissions()

    def permissions(self) -> Dict[str, Any]:
        cfg, err = self._safe_config()
        ks = self.kill_switches(cfg, err)
        now_s = self._ts(self._now())
        actions = []
        for spec in registry.REGISTRY.values():
            if spec.prohibited:
                actions.append({"action_type": spec.action_type, "category": "prohibited", "risk": spec.risk,
                                "effective_level": "PROHIBITED", "can_propose": False, "can_execute": False,
                                "required_approval": spec.approval, "standing_approval": None})
                continue
            lvl = self._effective(cfg, spec.category)
            grant = cfg["standing_approvals"].get(spec.action_type)
            actions.append({"action_type": spec.action_type, "category": spec.category, "risk": spec.risk,
                            "effective_level": lvl.name, "can_observe": lvl >= Level.OBSERVE,
                            "can_propose": lvl >= Level.RECOMMEND,
                            "can_execute": lvl >= Level.APPROVED_EXECUTION and not ks["any_active"],
                            "required_approval": spec.approval,
                            "standing_approval": (dict(grant, active=grant.get("expires_at_utc", "") > now_s)
                                                  if grant else None)})
        return {"global_level": cfg["global_level"], "category_levels": cfg["category_levels"],
                "effective_levels": {c: self._effective(cfg, c).name for c in CATEGORIES},
                "levels": list(Level.__members__), "categories": CATEGORIES,
                "standing_approvals": cfg["standing_approvals"], "actions": actions,
                "configuration_error": err, "kill_switches_active": ks["active"]}

    def _require_permitted_audited(self, action_type: Any, actor: str, stage: str) -> registry.ActionSpec:
        try:
            spec = registry.lookup(action_type)
        except UnknownActionError:
            self._audit_now("UNKNOWN_ACTION_REJECTED", actor, action_type=str(action_type)[:100],
                            reason="unknown action type", details={"stage": stage})
            raise
        if spec.prohibited:
            self._audit_now("PROHIBITED_ACTION_BLOCKED", actor, action_type=spec.action_type,
                            reason="permanently prohibited action", details={"stage": stage})
            raise ProhibitedActionError(f"action type {spec.action_type!r} is permanently prohibited for autonomy")
        return spec

    # -- observations -------------------------------------------------------------------------------------
    def record_observation(self, category: str, summary: str, evidence: Optional[Dict[str, Any]] = None,
                           actor: str = "autonomy-engine") -> Dict[str, Any]:
        if category not in CATEGORIES:
            raise ControlPlaneError("unknown category")
        cfg, err = self._safe_config()
        level = self._effective(cfg, category)
        if level < Level.OBSERVE:
            return {"recorded": False, "reason": f"{category} autonomy level is {level.name}"}
        self._audit_now("OBSERVATION_RECORDED", actor, reason=str(summary)[:MAX_REASON],
                        details={"category": category, "evidence": evidence or {}})
        return {"recorded": True}

    # -- approval queue -----------------------------------------------------------------------------------
    def propose(self, action_type: Any, payload: Any, *, reason: str, evidence: Optional[Dict[str, Any]] = None,
                proposed_by: str = "autonomy-engine", description: Optional[str] = None) -> Dict[str, Any]:
        spec = self._require_permitted_audited(action_type, proposed_by, "propose")
        clean = registry.validate_payload(spec, payload)
        reason = _clean_reason(reason)
        evidence = redact(evidence or {})
        if not isinstance(evidence, dict) or len(canonical_json(evidence).encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise ControlPlaneError(f"evidence must be an object of at most {MAX_EVIDENCE_BYTES} bytes")
        p_hash = payload_hash(spec.action_type, clean)
        dedupe = hashlib.sha256(f"{spec.action_type}|{p_hash}".encode()).hexdigest()
        now = self._now()
        refused: Optional[ControlPlaneError] = None
        result: Dict[str, Any] = {}
        with self._tx() as conn:
            self._maintenance(conn)
            cfg = self._load_config(conn)
            level = self._effective(cfg, spec.category)
            state = self.kill_switches(cfg, None)["control_state"]
            policy_ok, policy_why = self.policy_allows(spec.action_type, cfg, state)
            if level < Level.RECOMMEND and not policy_ok:
                self._audit(conn, "PROPOSAL_REFUSED", proposed_by, action_type=spec.action_type,
                            reason=f"{spec.category} autonomy level is {level.name}", details={"payload_hash": p_hash})
                refused = PermissionDenied(f"{spec.category} autonomy level is {level.name}; proposals need RECOMMEND or higher")
            else:
                existing = conn.execute("SELECT id, status FROM autonomy_approval_requests WHERE dedupe_key=? AND status IN "
                                        "('PENDING','APPROVED','EXECUTING')", (dedupe,)).fetchone()
                if existing:
                    result = {"request": self._get(conn, existing["id"]), "duplicate": True}
                else:
                    rid = "apr_" + secrets.token_hex(8)
                    expires = now + timedelta(hours=spec.request_ttl_hours)
                    conn.execute(
                        "INSERT INTO autonomy_approval_requests (id, action_type, category, description, reason, evidence_json, "
                        "payload_json, payload_hash, dedupe_key, risk, proposed_by, created_at_utc, expires_at_utc, status, "
                        "updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, spec.action_type, spec.category, (description or spec.description)[:300], reason,
                         canonical_json(evidence), canonical_json(clean), p_hash, dedupe, spec.risk, str(proposed_by)[:100],
                         self._ts(now), self._ts(expires), PENDING, self._ts(now)))
                    self._audit(conn, "ACTION_PROPOSED", proposed_by, request_id=rid, action_type=spec.action_type,
                                new_state=PENDING, reason=reason, details={"payload_hash": p_hash, "risk": spec.risk})
                    grant = cfg["standing_approvals"].get(spec.action_type)
                    if (grant and spec.approval == APPROVAL_STANDING_ALLOWED and level >= Level.APPROVED_EXECUTION
                            and grant.get("expires_at_utc", "") > self._ts(now)):
                        self._sign_and_approve(conn, rid, "STANDING", f"standing-approval:{grant['grant_id']}",
                                               f"standing approval granted by {grant.get('granted_by')}")
                    elif policy_ok:
                        self._sign_and_approve(conn, rid, "POLICY", f"{POLICY_APPROVER}:{state}", policy_why)
                    result = {"request": self._get(conn, rid), "duplicate": False}
        if refused:
            raise refused
        return result

    def _approval_key(self, create: bool) -> Optional[bytes]:
        path = self.autonomy_dir / "approval.key"
        try:
            data = path.read_bytes()
            return data if len(data) >= 32 else None
        except FileNotFoundError:
            if not create:
                return None
            self.autonomy_dir.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
            return path.read_bytes()
        except Exception:
            return None

    @staticmethod
    def _signature_material(row: Dict[str, Any]) -> bytes:
        return "|".join(str(row.get(k) or "") for k in ("id", "action_type", "approved_payload_hash", "approved_by",
                                                        "approved_at_utc", "expires_at_utc", "approval_mode")).encode()

    def _sign_and_approve(self, conn: sqlite3.Connection, rid: str, mode: str, approver: str, reason: str) -> None:
        key = self._approval_key(create=True)
        if not key:
            raise Unavailable("approval signing key unavailable")
        row = dict(conn.execute("SELECT * FROM autonomy_approval_requests WHERE id=?", (rid,)).fetchone())
        now = self._ts(self._now())
        row.update(approved_payload_hash=row["payload_hash"], approved_by=approver, approved_at_utc=now, approval_mode=mode)
        sig = hmac.new(key, self._signature_material(row), hashlib.sha256).hexdigest()
        cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, approval_mode=?, approved_by=?, approved_at_utc=?, "
                           "approved_payload_hash=?, approval_signature=?, decided_by=?, decided_at_utc=?, decision_reason=?, "
                           "updated_at_utc=? WHERE id=? AND status=?",
                           (APPROVED, mode, approver, now, row["payload_hash"], sig, approver, now, reason, now, rid, PENDING))
        if cur.rowcount != 1:
            raise Conflict("request is no longer pending")
        self._audit(conn, "APPROVED", approver, request_id=rid, action_type=row["action_type"], previous_state=PENDING,
                    new_state=APPROVED, reason=reason, details={"approval_mode": mode, "payload_hash": row["payload_hash"]})

    def _signature_valid(self, row: Dict[str, Any]) -> bool:
        key = self._approval_key(create=False)
        if not key or not row.get("approval_signature"):
            return False
        expected = hmac.new(key, self._signature_material(row), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, row["approval_signature"])

    def approve(self, request_id: str, operator: str, approved_payload_hash: str, reason: str = "") -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason or "approved by operator", required=False)
        if not isinstance(approved_payload_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_payload_hash):
            raise ControlPlaneError("payload_hash must be the 64-character hash shown with the request")
        err: Optional[ControlPlaneError] = None
        out: Dict[str, Any] = {}
        with self._tx() as conn:
            self._maintenance(conn)
            row = self._row(conn, request_id)
            spec = registry.REGISTRY.get(row["action_type"])
            current = payload_hash(row["action_type"], json.loads(row["payload_json"]))
            if spec is None or spec.prohibited:
                self._audit(conn, "PROHIBITED_ACTION_BLOCKED", op, request_id=request_id, action_type=row["action_type"],
                            reason="approval refused: not a permitted action type", details={"stage": "approve"})
                err = PermissionDenied("this action type can never be approved")
            elif row["status"] == APPROVED and row["approved_by"] == op and row["approved_payload_hash"] == approved_payload_hash:
                out = {"request": self._public(row), "already_approved": True}          # idempotent repeat
            elif row["status"] != PENDING:
                err = Conflict(f"request is {row['status']}; only PENDING requests can be approved")
            elif approved_payload_hash != row["payload_hash"] or current != row["payload_hash"]:
                self._audit(conn, "APPROVAL_REFUSED", op, request_id=request_id, action_type=row["action_type"],
                            reason="payload hash does not match the request", details={"presented": approved_payload_hash})
                err = Conflict("the payload changed or the hash does not match; review the current payload and approve again")
            else:
                self._sign_and_approve(conn, request_id, "EXPLICIT", op, reason)
                out = {"request": self._get(conn, request_id), "already_approved": False}
        if err:
            raise err
        return out

    def _decide(self, request_id: str, operator: str, reason: str, new_state: str, allowed_from: Tuple[str, ...],
                event: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        err: Optional[ControlPlaneError] = None
        out: Dict[str, Any] = {}
        with self._tx() as conn:
            self._maintenance(conn)
            row = self._row(conn, request_id)
            if row["status"] == new_state:
                out = {"request": self._public(row), "unchanged": True}
            elif row["status"] not in allowed_from:
                err = Conflict(f"request is {row['status']}; cannot change it to {new_state}")
            else:
                now = self._ts(self._now())
                cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, decided_by=?, decided_at_utc=?, "
                                   "decision_reason=?, approval_signature=NULL, updated_at_utc=? WHERE id=? AND status=?",
                                   (new_state, op, now, reason, now, request_id, row["status"]))
                if cur.rowcount != 1:
                    err = Conflict("request changed concurrently; reload and try again")
                else:
                    self._audit(conn, event, op, request_id=request_id, action_type=row["action_type"],
                                previous_state=row["status"], new_state=new_state, reason=reason)
                    out = {"request": self._get(conn, request_id), "unchanged": False}
        if err:
            raise err
        return out

    def reject(self, request_id: str, operator: str, reason: str) -> Dict[str, Any]:
        return self._decide(request_id, operator, reason, REJECTED, (PENDING, APPROVED), "REJECTED")

    def cancel(self, request_id: str, operator: str, reason: str) -> Dict[str, Any]:
        return self._decide(request_id, operator, reason, CANCELLED, (PENDING, APPROVED), "CANCELLED")

    def amend_payload(self, request_id: str, new_payload: Any, actor: str, reason: str) -> Dict[str, Any]:
        """Change a PENDING / APPROVED request's payload. Any prior approval is invalidated (back to PENDING)."""
        reason = _clean_reason(reason)
        err: Optional[ControlPlaneError] = None
        with self._tx() as conn:
            self._maintenance(conn)
            row = self._row(conn, request_id)
            spec = registry.require_permitted(row["action_type"])
            clean = registry.validate_payload(spec, new_payload)
            if row["status"] not in (PENDING, APPROVED):
                err = Conflict(f"request is {row['status']}; only PENDING or APPROVED requests can be amended")
            else:
                p_hash = payload_hash(spec.action_type, clean)
                dedupe = hashlib.sha256(f"{spec.action_type}|{p_hash}".encode()).hexdigest()
                now = self._ts(self._now())
                try:
                    conn.execute("UPDATE autonomy_approval_requests SET payload_json=?, payload_hash=?, dedupe_key=?, status=?, "
                                 "approval_mode=NULL, approved_by=NULL, approved_at_utc=NULL, approved_payload_hash=NULL, "
                                 "approval_signature=NULL, updated_at_utc=? WHERE id=? AND status=?",
                                 (canonical_json(clean), p_hash, dedupe, PENDING, now, request_id, row["status"]))
                except sqlite3.IntegrityError:
                    err = Conflict("an identical open request already exists")
                else:
                    if row["status"] == APPROVED:
                        self._audit(conn, "APPROVAL_INVALIDATED", actor, request_id=request_id, action_type=row["action_type"],
                                    previous_state=APPROVED, new_state=PENDING, reason="payload changed after approval",
                                    details={"old_hash": row["payload_hash"], "new_hash": p_hash})
                    self._audit(conn, "PAYLOAD_AMENDED", actor, request_id=request_id, action_type=row["action_type"],
                                previous_state=row["status"], new_state=PENDING, reason=reason,
                                details={"old_hash": row["payload_hash"], "new_hash": p_hash})
            out = {"request": self._get(conn, request_id)}
        if err:
            raise err
        return out

    def _maintenance(self, conn: sqlite3.Connection) -> None:
        """Expire overdue requests and reconcile executions interrupted by a process stop (never re-executed)."""
        self._reclaim_leases(conn)
        now = self._now()
        now_s = self._ts(now)
        for row in conn.execute("SELECT id, action_type, status FROM autonomy_approval_requests WHERE status IN ('PENDING','APPROVED') "
                                "AND expires_at_utc <= ?", (now_s,)).fetchall():
            cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, approval_signature=NULL, updated_at_utc=? "
                               "WHERE id=? AND status=?", (EXPIRED, now_s, row["id"], row["status"]))
            if cur.rowcount == 1:
                self._audit(conn, "EXPIRED", "control-plane", request_id=row["id"], action_type=row["action_type"],
                            previous_state=row["status"], new_state=EXPIRED, reason="request expired before execution")
        for row in conn.execute("SELECT id, action_type, claimed_at_utc, claim_token FROM autonomy_approval_requests "
                                "WHERE status='EXECUTING'").fetchall():
            spec = registry.REGISTRY.get(row["action_type"])
            budget = (spec.timeout_seconds * (spec.retry_limit + 1) if spec else 0) + 120
            if not row["claimed_at_utc"] or row["claimed_at_utc"] <= self._ts(now - timedelta(seconds=budget)):
                result = {"error": "interrupted: the process stopped during execution; the action was not re-executed"}
                conn.execute("UPDATE autonomy_executions SET outcome='INTERRUPTED', finished_at_utc=? WHERE request_id=? "
                             "AND outcome='RUNNING'", (now_s, row["id"]))
                cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, execution_status='INTERRUPTED', "
                                   "execution_result_json=?, approval_signature=NULL, updated_at_utc=? WHERE id=? AND "
                                   "status='EXECUTING' AND claim_token IS ?",
                                   (FAILED, canonical_json(result), now_s, row["id"], row["claim_token"]))
                if cur.rowcount == 1:
                    self._audit(conn, "EXECUTION_FAILED", "control-plane", request_id=row["id"], action_type=row["action_type"],
                                previous_state=EXECUTING, new_state=FAILED, reason=result["error"])

    # -- Stage 3.32G: leases, heartbeat, ownership, circuit breaker -----------------------------------------------
    def _heartbeat_loop(self, key: str, token: str, stop: threading.Event) -> None:
        while not stop.wait(LEASE_HEARTBEAT_SECONDS):
            try:
                with self._tx() as conn:
                    conn.execute("UPDATE autonomy_execution_leases SET heartbeat_at_utc=? WHERE resource_key=? AND "
                                 "owner_token=?", (self._ts(self._now()), key, token))
            except Exception:
                pass

    def _release_lease_when_done(self, key: str, token: str, futures: List[concurrent.futures.Future],
                                 stop: threading.Event) -> None:
        """Release only when every handler thread has REALLY ended. After a timeout the thread may still be running:
        the lease (and its heartbeat) then stays until that thread finishes, so nothing can run the action again."""
        def release(_f=None):
            if any(not f.done() for f in futures):
                return
            stop.set()
            try:
                with self._tx() as conn:
                    n = conn.execute("DELETE FROM autonomy_execution_leases WHERE resource_key=? AND owner_token=?",
                                     (key, token)).rowcount
                    if n:
                        self._audit(conn, "LEASE_RELEASED", "control-plane",
                                    details={"resource": key[:80], "claim": token[:8]})
            except Exception:
                pass
        pending = [f for f in futures if not f.done()]
        if not pending:
            release()
        for f in pending:
            f.add_done_callback(release)

    def _breaker(self, conn: sqlite3.Connection, action_type: str) -> Dict[str, Any]:
        rows = conn.execute("SELECT outcome, finished_at_utc FROM autonomy_executions WHERE action_type=? AND "
                            "outcome NOT IN ('RUNNING','BLOCKED') ORDER BY id DESC LIMIT ?",
                            (action_type, BREAKER_THRESHOLD)).fetchall()
        if len(rows) < BREAKER_THRESHOLD or any(r["outcome"] not in FAILED_OUTCOMES for r in rows):
            return {"open": False}
        last = rows[0]["finished_at_utc"] or ""
        try:
            until = datetime.strptime(last, TS_FMT).replace(tzinfo=timezone.utc) + BREAKER_COOLDOWN
        except ValueError:
            until = self._now() + BREAKER_COOLDOWN
        return {"open": until > self._now(), "until_utc": self._ts(until), "last_outcomes": [r["outcome"] for r in rows]}

    def _reclaim_leases(self, conn: sqlite3.Connection) -> None:
        """A lease whose heartbeat is stale is reclaimed ONLY when its owner process is no longer running. A stale
        heartbeat alone never lets a second worker run the action (the owner may be slow or blocked)."""
        stale = self._ts(self._now() - timedelta(seconds=LEASE_STALE_SECONDS))
        for lease in conn.execute("SELECT * FROM autonomy_execution_leases WHERE heartbeat_at_utc <= ?", (stale,)).fetchall():
            if lease["owner_pid"] == os.getpid() or self.pid_alive(int(lease["owner_pid"])):
                continue
            n = conn.execute("DELETE FROM autonomy_execution_leases WHERE resource_key=? AND owner_token=?",
                             (lease["resource_key"], lease["owner_token"])).rowcount
            if n:
                self._audit(conn, "LEASE_RECLAIMED", "control-plane", request_id=lease["request_id"],
                            action_type=lease["action_type"],
                            reason=f"owner process {lease['owner_pid']} is no longer running; the action was not re-executed")

    def safety_state(self) -> Dict[str, Any]:
        with self._read() as conn:
            leases = [dict(r) for r in conn.execute("SELECT request_id, action_type, owner_pid, acquired_at_utc, "
                                                    "heartbeat_at_utc FROM autonomy_execution_leases LIMIT 50")]
            breakers = {}
            for (at,) in conn.execute("SELECT DISTINCT action_type FROM autonomy_executions LIMIT 100").fetchall():
                b = self._breaker(conn, at)
                if b["open"]:
                    breakers[at] = b
        stale = self._ts(self._now() - timedelta(seconds=LEASE_STALE_SECONDS))
        for lease in leases:
            lease["heartbeat_stale"] = lease["heartbeat_at_utc"] <= stale
        return {"leases": leases, "open_circuit_breakers": breakers, "lease_stale_seconds": LEASE_STALE_SECONDS,
                "breaker_threshold": BREAKER_THRESHOLD,
                "breaker_cooldown_minutes": int(BREAKER_COOLDOWN.total_seconds() // 60)}

    def run_maintenance(self) -> None:
        with self._tx() as conn:
            self._maintenance(conn)

    # -- execution ----------------------------------------------------------------------------------------
    def _gate(self, conn: sqlite3.Connection, row: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """Every precondition, re-checked immediately before execution. Returns (refusal reason, context)."""
        spec = registry.REGISTRY.get(row["action_type"])
        if spec is None:
            return "unknown action type", {}
        if spec.prohibited:
            return "permanently prohibited action", {}
        if row["action_type"] not in self.handlers:
            return "no handler is registered for this action type", {}
        cfg = self._load_config(conn)
        ks = self.kill_switches(cfg, None)
        state = ks["control_state"]
        if state == EMERGENCY_STOP:
            return f"emergency stop: kill switch active: {', '.join(ks['active'])}", {}
        if row.get("approval_mode") == "POLICY":
            ok, why = self.policy_allows(row["action_type"], cfg, state)
            if not ok:
                return f"policy approval no longer covers this action: {why}", {}
        else:
            if ks["any_active"]:
                return (f"kill switch active: {', '.join(ks['active'])} (restricted autonomy: without the operator "
                        "startup configuration only the built-in automatic actions run)"), {}
            level = self._effective(cfg, spec.category)
            if level < Level.APPROVED_EXECUTION:
                return f"{spec.category} autonomy level is {level.name}; execution needs APPROVED_EXECUTION", {}
        if row["status"] != APPROVED:
            return f"request is {row['status']}, not APPROVED", {}
        if row["expires_at_utc"] <= self._ts(self._now()):
            return "approval expired", {}
        current = payload_hash(row["action_type"], json.loads(row["payload_json"]))
        if current != row["payload_hash"] or row["approved_payload_hash"] != row["payload_hash"]:
            return "PAYLOAD_MISMATCH", {}
        if not self._signature_valid(row):
            return "approval signature invalid or missing (approval was not made through the control plane)", {}
        breaker = self._breaker(conn, spec.action_type)
        if breaker["open"]:
            return (f"circuit breaker open: the last {BREAKER_THRESHOLD} attempts of {spec.action_type} failed; "
                    f"execution resumes after {breaker['until_utc']}"), {}
        limit = spec.daily_limit
        if row["approval_mode"] == "STANDING":
            grant = cfg["standing_approvals"].get(row["action_type"])
            if not grant or grant.get("expires_at_utc", "") <= self._ts(self._now()) \
                    or row["approved_by"] != f"standing-approval:{grant.get('grant_id')}":
                return "standing approval revoked or expired", {}
            limit = min(limit, int(grant.get("max_per_day", 0)))
        day_start = self._ts(self._now().astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
        used = conn.execute("SELECT COUNT(*) FROM autonomy_executions WHERE action_type=? AND attempt=1 AND started_at_utc>=?",
                            (row["action_type"], day_start)).fetchone()[0]
        if used >= limit:
            return f"daily limit reached ({used}/{limit})", {}
        return None, {"spec": spec}

    def execute(self, request_id: str, actor: str) -> Dict[str, Any]:
        """Execute one APPROVED request. Not a generic execution entry point: the action type, payload and approval
        come from the stored, signed request, never from the caller."""
        actor = str(actor)[:100]
        token = secrets.token_hex(12)
        refusal: Optional[str] = None
        with self._tx() as conn:
            self._maintenance(conn)
            row = self._row(conn, request_id)
            refusal, ctx = self._gate(conn, row)
            if refusal == "PAYLOAD_MISMATCH":
                now = self._ts(self._now())
                conn.execute("UPDATE autonomy_approval_requests SET status=?, approval_mode=NULL, approved_by=NULL, "
                             "approved_at_utc=NULL, approved_payload_hash=NULL, approval_signature=NULL, updated_at_utc=? "
                             "WHERE id=? AND status=?", (PENDING, now, request_id, APPROVED))
                self._audit(conn, "APPROVAL_INVALIDATED", "control-plane", request_id=request_id, action_type=row["action_type"],
                            previous_state=row["status"], new_state=PENDING,
                            reason="stored payload no longer matches the approved payload hash")
                refusal = "payload changed after approval; the approval was invalidated"
            elif refusal:
                self._audit(conn, "EXECUTION_BLOCKED", actor, request_id=request_id, action_type=row["action_type"],
                            previous_state=row["status"], new_state=row["status"], reason=refusal)
            else:
                now = self._ts(self._now())
                lease = conn.execute("SELECT * FROM autonomy_execution_leases WHERE resource_key=?",
                                     (row["dedupe_key"],)).fetchone()
                cur = None
                if lease is not None:
                    refusal = (f"a previous execution of this action ({lease['request_id']}) may still be running "
                               f"(lease owned by process {lease['owner_pid']}); it is never executed twice")
                    self._audit(conn, "EXECUTION_BLOCKED", actor, request_id=request_id, action_type=row["action_type"],
                                previous_state=row["status"], new_state=row["status"], reason=refusal)
                else:
                    cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, execution_status='RUNNING', claim_token=?, "
                                       "claimed_at_utc=?, updated_at_utc=? WHERE id=? AND status=? AND expires_at_utc>?",
                                       (EXECUTING, token, now, now, request_id, APPROVED, now))
                if cur is None:
                    pass
                elif cur.rowcount != 1:
                    refusal = "request was claimed by another execution"
                else:
                    conn.execute("INSERT INTO autonomy_execution_leases (resource_key, request_id, action_type, owner_token, "
                                 "owner_pid, acquired_at_utc, heartbeat_at_utc) VALUES (?,?,?,?,?,?,?)",
                                 (row["dedupe_key"], request_id, row["action_type"], token, os.getpid(), now, now))
                    self._audit(conn, "EXECUTION_STARTED", actor, request_id=request_id, action_type=row["action_type"],
                                previous_state=APPROVED, new_state=EXECUTING, details={"claim": token[:8]})
        if refusal:
            return {"executed": False, "status": "BLOCKED", "reason": refusal, "request": self.get_request(request_id)}
        return self._run_claimed(dict(row), ctx["spec"], token, actor)

    def _run_claimed(self, row: Dict[str, Any], spec: registry.ActionSpec, token: str, actor: str) -> Dict[str, Any]:
        handler = self.handlers[spec.action_type]
        payload = json.loads(row["payload_json"])
        futures: List[concurrent.futures.Future] = []
        stop = threading.Event()
        threading.Thread(target=self._heartbeat_loop, args=(row["dedupe_key"], token, stop),
                         name="autonomy-lease-heartbeat", daemon=True).start()
        try:
            return self._attempts(row, spec, token, actor, handler, payload, futures)
        finally:
            self._release_lease_when_done(row["dedupe_key"], token, futures, stop)

    def _attempts(self, row, spec, token, actor, handler, payload, futures) -> Dict[str, Any]:
        rid = row["id"]
        outcome, final_result, error, verification = "FAILED", None, None, None
        for attempt in range(1, spec.retry_limit + 2):
            if attempt > 1:
                time.sleep(max(0.0, float(self.retry_backoff(attempt - 1))))   # bounded backoff between retries
            ks = self.kill_switches()
            # Remediation WS2: an emergency stop always stops; a policy-approved request continues in RESTRICTED as
            # long as the policy still covers it, every other request stops on any active switch (unchanged).
            stop = ks["emergency_stop"] or (ks["any_active"] and not (
                row.get("approval_mode") == "POLICY"
                and self.policy_allows(row["action_type"], self._safe_config()[0], ks["control_state"])[0]))
            if stop:
                error = f"kill switch activated during execution ({', '.join(ks['active'])}); remaining attempts cancelled"
                if attempt == 1:
                    return self._release_claim(row, token, actor, error)
                break
            started = self._ts(self._now())
            with self._tx() as conn:
                conn.execute("INSERT INTO autonomy_executions (request_id, action_type, attempt, claim_token, started_at_utc, "
                             "outcome, executed_by) VALUES (?,?,?,?,?,?,?)",
                             (rid, spec.action_type, attempt, token, started, "RUNNING", actor))
                conn.execute("UPDATE autonomy_approval_requests SET attempts=? WHERE id=? AND claim_token=?", (attempt, rid, token))
            ctx = HandlerContext(rid, spec.action_type, self.autonomy_dir, self._now(), self, self.intelligence)
            attempt_outcome, retry = "FAILED", False
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="autonomy-action")
            try:
                fut = pool.submit(handler.run, ctx, payload)
                futures.append(fut)
                result = fut.result(timeout=spec.timeout_seconds)
                ok, verification = handler.verify(ctx, payload, result)
                if ok:
                    attempt_outcome, outcome, final_result, error = "SUCCEEDED", "COMPLETED", result, None
                else:
                    attempt_outcome, error = "VERIFY_FAILED", "verification failed"
                    try:
                        verification = dict(verification or {}, compensation=handler.compensate(ctx, payload, result))
                    except Exception as exc:
                        verification = dict(verification or {}, compensation_error=safe_error(exc))
                    final_result = result
            except concurrent.futures.TimeoutError:
                attempt_outcome, error = "TIMED_OUT", f"timed out after {spec.timeout_seconds:g} s"
            except PermanentActionError as exc:
                error = safe_error(exc)
            except Exception as exc:
                error, retry = safe_error(exc), True
            finally:
                pool.shutdown(wait=False)
            with self._tx() as conn:
                conn.execute("UPDATE autonomy_executions SET outcome=?, finished_at_utc=?, result_json=?, error=?, "
                             "verification_json=? WHERE request_id=? AND attempt=? AND claim_token=?",
                             (attempt_outcome, self._ts(self._now()), canonical_json(redact(final_result)) if final_result is not None else None,
                              error, canonical_json(redact(verification)) if verification is not None else None, rid, attempt, token))
            if attempt_outcome == "SUCCEEDED" or not retry:
                break
        status = COMPLETED if outcome == "COMPLETED" else FAILED
        body = {"result": redact(final_result), "verification": redact(verification), "error": error}
        with self._tx() as conn:
            now = self._ts(self._now())
            cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, execution_status=?, execution_result_json=?, "
                               "updated_at_utc=? WHERE id=? AND status=? AND claim_token=?",
                               (status, "SUCCEEDED" if status == COMPLETED else "FAILED", canonical_json(body), now, rid,
                                EXECUTING, token))
            if cur.rowcount == 1:
                self._audit(conn, "EXECUTION_COMPLETED" if status == COMPLETED else "EXECUTION_FAILED", actor, request_id=rid,
                            action_type=spec.action_type, previous_state=EXECUTING, new_state=status,
                            reason=error or "executed and verified", details={"verification": verification})
        return {"executed": status == COMPLETED, "status": status, "error": error, "request": self.get_request(rid)}

    def _release_claim(self, row: Dict[str, Any], token: str, actor: str, reason: str) -> Dict[str, Any]:
        with self._tx() as conn:
            now = self._ts(self._now())
            conn.execute("DELETE FROM autonomy_execution_leases WHERE resource_key=? AND owner_token=?",
                         (row["dedupe_key"], token))
            conn.execute("UPDATE autonomy_executions SET outcome='BLOCKED', finished_at_utc=?, error=? WHERE request_id=? "
                         "AND claim_token=? AND outcome='RUNNING'", (now, reason, row["id"], token))
            cur = conn.execute("UPDATE autonomy_approval_requests SET status=?, execution_status='NOT_STARTED', claim_token=NULL, "
                               "claimed_at_utc=NULL, updated_at_utc=? WHERE id=? AND status=? AND claim_token=?",
                               (APPROVED, now, row["id"], EXECUTING, token))
            if cur.rowcount == 1:
                self._audit(conn, "EXECUTION_BLOCKED", actor, request_id=row["id"], action_type=row["action_type"],
                            previous_state=EXECUTING, new_state=APPROVED, reason=reason)
        return {"executed": False, "status": "BLOCKED", "reason": reason, "request": self.get_request(row["id"])}

    def submit(self, action_type: str, payload: Dict[str, Any], *, reason: str, evidence: Optional[Dict[str, Any]] = None,
               proposed_by: str = "autonomy-engine") -> Dict[str, Any]:
        """Propose; if a standing approval approved it, execute it immediately (all gates still apply)."""
        proposal = self.propose(action_type, payload, reason=reason, evidence=evidence, proposed_by=proposed_by)
        req = proposal["request"]
        if req["status"] == APPROVED and req.get("approval_mode") in ("STANDING", "POLICY"):
            return {"proposal": proposal, "execution": self.execute(req["id"], actor=proposed_by)}
        return {"proposal": proposal, "execution": None}

    # -- reads --------------------------------------------------------------------------------------------
    def _row(self, conn: sqlite3.Connection, request_id: Any) -> Dict[str, Any]:
        if not isinstance(request_id, str) or not re.fullmatch(r"apr_[0-9a-f]{16}", request_id):
            raise NotFound("unknown request id")
        row = conn.execute("SELECT * FROM autonomy_approval_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise NotFound("unknown request id")
        return dict(row)

    @staticmethod
    def _public(row: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in row.items() if k not in ("approval_signature", "claim_token", "dedupe_key")}
        for k in ("payload_json", "evidence_json", "execution_result_json"):
            if out.get(k):
                try:
                    out[k[:-5]] = json.loads(out.pop(k))
                except Exception:
                    out[k[:-5]] = None
                    out.pop(k, None)
            else:
                out.pop(k, None)
                out[k[:-5]] = None
        out["signed"] = bool(row.get("approval_signature"))
        return out

    def _get(self, conn: sqlite3.Connection, request_id: str) -> Dict[str, Any]:
        return self._public(self._row(conn, request_id))

    def get_request(self, request_id: str) -> Dict[str, Any]:
        with self._read() as conn:
            return self._get(conn, request_id)

    def list_requests(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if status is not None and status not in STATES:
            raise ControlPlaneError(f"status must be one of {', '.join(STATES)}")
        limit = max(1, min(int(limit), 200))
        with self._tx() as conn:
            self._maintenance(conn)
        with self._read() as conn:
            if status:
                rows = conn.execute("SELECT * FROM autonomy_approval_requests WHERE status=? ORDER BY created_at_utc DESC LIMIT ?",
                                    (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM autonomy_approval_requests ORDER BY created_at_utc DESC LIMIT ?",
                                    (limit,)).fetchall()
            return [self._public(dict(r)) for r in rows]

    def list_executions(self, limit: int = 50, failures_only: bool = False) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        where = "WHERE outcome NOT IN ('SUCCEEDED','RUNNING')" if failures_only else ""
        with self._read() as conn:
            rows = conn.execute(f"SELECT id, request_id, action_type, attempt, started_at_utc, finished_at_utc, outcome, "
                                f"result_json, error, verification_json, executed_by FROM autonomy_executions {where} "
                                f"ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("result_json", "verification_json"):
                d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else None
            out.append(d)
        return out

    def audit_events(self, limit: int = 100, before_seq: Optional[int] = None,
                     request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql, args = "SELECT * FROM autonomy_audit_events WHERE 1=1", []
        if before_seq is not None:
            sql += " AND seq < ?"
            args.append(int(before_seq))
        if request_id is not None:
            sql += " AND request_id = ?"
            args.append(str(request_id)[:40])
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._read() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d.pop("details_json") or "{}")
            out.append(d)
        return out

    def status(self) -> Dict[str, Any]:
        cfg, err = self._safe_config()
        ks = self.kill_switches(cfg, err)
        db_ok, counts, recent, failures, notifications, total = err is None, {}, [], [], [], None
        try:
            with self._tx() as conn:
                self._maintenance(conn)
            with self._read() as conn:
                counts = {r[0]: r[1] for r in conn.execute(
                    "SELECT status, COUNT(*) FROM autonomy_approval_requests GROUP BY status")}
                total = conn.execute("SELECT COUNT(*) FROM autonomy_audit_events").fetchone()[0]
            recent = self.list_executions(limit=10)
            failures = self.list_executions(limit=10, failures_only=True)
            notifications = [e for e in self.list_executions(limit=50) if e["action_type"] == "operator.notify"
                             and e["outcome"] == "SUCCEEDED"][:10]
        except Exception as exc:
            db_ok, err = False, err or safe_error(exc)
        global_level = cfg["global_level"]
        any_level = any(self._effective(cfg, c) > Level.OFF for c in CATEGORIES)
        return {
            "autonomy_enabled": any_level and not ks["any_active"],
            "execution_permitted": (not ks["any_active"]) and any(self._effective(cfg, c) >= Level.APPROVED_EXECUTION
                                                                   for c in CATEGORIES),
            "global_level": global_level, "category_levels": cfg["category_levels"],
            "effective_levels": {c: self._effective(cfg, c).name for c in CATEGORIES},
            "kill_switches": ks, "database_available": db_ok, "error": err,
            "requests_by_status": counts, "pending_approvals": counts.get(PENDING, 0),
            "approved_waiting": counts.get(APPROVED, 0), "executing": counts.get(EXECUTING, 0),
            "recent_actions": recent, "recent_failures": failures, "recent_notifications": notifications,
            "audit_events_total": total, "generated_at_utc": self._ts(self._now()),
            "production_note": "The control plane never starts, stops or changes production; kill switches affect autonomy only.",
        }

    def registry_view(self) -> Dict[str, Any]:
        return {"categories": CATEGORIES, "levels": list(Level.__members__), "states": list(STATES),
                "permitted": [s.public() for s in registry.PERMITTED],
                "prohibited": [s.public() for s in registry.PROHIBITED]}


_instance: Optional[ControlPlane] = None
_instance_lock = threading.Lock()


def get_control_plane() -> ControlPlane:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ControlPlane()
        return _instance
