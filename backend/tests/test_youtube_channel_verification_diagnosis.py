"""
YouTube channel verification (2026-11-13 "channel_verification_failed" diagnosis).

Every channels.list outcome is mocked (no network, no real credentials). The root
cause was network-level: IPv6 to Google is dropped on this machine's network, and
httplib2 re-raises the first connect timeout instead of trying the next address, so
every YouTube API call failed after ~21 s. network_preferences puts IPv4 first for
*.googleapis.com; get_auth_status now reports the real failure instead of one
generic status.
"""
import json
import os
import socket
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from backend.app.core import network_preferences as netpref
from backend.app.services import youtube_publishing_engine as ype
from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager


class _Creds:
    refresh_token = "r"
    expiry = None
    valid = True
    expired = False
    scopes = list(ype.YOUTUBE_SCOPES)


def _mgr(tmp_path):
    m = YouTubeOAuthManager.__new__(YouTubeOAuthManager)
    m.token_file = str(tmp_path / "token.json")
    m.client_secrets_file = str(tmp_path / "secrets.json")
    m.scopes = list(ype.YOUTUBE_SCOPES)
    (tmp_path / "token.json").write_text("{}")
    return m


def _http_error(status, reason):
    body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}], "message": reason}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


def _status(tmp_path, execute):
    """Run get_auth_status with a mocked client whose channels.list().execute() does `execute`."""
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.side_effect = execute
    m = _mgr(tmp_path)
    with patch.object(YouTubeOAuthManager, "_load_credentials", lambda self: _Creds()), \
         patch.object(ype, "build", return_value=yt), \
         patch.object(YouTubeOAuthManager, "save_credentials") as save:
        st = m.get_auth_status()
    return st, save


CHANNEL = {"id": "UC_test", "snippet": {"title": "T", "thumbnails": {}}, "statistics": {}, "status": {}}


def test_1_valid_token_and_channel(tmp_path):
    st, _ = _status(tmp_path, lambda: {"items": [CHANNEL]})
    assert (st["status"], st["channel_connected"], st["is_authenticated"]) == ("connected", True, True)
    assert st["error_detail"] is None


def test_2_valid_token_but_no_channel(tmp_path):
    st, _ = _status(tmp_path, lambda: {"items": []})
    assert (st["status"], st["channel_connected"], st["reconnect_required"]) == ("not_connected", False, False)
    assert st["error_detail"]["kind"] == "empty_response"


def test_3_http_401_is_reported_as_revoked_and_requires_reconnect(tmp_path):
    def boom():
        raise _http_error(401, "authError")
    st, _ = _status(tmp_path, boom)
    assert st["status"] == "authorization_revoked" and st["reconnect_required"] is True
    assert st["error_detail"]["http_status"] == 401


@pytest.mark.parametrize("reason, expected", [
    ("insufficientPermissions", "channel_verification_failed"),
    ("accessNotConfigured", "channel_verification_failed"),
    ("youtubeSignupRequired", "channel_verification_failed"),
    ("forbidden", "channel_verification_failed"),
    ("quotaExceeded", "quota_exceeded"),
])
def test_4_5_6_403s_keep_the_google_reason_and_never_demand_reconnect(tmp_path, reason, expected):
    def boom():
        raise _http_error(403, reason)
    st, _ = _status(tmp_path, boom)
    assert st["status"] == expected
    assert st["reconnect_required"] is False and st["is_authenticated"] is True
    assert st["error_detail"] == {"kind": "http", "http_status": 403, "reason": reason}


def test_7_network_timeout_is_api_unavailable_not_a_channel_problem(tmp_path):
    def boom():
        raise TimeoutError(10060, "A connection attempt failed")
    st, _ = _status(tmp_path, boom)
    assert st["status"] == "api_unavailable" and st["is_authenticated"] is True
    assert st["reconnect_required"] is False
    assert st["error_detail"]["kind"] == "network" and st["error_detail"]["exception"] == "TimeoutError"


def test_8_multiple_channels_uses_the_first(tmp_path):
    second = {**CHANNEL, "id": "UC_other"}
    st, _ = _status(tmp_path, lambda: {"items": [CHANNEL, second]})
    assert st["channel_info"]["id"] == "UC_test"


def test_9_unexpected_response_shape_reads_as_not_connected(tmp_path):
    st, _ = _status(tmp_path, lambda: {"kind": "youtube#channelListResponse"})
    assert st["status"] == "not_connected" and st["error_detail"]["kind"] == "empty_response"


