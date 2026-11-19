"""
YouTube Analytics API (v2) collection for published videos (Stage 3.30).

OAuth: reuses the existing client and channel connection. Uploads keep exactly their scopes (youtube.upload +
youtube.readonly): google-auth sends the requested scopes on every token refresh, so adding an ungranted scope to
the upload client would break uploads. Analytics therefore uses the token ONLY when the granted scopes already
include yt-analytics.readonly; otherwise it reports `reauthorization_required` and makes no API call. The
reauthorization is an incremental consent (existing scopes + analytics, include_granted_scopes) started by the
operator from the dashboard. Read-only scope only; tokens are never logged.

Metrics (Top-videos report, dimension=video; verified against developers.google.com/youtube/analytics/channel_reports
on 2026-11-20): views, engagedViews, estimatedMinutesWatched, averageViewDuration, averageViewPercentage are
supported in every top-videos variant; likes, comments, shares, subscribersGained only in some variants, so they are
requested separately and recorded as `unavailable` when the API rejects them. A missing metric is never stored as 0:
statuses are available / available_zero / not_yet_populated / unavailable / request_failed.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
CORE_METRICS = ["views", "engagedViews", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage"]
ENGAGEMENT_METRICS = ["likes", "comments", "shares", "subscribersGained"]
ALL_METRICS = CORE_METRICS + ENGAGEMENT_METRICS
MAX_IDS_PER_QUERY = 50
DAILY_WINDOW_DAYS = 28          # collected daily while young; weekly afterwards


def granted_scopes(oauth_manager) -> List[str]:
    """Scopes actually granted to the stored token (names only)."""
    try:
        from backend.app.core.secure_storage import read_protected_file
        data = read_protected_file(oauth_manager.token_file)
        if data is None:
            return []
        return list(json.loads(data.decode("utf-8")).get("scopes") or [])
    except Exception:
        return []


def analytics_credentials(oauth_manager):
    """Credentials limited to the GRANTED scopes, or None when analytics is not authorized."""
    scopes = granted_scopes(oauth_manager)
    if ANALYTICS_SCOPE not in scopes:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from backend.app.core.secure_storage import read_protected_file
    info = json.loads(read_protected_file(oauth_manager.token_file).decode("utf-8"))
    creds = Credentials.from_authorized_user_info(info, scopes)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        oauth_manager.save_credentials(creds, event="refresh")
    return creds


def classify_error(exc: BaseException) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = (str(exc) + " " + str(getattr(exc, "content", b"") or b"")).lower()
    if status == 403 and ("insufficient" in text or "scope" in text):
        return "insufficient_scope"
    if status == 401 or "invalid_grant" in text:
        return "auth"
    if status == 429 or "quota" in text or "ratelimit" in text:
        return "quota_or_rate_limit"
    if status == 400:
        return "incompatible_query"
    if status and int(status) >= 500:
        return "server_error"
    return "request_failed"


class PerformanceStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS video_performance (
                    video_id TEXT NOT NULL,
                    collected_date TEXT NOT NULL,
                    collected_at_utc TEXT NOT NULL,
                    job_id TEXT, topic TEXT, topic_category TEXT, creative_angle TEXT, script_fingerprint TEXT,
                    hook TEXT, duration_sec REAL, renderer TEXT, visual_engine TEXT, visual_composition TEXT,
                    thumbnail TEXT, published_at_utc TEXT, window_start TEXT, window_end TEXT,
                    metrics_json TEXT, metric_status_json TEXT, error_category TEXT,
                    PRIMARY KEY (video_id, collected_date)
                )""")
            conn.commit()
        finally:
            conn.close()

    def upsert(self, row: Dict[str, Any]) -> None:
        cols = ["video_id", "collected_date", "collected_at_utc", "job_id", "topic", "topic_category", "creative_angle",
                "script_fingerprint", "hook", "duration_sec", "renderer", "visual_engine", "visual_composition",
                "thumbnail", "published_at_utc", "window_start", "window_end", "metrics_json", "metric_status_json",
                "error_category"]
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute(f"INSERT OR REPLACE INTO video_performance ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [row.get(c) for c in cols])
            conn.commit()
        finally:
            conn.close()

    def last_collected(self) -> Dict[str, str]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            return dict(conn.execute("SELECT video_id, MAX(collected_date) FROM video_performance GROUP BY video_id"))
        finally:
            conn.close()

    def latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""SELECT p.* FROM video_performance p JOIN
                (SELECT video_id, MAX(collected_date) d FROM video_performance GROUP BY video_id) m
                ON p.video_id = m.video_id AND p.collected_date = m.d ORDER BY p.published_at_utc DESC LIMIT ?""",
                                (limit,)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
            d["metric_status"] = json.loads(d.pop("metric_status_json") or "{}")
            out.append(d)
        return out


def metric_values(response: Optional[Dict[str, Any]], requested: List[str], video_ids: List[str],
                  error: Optional[str] = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per video: {metric: {"value": v|None, "status": ...}}. Missing data is never converted to zero."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {v: {} for v in video_ids}
    if error is not None:
        status = "unavailable" if error == "incompatible_query" else "request_failed"
        for v in video_ids:
            for m in requested:
                out[v][m] = {"value": None, "status": status, "error": error}
        return out
    headers = [h.get("name") for h in (response or {}).get("columnHeaders", [])]
    rows = {r[headers.index("video")]: r for r in (response or {}).get("rows", []) or []} if "video" in headers else {}
    for v in video_ids:
        for m in requested:
            if m not in headers:
                out[v][m] = {"value": None, "status": "unavailable"}
            elif v not in rows:
                out[v][m] = {"value": None, "status": "not_yet_populated"}
            else:
                val = rows[v][headers.index(m)]
                out[v][m] = {"value": val, "status": "available_zero" if val in (0, 0.0) else "available"}
    return out


class AnalyticsCollector:
    def __init__(self, oauth_manager=None, db_path: Optional[str] = None,
                 service_factory: Optional[Callable[[Any], Any]] = None, today: Optional[date] = None):
        if oauth_manager is None:
            from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
            oauth_manager = YouTubeOAuthManager()
        self.oauth = oauth_manager
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        self.db_path = db_path
        self.store = PerformanceStore(db_path)
        self.service_factory = service_factory or self._build_service
        self.today = today or datetime.now(timezone.utc).date()

    @staticmethod
    def _build_service(creds):
        from googleapiclient.discovery import build
        return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

    def status(self) -> Dict[str, Any]:
        scopes = granted_scopes(self.oauth)
        return {"implemented": True, "scope": ANALYTICS_SCOPE, "scope_granted": ANALYTICS_SCOPE in scopes,
                "granted_scopes": scopes,
                "state": "authorized" if ANALYTICS_SCOPE in scopes else "reauthorization_required",
                "action": None if ANALYTICS_SCOPE in scopes else
                "Dashboard > YouTube Analytics > Authorize analytics (one-time consent; uploads are not affected)"}

    def _published_videos(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
        try:
            rows = conn.execute("SELECT id, youtube_video_id, video_path, thumbnail_path, topic_title, upload_timestamp, "
                                "scheduled_timestamp, upload_status FROM publishing_ledger WHERE youtube_video_id IS NOT NULL "
                                "AND youtube_video_id != ''").fetchall()
        finally:
            conn.close()
        out = []
        for lid, vid, vpath, tpath, topic, up_ts, sched, st in rows:
            if (st or "").lower() not in ("uploaded_public", "published", "publication_scheduled", "uploaded"):
                continue
            pub = sched or up_ts
            out.append({"ledger_id": lid, "video_id": vid, "video_path": vpath or "", "thumbnail": tpath,
                        "topic": topic, "published_at_utc": pub})
        return out

    @staticmethod
    def _context(video_path: str) -> Dict[str, Any]:
        """Production context from the per-video render manifest (Stage 3.29+); older videos have none."""
        ctx: Dict[str, Any] = {}
        try:
            prod_dir = Path(video_path).parent.parent
            ctx["job_id"] = prod_dir.name
            man = next(iter((prod_dir / "manifests").glob("render_manifest_*.json")), None)
            if man:
                m = json.loads(man.read_text(encoding="utf-8"))
                ctx.update(renderer=m.get("renderer"), visual_engine=m.get("visual_engine"),
                           duration_sec=(m.get("duration") or {}).get("measured_sec"),
                           script_fingerprint=(m.get("script_fingerprint") or {}).get("sha256"),
                           visual_composition=json.dumps(m.get("scene_types") or []),
                           creative_angle=json.dumps({k: (m.get("creative_direction") or {}).get(k)
                                                      for k in ("primary_environment", "pacing", "palette_mood")}))
            pm = prod_dir / "production_manifest.json"
            if pm.exists():
                p = json.loads(pm.read_text(encoding="utf-8"))
                ctx.setdefault("topic_category", p.get("category") or (p.get("topic") or {}).get("category"))
                ctx.setdefault("hook", p.get("winning_hook") or (p.get("story") or {}).get("hook"))
        except Exception:
            pass
        return ctx

    def due_videos(self) -> List[Dict[str, Any]]:
        last = self.store.last_collected()
        due = []
        for v in self._published_videos():
            try:
                pub = datetime.fromisoformat(str(v["published_at_utc"]).replace("Z", "+00:00")).date()
            except (TypeError, ValueError):
                pub = None
            if pub and pub > self.today:
                continue                                   # scheduled, not public yet
            prev = last.get(v["video_id"])
            age = (self.today - pub).days if pub else 999
            every = 1 if age <= DAILY_WINDOW_DAYS else 7
            if prev and (self.today - date.fromisoformat(prev)).days < every:
                continue                                   # no repeated requests for unchanged history
            v["published_date"] = pub
            due.append(v)
        return due

    def collect(self) -> Dict[str, Any]:
        st = self.status()
        if not st["scope_granted"]:
            return {"state": "reauthorization_required", "collected": 0, "api_calls": 0}
        videos = self.due_videos()
        if not videos:
            return {"state": "nothing_due", "collected": 0, "api_calls": 0}
        try:
            creds = analytics_credentials(self.oauth)
            service = self.service_factory(creds)
        except Exception as exc:
            return {"state": "auth_failed", "error_category": classify_error(exc), "collected": 0, "api_calls": 0}
        calls, collected = 0, 0
        start = min((v["published_date"] or self.today) for v in videos).isoformat()
        end = self.today.isoformat()
        for i in range(0, len(videos), MAX_IDS_PER_QUERY):
            chunk = videos[i:i + MAX_IDS_PER_QUERY]
            ids = [v["video_id"] for v in chunk]
            merged: Dict[str, Dict[str, Any]] = {v: {} for v in ids}
            err_cat = None
            for group in (CORE_METRICS, ENGAGEMENT_METRICS):
                if err_cat in ("insufficient_scope", "auth"):   # no point sending more: record as failed
                    for v in ids:
                        merged[v].update(metric_values(None, group, ids, error=err_cat)[v])
                    continue
                try:
                    calls += 1
                    # top-videos reports need a sort; "views" is requested with every group so it can sort
                    query_metrics = group if "views" in group else ["views"] + group
                    resp = service.reports().query(ids="channel==MINE", startDate=start, endDate=end,
                                                   metrics=",".join(query_metrics), dimensions="video",
                                                   filters="video==" + ",".join(ids), sort="-views",
                                                   maxResults=min(200, len(ids))).execute()
                    vals = metric_values(resp, group, ids)
                except Exception as exc:
                    err_cat = classify_error(exc)
                    vals = metric_values(None, group, ids, error=err_cat)
                    logger.warning("[ANALYTICS] query (%s) failed: %s", ",".join(group), err_cat)
                for v in ids:
                    merged[v].update(vals[v])
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for v in chunk:
                ctx = self._context(v["video_path"])
                self.store.upsert({**ctx, "video_id": v["video_id"], "collected_date": self.today.isoformat(),
                                   "collected_at_utc": now, "topic": v["topic"], "thumbnail": v["thumbnail"],
                                   "published_at_utc": v["published_at_utc"], "window_start": start, "window_end": end,
                                   "metrics_json": json.dumps({m: s.get("value") for m, s in merged[v["video_id"]].items()}),
                                   "metric_status_json": json.dumps({m: s.get("status") for m, s in merged[v["video_id"]].items()}),
                                   "error_category": err_cat})
                collected += 1
        return {"state": "collected", "collected": collected, "api_calls": calls, "window": [start, end]}


def authorize_analytics(oauth_manager=None, *, open_browser: bool = True, timeout_sec: int = 300) -> Dict[str, Any]:
    """Incremental consent: existing YouTube scopes + yt-analytics.readonly on the SAME OAuth client and channel.
    Must be started by the operator (it opens Google's consent page in the local browser)."""
    from backend.app.services.youtube_publishing_engine import YOUTUBE_SCOPES, YouTubeOAuthManager
    mgr = YouTubeOAuthManager(scopes=list(YOUTUBE_SCOPES) + [ANALYTICS_SCOPE])
    if oauth_manager is not None:
        mgr.token_file, mgr.client_secrets_file = oauth_manager.token_file, oauth_manager.client_secrets_file
    ok, channel, err = mgr.start_interactive_auth(open_browser=open_browser, timeout_sec=timeout_sec)
    scopes = granted_scopes(mgr)
    return {"success": bool(ok and ANALYTICS_SCOPE in scopes), "granted_scopes": scopes,
            "upload_scopes_preserved": all(s in scopes for s in YOUTUBE_SCOPES), "error": err}
