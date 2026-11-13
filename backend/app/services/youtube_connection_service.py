"""
YouTube connection state for the dashboard: one canonical status, non-secret history,
and a single-flight manual Connect/Reconnect that reuses the existing OAuth flow.

Canonical states
  CONNECTED                  token valid and the channel verified
  DISCONNECTED               no client configuration or no stored token
  REAUTHENTICATION_REQUIRED  Google rejected the stored authorization (revoked/expired refresh token, 401)
  REFRESHING                 an interactive connection is in progress
  CHANNEL_VERIFICATION_FAILED token valid but channels.list did not confirm a channel (403 permission, empty)
  API_UNAVAILABLE            Google could not be reached (network); credentials untouched
  QUOTA_EXCEEDED             YouTube Data API quota exhausted; credentials untouched

Only REAUTHENTICATION_REQUIRED asks for a reconnect. Temporary failures offer a retry.

History (last successful verification, last token refresh, last error, last connect
result) is stored under the studio setting `youtube.connection_state` so the dashboard
and the scheduler daemon share it. It never contains tokens, codes or secrets.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

STATE_KEY = "youtube.connection_state"
VERIFICATION_WRITE_INTERVAL_SEC = 600   # unchanged successful checks are recorded at most this often

CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"
REAUTHENTICATION_REQUIRED = "REAUTHENTICATION_REQUIRED"
REFRESHING = "REFRESHING"
CHANNEL_VERIFICATION_FAILED = "CHANNEL_VERIFICATION_FAILED"
API_UNAVAILABLE = "API_UNAVAILABLE"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"

_connect_lock = threading.Lock()
_state_lock = threading.Lock()
_last_written: dict[str, Any] = {"status": None, "at": 0.0}

_REDACTIONS = (
    (re.compile(r"(code|state|access_token|refresh_token|id_token|client_secret)=[^&\s]+", re.I), r"\1=***"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-\.]+"), "***"),
    (re.compile(r"1//[0-9A-Za-z_\-]{10,}"), "***"),
    (re.compile(r"https?://\S+"), "<url>"),
)


def redact(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = str(text)
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out[:300]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read() -> dict[str, Any]:
    from backend.app.services import control_center_service as cc
    raw = cc._read_setting(STATE_KEY)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _write(state: dict[str, Any]) -> None:
    from backend.app.services import control_center_service as cc
    cc._write_settings({STATE_KEY: json.dumps(state, sort_keys=True)})


def get_state() -> dict[str, Any]:
    return _read()


def record_event(kind: str, **fields: Any) -> None:
    """Called from the OAuth manager (dashboard and daemon). Never stores secrets."""
    import time
    with _state_lock:
        if kind == "verification":
            status = fields.get("status")
            ok = bool(fields.get("channel_connected"))
            now = time.monotonic()
            unchanged = status == _last_written["status"]
            if ok and unchanged and now - _last_written["at"] < VERIFICATION_WRITE_INTERVAL_SEC:
                return
            state = _read()
            state["last_check_at"] = _now_iso()
            state["last_check_status"] = status
            if ok:
                state["last_verified_at"] = state["last_check_at"]
                state["channel_title"] = fields.get("channel_title")
            else:
                detail = fields.get("error_detail") or {}
                state["last_error"] = {
                    "at": state["last_check_at"],
                    "status": status,
                    "kind": detail.get("kind"),
                    "http_status": detail.get("http_status"),
                    "reason": detail.get("reason") or detail.get("exception"),
                    "message": redact(detail.get("message")),
                }
            _write(state)
            _last_written.update(status=status, at=now)
        elif kind == "token_saved":
            state = _read()
            state["last_token_refresh_at"] = _now_iso()
            state["last_token_event"] = fields.get("event")
            _write(state)
        elif kind == "connect":
            state = _read()
            state["last_connect"] = {"at": _now_iso(), "ok": bool(fields.get("ok")),
                                     "code": fields.get("code"), "message": redact(fields.get("message"))}
            if not fields.get("ok"):
                state["last_error"] = {"at": state["last_connect"]["at"], "status": fields.get("code"),
                                       "kind": "connect", "message": redact(fields.get("message"))}
            _write(state)


def canonical_status(auth: dict[str, Any], connecting: bool = False) -> str:
    if connecting:
        return REFRESHING
    if not auth.get("secrets_installed") or not auth.get("token_installed"):
        return DISCONNECTED
    if auth.get("reconnect_required"):
        return REAUTHENTICATION_REQUIRED
    status = auth.get("status")
    if status == "connected" and auth.get("channel_connected"):
        return CONNECTED
    if status == "quota_exceeded":
        return QUOTA_EXCEEDED
    if status == "api_unavailable":
        return API_UNAVAILABLE
    return CHANNEL_VERIFICATION_FAILED


_ACTIONS = {
    CONNECTED: {"connect": False, "reconnect": False, "retry_check": True},
    DISCONNECTED: {"connect": True, "reconnect": False, "retry_check": False},
    REAUTHENTICATION_REQUIRED: {"connect": False, "reconnect": True, "retry_check": False},
    REFRESHING: {"connect": False, "reconnect": False, "retry_check": False},
    CHANNEL_VERIFICATION_FAILED: {"connect": False, "reconnect": True, "retry_check": True},
    API_UNAVAILABLE: {"connect": False, "reconnect": False, "retry_check": True},
    QUOTA_EXCEEDED: {"connect": False, "reconnect": False, "retry_check": True},
}

_MESSAGES = {
    CONNECTED: "YouTube is connected and the channel is verified.",
    DISCONNECTED: "No YouTube account is connected.",
    REAUTHENTICATION_REQUIRED: "Google no longer accepts the stored authorization. Reconnect YouTube.",
    REFRESHING: "Waiting for you to finish Google authorization in the browser…",
    CHANNEL_VERIFICATION_FAILED: "Signed in, but the YouTube channel could not be verified.",
    API_UNAVAILABLE: "Google could not be reached. Your connection is kept; retry the check later.",
    QUOTA_EXCEEDED: "The YouTube API daily quota is used up. Your connection is kept; it resets at midnight Pacific time.",
}


def connection_view(force: bool = False) -> dict[str, Any]:
    """Dashboard view. `force` bypasses the 5-minute status cache (Retry Connection Check)."""
    from backend.app.services.youtube_publishing_engine import (
        invalidate_auth_status_cache,
        youtube_publishing_engine,
    )
    oauth = youtube_publishing_engine.oauth
    connecting = _connect_lock.locked()
    if force and not connecting:
        invalidate_auth_status_cache()
    auth = {} if connecting else oauth.get_auth_status_cached()
    if connecting:
        auth = {"secrets_installed": True, "token_installed": True}
    status = canonical_status(auth, connecting=connecting)
    state = get_state()
    if not auth.get("secrets_installed", True) and not connecting:
        message = "The OAuth client configuration (client_secrets.json) is not installed."
    else:
        message = _MESSAGES[status]
    channel = (auth.get("channel_info") or {}) if status == CONNECTED else {}
    return {
        "status": status,
        "message": message,
        "channel_verification": ("VERIFIED" if status == CONNECTED else
                                 "PENDING" if status == REFRESHING else
                                 "NOT_CONNECTED" if status == DISCONNECTED else "FAILED"),
        "channel_title": channel.get("title") or state.get("channel_title"),
        "token_valid": bool(auth.get("is_authenticated")),
        "last_verified_at": state.get("last_verified_at"),
        "last_check_at": state.get("last_check_at"),
        "last_token_refresh_at": state.get("last_token_refresh_at"),
        "last_error": state.get("last_error"),
        "last_connect": state.get("last_connect"),
        "actions": dict(_ACTIONS[status]),
        "scopes": ["youtube.upload", "youtube.readonly"],
        "connect_in_progress": connecting,
    }


class ConnectInProgress(RuntimeError):
    pass


def connect(timeout_sec: int = 180) -> dict[str, Any]:
    """
    Runs the existing interactive OAuth flow (google-auth-oauthlib InstalledAppFlow:
    random state + PKCE, redirect only to its own localhost listener, offline access,
    consent prompt) once at a time. Existing credentials are replaced only on success.
    """
    from backend.app.services.youtube_publishing_engine import (
        invalidate_auth_status_cache,
        youtube_publishing_engine,
    )
    if not _connect_lock.acquire(blocking=False):
        raise ConnectInProgress("A YouTube connection is already in progress.")
    try:
        oauth = youtube_publishing_engine.oauth
        ok, channel, error = oauth.start_interactive_auth(timeout_sec=timeout_sec, open_browser=True)
        code = getattr(oauth, "last_connect_error_code", None)
    finally:
        _connect_lock.release()
        invalidate_auth_status_cache()
    result = {"ok": bool(ok and channel), "authorized": bool(ok), "code": code or ("CONNECTED" if ok and channel else None),
              "message": redact(error) if error else "YouTube connected and channel verified.",
              "channel_title": (channel or {}).get("title")}
    result["connection"] = connection_view()
    return result
