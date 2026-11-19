"""
YouTube Data API quota model and request ledger (Stage 3.30).

Why this exists: the old tracker added a fixed 1,600 units per upload and 50 per thumbnail to one counter keyed by
the UTC date and refused uploads once 6 x 1,650 = 9,900 was reached. Google changed the model:

* 2025-12-04: videos.insert cost reduced from ~1,600 to ~100 units (revision history).
* 2026-06-01: videos.insert and search.list moved to their OWN quota buckets (100 calls/day each by default,
  1 unit per call); every other method still uses the shared 10,000-unit/day bucket.
* 2026-06-03: videos.batchGetStats added, 1 unit in its own bucket (10,000/day).
* Daily quotas reset at midnight Pacific Time.

Sources (verified 2026-11-20): https://developers.google.com/youtube/v3/determine_quota_cost and
https://developers.google.com/youtube/v3/revision_history

This ledger records requests that were actually SENT to Google (one row per method call; the chunks of a
resumable upload are one videos.insert call). It is a LOCAL ESTIMATE. Google's own usage numbers are only
available in the Cloud Console / Cloud Monitoring, which the existing OAuth token cannot read, so they are never
presented as verified here. Credentials, tokens and request payloads are never stored.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
IST = ZoneInfo("Asia/Kolkata")
COST_SOURCE = "developers.google.com/youtube/v3/determine_quota_cost + revision_history (verified 2026-11-20)"

GENERAL = "general"
UPLOADS = "videos.insert"
SEARCH = "search.list"
BATCH_STATS = "videos.batchGetStats"

# Default daily limits per bucket (project defaults; Google may grant more after an audit).
BUCKETS: Dict[str, Dict[str, Any]] = {
    GENERAL: {"label": "General queries", "daily_limit": 10_000, "unit": "units"},
    UPLOADS: {"label": "Video uploads (videos.insert)", "daily_limit": 100, "unit": "calls"},
    SEARCH: {"label": "Search queries (search.list)", "daily_limit": 100, "unit": "calls"},
    BATCH_STATS: {"label": "Video batch get stats", "daily_limit": 10_000, "unit": "units"},
}

# method -> (bucket, cost). Only methods with a documented cost; unknown methods are recorded as unverified.
METHOD_COSTS: Dict[str, tuple] = {
    "videos.insert": (UPLOADS, 1),
    "search.list": (SEARCH, 1),
    "videos.batchGetStats": (BATCH_STATS, 1),
    "videos.list": (GENERAL, 1),
    "videos.update": (GENERAL, 50),
    "videos.delete": (GENERAL, 50),
    "videos.getRating": (GENERAL, 1),
    "videos.rate": (GENERAL, 50),
    "thumbnails.set": (GENERAL, 50),
    "channels.list": (GENERAL, 1),
    "playlistItems.list": (GENERAL, 1),
    "playlists.list": (GENERAL, 1),
}


def method_cost(method: str) -> Dict[str, Any]:
    if method in METHOD_COSTS:
        bucket, cost = METHOD_COSTS[method]
        return {"bucket": bucket, "cost": cost, "verified": True, "source": COST_SOURCE}
    return {"bucket": GENERAL, "cost": 1, "verified": False, "source": "unknown method: assumed 1 unit (estimate)"}


def pacific_day(at: Optional[datetime] = None) -> str:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(PACIFIC).strftime("%Y-%m-%d")


def next_reset(at: Optional[datetime] = None) -> datetime:
    """Next midnight Pacific Time (DST-aware), as an aware datetime."""
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    local = at.astimezone(PACIFIC)
    d = local.date() + timedelta(days=1)
    return datetime(d.year, d.month, d.day, tzinfo=PACIFIC)   # wall-clock midnight; zoneinfo applies PST/PDT


def classify_http_error(exc: BaseException) -> Dict[str, Any]:
    """HTTP status and error category of a googleapiclient error, without its message body."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    content = getattr(exc, "content", b"") or b""
    if isinstance(content, bytes):
        content = content.decode("utf-8", "ignore")
    # the reason ("quotaExceeded", ...) is in the response body, not always in str(exc)
    text = " ".join((str(exc), str(content), str(getattr(exc, "error_details", "") or ""))).lower()
    if "quotaexceeded" in text or "dailylimitexceeded" in text or (status == 403 and "quota" in text):
        cat = "quota_exceeded"
    elif "uploadlimitexceeded" in text:
        cat = "upload_limit_exceeded"
    elif "ratelimitexceeded" in text or "userratelimitexceeded" in text or status == 429:
        cat = "rate_limited"
    elif status == 401:
        cat = "auth"
    elif status == 403 and "insufficient" in text:
        cat = "insufficient_scope"
    elif status in (400, 404, 409):
        cat = "client_error"
    elif status and int(status) >= 500:
        cat = "server_error"
    elif status is None:
        cat = "transport"
    else:
        cat = "http_error"
    return {"http_status": status, "error_category": cat}


