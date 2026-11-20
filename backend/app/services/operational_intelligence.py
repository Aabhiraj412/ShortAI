"""Stage 3.32A Operational Intelligence: a read-only analytics layer over existing production records.

Nothing here changes production state:

* The database is opened with ``mode=ro`` and ``PRAGMA query_only=ON``, so a write is impossible even by
  mistake. A missing database is reported as unavailable, never created.
* Files (the production progress store, GNews usage, provider probes) are only read.
* No external API is called and no production service is invoked.

Sources reused (no duplicated records):
  persistent_jobs, production_lifecycle_events, creative_identity_registry, youtube_api_requests,
  youtube_quota_blocks, topic_candidate_ledger / _events / topic_discovery_runs (Stage 3.31, when enabled),
  backend/data/production_progress_store.json (production_observability), backend/data/cache/gnews/usage.json,
  backend/data/provider_probes.json.

Every section reports ``status`` (``actual`` | ``partial`` | ``unavailable``), its ``sources``, the metric
names that are ``estimated`` and the ones that are ``unavailable`` with a reason. Unknown values are never
shown as zero.

Cost: one bundle of bounded, time-windowed queries per (window, date), cached for ``cache_ttl`` seconds,
shared by every section. The database uses the rollback journal, so reads are kept short.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MAX_DAYS = 30
MAX_JOBS = 5000
MAX_EVENTS = 50000
MAX_API_ROWS = 50000
MAX_CANDIDATE_EVENTS = 50000
NORMAL_PRODUCTION_MINUTES = 240
FINISHED = ("COMPLETED", "FAILED", "RECOVERY_WINDOW_EXPIRED", "ABORTED_PRE_RESET")
RUNNING = ("RUNNING", "RETRYING", "PAUSED")
QUEUED = ("QUEUED", "RETRY_SCHEDULED", "HANDOFF_PENDING", "PENDING")
EXPIRED = "RECOVERY_WINDOW_EXPIRED"

# Ordered root-cause catalog: the first match wins, so specific causes come before general ones.
ROOT_CAUSES: Tuple[Tuple[str, str], ...] = (
    ("file_lock", r"winerror 32|being used by another process|access is denied|permission ?error"),
    ("topic_exhaustion", r"duplicatecreativeidentity|every discovered topic|no eligible candidate|topic_exhausted"),
    ("renderer_compliance", r"renderer compliance|not an approved production renderer"),
    ("render_timeout", r"render\w*[^.]{0,40}tim(ed|e) ?out|timeout[^.]{0,40}render"),
    ("ffmpeg", r"ffmpeg|parsed_pad|padded dimensions|error reinitializing filters|libx264|muxer"),
    ("renderer", r"lemo|opuscar|renderfailure|render failure|node(\.exe)? "),
    ("quota", r"quota|ratelimit|rate limit|uploadlimit|daily limit"),
    ("auth", r"insufficient_scope|oauth|invalid_grant|unauthori[sz]ed|token (expired|revoked)"),
    ("media_acquisition", r"openverse|wikimedia|commons|pixabay|images-api\.nasa|archive\.org|acquisition|license"),
    ("duplicate_content", r"duplicate (script|content)|near[_ -]duplicate|scene composition repeats"),
    ("qc", r"qc failure|blocked_qc|quality check|duration (is|was) "),
    ("tts", r"\btts\b|edge-tts|narration|audio mux"),
    ("script_generation", r"gemini|script writer|grounded"),
    ("network", r"timed out|timeout|connection (reset|refused|aborted)|getaddrinfo|ssl|temporarily unavailable|\b50[234]\b"),
    ("stop_requested", r"stop request|graceful stop|interrupted"),
    ("recovery_window_expired", r"recovery window expired"),
)
_CAUSE_RX = [(name, re.compile(rx, re.I)) for name, rx in ROOT_CAUSES]
_PREFIX_RX = re.compile(r"^\s*\[(TRANSIENT|PERMANENT|DETERMINISTIC|POST_RENDER_FAILURE)\]\s*(?:([A-Za-z_][\w.]*)\s*:)?", re.I)

RENDER_CAUSES = ("ffmpeg", "renderer", "renderer_compliance", "render_timeout", "file_lock", "media_acquisition")


# ---- helpers ---------------------------------------------------------------------------------------------
def _parse_ts(value: Any) -> Optional[datetime]:
    """Parses the several timestamp formats in use into aware UTC datetimes (naive values are UTC)."""
    if value is None or value == "":
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def root_cause(message: str, status: str = "") -> str:
    text = message or ""
    for name, rx in _CAUSE_RX:
        if rx.search(text):
            return name
    if status == EXPIRED and not text.strip():
        return "recovery_window_expired"
    return "other" if text.strip() else "unrecorded"


def failure_class(message: str) -> Tuple[str, str]:
    """(TRANSIENT|PERMANENT|DETERMINISTIC|POST_RENDER_FAILURE|UNCLASSIFIED, error type). Uses the
    classification already stamped by the retry policy; otherwise the retry policy's own classifier."""
    m = _PREFIX_RX.match(message or "")
    if m:
        return m.group(1).upper(), (m.group(2) or "")
    typ = re.match(r"\s*([A-Z][A-Za-z]+(?:Error|Exception|Identity|Failure|Blocked))\b", message or "")
    try:
        from backend.app.scheduler.retry_policy import classify_error
        cls, etype = classify_error(error_type=typ.group(1) if typ else "", message=message or "")
        return cls, etype
    except Exception:
        return "UNCLASSIFIED", typ.group(1) if typ else ""


