"""Stage 3.32F: minimal client for the OFFICIAL YouTube Data API posts endpoints (and channels.list for the channel
id). The only Community module that makes network requests to YouTube.

Endpoints (developers.google.com/youtube/data/reference/rest/v3/posts, read 2026-11-20):
* GET  https://www.googleapis.com/youtube/v3/channels?part=id&mine=true            (default quota, 1 unit)
* GET  https://www.googleapis.com/youtube/v3/posts?part=...&channelId=...          ("Post List Queries", 1 unit)
* POST https://www.googleapis.com/upload/youtube/v3/posts/image?channelId=...      ("Post Modifications and Image
                                                                                     Uploads", 1 unit)
* POST https://www.googleapis.com/youtube/v3/posts?part=snippet,attachments        ("Post Uploads", 1 unit;
                                                                                     public and immediate)
The posts resource is in a PILOT limited to approved Google Cloud projects. No undocumented endpoint, no browser
automation. Credentials are used in memory only: an expired access token is refreshed in memory and never written.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
TIMEOUT = 30.0
YT = "https://www.googleapis.com/auth/"
PUBLISH_SCOPES = {YT + "youtube", YT + "youtube.force-ssl", YT + "youtubepartner"}
LIST_SCOPES = PUBLISH_SCOPES | {YT + "youtube.readonly"}
IMAGE_UPLOAD_SCOPES = PUBLISH_SCOPES | {YT + "youtube.upload"}


class NotConnected(Exception):
    """No usable YouTube credentials are installed."""


class ApiError(Exception):
    """A YouTube API error. ``ambiguous`` is True when the request may have reached YouTube and its outcome is
    unknown (timeout, dropped connection, 5xx on a write): such a write must never be retried blindly."""

    def __init__(self, status: Optional[int], reason: str, message: str, *, ambiguous: bool = False):
        super().__init__(f"{status} {reason}: {message}")
        self.status, self.reason, self.message, self.ambiguous = status, reason, message[:300], ambiguous

    def public(self) -> Dict[str, Any]:
        return {"http_status": self.status, "reason": self.reason, "message": self.message, "ambiguous": self.ambiguous}


class LocalCredentials:
    """The existing CrazzyDocs OAuth token (DPAPI-protected ``backend/token.json``), read-only. Exposes the granted
    scopes and an access token; refreshes an expired token IN MEMORY only (the file is never written here)."""

    def __init__(self, token_file: Optional[str] = None):
        from backend.app.services.youtube_publishing_engine import DEFAULT_TOKEN_FILE
        self.token_file = token_file or DEFAULT_TOKEN_FILE
        self._creds = None

    def _data(self) -> Dict[str, Any]:
        from backend.app.core.secure_storage import read_protected_file
        raw = read_protected_file(self.token_file)
        if raw is None:
            raise NotConnected("no YouTube token is installed")
        return json.loads(raw.decode("utf-8"))

    def granted_scopes(self) -> List[str]:
        return list(self._data().get("scopes") or [])

    def project_id(self) -> Optional[str]:
        try:
            from backend.app.core.secure_storage import read_protected_file
            from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
            raw = read_protected_file(YouTubeOAuthManager().client_secrets_file)
            cfg = json.loads(raw.decode("utf-8")) if raw else {}
            return (cfg.get("installed") or cfg.get("web") or {}).get("project_id")
        except Exception:
            return None

    def access_token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        if self._creds is None:
            data = self._data()
            self._creds = Credentials.from_authorized_user_info(data, data.get("scopes"))
        if not self._creds.valid:
            if not self._creds.refresh_token:
                raise NotConnected("the YouTube token cannot be refreshed")
            self._creds.refresh(Request())                   # in memory only; nothing is written
        return self._creds.token


def _error(exc: urllib.error.HTTPError, *, write: bool) -> ApiError:
    try:
        body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
    except (ValueError, OSError):
        body = {}
    err = body.get("error") or {}
    errors = err.get("errors") or [{}]
    reason = str(errors[0].get("reason") or (err.get("status") or "") or "").strip()
    for d in err.get("details") or []:
        if isinstance(d, dict) and d.get("reason"):
            reason = reason or str(d["reason"])
            if d["reason"] in ("SERVICE_DISABLED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
                reason = str(d["reason"])
    message = str(err.get("message") or exc.reason or "")
    return ApiError(exc.code, reason or "unknown", message, ambiguous=write and exc.code >= 500)


class PostsApi:
    def __init__(self, credentials: Any, *, opener: Optional[Callable[..., Any]] = None):
        self.credentials = credentials
        self.opener = opener or urllib.request.urlopen

    def _call(self, method: str, url: str, *, body: Optional[bytes] = None, content_type: Optional[str] = None,
              write: bool = False) -> Dict[str, Any]:
        headers = {"Authorization": "Bearer " + self.credentials.access_token(), "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener(req, timeout=TIMEOUT) as resp:
                raw = resp.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise _error(exc, write=write) from None
        except (socket.timeout, TimeoutError) as exc:
            raise ApiError(None, "timeout", f"no response within {int(TIMEOUT)} s", ambiguous=write) from exc
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            raise ApiError(None, "network", type(exc).__name__, ambiguous=write) from exc
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ApiError(200, "invalidResponse", "response was not JSON", ambiguous=write) from None

    def channel_id(self) -> str:
        data = self._call("GET", f"{API}/channels?part=id&mine=true")
        items = data.get("items") or []
        if not items:
            raise ApiError(404, "channelNotFound", "the authorised account has no channel")
        return str(items[0]["id"])

    def list_posts(self, channel_id: str, max_results: int = 5) -> List[Dict[str, Any]]:
        q = urllib.parse.urlencode({"part": "id,snippet,status", "channelId": channel_id, "maxResults": max_results})
        return list(self._call("GET", f"{API}/posts?{q}").get("items") or [])

    def image_upload(self, channel_id: str, path: Path, mime: str = "image/jpeg") -> str:
        q = urllib.parse.urlencode({"channelId": channel_id, "uploadType": "media"})
        data = self._call("POST", f"{UPLOAD}/posts/image?{q}", body=Path(path).read_bytes(), content_type=mime,
                          write=False)                       # an unused token is harmless: never ambiguous
        token = data.get("imageUploadToken")
        if not token:
            raise ApiError(200, "invalidResponse", "no imageUploadToken in the response")
        return str(token)

    def insert(self, channel_id: str, text: str, upload_tokens: List[str]) -> Dict[str, Any]:
        body = {"snippet": {"channelId": channel_id, "textDisplay": text}}
        part = "snippet"
        if upload_tokens:
            body["attachments"] = {"imageAttachments": [{"uploadToken": t} for t in upload_tokens]}
            part = "snippet,attachments"
        data = self._call("POST", f"{API}/posts?part={part}", body=json.dumps(body).encode("utf-8"),
                          content_type="application/json", write=True)
        if not data.get("id"):
            raise ApiError(200, "invalidResponse", "insert returned no post id", ambiguous=True)
        return data
