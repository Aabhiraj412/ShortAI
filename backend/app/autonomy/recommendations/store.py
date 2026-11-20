"""Recommendation persistence: deduplication, update-in-place, expiry, supersession and operator status changes.

Tables (studio.db, created on first use like the other autonomy tables):
  autonomy_recommendations      one row per recommendation; a partial UNIQUE index on dedupe_key for open statuses
                                guarantees one open recommendation per underlying issue.
  autonomy_recommendation_runs  one row per engine run (the historical trend).
Status history is written to the append-only 3.32B audit trail by the engine, not here.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.app.autonomy.control_plane import Conflict, ControlPlaneError, NotFound, Unavailable, redact

NEW, ACKNOWLEDGED, APPROVED, REJECTED = "NEW", "ACKNOWLEDGED", "APPROVED", "REJECTED"
RESOLVED, EXPIRED, SUPERSEDED = "RESOLVED", "EXPIRED", "SUPERSEDED"
STATUSES = (NEW, ACKNOWLEDGED, APPROVED, REJECTED, RESOLVED, EXPIRED, SUPERSEDED)
OPEN = (NEW, ACKNOWLEDGED, APPROVED)
OPERATOR_TRANSITIONS = {
    NEW: (ACKNOWLEDGED, APPROVED, REJECTED, RESOLVED),
    ACKNOWLEDGED: (APPROVED, REJECTED, RESOLVED),
    APPROVED: (REJECTED, RESOLVED),
}
REJECTION_COOLDOWN_HOURS = 7 * 24
TS = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomy_recommendations (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    problem TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    observations_json TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    hypothesis_strength TEXT NOT NULL,
    action TEXT NOT NULL,
    benefit TEXT NOT NULL,
    risk TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    confidence_label TEXT NOT NULL,
    priority REAL NOT NULL,
    priority_label TEXT NOT NULL,
    scoring_json TEXT NOT NULL,
    related_json TEXT NOT NULL,
    occurrences INTEGER NOT NULL,
    detections INTEGER NOT NULL DEFAULT 1,
    first_detected_at TEXT NOT NULL,
    last_detected_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,
    status_by TEXT,
    status_at TEXT,
    status_reason TEXT,
    resolution_evidence TEXT,
    superseded_by TEXT,
    previous_id TEXT,
    explanation TEXT,
    explanation_fingerprint TEXT,
    last_run_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_autonomy_rec_open_key ON autonomy_recommendations (dedupe_key)
    WHERE status IN ('NEW', 'ACKNOWLEDGED', 'APPROVED');
CREATE INDEX IF NOT EXISTS idx_autonomy_rec_status ON autonomy_recommendations (status, priority);
CREATE INDEX IF NOT EXISTS idx_autonomy_rec_key ON autonomy_recommendations (dedupe_key, status_at);
CREATE INDEX IF NOT EXISTS idx_autonomy_rec_expiry ON autonomy_recommendations (status, expires_at);
CREATE TABLE IF NOT EXISTS autonomy_recommendation_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    triggered_by TEXT NOT NULL,
    findings INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    expired INTEGER NOT NULL DEFAULT 0,
    superseded INTEGER NOT NULL DEFAULT 0,
    suppressed INTEGER NOT NULL DEFAULT 0,
    insufficient_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '{}',
    skipped_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_autonomy_rec_runs_started ON autonomy_recommendation_runs (started_at);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS)


class RecommendationStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._ready = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if not Path(self.db_path).is_file():
            raise Unavailable("database not available")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=rw", uri=True, timeout=5.0,
                               isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
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

    # -- engine side ------------------------------------------------------------------------------------------
    def upsert(self, conn: sqlite3.Connection, finding: Any, conf: Dict[str, Any], prio: Dict[str, Any], now: datetime,
               run_id: str) -> Dict[str, Any]:
        """Create or update the open recommendation for ``finding.key``. Returns {"action": created|updated|suppressed, ...}."""
        now_s = _ts(now)
        expires = _ts(now + timedelta(hours=finding.ttl_hours))
        evidence = canonical(redact(finding.evidence))
        related = canonical(redact({"jobs": finding.affected_jobs, "events": finding.related_events}))
        scoring = canonical({"confidence": conf, "priority": prio, "sample_size": finding.sample_size,
                             "min_sample": finding.min_sample})
        row = conn.execute("SELECT * FROM autonomy_recommendations WHERE dedupe_key=? AND status IN ('NEW','ACKNOWLEDGED','APPROVED')",
                           (finding.key,)).fetchone()
        if row:
            row = dict(row)
            conn.execute(
                "UPDATE autonomy_recommendations SET title=?, description=?, problem=?, evidence_json=?, hypothesis=?, "
                "hypothesis_strength=?, action=?, benefit=?, risk=?, severity=?, confidence=?, confidence_label=?, priority=?, "
                "priority_label=?, scoring_json=?, related_json=?, occurrences=?, detections=detections+1, last_detected_at=?, "
                "updated_at=?, expires_at=?, last_run_id=? WHERE id=? AND status=?",
                (finding.title, finding.description, finding.problem, evidence, finding.hypothesis, finding.hypothesis_strength,
                 finding.action, finding.benefit, finding.risk, finding.severity, conf["score"], conf["label"], prio["score"],
                 prio["label"], scoring, related, finding.occurrences, now_s, now_s, expires, run_id, row["id"], row["status"]))
            changed = (row["priority_label"] != prio["label"] or row["confidence_label"] != conf["label"]
                       or row["severity"] != finding.severity)
            return {"action": "updated", "id": row["id"], "material_change": changed,
                    "previous": {"priority_label": row["priority_label"], "confidence_label": row["confidence_label"]}}
        closed = conn.execute("SELECT id, status, status_at FROM autonomy_recommendations WHERE dedupe_key=? "
                              "ORDER BY created_at DESC LIMIT 1", (finding.key,)).fetchone()
        observations = list(finding.observations)
        previous_id = None
        if closed:
            previous_id = closed["id"]
            if closed["status"] == REJECTED and closed["status_at"] and \
                    closed["status_at"] > _ts(now - timedelta(hours=REJECTION_COOLDOWN_HOURS)):
                return {"action": "suppressed", "id": closed["id"], "reason": "rejected by the operator within the last 7 days"}
            if closed["status"] == RESOLVED:
                observations.insert(0, f"Detected again after recommendation {closed['id']} was marked resolved ({closed['status_at']}).")
            elif closed["status"] in (EXPIRED, SUPERSEDED, REJECTED):
                observations.insert(0, f"Previously reported as {closed['id']} ({closed['status'].lower()}).")
        rid = "rec_" + secrets.token_hex(8)
        conn.execute(
            "INSERT INTO autonomy_recommendations (id, dedupe_key, rule_id, category, subject, title, description, problem, "
            "evidence_json, observations_json, hypothesis, hypothesis_strength, action, benefit, risk, severity, confidence, "
            "confidence_label, priority, priority_label, scoring_json, related_json, occurrences, first_detected_at, "
            "last_detected_at, created_at, updated_at, expires_at, status, previous_id, last_run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, finding.key, finding.rule_id, finding.category, finding.subject[:200], finding.title, finding.description,
             finding.problem, evidence, canonical(redact(observations)), finding.hypothesis, finding.hypothesis_strength,
             finding.action, finding.benefit, finding.risk, finding.severity, conf["score"], conf["label"], prio["score"],
             prio["label"], scoring, related, finding.occurrences, now_s, now_s, now_s, now_s, expires, NEW, previous_id, run_id))
        return {"action": "created", "id": rid}

    def supersede(self, conn: sqlite3.Connection, keys: List[str], by_id: str, now: datetime) -> List[Dict[str, Any]]:
        out = []
        for key in keys:
            for row in conn.execute("SELECT id, status FROM autonomy_recommendations WHERE dedupe_key=? AND status IN "
                                    "('NEW','ACKNOWLEDGED','APPROVED')", (key,)).fetchall():
                conn.execute("UPDATE autonomy_recommendations SET status=?, superseded_by=?, status_at=?, status_by=?, "
                             "updated_at=? WHERE id=? AND status=?",
                             (SUPERSEDED, by_id, _ts(now), "recommendation-engine", _ts(now), row["id"], row["status"]))
                out.append({"id": row["id"], "previous": row["status"]})
        return out

    def expire_due(self, conn: sqlite3.Connection, now: datetime) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT id, status FROM autonomy_recommendations WHERE status IN ('NEW','ACKNOWLEDGED','APPROVED') "
                            "AND expires_at <= ?", (_ts(now),)).fetchall()
        for row in rows:
            conn.execute("UPDATE autonomy_recommendations SET status=?, status_at=?, status_by=?, status_reason=?, updated_at=? "
                         "WHERE id=? AND status=?", (EXPIRED, _ts(now), "recommendation-engine",
                                                    "no longer detected within its expiry period", _ts(now), row["id"], row["status"]))
        return [dict(r) for r in rows]

    # -- operator side ----------------------------------------------------------------------------------------
    def transition(self, rec_id: str, new_status: str, operator: str, now: datetime, reason: Optional[str] = None,
                   evidence: Optional[str] = None) -> Dict[str, Any]:
        if new_status not in (ACKNOWLEDGED, APPROVED, REJECTED, RESOLVED):
            raise ControlPlaneError("operators can acknowledge, approve, reject or resolve a recommendation")
        if new_status == REJECTED and not (reason or "").strip():
            raise ControlPlaneError("a reason is required to reject a recommendation")
        if new_status == RESOLVED and not (evidence or "").strip():
            raise ControlPlaneError("evidence is required to mark a recommendation resolved")
        with self.tx() as conn:
            row = self._row(conn, rec_id)
            if row["status"] == new_status:
                return {"recommendation": self._public(row), "unchanged": True, "previous": row["status"]}
            if new_status not in OPERATOR_TRANSITIONS.get(row["status"], ()):
                raise Conflict(f"recommendation is {row['status']}; it cannot become {new_status}")
            cur = conn.execute("UPDATE autonomy_recommendations SET status=?, status_by=?, status_at=?, status_reason=?, "
                               "resolution_evidence=COALESCE(?, resolution_evidence), updated_at=? WHERE id=? AND status=?",
                               (new_status, operator, _ts(now), redact((reason or "").strip()[:500]) or None,
                                redact((evidence or "").strip()[:2000]) or None, _ts(now), rec_id, row["status"]))
            if cur.rowcount != 1:
                raise Conflict("recommendation changed concurrently; reload and try again")
            return {"recommendation": self._public(self._row(conn, rec_id)), "unchanged": False, "previous": row["status"]}

    def set_explanation(self, rec_id: str, text: str, fingerprint: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE autonomy_recommendations SET explanation=?, explanation_fingerprint=? WHERE id=?",
                         (redact(text[:2000]), fingerprint, rec_id))

    # -- runs -------------------------------------------------------------------------------------------------
    def record_run(self, run: Dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO autonomy_recommendation_runs (run_id, started_at, finished_at, mode, window_days, triggered_by, "
                "findings, created, updated, expired, superseded, suppressed, insufficient_json, sources_json, skipped_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["run_id"], run["started_at"], run.get("finished_at"), run["mode"], run["window_days"], run["triggered_by"],
                 run.get("findings", 0), run.get("created", 0), run.get("updated", 0), run.get("expired", 0),
                 run.get("superseded", 0), run.get("suppressed", 0), canonical(run.get("insufficient", [])),
                 canonical(run.get("sources", {})), run.get("skipped_reason")))

    def last_run(self, completed_only: bool = True) -> Optional[Dict[str, Any]]:
        with self.read() as conn:
            sql = "SELECT * FROM autonomy_recommendation_runs"
            if completed_only:
                sql += " WHERE skipped_reason IS NULL"
            row = conn.execute(sql + " ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["insufficient"] = json.loads(d.pop("insufficient_json") or "[]")
        d["sources"] = json.loads(d.pop("sources_json") or "{}")
        return d

    def trend(self, days: int, now: datetime) -> Dict[str, Any]:
        since = _ts(now - timedelta(days=days))
        with self.read() as conn:
            runs = conn.execute("SELECT substr(started_at,1,10) d, COUNT(*) n, MAX(findings) f, SUM(created) c, SUM(expired) e "
                                "FROM autonomy_recommendation_runs WHERE started_at>=? AND skipped_reason IS NULL GROUP BY d ORDER BY d",
                                (since,)).fetchall()
            created = conn.execute("SELECT substr(created_at,1,10) d, category, COUNT(*) n FROM autonomy_recommendations "
                                   "WHERE created_at>=? GROUP BY d, category ORDER BY d", (since,)).fetchall()
        by_day: Dict[str, Dict[str, int]] = {}
        for r in created:
            by_day.setdefault(r["d"], {})[r["category"]] = r["n"]
        return {"days": days, "runs": [dict(r) for r in runs], "created_by_day": by_day}

    # -- reads ------------------------------------------------------------------------------------------------
    def _row(self, conn: sqlite3.Connection, rec_id: Any) -> Dict[str, Any]:
        import re
        if not isinstance(rec_id, str) or not re.fullmatch(r"rec_[0-9a-f]{16}", rec_id):
            raise NotFound("unknown recommendation")
        row = conn.execute("SELECT * FROM autonomy_recommendations WHERE id=?", (rec_id,)).fetchone()
        if row is None:
            raise NotFound("unknown recommendation")
        return dict(row)

    @staticmethod
    def _public(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for k in ("evidence_json", "observations_json", "scoring_json", "related_json"):
            out[k[:-5]] = json.loads(out.pop(k) or "null")
        out["open"] = out["status"] in OPEN
        out["kind"] = "recommendation"          # never an executable approval request
        out.pop("explanation_fingerprint", None)
        return out

    def get(self, rec_id: str) -> Dict[str, Any]:
        with self.read() as conn:
            return self._public(self._row(conn, rec_id))

    def list(self, status: Optional[str] = None, category: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 300))
        sql, args = "SELECT * FROM autonomy_recommendations WHERE 1=1", []
        if status == "open":
            sql += " AND status IN ('NEW','ACKNOWLEDGED','APPROVED')"
        elif status == "history":
            sql += " AND status NOT IN ('NEW','ACKNOWLEDGED','APPROVED')"
        elif status and status != "all":
            if status not in STATUSES:
                raise ControlPlaneError(f"status must be open, history, all or one of {', '.join(STATUSES)}")
            sql += " AND status=?"
            args.append(status)
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY CASE WHEN status IN ('NEW','ACKNOWLEDGED','APPROVED') THEN 0 ELSE 1 END, priority DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        with self.read() as conn:
            return [self._public(dict(r)) for r in conn.execute(sql, args).fetchall()]

    def summary(self, now: datetime) -> Dict[str, Any]:
        with self.read() as conn:
            open_rows = conn.execute("SELECT category, priority_label, confidence_label, created_at FROM autonomy_recommendations "
                                     "WHERE status IN ('NEW','ACKNOWLEDGED','APPROVED')").fetchall()
            by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM autonomy_recommendations GROUP BY status")}
        day_ago = _ts(now - timedelta(hours=24))
        count = lambda f: sum(1 for r in open_rows if f(r))
        cats: Dict[str, int] = {}
        conf: Dict[str, int] = {}
        for r in open_rows:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
            conf[r["confidence_label"]] = conf.get(r["confidence_label"], 0) + 1
        return {"active": len(open_rows), "critical": count(lambda r: r["priority_label"] == "CRITICAL"),
                "high": count(lambda r: r["priority_label"] == "HIGH"), "new_last_24h": count(lambda r: r["created_at"] >= day_ago),
                "by_category": cats, "by_confidence": conf, "by_status": by_status}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
