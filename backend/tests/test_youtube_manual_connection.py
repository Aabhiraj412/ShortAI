"""
Manual YouTube Connect / Reconnect (dashboard card + guarded endpoints).

Everything external is mocked: InstalledAppFlow (Google consent + code exchange) and the
YouTube Data API client. Credentials go to temporary files; settings to a temporary DB.
No real authorization, token refresh, upload or network call happens here.
"""
import json
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, InvalidGrantError, MismatchingStateError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.secure_storage import read_protected_file, write_protected_file
from backend.app.database.db import Base
from backend.app.main import app
from backend.app.services import control_center_service as control
from backend.app.services import youtube_connection_service as ycs
from backend.app.services import youtube_publishing_engine as ype

LOCAL = dict(base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
CHANNEL = {"id": "UC_test", "snippet": {"title": "Test Channel", "thumbnails": {}}, "statistics": {}, "status": {}}


def _creds(refresh="refresh-old", token="access-1", hours=1):
    return Credentials(token=token, refresh_token=refresh, token_uri="https://oauth2.googleapis.com/token",
                       client_id="cid", client_secret="csecret", scopes=list(ype.YOUTUBE_SCOPES),
                       expiry=datetime.utcnow() + timedelta(hours=hours))


def _http_error(status, reason):
    body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(control, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    oauth = ype.youtube_publishing_engine.oauth
    monkeypatch.setattr(oauth, "token_file", str(tmp_path / "token.json"))
    monkeypatch.setattr(oauth, "client_secrets_file", str(tmp_path / "client_secrets.json"))
    write_protected_file(oauth.client_secrets_file, json.dumps(
        {"installed": {"client_id": "cid", "client_secret": "csecret",
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token"}}).encode())
    ycs._last_written.update(status=None, at=0.0)
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.return_value = {"items": [CHANNEL]}
    monkeypatch.setattr(ype, "build", lambda *a, **k: yt)
    flow = MagicMock()
    monkeypatch.setattr(ype.InstalledAppFlow, "from_client_config", classmethod(lambda cls, *a, **k: flow))
    yield {"oauth": oauth, "yt": yt, "flow": flow, "tmp": tmp_path}
    engine.dispose()


def _stored(env):
    data = read_protected_file(env["oauth"].token_file)
    return json.loads(data.decode()) if data else None


def _store(env, creds):
    env["oauth"].save_credentials(creds, event="migrate")


# 1. Successful connection --------------------------------------------------------
def test_successful_connection(env):
    env["flow"].run_local_server.return_value = _creds(refresh="refresh-new")
    res = ycs.connect(timeout_sec=5)
    assert res["ok"] and res["channel_title"] == "Test Channel"
    assert res["connection"]["status"] == ycs.CONNECTED
    kwargs = env["flow"].run_local_server.call_args.kwargs
    assert kwargs["access_type"] == "offline" and kwargs["prompt"] == "consent"
    assert kwargs["host"] == "localhost"          # redirect only to the local listener
    assert _stored(env)["refresh_token"] == "refresh-new"
    view = ycs.connection_view()
    assert view["last_verified_at"] and view["last_token_refresh_at"] and view["last_connect"]["ok"]


# 2. Successful reconnection --------------------------------------------------------
def test_successful_reconnection_replaces_credentials(env):
    _store(env, _creds(refresh="refresh-old", token="access-old"))
    env["flow"].run_local_server.return_value = _creds(refresh="refresh-new", token="access-new")
    assert ycs.connect(timeout_sec=5)["ok"]
    assert _stored(env)["refresh_token"] == "refresh-new" and _stored(env)["token"] == "access-new"


# 3-5. Cancelled, invalid state, expired code: nothing saved, old credentials kept ---
@pytest.mark.parametrize("exc, code", [
    (AccessDeniedError(description="access_denied"), "CANCELLED"),
    (MismatchingStateError(), "INVALID_STATE"),
    (InvalidGrantError(description="Bad Request"), "CODE_EXPIRED"),
    (TransportError("connection reset"), "NETWORK"),
])
def test_failed_authorization_keeps_existing_credentials(env, exc, code):
    _store(env, _creds(refresh="refresh-old"))
    before = _stored(env)
    env["flow"].run_local_server.side_effect = exc
    res = ycs.connect(timeout_sec=5)
    assert res["ok"] is False and res["code"] == code
    assert _stored(env) == before
    assert ycs.get_state()["last_error"]["status"] == code


def test_consent_timeout_is_reported_as_timed_out(env):
    class WSGITimeoutError(AttributeError):   # what google-auth-oauthlib raises
        pass
    env["flow"].run_local_server.side_effect = WSGITimeoutError("Timed out waiting for response from authorization server")
    assert ycs.connect(timeout_sec=1)["code"] == "TIMED_OUT"


# 6. Missing refresh token in the response ------------------------------------------
def test_missing_refresh_token_keeps_the_stored_one(env):
    _store(env, _creds(refresh="refresh-old"))
    env["flow"].run_local_server.return_value = _creds(refresh=None, token="access-new")
    assert ycs.connect(timeout_sec=5)["ok"]
    stored = _stored(env)
    assert stored["refresh_token"] == "refresh-old" and stored["token"] == "access-new"


def test_token_refresh_without_refresh_token_in_response_keeps_it(env):
    _store(env, _creds(refresh="refresh-old"))
    env["oauth"].save_credentials(_creds(refresh=None, token="access-2"))
    assert _stored(env)["refresh_token"] == "refresh-old"


# 7. Invalid refresh token: genuine authorization failure -> reconnect --------------
def test_rejected_refresh_token_requires_reauthentication(env):
    _store(env, _creds(hours=-2))
    before = _stored(env)
    with patch.object(Credentials, "refresh", side_effect=RefreshError("invalid_grant: Token has been expired or revoked.")):
        view = ycs.connection_view(force=True)
    assert view["status"] == ycs.REAUTHENTICATION_REQUIRED
    assert view["actions"] == {"connect": False, "reconnect": True, "retry_check": False}
    assert _stored(env) == before                 # nothing erased


# 8. Temporary network failure: never a reconnect -----------------------------------
def test_network_failure_during_refresh_is_api_unavailable(env):
    _store(env, _creds(hours=-2))
    with patch.object(Credentials, "refresh", side_effect=TransportError("Failed to establish a new connection")):
        view = ycs.connection_view(force=True)
    assert view["status"] == ycs.API_UNAVAILABLE
    assert view["actions"]["reconnect"] is False and view["actions"]["retry_check"] is True


def test_network_failure_during_verification_is_api_unavailable(env):
    _store(env, _creds())
    env["yt"].channels.return_value.list.return_value.execute.side_effect = TimeoutError(10060, "timed out")
    view = ycs.connection_view(force=True)
    assert view["status"] == ycs.API_UNAVAILABLE and view["token_valid"] is True
    assert view["last_error"]["kind"] == "network"


# 9. Quota exhaustion ------------------------------------------------------------
def test_quota_exhaustion_keeps_credentials_and_offers_retry(env):
    _store(env, _creds())
    before = _stored(env)
    env["yt"].channels.return_value.list.return_value.execute.side_effect = _http_error(403, "quotaExceeded")
    view = ycs.connection_view(force=True)
    assert view["status"] == ycs.QUOTA_EXCEEDED and view["actions"]["reconnect"] is False
    assert view["last_error"]["reason"] == "quotaExceeded" and _stored(env) == before


# 10. Channel verification failure ----------------------------------------------
def test_channel_verification_failure(env):
    _store(env, _creds())
    env["yt"].channels.return_value.list.return_value.execute.side_effect = _http_error(403, "forbidden")
    view = ycs.connection_view(force=True)
    assert view["status"] == ycs.CHANNEL_VERIFICATION_FAILED
    assert view["actions"] == {"connect": False, "reconnect": True, "retry_check": True}


# 11. Successful verification after reconnect -----------------------------------
def test_verification_succeeds_after_reconnect(env):
    _store(env, _creds())
    env["yt"].channels.return_value.list.return_value.execute.side_effect = _http_error(403, "forbidden")
    assert ycs.connection_view(force=True)["status"] == ycs.CHANNEL_VERIFICATION_FAILED
    env["yt"].channels.return_value.list.return_value.execute.side_effect = None
    env["flow"].run_local_server.return_value = _creds(refresh="refresh-new")
    res = ycs.connect(timeout_sec=5)
    assert res["connection"]["status"] == ycs.CONNECTED and res["connection"]["last_verified_at"]


# 12. Status cache invalidation ----------------------------------------------------
def test_status_cache_is_used_bypassed_and_invalidated(env):
    _store(env, _creds())
    calls = []
    real = ype.YouTubeOAuthManager.get_auth_status

    def counting(self):
        calls.append(1)
        return real(self)

    with patch.object(ype.YouTubeOAuthManager, "get_auth_status", counting):
        ycs.connection_view()
        ycs.connection_view()
        assert len(calls) == 1                     # cached
        ycs.connection_view(force=True)
        assert len(calls) == 2                     # Retry Connection Check bypasses
        env["flow"].run_local_server.return_value = _creds(refresh="refresh-new")
        ycs.connect(timeout_sec=5)                 # connect invalidates and re-verifies
        n = len(calls)
        ycs.connection_view()
        assert len(calls) == n                     # fresh result cached again


# 13. Dashboard button visibility -------------------------------------------------
@pytest.mark.parametrize("auth, status", [
    ({"secrets_installed": True, "token_installed": False}, ycs.DISCONNECTED),
    ({"secrets_installed": False, "token_installed": True}, ycs.DISCONNECTED),
    ({"secrets_installed": True, "token_installed": True, "reconnect_required": True}, ycs.REAUTHENTICATION_REQUIRED),
    ({"secrets_installed": True, "token_installed": True, "status": "connected", "channel_connected": True}, ycs.CONNECTED),
    ({"secrets_installed": True, "token_installed": True, "status": "quota_exceeded"}, ycs.QUOTA_EXCEEDED),
    ({"secrets_installed": True, "token_installed": True, "status": "api_unavailable"}, ycs.API_UNAVAILABLE),
    ({"secrets_installed": True, "token_installed": True, "status": "channel_verification_failed"}, ycs.CHANNEL_VERIFICATION_FAILED),
])
def test_canonical_status_and_button_visibility(auth, status):
    assert ycs.canonical_status(auth) == status
    actions = ycs._ACTIONS[status]
    assert actions["connect"] == (status == ycs.DISCONNECTED)
    assert actions["reconnect"] == (status in (ycs.REAUTHENTICATION_REQUIRED, ycs.CHANNEL_VERIFICATION_FAILED))
    assert ycs.canonical_status(auth, connecting=True) == ycs.REFRESHING


def test_dashboard_renders_connection_card():
    html = TestClient(app, **LOCAL).get("/dashboard").text
    for el in ("ytConnCard", "ytBtnConnect", "ytBtnReconnect", "ytBtnRetry", "ytConnVerifiedAt",
               "ytConnRefreshAt", "/api/youtube/connection/connect", "Retry Connection Check"):
        assert el in html
    assert "errBox.textContent" in html            # errors rendered as text, never HTML


# --- Security ---------------------------------------------------------------------
def test_endpoints_never_return_tokens(env):
    _store(env, _creds(refresh="refresh-SECRET", token="access-SECRET"))
    body = TestClient(app, **LOCAL).get("/api/youtube/connection").text
    assert "SECRET" not in body and "csecret" not in body


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "null"},
])
def test_cross_site_connect_and_check_are_refused(env, headers):
    c = TestClient(app, **LOCAL)
    assert c.post("/api/youtube/connection/connect", json={"confirm": True}, headers=headers).status_code == 403
    assert c.post("/api/youtube/connection/check", headers=headers).status_code == 403
    assert c.post("/api/stage-3-22/youtube/connect", headers=headers).status_code == 403
    env["flow"].run_local_server.assert_not_called()


def test_remote_clients_and_unconfirmed_requests_are_refused(env):
    assert TestClient(app).post("/api/youtube/connection/connect", json={"confirm": True}).status_code == 403
    assert TestClient(app).get("/api/youtube/connection").status_code == 403
    c = TestClient(app, **LOCAL)
    assert c.post("/api/youtube/connection/connect", json={}).status_code == 400
    env["flow"].run_local_server.assert_not_called()


def test_same_origin_dashboard_request_is_allowed(env):
    env["flow"].run_local_server.return_value = _creds(refresh="refresh-new")
    r = TestClient(app, **LOCAL).post("/api/youtube/connection/connect", json={"confirm": True},
                                      headers={"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_only_one_connection_attempt_at_a_time(env):
    assert ycs._connect_lock.acquire(blocking=False)
    try:
        assert TestClient(app, **LOCAL).post("/api/youtube/connection/connect",
                                             json={"confirm": True}).status_code == 409
        assert ycs.connection_view()["status"] == ycs.REFRESHING
    finally:
        ycs._connect_lock.release()


def test_authorization_codes_are_never_logged_or_stored(env, caplog):
    leaky = InvalidGrantError(description="code=4/0SECRETCODE&state=xyz invalid")
    env["flow"].run_local_server.side_effect = leaky
    with caplog.at_level(logging.DEBUG):
        res = ycs.connect(timeout_sec=5)
    assert "SECRETCODE" not in caplog.text
    assert "SECRETCODE" not in json.dumps(res) and "SECRETCODE" not in json.dumps(ycs.get_state())


def test_redaction_helper():
    out = ycs.redact("failed https://x/cb?code=4/abc&state=s token ya29.AbC refresh 1//0gSECRETTOKENVALUE")
    assert "4/abc" not in out and "ya29.AbC" not in out and "SECRETTOKENVALUE" not in out and "https://" not in out


# --- Credential storage (audit R1) --------------------------------------------------
def test_loading_protected_credentials_does_not_rewrite_the_token(env):
    _store(env, _creds())
    with patch.object(ype, "write_protected_file") as write:
        env["oauth"]._load_credentials()
    write.assert_not_called()


def test_plaintext_token_on_disk_is_migrated_once(env):
    with open(env["oauth"].token_file, "w", encoding="utf-8") as f:
        f.write(_creds().to_json())
    env["oauth"]._load_credentials()
    with open(env["oauth"].token_file, "rb") as f:
        assert not f.read(64).lstrip().startswith(b"{")          # now protected
    assert _stored(env)["refresh_token"] == "refresh-old"
