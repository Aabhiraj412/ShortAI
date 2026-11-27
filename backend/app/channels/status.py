"""Stage 4.01: per-channel status for the dashboard (read-only; no YouTube API call of its own).

English OAuth comes from the connection view the dashboard already caches. Hindi OAuth is reported from its own
token file's presence and its last recorded verification only. Each source is optional: an unavailable one is
reported as such and never hides the others.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.app.channels.registry import (ACTIVE, AUTH_REQUIRED, BLOCKED, MASTER, PENDING_VERIFICATION, VERIFIED,
                                           ChannelRegistry, get_registry)

ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING", "RETRYING", "WAITING_FOR_INTERNET", "RETRY_SCHEDULED")


def _ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _english_oauth() -> Dict[str, Any]:
    from backend.app.services import youtube_connection_service as ycs
    v = ycs.connection_view()
    return {"status": v.get("status"), "verification": v.get("channel_verification"),
            "token_valid": v.get("token_valid"), "message": v.get("message"),
            "last_verified_at": v.get("last_verified_at")}


def _english_analytics() -> Dict[str, Any]:
    from backend.app.services import youtube_analytics as ya
    from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
    auth = ya.auth_status(YouTubeOAuthManager())
    sync = ya._read_state(ya.SYNC_STATE_KEY)
    return {"state": auth.get("state"), "last_success_utc": auth.get("last_success_utc"),
            "last_sync": {k: sync.get(k) for k in ("finished_at_utc", "outcome", "videos") if k in sync} or None,
            "action": auth.get("action")}


def _publishing(db_path: str, ch: Dict[str, Any]) -> Dict[str, Any]:
    conn = _ro(db_path)
    try:
        if ch["role"] == MASTER:
            rows = conn.execute("SELECT upload_status, COUNT(*) FROM publishing_ledger WHERE upload_timestamp >= "
                                "strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 day') GROUP BY upload_status").fetchall()
            last = conn.execute("SELECT upload_timestamp, upload_status FROM publishing_ledger ORDER BY id DESC "
                                "LIMIT 1").fetchone()
            return {"queue": "publishing_ledger", "last_7_days": {r[0]: r[1] for r in rows},
                    "last": {"at_utc": last[0], "status": last[1]} if last else None}
        rows = conn.execute("SELECT status, COUNT(*) FROM content_localizations WHERE target_channel_key=? "
                            "GROUP BY status", (ch["channel_key"],)).fetchall()
        return {"queue": "content_localizations", "by_status": {r[0]: r[1] for r in rows},
                "enabled": ch["status"] == ACTIVE}
    finally:
        conn.close()


def _processes(db_path: str, ch: Dict[str, Any]) -> List[Dict[str, Any]]:
    conn = _ro(db_path)
    try:
        if ch["role"] == MASTER:
            marks = ",".join("?" * len(ACTIVE_JOB_STATUSES))
            rows = conn.execute(f"SELECT id, status, current_stage FROM persistent_jobs WHERE status IN ({marks}) "
                                "AND id NOT LIKE 'test%' AND id NOT LIKE 'mock%'", ACTIVE_JOB_STATUSES).fetchall()
            return [{"job_id": r[0], "status": r[1], "stage": r[2], "pipeline": "Shorts"} for r in rows]
        rows = conn.execute("SELECT source_job_id, status FROM content_localizations WHERE target_channel_key=? AND "
                            "status IN ('IN_PROGRESS','RENDERING','PUBLISHING')", (ch["channel_key"],)).fetchall()
        return [{"job_id": r[0], "status": r[1], "stage": r[1], "pipeline": "Shorts localization"} for r in rows]
    finally:
        conn.close()


def _actions(ch: Dict[str, Any], token_present: bool) -> List[str]:
    st = ch["status"]
    if st == ACTIVE:
        return []
    if st == PENDING_VERIFICATION and not token_present:
        return [f"Connect the {ch['name']} channel with its own Google account (Channels card → Connect). "
                "The English authorization is never used for it."]
    if st == PENDING_VERIFICATION:
        return ["Verify the channel (Channels card → Verify)."]
    if st == VERIFIED:
        return ["Identity verified. Activate the channel when its publishing setup is ready."]
    if st == AUTH_REQUIRED:
        return [f"Reconnect the {ch['name']} channel: {ch['status_reason']}"]
    if st == BLOCKED:
        return [ch["status_reason"]]
    return [ch["status_reason"]] if ch.get("status_reason") else []


def _safe(name: str, fn: Callable[[], Any], unavailable: List[str]) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"{name}: {type(exc).__name__}")
        return None


_ACTOR_REASON = re.compile(r"^(activated|deactivated) by .*", re.S)
EVENT_FIELDS = ("id", "ts_utc", "channel_key", "event", "previous_status", "new_status")


def display_reason(text: Optional[str]) -> str:
    """The dashboard never shows who activated a channel or why (older records stored "activated by <name>: <reason>";
    the record itself is kept unchanged in the database)."""
    m = _ACTOR_REASON.match(text or "")
    return m.group(1) if m else (text or "")


def channels_status(registry: Optional[ChannelRegistry] = None, *,
                    english_oauth: Callable[[], Dict[str, Any]] = _english_oauth,
                    english_analytics: Callable[[], Dict[str, Any]] = _english_analytics,
                    auto_verify_master: bool = True) -> Dict[str, Any]:
    reg = registry or get_registry()
    if auto_verify_master:
        try:   # the English status is already cached by the dashboard: no extra channels.list, no write if unchanged
            master = next((c for c in reg.list() if c["role"] == MASTER), None)
            if master:
                reg.verify(master["channel_key"], actor="dashboard")
        except Exception:
            pass
    out = []
    for ch in reg.list():
        ch = dict(ch, status_reason=display_reason(ch.get("status_reason")))
        unavailable: List[str] = []
        token = reg.credential_path(ch)
        token_present = token.exists()
        if ch["role"] == MASTER:
            oauth = _safe("oauth", english_oauth, unavailable) or {}
            analytics = _safe("analytics", english_analytics, unavailable) or {}
        else:
            oauth = {"status": "TOKEN_PRESENT" if token_present else "NOT_CONNECTED",
                     "verification": "VERIFIED" if ch.get("youtube_channel_id") else "PENDING",
                     "token_valid": None, "isolated_credential": True,
                     "message": ("own token file; verified identity " + str(ch.get("verified_title") or ch.get("youtube_channel_id"))
                                 if ch.get("youtube_channel_id") else
                                 "own token file present; not verified yet" if token_present else
                                 "no authorization yet (separate from English)")}
            analytics = {"state": "ENABLED" if ch["status"] == ACTIVE and ch["analytics_config"].get("enabled")
                         else "NOT_CONFIGURED", "action": None if ch["status"] == ACTIVE else
                         "available after the channel is verified and active"}
        out.append({
            "channel_key": ch["channel_key"], "name": ch["name"], "language": ch["language"], "role": ch["role"],
            "source_channel_key": ch["source_channel_key"], "status": ch["status"], "status_reason": ch["status_reason"],
            "active": ch["status"] == ACTIVE,
            "identity": {"youtube_channel_id": ch.get("youtube_channel_id"), "verified_title": ch.get("verified_title"),
                         "verified_at_utc": ch.get("verified_at_utc"), "method": "channels.list?mine=true"},
            "credential": {"ref": ch["credential_ref"], "present": token_present},
            "oauth": oauth, "analytics": analytics,
            "publishing": _safe("publishing", lambda: _publishing(reg.db_path, ch), unavailable),
            "processes": _safe("processes", lambda: _processes(reg.db_path, ch), unavailable) or [],
            "actions": _actions(ch, token_present), "unavailable": unavailable,
            "production_ready": ch["status"] == ACTIVE and bool(ch.get("youtube_channel_id")),
        })
    return {"channels": out, "events": [{k: e.get(k) for k in EVENT_FIELDS} for e in reg.events(limit=20)],
            "localizations": reg.localizations(limit=20),
            "note": "Identity comes from channels.list?mine=true only. Hindi uses its own token file and never the "
                    "English authorization; it localizes completed English Shorts and never discovers topics."}
