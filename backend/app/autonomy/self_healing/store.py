"""Self-healing persistence: incidents, recovery records, learning outcomes, resource locks, notification limits.

Only the ``selfheal_*`` tables are written. Production tables (persistent_jobs, events, schedules, publishing) are
never written by this package. History is bounded (``prune``).

Statuses (the Stage 3.32D vocabulary):
  incidents : DETECTED, DIAGNOSING, DIAGNOSED, RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED, EXECUTING, VERIFYING,
              RECOVERED, FAILED, UNSAFE, EXPIRED, CANCELLED
  recoveries: RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED, EXECUTING, VERIFYING, then a final status:
              RECOVERED (a recovery action verified), DIAGNOSED (a diagnostic completed and verified),
              FAILED (execution or verification failed, or the result could not be independently verified),
              EXPIRED, CANCELLED (rejected or cancelled by the operator), UNSAFE (refused before execution).
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

DETECTED, DIAGNOSING, DIAGNOSED = "DETECTED", "DIAGNOSING", "DIAGNOSED"
RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED = "RECOVERY_PROPOSED", "AWAITING_APPROVAL", "APPROVED"
EXECUTING, VERIFYING, RECOVERED, FAILED = "EXECUTING", "VERIFYING", "RECOVERED", "FAILED"
UNSAFE, EXPIRED, CANCELLED = "UNSAFE", "EXPIRED", "CANCELLED"
STATUSES = (DETECTED, DIAGNOSING, DIAGNOSED, RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED, EXECUTING, VERIFYING,
            RECOVERED, FAILED, UNSAFE, EXPIRED, CANCELLED)
OPEN_RECOVERY = (RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED, EXECUTING, VERIFYING)
FINAL_RECOVERY = (RECOVERED, DIAGNOSED, FAILED, EXPIRED, CANCELLED, UNSAFE)
OPEN_INCIDENT = (DETECTED, DIAGNOSING, DIAGNOSED, RECOVERY_PROPOSED, AWAITING_APPROVAL, APPROVED, EXECUTING,
                 VERIFYING, UNSAFE)
VERIFIED, VERIFY_FAILED, UNVERIFIED = "VERIFIED", "FAILED", "UNVERIFIED"

MAX_INCIDENTS = 5000
MAX_OUTCOMES = 10000
MAX_RECOVERIES = 20000
RETENTION_DAYS = 90
NOTIFICATION_RETENTION_DAYS = 30
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS selfheal_incidents (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    signature TEXT NOT NULL,
    template TEXT NOT NULL,
    cause TEXT NOT NULL,
    job_id TEXT,
    stage TEXT,
    error_type TEXT,
    message TEXT NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    failure_class TEXT NOT NULL,
    certainty TEXT NOT NULL,
    classification_json TEXT NOT NULL,
    diagnosis_json TEXT,
    plan_json TEXT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    explanation TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_selfheal_inc_status ON selfheal_incidents (status, last_seen_utc);
CREATE INDEX IF NOT EXISTS idx_selfheal_inc_signature ON selfheal_incidents (signature);
CREATE INDEX IF NOT EXISTS idx_selfheal_inc_job ON selfheal_incidents (job_id);
CREATE TABLE IF NOT EXISTS selfheal_recoveries (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    job_id TEXT,
    failure_signature TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    request_id TEXT,
    preconditions_json TEXT NOT NULL,
    approval_requirement TEXT NOT NULL,
    approval_outcome TEXT,
    status TEXT NOT NULL,
    timeout_seconds REAL NOT NULL,
    retry_limit INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    execution_result_json TEXT,
    verification_json TEXT,
    verification_result TEXT,
    final_status TEXT,
    status_reason TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_selfheal_rcv_open ON selfheal_recoveries (incident_id, action_type, payload_hash)
    WHERE status IN ('RECOVERY_PROPOSED','AWAITING_APPROVAL','APPROVED','EXECUTING','VERIFYING');
CREATE INDEX IF NOT EXISTS idx_selfheal_rcv_status ON selfheal_recoveries (status);
CREATE INDEX IF NOT EXISTS idx_selfheal_rcv_request ON selfheal_recoveries (request_id);
CREATE INDEX IF NOT EXISTS idx_selfheal_rcv_incident ON selfheal_recoveries (incident_id);
CREATE TABLE IF NOT EXISTS selfheal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recovery_id TEXT NOT NULL UNIQUE,
    incident_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    action_type TEXT NOT NULL,
    diagnostic TEXT,
    findings_json TEXT NOT NULL,
    approval_outcome TEXT,
    execution_result TEXT NOT NULL,
    verification_result TEXT,
    final_status TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    recurred INTEGER NOT NULL DEFAULT 0,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    last_recurrence_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfheal_out_sig ON selfheal_outcomes (signature, action_type);
CREATE TABLE IF NOT EXISTS selfheal_resource_locks (
    resource_key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    acquired_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selfheal_notifications (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    last_sent_utc TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    last_outcome TEXT
);
CREATE TABLE IF NOT EXISTS selfheal_scan_runs (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    mode TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    cursor_utc TEXT,
    summary_json TEXT NOT NULL
);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _loads(raw: Optional[str], default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _dumps(value: Any) -> str:
    from backend.app.autonomy.control_plane import canonical_json, redact
    return canonical_json(redact(value))


class ResourceBusy(Exception):
    pass


def read_learning(db_path: str) -> Optional[Dict[str, Any]]:
    """Read-only learning data for the recommendation engine. None when self-healing has never run here."""
    store = SelfHealingStore(db_path, readonly=True)
    try:
        with store.read() as conn:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'selfheal_%'")}
        if not {"selfheal_outcomes", "selfheal_incidents", "selfheal_recoveries"} <= have:
            return None
        return {"learning": store.learning(200), "deterministic": store.deterministic_signatures()}
    except (sqlite3.Error, FileNotFoundError):
        return None


class SelfHealingStore:
    def __init__(self, db_path: str, readonly: bool = False):
        self.db_path = str(db_path)
        self.readonly = readonly          # read-only: never creates tables (used by the recommendation engine)
        self._ready = False
        self._lock = threading.Lock()

    # -- connections ------------------------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + ("?mode=ro" if self.readonly else "?mode=rw"),
                               uri=True, timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.readonly:
            conn.execute("PRAGMA query_only = ON")
            return conn
        if not self._ready:
            with self._lock:
                if not self._ready:
                    conn.executescript(SCHEMA)
                    self._ready = True
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
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
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # -- incidents --------------------------------------------------------------------------------------------
    @staticmethod
    def _incident(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["classification"] = _loads(d.pop("classification_json"), {})
        d["diagnosis"] = _loads(d.pop("diagnosis_json"), None)
        d["plan"] = _loads(d.pop("plan_json"), None)
        d["kind"] = "self_healing_incident"
        return d

    def upsert_incident(self, conn: sqlite3.Connection, *, dedupe_key: str, signature: str, template: str, cause: str,
                        job_id: Optional[str], stage: Optional[str], error_type: str, message: str, at: str,
                        failure_class: str, certainty: str, classification: Dict[str, Any], severity: str,
                        now: datetime) -> Dict[str, Any]:
        """Create the incident or, for a known (job, signature), count a new occurrence. Returns {incident, created}."""
        from backend.app.autonomy.control_plane import redact
        message, template, error_type = redact(message or ""), redact(template or ""), redact(error_type or "")
        row = conn.execute("SELECT * FROM selfheal_incidents WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        now_s = _ts(now)
        if row is None:
            iid = "inc_" + secrets.token_hex(6)
            conn.execute("INSERT INTO selfheal_incidents (id, dedupe_key, signature, template, cause, job_id, stage, "
                         "error_type, message, first_seen_utc, last_seen_utc, occurrences, failure_class, certainty, "
                         "classification_json, status, severity, created_at_utc, updated_at_utc) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)",
                         (iid, dedupe_key, signature, template[:300], cause, job_id, stage, error_type[:100],
                          message[:2000], at, at, failure_class, certainty, _dumps(classification), DETECTED,
                          severity, now_s, now_s))
            return {"incident": self._incident(conn.execute("SELECT * FROM selfheal_incidents WHERE id=?", (iid,)).fetchone()),
                    "created": True}
        changed = at > row["last_seen_utc"]
        conn.execute("UPDATE selfheal_incidents SET last_seen_utc=?, occurrences=occurrences+?, failure_class=?, "
                     "certainty=?, classification_json=?, severity=?, updated_at_utc=? WHERE id=?",
                     (max(at, row["last_seen_utc"]), 1 if changed else 0, failure_class, certainty,
                      _dumps(classification), severity, now_s, row["id"]))
        return {"incident": self._incident(conn.execute("SELECT * FROM selfheal_incidents WHERE id=?", (row["id"],)).fetchone()),
                "created": False, "new_occurrence": changed}

    def set_incident(self, conn: sqlite3.Connection, incident_id: str, now: datetime, **fields: Any) -> None:
        cols, args = [], []
        for k, v in fields.items():
            if k in ("diagnosis", "plan"):
                cols.append(f"{k}_json=?")
                args.append(_dumps(v) if v is not None else None)
            elif k in ("status", "severity", "explanation"):
                cols.append(f"{k}=?")
                args.append(v)
            else:
                raise ValueError(f"unknown incident field {k}")
        cols.append("updated_at_utc=?")
        args.append(_ts(now))
        conn.execute(f"UPDATE selfheal_incidents SET {', '.join(cols)} WHERE id=?", args + [incident_id])

    def get_incident(self, incident_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        if conn is not None:
            row = conn.execute("SELECT * FROM selfheal_incidents WHERE id=?", (incident_id,)).fetchone()
            return self._incident(row) if row else None
        with self.read() as c:
            return self.get_incident(incident_id, c)

    def list_incidents(self, status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if status == "open":
                rows = conn.execute(f"SELECT * FROM selfheal_incidents WHERE status IN ({','.join('?' * len(OPEN_INCIDENT))}) "
                                    "ORDER BY last_seen_utc DESC LIMIT ?", (*OPEN_INCIDENT, limit)).fetchall()
            elif status == "all":
                rows = conn.execute("SELECT * FROM selfheal_incidents ORDER BY last_seen_utc DESC LIMIT ?", (limit,)).fetchall()
            elif status in STATUSES:
                rows = conn.execute("SELECT * FROM selfheal_incidents WHERE status=? ORDER BY last_seen_utc DESC LIMIT ?",
                                    (status, limit)).fetchall()
            else:
                raise ValueError("status must be open, all or one of " + ", ".join(STATUSES))
            return [self._incident(r) for r in rows]

    def signature_history(self, conn: sqlite3.Connection, signature: str, limit: int = 50) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT id, job_id, stage, occurrences, first_seen_utc, last_seen_utc, status, failure_class "
                            "FROM selfheal_incidents WHERE signature=? ORDER BY last_seen_utc DESC LIMIT ?",
                            (signature, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- recoveries -------------------------------------------------------------------------------------------
    @staticmethod
    def _recovery(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("payload_json", "preconditions_json", "execution_result_json", "verification_json"):
            d[k[:-5]] = _loads(d.pop(k), None)
        d["kind"] = "self_healing_recovery"
        return d

    def create_recovery(self, conn: sqlite3.Connection, *, incident: Dict[str, Any], action_type: str,
                        payload: Dict[str, Any], payload_hash: str, preconditions: Dict[str, Any],
                        approval_requirement: str, timeout_seconds: float, retry_limit: int, status: str,
                        now: datetime, request_id: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
        rid = "rcv_" + secrets.token_hex(6)
        now_s = _ts(now)
        conn.execute("INSERT INTO selfheal_recoveries (id, incident_id, job_id, failure_signature, action_type, payload_json, "
                     "payload_hash, request_id, preconditions_json, approval_requirement, status, timeout_seconds, "
                     "retry_limit, status_reason, created_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, incident["id"], incident.get("job_id"), incident["signature"], action_type, _dumps(payload),
                      payload_hash, request_id, _dumps(preconditions), approval_requirement, status, float(timeout_seconds),
                      int(retry_limit), reason[:500], now_s, now_s))
        return self._recovery(conn.execute("SELECT * FROM selfheal_recoveries WHERE id=?", (rid,)).fetchone())

    def open_recovery(self, conn: sqlite3.Connection, incident_id: str, action_type: str,
                      payload_hash: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(f"SELECT * FROM selfheal_recoveries WHERE incident_id=? AND action_type=? AND payload_hash=? "
                           f"AND status IN ({','.join('?' * len(OPEN_RECOVERY))})",
                           (incident_id, action_type, payload_hash, *OPEN_RECOVERY)).fetchone()
        return self._recovery(row) if row else None

    def update_recovery(self, conn: sqlite3.Connection, recovery_id: str, now: datetime, *,
                        expect_status: Optional[Sequence[str]] = None, **fields: Any) -> bool:
        allowed = {"status", "request_id", "approval_outcome", "attempts", "started_at_utc", "finished_at_utc",
                   "verification_result", "final_status", "status_reason"}
        cols, args = [], []
        for k, v in fields.items():
            if k in ("execution_result", "verification"):
                cols.append(f"{k}_json=?")
                args.append(_dumps(v) if v is not None else None)
            elif k in allowed:
                cols.append(f"{k}=?")
                args.append(v)
            else:
                raise ValueError(f"unknown recovery field {k}")
        cols.append("updated_at_utc=?")
        args.append(_ts(now))
        sql = f"UPDATE selfheal_recoveries SET {', '.join(cols)} WHERE id=?"
        args.append(recovery_id)
        if expect_status:
            sql += f" AND status IN ({','.join('?' * len(expect_status))})"
            args.extend(expect_status)
        return conn.execute(sql, args).rowcount == 1

    def get_recovery(self, recovery_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        if conn is not None:
            row = conn.execute("SELECT * FROM selfheal_recoveries WHERE id=?", (recovery_id,)).fetchone()
            return self._recovery(row) if row else None
        with self.read() as c:
            return self.get_recovery(recovery_id, c)

    def recoveries_for(self, incident_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.read() as conn:
            return [self._recovery(r) for r in conn.execute(
                "SELECT * FROM selfheal_recoveries WHERE incident_id=? ORDER BY created_at_utc LIMIT ?", (incident_id, limit))]

    def list_recoveries(self, status: str = "all", limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if status == "open":
                rows = conn.execute(f"SELECT * FROM selfheal_recoveries WHERE status IN ({','.join('?' * len(OPEN_RECOVERY))}) "
                                    "ORDER BY created_at_utc DESC LIMIT ?", (*OPEN_RECOVERY, limit)).fetchall()
            elif status == "history":
                rows = conn.execute(f"SELECT * FROM selfheal_recoveries WHERE status IN ({','.join('?' * len(FINAL_RECOVERY))}) "
                                    "ORDER BY updated_at_utc DESC LIMIT ?", (*FINAL_RECOVERY, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM selfheal_recoveries ORDER BY created_at_utc DESC LIMIT ?", (limit,)).fetchall()
            return [self._recovery(r) for r in rows]

    def open_recoveries_with_requests(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self.read() as conn:
            return [self._recovery(r) for r in conn.execute(
                f"SELECT * FROM selfheal_recoveries WHERE request_id IS NOT NULL AND status IN "
                f"({','.join('?' * len(OPEN_RECOVERY))}) ORDER BY created_at_utc LIMIT ?", (*OPEN_RECOVERY, limit))]

    def tables_exist(self) -> bool:
        """True once self-healing has run here. Checked read-only, so reads never create tables."""
        try:
            conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return False
        try:
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'selfheal_%'")}
        except sqlite3.Error:
            return False
        finally:
            conn.close()
        return {"selfheal_incidents", "selfheal_recoveries", "selfheal_outcomes", "selfheal_resource_locks",
                "selfheal_notifications", "selfheal_scan_runs"} <= have

    def recent_completed(self, conn: sqlite3.Connection, signature: str, action_type: str, payload_hash: str,
                         since: str) -> Optional[Dict[str, Any]]:
        row = conn.execute("SELECT * FROM selfheal_recoveries WHERE failure_signature=? AND action_type=? AND payload_hash=? "
                           "AND status IN ('RECOVERED','DIAGNOSED') AND finished_at_utc>=? ORDER BY finished_at_utc DESC LIMIT 1",
                           (signature, action_type, payload_hash, since)).fetchone()
        return self._recovery(row) if row else None

    # -- learning ---------------------------------------------------------------------------------------------
    def record_outcome(self, conn: sqlite3.Connection, recovery: Dict[str, Any], incident: Dict[str, Any],
                       execution_result: str, findings: Dict[str, Any], now: datetime) -> None:
        conn.execute("INSERT OR IGNORE INTO selfheal_outcomes (recovery_id, incident_id, signature, failure_class, action_type, "
                     "diagnostic, findings_json, approval_outcome, execution_result, verification_result, final_status, "
                     "finished_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (recovery["id"], incident["id"], incident["signature"], incident["failure_class"],
                      recovery["action_type"], (recovery.get("payload") or {}).get("diagnostic"), _dumps(findings),
                      recovery.get("approval_outcome"), execution_result, recovery.get("verification_result"),
                      recovery.get("final_status") or recovery.get("status"), _ts(now)))

    def mark_recurrence(self, conn: sqlite3.Connection, signature: str, at: str) -> int:
        """A failure with this signature happened at ``at``: every earlier finished outcome for the signature counts
        a recurrence (the problem came back after that recovery)."""
        cur = conn.execute("UPDATE selfheal_outcomes SET recurred=1, recurrence_count=recurrence_count+1, "
                           "last_recurrence_utc=? WHERE signature=? AND finished_at_utc < ? AND "
                           "(last_recurrence_utc IS NULL OR last_recurrence_utc < ?)", (at, signature, at, at))
        return cur.rowcount

    def learning(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Per (signature, action type): attempts, verified, failed, unverified, recurred after success."""
        with self.read() as conn:
            rows = conn.execute(
                "SELECT o.signature, o.action_type, o.failure_class, COUNT(*) AS attempts, "
                "SUM(CASE WHEN o.final_status IN ('RECOVERED','DIAGNOSED') THEN 1 ELSE 0 END) AS succeeded, "
                "SUM(CASE WHEN o.final_status='FAILED' THEN 1 ELSE 0 END) AS failed, "
                "SUM(CASE WHEN o.verification_result='UNVERIFIED' THEN 1 ELSE 0 END) AS unverified, "
                "SUM(CASE WHEN o.final_status IN ('RECOVERED','DIAGNOSED') AND o.recurred=1 THEN 1 ELSE 0 END) AS recurred_after_success, "
                "SUM(CASE WHEN o.approval_outcome='REJECTED' THEN 1 ELSE 0 END) AS rejected, "
                "MAX(o.finished_at_utc) AS last_finished, MAX(i.template) AS template, MAX(i.occurrences) AS max_occurrences "
                "FROM selfheal_outcomes o LEFT JOIN selfheal_incidents i ON i.id=o.incident_id "
                "GROUP BY o.signature, o.action_type ORDER BY last_finished DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def deterministic_signatures(self, min_occurrences: int = 3, limit: int = 50) -> List[Dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT signature, MAX(template) AS template, MAX(cause) AS cause, MAX(certainty) AS certainty, "
                "SUM(occurrences) AS occurrences, COUNT(DISTINCT job_id) AS jobs, MAX(last_seen_utc) AS last_seen, "
                "GROUP_CONCAT(DISTINCT job_id) AS job_ids, GROUP_CONCAT(DISTINCT stage) AS stages "
                "FROM selfheal_incidents WHERE failure_class='DETERMINISTIC' GROUP BY signature "
                "HAVING SUM(occurrences) >= ? ORDER BY occurrences DESC LIMIT ?", (min_occurrences, limit)).fetchall()
            return [dict(r) for r in rows]

    # -- resource locks ---------------------------------------------------------------------------------------
    def acquire_locks(self, keys: Sequence[str], owner: str, ttl_seconds: float, now: datetime) -> None:
        """All-or-nothing. Expired locks are reclaimed. Raises ResourceBusy if any key is held by another owner."""
        now_s = _ts(now)
        exp = _ts(now + timedelta(seconds=ttl_seconds))
        with self.tx() as conn:
            conn.execute("DELETE FROM selfheal_resource_locks WHERE expires_at_utc <= ?", (now_s,))
            for key in keys:
                row = conn.execute("SELECT owner FROM selfheal_resource_locks WHERE resource_key=?", (key,)).fetchone()
                if row and row["owner"] != owner:
                    raise ResourceBusy(f"resource {key} is locked by another recovery execution")
            for key in keys:
                conn.execute("INSERT INTO selfheal_resource_locks (resource_key, owner, acquired_at_utc, expires_at_utc) "
                             "VALUES (?,?,?,?) ON CONFLICT(resource_key) DO UPDATE SET expires_at_utc=excluded.expires_at_utc",
                             (key, owner, now_s, exp))

    def release_locks(self, owner: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM selfheal_resource_locks WHERE owner=?", (owner,))

    def held_locks(self, keys: Sequence[str], now: datetime) -> List[Dict[str, Any]]:
        if not keys:
            return []
        with self.read() as conn:
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM selfheal_resource_locks WHERE expires_at_utc > ? AND resource_key IN "
                f"({','.join('?' * len(keys))})", (_ts(now), *keys))]

    # -- notifications ----------------------------------------------------------------------------------------
    def notification_allowed(self, key: str, cooldown_hours: float, now: datetime) -> bool:
        with self.read() as conn:
            row = conn.execute("SELECT last_sent_utc FROM selfheal_notifications WHERE key=?", (key,)).fetchone()
        return row is None or row["last_sent_utc"] <= _ts(now - timedelta(hours=cooldown_hours))

    def notifications_since(self, since: datetime) -> int:
        with self.read() as conn:
            return conn.execute("SELECT COUNT(*) FROM selfheal_notifications WHERE last_sent_utc > ?",
                                (_ts(since),)).fetchone()[0]

    def record_notification(self, key: str, kind: str, outcome: str, now: datetime) -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO selfheal_notifications (key, kind, last_sent_utc, count, last_outcome) VALUES (?,?,?,1,?) "
                         "ON CONFLICT(key) DO UPDATE SET last_sent_utc=excluded.last_sent_utc, count=count+1, "
                         "last_outcome=excluded.last_outcome", (key, kind, _ts(now), outcome[:200]))

    # -- scan runs ----------------------------------------------------------------------------------------------
    def record_scan(self, run: Dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO selfheal_scan_runs (run_id, started_at_utc, finished_at_utc, mode, triggered_by, "
                         "cursor_utc, summary_json) VALUES (?,?,?,?,?,?,?)",
                         (run["run_id"], run["started_at"], run.get("finished_at"), run["mode"], run["triggered_by"],
                          run.get("cursor"), _dumps(run)))

    def last_scan(self) -> Optional[Dict[str, Any]]:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM selfheal_scan_runs ORDER BY started_at_utc DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["summary"] = _loads(d.pop("summary_json"), {})
        return d

    def last_cursor(self) -> Optional[str]:
        with self.read() as conn:
            row = conn.execute("SELECT MAX(cursor_utc) FROM selfheal_scan_runs WHERE cursor_utc IS NOT NULL").fetchone()
        return row[0] if row else None

    # -- summaries ----------------------------------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        with self.read() as conn:
            inc = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM selfheal_incidents GROUP BY status")}
            cls = {r[0]: r[1] for r in conn.execute(
                f"SELECT failure_class, COUNT(*) FROM selfheal_incidents WHERE status IN ({','.join('?' * len(OPEN_INCIDENT))}) "
                "GROUP BY failure_class", OPEN_INCIDENT)}
            rcv = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM selfheal_recoveries GROUP BY status")}
            ver = {r[0] or "NONE": r[1] for r in conn.execute(
                "SELECT verification_result, COUNT(*) FROM selfheal_recoveries GROUP BY verification_result")}
            by_action = [dict(r) for r in conn.execute(
                "SELECT action_type, COUNT(*) AS total, SUM(CASE WHEN status IN ('RECOVERED','DIAGNOSED') THEN 1 ELSE 0 END) "
                "AS succeeded, SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed FROM selfheal_recoveries "
                "GROUP BY action_type")]
        recovery_actions = [a for a in by_action if a["action_type"] != "selfheal.diagnostic.run"]
        done = sum((a["succeeded"] or 0) + (a["failed"] or 0) for a in recovery_actions)
        ok = sum(a["succeeded"] or 0 for a in recovery_actions)
        return {"incidents_by_status": inc, "open_by_class": cls, "recoveries_by_status": rcv,
                "verification": ver, "by_action": by_action,
                "open_incidents": sum(v for k, v in inc.items() if k in OPEN_INCIDENT),
                "awaiting_approval": rcv.get(AWAITING_APPROVAL, 0),
                "recovery_success_rate": round(100.0 * ok / done, 1) if done else None,
                "recovery_actions_finished": done}

    # -- bounded history ----------------------------------------------------------------------------------------
    def prune(self, now: datetime) -> Dict[str, int]:
        cutoff = _ts(now - timedelta(days=RETENTION_DAYS))
        with self.tx() as conn:
            final_inc = ("RECOVERED", "FAILED", "EXPIRED", "CANCELLED", "DIAGNOSED")
            a = conn.execute(f"DELETE FROM selfheal_incidents WHERE last_seen_utc < ? AND status IN "
                             f"({','.join('?' * len(final_inc))}) AND id NOT IN (SELECT incident_id FROM selfheal_recoveries "
                             f"WHERE status IN ({','.join('?' * len(OPEN_RECOVERY))}))",
                             (cutoff, *final_inc, *OPEN_RECOVERY)).rowcount
            n = conn.execute("SELECT COUNT(*) FROM selfheal_incidents").fetchone()[0]
            b = 0
            if n > MAX_INCIDENTS:
                b = conn.execute(f"DELETE FROM selfheal_incidents WHERE id IN (SELECT id FROM selfheal_incidents WHERE status "
                                 f"NOT IN ({','.join('?' * len(OPEN_INCIDENT))}) ORDER BY last_seen_utc LIMIT ?)",
                                 (*OPEN_INCIDENT, n - MAX_INCIDENTS)).rowcount
            m = conn.execute("SELECT COUNT(*) FROM selfheal_outcomes").fetchone()[0]
            c = 0
            if m > MAX_OUTCOMES:
                c = conn.execute("DELETE FROM selfheal_outcomes WHERE id IN (SELECT id FROM selfheal_outcomes ORDER BY id LIMIT ?)",
                                 (m - MAX_OUTCOMES,)).rowcount
            conn.execute("DELETE FROM selfheal_scan_runs WHERE started_at_utc < ?", (cutoff,))
            conn.execute("DELETE FROM selfheal_resource_locks WHERE expires_at_utc <= ?", (_ts(now),))
            # Finished recoveries whose incident was pruned, then a hard cap on finished recoveries.
            d = conn.execute(f"DELETE FROM selfheal_recoveries WHERE status IN ({','.join('?' * len(FINAL_RECOVERY))}) "
                             "AND incident_id NOT IN (SELECT id FROM selfheal_incidents)", FINAL_RECOVERY).rowcount
            r = conn.execute("SELECT COUNT(*) FROM selfheal_recoveries").fetchone()[0]
            if r > MAX_RECOVERIES:
                d += conn.execute(f"DELETE FROM selfheal_recoveries WHERE id IN (SELECT id FROM selfheal_recoveries WHERE "
                                  f"status IN ({','.join('?' * len(FINAL_RECOVERY))}) ORDER BY updated_at_utc LIMIT ?)",
                                  (*FINAL_RECOVERY, r - MAX_RECOVERIES)).rowcount
            e = conn.execute("DELETE FROM selfheal_notifications WHERE last_sent_utc < ?",
                             (_ts(now - timedelta(days=NOTIFICATION_RETENTION_DAYS)),)).rowcount
        return {"incidents_expired": a, "incidents_over_cap": b, "outcomes_over_cap": c, "recoveries_pruned": d,
                "notification_keys_pruned": e}
