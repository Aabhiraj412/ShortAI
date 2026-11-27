"""
Provider and integration inventory with health (Stage 3.29, extended in 3.30). Never returns or logs credential
values: only whether a variable is set.

Status classes:
  NOT_IMPLEMENTED, KEY_MISSING, CONFIGURED_INVALID, CONFIGURED_HEALTHY, NOT_REQUIRED_LOCAL, UNAVAILABLE,
  NO_KEY_NEEDED (anonymous public API), INTERMITTENT, AUTHORIZATION_REQUIRED, UNVERIFIED (configured, no recent check)

Health comes from (a) real requests the pipeline made (media/net.py ProviderHealth, persisted by the photo stage)
and (b) explicit health probes (`probe()`), which are cheap read-only requests run ONLY on demand (dashboard
button) and throttled per provider. Opening or refreshing the dashboard never calls a provider; the YouTube Data API
is never probed (it would spend quota) and uses the cached channel check instead.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
KEY_MISSING = "KEY_MISSING"
CONFIGURED_INVALID = "CONFIGURED_INVALID"
CONFIGURED_HEALTHY = "CONFIGURED_HEALTHY"
NOT_REQUIRED_LOCAL = "NOT_REQUIRED_LOCAL"
UNAVAILABLE = "UNAVAILABLE"
NO_KEY_NEEDED = "NO_KEY_NEEDED"
INTERMITTENT = "INTERMITTENT"
AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
UNVERIFIED = "UNVERIFIED"

PROBE_MIN_INTERVAL_SEC = 600

PROVIDERS: List[Dict[str, Any]] = [
    {"name": "Gemini API", "key": "gemini", "purpose": "script writing (model chain gemini-3.8-flash -> gemini-3.6-flash)",
     "env": "GEMINI_API_KEY", "implemented": True, "production": True,
     "fallback": "next model in chain, then labelled extractive script", "limits": "free tier: per-minute and per-day caps"},
    {"name": "YouTube Data API v3", "key": "youtube_data", "purpose": "upload, schedule, thumbnails, status verification",
     "env": "OAuth token (youtube.upload, youtube.readonly)", "implemented": True, "production": True, "files": True,
     "fallback": "upload stays queued (HANDOFF_PENDING); never re-uploads",
     "limits": "general 10,000 units/day; videos.insert 100 calls/day (own bucket); reset midnight PT"},
    {"name": "YouTube Analytics API", "key": "youtube_analytics", "purpose": "performance metrics of published videos",
     "env": "OAuth scope yt-analytics.readonly", "implemented": True, "production": False, "oauth_scope": True,
     "fallback": "metrics recorded as unavailable (never zero)", "limits": "Analytics API quota (separate project quota)"},
    {"name": "Pixabay", "key": "pixabay", "purpose": "licensed stock photos / video clips", "env": "PIXABAY_API_KEY",
     "implemented": True, "production": True, "health_key": "Pixabay",
     "fallback": "Wikimedia / Openverse / NASA / Internet Archive", "limits": "100 requests/60 s; 24 h response cache"},
    {"name": "Pexels", "key": "pexels", "purpose": "licensed stock photos", "env": "PEXELS_API_KEY", "implemented": True,
     "production": True, "health_key": "Pexels", "fallback": "other photo providers", "limits": "200 requests/hour"},
    {"name": "GNews", "key": "gnews", "purpose": "current-events topic discovery (corroborated, >= 2 outlets)",
     "env": "GNEWS_API_KEY", "implemented": True, "production": True, "health_key": "GNews",
     "fallback": "Wikipedia / Phys.org discovery (cycles never depend on GNews)", "limits": "100 requests/day (budget 90)"},
    {"name": "Openverse", "key": "openverse", "purpose": "CC-licensed photos (scene-level selection)", "env": None,
     "implemented": True, "production": True, "health_key": "Openverse",
     "fallback": "Wikimedia / NASA / Internet Archive", "limits": "anonymous rate limit; intermittent 504s observed"},
    {"name": "Wikimedia Commons", "key": "wikimedia", "purpose": "CC/PD photos (scene-level selection)", "env": None,
     "implemented": True, "production": True, "health_key": "Wikimedia Commons",
     "fallback": "other photo providers", "limits": "polite use; identifiable User-Agent sent"},
    {"name": "Internet Archive", "key": "internet_archive", "purpose": "public-domain images", "env": None,
     "implemented": True, "production": True, "health_key": "Internet Archive", "fallback": "other photo providers",
     "limits": "no published limit"},
    {"name": "NASA Image Library", "key": "nasa", "purpose": "space imagery", "env": None, "implemented": True,
     "production": True, "health_key": "NASA Image Library", "fallback": "other photo providers", "limits": "none published"},
    {"name": "Microsoft Edge neural TTS (edge-tts)", "key": "edge_tts", "purpose": "voice-over", "env": None,
     "implemented": True, "production": True, "fallback": "none (production fails at VOICEOVER)",
     "limits": "unofficial free service"},
    {"name": "Wikipedia REST / phys.org RSS", "key": "wikipedia", "purpose": "topic discovery and research", "env": None,
     "implemented": True, "production": True, "fallback": "other feeds / cached topics", "limits": "polite use"},
    {"name": "SMTP (email alerts)", "key": "smtp", "purpose": "cycle summaries and alerts", "env": "CRAZZYDOCS_SMTP_PASSWORD",
     "implemented": True, "production": True, "fallback": "dashboard notifications", "limits": "provider dependent"},
    {"name": "Sound, illustrations, fonts", "key": "local", "purpose": "SFX, environments, icons, typography", "env": None,
     "implemented": True, "production": True, "local": True, "fallback": "-", "limits": "-"},
]


def _env_value(name: Optional[str]) -> str:
    """The configured value (used only to send probes; never returned, logged or stored)."""
    if not name or not name.isupper():
        return ""
    if os.environ.get(name, "").strip():
        return os.environ[name].strip()
    try:
        from backend.app.core.paths import ENV_LOCAL_PATH
        for line in Path(ENV_LOCAL_PATH).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _env_set(name: Optional[str]) -> Optional[bool]:
    if not name or not name.isupper():
        return None
    return bool(_env_value(name))


def _probe_path() -> Path:
    from backend.app.core.paths import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "backend" / "data" / "provider_probes.json"


def load_probes(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        return json.loads((path or _probe_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _photo_health() -> Dict[str, Any]:
    try:
        from backend.app.services.creative_engine_status import _provider_health
        return (_provider_health() or {}).get("providers") or {}
    except Exception:
        return {}


def _youtube_status() -> Dict[str, Any]:
    try:
        from backend.app.core.paths import CLIENT_SECRETS_PATH, TOKEN_PATH
        return {"client_secrets": os.path.exists(CLIENT_SECRETS_PATH), "token": os.path.exists(TOKEN_PATH)}
    except Exception:
        return {}


def _analytics_scope_granted() -> Optional[bool]:
    try:
        from backend.app.services.youtube_analytics import ANALYTICS_SCOPE, granted_scopes
        from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
        return ANALYTICS_SCOPE in granted_scopes(YouTubeOAuthManager())
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------- probes
def _probe_requests() -> Dict[str, Callable[[], Dict[str, Any]]]:
    from backend.app.media.net import fetch_json

    def gemini():
        key = _env_value("GEMINI_API_KEY")
        if not key:
            return {"ok": False, "category": "key_missing"}
        # key in a header, never in the URL
        fetch_json("https://generativelanguage.googleapis.com/v1/models?pageSize=1", provider="Gemini", retries=0,
                   timeout=10, headers={"x-goog-api-key": key})
        return {"ok": True}

    def pixabay():
        key = _env_value("PIXABAY_API_KEY")
        if not key:
            return {"ok": False, "category": "key_missing"}
        d = fetch_json("https://pixabay.com/api/?" + urllib.parse.urlencode({"key": key, "q": "sky", "per_page": 3}),
                       provider="Pixabay", retries=0, timeout=10)
        return {"ok": True, "results": len(d.get("hits") or [])}

    def gnews():
        from backend.app.content.gnews_client import GNewsClient
        c = GNewsClient()
        if not c.configured():
            return {"ok": False, "category": "key_missing"}
        arts = c.top_headlines("science", 1)
        if c.last_error_category and c.last_error_category != "empty":
            return {"ok": False, "category": c.last_error_category}
        return {"ok": True, "results": len(arts), "requests_today": c.usage_today()}

    def openverse():
        d = fetch_json("https://api.openverse.org/v1/images/?q=science&page_size=1", provider="Openverse", retries=0,
                       timeout=15)
        return {"ok": True, "results": d.get("result_count")}

    def wikimedia():
        fetch_json("https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch=telescope&srnamespace=6"
                   "&srlimit=1&format=json", provider="Wikimedia Commons", retries=0, timeout=10)
        return {"ok": True}

    def nasa():
        fetch_json("https://images-api.nasa.gov/search?q=moon&page_size=1", provider="NASA Image Library", retries=0,
                   timeout=10)
        return {"ok": True}

    def internet_archive():
        fetch_json("https://archive.org/advancedsearch.php?q=moon&fl%5B%5D=identifier&rows=1&output=json",
                   provider="Internet Archive", retries=0, timeout=10)
        return {"ok": True}

    def edge_tts():
        import asyncio
        import edge_tts as _et
        voices = asyncio.run(asyncio.wait_for(_et.list_voices(), timeout=15))
        return {"ok": bool(voices), "voices": len(voices or [])}

    return {"gemini": gemini, "pixabay": pixabay, "gnews": gnews, "openverse": openverse, "wikimedia": wikimedia,
            "nasa": nasa, "internet_archive": internet_archive, "edge_tts": edge_tts}


def probe(keys: Optional[List[str]] = None, *, path: Optional[Path] = None, now: Optional[float] = None,
          requests: Optional[Dict[str, Callable[[], Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Runs cheap read-only health checks (on demand only), throttled to one per provider per 10 minutes."""
    now = now or time.time()
    reqs = requests if requests is not None else _probe_requests()
    state = load_probes(path)
    results = {}
    for key in keys or list(reqs):
        if key not in reqs:
            continue
        prev = state.get(key) or {}
        if now - float(prev.get("last_checked_ts") or 0) < PROBE_MIN_INTERVAL_SEC:
            results[key] = {**prev, "throttled": True}
            continue
        t0 = time.perf_counter()
        try:
            res = reqs[key]() or {}
        except Exception as exc:
            status = getattr(exc, "status", None)
            kind = getattr(exc, "kind", type(exc).__name__)
            cat = ("invalid_credential" if status in (400, 401, 403) and key in ("gemini", "pixabay", "gnews")
                   else "rate_limited" if status == 429 else "timeout" if kind == "timeout"
                   else "server_error" if status and int(status) >= 500 else "connection" if kind == "connection"
                   else "error")
            res = {"ok": False, "category": cat, "http_status": status}
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {**prev, "last_checked_ts": now, "last_checked_utc": stamp,
                 "latency_ms": int((time.perf_counter() - t0) * 1000), "last_result": res}
        if res.get("ok"):
            entry["last_success_utc"] = stamp
        else:
            entry["last_failure_utc"] = stamp
            entry["last_failure_category"] = res.get("category")
        hist = (prev.get("recent") or [])[-4:] + [bool(res.get("ok"))]
        entry["recent"] = hist
        state[key] = entry
        results[key] = entry
    try:
        p = path or _probe_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return results


