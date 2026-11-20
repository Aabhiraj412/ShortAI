"""Community post persistence: drafts, generation runs, images (provenance), audit events, notifications, config.

Only ``community_*`` tables are written. Production tables are read elsewhere, read-only, and never written here.

Guarantees enforced by the database, not only by code:
* ``community_runs.idem_key`` is UNIQUE: one scheduled run per local date (``community:scheduled:<date>``).
* a partial UNIQUE index allows at most ONE active draft per ``post_date`` (active = not failed, rejected,
  archived or cancelled), so overlapping manual and scheduled generation cannot both produce a draft.
* ``community_events`` is append-only (an UPDATE trigger aborts); rows are only removed by retention.
* every state change is a compare-and-set on (status, version) inside ``BEGIN IMMEDIATE``.

Reads never create tables (``tables_exist`` is checked read-only), so opening the dashboard with Stage 3.32E
never configured changes nothing.
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

# -- draft lifecycle ----------------------------------------------------------------------------------------------
GENERATING, IMAGE_PENDING, VALIDATING = "GENERATING", "IMAGE_PENDING", "VALIDATING"
READY_FOR_REVIEW, APPROVED, REJECTED, ARCHIVED = "READY_FOR_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"
GENERATION_FAILED, IMAGE_FAILED, VALIDATION_FAILED, CANCELLED = (
    "GENERATION_FAILED", "IMAGE_FAILED", "VALIDATION_FAILED", "CANCELLED")
STATUSES = (GENERATING, IMAGE_PENDING, VALIDATING, READY_FOR_REVIEW, APPROVED, REJECTED, ARCHIVED,
            GENERATION_FAILED, IMAGE_FAILED, VALIDATION_FAILED, CANCELLED)
IN_PROGRESS = (GENERATING, IMAGE_PENDING, VALIDATING)
FAILED_STATES = (GENERATION_FAILED, IMAGE_FAILED, VALIDATION_FAILED)
# Active drafts occupy their post_date: a new draft for that date is refused while one of these exists.
ACTIVE = IN_PROGRESS + (READY_FOR_REVIEW, APPROVED)

# Allowed transitions. Regeneration and edits re-enter GENERATING / VALIDATING from a reviewable or failed state.
TRANSITIONS: Dict[str, Sequence[str]] = {
    GENERATING: (IMAGE_PENDING, GENERATION_FAILED, CANCELLED),
    IMAGE_PENDING: (VALIDATING, IMAGE_FAILED, CANCELLED),
    VALIDATING: (READY_FOR_REVIEW, VALIDATION_FAILED, CANCELLED),
    READY_FOR_REVIEW: (APPROVED, REJECTED, ARCHIVED, GENERATING, IMAGE_PENDING, VALIDATING),
    APPROVED: (ARCHIVED, VALIDATING, GENERATING, IMAGE_PENDING),     # any change withdraws the approval
    REJECTED: (ARCHIVED,),
    GENERATION_FAILED: (GENERATING, ARCHIVED),
    IMAGE_FAILED: (IMAGE_PENDING, GENERATING, ARCHIVED),
    VALIDATION_FAILED: (GENERATING, IMAGE_PENDING, VALIDATING, ARCHIVED),
    ARCHIVED: (),
    CANCELLED: (),
}

# -- validation outcome -------------------------------------------------------------------------------------------
VALID, VALID_WITH_WARNINGS = "VALID", "VALID_WITH_WARNINGS"
V_REJECTED, NEEDS_OPERATOR_REVIEW = "REJECTED", "NEEDS_OPERATOR_REVIEW"
VALIDATION_STATUSES = (VALID, VALID_WITH_WARNINGS, V_REJECTED, NEEDS_OPERATOR_REVIEW)

# -- runs -----------------------------------------------------------------------------------------------------------
RUN_RUNNING, RUN_SUCCEEDED, RUN_FAILED, RUN_SKIPPED, RUN_ABANDONED = (
    "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "ABANDONED")
RUN_STATUSES = (RUN_RUNNING, RUN_SUCCEEDED, RUN_FAILED, RUN_SKIPPED, RUN_ABANDONED)

RETENTION_DAYS_DRAFTS = 365
RETENTION_DAYS_RUNS = 180
RETENTION_DAYS_EVENTS = 365
RETENTION_DAYS_NOTIFICATIONS = 90
MAX_DRAFTS = 5000
MAX_RUNS = 5000
MAX_EVENTS = 50000
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS community_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS community_runs (
    id TEXT PRIMARY KEY,
    idem_key TEXT NOT NULL UNIQUE,
    post_date TEXT NOT NULL,
    trigger TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    lease_expires_utc TEXT,
    draft_id TEXT,
    error TEXT,
    metrics_json TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_community_runs_date ON community_runs (post_date, status);
CREATE INDEX IF NOT EXISTS idx_community_runs_started ON community_runs (started_at_utc);
CREATE TABLE IF NOT EXISTS community_drafts (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    post_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    format TEXT,
    topic_key TEXT,
    topic_title TEXT,
    category TEXT,
    source_kind TEXT,
    source_ref TEXT,
    source_video_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    text_fingerprint TEXT,
    opening_key TEXT,
    question_key TEXT,
    cta_key TEXT,
    image_id TEXT,
    validation_status TEXT,
    validation_json TEXT,
    generation_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status_reason TEXT,
    decided_by TEXT,
    decided_at_utc TEXT,
    decision_reason TEXT,
    warnings_acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_community_active_date ON community_drafts (post_date) WHERE active = 1;
CREATE INDEX IF NOT EXISTS idx_community_drafts_status ON community_drafts (status, updated_at_utc);
CREATE INDEX IF NOT EXISTS idx_community_drafts_topic ON community_drafts (topic_key, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_community_drafts_created ON community_drafts (created_at_utc);
CREATE TABLE IF NOT EXISTS community_images (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    dhash TEXT,
    width INTEGER,
    height INTEGER,
    bytes INTEGER,
    mime TEXT,
    method TEXT NOT NULL,
    source_kind TEXT,
    source_ref TEXT,
    licence TEXT,
    attribution TEXT,
    provenance_json TEXT NOT NULL,
    validation_json TEXT,
    current INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_images_draft ON community_images (draft_id, current);
CREATE INDEX IF NOT EXISTS idx_community_images_sha ON community_images (sha256);
CREATE TABLE IF NOT EXISTS community_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT,
    run_id TEXT,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail_json TEXT,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_events_draft ON community_events (draft_id, id);
CREATE INDEX IF NOT EXISTS idx_community_events_created ON community_events (created_at_utc);
CREATE TRIGGER IF NOT EXISTS community_events_no_update BEFORE UPDATE ON community_events
BEGIN SELECT RAISE(ABORT, 'community_events is append-only'); END;
CREATE TABLE IF NOT EXISTS community_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    severity TEXT NOT NULL,
    draft_id TEXT,
    delivery TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_notif_created ON community_notifications (created_at_utc);
"""

