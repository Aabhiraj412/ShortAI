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
API_STATE_KEY = "youtube_analytics_api_state"          # last real Analytics API request: success / failure (2026-11-28)
AUTHZ_STATE_KEY = "youtube_analytics_authorization"    # last dashboard-started authorization attempt (no secrets)
COLLECT_STALE_SEC = 1800                               # a collection marked running longer than this is stale
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
        running = sync.get("running_since_utc")
        if running:
            try:
                age = (datetime.now(timezone.utc) - datetime.strptime(running, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)).total_seconds()
            except ValueError:
                age = COLLECT_STALE_SEC + 1
            if age < COLLECT_STALE_SEC:
                return {"state": "already_running", "collected": 0, "api_calls": 0, "running_since_utc": running}
        sync["running_since_utc"] = _now_iso()
        _write_state(SYNC_STATE_KEY, sync)
        try:
            return self._collect(sync, max_calls)
        finally:
            cur = _read_state(SYNC_STATE_KEY)
            cur.pop("running_since_utc", None)
            _write_state(SYNC_STATE_KEY, cur)

    def _collect(self, sync: Dict[str, Any], max_calls: int) -> Dict[str, Any]:
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
                res = service.reports().query(ids="channel==MINE", **kw).execute()
                record_api_result(True)
                return res
            except Exception as exc:
                cat = classify_error(exc)
                if cat not in ("quota_or_rate_limit", "server_error") or attempt == 2:
                    record_api_result(False, cat)
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


def record_api_result(ok: bool, category: Optional[str] = None) -> None:
    st = _read_state(API_STATE_KEY)
    if ok:
        st.update(last_success_utc=_now_iso(), consecutive_failures=0, last_result="ok")
    else:
        st.update(last_failure_utc=_now_iso(), last_failure_category=category, last_result="failed",
                  consecutive_failures=int(st.get("consecutive_failures") or 0) + 1)
    _write_state(API_STATE_KEY, st)


def _short(scope: str) -> str:
    return scope.rsplit("/", 1)[-1]


def analytics_overview(oauth_manager, store: Optional["PerformanceStore"] = None) -> Dict[str, Any]:
    """The single Analytics state model used by every dashboard section (2026-11-28). It keeps apart:
    OAuth authorization, granted scopes (names only), API connectivity (a real Analytics request), the last
    successful metrics collection and whether metrics are populated. "Operational" requires an Analytics API request
    that actually succeeded; missing metrics are reported as pending, never as zero."""
    st = _read_state(AUTH_STATE_KEY)
    api = _read_state(API_STATE_KEY)
    sync = _read_state(SYNC_STATE_KEY)
    authz = _read_state(AUTHZ_STATE_KEY)
    scopes = granted_scopes(oauth_manager)
    recorded = ANALYTICS_SCOPE in scopes
    auth_state = st.get("state")
    # A refresh that requested yt-analytics.readonly succeeded (CONNECTED) or the token records the grant.
    if auth_state in (REVOKED, AUTH_REQUIRED):
        oauth = "REVOKED" if auth_state == REVOKED else "AUTHORIZATION_REQUIRED"
    elif auth_state == CONNECTED or recorded:
        oauth = "AUTHORIZED"
    elif auth_state in (EXPIRED, AUTH_ERROR):
        oauth = "REFRESH_FAILING"
    else:
        oauth = "AUTHORIZATION_REQUIRED"
    granted = sorted({_short(x) for x in scopes} | ({_short(ANALYTICS_SCOPE)} if auth_state == CONNECTED else set()))
    last_ok, last_fail = api.get("last_success_utc"), api.get("last_failure_utc")
    if not last_ok and not last_fail:
        api_status = "UNTESTED"
    elif api.get("last_result") == "failed" or (not api.get("last_result") and not last_ok):
        api_status = "FAILING"
    else:
        api_status = "CONNECTED"
    videos = (store.latest(200) if store is not None else [])
    populated = sum(1 for v in videos if any(s in ("available", "available_zero")
                                             for s in (v.get("metric_status") or {}).values()))
    pending = len(videos) - populated
    if oauth != "AUTHORIZED":
        overall, label = "AUTHORIZATION_REQUIRED", "Analytics authorization required"
    elif api_status == "UNTESTED":
        overall, label = "AUTHORIZED_UNTESTED", "Analytics connected (API not yet tested)"
    elif api_status == "FAILING":
        overall, label = "API_FAILING", "Analytics connected, API requests failing"
    elif populated == 0:
        overall, label = "METRICS_PENDING", "Analytics connected, metrics pending"
    else:
        overall, label = "OPERATIONAL", "Analytics connected"
    return {
        "overall": overall, "label": label,
        "oauth": {"status": oauth, "authorized": oauth == "AUTHORIZED", "reason": st.get("reason"),
                  "checked_at_utc": st.get("checked_at_utc"), "scope_recorded_in_token": recorded,
                  "action": "Authorize Analytics" if oauth != "AUTHORIZED" else "Reauthorize"},
        "granted_scopes": granted,
        "required_scopes": [_short(x) for x in REQUIRED_AUTH_SCOPES],
        "api": {"status": api_status, "last_success_utc": last_ok, "last_failure_utc": last_fail,
                "last_failure_category": api.get("last_failure_category") if api_status == "FAILING" else None},
        "metrics": {"last_collection_utc": sync.get("last_success_utc"), "last_attempt_utc": sync.get("last_attempt_utc"),
                    "last_result": sync.get("last_result"), "running_since_utc": sync.get("running_since_utc"),
                    "videos_tracked": len(videos), "videos_with_metrics": populated, "videos_pending": pending,
                    "note": "Missing metrics are pending (YouTube populates them with a delay), never zero."},
        "last_authorization": {k: authz.get(k) for k in ("at_utc", "ok", "code", "message", "channel")} if authz else None,
        "schedule": "daily 07:15 IST (unchanged)",
    }


