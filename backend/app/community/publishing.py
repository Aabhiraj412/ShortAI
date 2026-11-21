"""Stage 3.32F: Community post publishing - capability audit, fail-closed API publishing, manual handoff.

Facts (official docs, 2026-11-20): the YouTube ``posts`` resource is a PILOT for approved Google Cloud projects
(GA planned for 2027); ``posts.insert`` needs the ``youtube``, ``youtube.force-ssl`` or ``youtubepartner`` scope and
"Posts that you create with this method are public and are published immediately. Scheduled posts aren't
supported." Scheduled posting exists only in the YouTube UI (Create > Create post > arrow next to Post > Schedule).

Design:
* Publishing is OFF by default and can only be switched on after a capability check returned API_AVAILABLE (less
  than 7 days old). Every gate is re-checked at publish time; anything unknown fails closed.
* Approval and publishing are separate actions. Only an APPROVED draft can be published, and only if its text
  fingerprint and image SHA-256 still equal the values bound at approval (re-hashed from disk).
* One publication record per draft content (UNIQUE idempotency key) and never two live API publications per
  draft. An insert that may have reached YouTube (timeout, dropped connection, 5xx) becomes UNCERTAIN and is NEVER
  retried: it is reconciled with ``posts.list`` or resolved explicitly by the operator.
* When the API is not available, the manual handoff exports the approved text and image and records the handoff;
  a manual post is only ever recorded as operator-reported, never as verified.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.app.community import store as S

API_AVAILABLE, API_NOT_ENABLED, PILOT_ACCESS_REQUIRED = "API_AVAILABLE", "API_NOT_ENABLED", "PILOT_ACCESS_REQUIRED"
OAUTH_SCOPE_INSUFFICIENT, CHANNEL_PERMISSION_INSUFFICIENT, UNKNOWN = (
    "OAUTH_SCOPE_INSUFFICIENT", "CHANNEL_PERMISSION_INSUFFICIENT", "UNKNOWN")
OUTCOMES = (API_AVAILABLE, API_NOT_ENABLED, PILOT_ACCESS_REQUIRED, OAUTH_SCOPE_INSUFFICIENT,
            CHANNEL_PERMISSION_INSUFFICIENT, UNKNOWN)
CAPABILITY_MAX_AGE = timedelta(days=7)
DAILY_API_PUBLISH_LIMIT = 2
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# publication statuses
REQUESTED, IMAGE_UPLOADED, INSERTING = "REQUESTED", "IMAGE_UPLOADED", "INSERTING"
PUBLISHED, FAILED, UNCERTAIN = "PUBLISHED", "FAILED", "UNCERTAIN"
HANDOFF_PREPARED, MANUAL_REPORTED, HANDOFF_CANCELLED = "HANDOFF_PREPARED", "MANUAL_REPORTED_POSTED", "HANDOFF_CANCELLED"
# Stage 3.32F completion: local publishing queue, bounded automatic retries, reauthorization, cancellation
QUEUED, RETRY_PENDING, REQUIRES_REAUTH, CANCELLED = "QUEUED", "RETRY_PENDING", "REQUIRES_REAUTHORIZATION", "CANCELLED"
IN_FLIGHT = (REQUESTED, IMAGE_UPLOADED, INSERTING)
LIVE = (REQUESTED, IMAGE_UPLOADED, INSERTING, UNCERTAIN, PUBLISHED)     # an API publication occupies the draft
PENDING = (QUEUED, RETRY_PENDING, REQUIRES_REAUTH)                    # authorized, not yet sent; cancelled by edits
ACTIVE_API = PENDING + LIVE                                           # at most one per draft (unique index)
LOCKS_DRAFT = (REQUESTED, IMAGE_UPLOADED, INSERTING, UNCERTAIN, PUBLISHED, MANUAL_REPORTED)
RESTARTABLE = (FAILED, RETRY_PENDING, REQUIRES_REAUTH, CANCELLED, QUEUED)   # explicit "publish now" may reuse the row
MAX_ATTEMPTS = 3                                                      # executions per authorization (1 + 2 retries)
RETRY_BACKOFF = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
STALE_AFTER = timedelta(minutes=15)                                   # an in-flight row older than this was interrupted
MAX_SCHEDULE_AHEAD = timedelta(days=30)
QUEUE_ACTOR = "system:community-publish-queue"
AUTH_REASONS = {"authRequired", "notConnected", "invalid_grant", "unauthorized", "authError",
                "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError", "internalError",
                 "timeout", "network", "RESOURCE_EXHAUSTED"}

# Workflow (lifecycle) states shown to the operator: one per draft, derived from the draft and its latest publication.
LC_DRAFT, LC_VALIDATED, LC_APPROVED, LC_QUEUED, LC_PUBLISHING, LC_PUBLISHED = (
    "DRAFT", "VALIDATED", "APPROVED", "QUEUED", "PUBLISHING", "PUBLISHED")
LC_FAILED, LC_RETRY, LC_CANCELLED, LC_REAUTH, LC_MANUAL, LC_REJECTED = (
    "FAILED", "RETRY_PENDING", "CANCELLED", "REQUIRES_REAUTHORIZATION", "MANUAL_HANDOFF", "REJECTED")
LIFECYCLE_STATES = (LC_DRAFT, LC_VALIDATED, LC_APPROVED, LC_QUEUED, LC_PUBLISHING, LC_PUBLISHED, LC_FAILED, LC_RETRY,
                    LC_CANCELLED, LC_REAUTH, LC_MANUAL, LC_REJECTED)

MANUAL_STEPS = [
    "Download the image and copy the text below.",
    "Open youtube.com while signed in to the CrazzyDocs channel.",
    "At the top of the page select Create, then Create post (or open your channel's Posts tab).",
    "Paste the text and add the downloaded image (JPG/PNG/GIF/WEBP, up to 16 MB).",
    "To publish now select Post. To schedule: select the down arrow next to Post, choose Schedule Post, pick the "
    "date, time and time zone, then select Schedule.",
    "Optionally come back and record that you posted it (operator-reported; CrazzyDocs cannot verify it).",
]
POST_URL = re.compile(r"^https://(www\.)?youtube\.com/(post/[A-Za-z0-9_-]{10,60}|channel/UC[A-Za-z0-9_-]{22}/(community|posts)\?lb=[A-Za-z0-9_-]{10,60})$")
POST_ID = re.compile(r"^[A-Za-z0-9_-]{10,60}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS community_capability_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at_utc TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL,
    checked_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS community_publications (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    idem_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    text_fingerprint TEXT NOT NULL,
    image_sha256 TEXT,
    image_id TEXT,
    youtube_post_id TEXT,
    post_url TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    request_json TEXT,
    response_json TEXT,
    error TEXT,
    requested_by TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    publish_at_utc TEXT,
    next_attempt_at_utc TEXT,
    blocked_reason TEXT,
    authorized_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_community_pub_draft ON community_publications (draft_id, status);
"""
NEW_COLUMNS = ("publish_at_utc", "next_attempt_at_utc", "blocked_reason", "authorized_by")
ACTIVE_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_community_pub_active_api ON community_publications (draft_id) "
                "WHERE mode = 'api' AND status IN (%s)" % ",".join(f"'{s}'" for s in (
                    "QUEUED", "RETRY_PENDING", "REQUIRES_REAUTHORIZATION", "REQUESTED", "IMAGE_UPLOADED", "INSERTING",
                    "UNCERTAIN", "PUBLISHED")))