TABLES = ("community_config", "community_runs", "community_drafts", "community_images", "community_events",
          "community_notifications")
JSON_FIELDS = ("validation_json", "generation_json", "metrics_json", "provenance_json", "detail_json", "value_json")


class StoreError(Exception):
    """Base error. ``code`` is the HTTP status the API maps it to."""
    code = 500


class NotFound(StoreError):
    code = 404


class Conflict(StoreError):
    code = 409


class InvalidTransition(Conflict):
    pass


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS:
        if k in d and d[k] is not None:
            try:
                d[k[:-5] if k.endswith("_json") else k] = json.loads(d.pop(k))
            except ValueError:
                d[k[:-5]] = None
    return d


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


class CommunityStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._ready = False
        self._lock = threading.Lock()

    # -- connections ----------------------------------------------------------------------------------------
    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")      # never create a database file
        uri = Path(self.db_path).resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        if readonly:
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
        conn = self._connect(readonly=True)
        try:
            yield conn
        finally:
            conn.close()

    def tables_exist(self) -> bool:
        """True once Stage 3.32E has written here. Checked read-only, so reads never create tables."""
        if not os.path.isfile(self.db_path):
            return False
        try:
            with self.read() as conn:
                n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (%s)"
                                 % ",".join("?" * len(TABLES)), TABLES).fetchone()[0]
            return n == len(TABLES)
        except sqlite3.Error:
            return False

    def ensure_schema(self) -> None:
        with self.tx():
            pass

    # -- config -------------------------------------------------------------------------------------------
    def get_config(self) -> Dict[str, Any]:
        if not self.tables_exist():
            return {}
        with self.read() as conn:
            return {r["key"]: json.loads(r["value_json"]) for r in conn.execute("SELECT key, value_json FROM community_config")}

    def set_config(self, conn: sqlite3.Connection, values: Dict[str, Any], actor: str, now: datetime) -> None:
        for k, v in values.items():
            conn.execute("INSERT INTO community_config (key, value_json, updated_at_utc, updated_by) VALUES (?,?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                         "updated_at_utc=excluded.updated_at_utc, updated_by=excluded.updated_by",
                         (k, _dumps(v), ts(now), actor))

    # -- audit ----------------------------------------------------------------------------------------------
    def event(self, conn: sqlite3.Connection, event: str, actor: str, now: datetime, *, draft_id: Optional[str] = None,
              run_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        conn.execute("INSERT INTO community_events (draft_id, run_id, actor, event, detail_json, created_at_utc) "
                     "VALUES (?,?,?,?,?,?)", (draft_id, run_id, actor[:80], event[:60], _dumps(detail or {}), ts(now)))

    def events(self, draft_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.tables_exist():
            return []
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if draft_id:
                rows = conn.execute("SELECT * FROM community_events WHERE draft_id=? ORDER BY id DESC LIMIT ?",
                                    (draft_id, limit))
            else:
                rows = conn.execute("SELECT * FROM community_events ORDER BY id DESC LIMIT ?", (limit,))
            return [_row(r) for r in rows]

    # -- runs -----------------------------------------------------------------------------------------------
    def get_run_by_key(self, conn: sqlite3.Connection, idem_key: str) -> Optional[Dict[str, Any]]:
        return _row(conn.execute("SELECT * FROM community_runs WHERE idem_key=?", (idem_key,)).fetchone())

    def running_run_for_date(self, conn: sqlite3.Connection, post_date: str, now: datetime) -> Optional[Dict[str, Any]]:
        return _row(conn.execute("SELECT * FROM community_runs WHERE post_date=? AND status=? AND lease_expires_utc > ? "
                                 "ORDER BY started_at_utc DESC LIMIT 1", (post_date, RUN_RUNNING, ts(now))).fetchone())

    def insert_run(self, conn: sqlite3.Connection, *, idem_key: str, post_date: str, trigger: str, requested_by: str,
                   now: datetime, lease_seconds: int) -> Dict[str, Any]:
        rid = new_id("crun")
        conn.execute("INSERT INTO community_runs (id, idem_key, post_date, trigger, requested_by, status, attempts, "
                     "lease_expires_utc, started_at_utc) VALUES (?,?,?,?,?,?,1,?,?)",
                     (rid, idem_key, post_date, trigger, requested_by, RUN_RUNNING,
                      ts(now + timedelta(seconds=lease_seconds)), ts(now)))
        return self.get_run(rid, conn)

    def restart_run(self, conn: sqlite3.Connection, run_id: str, expect_status: str, now: datetime,
                    lease_seconds: int) -> bool:
        cur = conn.execute("UPDATE community_runs SET status=?, attempts=attempts+1, lease_expires_utc=?, error=NULL, "
                           "started_at_utc=?, finished_at_utc=NULL, draft_id=NULL WHERE id=? AND status=?",
                           (RUN_RUNNING, ts(now + timedelta(seconds=lease_seconds)), ts(now), run_id, expect_status))
        return cur.rowcount == 1

    def finish_run(self, conn: sqlite3.Connection, run_id: str, status: str, now: datetime, *,
                   draft_id: Optional[str] = None, error: Optional[str] = None,
                   metrics: Optional[Dict[str, Any]] = None) -> None:
        conn.execute("UPDATE community_runs SET status=?, finished_at_utc=?, lease_expires_utc=NULL, "
                     "draft_id=COALESCE(?, draft_id), error=?, metrics_json=? WHERE id=? AND status=?",
                     (status, ts(now), draft_id, (error or None) and error[:500], _dumps(metrics or {}), run_id, RUN_RUNNING))

    def get_run(self, run_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        if conn is not None:
            return _row(conn.execute("SELECT * FROM community_runs WHERE id=?", (run_id,)).fetchone())
        if not self.tables_exist():
            return None
        with self.read() as c:
            return _row(c.execute("SELECT * FROM community_runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.tables_exist():
            return []
        limit = max(1, min(int(limit), 500))
        with self.read() as conn:
            if status:
                rows = conn.execute("SELECT * FROM community_runs WHERE status=? ORDER BY started_at_utc DESC LIMIT ?",
                                    (status, limit))
            else:
                rows = conn.execute("SELECT * FROM community_runs ORDER BY started_at_utc DESC LIMIT ?", (limit,))
            return [_row(r) for r in rows]

    def count_runs_since(self, conn: sqlite3.Connection, since: datetime, trigger: Optional[str] = None) -> int:
        if trigger:
            return conn.execute("SELECT COUNT(*) FROM community_runs WHERE started_at_utc >= ? AND trigger=?",
                                (ts(since), trigger)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM community_runs WHERE started_at_utc >= ?", (ts(since),)).fetchone()[0]

    def expired_leases(self, conn: sqlite3.Connection, now: datetime) -> List[Dict[str, Any]]:
        return [_row(r) for r in conn.execute("SELECT * FROM community_runs WHERE status=? AND lease_expires_utc <= ? LIMIT 50",
                                              (RUN_RUNNING, ts(now)))]

    # -- drafts ---------------------------------------------------------------------------------------------
    def active_draft_for_date(self, conn: sqlite3.Connection, post_date: str) -> Optional[Dict[str, Any]]:
        return _row(conn.execute("SELECT * FROM community_drafts WHERE post_date=? AND active=1", (post_date,)).fetchone())

    def insert_draft(self, conn: sqlite3.Connection, *, run_id: Optional[str], post_date: str, now: datetime) -> Dict[str, Any]:
        did = new_id("cdraft")
        try:
            conn.execute("INSERT INTO community_drafts (id, run_id, post_date, active, status, created_at_utc, updated_at_utc) "
                         "VALUES (?,?,?,1,?,?,?)", (did, run_id, post_date, GENERATING, ts(now), ts(now)))
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"an active draft already exists for {post_date}") from exc
        return self.get_draft(did, conn)

    def get_draft(self, draft_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        if conn is not None:
            return _row(conn.execute("SELECT * FROM community_drafts WHERE id=?", (draft_id,)).fetchone())
        if not self.tables_exist():
            return None
        with self.read() as c:
            return _row(c.execute("SELECT * FROM community_drafts WHERE id=?", (draft_id,)).fetchone())

    def transition(self, conn: sqlite3.Connection, draft_id: str, new_status: str, now: datetime, *,
                   expect_status: Optional[Sequence[str]] = None, expect_version: Optional[int] = None,
                   **fields: Any) -> Dict[str, Any]:
        """Atomic compare-and-set. Refuses transitions not in TRANSITIONS and stale versions."""
        cur = self.get_draft(draft_id, conn)
        if cur is None:
            raise NotFound("draft not found")
        if expect_version is not None and int(cur["version"]) != int(expect_version):
            raise Conflict(f"draft changed since it was loaded (version {cur['version']}, expected {expect_version})")
        if expect_status is not None and cur["status"] not in expect_status:
            raise InvalidTransition(f"draft is {cur['status']}; this action needs {', '.join(expect_status)}")
        if new_status != cur["status"] and new_status not in TRANSITIONS.get(cur["status"], ()):
            raise InvalidTransition(f"cannot move a draft from {cur['status']} to {new_status}")
        sets = {"status": new_status, "active": 1 if new_status in ACTIVE else 0, "updated_at_utc": ts(now)}
        for k, v in fields.items():
            sets[k + "_json" if k in ("validation", "generation") else k] = _dumps(v) if k in ("validation", "generation") else v
        sql = ", ".join(f"{k}=?" for k in sets) + ", version=version+1"
        try:
            n = conn.execute(f"UPDATE community_drafts SET {sql} WHERE id=? AND status=? AND version=?",
                             (*sets.values(), draft_id, cur["status"], cur["version"])).rowcount
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"an active draft already exists for {cur['post_date']}") from exc
        if n != 1:
            raise Conflict("draft changed concurrently; reload and try again")
        return self.get_draft(draft_id, conn)

    def list_drafts(self, *, status: Optional[str] = None, query: Optional[str] = None, fmt: Optional[str] = None,
                    validation: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if not self.tables_exist():
            return []
        where, args = [], []
        if status == "active":
            where.append("active=1")
        elif status == "failed":
            where.append("status IN (%s)" % ",".join("?" * len(FAILED_STATES)))
            args += list(FAILED_STATES)
        elif status and status != "all":
            where.append("status=?")
            args.append(status)
        if fmt:
            where.append("format=?")
            args.append(fmt)
        if validation:
            where.append("validation_status=?")
            args.append(validation)
        if date_from:
            where.append("post_date >= ?")
            args.append(date_from)
        if date_to:
            where.append("post_date <= ?")
            args.append(date_to)
        if query:
            where.append("(text LIKE ? ESCAPE '\\' OR topic_title LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\')")
            q = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [q, q, q]
        sql = "SELECT * FROM community_drafts" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY post_date DESC, created_at_utc DESC LIMIT ? OFFSET ?"
        args += [max(1, min(int(limit), 200)), max(0, min(int(offset), 100000))]
        with self.read() as conn:
            return [_row(r) for r in conn.execute(sql, args)]

    def recent_drafts(self, conn: sqlite3.Connection, since: datetime, limit: int = 400) -> List[Dict[str, Any]]:
        """Drafts that count for duplicate and rotation checks (cancelled and failed drafts never published text)."""
        return [_row(r) for r in conn.execute(
            "SELECT * FROM community_drafts WHERE created_at_utc >= ? AND status NOT IN (?,?,?,?) "
            "ORDER BY created_at_utc DESC LIMIT ?",
            (ts(since), CANCELLED, GENERATION_FAILED, IMAGE_FAILED, VALIDATION_FAILED, limit))]

    def stats(self) -> Dict[str, Any]:
        if not self.tables_exist():
            return {"by_status": {}, "by_validation": {}, "runs_by_status": {}}
        with self.read() as conn:
            by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM community_drafts GROUP BY status")}
            by_val = {r[0]: r[1] for r in conn.execute(
                "SELECT validation_status, COUNT(*) FROM community_drafts WHERE active=1 AND validation_status IS NOT NULL "
                "GROUP BY validation_status")}
            runs = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM community_runs GROUP BY status")}
        return {"by_status": by_status, "by_validation": by_val, "runs_by_status": runs}

    # -- images ---------------------------------------------------------------------------------------------
    def insert_image(self, conn: sqlite3.Connection, record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        conn.execute("UPDATE community_images SET current=0 WHERE draft_id=? AND current=1", (record["draft_id"],))
        conn.execute("INSERT INTO community_images (id, draft_id, filename, sha256, dhash, width, height, bytes, mime, "
                     "method, source_kind, source_ref, licence, attribution, provenance_json, validation_json, current, "
                     "created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                     (record["id"], record["draft_id"], record["filename"], record["sha256"], record.get("dhash"),
                      record.get("width"), record.get("height"), record.get("bytes"), record.get("mime"), record["method"],
                      record.get("source_kind"), record.get("source_ref"), record.get("licence"), record.get("attribution"),
                      _dumps(record.get("provenance") or {}), _dumps(record.get("validation") or {}), ts(now)))
        return self.get_image(record["id"], conn)

    def get_image(self, image_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        if conn is not None:
            return _row(conn.execute("SELECT * FROM community_images WHERE id=?", (image_id,)).fetchone())
        if not self.tables_exist():
            return None
        with self.read() as c:
            return _row(c.execute("SELECT * FROM community_images WHERE id=?", (image_id,)).fetchone())

    def update_image_validation(self, conn: sqlite3.Connection, image_id: str, validation: Dict[str, Any]) -> None:
        conn.execute("UPDATE community_images SET validation_json=? WHERE id=?", (_dumps(validation), image_id))

    def recent_image_hashes(self, conn: sqlite3.Connection, since: datetime, exclude_draft: Optional[str] = None,
                            limit: int = 1000) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT i.id, i.draft_id, i.sha256, i.dhash, i.source_ref FROM community_images i "
                            "JOIN community_drafts d ON d.id = i.draft_id WHERE i.created_at_utc >= ? AND i.current=1 "
                            "AND d.status NOT IN (?,?) AND i.draft_id != ? ORDER BY i.created_at_utc DESC LIMIT ?",
                            (ts(since), CANCELLED, ARCHIVED, exclude_draft or "", limit))
        return [dict(r) for r in rows]

    def images_for_draft(self, draft_id: str) -> List[Dict[str, Any]]:
        if not self.tables_exist():
            return []
        with self.read() as conn:
            return [_row(r) for r in conn.execute("SELECT * FROM community_images WHERE draft_id=? "
                                                  "ORDER BY created_at_utc DESC LIMIT 50", (draft_id,))]

    # -- notifications ----------------------------------------------------------------------------------------
    def add_notification(self, conn: sqlite3.Connection, kind: str, subject: str, message: str, severity: str,
                         draft_id: Optional[str], delivery: str, now: datetime) -> None:
        conn.execute("INSERT INTO community_notifications (kind, subject, message, severity, draft_id, delivery, "
                     "created_at_utc) VALUES (?,?,?,?,?,?,?)",
                     (kind, subject[:160], message[:1000], severity, draft_id, delivery, ts(now)))

    def notifications(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.tables_exist():
            return []
        with self.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM community_notifications ORDER BY id DESC LIMIT ?",
                                                  (max(1, min(int(limit), 200)),))]

    # -- retention --------------------------------------------------------------------------------------------
    def prune(self, now: datetime) -> Dict[str, Any]:
        """Bounded history. Removes only finished (inactive) drafts and their image records; returns the image
        files to delete so the caller can remove them from its own folder."""
        out: Dict[str, Any] = {"drafts": 0, "runs": 0, "events": 0, "notifications": 0, "image_files": []}
        if not self.tables_exist():
            return out
        with self.tx() as conn:
            cutoff = ts(now - timedelta(days=RETENTION_DAYS_DRAFTS))
            old = [r[0] for r in conn.execute("SELECT id FROM community_drafts WHERE active=0 AND updated_at_utc < ? "
                                              "LIMIT 1000", (cutoff,))]
            excess = conn.execute("SELECT COUNT(*) FROM community_drafts").fetchone()[0] - MAX_DRAFTS
            if excess > 0:
                old += [r[0] for r in conn.execute("SELECT id FROM community_drafts WHERE active=0 ORDER BY updated_at_utc "
                                                   "LIMIT ?", (min(excess, 1000),))]
            for did in sorted(set(old)):
                out["image_files"] += [(r[0], r[1]) for r in conn.execute(
                    "SELECT draft_id, filename FROM community_images WHERE draft_id=?", (did,))]
                conn.execute("DELETE FROM community_images WHERE draft_id=?", (did,))
                out["drafts"] += conn.execute("DELETE FROM community_drafts WHERE id=?", (did,)).rowcount
            out["runs"] = conn.execute("DELETE FROM community_runs WHERE status != ? AND started_at_utc < ?",
                                       (RUN_RUNNING, ts(now - timedelta(days=RETENTION_DAYS_RUNS)))).rowcount
            out["events"] = conn.execute("DELETE FROM community_events WHERE created_at_utc < ?",
                                         (ts(now - timedelta(days=RETENTION_DAYS_EVENTS)),)).rowcount
            over = conn.execute("SELECT COUNT(*) FROM community_events").fetchone()[0] - MAX_EVENTS
            if over > 0:
                out["events"] += conn.execute("DELETE FROM community_events WHERE id IN (SELECT id FROM community_events "
                                              "ORDER BY id LIMIT ?)", (over,)).rowcount
            out["notifications"] = conn.execute("DELETE FROM community_notifications WHERE created_at_utc < ?",
                                                (ts(now - timedelta(days=RETENTION_DAYS_NOTIFICATIONS)),)).rowcount
        return out