def error_template(message: str) -> str:
    """Normalizes an error so repeats group together: paths, addresses, numbers and quotes removed."""
    t = (message or "").strip().splitlines()[0] if message else ""
    t = _PREFIX_RX.sub("", t)
    t = re.sub(r"[A-Za-z]:\\[^\s'\"]+|/[\w./-]{6,}", "<path>", t)
    t = re.sub(r"0x[0-9a-f]+|@ [0-9a-f]{8,}|\b[0-9a-f]{12,}\b", "<id>", t, flags=re.I)
    t = re.sub(r"'[^']{0,200}'|\"[^\"]{0,200}\"", "<s>", t)
    t = re.sub(r"\d+(\.\d+)?", "<n>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:180] or "(no error text recorded)"


def pattern_key(template: str) -> str:
    """Grouping key: trailing parenthetical notes (e.g. '(failed after rendering; not retried …)') and
    anything past 100 characters do not split one recurring error into several patterns."""
    t = re.sub(r"\s*\([^()]*\)?\s*$", "", template or "")
    return t[:100]


def _stage_label(stage: Optional[str]) -> str:
    """Expired or failed jobs sometimes store their status instead of the stage they stopped in."""
    if not stage or stage in FINISHED or stage in RUNNING or stage in QUEUED:
        return "NOT_RECORDED"
    return stage


def _median(values: List[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)


def _rate(num: int, den: int) -> Optional[float]:
    return round(100.0 * num / den, 1) if den else None


def _avg(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _section(status: str, sources: List[str], metrics: Dict[str, Any], estimated: Optional[List[str]] = None,
             unavailable: Optional[Dict[str, str]] = None, notes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"status": status, "sources": sources, "metrics": metrics, "estimated": estimated or [],
            "unavailable": unavailable or {}, "notes": notes or []}


# ---- service ---------------------------------------------------------------------------------------------
class OperationalIntelligence:
    def __init__(self, db_path: Optional[str] = None, data_dir: Optional[str] = None,
                 progress_store_path: Optional[str] = None, cache_ttl: float = 60.0,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
        self.db_path = str(db_path)
        self.data_dir = str(data_dir)
        self.progress_store_path = progress_store_path or os.path.join(self.data_dir, "production_progress_store.json")
        self.cache_ttl = float(cache_ttl)
        self.clock = clock
        self._cache: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._store_cache: Tuple[Optional[float], Optional[Dict[str, Any]]] = (None, None)

    # -- read-only access ----------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError(f"database not found: {self.db_path}")
        uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _cached(self, key: Any, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
            if len(self._cache) > 64:
                for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:16]:
                    self._cache.pop(k, None)
        return value

    def _read_json(self, path: str) -> Optional[Any]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _progress_store(self) -> Optional[Dict[str, Any]]:
        """production_observability store, re-parsed only when its modification time changes. A file
        caught mid-write keeps the previous parse."""
        try:
            mtime = os.path.getmtime(self.progress_store_path)
        except OSError:
            return None
        cached_mtime, cached = self._store_cache
        if cached_mtime == mtime and cached is not None:
            return cached
        data = self._read_json(self.progress_store_path)
        if isinstance(data, dict):
            self._store_cache = (mtime, data)
            return data
        return cached

    # -- window -------------------------------------------------------------------------------------
    def window(self, days: int = 7, date: Optional[str] = None) -> Tuple[datetime, datetime, str]:
        """(start_utc, end_utc, label). ``date`` (YYYY-MM-DD, IST) selects one IST calendar day."""
        if date:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=IST)
            return d.astimezone(timezone.utc), (d + timedelta(days=1)).astimezone(timezone.utc), f"{date} (IST day)"
        days = max(1, min(MAX_DAYS, int(days or 7)))
        end = self.clock()
        return end - timedelta(days=days), end, f"last {days} day{'s' if days > 1 else ''}"

    # -- bundle -------------------------------------------------------------------------------------
    def bundle(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        start, end, label = self.window(days, date)
        return self._cached(("bundle", start.replace(second=0, microsecond=0).isoformat() if date else days, date),
                            lambda: self._load_bundle(start, end, label))

    def _load_bundle(self, start: datetime, end: datetime, label: str) -> Dict[str, Any]:
        b: Dict[str, Any] = {"window": {"start_utc": start.isoformat(), "end_utc": end.isoformat(), "label": label},
                             "generated_at_utc": self.clock().isoformat(), "db_available": False, "db_error": None,
                             "tables": set(), "truncated": {}}
        s_space = start.strftime("%Y-%m-%d %H:%M:%S")
        e_space = end.strftime("%Y-%m-%d %H:%M:%S")
        s_iso, e_iso = start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            conn = self._connect()
        except Exception as exc:
            b["db_error"] = f"{type(exc).__name__}: {exc}"
            for k in ("jobs", "events", "api", "registry", "cand_events", "cand_rows", "runs", "blocks"):
                b[k] = []
            return self._attach_files(b, start, end)
        try:
            b["db_available"] = True
            b["tables"] = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

            def q(table: str, sql: str, args: tuple, limit: int, key: str) -> None:
                if table not in b["tables"]:
                    b[key] = []
                    return
                rows = [dict(r) for r in conn.execute(sql + " LIMIT ?", args + (limit + 1,))]
                if len(rows) > limit:
                    b["truncated"][key] = limit
                    rows = rows[:limit]
                b[key] = rows

            q("persistent_jobs",
              "SELECT id, status, current_stage, retry_count, error_details, created_at, updated_at, completed_at, "
              "actual_start_at, recovery_reason FROM persistent_jobs WHERE id LIKE 'cycle\\_%' ESCAPE '\\' "
              "AND created_at >= ? AND created_at < ? ORDER BY created_at", (s_space, e_space), MAX_JOBS, "jobs")
            q("production_lifecycle_events",
              "SELECT timestamp_utc, job_id, cycle_id, event_type, details_json, error_details FROM production_lifecycle_events "
              "WHERE timestamp_utc >= ? AND timestamp_utc < ? ORDER BY id", (s_iso, e_iso), MAX_EVENTS, "events")
            q("youtube_api_requests",
              "SELECT ts_utc, pt_day, method, bucket, cost, cost_verified, purpose, outcome, http_status, error_category "
              "FROM youtube_api_requests WHERE ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc", (s_iso, e_iso), MAX_API_ROWS, "api")
            today_pt = self._pacific_day(self.clock())
            q("youtube_api_requests",
              "SELECT bucket, SUM(cost) AS used, COUNT(*) AS requests, MIN(cost_verified) AS verified FROM youtube_api_requests "
              "WHERE pt_day = ? GROUP BY bucket", (today_pt,), 100, "api_today")
            q("youtube_quota_blocks", "SELECT * FROM youtube_quota_blocks", (), 100, "blocks")
            q("creative_identity_registry",
              "SELECT job_id, topic_title, category, status, renderer_config_json, visual_engine, script_source, created_at "
              "FROM creative_identity_registry WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
              (s_iso, e_iso), MAX_JOBS, "registry")
            q("topic_discovery_runs",
              "SELECT run_id, job_id, attempts, started_at, candidate_count, eligible_count, selected_candidate_id, outcome, "
              "provider_stats_json, recovery_json FROM topic_discovery_runs WHERE started_at >= ? AND started_at < ? "
              "ORDER BY started_at", (s_iso, e_iso), MAX_JOBS, "runs")
            q("topic_candidate_events",
              "SELECT run_id, candidate_id, stage, status, reason, created_at FROM topic_candidate_events "
              "WHERE created_at >= ? AND created_at < ?", (s_iso, e_iso), MAX_CANDIDATE_EVENTS, "cand_events")
            q("topic_candidate_ledger",
              "SELECT candidate_id, provider_family, category, category_v2_json, selection_status, rejection_reason, "
              "last_discovered_at FROM topic_candidate_ledger WHERE last_discovered_at >= ? AND last_discovered_at < ?",
              (s_iso, e_iso), MAX_JOBS * 10, "cand_rows")
        except sqlite3.OperationalError as exc:
            b["db_error"] = f"OperationalError: {exc}"
            b["db_available"] = False
            for k in ("jobs", "events", "api", "api_today", "registry", "cand_events", "cand_rows", "runs", "blocks"):
                b.setdefault(k, [])
        finally:
            conn.close()
        return self._attach_files(b, start, end)

    def _attach_files(self, b: Dict[str, Any], start: datetime, end: datetime) -> Dict[str, Any]:
        store = self._progress_store()
        prods: List[Dict[str, Any]] = []
        if isinstance(store, dict):
            for pid, rec in (store.get("productions") or {}).items():
                if not str(pid).startswith("cycle_") or not isinstance(rec, dict):
                    continue
                if str(rec.get("execution_context", "PRODUCTION")).upper() != "PRODUCTION":
                    continue
                st = _parse_ts(rec.get("started_at"))
                if st and start <= st < end:
                    prods.append({"id": pid, **{k: rec.get(k) for k in ("overall_status", "started_at", "completed_at",
                                                                          "stages", "assets", "rendering", "error")}})
        b["store_available"] = isinstance(store, dict)
        b["productions"] = prods
        b["gnews_usage"] = self._read_json(os.path.join(self.data_dir, "cache", "gnews", "usage.json"))
        b["provider_probes"] = self._read_json(os.path.join(self.data_dir, "provider_probes.json"))
        return b

    @staticmethod
    def _pacific_day(at: datetime) -> str:
        try:
            from backend.app.services.youtube_quota import pacific_day
            return pacific_day(at)
        except Exception:
            return at.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

    # -- shared derived data ------------------------------------------------------------------------
    def _failures(self, b: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One record per failure occurrence: every dispatch-failure event plus each job's final error
        when it adds a new (job, template) pair, plus expired jobs with no recorded error."""
        out: List[Dict[str, Any]] = []
        seen = set()
        stage_by_job = {j["id"]: j.get("current_stage") for j in b["jobs"]}
        for ev in b["events"]:
            if ev["event_type"] != "CYCLE_DISPATCH_FAILED" or not (ev.get("error_details") or "").strip():
                continue
            msg = ev["error_details"]
            tpl = error_template(msg)
            seen.add((ev.get("job_id"), tpl))
            cls, etype = failure_class(msg)
            out.append({"job_id": ev.get("job_id"), "at": ev["timestamp_utc"], "message": msg[:400], "template": tpl,
                        "cause": root_cause(msg), "class": cls, "type": etype, "stage": _stage_label(stage_by_job.get(ev.get("job_id"))),
                        "source": "lifecycle_event"})
        for j in b["jobs"]:
            if j["status"] not in ("FAILED", EXPIRED, "ABORTED_PRE_RESET"):
                continue
            msg = j.get("error_details") or ""
            tpl = error_template(msg)
            if msg and (j["id"], tpl) in seen:
                continue
            cause = root_cause(msg, j["status"])
            cls, etype = failure_class(msg) if msg else ("UNCLASSIFIED", "")
            if cause == "recovery_window_expired":
                cls = "EXPIRED"          # a deadline outcome, not an error class
            out.append({"job_id": j["id"], "at": j.get("updated_at") or j.get("created_at"), "message": msg[:400],
                        "template": tpl, "cause": cause, "class": cls, "type": etype,
                        "stage": _stage_label(j.get("current_stage")), "source": "persistent_jobs"})
        return out

    # -- A. production health -----------------------------------------------------------------------
    def production_health(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        if not b["db_available"]:
            return _section("unavailable", ["persistent_jobs"], {}, unavailable={"all": b["db_error"] or "database unavailable"})
        jobs = b["jobs"]
        st = Counter(j["status"] for j in jobs)
        finished = sum(st[s] for s in FINISHED)
        durations = []
        for j in jobs:
            if j["status"] == "COMPLETED":
                a, c = _parse_ts(j.get("actual_start_at")), _parse_ts(j.get("completed_at"))
                if a and c and c >= a:
                    durations.append((c - a).total_seconds() / 60.0)
        stage_fail = Counter(_stage_label(j.get("current_stage")) for j in jobs if j["status"] in ("FAILED", EXPIRED))
        normal = [d for d in durations if d <= NORMAL_PRODUCTION_MINUTES]
        stage_secs: Dict[str, List[float]] = defaultdict(list)
        for p in b.get("productions", []):
            for key, sd in (p.get("stages") or {}).items():
                s0, s1 = _parse_ts((sd or {}).get("started_at")), _parse_ts((sd or {}).get("completed_at"))
                if (sd or {}).get("status") == "completed" and s0 and s1 and s1 >= s0:
                    stage_secs[key].append((s1 - s0).total_seconds())
        metrics = {
            "total_cycles": len(jobs),
            "completed": st["COMPLETED"], "failed": st["FAILED"], "recovery_window_expired": st[EXPIRED],
            "aborted": st["ABORTED_PRE_RESET"], "running": sum(st[s] for s in RUNNING),
            "queued": sum(st[s] for s in QUEUED),
            "finished": finished,
            "completion_pct": _rate(st["COMPLETED"], finished),
            "avg_production_minutes": _avg(normal),
            "median_production_minutes": _median(durations),
            "long_running_completions": len(durations) - len(normal),
            "avg_stage_seconds": {k: _avg(v) for k, v in stage_secs.items()},
            "stage_failure_distribution": dict(stage_fail.most_common()),
            "status_counts": dict(st),
        }
        unavailable = {} if normal else {"avg_production_minutes": "no completed cycle with start and completion times in window"}
        if not stage_secs:
            unavailable["avg_stage_seconds"] = "no stage timings in the progress store for this window"
        notes = [f"avg_production_minutes excludes completions that took longer than {NORMAL_PRODUCTION_MINUTES} min "
                 "(cycles re-armed hours or days later); they are counted in long_running_completions."]
        if b["truncated"].get("jobs"):
            notes.append(f"Job list truncated at {MAX_JOBS} rows.")
        return _section("actual", ["persistent_jobs", "production_progress_store.json (stage timings)"], metrics,
                        unavailable=unavailable, notes=notes)

    # -- B. failure intelligence --------------------------------------------------------------------
    def failure_intelligence(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        if not b["db_available"]:
            return _section("unavailable", ["persistent_jobs", "production_lifecycle_events"], {},
                            unavailable={"all": b["db_error"] or "database unavailable"})
        fails = self._failures(b)
        by_cause = Counter(f["cause"] for f in fails)
        by_class = Counter(f["class"] for f in fails)
        by_stage = Counter((f.get("stage") or "UNKNOWN") for f in fails)
        groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for f in fails:
            g = groups.setdefault((f["cause"], pattern_key(f["template"])), {
                "cause": f["cause"], "template": f["template"], "class": f["class"], "classes": [], "type": f["type"],
                "count": 0, "jobs": [], "first_seen": f["at"], "last_seen": f["at"]})
            g["count"] += 1
            if f["class"] not in g["classes"]:
                g["classes"].append(f["class"])
            # the most severe class seen describes the pattern
            order = ("PERMANENT", "DETERMINISTIC", "POST_RENDER_FAILURE", "EXPIRED", "TRANSIENT", "UNCLASSIFIED")
            g["class"] = min(g["classes"], key=lambda c: order.index(c) if c in order else len(order))
            if f["job_id"] and f["job_id"] not in g["jobs"] and len(g["jobs"]) < 6:
                g["jobs"].append(f["job_id"])
            g["first_seen"] = min(g["first_seen"] or f["at"], f["at"] or g["first_seen"])
            g["last_seen"] = max(g["last_seen"] or f["at"], f["at"] or g["last_seen"])
        patterns = sorted(groups.values(), key=lambda g: (-g["count"], g["cause"]))
        daily: Dict[str, Counter] = defaultdict(Counter)
        for f in fails:
            dt = _parse_ts(f["at"])
            if dt:
                daily[dt.astimezone(IST).strftime("%Y-%m-%d")][f["cause"]] += 1
        metrics = {
            "total_failures": len(fails),
            "by_root_cause": dict(by_cause.most_common()),
            "by_class": dict(by_class),
            "transient": by_class.get("TRANSIENT", 0),
            "permanent_or_deterministic": by_class.get("PERMANENT", 0) + by_class.get("DETERMINISTIC", 0),
            "by_stage": dict(by_stage.most_common()),
            "most_failure_prone_stage": next((k for k, _ in by_stage.most_common() if k != "NOT_RECORDED"), None),
            "patterns": patterns[:25],
            "repeated_patterns": [p for p in patterns if p["count"] >= 2][:15],
            "daily_trend": {d: dict(c) for d, c in sorted(daily.items())},
        }
        notes = ["A failure is each recorded dispatch failure plus each job's final error; expired cycles with no "
                 "recorded error are counted as 'recovery_window_expired'."]
        return _section("actual", ["production_lifecycle_events", "persistent_jobs", "retry_policy.classify_error"],
                        metrics, notes=notes)

    # -- C. renderer reliability --------------------------------------------------------------------
    def renderer_reliability(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        fails = self._failures(b) if b["db_available"] else []
        causes = Counter(f["cause"] for f in fails)
        renderers = Counter()
        for r in b.get("registry", []):
            if r.get("status") in ("PRODUCED", "HISTORICAL_PRODUCED"):
                try:
                    renderers[(json.loads(r.get("renderer_config_json") or "{}") or {}).get("renderer") or "unknown"] += 1
                except ValueError:
                    renderers["unknown"] += 1
        render_ok = render_fail = 0
        durations: List[float] = []
        acq_failed = acq_unique = 0
        acq_known = False
        daily: Dict[str, Counter] = defaultdict(Counter)
        for p in b.get("productions", []):
            stages = p.get("stages") or {}
            stage = stages.get("video_rendering") or stages.get("rendering") or {}
            day = (_parse_ts(p.get("started_at")) or self.clock()).astimezone(IST).strftime("%Y-%m-%d")
            if stage.get("status") == "completed":
                render_ok += 1
                daily[day]["ok"] += 1
                s, c = _parse_ts(stage.get("started_at")), _parse_ts(stage.get("completed_at"))
                if s and c and c >= s:
                    durations.append((c - s).total_seconds())
            elif stage.get("status") in ("failed", "error"):
                render_fail += 1
                daily[day]["failed"] += 1
            assets = p.get("assets") or {}
            if "failed_acquisitions" in assets:
                acq_known = True
                acq_failed += int(assets.get("failed_acquisitions") or 0)
                acq_unique += int(assets.get("unique_images") or 0)
        attempts = render_ok + render_fail
        metrics = {
            "renders_completed": render_ok, "renders_failed": render_fail,
            "render_success_pct": _rate(render_ok, attempts),
            "avg_render_seconds": _avg(durations),
            "produced_by_renderer": dict(renderers),
            "lemo_opuscar_produced": renderers.get("lemo-opuscar", 0),
            "ffmpeg_errors": causes.get("ffmpeg", 0),
            "renderer_errors": causes.get("renderer", 0),
            "renderer_compliance_blocks": causes.get("renderer_compliance", 0),
            "render_timeouts": causes.get("render_timeout", 0),
            "file_lock_errors": causes.get("file_lock", 0),
            "media_acquisition_errors": causes.get("media_acquisition", 0),
            "media_acquisitions_failed": acq_failed if acq_known else None,
            "media_unique_images": acq_unique if acq_known else None,
            "daily_trend": {d: dict(c) for d, c in sorted(daily.items())},
        }
        unavailable = {}
        if not b.get("store_available"):
            unavailable["renders_completed"] = "production progress store unreadable"
        if not durations:
            unavailable["avg_render_seconds"] = "no completed rendering stage with timestamps in window"
        if not acq_known:
            unavailable["media_acquisitions_failed"] = "no asset statistics in window"
        status = "actual" if b["db_available"] and b.get("store_available") else ("partial" if b["db_available"] or b.get("store_available") else "unavailable")
        return _section(status, ["production_progress_store.json (rendering stage, assets)", "creative_identity_registry",
                                 "failure catalog"], metrics, unavailable=unavailable,
                        notes=["Render counts cover productions whose progress record started in the window."])

    # -- D. topic intelligence ----------------------------------------------------------------------
    def topic_intelligence(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        if "topic_discovery_runs" not in b.get("tables", set()):
            return _section("unavailable", ["topic_candidate_ledger"], {},
                            unavailable={"all": "Stage 3.31 candidate ledger tables not present (flag candidate_ledger off)"})
        runs = b["runs"]
        ev = b["cand_events"]
        rej = Counter(e["reason"] for e in ev if e["status"] == "REJECTED" and e["reason"])
        decided = Counter(e["status"] for e in ev if e["status"] in ("ELIGIBLE", "REJECTED", "SELECTED"))
        providers: Dict[str, Counter] = defaultdict(Counter)
        prov_daily: Dict[str, Dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        for r in runs:
            try:
                stats = json.loads(r.get("provider_stats_json") or "{}")
            except ValueError:
                stats = {}
            day = (_parse_ts(r.get("started_at")) or self.clock()).astimezone(IST).strftime("%Y-%m-%d")
            for fam, s in (stats or {}).items():
                for k, v in (s or {}).items():
                    if isinstance(v, (int, float)):
                        providers[fam][k] += v
                        prov_daily[day][fam][k] += v
        cats = Counter(r.get("category") or "?" for r in b["cand_rows"] if r.get("selection_status") in ("SELECTED", "PRODUCED"))
        agree = Counter()
        disagreements = []
        for r in b["cand_rows"]:
            try:
                v2 = json.loads(r.get("category_v2_json") or "{}") or {}
            except ValueError:
                v2 = {}
            if not v2:
                continue
            s = v2.get("status")
            if s in ("UNKNOWN", "OUT_OF_SCOPE"):
                agree[s.lower()] += 1
            elif v2.get("display_name") == r.get("category"):
                agree["agree"] += 1
            else:
                agree["disagree"] += 1
            if v2.get("display_name") != r.get("category") and len(disagreements) < 20:
                disagreements.append({"candidate_id": r["candidate_id"], "v1": r.get("category"),
                                      "v2": v2.get("display_name") or f"{s}{':' + v2['out_of_scope_domain'] if v2.get('out_of_scope_domain') else ''}"})
        exhaustion_failures = sum(1 for f in self._failures(b) if f["cause"] == "topic_exhaustion") if b["db_available"] else 0
        low_pool_runs = sum(1 for r in runs if (r.get("eligible_count") or 0) <= 1)
        recoveries = Counter()
        for r in runs:
            try:
                rec = json.loads(r.get("recovery_json") or "{}") or {}
            except ValueError:
                rec = {}
            if rec.get("outcome"):
                recoveries[rec["outcome"]] += 1
        total_v2 = sum(agree.values())
        daily = {}
        for r in runs:
            day = (_parse_ts(r.get("started_at")) or self.clock()).astimezone(IST).strftime("%Y-%m-%d")
            d = daily.setdefault(day, {"runs": 0, "candidates": 0, "eligible": 0})
            d["runs"] += 1
            d["candidates"] += int(r.get("candidate_count") or 0)
            d["eligible"] += int(r.get("eligible_count") or 0)
        metrics = {
            "discovery_runs": len(runs),
            "candidates_recorded": sum(int(r.get("candidate_count") or 0) for r in runs),
            "avg_candidates_per_run": _avg([float(r.get("candidate_count") or 0) for r in runs]),
            "avg_eligible_per_run": _avg([float(r.get("eligible_count") or 0) for r in runs]),
            "acceptance_pct": _rate(decided["ELIGIBLE"], decided["ELIGIBLE"] + decided["REJECTED"]),
            "rejection_reasons": dict(rej.most_common()),
            "provider_contribution": {k: dict(v) for k, v in sorted(providers.items())},
            "selected_category_distribution": dict(cats.most_common()),
            "classifier_v1_vs_v2": dict(agree),
            "classifier_disagreement_pct": _rate(agree["disagree"] + agree["unknown"] + agree["out_of_scope"], total_v2),
            "classifier_disagreements": disagreements,
            "topic_exhaustion_failures": exhaustion_failures,
            "low_pool_runs": low_pool_runs,
            "exhaustion_recoveries": dict(recoveries),
            "daily_trend": daily,
            "provider_daily_trend": {d: {f: dict(c) for f, c in v.items()} for d, v in sorted(prov_daily.items())},
        }
        unavailable = {}
        if not runs:
            unavailable["discovery_runs"] = "no discovery run recorded in window (ledger enabled 2026-11-20 05:00 IST)"
        if not total_v2:
            unavailable["classifier_v1_vs_v2"] = "no v2 shadow classifications in window"
        return _section("actual" if runs else "partial",
                        ["topic_discovery_runs", "topic_candidate_events", "topic_candidate_ledger", "failure catalog"],
                        metrics, unavailable=unavailable,
                        notes=["acceptance_pct counts decisions (events), so a candidate seen in several runs counts each time."])

    # -- E. API intelligence ------------------------------------------------------------------------
    def api_intelligence(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        try:
            from backend.app.services.youtube_quota import BUCKETS
        except Exception:
            BUCKETS = {}
        api = b.get("api", [])
        by_method = Counter(r["method"] for r in api)
        units_by_bucket = Counter()
        errors = Counter()
        daily_units: Dict[str, Counter] = defaultdict(Counter)
        for r in api:
            units_by_bucket[r["bucket"]] += int(r.get("cost") or 0)
            daily_units[r["pt_day"]][r["bucket"]] += int(r.get("cost") or 0)
            if r.get("error_category"):
                errors[f'{r["method"]}:{r["error_category"]}'] += 1
        today_rows = {r["bucket"]: r for r in b.get("api_today", [])}
        blocks = {r["bucket"]: r for r in b.get("blocks", [])}
        now = self.clock()
        quota_today = {}
        for bucket, meta in BUCKETS.items():
            row = today_rows.get(bucket) or {}
            used = int(row.get("used") or 0)
            blk = blocks.get(bucket)
            active_block = bool(blk and (_parse_ts(blk.get("blocked_until_utc")) or now) > now)
            quota_today[bucket] = {"label": meta.get("label"), "used_estimate": used, "daily_limit_default": meta.get("daily_limit"),
                                   "unit": meta.get("unit"), "requests": int(row.get("requests") or 0),
                                   "used_pct_estimate": _rate(used, int(meta.get("daily_limit") or 0)),
                                   "remaining_estimate": max(0, int(meta.get("daily_limit") or 0) - used),
                                   "costs_verified": bool(row.get("verified", 1)), "google_block_active": active_block}
        scripts = Counter()
        for r in b.get("registry", []):
            src = (r.get("script_source") or "").strip()
            if src:
                scripts["gemini" if src.startswith("llm:") else src] += 1
        research = {}
        for r in b.get("runs", []):
            try:
                stats = json.loads(r.get("provider_stats_json") or "{}") or {}
            except ValueError:
                stats = {}
            for fam, s in stats.items():
                agg = research.setdefault(fam, {"queries": 0, "errors": 0})
                agg["queries"] += int((s or {}).get("queries") or 0)
                agg["errors"] += int((s or {}).get("errors") or 0)
        gnews = b.get("gnews_usage") if isinstance(b.get("gnews_usage"), dict) else None
        probes = b.get("provider_probes") if isinstance(b.get("provider_probes"), dict) else None
        probe_failures = {}
        if probes:
            for name, p in probes.items():
                if isinstance(p, dict) and p.get("last_failure_utc"):
                    probe_failures[name] = {"last_failure_utc": p.get("last_failure_utc"), "category": p.get("last_failure_category")}
        metrics = {
            "youtube_requests": len(api),
            "youtube_requests_by_method": dict(by_method.most_common()),
            "youtube_units_by_bucket": dict(units_by_bucket),
            "youtube_errors": dict(errors),
            "youtube_quota_today_pacific": quota_today,
            "youtube_quota_day": self._pacific_day(now),
            "youtube_daily_units": {d: dict(c) for d, c in sorted(daily_units.items())},
            "scripts_by_source": dict(scripts),
            "gemini_scripts": scripts.get("gemini", 0),
            "research_provider_requests": research,
            "gnews_requests_by_utc_day": gnews,
            "provider_probe_failures": probe_failures,
        }
        estimated = ["youtube_quota_today_pacific"]
        unavailable = {
            "google_reported_quota": "Google's own usage needs Cloud Monitoring access, which the YouTube OAuth token does not have",
            "gemini_call_count": "Gemini calls are not recorded per request; only which scripts Gemini wrote (scripts_by_source)",
        }
        if not research:
            unavailable["research_provider_requests"] = "recorded only while the Stage 3.31 candidate ledger is on"
        if gnews is None:
            unavailable["gnews_requests_by_utc_day"] = "GNews usage file not present"
        if probes is None:
            unavailable["provider_probe_failures"] = "no on-demand provider health check has been run"
        return _section("actual" if b["db_available"] else "unavailable",
                        ["youtube_api_requests", "youtube_quota_blocks", "creative_identity_registry.script_source",
                         "topic_discovery_runs", "cache/gnews/usage.json", "provider_probes.json"],
                        metrics, estimated=estimated, unavailable=unavailable,
                        notes=["YouTube usage is CrazzyDocs' own count of requests sent, with documented costs; limits are the "
                               "documented project defaults, so 'remaining' is an estimate."])

    # -- F. recovery analytics ----------------------------------------------------------------------
    def recovery_analytics(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        if not b["db_available"]:
            return _section("unavailable", ["persistent_jobs"], {}, unavailable={"all": b["db_error"] or "database unavailable"})
        ev_by_job: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in b["events"]:
            if e.get("job_id"):
                ev_by_job[e["job_id"]].append(e)
        attempted, ttr = [], []
        retry_counts = Counter()
        for j in b["jobs"]:
            evs = ev_by_job.get(j["id"], [])
            dispatches = sum(1 for e in evs if e["event_type"] == "CYCLE_DISPATCH_STARTED")
            rearmed = any(e["event_type"] in ("RETRY_POLICY_REARMED", "OPERATOR_RETRY_REQUESTED") for e in evs)
            failed_first = [e for e in evs if e["event_type"] == "CYCLE_DISPATCH_FAILED"]
            rc = int(j.get("retry_count") or 0)
            retry_counts[rc] += 1
            if rc > 0 or rearmed or dispatches >= 2 or failed_first:
                attempted.append(j)
                if j["status"] == "COMPLETED" and failed_first:
                    f0 = _parse_ts(failed_first[0]["timestamp_utc"])
                    done = next((_parse_ts(e["timestamp_utc"]) for e in evs if e["event_type"] == "PRODUCTION_CYCLE_COMPLETED"), None)
                    if f0 and done and done >= f0:
                        ttr.append((done - f0).total_seconds() / 60.0)
        recovered = sum(1 for j in attempted if j["status"] == "COMPLETED")
        failed_after = sum(1 for j in attempted if j["status"] in ("FAILED", EXPIRED))
        finished = sum(1 for j in b["jobs"] if j["status"] in FINISHED)
        expired = sum(1 for j in b["jobs"] if j["status"] == EXPIRED)
        metrics = {
            "jobs_needing_recovery": len(attempted),
            "recovered": recovered,
            "recovery_success_pct": _rate(recovered, len(attempted)),
            "failed_after_recovery": failed_after,
            "recovery_window_expiration_pct": _rate(expired, finished),
            "avg_time_to_recover_minutes": _avg(ttr),
            "median_time_to_recover_minutes": _median(ttr),
            "recovered_within_60_min": sum(1 for t in ttr if t <= 60),
            "retry_count_distribution": {str(k): v for k, v in sorted(retry_counts.items())},
            "total_retries": sum(k * v for k, v in retry_counts.items()),
            "rearm_events": sum(1 for e in b["events"] if e["event_type"] == "RETRY_POLICY_REARMED"),
            "operator_retries": sum(1 for e in b["events"] if e["event_type"] == "OPERATOR_RETRY_REQUESTED"),
        }
        unavailable = {} if ttr else {"avg_time_to_recover_minutes": "no cycle recovered from a recorded failure in window"}
        return _section("actual", ["persistent_jobs", "production_lifecycle_events"], metrics, unavailable=unavailable,
                        notes=["A job 'needed recovery' if it was retried, re-armed, dispatched more than once or had a "
                               "recorded dispatch failure. Time to recover runs from its first failure to completion."])

    # -- G. trends ----------------------------------------------------------------------------------
    def reliability_trends(self, days: int = 7, date: Optional[str] = None) -> Dict[str, Any]:
        b = self.bundle(days, date)
        if not b["db_available"]:
            return _section("unavailable", ["persistent_jobs"], {}, unavailable={"all": b["db_error"] or "database unavailable"})
        daily: Dict[str, Counter] = defaultdict(Counter)
        hourly: Dict[int, Counter] = defaultdict(Counter)
        for j in b["jobs"]:
            dt = _parse_ts(j.get("created_at"))
            if not dt or j["status"] not in FINISHED:
                continue
            ist = dt.astimezone(IST)
            ok = "completed" if j["status"] == "COMPLETED" else "not_completed"
            daily[ist.strftime("%Y-%m-%d")][ok] += 1
            hourly[ist.hour][ok] += 1

        def with_rate(c: Counter) -> Dict[str, Any]:
            tot = c["completed"] + c["not_completed"]
            return {"completed": c["completed"], "not_completed": c["not_completed"], "success_pct": _rate(c["completed"], tot)}

        fi = self.failure_intelligence(days, date)["metrics"]
        ri = self.renderer_reliability(days, date)["metrics"]
        ti = self.topic_intelligence(days, date)
        metrics = {
            "daily_success": {d: with_rate(c) for d, c in sorted(daily.items())},
            "hourly_success_ist": {f"{h:02d}:00": with_rate(c) for h, c in sorted(hourly.items())},
            "daily_failures_by_cause": fi.get("daily_trend", {}),
            "daily_renderer": ri.get("daily_trend", {}),
            "daily_providers": ti["metrics"].get("provider_daily_trend", {}),
            "daily_topic_discovery": ti["metrics"].get("daily_trend", {}),
        }
        return _section("actual", ["persistent_jobs", "failure catalog", "progress store", "topic_discovery_runs"], metrics,
                        notes=["Cycles are bucketed by their creation time in IST."])

    # -- H. daily summary ---------------------------------------------------------------------------
    def daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        date = date or self.clock().astimezone(IST).strftime("%Y-%m-%d")
        ph = self.production_health(date=date)
        fi = self.failure_intelligence(date=date)
        ri = self.renderer_reliability(date=date)
        ti = self.topic_intelligence(date=date)
        ai = self.api_intelligence(date=date)
        rc = self.recovery_analytics(date=date)
        base7 = self.production_health(days=7)
        anomalies: List[Dict[str, str]] = []
        recs: List[str] = []
        pm = ph["metrics"]
        if ph["status"] != "unavailable":
            day_rate, week_rate = pm.get("completion_pct"), base7["metrics"].get("completion_pct")
            if day_rate is not None and week_rate is not None and pm.get("finished", 0) >= 4 and day_rate < week_rate - 20:
                anomalies.append({"severity": "high", "text": f"Completion {day_rate}% vs 7-day {week_rate}%."})
            if pm.get("recovery_window_expired", 0) >= 3:
                anomalies.append({"severity": "medium", "text": f"{pm['recovery_window_expired']} cycles expired without finishing."})
        for p in fi["metrics"].get("repeated_patterns", []):
            if p["count"] >= 3:
                anomalies.append({"severity": "high" if p["class"] in ("DETERMINISTIC", "PERMANENT") else "medium",
                                  "text": f"Repeated {p['cause']} failure ({p['count']}x): {p['template'][:120]}"})
                if p["class"] in ("DETERMINISTIC", "PERMANENT"):
                    recs.append(f"Investigate the recurring {p['cause']} error; it is {p['class'].lower()} and will not clear by retrying.")
        for k, v in (ai["metrics"].get("youtube_errors") or {}).items():
            anomalies.append({"severity": "medium", "text": f"YouTube API error {k} ({v}x)."})
            if "insufficient_scope" in k:
                recs.append("A YouTube call failed for insufficient OAuth scope; review which operation needs it before granting anything.")
        for bucket, q in (ai["metrics"].get("youtube_quota_today_pacific") or {}).items():
            if (q.get("used_pct_estimate") or 0) >= 80 or q.get("google_block_active"):
                anomalies.append({"severity": "high", "text": f"YouTube quota {bucket}: {q['used_estimate']}/{q['daily_limit_default']} {q['unit']} (estimate)."})
        tm = ti["metrics"]
        if ti["status"] != "unavailable":
            if tm.get("topic_exhaustion_failures"):
                anomalies.append({"severity": "high", "text": f"{tm['topic_exhaustion_failures']} topic-exhaustion failures."})
                recs.append("Topic exhaustion recurred; review the Stage 3.31 rotation and recovery flags (activation needs approval).")
            if tm.get("low_pool_runs"):
                anomalies.append({"severity": "medium", "text": f"{tm['low_pool_runs']} discovery runs had at most one eligible topic."})
            for fam, s in (tm.get("provider_contribution") or {}).items():
                if s.get("queries", 0) >= 3 and s.get("accepted", 0) == 0 and s.get("candidates_created", 0) > 0:
                    recs.append(f"Provider '{fam}' produced {s['candidates_created']} candidates but none were accepted.")
                if s.get("errors"):
                    anomalies.append({"severity": "low", "text": f"Provider '{fam}' had {s['errors']} errors."})
        rm = ri["metrics"]
        if rm.get("renders_failed"):
            anomalies.append({"severity": "high", "text": f"{rm['renders_failed']} renders failed."})
        if rm.get("file_lock_errors"):
            recs.append("File-lock errors occurred during media handling; check for overlapping processes or antivirus scans.")
        total_finished = pm.get("finished") or 0
        cr = pm.get("completion_pct")
        if ph["status"] == "unavailable" or total_finished == 0:
            health = "UNKNOWN"
        elif cr is not None and cr >= 80 and not any(a["severity"] == "high" for a in anomalies):
            health = "HEALTHY"
        elif cr is not None and cr >= 50:
            health = "DEGRADED"
        else:
            health = "CRITICAL"
        return {
            "date": date, "health": health,
            "production": {k: pm.get(k) for k in ("total_cycles", "completed", "failed", "recovery_window_expired",
                                                   "running", "queued", "completion_pct", "avg_production_minutes")},
            "major_failures": fi["metrics"].get("patterns", [])[:5],
            "repeated_problems": fi["metrics"].get("repeated_patterns", [])[:5],
            "api_usage": {"youtube_requests": ai["metrics"].get("youtube_requests"),
                          "youtube_units_by_bucket": ai["metrics"].get("youtube_units_by_bucket"),
                          "youtube_errors": ai["metrics"].get("youtube_errors"),
                          "gemini_scripts": ai["metrics"].get("gemini_scripts")},
            "recovery": {k: rc["metrics"].get(k) for k in ("jobs_needing_recovery", "recovered", "recovery_success_pct",
                                                            "recovery_window_expiration_pct", "avg_time_to_recover_minutes")},
            "renderer": {k: rm.get(k) for k in ("renders_completed", "renders_failed", "avg_render_seconds", "ffmpeg_errors",
                                                 "file_lock_errors")},
            "topics": {k: tm.get(k) for k in ("discovery_runs", "avg_candidates_per_run", "avg_eligible_per_run",
                                                "acceptance_pct", "classifier_disagreement_pct")} if ti["status"] != "unavailable" else None,
            "anomalies": anomalies,
            "recommendations": recs or ["No operator action suggested."],
            "recommendations_note": "Recommendations are for operator review only; nothing is executed automatically.",
            "generated_at_utc": self.clock().isoformat(),
        }

    # -- overview -----------------------------------------------------------------------------------
    SECTIONS = ("production_health", "failure_intelligence", "renderer_reliability", "topic_intelligence",
                "api_intelligence", "recovery_analytics", "reliability_trends")

    def section(self, name: str, days: int = 7) -> Dict[str, Any]:
        if name not in self.SECTIONS:
            raise KeyError(name)
        return getattr(self, name)(days)

    def overview(self, days: int = 7) -> Dict[str, Any]:
        b = self.bundle(days)
        out = {"window": b["window"], "generated_at_utc": b["generated_at_utc"], "database_available": b["db_available"],
               "database_error": b["db_error"], "truncated": b["truncated"]}
        for name in self.SECTIONS:
            try:
                out[name] = getattr(self, name)(days)
            except Exception as exc:   # one broken section never hides the others
                logger.warning("Operational intelligence section %s failed: %s", name, exc)
                out[name] = _section("unavailable", [], {}, unavailable={"all": f"{type(exc).__name__}: {exc}"})
        try:
            out["daily_summary"] = self.daily_summary()
        except Exception as exc:
            out["daily_summary"] = {"health": "UNKNOWN", "error": f"{type(exc).__name__}: {exc}"}
        return out


_service: Optional[OperationalIntelligence] = None
_service_lock = threading.Lock()


def get_service() -> OperationalIntelligence:
    global _service
    with _service_lock:
        if _service is None:
            _service = OperationalIntelligence()
        return _service