def test_connection(oauth_manager, service_factory: Optional[Callable[[Any], Any]] = None,
                    today: Optional[date] = None) -> Dict[str, Any]:
    """One minimal read-only Analytics request (channel views, last 7 days). Records the API result."""
    today = today or datetime.now(timezone.utc).date()
    try:
        creds = analytics_credentials(oauth_manager, force=True)
    except Exception as exc:
        record_api_result(False, "auth")
        return {"ok": False, "stage": "authorization", "error": type(exc).__name__,
                "auth_state": _read_state(AUTH_STATE_KEY).get("state")}
    try:
        svc = (service_factory or AnalyticsCollector._build_service)(creds)
        svc.reports().query(ids="channel==MINE", startDate=(today - timedelta(days=7)).isoformat(),
                            endDate=today.isoformat(), metrics="views").execute()
    except Exception as exc:
        cat = classify_error(exc)
        record_api_result(False, cat)
        return {"ok": False, "stage": "api_request", "error_category": cat}
    record_api_result(True)
    return {"ok": True, "stage": "api_request", "message": "YouTube Analytics API answered a read-only request."}


# ---- dashboard-started (re)authorization ----------------------------------------------------------------------------
REQUIRED_AUTH_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                        "https://www.googleapis.com/auth/youtube.readonly", ANALYTICS_SCOPE]
_authz_lock = __import__("threading").Lock()