class QuotaLedger:
    """Per-request ledger in SQLite (same database as the publishing ledger)."""

    def __init__(self, db_path: str, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db_path = db_path
        self.clock = clock
        self._lock = threading.Lock()
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def _init(self) -> None:
        conn = self._conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS youtube_api_requests (
                    correlation_id TEXT PRIMARY KEY,
                    ts_utc TEXT NOT NULL,
                    pt_day TEXT NOT NULL,
                    method TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    cost_verified INTEGER NOT NULL,
                    cost_source TEXT,
                    purpose TEXT,
                    job_id TEXT,
                    video_id TEXT,
                    attempt INTEGER,
                    retry_reason TEXT,
                    outcome TEXT,
                    http_status INTEGER,
                    error_category TEXT
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_yar_day ON youtube_api_requests(pt_day, bucket)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS youtube_quota_blocks (
                    bucket TEXT PRIMARY KEY,
                    blocked_at_utc TEXT NOT NULL,
                    blocked_until_utc TEXT NOT NULL,
                    reason TEXT,
                    operation TEXT,
                    job_id TEXT,
                    video_id TEXT
                )""")
            conn.commit()
        finally:
            conn.close()

    # ---- recording -------------------------------------------------------------------------------------------
    def record(self, method: str, *, purpose: str, outcome: str, job_id: Optional[str] = None,
               video_id: Optional[str] = None, attempt: int = 1, retry_reason: Optional[str] = None,
               http_status: Optional[int] = None, error_category: Optional[str] = None,
               correlation_id: Optional[str] = None) -> str:
        """Records ONE request that was sent to Google. Replaying the same correlation_id is a no-op."""
        cid = correlation_id or uuid.uuid4().hex
        info = method_cost(method)
        now = self.clock()
        try:   # bookkeeping must never break the API operation it describes
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO youtube_api_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, now.strftime("%Y-%m-%dT%H:%M:%SZ"), pacific_day(now), method, info["bucket"],
                         info["cost"], 1 if info["verified"] else 0, info["source"], purpose, job_id, video_id,
                         attempt, retry_reason, outcome, http_status, error_category))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            logger.warning("[QUOTA] Could not record %s request: %s", method, type(exc).__name__)
        if error_category in ("quota_exceeded", "upload_limit_exceeded"):
            self.mark_exhausted(info["bucket"], reason=error_category, operation=purpose, job_id=job_id,
                                video_id=video_id)
        return cid

    def execute(self, request: Any, method: str, *, purpose: str, job_id: Optional[str] = None,
                video_id: Optional[str] = None, attempt: int = 1, retry_reason: Optional[str] = None) -> Any:
        """Runs request.execute() and records it. A request that raises before reaching Google (no HTTP
        response, e.g. local validation) is recorded as a transport failure only if it carried a response."""
        try:
            resp = request.execute()
        except Exception as exc:
            c = classify_http_error(exc)
            if c["http_status"] is not None:   # Google answered: the call counts
                self.record(method, purpose=purpose, outcome="error", job_id=job_id, video_id=video_id,
                            attempt=attempt, retry_reason=retry_reason, **c)
            raise
        self.record(method, purpose=purpose, outcome="ok", job_id=job_id, video_id=video_id, attempt=attempt,
                    retry_reason=retry_reason, http_status=200)
        return resp

    # ---- exhaustion ------------------------------------------------------------------------------------------
    def mark_exhausted(self, bucket: str, *, reason: str, operation: Optional[str] = None,
                       job_id: Optional[str] = None, video_id: Optional[str] = None) -> None:
        now = self.clock()
        until = next_reset(now).astimezone(timezone.utc)
        conn = self._conn()
        try:
            conn.execute("INSERT OR REPLACE INTO youtube_quota_blocks VALUES (?,?,?,?,?,?,?)",
                         (bucket, now.strftime("%Y-%m-%dT%H:%M:%SZ"), until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          reason, operation, job_id, video_id))
            conn.commit()
        finally:
            conn.close()
        logger.warning("[QUOTA] Google reported %s for bucket %s; blocked until %s (next Pacific midnight).",
                       reason, bucket, until.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"))

    def blocked(self, bucket: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM youtube_quota_blocks WHERE bucket=?", (bucket,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        until = datetime.strptime(row[2], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if self.clock() >= until:
            return None
        return {"bucket": row[0], "blocked_at_utc": row[1], "blocked_until_utc": row[2], "reason": row[3],
                "operation": row[4], "job_id": row[5], "video_id": row[6],
                "blocked_until_ist": until.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")}

    # ---- reads -----------------------------------------------------------------------------------------------
    def usage(self, day: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        day = day or pacific_day(self.clock())
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT bucket, SUM(cost), COUNT(*), MIN(cost_verified) FROM youtube_api_requests "
                "WHERE pt_day=? GROUP BY bucket", (day,)).fetchall()
        finally:
            conn.close()
        out = {}
        for b, meta in BUCKETS.items():
            used = next(((r[1], r[2], r[3]) for r in rows if r[0] == b), (0, 0, 1))
            out[b] = {**meta, "used_estimate": int(used[0] or 0), "requests": int(used[1] or 0),
                      "costs_verified": bool(used[2]), "remaining_estimate": max(0, meta["daily_limit"] - int(used[0] or 0)),
                      "blocked": self.blocked(b)}
        return out

    def check(self, bucket: str, cost: int) -> Dict[str, Any]:
        """May an operation that needs `cost` from `bucket` run now? Uses Google's own exhaustion signal first,
        then the local estimate. Never blocks on another bucket."""
        blk = self.blocked(bucket)
        if blk:
            return {"allowed": False, "reason": f"Google reported {blk['reason']} for {bucket}; resets {blk['blocked_until_ist']}",
                    "bucket": bucket, "blocked": blk}
        u = self.usage()[bucket]
        if u["used_estimate"] + cost > u["daily_limit"]:
            return {"allowed": False, "bucket": bucket,
                    "reason": (f"Local estimate {u['used_estimate']}/{u['daily_limit']} {u['unit']} in {bucket} "
                               f"(Pacific day); resets {next_reset(self.clock()).astimezone(IST).strftime('%Y-%m-%d %H:%M IST')}")}
        return {"allowed": True, "bucket": bucket}

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT ts_utc, method, bucket, cost, purpose, job_id, video_id, attempt, outcome, "
                                "http_status, error_category, cost_verified FROM youtube_api_requests "
                                "ORDER BY ts_utc DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        keys = ("ts_utc", "method", "bucket", "cost", "purpose", "job_id", "video_id", "attempt", "outcome",
                "http_status", "error_category", "cost_verified")
        return [dict(zip(keys, r)) for r in rows]

    def top_consumers(self, day: Optional[str] = None) -> List[Dict[str, Any]]:
        day = day or pacific_day(self.clock())
        conn = self._conn()
        try:
            rows = conn.execute("SELECT method, purpose, bucket, COUNT(*), SUM(cost) FROM youtube_api_requests "
                                "WHERE pt_day=? GROUP BY method, purpose, bucket ORDER BY SUM(cost) DESC", (day,)).fetchall()
        finally:
            conn.close()
        return [{"method": r[0], "purpose": r[1], "bucket": r[2], "requests": r[3], "units": r[4]} for r in rows]

    def summary(self) -> Dict[str, Any]:
        now = self.clock()
        reset = next_reset(now)
        conn = self._conn()
        try:
            last = conn.execute("SELECT MAX(ts_utc) FROM youtube_api_requests").fetchone()[0]
        finally:
            conn.close()
        return {
            "pacific_day": pacific_day(now),
            "next_reset_pt": reset.strftime("%Y-%m-%d %H:%M %Z"),
            "next_reset_ist": reset.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"),
            "buckets": self.usage(),
            "top_consumers": self.top_consumers(),
            "last_request_utc": last,
            "source": "local estimate from requests sent by CrazzyDocs (not Google-reported)",
            "google_reported": {"available": False,
                                "reason": ("Google's usage is shown in Cloud Console > APIs & Services > YouTube Data API v3 > "
                                           "Quotas. Reading it programmatically needs Cloud Monitoring access, which the "
                                           "existing YouTube OAuth token does not have.")},
            "cost_source": COST_SOURCE,
        }