# ---------------------------------------------------------------------------------------------- inventory
def _status(p: Dict[str, Any], configured: Optional[bool], pr: Dict[str, Any], h: Dict[str, Any]) -> str:
    if p.get("local"):
        return NOT_REQUIRED_LOCAL
    if not p["implemented"]:
        return NOT_IMPLEMENTED
    if p.get("oauth_scope"):
        return CONFIGURED_HEALTHY if configured else AUTHORIZATION_REQUIRED
    if configured is False:
        return KEY_MISSING
    last = (pr.get("last_result") or {}) if pr else {}
    if last.get("category") == "invalid_credential":
        return CONFIGURED_INVALID
    recent = pr.get("recent") or []
    if recent:
        if all(recent[-3:]) and last.get("ok"):
            return CONFIGURED_HEALTHY if configured else NO_KEY_NEEDED
        if any(recent) and not all(recent):
            return INTERMITTENT
        if not any(recent):
            return UNAVAILABLE
    if h:
        if configured and (h.get("last_error") or {}).get("status") in (401, 403):
            return CONFIGURED_INVALID          # a credential problem outranks the success/failure counts
        if h.get("failed", 0) and not h.get("ok", 0):
            return UNAVAILABLE
        if h.get("failed", 0) and h.get("ok", 0):
            return INTERMITTENT
        return CONFIGURED_HEALTHY if configured else NO_KEY_NEEDED
    return UNVERIFIED if configured else NO_KEY_NEEDED


