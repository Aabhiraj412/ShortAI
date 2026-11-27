"""Stage 4.01: persistent channel registry with verified identities and isolated credentials.

Channels
--------
* ``en``: English, the **master** channel. It owns the existing production: the 24-cycle schedule, topic discovery,
  the publishing queue (``publishing_ledger``), analytics (``video_performance``), and the existing OAuth token
  (``backend/token.json``). Nothing about it changes here: the registry describes it and verifies its identity
  from the status the dashboard already caches, so no extra API call or quota is spent.
* ``hi``: Hindi, a **localized** channel. It never discovers or consumes topics. Its content can only be a
  localization of a completed English Short (``content_localizations``: one row per source job and channel).
  It has its **own** token file (``backend/channel_tokens/hi/token.json``), its own OAuth manager (which never
  writes the English connection history) and its own state. It stays ``PENDING_VERIFICATION`` until that token
  exists AND ``channels.list?mine=true`` returns a channel that is not the English one.

Rules
-----
* Identity comes only from ``channels.list?mine=true`` (the channel id), never from a display name.
* No secret is stored here: ``credential_ref`` is a path relative to the backend directory.
* The English token is never read for Hindi, overwritten or revoked. If the Hindi authorization turns out to be the
  English channel, Hindi is BLOCKED with ``IDENTITY_CONFLICT`` (its own token file is left for the operator).
* If the English channel id ever changes, English is BLOCKED (``IDENTITY_CHANGED``); the master is never switched.
* Only a verified channel can be activated. The master cannot be deactivated here (that would be a production
  change; the operating mode governs English production).
* Every registration, verification and status change is recorded in ``channel_events``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ACTIVE, INACTIVE = "ACTIVE", "INACTIVE"
PENDING_VERIFICATION, VERIFIED, BLOCKED, AUTH_REQUIRED = "PENDING_VERIFICATION", "VERIFIED", "BLOCKED", "AUTH_REQUIRED"
MASTER, LOCALIZED = "MASTER", "LOCALIZED"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
HINDI_REVERIFY = timedelta(hours=6)          # one channels.list per 6 h at most for a non-master channel

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_registry (
    channel_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    role TEXT NOT NULL,
    source_channel_key TEXT,
    status TEXT NOT NULL,
    status_reason TEXT NOT NULL DEFAULT '',
    youtube_channel_id TEXT,
    verified_title TEXT,
    verified_at_utc TEXT,
    credential_ref TEXT NOT NULL,
    publishing_config_json TEXT NOT NULL DEFAULT '{}',
    analytics_config_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS content_localizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_channel_key TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_topic_title TEXT,
    source_youtube_video_id TEXT,
    target_channel_key TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    localized_artifact_ref TEXT,
    target_youtube_video_id TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (source_job_id, target_channel_key)
);
"""