TABLES = ("community_capability_checks", "community_publications")


class PublishingError(S.StoreError):
    code = 409


class PublishingDisabled(PublishingError):
    code = 423


def _dumps(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def tables_exist(db_path: str) -> bool:
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (?,?)", TABLES).fetchone()[0]
        return n == len(TABLES)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def failure_status(exc: Any, stage: str) -> str:
    """Where a failed attempt goes. Authentication problems stop and ask for reauthorization; an insert whose outcome
    is unknown is UNCERTAIN and never retried; quota, rate limits, 5xx and network trouble are retried with backoff;
    anything else is a definitive failure."""
    status, reason = getattr(exc, "status", None), getattr(exc, "reason", "") or ""
    if status == 401 or reason in AUTH_REASONS:
        return REQUIRES_REAUTH
    if stage == "insert" and getattr(exc, "ambiguous", False):
        return UNCERTAIN
    if reason in RETRY_REASONS or status == 429 or (status or 0) >= 500:
        return RETRY_PENDING
    return FAILED


def lifecycle(draft: Optional[Dict[str, Any]], pub: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The single workflow state of a draft: DRAFT > VALIDATED > APPROVED > QUEUED > PUBLISHING > PUBLISHED, or
    FAILED / RETRY_PENDING / CANCELLED / REQUIRES_REAUTHORIZATION / MANUAL_HANDOFF / REJECTED. A manual handoff is
    never reported as PUBLISHED: an operator-reported post stays MANUAL_HANDOFF with ``handoff_completed``."""
    if not draft:
        return {"state": LC_DRAFT}
    st = draft.get("status")
    if st in S.IN_PROGRESS or st in S.FAILED_STATES:
        return {"state": LC_DRAFT, "detail": st}
    if st == S.READY_FOR_REVIEW:
        return {"state": LC_VALIDATED, "detail": draft.get("validation_status")}
    if st == S.REJECTED:
        return {"state": LC_REJECTED}
    if st in (S.ARCHIVED, S.CANCELLED):
        return {"state": LC_CANCELLED, "detail": st}
    ps = (pub or {}).get("status")
    if not pub or ps in (HANDOFF_CANCELLED, CANCELLED):
        return {"state": LC_APPROVED, "detail": "approved; not queued or published"}
    if ps == QUEUED:
        return {"state": LC_QUEUED, "publish_at_utc": pub.get("publish_at_utc"), "blocked_reason": pub.get("blocked_reason")}
    if ps in IN_FLIGHT:
        return {"state": LC_PUBLISHING, "detail": ps}
    if ps == UNCERTAIN:
        return {"state": LC_PUBLISHING, "detail": "outcome uncertain: reconcile before anything else", "uncertain": True}
    if ps == PUBLISHED:
        return {"state": LC_PUBLISHED, "post_url": pub.get("post_url"), "verified": True}
    if ps == RETRY_PENDING:
        return {"state": LC_RETRY, "next_attempt_at_utc": pub.get("next_attempt_at_utc")}
    if ps == REQUIRES_REAUTH:
        return {"state": LC_REAUTH}
    if ps == FAILED:
        return {"state": LC_FAILED}
    if ps == HANDOFF_PREPARED:
        return {"state": LC_MANUAL, "handoff_completed": False}
    if ps == MANUAL_REPORTED:
        return {"state": LC_MANUAL, "handoff_completed": True, "verified": False}
    return {"state": LC_APPROVED}


def latest_publication(conn: sqlite3.Connection, draft_id: str) -> Optional[Dict[str, Any]]:
    try:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM community_publications WHERE draft_id=? ORDER BY updated_at_utc DESC, "
                         "created_at_utc DESC LIMIT 1", (draft_id,)).fetchone()
    except sqlite3.Error:
        return None
    return dict(r) if r else None


def lock_reason(conn: sqlite3.Connection, draft_id: str) -> Optional[str]:
    """Why a draft can no longer change (it was published, is being published, or was reported posted)."""
    try:
        row = conn.execute("SELECT status, mode FROM community_publications WHERE draft_id=? AND status IN (%s) "
                           "ORDER BY created_at_utc DESC LIMIT 1" % ",".join("?" * len(LOCKS_DRAFT)),
                           (draft_id, *LOCKS_DRAFT)).fetchone()
    except sqlite3.Error:
        return None
    return f"the draft is {row[0]} ({row[1]}); it can no longer be changed" if row else None


def classify(granted: List[str], probe_error: Optional[Any], list_ok: bool) -> Dict[str, Any]:
    """Outcome from the granted scopes and the read-only posts.list probe."""
    from backend.app.community.youtube_posts import PUBLISH_SCOPES
    has_publish = bool(set(granted) & PUBLISH_SCOPES)
    if list_ok:
        return {"outcome": API_AVAILABLE if has_publish else OAUTH_SCOPE_INSUFFICIENT, "inferred": False,
                "basis": "posts.list succeeded" + ("" if has_publish else "; the token lacks a posts.insert scope")}
    if probe_error is None:
        return {"outcome": UNKNOWN, "inferred": True, "basis": "no probe result"}
    reason, status, msg = (probe_error.reason or "").lower(), probe_error.status, (probe_error.message or "").lower()
    if reason in ("accessnotconfigured", "service_disabled") or "has not been used in project" in msg or "is disabled" in msg:
        return {"outcome": API_NOT_ENABLED, "inferred": False, "basis": f"posts.list: {probe_error.reason}"}
    if reason in ("insufficientpermissions", "access_token_scope_insufficient"):
        return {"outcome": OAUTH_SCOPE_INSUFFICIENT, "inferred": False, "basis": f"posts.list: {probe_error.reason}"}
    if reason == "channelnotfound":
        return {"outcome": CHANNEL_PERMISSION_INSUFFICIENT, "inferred": False, "basis": "posts.list: channelNotFound"}
    if reason in ("ratelimitexceeded", "quotaexceeded", "dailylimitexceeded", "timeout", "network", "internalerror") \
            or (status or 0) >= 500:
        return {"outcome": UNKNOWN, "inferred": True, "basis": f"posts.list could not be evaluated: {probe_error.reason}"}
    if status in (403, 404):
        return {"outcome": PILOT_ACCESS_REQUIRED, "inferred": True,
                "basis": f"posts.list refused ({status} {probe_error.reason}); the documentation does not state the "
                         "error a non-pilot project receives, so pilot access is the documented explanation"}
    if status == 401:
        return {"outcome": OAUTH_SCOPE_INSUFFICIENT, "inferred": True, "basis": "posts.list: 401 unauthorised"}
    return {"outcome": UNKNOWN, "inferred": True, "basis": f"posts.list: {status} {probe_error.reason}"}


class PublishingService:
    def __init__(self, community, *, credentials_factory: Optional[Callable[[], Any]] = None,
                 api_factory: Optional[Callable[[Any], Any]] = None):
        self.c = community                                       # CommunityService
        self.store = community.store
        self.db_path = community.db_path
        self._credentials_factory = credentials_factory
        self._api_factory = api_factory

    # -- plumbing ---------------------------------------------------------------------------------------------
    def _credentials(self):
        if self._credentials_factory:
            return self._credentials_factory()
        from backend.app.community.youtube_posts import LocalCredentials
        return LocalCredentials()

    def _api(self, creds):
        if self._api_factory:
            return self._api_factory(creds)
        from backend.app.community.youtube_posts import PostsApi
        return PostsApi(creds)

    def _ensure(self) -> None:
        self.store.ensure_schema()
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=rw", uri=True, timeout=5.0)
        try:
            conn.executescript(SCHEMA)
            have = {r[1] for r in conn.execute("PRAGMA table_info(community_publications)")}
            for col in NEW_COLUMNS:                                   # additive: rows and history are untouched
                if col not in have:
                    conn.execute(f"ALTER TABLE community_publications ADD COLUMN {col} TEXT")
            conn.execute("DROP INDEX IF EXISTS uq_community_pub_live_api")   # superseded: also covers queued rows
            conn.execute(ACTIVE_INDEX)
            conn.commit()
        finally:
            conn.close()

    def _ready(self) -> bool:
        return tables_exist(self.db_path)

    def _row(self, conn, pid: str) -> Dict[str, Any]:
        r = conn.execute("SELECT * FROM community_publications WHERE id=?", (pid,)).fetchone()
        if r is None:
            raise S.NotFound("publication not found")
        d = dict(r)
        for k in ("request_json", "response_json"):
            if d.get(k):
                try:
                    d[k[:-5]] = json.loads(d.pop(k))
                except ValueError:
                    d[k[:-5]] = None
            else:
                d.pop(k, None)
        return d

    def _update(self, conn, pid: str, now: datetime, expect: Optional[tuple] = None, **fields: Any) -> None:
        sets = dict(fields, updated_at_utc=S.ts(now))
        for k in ("request", "response"):
            if k in sets:
                sets[k + "_json"] = _dumps(sets.pop(k))
        sql = ", ".join(f"{k}=?" for k in sets)
        args = list(sets.values()) + [pid]
        where = "id=?"
        if expect:
            where += " AND status IN (%s)" % ",".join("?" * len(expect))
            args += list(expect)
        if conn.execute(f"UPDATE community_publications SET {sql} WHERE {where}", args).rowcount != 1:
            raise PublishingError("the publication changed concurrently; reload and try again")

    # -- capability ---------------------------------------------------------------------------------------------
    def latest_capability(self) -> Optional[Dict[str, Any]]:
        if not self._ready():
            return None
        with self.store.read() as conn:
            r = conn.execute("SELECT * FROM community_capability_checks ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        d = dict(r)
        d["details"] = json.loads(d.pop("details_json") or "{}")
        checked = S.parse_ts(d["checked_at_utc"])
        d["fresh"] = bool(checked and self.c.clock() - checked <= CAPABILITY_MAX_AGE)
        return d

    def check_capability(self, operator: Optional[str] = None) -> Dict[str, Any]:
        """Read-only probe: granted scopes (local), channels.list?mine=true and posts.list (1 unit each, separate
        quotas). Never uploads, inserts, updates or deletes. Stores the diagnosis without any credential."""
        from backend.app.community.service import _actor, _validate_operator
        from backend.app.community.youtube_posts import (IMAGE_UPLOAD_SCOPES, LIST_SCOPES, PUBLISH_SCOPES, ApiError,
                                                         NotConnected)
        op = _validate_operator(operator)
        now = self.c.clock()
        details: Dict[str, Any] = {"required_scopes": {"posts.list": sorted(LIST_SCOPES), "posts.insert": sorted(PUBLISH_SCOPES),
                                                       "posts.imageUpload": sorted(IMAGE_UPLOAD_SCOPES)},
                                   "calls": [], "docs": "https://developers.google.com/youtube/data/reference/rest/v3/posts"}
        try:
            creds = self._credentials()
            granted = creds.granted_scopes()
            details["granted_scopes"] = granted
            details["project_id"] = getattr(creds, "project_id", lambda: None)()
            details["has_list_scope"] = bool(set(granted) & LIST_SCOPES)
            details["has_insert_scope"] = bool(set(granted) & PUBLISH_SCOPES)
            api = self._api(creds)
            probe_error, list_ok = None, False
            try:
                channel = api.channel_id()
                details["channel_id"] = channel
                details["calls"].append({"call": "channels.list(mine=true)", "ok": True})
                try:
                    items = api.list_posts(channel, 1)
                    list_ok = True
                    details["calls"].append({"call": "posts.list", "ok": True, "items": len(items)})
                except ApiError as exc:
                    probe_error = exc
                    details["calls"].append({"call": "posts.list", "ok": False, **exc.public()})
            except ApiError as exc:
                details["calls"].append({"call": "channels.list(mine=true)", "ok": False, **exc.public()})
                result = {"outcome": CHANNEL_PERMISSION_INSUFFICIENT if exc.status in (401, 403, 404) else UNKNOWN,
                          "inferred": exc.status not in (404,), "basis": f"channels.list: {exc.status} {exc.reason}"}
            else:
                result = classify(granted, probe_error, list_ok)
        except NotConnected as exc:
            result = {"outcome": UNKNOWN, "inferred": False, "basis": f"not connected: {exc}"}
        except Exception as exc:                                      # noqa: BLE001 - recorded, fail closed
            from backend.app.autonomy.control_plane import safe_error
            result = {"outcome": UNKNOWN, "inferred": True, "basis": safe_error(exc)}
        details.update(result)
        details["publishing_possible"] = result["outcome"] == API_AVAILABLE
        extra = []                                   # every blocker, not only the first one found
        if details.get("has_insert_scope") is False and result["outcome"] != OAUTH_SCOPE_INSUFFICIENT:
            extra.append({"outcome": OAUTH_SCOPE_INSUFFICIENT,
                          "basis": "the token holds no posts.insert scope (youtube, youtube.force-ssl or youtubepartner)"})
        details["additional_blockers"] = extra
        from backend.app.autonomy.control_plane import redact
        details = redact(details)
        self.store.ensure_schema()
        self._ensure()
        with self.store.tx() as conn:
            conn.execute("INSERT INTO community_capability_checks (checked_at_utc, outcome, details_json, checked_by) "
                         "VALUES (?,?,?,?)", (S.ts(now), result["outcome"], _dumps(details), _actor(op)))
            conn.execute("DELETE FROM community_capability_checks WHERE id NOT IN (SELECT id FROM "
                         "community_capability_checks ORDER BY id DESC LIMIT 50)")
            self.store.event(conn, "PUBLISHING_CAPABILITY_CHECKED", _actor(op), now,
                             detail={"outcome": result["outcome"], "basis": result["basis"]})
        return self.latest_capability()

    # -- enable / disable ---------------------------------------------------------------------------------------
    def set_enabled(self, operator: Optional[str], enabled: bool, confirm: bool = False) -> Dict[str, Any]:
        from backend.app.community.service import BadRequest, _actor, _validate_operator
        op = _validate_operator(operator)
        if not isinstance(enabled, bool):
            raise BadRequest("enabled must be true or false")
        if enabled:
            if confirm is not True:
                raise BadRequest("enabling publishing needs confirm=true")
            cap = self.latest_capability()
            if not cap or cap["outcome"] != API_AVAILABLE or not cap["fresh"]:
                raise PublishingDisabled("publishing can only be enabled after a capability check returned "
                                         f"API_AVAILABLE within {CAPABILITY_MAX_AGE.days} days "
                                         f"(latest: {(cap or {}).get('outcome', 'never checked')})")
        now = self.c.clock()
        self.store.ensure_schema()
        with self.store.tx() as conn:
            self.store.set_config(conn, {"publishing_enabled": enabled}, _actor(op), now)
            self.store.event(conn, "PUBLISHING_ENABLED" if enabled else "PUBLISHING_DISABLED", _actor(op), now)
        return self.status()

    def _enabled(self) -> bool:
        return self.store.get_config().get("publishing_enabled") is True

    # -- gates ----------------------------------------------------------------------------------------------------
    def gates(self, draft_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cap = self.latest_capability()
        cfg = self.c.config()
        out = [
            {"gate": "publishing_enabled", "ok": self._enabled(), "detail": "publishing is OFF by default"},
            {"gate": "capability_api_available", "ok": bool(cap and cap["outcome"] == API_AVAILABLE),
             "detail": (cap or {}).get("outcome", "never checked")},
            {"gate": "capability_fresh", "ok": bool(cap and cap["fresh"]),
             "detail": f"checked within {CAPABILITY_MAX_AGE.days} days"},
            {"gate": "no_kill_switch", "ok": not self.c.kill_switches(cfg), "detail": ", ".join(self.c.kill_switches(cfg))},
        ]
        if draft_id:
            d = self.store.get_draft(draft_id)
            out.append({"gate": "draft_approved", "ok": bool(d and d["status"] == S.APPROVED),
                        "detail": (d or {}).get("status", "not found")})
            binding = self._approval_binding(draft_id) if d else None
            ok, why = self._content_matches(d, binding) if d else (False, "draft not found")
            out.append({"gate": "content_matches_approval", "ok": ok, "detail": why})
        return out

    def _approval_binding(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self.store.read() as conn:
            r = conn.execute("SELECT detail_json, created_at_utc FROM community_events WHERE draft_id=? AND "
                             "event='DRAFT_APPROVED' ORDER BY id DESC LIMIT 1", (draft_id,)).fetchone()
        return json.loads(r[0] or "{}") if r else None

    def _content_matches(self, d: Dict[str, Any], binding: Optional[Dict[str, Any]]):
        from backend.app.community import content as C
        from backend.app.community import images as I
        if not binding:
            return False, "no approval record"
        if C.fingerprint(d.get("text") or "") != binding.get("text_fingerprint") or \
                d.get("text_fingerprint") != binding.get("text_fingerprint"):
            return False, "text changed after approval"
        img = self.store.get_image(d["image_id"]) if d.get("image_id") else None
        path = self.c._image_path(img)
        if not img or path is None or not path.is_file():
            return False, "image missing"
        sha = I.sha256_file(path)
        if sha != img["sha256"] or sha != binding.get("image_sha256"):
            return False, "image changed after approval"
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return False, "image larger than 16 MB"
        return True, "text and image equal the approved versions"

    # -- API publishing -------------------------------------------------------------------------------------------
    def publish(self, draft_id: str, operator: Optional[str], version: int, confirm: bool = False) -> Dict[str, Any]:
        """Immediate, PUBLIC publication of one approved draft through posts.insert. Separate from approval; only
        when every gate passes. Never called by the scheduler."""
        from backend.app.community.service import BadRequest, _actor, _validate_operator
        op = _validate_operator(operator)
        if confirm is not True:
            raise BadRequest("publishing needs confirm=true: posts.insert publishes publicly and immediately")
        d = self.c._draft(draft_id)
        if int(d["version"]) != int(version):
            raise S.Conflict("the draft changed since it was loaded; reload and try again")
        failed = [g for g in self.gates(draft_id) if not g["ok"]]
        if failed:
            raise PublishingDisabled("publishing refused (fail closed): " +
                                     "; ".join(f"{g['gate']} ({g['detail']})" for g in failed))
        now = self.c.clock()
        tz = self.c._tz(self.c.config())
        local = now.astimezone(tz)
        midnight = datetime(local.year, local.month, local.day, tzinfo=tz)
        img = self.store.get_image(d["image_id"])
        key = f"api:{draft_id}:{d['text_fingerprint']}:{img['sha256']}"
        self._ensure()
        with self.store.tx() as conn:
            n = conn.execute("SELECT COUNT(*) FROM community_publications WHERE mode='api' AND created_at_utc >= ? "
                             "AND status IN ('PUBLISHED','UNCERTAIN','INSERTING')", (S.ts(midnight),)).fetchone()[0]
            if n >= DAILY_API_PUBLISH_LIMIT:
                raise S.Conflict(f"daily API publication limit reached ({DAILY_API_PUBLISH_LIMIT})")
            existing = conn.execute("SELECT id, status FROM community_publications WHERE idem_key=?", (key,)).fetchone()
            if existing and existing[1] not in RESTARTABLE:
                raise S.Conflict(f"this approved draft already has publication {existing[0]} ({existing[1]})")
            pid = existing[0] if existing else S.new_id("cpub")
            try:
                if existing:                                            # explicit "publish now" by the operator
                    self._update(conn, pid, now, expect=RESTARTABLE, status=REQUESTED, error=None,
                                 next_attempt_at_utc=None, blocked_reason=None, authorized_by=_actor(op),
                                 **({"attempts": 0} if existing[1] in (CANCELLED, QUEUED, REQUIRES_REAUTH) else {}))
                else:
                    conn.execute("INSERT INTO community_publications (id, draft_id, mode, idem_key, status, text_fingerprint, "
                                 "image_sha256, image_id, attempts, requested_by, created_at_utc, updated_at_utc, "
                                 "authorized_by) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?)",
                                 (pid, draft_id, "api", key, REQUESTED, d["text_fingerprint"], img["sha256"], img["id"],
                                  _actor(op), S.ts(now), S.ts(now), _actor(op)))
            except sqlite3.IntegrityError as exc:
                raise S.Conflict("another API publication for this draft is in progress or done") from exc
            self.store.event(conn, "PUBLISH_REQUESTED", _actor(op), now, draft_id=draft_id,
                             detail={"publication_id": pid, "text_fingerprint": d["text_fingerprint"],
                                     "image_sha256": img["sha256"]})
        return self._run_publication(pid, d, img, _actor(op))

    def _run_publication(self, pid: str, d: Dict[str, Any], img: Dict[str, Any], actor: str) -> Dict[str, Any]:
        from backend.app.community.youtube_posts import ApiError, NotConnected
        with self.store.tx() as conn:                            # one attempt per execution (bounded retries)
            self._update(conn, pid, self.c.clock(), expect=(REQUESTED,),
                         attempts=int(conn.execute("SELECT attempts FROM community_publications WHERE id=?",
                                                   (pid,)).fetchone()[0]) + 1)
        cap = self.latest_capability() or {}
        channel = (cap.get("details") or {}).get("channel_id")
        try:
            api = self._api(self._credentials())
            if not channel:
                raise ApiError(None, "noChannel", "the capability check recorded no channel id")
            token = api.image_upload(channel, self.c._image_path(img), "image/jpeg")
            with self.store.tx() as conn:
                self._update(conn, pid, self.c.clock(), expect=(REQUESTED,), status=IMAGE_UPLOADED)
        except ApiError as exc:                                  # nothing was posted
            return self._failed(pid, d["id"], actor, exc, "image_upload")
        except NotConnected as exc:
            return self._failed(pid, d["id"], actor, ApiError(None, "notConnected", str(exc)), "image_upload")
        except Exception as exc:                                 # noqa: BLE001 - never leave a row stuck mid-flight
            return self._finish(pid, d["id"], FAILED, actor, stage="image_upload",
                                error={"http_status": None, "reason": "internal", "message": type(exc).__name__,
                                       "ambiguous": False})
        with self.store.tx() as conn:
            self._update(conn, pid, self.c.clock(), expect=(IMAGE_UPLOADED,), status=INSERTING,
                         request={"channel_id": channel, "text_fingerprint": d["text_fingerprint"],
                                  "image_sha256": img["sha256"], "images": 1, "sent_at_utc": S.ts(self.c.clock())})
        try:
            post = api.insert(channel, d["text"], [token])
        except ApiError as exc:
            return self._failed(pid, d["id"], actor, exc, "insert")
        except Exception as exc:                                 # noqa: BLE001 - the request may have been sent
            return self._finish(pid, d["id"], UNCERTAIN, actor, stage="insert",
                                error={"http_status": None, "reason": "internal", "message": type(exc).__name__,
                                       "ambiguous": True})
        post_id = str(post.get("id"))
        return self._finish(pid, d["id"], PUBLISHED, actor, response={"id": post_id, "status": post.get("status"),
                                                                     "publishTime": (post.get("snippet") or {}).get("publishTime")},
                            post_id=post_id)

    def _failed(self, pid: str, draft_id: str, actor: str, exc: Any, stage: str) -> Dict[str, Any]:
        status = failure_status(exc, stage)
        extra: Dict[str, Any] = {}
        if status == RETRY_PENDING:
            with self.store.read() as conn:
                attempts = int(conn.execute("SELECT attempts FROM community_publications WHERE id=?", (pid,)).fetchone()[0])
            if attempts >= MAX_ATTEMPTS:
                status, extra = FAILED, {"retries_exhausted": True}
            else:
                extra = {"next_attempt_at_utc": S.ts(self.c.clock() + RETRY_BACKOFF[min(attempts, len(RETRY_BACKOFF)) - 1])}
        return self._finish(pid, draft_id, status, actor, error={**exc.public(), **({"retries_exhausted": True}
                                                                                    if extra.get("retries_exhausted") else {})},
                            stage=stage, next_attempt_at_utc=extra.get("next_attempt_at_utc"))

    def _finish(self, pid: str, draft_id: str, status: str, actor: str, *, error: Optional[Dict[str, Any]] = None,
                response: Optional[Dict[str, Any]] = None, post_id: Optional[str] = None,
                stage: Optional[str] = None, next_attempt_at_utc: Optional[str] = None) -> Dict[str, Any]:
        now = self.c.clock()
        fields: Dict[str, Any] = {"status": status, "next_attempt_at_utc": next_attempt_at_utc, "blocked_reason": None}
        if error is not None:
            fields["error"] = _dumps({**error, "stage": stage})
        if response is not None:
            fields["response"] = response
        if post_id:
            fields.update(youtube_post_id=post_id, post_url=f"https://www.youtube.com/post/{post_id}")
        with self.store.tx() as conn:
            self._update(conn, pid, now, **fields)
            self.store.event(conn, {PUBLISHED: "PUBLISH_SUCCEEDED", FAILED: "PUBLISH_FAILED",
                                    UNCERTAIN: "PUBLISH_UNCERTAIN", RETRY_PENDING: "PUBLISH_RETRY_SCHEDULED",
                                    REQUIRES_REAUTH: "PUBLISH_REAUTHORIZATION_REQUIRED"}[status], actor, now,
                             draft_id=draft_id, detail={"publication_id": pid, "post_id": post_id, "error": error,
                                                        "stage": stage, "next_attempt_at_utc": next_attempt_at_utc})
        reason = (error or {}).get("reason")
        if status == UNCERTAIN:
            self.c._notify("publish_uncertain", "Community post outcome unknown",
                           "The publish request may have reached YouTube but no answer arrived. It will not be retried; "
                           "reconcile it in the dashboard before doing anything else.", "high", draft_id)
        elif status == PUBLISHED:
            self.c._notify("publish_succeeded", "Community post published",
                           f"The approved Community post was published on YouTube (post {post_id}).", "info", draft_id)
        elif status == RETRY_PENDING:
            self.c._notify("publish_failed", "Community post publishing failed; will retry",
                           f"YouTube refused or did not answer ({reason}). A bounded automatic retry is scheduled for "
                           f"{next_attempt_at_utc} (UTC); every check is repeated first.", "medium", draft_id)
        elif status == REQUIRES_REAUTH:
            self.c._notify("auth_failure", "YouTube authorization needed for Community publishing",
                           f"Publishing stopped: the YouTube credentials were refused ({reason}). Nothing is retried "
                           "until the account is reauthorized; the approved post stays available for the manual handoff.",
                           "high", draft_id)
        elif status == FAILED:
            self.c._notify("publish_failed", "Community post publishing failed",
                           f"The post was not published ({reason}). It stays approved: retry explicitly or use the "
                           "manual handoff.", "high", draft_id)
        return self.publication(pid)

    # -- local publishing queue (Stage 3.32F completion) ------------------------------------------------------------
    def authorize(self, draft_id: str, operator: Optional[str], version: int, confirm: bool = False,
                  publish_at: Optional[str] = None) -> Dict[str, Any]:
        """Publication authorization, separate from draft approval: queues ONE approved draft for API publication at
        ``publish_at`` (ISO time; naive values are in the Community timezone; default: as soon as possible). The
        official API cannot schedule posts, so the local queue sends it at that time, after re-checking every gate.
        Refused (fail closed) unless API publishing is enabled and ready today."""
        from backend.app.community.service import BadRequest, _actor, _validate_operator
        op = _validate_operator(operator)
        if confirm is not True:
            raise BadRequest("authorizing publication needs confirm=true: the post becomes public at the queued time")
        d = self.c._draft(draft_id)
        if int(d["version"]) != int(version):
            raise S.Conflict("the draft changed since it was loaded; reload and try again")
        failed = [g for g in self.gates(draft_id) if not g["ok"]]
        if failed:
            raise PublishingDisabled("queueing refused (fail closed): " +
                                     "; ".join(f"{g['gate']} ({g['detail']})" for g in failed) +
                                     ". Use the manual handoff while API publishing is unavailable.")
        now = self.c.clock()
        when = now
        if publish_at not in (None, ""):
            try:
                when = datetime.fromisoformat(str(publish_at).replace("Z", "+00:00"))
            except ValueError:
                raise BadRequest("publish_at must be an ISO date-time") from None
            if when.tzinfo is None:
                when = when.replace(tzinfo=self.c._tz(self.c.config()))
            when = when.astimezone(timezone.utc)
            if when < now - timedelta(minutes=1):
                raise BadRequest("publish_at is in the past")
            if when > now + MAX_SCHEDULE_AHEAD:
                raise BadRequest(f"publish_at must be within {MAX_SCHEDULE_AHEAD.days} days")
        img = self.store.get_image(d["image_id"])
        key = f"api:{draft_id}:{d['text_fingerprint']}:{img['sha256']}"
        self._ensure()
        with self.store.tx() as conn:
            existing = conn.execute("SELECT id, status FROM community_publications WHERE idem_key=?", (key,)).fetchone()
            if existing and existing[1] not in (FAILED, CANCELLED):
                raise S.Conflict(f"this approved draft already has publication {existing[0]} ({existing[1]})")
            pid = existing[0] if existing else S.new_id("cpub")
            try:
                if existing:
                    self._update(conn, pid, now, expect=(FAILED, CANCELLED), status=QUEUED, error=None, attempts=0,
                                 publish_at_utc=S.ts(when), next_attempt_at_utc=None, blocked_reason=None,
                                 authorized_by=_actor(op))
                else:
                    conn.execute("INSERT INTO community_publications (id, draft_id, mode, idem_key, status, text_fingerprint, "
                                 "image_sha256, image_id, attempts, requested_by, created_at_utc, updated_at_utc, "
                                 "publish_at_utc, authorized_by) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                                 (pid, draft_id, "api", key, QUEUED, d["text_fingerprint"], img["sha256"], img["id"],
                                  _actor(op), S.ts(now), S.ts(now), S.ts(when), _actor(op)))
            except sqlite3.IntegrityError as exc:
                raise S.Conflict("another API publication for this draft is queued, in progress or done") from exc
            self.store.event(conn, "PUBLICATION_AUTHORIZED", _actor(op), now, draft_id=draft_id,
                             detail={"publication_id": pid, "publish_at_utc": S.ts(when),
                                     "text_fingerprint": d["text_fingerprint"], "image_sha256": img["sha256"]})
        return self.publication(pid)

    def cancel_publication(self, pid: str, operator: Optional[str], reason: str = "") -> Dict[str, Any]:
        from backend.app.community.service import _actor, _validate_operator
        op = _validate_operator(operator)
        pub = self.publication(pid)
        if pub["mode"] != "api" or pub["status"] not in PENDING + (FAILED,):
            raise S.Conflict(f"only a queued, retry-pending, reauthorization-pending or failed API publication can be "
                             f"cancelled (this one is {pub['mode']} {pub['status']})")
        now = self.c.clock()
        with self.store.tx() as conn:
            self._update(conn, pid, now, expect=PENDING + (FAILED,), status=CANCELLED, next_attempt_at_utc=None,
                         blocked_reason=None)
            self.store.event(conn, "PUBLICATION_CANCELLED", _actor(op), now, draft_id=pub["draft_id"],
                             detail={"publication_id": pid, "previous_status": pub["status"],
                                     "reason": (reason or "").strip()[:300]})
        return self.publication(pid)

    def cancel_pending_for_draft(self, draft_id: str, actor: str, reason: str) -> int:
        """An edit, regeneration or archive withdraws approval, so any authorization for the old content ends."""
        if not self._ready():
            return 0
        now = self.c.clock()
        n = 0
        with self.store.tx() as conn:
            rows = conn.execute("SELECT id, status FROM community_publications WHERE draft_id=? AND status IN (%s)"
                                % ",".join("?" * len(PENDING)), (draft_id, *PENDING)).fetchall()
            for pid, st in rows:
                self._update(conn, pid, now, expect=PENDING, status=CANCELLED, next_attempt_at_utc=None,
                             blocked_reason=None)
                self.store.event(conn, "PUBLICATION_CANCELLED", actor, now, draft_id=draft_id,
                                 detail={"publication_id": pid, "previous_status": st, "reason": reason})
                n += 1
        return n

    def recover_interrupted(self) -> Dict[str, int]:
        """After a crash or restart: a row stuck before the insert was sent is safely retryable (an unused image
        upload token is harmless); a row stuck while inserting may have been published and becomes UNCERTAIN."""
        out = {"retry": 0, "uncertain": 0}
        if not self._ready():
            return out
        now = self.c.clock()
        cutoff = S.ts(now - STALE_AFTER)
        with self.store.read() as conn:
            rows = conn.execute("SELECT id, draft_id, status, attempts FROM community_publications WHERE mode='api' AND "
                                "status IN (?,?,?) AND updated_at_utc < ?", (*IN_FLIGHT, cutoff)).fetchall()
        for pid, draft_id, st, attempts in rows:
            if st == INSERTING:
                self._finish(pid, draft_id, UNCERTAIN, QUEUE_ACTOR, stage="insert",
                             error={"http_status": None, "reason": "interrupted", "ambiguous": True,
                                    "message": "the process stopped while the insert request was in flight"})
                out["uncertain"] += 1
            else:
                retry = int(attempts) < MAX_ATTEMPTS
                self._finish(pid, draft_id, RETRY_PENDING if retry else FAILED, QUEUE_ACTOR, stage="image_upload",
                             error={"http_status": None, "reason": "interrupted", "ambiguous": False,
                                    "message": "the process stopped before the post was sent"},
                             next_attempt_at_utc=S.ts(now) if retry else None)
                out["retry"] += 1
        return out

    def process_queue(self) -> Dict[str, Any]:
        """Called by the scheduler tick. Sends at most ONE due, operator-authorized publication per tick, and only
        when every gate passes again; otherwise records why it waits. Never creates an authorization itself."""
        if not self._ready():
            return {"status": "not_configured"}
        self._ensure()
        recovered = self.recover_interrupted()
        now = self.c.clock()
        with self.store.read() as conn:
            due = conn.execute("SELECT id, draft_id, status FROM community_publications WHERE mode='api' AND ("
                               "(status=? AND COALESCE(publish_at_utc, created_at_utc) <= ?) OR "
                               "(status=? AND COALESCE(next_attempt_at_utc, updated_at_utc) <= ?)) "
                               "ORDER BY COALESCE(next_attempt_at_utc, publish_at_utc, created_at_utc) LIMIT 5",
                               (QUEUED, S.ts(now), RETRY_PENDING, S.ts(now))).fetchall()
        if not due:
            return {"status": "idle", "recovered": recovered}
        waiting = []
        for pid, draft_id, st in due:
            pub = self.publication(pid)
            d = self.store.get_draft(draft_id)
            gates = self.gates(draft_id)
            bound = bool(d) and d.get("text_fingerprint") == pub["text_fingerprint"]
            content_bad = [g for g in gates if g["gate"] in ("draft_approved", "content_matches_approval") and not g["ok"]]
            if content_bad or not bound:
                why = "; ".join(f"{g['gate']} ({g['detail']})" for g in content_bad) or "text differs from the authorization"
                self.cancel_pending_for_draft(draft_id, QUEUE_ACTOR, f"authorization no longer valid: {why}")
                self.c._notify("publish_failed", "Queued Community post cancelled",
                               f"The queued post no longer matches what was approved and authorized ({why}). It was "
                               "cancelled; nothing was published.", "medium", draft_id)
                continue
            blocked = [g for g in gates if not g["ok"]]
            if not blocked:
                tz = self.c._tz(self.c.config())
                local = now.astimezone(tz)
                midnight = datetime(local.year, local.month, local.day, tzinfo=tz)
                with self.store.read() as conn:
                    n = conn.execute("SELECT COUNT(*) FROM community_publications WHERE mode='api' AND updated_at_utc >= ? "
                                     "AND status IN ('PUBLISHED','UNCERTAIN','INSERTING')", (S.ts(midnight),)).fetchone()[0]
                if n >= DAILY_API_PUBLISH_LIMIT:
                    blocked = [{"gate": "daily_limit", "detail": f"{DAILY_API_PUBLISH_LIMIT} API publications per day"}]
            if blocked:
                why = "; ".join(f"{g['gate']} ({g['detail']})" for g in blocked)[:300]
                if pub.get("blocked_reason") != why:
                    with self.store.tx() as conn:
                        self._update(conn, pid, now, expect=(st,), blocked_reason=why)
                waiting.append({"publication_id": pid, "blocked": why})
                continue
            try:
                with self.store.tx() as conn:                    # claim (compare-and-set): never sent twice
                    self._update(conn, pid, now, expect=(st,), status=REQUESTED, blocked_reason=None,
                                 next_attempt_at_utc=None)
                    self.store.event(conn, "PUBLISH_REQUESTED", QUEUE_ACTOR, now, draft_id=draft_id,
                                     detail={"publication_id": pid, "from": st, "authorized_by": pub.get("authorized_by"),
                                             "text_fingerprint": pub["text_fingerprint"], "image_sha256": pub["image_sha256"]})
            except PublishingError:
                continue
            img = self.store.get_image(d["image_id"])
            result = self._run_publication(pid, d, img, QUEUE_ACTOR)
            return {"status": "processed", "publication": {k: result.get(k) for k in ("id", "status", "attempts")},
                    "recovered": recovered}
        return {"status": "waiting", "waiting": waiting, "recovered": recovered}

    def notify_after_approval(self, draft_id: str) -> None:
        """Approval never publishes. Tell the operator what happens next: publication awaits their separate
        authorization, or (API unavailable) the manual handoff is required."""
        try:
            gates = self.gates()
            if all(g["ok"] for g in gates):
                self.c._notify("approved_awaiting_publication", "Approved Community post awaiting publication",
                               "The draft is approved. It is NOT published: authorize publication (now or queued for a "
                               "time) in the Community tab.", "info", draft_id)
            else:
                cap = self.latest_capability()
                self.c._notify("manual_handoff_required", "Manual handoff required for an approved Community post",
                               "The draft is approved but API publishing is unavailable "
                               f"({(cap or {}).get('outcome', 'never checked')}). Use Manual handoff in the Community "
                               "tab to copy the text and image into YouTube. Nothing has been published.", "info", draft_id)
        except Exception:                                        # noqa: BLE001 - notifications never break approval
            pass

    def reconcile(self, pid: str, operator: Optional[str] = None) -> Dict[str, Any]:
        """For an UNCERTAIN insert: look for a post with the same text on the channel (posts.list, read-only)."""
        from backend.app.community.service import _actor, _validate_operator
        from backend.app.community.youtube_posts import ApiError
        op = _validate_operator(operator)
        pub = self.publication(pid)
        if pub["status"] != UNCERTAIN:
            raise S.Conflict(f"only UNCERTAIN publications are reconciled (this one is {pub['status']})")
        d = self.store.get_draft(pub["draft_id"])
        channel = ((self.latest_capability() or {}).get("details") or {}).get("channel_id")
        sent = S.parse_ts((pub.get("request") or {}).get("sent_at_utc"))
        try:
            items = self._api(self._credentials()).list_posts(channel, 20)
        except ApiError as exc:
            return {"publication": pub, "reconciled": False, "reason": f"posts.list failed: {exc.reason}"}

        def norm(t):
            return " ".join((t or "").split())
        match = None
        for it in items:
            sn = it.get("snippet") or {}
            when = _parse_iso(sn.get("publishTime"))
            if norm(sn.get("textDisplay")) == norm(d["text"]) and (sent is None or when is None or when >= sent - timedelta(minutes=5)):
                match = it
                break
        if match is None:
            return {"publication": pub, "reconciled": False,
                    "reason": "no matching post found yet; it stays UNCERTAIN (never retried automatically)"}
        now = self.c.clock()
        with self.store.tx() as conn:
            self._update(conn, pid, now, expect=(UNCERTAIN,), status=PUBLISHED, youtube_post_id=str(match["id"]),
                         post_url=f"https://www.youtube.com/post/{match['id']}", response={"reconciled": True, "id": match["id"]})
            self.store.event(conn, "PUBLISH_RECONCILED", _actor(op), now, draft_id=pub["draft_id"],
                             detail={"publication_id": pid, "post_id": match["id"]})
        return {"publication": self.publication(pid), "reconciled": True}

    def resolve_uncertain(self, pid: str, operator: Optional[str], outcome: str, reason: str,
                          post_id: Optional[str] = None) -> Dict[str, Any]:
        """Operator decision after checking YouTube by hand. 'not_published' makes a retry possible."""
        from backend.app.community.service import BadRequest, _actor, _validate_operator
        op = _validate_operator(operator)
        if outcome not in ("published", "not_published"):
            raise BadRequest("outcome must be published or not_published")
        if not 5 <= len((reason or "").strip()) <= 500:
            raise BadRequest("a reason of 5-500 characters is required")
        if outcome == "published" and post_id is not None and not POST_ID.match(post_id):
            raise BadRequest("invalid post id")
        pub = self.publication(pid)
        if pub["status"] != UNCERTAIN:
            raise S.Conflict(f"only UNCERTAIN publications can be resolved (this one is {pub['status']})")
        now = self.c.clock()
        with self.store.tx() as conn:
            if outcome == "published":
                self._update(conn, pid, now, expect=(UNCERTAIN,), status=PUBLISHED, youtube_post_id=post_id,
                             post_url=f"https://www.youtube.com/post/{post_id}" if post_id else None)
            else:
                self._update(conn, pid, now, expect=(UNCERTAIN,), status=FAILED,
                             error=_dumps({"resolved": "operator confirmed nothing was published"}))
            self.store.event(conn, "PUBLISH_RESOLVED", _actor(op), now, draft_id=pub["draft_id"],
                             detail={"publication_id": pid, "outcome": outcome, "reason": reason.strip(), "post_id": post_id,
                                     "verified": False})
        return self.publication(pid)

    # -- manual handoff -------------------------------------------------------------------------------------------
    def prepare_handoff(self, draft_id: str, operator: Optional[str] = None) -> Dict[str, Any]:
        from backend.app.community.service import _actor, _validate_operator
        op = _validate_operator(operator)
        d = self.c._draft(draft_id)
        if d["status"] != S.APPROVED:
            raise S.InvalidTransition(f"only APPROVED drafts can be handed off (draft is {d['status']})")
        ok, why = self._content_matches(d, self._approval_binding(draft_id))
        if not ok:
            raise S.Conflict(f"the draft differs from what was approved: {why}; re-approve it first")
        img = self.store.get_image(d["image_id"])
        key = f"manual:{draft_id}:{d['text_fingerprint']}:{img['sha256']}"
        now = self.c.clock()
        self._ensure()
        with self.store.tx() as conn:
            row = conn.execute("SELECT id FROM community_publications WHERE idem_key=?", (key,)).fetchone()
            if row is None:
                pid = S.new_id("cpub")
                conn.execute("INSERT INTO community_publications (id, draft_id, mode, idem_key, status, text_fingerprint, "
                             "image_sha256, image_id, attempts, requested_by, created_at_utc, updated_at_utc) "
                             "VALUES (?,?,?,?,?,?,?,?,0,?,?,?)", (pid, draft_id, "manual", key, HANDOFF_PREPARED,
                                                                  d["text_fingerprint"], img["sha256"], img["id"],
                                                                  _actor(op), S.ts(now), S.ts(now)))
                self.store.event(conn, "MANUAL_HANDOFF_PREPARED", _actor(op), now, draft_id=draft_id,
                                 detail={"publication_id": pid, "published": False})
            else:
                pid = row[0]
        return {**self.publication(pid), "handoff": {
            "text": d["text"], "image_url": f"/api/community/images/{img['id']}",
            "image_download_url": f"/api/community/images/{img['id']}?download=1",
            "open_url": "https://www.youtube.com/", "steps": MANUAL_STEPS,
            "note": "Nothing has been published. Scheduling a post is done in the YouTube interface; the API cannot "
                    "schedule posts."}}

    def report_manual_post(self, pid: str, operator: Optional[str] = None, post_url: Optional[str] = None) -> Dict[str, Any]:
        from backend.app.community.service import BadRequest, _actor, _validate_operator
        op = _validate_operator(operator)
        if post_url is not None and post_url != "" and not POST_URL.match(post_url):
            raise BadRequest("post_url must be a youtube.com post link, e.g. https://www.youtube.com/post/<id>")
        pub = self.publication(pid)
        if pub["mode"] != "manual" or pub["status"] != HANDOFF_PREPARED:
            raise S.Conflict(f"only a prepared manual handoff can be reported (this one is {pub['mode']} {pub['status']})")
        now = self.c.clock()
        with self.store.tx() as conn:
            self._update(conn, pid, now, expect=(HANDOFF_PREPARED,), status=MANUAL_REPORTED, post_url=post_url or None)
            self.store.event(conn, "MANUAL_POST_REPORTED", _actor(op), now, draft_id=pub["draft_id"],
                             detail={"publication_id": pid, "post_url": post_url or None, "verified": False})
        return self.publication(pid)

    def cancel_handoff(self, pid: str, operator: Optional[str] = None) -> Dict[str, Any]:
        from backend.app.community.service import _actor, _validate_operator
        op = _validate_operator(operator)
        pub = self.publication(pid)
        if pub["mode"] != "manual" or pub["status"] != HANDOFF_PREPARED:
            raise S.Conflict("only a prepared manual handoff can be cancelled")
        now = self.c.clock()
        with self.store.tx() as conn:
            self._update(conn, pid, now, expect=(HANDOFF_PREPARED,), status=HANDOFF_CANCELLED)
            self.store.event(conn, "MANUAL_HANDOFF_CANCELLED", _actor(op), now, draft_id=pub["draft_id"],
                             detail={"publication_id": pid})
        return self.publication(pid)

    # -- reads ----------------------------------------------------------------------------------------------------
    def publication(self, pid: str) -> Dict[str, Any]:
        if not isinstance(pid, str) or not re.fullmatch(r"cpub_[0-9a-f]{16}", pid) or not self._ready():
            raise S.NotFound("publication not found")
        with self.store.read() as conn:
            return self._row(conn, pid)

    def publications(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._ready():
            return []
        with self.store.read() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM community_publications ORDER BY created_at_utc DESC LIMIT ?",
                                              (max(1, min(int(limit), 200)),))]
            return [self._row(conn, i) for i in ids]

    def status(self) -> Dict[str, Any]:
        cap = self.latest_capability()
        pubs = self.publications(100)
        latest: Dict[str, Dict[str, Any]] = {}
        for p in sorted(pubs, key=lambda x: (x.get("updated_at_utc") or "", x.get("created_at_utc") or "")):
            latest[p["draft_id"]] = p
        approved_all = self.c.list_drafts(status=S.APPROVED, limit=50)
        approved = [d for d in approved_all if lifecycle(d, latest.get(d["id"]))["state"] == LC_APPROVED]
        gates = self.gates()
        api_ready = all(g["ok"] for g in gates)
        counts = {s: 0 for s in LIFECYCLE_STATES}
        for d in approved_all:
            counts[lifecycle(d, latest.get(d["id"]))["state"]] += 1
        api = [p for p in pubs if p["mode"] == "api"]
        return {
            "capability": cap, "publishing_enabled": self._enabled(), "api_publishing_ready": api_ready, "gates": gates,
            "approved_awaiting": [{"id": d["id"], "post_date": d["post_date"], "topic_title": d["topic_title"],
                                   "version": d["version"], "format_label": d.get("format_label")} for d in approved],
            "lifecycle_counts": counts,
            "queue": {
                "queued": [p for p in api if p["status"] == QUEUED],
                "publishing": [p for p in api if p["status"] in IN_FLIGHT + (UNCERTAIN,)],
                "published": [p for p in api if p["status"] == PUBLISHED],
                "failed": [p for p in api if p["status"] == FAILED],
                "retry_pending": [p for p in api if p["status"] == RETRY_PENDING],
                "requires_reauthorization": [p for p in api if p["status"] == REQUIRES_REAUTH],
                "cancelled": [p for p in api if p["status"] == CANCELLED][:10],
                "manual_handoffs": [p for p in pubs if p["mode"] == "manual"],
            },
            "retry_policy": {"max_attempts": MAX_ATTEMPTS, "backoff_minutes": [int(b.total_seconds() // 60) for b in RETRY_BACKOFF],
                             "uncertain_is_never_retried": True},
            "published": [p for p in pubs if p["status"] == PUBLISHED],
            "attention": [p for p in pubs if p["status"] in (UNCERTAIN, FAILED, REQUIRES_REAUTH, RETRY_PENDING)],
            "handoffs": [p for p in pubs if p["mode"] == "manual"],
            "scheduling": {"api_can_schedule": False, "local_queue": True,
                           "note": "posts.insert publishes publicly and immediately; the API cannot schedule. A post can "
                                   "be authorized for a later time: the local queue sends it then, after re-checking every "
                                   "gate (only while API publishing is enabled). In the YouTube interface: Create > Create "
                                   "post > arrow next to Post > Schedule."},
            "outcomes": list(OUTCOMES),
        }