def inventory(probe_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    health = _photo_health() or {}
    probes = load_probes(probe_path)
    yt = _youtube_status()
    out = []
    for p in PROVIDERS:
        row = {k: p.get(k) for k in ("name", "key", "purpose", "env", "implemented", "production", "fallback", "limits")}
        if p.get("files"):
            configured: Optional[bool] = bool(yt.get("client_secrets") and yt.get("token"))
        elif p.get("oauth_scope"):
            configured = _analytics_scope_granted()
        else:
            configured = _env_set(p.get("env"))
        row["configured"] = configured
        row["credential"] = ("not required" if configured is None and not p.get("oauth_scope") else
                             "set" if configured else ("authorization required" if p.get("oauth_scope") else "missing"))
        h = health.get(p.get("health_key") or "", {}) if p.get("health_key") else {}
        pr = probes.get(p.get("key") or "", {})
        row["recent_health"] = h or None
        row["probe"] = {k: pr.get(k) for k in ("last_checked_utc", "last_success_utc", "last_failure_utc",
                                               "last_failure_category", "latency_ms")} if pr else None
        last_err = (h.get("last_error") or {}) if h else {}
        row["error_category"] = (pr.get("last_failure_category") if pr and not (pr.get("last_result") or {}).get("ok")
                                 else (last_err.get("kind") if last_err else None))
        row["rate_limited"] = bool(row["error_category"] == "rate_limited" or last_err.get("status") == 429)
        status = _status(p, configured, pr, h)
        row["status"] = status
        row["usable_by_production"] = bool(p["production"] and status in (
            CONFIGURED_HEALTHY, NO_KEY_NEEDED, INTERMITTENT, UNVERIFIED, NOT_REQUIRED_LOCAL))
        out.append(row)
    return out


MEDIA_PROVIDER_NAMES = ("Pixabay", "Pexels", "Openverse", "Wikimedia Commons", "Internet Archive", "NASA")


def state_335(row: Dict[str, Any], rendered: Optional[int]) -> str:
    """Stage 3.35 provider state. A key being set is not health; reachability is not usage."""
    st = row.get("status")
    if st == KEY_MISSING:
        return "KEY_MISSING"
    if st in (CONFIGURED_INVALID, AUTHORIZATION_REQUIRED):
        return "AUTHENTICATION_REQUIRED"
    if st == NOT_IMPLEMENTED:
        return "DISABLED"
    if row.get("rate_limited"):
        return "RATE_LIMITED"
    if st in (INTERMITTENT, UNAVAILABLE):
        return "DEGRADED"
    if st == NOT_REQUIRED_LOCAL:
        return "HEALTHY"
    if st in (CONFIGURED_HEALTHY, NO_KEY_NEEDED):
        ok = (row.get("recent_health") or {}).get("ok", 0) if row.get("recent_health") else 0
        if rendered == 0 and ok:
            return "NOT_USED"
        return "HEALTHY" if ok or (row.get("probe") or {}).get("last_success_utc") else "CONFIGURED"
    if st == UNVERIFIED:
        return "CONFIGURED"
    return "UNKNOWN"


def rendered_usage(limit: int = 20) -> Dict[str, Dict[str, int]]:
    """Assets actually drawn in the last ``limit`` rendered videos, per provider and kind (from the rendered plans)."""
    try:
        import sqlite3
        from backend.app.core.paths import DATABASE_PATH, MEDIA_PRODUCTIONS_DIR
        from backend.app.rendering.pop_info.asset_manifest import audit_recent
        c = sqlite3.connect(Path(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            jobs = [r[0] for r in c.execute("SELECT id FROM persistent_jobs WHERE status='COMPLETED' ORDER BY created_at DESC LIMIT ?", (limit,))]
        finally:
            c.close()
        out: Dict[str, Dict[str, int]] = {}
        for key, n in audit_recent(Path(MEDIA_PRODUCTIONS_DIR), jobs)["providers"].items():
            prov, kind = key.rsplit(":", 1)
            out.setdefault(prov, {})[kind] = out.setdefault(prov, {}).get(kind, 0) + n
        return out
    except Exception:
        return {}


def production_evidence(limit_jobs: int = 20, limit_runs: int = 24) -> Dict[str, Dict[str, Any]]:
    """Remediation WS4: what production actually did with each non-media provider, from persisted records only
    (read-only; never calls a provider). Configured is not healthy and healthy is not used."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import json as _json
        import sqlite3
        from datetime import datetime, timedelta
        from backend.app.core.paths import DATABASE_PATH
        c = sqlite3.connect(Path(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except Exception:
        return out
    try:
        def q(sql: str, *a: Any) -> List[Any]:
            try:
                return c.execute(sql, a).fetchall()
            except Exception:
                return []
        # Script writer (Gemini): which source wrote the last N evaluated videos.
        src: Dict[str, int] = {}
        for (dj,) in q("SELECT decisions_json FROM creative_records ORDER BY evaluated_at_utc DESC LIMIT ?", limit_jobs):
            try:
                s = str((_json.loads(dj or "{}") or {}).get("script_source") or "unknown")
            except ValueError:
                s = "unknown"
            key = "llm" if s.startswith("llm:") else s
            src[key] = src.get(key, 0) + 1
        if src:
            n = sum(src.values())
            out["gemini"] = {"scripts_last_n": n, "written_by_model": src.get("llm", 0),
                             "extractive_fallback": src.get("extractive_fallback", 0),
                             "model_share": round(src.get("llm", 0) / n, 2)}
        # Voice-over (edge-tts): every completed production went through VOICEOVER (no fallback is enabled).
        since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        done = q("SELECT COUNT(*) FROM persistent_jobs WHERE status='COMPLETED' AND updated_at >= ? "
                 "AND id NOT LIKE 'test%' AND id NOT LIKE 'mock%'", since)
        if done:
            out["edge_tts"] = {"productions_voiced_24h": int(done[0][0])}
        # Topic discovery providers: the last N discovery runs.
        agg: Dict[str, Dict[str, int]] = {}
        for (pj,) in q("SELECT provider_stats_json FROM topic_discovery_runs ORDER BY started_at DESC LIMIT ?", limit_runs):
            try:
                stats = _json.loads(pj or "{}") or {}
            except ValueError:
                continue
            for fam, v in stats.items():
                a = agg.setdefault(fam, {})
                for k in ("queries", "articles", "candidates_created", "accepted", "duplicates", "errors"):
                    a[k] = a.get(k, 0) + int((v or {}).get(k) or 0)
        def fam_sum(prefixes: Tuple[str, ...]) -> Optional[Dict[str, int]]:
            fams = {k: v for k, v in agg.items() if k.startswith(prefixes)}
            if not fams:
                return None
            tot = {k: sum(v.get(k, 0) for v in fams.values()) for k in ("queries", "articles", "candidates_created",
                                                                       "accepted", "duplicates", "errors")}
            tot["families"] = sorted(fams)
            return tot
        for key, prefixes in (("gnews", ("gnews",)), ("wikipedia", ("wikipedia", "physorg")), ("nasa", ("nasa",))):
            s = fam_sum(prefixes)
            if s:
                out.setdefault(key, {})["topic_discovery_last_runs"] = dict(s, runs=limit_runs)
        # Email (SMTP): delivery outcomes in the last 24 h.
        rows = q("SELECT status, COUNT(*) FROM persistent_notifications WHERE created_at >= ? GROUP BY status", since)
        if rows:
            out["smtp"] = {"notifications_24h": {str(s): int(n) for s, n in rows}}
    finally:
        c.close()
    return out


def state_with_evidence(row: Dict[str, Any], ev: Dict[str, Any], rendered: Optional[int]) -> Tuple[str, str]:
    """Remediation WS4: the provider state from what production actually observed, with a one-line reason."""
    base = state_335(row, rendered)
    key = row.get("key")
    if base in ("KEY_MISSING", "AUTHENTICATION_REQUIRED", "DISABLED"):
        return base, "credential or authorization missing"
    if key == "gemini" and ev:
        share = ev.get("model_share", 0)
        if ev.get("written_by_model", 0) == 0:
            return "DEGRADED", (f"0 of the last {ev['scripts_last_n']} scripts came from the model; all used the "
                                "labelled extractive fallback")
        if share < 0.5:
            return "DEGRADED", (f"{ev['written_by_model']} of the last {ev['scripts_last_n']} scripts came from the "
                                f"model; {ev['extractive_fallback']} used the extractive fallback (rate limits / overload)")
        return "HEALTHY", f"{ev['written_by_model']} of the last {ev['scripts_last_n']} scripts came from the model"
    if key == "edge_tts" and ev:
        n = ev.get("productions_voiced_24h", 0)
        return ("HEALTHY", f"voiced {n} completed productions in 24 h") if n else ("CONFIGURED", "no completed production in 24 h")
    if key == "smtp" and ev:
        counts = ev.get("notifications_24h", {})
        sent = sum(v for k, v in counts.items() if k.upper() == "SENT")
        bad = sum(v for k, v in counts.items() if k.upper() in ("FAILED", "DELIVERY_UNKNOWN"))
        if sent and not bad:
            return "HEALTHY", f"{sent} notifications sent in 24 h"
        if bad:
            return "DEGRADED", f"{sent} sent, {bad} failed or unknown in 24 h"
    disc = (ev or {}).get("topic_discovery_last_runs")
    if key in ("gnews", "wikipedia") and disc:
        if disc.get("errors") and not disc.get("candidates_created"):
            return "DEGRADED", f"{disc['errors']} errors and no candidates in the last {disc['runs']} discovery runs"
        return "HEALTHY", (f"{disc['candidates_created']} candidates, {disc['accepted']} accepted in the last "
                           f"{disc['runs']} discovery runs")
    if base in ("DEGRADED", "RATE_LIMITED"):
        h = row.get("recent_health") or {}
        err = h.get("last_error") or {}
        code = row.get("error_category") or err.get("kind") or "error"
        status = err.get("status")
        hint = {"bad_query": "the request was rejected (query); queries are now sanitised to the provider limits",
                "invalid_key": "check the API key", "invalid_key_or_query": "check the API key or the query",
                "rate_limited": "the provider's rate limit; requests back off automatically",
                "timeout": "slow responses; retried automatically", "connection": "network or provider outage"
                }.get(code, "see the dashboard log for the request")
        return base, (f"{h.get('ok', 0)} ok / {h.get('failed', 0)} failed recently; last error {code}"
                      f"{f' (HTTP {status})' if status else ''}: {hint}")
    if base == "NOT_USED":
        ok = (row.get("recent_health") or {}).get("ok", 0)
        return "AVAILABLE_NOT_SELECTED", (f"{ok} successful requests, but none of its assets was selected in the last "
                                          "20 rendered videos")
    if rendered and base == "CONFIGURED":
        return "HEALTHY", f"{rendered} of its assets appear in the last 20 rendered videos"
    if base == "CONFIGURED":
        return base, "configured; no production evidence of use or health yet"
    return base, ""


def summary() -> Dict[str, Any]:
    rows = inventory()
    usage = rendered_usage()
    evidence = production_evidence()
    for r in rows:
        name = str(r.get("name") or "")
        is_media = any(m.lower() in name.lower() for m in MEDIA_PROVIDER_NAMES)
        used = {k: v for prov, kinds in usage.items() for k, v in kinds.items()
                if prov.lower().startswith(name.lower().split(" (")[0].split(" api")[0].strip())} if is_media else None
        r["rendered_usage_last_20"] = used
        ev = evidence.get(str(r.get("key") or ""), {})
        r["production_evidence"] = ev or None
        r["state"], r["state_reason"] = state_with_evidence(r, ev, (sum(used.values()) if used is not None else None))
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"providers": rows, "counts": counts,
            "note": ("Credential values are never shown. Health = real pipeline requests plus on-demand probes; "
                     "refreshing the dashboard never calls a provider. The YouTube Data API is not probed (quota).")}