def test_10_11_failures_are_cached_briefly_and_successes_longer(tmp_path, monkeypatch):
    m = _mgr(tmp_path)
    results = [{"status": "api_unavailable", "channel_connected": False},
               {"status": "connected", "channel_connected": True}]
    clock = [1000.0]
    monkeypatch.setattr(ype.time, "monotonic", lambda: clock[0])
    with patch.object(YouTubeOAuthManager, "get_auth_status", lambda self: results.pop(0)):
        assert m.get_auth_status_cached(max_age=300)["status"] == "api_unavailable"
        clock[0] += 30
        assert m.get_auth_status_cached(max_age=300)["status"] == "api_unavailable"   # cached
        clock[0] += 31
        assert m.get_auth_status_cached(max_age=300)["status"] == "connected"         # failure TTL 60 s
        clock[0] += 200
        assert m.get_auth_status_cached(max_age=300)["status"] == "connected"         # success cached
    assert results == []


def test_12_verification_failure_never_writes_or_erases_credentials(tmp_path):
    def boom():
        raise TimeoutError(10060, "timed out")
    st, save = _status(tmp_path, boom)
    save.assert_not_called()
    assert (tmp_path / "token.json").read_text() == "{}"
    assert st["token_installed"] is True


# --- Network: IPv4 first for Google API hosts ----------------------------------
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


class _FakeSock:
    families = []

    def __init__(self, family, *a, **k):
        self.family = family
        _FakeSock.families.append(family)

    def setsockopt(self, *a):
        pass

    def settimeout(self, *a):
        pass

    def connect(self, addr):
        if self.family == socket.AF_INET6:
            raise TimeoutError(10060, "connection attempt timed out")   # what Windows raises

    def close(self):
        pass


@pytest.fixture
def fake_dns():
    """No preference installed and DNS answering IPv6 first; restores the real lookup after."""
    netpref.uninstall_google_ipv4_preference()
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [V6, V4]
    try:
        yield
    finally:
        netpref.uninstall_google_ipv4_preference()
        socket.getaddrinfo = real
        with patch.dict(os.environ, {"CRAZZYDOCS_PREFER_IPV4_FOR_GOOGLE": "1"}):
            netpref.install_google_ipv4_preference()   # the app state after importing the engine


def test_httplib2_itself_gives_up_on_the_first_timed_out_address(fake_dns):
    """Root cause (library behaviour): a timed-out IPv6 address is never followed by IPv4."""
    _FakeSock.families = []
    conn = httplib2.HTTPSConnectionWithTimeout("youtube.googleapis.com", 443)
    with patch("httplib2.socket.socket", _FakeSock):
        with pytest.raises(TimeoutError):
            conn.connect()
    assert _FakeSock.families == [socket.AF_INET6]


def test_ipv4_first_lets_httplib2_reach_google_through_ipv4(fake_dns):
    assert netpref.install_google_ipv4_preference()
    _FakeSock.families = []
    conn = httplib2.HTTPSConnectionWithTimeout("youtube.googleapis.com", 443)
    conn._context = MagicMock()
    with patch("httplib2.socket.socket", _FakeSock):
        conn.connect()
    assert _FakeSock.families == [socket.AF_INET]


def test_ipv4_first_only_touches_google_api_hosts_and_keeps_ipv6_fallback(fake_dns):
    netpref.install_google_ipv4_preference()
    assert socket.getaddrinfo("www.googleapis.com", 443) == [V4, V6]
    assert socket.getaddrinfo(b"YouTube.GoogleAPIs.com.", 443) == [V4, V6]
    assert socket.getaddrinfo("example.com", 443) == [V6, V4]
    assert socket.getaddrinfo("googleapis.com.evil.example", 443) == [V6, V4]
    wrapped = socket.getaddrinfo
    assert netpref.install_google_ipv4_preference()      # idempotent
    assert socket.getaddrinfo is wrapped


def test_ipv4_preference_can_be_disabled(fake_dns):
    original = socket.getaddrinfo
    with patch.dict(os.environ, {"CRAZZYDOCS_PREFER_IPV4_FOR_GOOGLE": "0"}):
        assert netpref.install_google_ipv4_preference() is False
    assert socket.getaddrinfo is original


def test_fixture_leaves_the_real_lookup_with_the_preference_active():
    # Runs after the fake_dns tests: the real resolver is back, wrapped once.
    assert netpref._original_getaddrinfo is not None
    assert getattr(socket.getaddrinfo, "__wrapped__", None) is netpref._original_getaddrinfo
    assert "lambda" not in getattr(netpref._original_getaddrinfo, "__qualname__", "")


def test_youtube_engine_installs_the_preference_at_import_time():
    # Both the dashboard and the scheduler daemon import this module before any API call.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ype))
    top_level_calls = [n.value.func.id for n in tree.body
                       if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                       and isinstance(n.value.func, ast.Name)]
    assert "install_google_ipv4_preference" in top_level_calls
    assert netpref._original_getaddrinfo is not None   # active in this test process too