LOCALIZATION_COLUMNS = (
    ("step", "TEXT"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("error_kind", "TEXT"), ("error", "TEXT"),
    ("artifacts_json", "TEXT NOT NULL DEFAULT '{}'"), ("qc_json", "TEXT"), ("owner_token", "TEXT"),
    ("owner_pid", "INTEGER"), ("version", "INTEGER NOT NULL DEFAULT 0"),
)

DEFAULT_CHANNELS = (
    {"channel_key": "en", "name": "CrazzyDocs (English)", "language": "en", "role": MASTER, "source_channel_key": None,
     "status": ACTIVE, "status_reason": "master channel; existing production",
     "credential_ref": "token.json",
     "publishing": {"queue": "publishing_ledger", "privacy": "private", "policy": "existing (automatic_private_scheduled)"},
     "analytics": {"enabled": True, "source": "youtube_analytics (video_performance)"}},
    {"channel_key": "hi", "name": "CrazzyDocs (Hindi)", "language": "hi", "role": LOCALIZED, "source_channel_key": "en",
     "status": PENDING_VERIFICATION,
     "status_reason": "Hindi channel not authorized yet: connect it with the Hindi channel's Google account",
     "credential_ref": "channel_tokens/hi/token.json",
     "publishing": {"queue": "content_localizations", "privacy": "private", "policy": "none until verified and active"},
     "analytics": {"enabled": False, "source": "youtube_analytics (after verification)"}},
)


class ChannelError(Exception):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _backend_dir() -> Path:
    from backend.app.core.paths import BACKEND_DIR
    return Path(BACKEND_DIR)


def _default_oauth_factory(token_path: str, is_master: bool):
    from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
    return YouTubeOAuthManager(token_file=token_path, record_connection_events=is_master)


class ChannelRegistry:
    def __init__(self, db_path: Optional[str] = None, *, backend_dir: Optional[Path] = None,
                 oauth_factory: Callable[[str, bool], Any] = _default_oauth_factory,
                 clock: Callable[[], datetime] = _now):
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        self.db_path = str(db_path)
        self.backend_dir = Path(backend_dir) if backend_dir else _backend_dir()
        self.oauth_factory = oauth_factory
        self.clock = clock
        self._ensured = False
        self._lock = threading.Lock()

    # -- storage ------------------------------------------------------------------------------------------------
    def _connect(self, write: bool = True) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
                               uri=True, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def ensure(self) -> None:
        """Creates the tables and registers the two channels once (idempotent; never changes an existing row)."""
        if self._ensured:
            return
        with self._lock:
            conn = self._connect(True)
            try:
                conn.executescript(SCHEMA)
                # Stage 4.02: additive step tracking on the localization map (resumable, no duplicated work)
                have = {r[1] for r in conn.execute("PRAGMA table_info(content_localizations)")}
                for col, ddl in LOCALIZATION_COLUMNS:
                    if col not in have:
                        conn.execute(f"ALTER TABLE content_localizations ADD COLUMN {col} {ddl}")
                now = _ts(self.clock())
                for ch in DEFAULT_CHANNELS:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO channel_registry (channel_key, name, language, role, source_channel_key, "
                        "status, status_reason, credential_ref, publishing_config_json, analytics_config_json, "
                        "created_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ch["channel_key"], ch["name"], ch["language"], ch["role"], ch["source_channel_key"],
                         ch["status"], ch["status_reason"], ch["credential_ref"], json.dumps(ch["publishing"]),
                         json.dumps(ch["analytics"]), now, now))
                    if cur.rowcount:
                        self._event(conn, ch["channel_key"], "REGISTERED", "system", None, ch["status"],
                                    {"role": ch["role"], "language": ch["language"]})
            finally:
                conn.close()
            self._ensured = True

    def _event(self, conn, key: str, event: str, actor: str, prev: Optional[str], new: Optional[str],
               detail: Any = None) -> None:
        conn.execute("INSERT INTO channel_events (ts_utc, channel_key, event, actor, previous_status, new_status, detail) "
                     "VALUES (?,?,?,?,?,?,?)", (_ts(self.clock()), key, event, actor, prev, new,
                                                json.dumps(detail, default=str)[:2000] if detail is not None else None))

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["publishing_config"] = json.loads(d.pop("publishing_config_json") or "{}")
        d["analytics_config"] = json.loads(d.pop("analytics_config_json") or "{}")
        return d

    def get(self, key: str) -> Dict[str, Any]:
        self.ensure()
        conn = self._connect(False)
        try:
            r = conn.execute("SELECT * FROM channel_registry WHERE channel_key=?", (key,)).fetchone()
        finally:
            conn.close()
        if r is None:
            raise ChannelError(f"unknown channel '{key}'", 404)
        return self._row(r)

    def list(self) -> List[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            rows = conn.execute("SELECT * FROM channel_registry ORDER BY CASE role WHEN 'MASTER' THEN 0 ELSE 1 END, "
                                "channel_key").fetchall()
        finally:
            conn.close()
        return [self._row(r) for r in rows]

    def events(self, key: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            if key:
                rows = conn.execute("SELECT * FROM channel_events WHERE channel_key=? ORDER BY id DESC LIMIT ?",
                                    (key, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM channel_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _set(self, key: str, actor: str, event: str, detail: Any = None, **values: Any) -> Dict[str, Any]:
        cur_row = self.get(key)
        cols = ", ".join(f"{k}=?" for k in values)
        conn = self._connect(True)
        try:
            conn.execute(f"UPDATE channel_registry SET {cols}, updated_at_utc=? WHERE channel_key=?",
                         (*values.values(), _ts(self.clock()), key))
            self._event(conn, key, event, actor, cur_row["status"], values.get("status", cur_row["status"]), detail)
        finally:
            conn.close()
        return self.get(key)

    # -- credentials (paths only; isolation checks) --------------------------------------------------------------
    def credential_path(self, ch: Dict[str, Any]) -> Path:
        p = (self.backend_dir / ch["credential_ref"]).resolve()
        if self.backend_dir.resolve() not in p.parents:
            raise ChannelError("credential reference escapes the backend directory", 400)
        return p

    def _isolation_problem(self, ch: Dict[str, Any]) -> Optional[str]:
        """A non-master channel must never point at the master's credential file."""
        if ch["role"] == MASTER:
            return None
        master = next((c for c in self.list() if c["role"] == MASTER), None)
        if master and self.credential_path(ch) == self.credential_path(master):
            return "credential reference is the master channel's token file"
        return None

    def oauth_manager(self, key: str):
        ch = self.get(key)
        problem = self._isolation_problem(ch)
        if problem:
            raise ChannelError(problem, 409)
        return self.oauth_factory(str(self.credential_path(ch)), ch["role"] == MASTER)

    # -- verification --------------------------------------------------------------------------------------------
    def verify(self, key: str, *, actor: str = "system", force: bool = False) -> Dict[str, Any]:
        """Verifies the channel identity from channels.list?mine=true (via the OAuth manager's status). Never
        assumes an identity: without a token there is no identity, and the display name is informational only."""
        ch = self.get(key)
        problem = self._isolation_problem(ch)
        if problem:
            return self._set(key, actor, "VERIFICATION_REFUSED", {"reason": problem}, status=BLOCKED,
                             status_reason=f"IDENTITY_CONFLICT: {problem}")
        token = self.credential_path(ch)
        if not token.exists():
            if ch["role"] == MASTER:
                return self._set(key, actor, "VERIFICATION_FAILED", {"reason": "token missing"}, status=AUTH_REQUIRED,
                                 status_reason="English authorization missing: reconnect the English channel")
            if ch["status"] == PENDING_VERIFICATION:
                return ch
            return self._set(key, actor, "VERIFICATION_PENDING", {"reason": "token missing"},
                             status=PENDING_VERIFICATION,
                             status_reason="Hindi channel not authorized yet: connect it with the Hindi channel's "
                                           "Google account")
        if (ch["role"] != MASTER and not force and ch.get("verified_at_utc")
                and self.clock() - datetime.strptime(ch["verified_at_utc"], TS_FMT).replace(tzinfo=timezone.utc)
                < HINDI_REVERIFY and ch["status"] in (VERIFIED, ACTIVE)):
            return ch
        mgr = self.oauth_factory(str(token), ch["role"] == MASTER)
        status = (mgr.get_auth_status_cached() if ch["role"] == MASTER else mgr.get_auth_status()) or {}
        info = status.get("channel_info") or {}
        cid, title = info.get("id"), info.get("title")
        if not status.get("is_authenticated") and not cid:
            return self._set(key, actor, "VERIFICATION_FAILED", {"auth_problem": status.get("auth_problem")},
                             status=AUTH_REQUIRED,
                             status_reason=f"authorization not usable ({status.get('auth_problem') or 'not authenticated'}): "
                                           "reconnect this channel")
        if not cid:
            return self._set(key, actor, "VERIFICATION_FAILED", {"error": status.get("error_detail")},
                             status=AUTH_REQUIRED, status_reason="channels.list?mine=true returned no channel")
        if ch["role"] == MASTER:
            if ch.get("youtube_channel_id") and ch["youtube_channel_id"] != cid:
                return self._set(key, actor, "IDENTITY_CHANGED", {"stored": ch["youtube_channel_id"], "now": cid},
                                 status=BLOCKED, status_reason="IDENTITY_CHANGED: the English token now belongs to a "
                                                               "different channel; reconnect the English channel")
            new_status = ACTIVE if ch["status"] in (ACTIVE, AUTH_REQUIRED, PENDING_VERIFICATION) else ch["status"]
            if ch.get("youtube_channel_id") == cid and ch["status"] == new_status and ch.get("verified_title") == title:
                return ch                                  # unchanged: no write, no repeated history event
            return self._set(key, actor, "VERIFIED", {"channel_id": cid}, status=new_status,
                             status_reason="verified with channels.list?mine=true", youtube_channel_id=cid,
                             verified_title=title, verified_at_utc=_ts(self.clock()))
        master = next((c for c in self.list() if c["role"] == MASTER), None)
        if master and master.get("youtube_channel_id") and master["youtube_channel_id"] == cid:
            return self._set(key, actor, "IDENTITY_CONFLICT", {"channel_id": cid}, status=BLOCKED,
                             status_reason="IDENTITY_CONFLICT: this authorization is the English channel. Reconnect "
                                           "with the Hindi channel's Google account (the English token is untouched)",
                             youtube_channel_id=None, verified_title=None, verified_at_utc=None)
        if master and not master.get("youtube_channel_id"):
            return self._set(key, actor, "VERIFICATION_DEFERRED", {"channel_id": cid}, status=PENDING_VERIFICATION,
                             status_reason="verify the English channel first, so the Hindi identity can be checked "
                                           "against it")
        new_status = ACTIVE if ch["status"] == ACTIVE else VERIFIED
        return self._set(key, actor, "VERIFIED", {"channel_id": cid}, status=new_status,
                         status_reason="verified with channels.list?mine=true" + ("" if new_status == ACTIVE else
                                                                                   "; activate it to use it"),
                         youtube_channel_id=cid, verified_title=title, verified_at_utc=_ts(self.clock()))

    def connect(self, key: str, *, actor: str, timeout_sec: int = 180) -> Dict[str, Any]:
        """Interactive Google consent for a NON-master channel, written only to that channel's own token file, then
        an identity check against the English channel. The English connection flow is unchanged and is used for
        English. One authorization flow at a time (shared with the English flow)."""
        ch = self.get(key)
        if ch["role"] == MASTER:
            raise ChannelError("the English channel uses the existing YouTube connection (Control Center)", 400)
        if self._isolation_problem(ch):
            raise ChannelError(self._isolation_problem(ch), 409)
        from backend.app.services import youtube_connection_service as ycs
        if not ycs._connect_lock.acquire(blocking=False):
            raise ChannelError("another YouTube authorization is already in progress", 409)
        try:
            token = self.credential_path(ch)
            token.parent.mkdir(parents=True, exist_ok=True)
            mgr = self.oauth_factory(str(token), False)
            ok, _info, error = mgr.start_interactive_auth(timeout_sec=timeout_sec, open_browser=True)
        finally:
            ycs._connect_lock.release()
        conn = self._connect(True)
        try:
            self._event(conn, key, "CONNECT_" + ("COMPLETED" if ok else "FAILED"), actor, ch["status"], ch["status"],
                        {"error": error} if error else None)
        finally:
            conn.close()
        if not ok:
            raise ChannelError(error or "authorization did not complete", 400)
        return self.verify(key, actor=actor, force=True)

    # -- activation ----------------------------------------------------------------------------------------------
    def activate(self, key: str, *, actor: str = "dashboard") -> Dict[str, Any]:
        """Activates an already VERIFIED channel (one confirmed action; no operator name or reason)."""
        ch = self.get(key)
        if ch["status"] == ACTIVE:
            return ch
        if ch["status"] != VERIFIED or not ch.get("youtube_channel_id"):
            raise ChannelError(f"only a verified channel can be activated (status {ch['status']}: {ch['status_reason']})")
        return self._set(key, actor, "ACTIVATED", None, status=ACTIVE,
                         status_reason="activated (identity verified with channels.list?mine=true)")

    def deactivate(self, key: str, *, actor: str = "dashboard") -> Dict[str, Any]:
        ch = self.get(key)
        if ch["role"] == MASTER:
            raise ChannelError("the English master channel cannot be deactivated here; English production is governed "
                               "by the operating mode", 400)
        if ch["status"] == INACTIVE:
            return ch
        verified = bool(ch.get("youtube_channel_id"))
        return self._set(key, actor, "DEACTIVATED", None, status=INACTIVE if verified else ch["status"],
                         status_reason="deactivated" if verified else ch["status_reason"])

    def is_active(self, key: str) -> bool:
        try:
            ch = self.get(key)
        except Exception:
            return False
        return ch["status"] == ACTIVE and bool(ch.get("youtube_channel_id"))

    # -- localization map (no discovery, no topic consumption) ---------------------------------------------------
    def plan_localization(self, source_job_id: str, target_key: str, *, actor: str = "system") -> Dict[str, Any]:
        """Links one completed English Short to a localized channel. Idempotent per (source job, channel); the
        localized content keeps the English content identity and never enters topic discovery or selection."""
        target = self.get(target_key)
        if target["role"] != LOCALIZED or not target.get("source_channel_key"):
            raise ChannelError(f"channel '{target_key}' is not a localized channel", 400)
        if target["status"] == BLOCKED:
            raise ChannelError(f"channel '{target_key}' is blocked: {target['status_reason']}")
        # Stage 4.02: local production (translation, voice, render, QC) is allowed before the channel is verified;
        # publishing eligibility is gated separately (verified + active + explicit authorization).
        conn = self._connect(True)
        try:
            job = conn.execute("SELECT id, status, topic_title, youtube_video_id FROM persistent_jobs WHERE id=?",
                               (source_job_id,)).fetchone()
            if job is None or job["status"] != "COMPLETED" or str(job["id"]).startswith(("test_", "mock_")):
                raise ChannelError("the source must be a completed English production", 400)
            from backend.app.content.topic_ledger import normalize_topic_title
            identity = normalize_topic_title(job["topic_title"] or source_job_id)
            now = _ts(self.clock())
            cur = conn.execute(
                "INSERT OR IGNORE INTO content_localizations (source_channel_key, source_job_id, source_identity, "
                "source_topic_title, source_youtube_video_id, target_channel_key, language, status, created_at_utc, "
                "updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (target["source_channel_key"], source_job_id, identity, job["topic_title"], job["youtube_video_id"],
                 target_key, target["language"], "PLANNED", now, now))
            if cur.rowcount:
                self._event(conn, target_key, "LOCALIZATION_PLANNED", actor, None, None,
                            {"source_job_id": source_job_id, "identity": identity})
            row = conn.execute("SELECT * FROM content_localizations WHERE source_job_id=? AND target_channel_key=?",
                               (source_job_id, target_key)).fetchone()
        finally:
            conn.close()
        return {"duplicate": cur.rowcount == 0, "localization": dict(row)}

    def publishing_eligibility(self, target_key: str, localization: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Stage 4.02: whether a localized video could be published. Never automatic; publishing itself is a later,
        separately authorized milestone. Every unmet condition is listed."""
        ch = self.get(target_key)
        reasons = []
        if ch["status"] != ACTIVE or not ch.get("youtube_channel_id"):
            reasons.append(f"channel not verified and active ({ch['status']}: {ch['status_reason']})")
        if not (ch.get("publishing_config") or {}).get("publishing_authorized"):
            reasons.append("Hindi publishing has not been authorized (separate milestone)")
        if localization is not None and localization.get("status") != "QC_PASSED":
            reasons.append(f"video has not passed Hindi QC (status {localization.get('status')})")
        return {"eligible": not reasons, "reasons": reasons, "automatic_publishing": False}

    def localizations(self, target_key: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            if target_key:
                rows = conn.execute("SELECT * FROM content_localizations WHERE target_channel_key=? ORDER BY id DESC "
                                    "LIMIT ?", (target_key, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM content_localizations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]


_registry: Optional[ChannelRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ChannelRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ChannelRegistry()
        return _registry
