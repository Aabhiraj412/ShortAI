"""
Provider and integration inventory with health (Stage 3.29). Never returns or logs credential values:
only whether a variable is set. See docs/API_INTEGRATION_AUDIT.md for the full audit.

Status classes (as required by the audit):
  NOT_IMPLEMENTED, KEY_MISSING, CONFIGURED_INVALID, CONFIGURED_HEALTHY, NOT_REQUIRED_LOCAL, UNAVAILABLE
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
KEY_MISSING = "KEY_MISSING"
CONFIGURED_INVALID = "CONFIGURED_INVALID"
CONFIGURED_HEALTHY = "CONFIGURED_HEALTHY"
NOT_REQUIRED_LOCAL = "NOT_REQUIRED_LOCAL"
UNAVAILABLE = "UNAVAILABLE"
NO_KEY_NEEDED = "NO_KEY_NEEDED"       # implemented, anonymous public API

PROVIDERS: List[Dict[str, Any]] = [
    {"name": "Gemini API", "purpose": "script writing (model chain gemini-3.8-flash -> gemini-3.6-flash)",
     "env": "GEMINI_API_KEY", "implemented": True, "test": True, "production": True,
     "fallback": "next model in chain, then labelled extractive script", "limits": "free tier: per-minute and per-day request caps; 503/504 seen under load"},
    {"name": "YouTube Data API v3 (OAuth)", "purpose": "upload, schedule, thumbnails, status verification",
     "env": "client_secrets.json + OAuth token (files)", "implemented": True, "test": False, "production": True,
     "fallback": "upload stays queued (HANDOFF_PENDING); never re-uploads", "limits": "10,000 units/day; upload 1,600; thumbnail 50",
     "files": True},
    {"name": "YouTube Analytics API", "purpose": "performance metrics", "env": "(OAuth scope yt-analytics.readonly)",
     "implemented": False, "test": False, "production": False, "fallback": "none", "limits": "-"},
    {"name": "Pixabay", "purpose": "licensed stock photos (topic assets)", "env": "PIXABAY_API_KEY",
     "implemented": True, "test": False, "production": True, "fallback": "Openverse / Wikimedia / NASA / Internet Archive", "limits": "100 requests/60 s"},
    {"name": "Pexels", "purpose": "licensed stock photos", "env": "PEXELS_API_KEY", "implemented": True,
     "test": False, "production": True, "fallback": "other photo providers", "limits": "200 requests/hour, 20,000/month"},
    {"name": "Openverse", "purpose": "CC-licensed photos (scene-level selection)", "env": None, "implemented": True,
     "test": True, "production": True, "fallback": "Wikimedia / NASA / Internet Archive", "limits": "anonymous rate limit; frequent 504s observed",
     "health_key": "Openverse"},
    {"name": "Wikimedia Commons", "purpose": "CC/PD photos (scene-level selection)", "env": None, "implemented": True,
     "test": True, "production": True, "fallback": "other photo providers", "limits": "polite-use limits", "health_key": "Wikimedia Commons"},
    {"name": "Internet Archive", "purpose": "public-domain images", "env": None, "implemented": True, "test": True,
     "production": True, "fallback": "other photo providers", "limits": "no published limit; intermittent connection failures",
     "health_key": "Internet Archive"},
    {"name": "NASA Image Library", "purpose": "space imagery", "env": None, "implemented": True, "test": True,
     "production": True, "fallback": "other photo providers", "limits": "none published", "health_key": "NASA Image Library"},
    {"name": "Wikipedia REST / phys.org RSS", "purpose": "topic discovery and research", "env": None,
     "implemented": True, "test": True, "production": True, "fallback": "other feeds / cached topics", "limits": "polite-use"},
    {"name": "Microsoft Edge neural TTS (edge-tts)", "purpose": "voice-over", "env": None, "implemented": True,
     "test": True, "production": True, "fallback": "none (production fails at VOICEOVER)", "limits": "unofficial free service"},
    {"name": "GNews", "purpose": "news topics", "env": "GNEWS_API_KEY", "implemented": False, "test": False,
     "production": False, "fallback": "-", "limits": "free: 100 requests/day"},
    {"name": "SMTP (email alerts)", "purpose": "cycle summaries and alerts", "env": "CRAZZYDOCS_SMTP_PASSWORD",
     "implemented": True, "test": False, "production": True, "fallback": "dashboard notifications", "limits": "provider dependent"},
    {"name": "Sound, illustrations, fonts", "purpose": "SFX, environments, icons, typography", "env": None,
     "implemented": True, "test": True, "production": True, "local": True, "fallback": "-", "limits": "-"},
]


def _env_set(name: Optional[str]) -> Optional[bool]:
    if not name or not name.isupper():
        return None
    if os.environ.get(name, "").strip():
        return True
    try:
        from backend.app.core.paths import ENV_LOCAL_PATH
        for line in Path(ENV_LOCAL_PATH).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return bool(line.split("=", 1)[1].strip().strip("'\""))
    except OSError:
        pass
    return False


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


def inventory() -> List[Dict[str, Any]]:
    health = _photo_health()
    yt = _youtube_status()
    out = []
    for p in PROVIDERS:
        row = {k: p.get(k) for k in ("name", "purpose", "env", "implemented", "test", "production", "fallback", "limits")}
        configured = _env_set(p.get("env"))
        if p.get("files"):
            configured = bool(yt.get("client_secrets") and yt.get("token"))
        row["configured"] = configured
        h = health.get(p.get("health_key") or "", {}) if p.get("health_key") else {}
        row["recent_health"] = h or None
        if p.get("local"):
            status = NOT_REQUIRED_LOCAL
        elif not p["implemented"]:
            status = NOT_IMPLEMENTED
        elif configured is False:
            status = KEY_MISSING
        elif h and h.get("failed", 0) and not h.get("ok", 0):
            status = UNAVAILABLE
        elif h and (h.get("last_error") or {}).get("status") in (401, 403):
            status = CONFIGURED_INVALID
        elif configured is None:
            status = NO_KEY_NEEDED if not (h and h.get("failed") and h.get("failed") > h.get("ok", 0)) else UNAVAILABLE
        else:
            status = CONFIGURED_HEALTHY
        row["status"] = status
        out.append(row)
    return out


def summary() -> Dict[str, Any]:
    rows = inventory()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"providers": rows, "counts": counts,
            "note": "Credential values are never shown; 'configured' only says whether a value is set."}
