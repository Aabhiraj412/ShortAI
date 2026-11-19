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
from typing import Any, Callable, Dict, List, Optional

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


def summary() -> Dict[str, Any]:
    rows = inventory()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"providers": rows, "counts": counts,
            "note": ("Credential values are never shown. Health = real pipeline requests plus on-demand probes; "
                     "refreshing the dashboard never calls a provider. The YouTube Data API is not probed (quota).")}
