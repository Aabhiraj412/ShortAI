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
MAX_CALLS_PER_RUN = 120         # Stage 3.35: bounded API use per run (the YouTube Analytics API has its own quota)


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


# -- Stage 3.35: analytics authentication -------------------------------------------------------------------------
# Root cause of the repeated "disconnections": analytics trusted the token file's stored scope list, and the upload
# client's routine refresh rewrote that list with only its own two scopes (fixed in save_credentials). In addition,
# analytics now:
#   * loads the shared refresh token with ONLY yt-analytics.readonly and refreshes IN MEMORY (never writes the shared
#     token file, so it can never narrow the upload client's credentials);
#   * verifies the grant with Google instead of trusting the stored list, and records a confirmed grant;
#   * classifies failures: invalid_grant -> REVOKED, invalid_scope -> AUTHENTICATION_REQUIRED, network -> EXPIRED
#     (retried with backoff), anything else -> ERROR;
#   * serialises refreshes in-process and caches the access token until shortly before it expires.
CONNECTED, REFRESHING, EXPIRED, REVOKED, AUTH_REQUIRED, AUTH_ERROR = (
    "CONNECTED", "REFRESHING", "EXPIRED", "REVOKED", "AUTHENTICATION_REQUIRED", "ERROR")
AUTH_STATE_KEY = "youtube_analytics_auth_state"
SYNC_STATE_KEY = "youtube_analytics_sync_state"
REFRESH_ATTEMPTS = 3
_refresh_lock = __import__("threading").Lock()
_cached: Dict[str, Any] = {"creds": None, "token_file": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_state(key: str) -> Dict[str, Any]:
    try:
        from backend.app.services import control_center_service as cc
        raw = cc._read_setting(key)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _write_state(key: str, state: Dict[str, Any]) -> None:
    try:
        from backend.app.services import control_center_service as cc
        cc._write_settings({key: json.dumps(state, sort_keys=True)})
    except Exception:
        logger.debug("Could not persist %s", key)


def record_auth_state(state: str, *, reason: str = "", ok: bool = False) -> Dict[str, Any]:
    st = _read_state(AUTH_STATE_KEY)
    if st.get("state") != state:
        st["changed_at_utc"] = _now_iso()
        st.setdefault("transitions", [])
        st["transitions"] = (st["transitions"] + [{"at_utc": _now_iso(), "from": st.get("state"), "to": state,
                                                   "reason": reason[:200]}])[-20:]
    st.update(state=state, reason=reason[:300], checked_at_utc=_now_iso())
    if ok:
        st["last_success_utc"] = st["checked_at_utc"]
    _write_state(AUTH_STATE_KEY, st)
    return st


def granted_scopes(oauth_manager) -> List[str]:
    """Scope NAMES recorded as granted for the stored token (never token values)."""
    try:
        from backend.app.core.secure_storage import read_protected_file
        data = read_protected_file(oauth_manager.token_file)
        if data is None:
            return []
        return list(json.loads(data.decode("utf-8")).get("scopes") or [])
    except Exception:
        return []


def _classify_refresh_error(exc: BaseException) -> str:
    text = str(exc).lower()
    name = type(exc).__name__
    if "invalid_grant" in text:
        return REVOKED
    if "invalid_scope" in text or "insufficient" in text:
        return AUTH_REQUIRED
    if name in ("TransportError", "ConnectionError", "TimeoutError") or "transport" in name.lower() or \
            isinstance(exc, (ConnectionError, TimeoutError, OSError)) and name != "RefreshError":
        return EXPIRED
    return AUTH_ERROR


def analytics_credentials(oauth_manager, *, sleep: Callable[[float], None] = None, force: bool = False):
    """Valid analytics-only credentials, or raises. Never writes the shared token file."""
    import time as _time
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from backend.app.core.secure_storage import read_protected_file
    sleep = sleep or _time.sleep
    with _refresh_lock:
        c = _cached["creds"]
        if (not force and c is not None and _cached["token_file"] == oauth_manager.token_file and c.valid
                and getattr(c, "expiry", None) and (c.expiry - datetime.utcnow()).total_seconds() > 120):
            return c
        raw = read_protected_file(oauth_manager.token_file)
        if raw is None:
            record_auth_state(AUTH_REQUIRED, reason="no stored YouTube credentials")
            raise PermissionError("no stored YouTube credentials")
        info = json.loads(raw.decode("utf-8"))
        if not info.get("refresh_token"):
            record_auth_state(AUTH_REQUIRED, reason="stored credentials have no refresh token")
            raise PermissionError("no refresh token")
        creds = Credentials.from_authorized_user_info(info, [ANALYTICS_SCOPE])
        creds.token = None                        # the stored access token belongs to the upload scopes
        last_exc: Optional[BaseException] = None
        for attempt in range(REFRESH_ATTEMPTS):
            try:
                creds.refresh(Request())
                break
            except Exception as exc:              # never log the exception text (may echo request data)
                last_exc = exc
                kind = _classify_refresh_error(exc)
                if kind != EXPIRED or attempt == REFRESH_ATTEMPTS - 1:
                    record_auth_state(kind, reason=f"token refresh failed ({type(exc).__name__}"
                                                   f"{': ' + kind.lower() if kind in (REVOKED, AUTH_REQUIRED) else ''})")
                    raise
                sleep(2.0 ** attempt)
        granted = set(creds.granted_scopes or [ANALYTICS_SCOPE])
        if ANALYTICS_SCOPE not in granted:
            record_auth_state(AUTH_REQUIRED, reason="Google did not grant yt-analytics.readonly")
            raise PermissionError("analytics scope not granted")
        try:
            oauth_manager.record_granted_scope(ANALYTICS_SCOPE)    # records the confirmed grant (scope name only)
        except Exception:
            pass
        _cached.update(creds=creds, token_file=oauth_manager.token_file)
        record_auth_state(CONNECTED, reason="analytics token refreshed", ok=True)
        return creds


def auth_status(oauth_manager, *, verify: bool = False) -> Dict[str, Any]:
    """The persisted analytics auth state; ``verify`` performs one in-memory refresh to confirm it."""
    if verify:
        try:
            analytics_credentials(oauth_manager, force=True)
        except Exception:
            pass
    st = _read_state(AUTH_STATE_KEY)
    scopes = granted_scopes(oauth_manager)
    state = st.get("state") or (CONNECTED if ANALYTICS_SCOPE in scopes else AUTH_REQUIRED)
    action = None
    if state in (REVOKED, AUTH_REQUIRED):
        action = ("Operator authorization required: one-time consent for yt-analytics.readonly on the existing "
                  "OAuth client (uploads unaffected).")
    return {"state": state, "reason": st.get("reason"), "checked_at_utc": st.get("checked_at_utc"),
            "last_success_utc": st.get("last_success_utc"), "transitions": st.get("transitions", [])[-8:],
            "scope": ANALYTICS_SCOPE, "scope_recorded": ANALYTICS_SCOPE in scopes, "action": action}


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
            cols = {r[1] for r in conn.execute("PRAGMA table_info(video_performance)")}
            if "window_kind" not in cols:            # Stage 3.35, additive
                conn.execute("ALTER TABLE video_performance ADD COLUMN window_kind TEXT")
            conn.commit()
        finally:
            conn.close()

    def upsert(self, row: Dict[str, Any]) -> None:
        cols = ["video_id", "collected_date", "collected_at_utc", "job_id", "topic", "topic_category", "creative_angle",
                "script_fingerprint", "hook", "duration_sec", "renderer", "visual_engine", "visual_composition",
                "thumbnail", "published_at_utc", "window_start", "window_end", "metrics_json", "metric_status_json",
                "error_category", "window_kind"]
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
            return dict(conn.execute("SELECT video_id, MAX(collected_date) FROM video_performance "
                                     "WHERE collected_date NOT LIKE 'D7%' GROUP BY video_id"))
        finally:
            conn.close()

    def d7_done(self) -> set:
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            return {r[0] for r in conn.execute("SELECT video_id FROM video_performance WHERE window_kind='first_7_days' "
                                               "AND error_category IS NULL")}
        finally:
            conn.close()

    def latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""SELECT p.* FROM video_performance p JOIN
                (SELECT video_id, MAX(collected_date) d FROM video_performance WHERE collected_date NOT LIKE 'D7%'
                 GROUP BY video_id) m
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
        st = auth_status(self.oauth)
        return {"implemented": True, "scope": ANALYTICS_SCOPE, "scope_granted": st["state"] == CONNECTED or st["scope_recorded"],
                "granted_scopes": granted_scopes(self.oauth), "auth": st,
                "state": "authorized" if st["state"] == CONNECTED else st["state"].lower(),
                "action": st["action"], "sync": _read_state(SYNC_STATE_KEY)}

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

    def collect(self, *, max_calls: int = MAX_CALLS_PER_RUN) -> Dict[str, Any]:
        """Lifetime-to-date snapshot for due videos, plus the fixed first-7-days window (with retention and traffic
        sources) once per video aged 7+ days. Idempotent per day; bounded API calls; sync state persisted."""
        sync = _read_state(SYNC_STATE_KEY)
        sync["last_attempt_utc"] = _now_iso()
        videos = self.due_videos()
        d7_due = self.d7_due_videos()
        if not videos and not d7_due:
            sync.update(last_result="nothing_due")
            _write_state(SYNC_STATE_KEY, sync)
            return {"state": "nothing_due", "collected": 0, "api_calls": 0}
        try:
            creds = analytics_credentials(self.oauth)
            service = self.service_factory(creds)
        except Exception as exc:
            st = _read_state(AUTH_STATE_KEY).get("state") or AUTH_ERROR
            sync.update(last_result="auth_failed", last_error=f"{st}: {type(exc).__name__}", last_error_utc=_now_iso())
            _write_state(SYNC_STATE_KEY, sync)
            return {"state": "auth_failed", "auth_state": st, "error_category": classify_error(exc), "collected": 0,
                    "api_calls": 0}
        budget = {"calls": 0, "max": max_calls}
        collected = self._collect_snapshots(service, videos, budget) if videos else 0
        d7 = self._collect_first_week(service, d7_due, budget) if d7_due else 0
        res = {"state": "collected", "collected": collected, "first_7_days": d7, "api_calls": budget["calls"],
               "budget_exhausted": budget["calls"] >= budget["max"]}
        sync.update(last_result="collected", last_success_utc=_now_iso(), last_counts={k: res[k] for k in
                    ("collected", "first_7_days", "api_calls", "budget_exhausted")})
        sync.pop("last_error", None)
        _write_state(SYNC_STATE_KEY, sync)
        return res

    def _query(self, service, budget: Dict[str, int], **kw) -> Dict[str, Any]:
        if budget["calls"] >= budget["max"]:
            raise RuntimeError("quota_budget_exhausted")
        budget["calls"] += 1
        delay = 1.0
        for attempt in range(3):
            try:
                return service.reports().query(ids="channel==MINE", **kw).execute()
            except Exception as exc:
                cat = classify_error(exc)
                if cat not in ("quota_or_rate_limit", "server_error") or attempt == 2:
                    raise
                __import__("time").sleep(delay)
                delay *= 2
        return {}

    def _collect_snapshots(self, service, videos: List[Dict[str, Any]], budget: Dict[str, int]) -> int:
        collected = 0
        start = min((v["published_date"] or self.today) for v in videos).isoformat()
        end = self.today.isoformat()
        for i in range(0, len(videos), MAX_IDS_PER_QUERY):
            chunk = videos[i:i + MAX_IDS_PER_QUERY]
            ids = [v["video_id"] for v in chunk]
            merged, err_cat = self._metric_groups(service, budget, ids, start, end)
            now = _now_iso()
            for v in chunk:
                ctx = self._context(v["video_path"])
                self.store.upsert({**ctx, "video_id": v["video_id"], "collected_date": self.today.isoformat(),
                                   "collected_at_utc": now, "topic": v["topic"], "thumbnail": v["thumbnail"],
                                   "published_at_utc": v["published_at_utc"], "window_start": start, "window_end": end,
                                   "metrics_json": json.dumps({m: s.get("value") for m, s in merged[v["video_id"]].items()}),
                                   "metric_status_json": json.dumps({m: s.get("status") for m, s in merged[v["video_id"]].items()}),
                                   "error_category": err_cat, "window_kind": "lifetime_to_date"})
                collected += 1
        return collected

    def _metric_groups(self, service, budget, ids: List[str], start: str, end: str):
        merged: Dict[str, Dict[str, Any]] = {v: {} for v in ids}
        err_cat = None
        for group in (CORE_METRICS, ENGAGEMENT_METRICS):
            if err_cat in ("insufficient_scope", "auth"):
                for v in ids:
                    merged[v].update(metric_values(None, group, ids, error=err_cat)[v])
                continue
            try:
                query_metrics = group if "views" in group else ["views"] + group
                resp = self._query(service, budget, startDate=start, endDate=end, metrics=",".join(query_metrics),
                                   dimensions="video", filters="video==" + ",".join(ids), sort="-views",
                                   maxResults=min(200, len(ids)))
                vals = metric_values(resp, group, ids)
            except Exception as exc:
                err_cat = "quota_budget" if "quota_budget_exhausted" in str(exc) else classify_error(exc)
                vals = metric_values(None, group, ids, error=err_cat)
                logger.warning("[ANALYTICS] query (%s) failed: %s", ",".join(group), err_cat)
            for v in ids:
                merged[v].update(vals[v])
        return merged, err_cat

    def d7_due_videos(self) -> List[Dict[str, Any]]:
        """Videos public for at least 7 full days whose first-7-days window has not been recorded yet."""
        done = self.store.d7_done()
        due = []
        for v in self._published_videos():
            try:
                pub = datetime.fromisoformat(str(v["published_at_utc"]).replace("Z", "+00:00")).date()
            except (TypeError, ValueError):
                continue
            if v["video_id"] in done or (self.today - pub).days < 8:      # day 7 must be complete and reported
                continue
            v["published_date"] = pub
            due.append(v)
        return due

    def _collect_first_week(self, service, videos: List[Dict[str, Any]], budget: Dict[str, int]) -> int:
        n = 0
        for v in videos:
            if budget["calls"] >= budget["max"]:
                break
            start = v["published_date"].isoformat()
            end = (v["published_date"] + timedelta(days=6)).isoformat()
            merged, err_cat = self._metric_groups(service, budget, [v["video_id"]], start, end)
            metrics = {m: s.get("value") for m, s in merged[v["video_id"]].items()}
            status = {m: s.get("status") for m, s in merged[v["video_id"]].items()}
            for name, fn in (("retention", self._retention), ("traffic_sources", self._traffic)):
                try:
                    metrics[name], status[name] = fn(service, budget, v["video_id"], start, end)
                except Exception as exc:
                    metrics[name] = None
                    status[name] = "unavailable" if classify_error(exc) == "incompatible_query" else "request_failed"
            ctx = self._context(v["video_path"])
            self.store.upsert({**ctx, "video_id": v["video_id"], "collected_date": "D7:" + end, "collected_at_utc": _now_iso(),
                               "topic": v["topic"], "thumbnail": v["thumbnail"], "published_at_utc": v["published_at_utc"],
                               "window_start": start, "window_end": end, "metrics_json": json.dumps(metrics),
                               "metric_status_json": json.dumps(status), "error_category": err_cat,
                               "window_kind": "first_7_days"})
            n += 1
        return n

    def _retention(self, service, budget, video_id: str, start: str, end: str):
        resp = self._query(service, budget, startDate=start, endDate=end, metrics="audienceWatchRatio",
                           dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}")
        rows = resp.get("rows") or []
        if not rows:
            return None, "not_yet_populated"
        curve = {round(float(r[0]), 2): float(r[1]) for r in rows}
        pick = lambda x: curve.get(min(curve, key=lambda k: abs(k - x)))
        return {"points": len(curve), "at_10pct": pick(0.1), "at_25pct": pick(0.25), "at_50pct": pick(0.5),
                "at_75pct": pick(0.75), "at_100pct": pick(1.0)}, "available"

    def _traffic(self, service, budget, video_id: str, start: str, end: str):
        resp = self._query(service, budget, startDate=start, endDate=end, metrics="views",
                           dimensions="insightTrafficSourceType", filters=f"video=={video_id}", sort="-views")
        rows = resp.get("rows") or []
        if not rows:
            return None, "not_yet_populated"
        return {str(r[0]): r[1] for r in rows}, "available"


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