class AuthorizationFailed(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _expected_channel_id(channel: str, oauth_manager) -> Optional[str]:
    try:
        from backend.app.channels.registry import get_registry
        cid = (get_registry().get(channel) or {}).get("youtube_channel_id")
        if cid:
            return cid
    except Exception:
        pass
    if channel == "en":
        try:
            info = (oauth_manager.get_auth_status() or {}).get("channel_info") or {}
            return info.get("id") or info.get("channel_id")
        except Exception:
            return None
    return None


def _record_authz(**fields: Any) -> None:
    _write_state(AUTHZ_STATE_KEY, dict(fields, at_utc=_now_iso()))


def authorize_analytics(oauth_manager=None, *, channel: str = "en", open_browser: bool = True, timeout_sec: int = 300,
                        flow_factory: Optional[Callable[..., Any]] = None,
                        youtube_factory: Optional[Callable[[Any], Any]] = None,
                        analytics_factory: Optional[Callable[[Any], Any]] = None,
                        today: Optional[date] = None) -> Dict[str, Any]:
    """Dashboard-started (re)authorization of the ENGLISH channel for Analytics (2026-11-28).

    Google's normal consent screen (InstalledAppFlow, loopback redirect to localhost:8088, PKCE, a random ``state``
    that the library validates on the callback, offline access, include_granted_scopes) for youtube.upload +
    youtube.readonly + yt-analytics.readonly on the SAME OAuth client. The new credentials stay in memory until:
      1. every required scope is in the granted set,
      2. ``channels.list(mine=true)`` returns exactly the verified English channel (a consent given for another
         channel, e.g. the Hindi brand channel, is rejected and never replaces the English token),
      3. a minimal read-only Analytics request succeeds.
    Only then is the English token saved (``save_credentials`` keeps the stored refresh token if Google omits it).
    Any failure leaves the existing upload credentials exactly as they were. Hindi credentials are never touched:
    Hindi Analytics collection does not exist, so only ``channel="en"`` is accepted."""
    from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager, invalidate_auth_status_cache
    if channel != "en":
        return {"success": False, "code": "CHANNEL_NOT_SUPPORTED",
                "error": "Analytics is collected for the English channel only; Hindi credentials are not changed."}
    if not _authz_lock.acquire(blocking=False):
        return {"success": False, "code": "IN_PROGRESS", "error": "An Analytics authorization is already in progress."}
    try:
        mgr = oauth_manager or YouTubeOAuthManager()
        expected = _expected_channel_id(channel, mgr)
        if not expected:
            raise AuthorizationFailed("CHANNEL_UNKNOWN", "The verified English channel id is unknown; connect YouTube first.")
        import secrets
        state = secrets.token_urlsafe(32)
        if flow_factory is None:
            from google_auth_oauthlib.flow import InstalledAppFlow

            def flow_factory(scopes, state):
                return InstalledAppFlow.from_client_config(mgr._load_client_config(), scopes=scopes, state=state)
        flow = flow_factory(list(REQUIRED_AUTH_SCOPES), state)
        try:
            creds = flow.run_local_server(host="localhost", port=8088, open_browser=open_browser,
                                          timeout_seconds=timeout_sec, access_type="offline", prompt="consent",
                                          include_granted_scopes="true",
                                          authorization_prompt_message="Authorize CrazzyDocs Analytics: {url}",
                                          success_message="Authorization received. You can close this tab and return to CrazzyDocs.")
        except Exception as exc:                      # never log the text: it can carry the redirect URL / code
            from backend.app.services.youtube_publishing_engine import _classify_connect_error
            code, message = _classify_connect_error(exc)
            if "state" in type(exc).__name__.lower() or "mismatchingstate" in str(exc).lower():
                code, message = "STATE_MISMATCH", "The authorization response did not match this request; nothing was saved."
            raise AuthorizationFailed(code, message)
        if creds is None or not getattr(creds, "refresh_token", None) and not getattr(creds, "token", None):
            raise AuthorizationFailed("NO_CREDENTIALS", "Google returned no credentials; nothing was saved.")
        granted = set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or [])
        missing = [_short(s) for s in REQUIRED_AUTH_SCOPES if s not in granted]
        if missing:
            raise AuthorizationFailed("SCOPES_NOT_GRANTED",
                                      "Not all permissions were granted (missing: " + ", ".join(missing) +
                                      "). Nothing was saved; uploads keep working with the existing authorization.")
        try:
            if youtube_factory is None:
                from googleapiclient.discovery import build

                def youtube_factory(c):
                    return build("youtube", "v3", credentials=c, cache_discovery=False)
            items = (youtube_factory(creds).channels().list(part="id", mine=True).execute() or {}).get("items") or []
        except Exception as exc:
            raise AuthorizationFailed("CHANNEL_VERIFICATION_FAILED",
                                      f"Could not verify the channel ({type(exc).__name__}); nothing was saved.")
        ids = [i.get("id") for i in items]
        if ids != [expected]:
            raise AuthorizationFailed("WRONG_CHANNEL",
                                      "The consent was given for a different YouTube channel than the verified English "
                                      "channel. Nothing was saved; choose the English channel on Google's screen.")
        today = today or datetime.now(timezone.utc).date()
        try:
            svc = (analytics_factory or AnalyticsCollector._build_service)(creds)
            svc.reports().query(ids="channel==MINE", startDate=(today - timedelta(days=7)).isoformat(),
                                endDate=today.isoformat(), metrics="views").execute()
        except Exception as exc:
            cat = classify_error(exc)
            record_api_result(False, cat)
            raise AuthorizationFailed("ANALYTICS_VERIFICATION_FAILED",
                                      f"The Analytics API request failed ({cat}); nothing was saved.")
        mgr.save_credentials(creds, event="connect")       # only now: verified scopes, channel and Analytics access
        record_api_result(True)
        with _refresh_lock:
            _cached.update(creds=None, token_file=None)
        record_auth_state(CONNECTED, reason="authorized from the dashboard (scopes, channel and Analytics verified)", ok=True)
        invalidate_auth_status_cache()
        _record_authz(ok=True, code="AUTHORIZED", message="Analytics authorized and verified.", channel=channel)
        return {"success": True, "code": "AUTHORIZED", "granted_scopes": sorted(_short(s) for s in granted),
                "upload_scopes_preserved": True, "channel_verified": True, "analytics_verified": True, "error": None}
    except AuthorizationFailed as exc:
        _record_authz(ok=False, code=exc.code, message=str(exc), channel=channel)
        logger.warning("Analytics authorization not completed (%s); existing credentials unchanged.", exc.code)
        return {"success": False, "code": exc.code, "error": str(exc), "upload_scopes_preserved": True}
    finally:
        _authz_lock.release()
