"""Dashboard-started YouTube Analytics (re)authorization and the unified Analytics state model (2026-11-28).
Fakes only: no browser, no Google request, no token file."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from backend.app.services import youtube_analytics as ya
from backend.app.services.youtube_publishing_engine import YOUTUBE_SCOPES

EN = "UC_english_verified"
ALL = YOUTUBE_SCOPES + [ya.ANALYTICS_SCOPE]


@pytest.fixture(autouse=True)
def clean_state():
    for k in (ya.AUTH_STATE_KEY, ya.API_STATE_KEY, ya.SYNC_STATE_KEY, ya.AUTHZ_STATE_KEY):
        ya._write_state(k, {})
    yield


class Mgr:
    token_file, client_secrets_file = "no-such-token.json", "cs"

    def __init__(self):
        self.saved = []

    def save_credentials(self, creds, event="refresh"):
        self.saved.append((creds, event))

    def get_auth_status(self):
        return {"channel_info": {"id": EN}}


class Flow:
    def __init__(self, creds=None, exc=None):
        self.creds, self.exc, self.kwargs, self.scopes, self.state = creds, exc, None, None, None

    def factory(self, scopes, state):
        self.scopes, self.state = scopes, state
        return self

    def run_local_server(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.creds


class Exec:
    def __init__(self, value=None, exc=None):
        self.value, self.exc = value, exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.value


def yt(ids):
    return lambda c: SimpleNamespace(channels=lambda: SimpleNamespace(list=lambda **k: Exec({"items": [{"id": i} for i in ids]})))


def an(exc=None, calls=None):
    def query(**k):
        if calls is not None:
            calls.append(k)
        return Exec({"rows": []}, exc)
    return lambda c: SimpleNamespace(reports=lambda: SimpleNamespace(query=query))


def creds(scopes=ALL):
    return SimpleNamespace(refresh_token="r", token="t", granted_scopes=scopes)


def _auth(mgr, flow, ids=(EN,), analytics=None, monkeypatch=None, **kw):
    monkeypatch.setattr(ya, "_expected_channel_id", lambda ch, m: EN)
    return ya.authorize_analytics(mgr, open_browser=False, flow_factory=flow.factory, youtube_factory=yt(list(ids)),
                                  analytics_factory=analytics or an(), today=date(2026, 11, 28), **kw)


def test_requests_existing_scopes_plus_analytics_with_random_state(monkeypatch):
    f, m = Flow(creds()), Mgr()
    res = _auth(m, f, monkeypatch=monkeypatch)
    assert f.scopes == ALL and len(f.state) >= 40
    assert f.kwargs["access_type"] == "offline" and f.kwargs["include_granted_scopes"] == "true"
    assert f.kwargs["host"] == "localhost" and f.kwargs["prompt"] == "consent"
    assert res["success"] and res["channel_verified"] and res["analytics_verified"]
    assert len(m.saved) == 1 and m.saved[0][1] == "connect"
    f2 = Flow(creds())
    _auth(Mgr(), f2, monkeypatch=monkeypatch)
    assert f2.state != f.state


def test_success_marks_authorized_and_api_connected(monkeypatch):
    calls = []
    _auth(Mgr(), Flow(creds()), analytics=an(calls=calls), monkeypatch=monkeypatch)
    assert calls and calls[0]["metrics"] == "views" and calls[0]["ids"] == "channel==MINE"
    ov = ya.analytics_overview(Mgr())
    assert ov["oauth"]["authorized"] and ov["api"]["status"] == "CONNECTED"
    assert ov["overall"] == "METRICS_PENDING"                 # authorized + API OK, but no metrics yet
    assert ov["last_authorization"]["ok"] is True


@pytest.mark.parametrize("case,expected_code", [
    ("missing_scope", "SCOPES_NOT_GRANTED"),
    ("wrong_channel", "WRONG_CHANNEL"),
    ("two_channels", "WRONG_CHANNEL"),
    ("analytics_fails", "ANALYTICS_VERIFICATION_FAILED"),
    ("cancelled", "CANCELLED"),
    ("state_mismatch", "STATE_MISMATCH"),
])
def test_any_failure_leaves_existing_credentials_untouched(monkeypatch, case, expected_code):
    m = Mgr()
    flow, ids, analytics = Flow(creds()), (EN,), None
    if case == "missing_scope":
        flow = Flow(creds(YOUTUBE_SCOPES))
    elif case == "wrong_channel":
        ids = ("UC_hindi_channel",)
    elif case == "two_channels":
        ids = (EN, "UC_other")
    elif case == "analytics_fails":
        analytics = an(exc=RuntimeError("403 insufficient scope"))
    elif case == "cancelled":
        flow = Flow(exc=type("AccessDeniedError", (Exception,), {})("access_denied"))
    elif case == "state_mismatch":
        flow = Flow(exc=type("MismatchingStateError", (Exception,), {})("CSRF Warning! State not equal"))
    res = _auth(m, flow, ids=ids, analytics=analytics, monkeypatch=monkeypatch)
    assert res["success"] is False and res["code"] == expected_code and m.saved == []
    assert ya.analytics_overview(m)["last_authorization"]["ok"] is False


def test_hindi_channel_is_never_authorized_here(monkeypatch):
    f, m = Flow(creds()), Mgr()
    res = _auth(m, f, monkeypatch=monkeypatch, channel="hi")
    assert res["code"] == "CHANNEL_NOT_SUPPORTED" and f.kwargs is None and m.saved == []


def test_secrets_never_in_result_or_state(monkeypatch):
    f = Flow(exc=RuntimeError("redirect http://localhost:8088/?code=4/SECRETCODE&state=abc"))
    res = _auth(Mgr(), f, monkeypatch=monkeypatch)
    blob = repr(res) + repr(ya._read_state(ya.AUTHZ_STATE_KEY))
    assert "SECRETCODE" not in blob and "refresh_token" not in blob


# -- state model -----------------------------------------------------------------------------------------------------
def test_overview_never_operational_without_a_successful_api_request():
    ya.record_auth_state(ya.CONNECTED, reason="analytics token refreshed", ok=True)
    ov = ya.analytics_overview(Mgr())
    assert ov["oauth"]["status"] == "AUTHORIZED" and ov["api"]["status"] == "UNTESTED"
    assert ov["overall"] == "AUTHORIZED_UNTESTED" and ov["label"] != "Analytics connected"
    ya.record_api_result(False, "insufficient_scope")
    assert ya.analytics_overview(Mgr())["overall"] == "API_FAILING"


def test_overview_authorization_required_and_metrics_pending_not_zero():
    ov = ya.analytics_overview(Mgr())
    assert ov["overall"] == "AUTHORIZATION_REQUIRED" and ov["oauth"]["action"] == "Authorize Analytics"
    ya.record_auth_state(ya.CONNECTED, ok=True)
    ya.record_api_result(True)
    store = SimpleNamespace(latest=lambda n: [{"metric_status": {"views": "not_yet_populated"}},
                                              {"metric_status": {"views": "available"}}])
    ov = ya.analytics_overview(Mgr(), store)
    assert ov["overall"] == "OPERATIONAL" and ov["oauth"]["action"] == "Reauthorize"
    assert ov["metrics"]["videos_pending"] == 1 and ov["metrics"]["videos_with_metrics"] == 1


def test_test_connection_records_result(monkeypatch):
    monkeypatch.setattr(ya, "analytics_credentials", lambda m, force=False: object())
    assert ya.test_connection(Mgr(), service_factory=an())["ok"] is True
    assert ya.analytics_overview(Mgr())["api"]["status"] == "CONNECTED"
    res = ya.test_connection(Mgr(), service_factory=an(exc=RuntimeError("boom")))
    assert res["ok"] is False and ya.analytics_overview(Mgr())["api"]["status"] == "FAILING"


def test_manual_collection_never_overlaps_a_running_one(monkeypatch):
    ya._write_state(ya.SYNC_STATE_KEY, {"running_since_utc": ya._now_iso()})
    col = ya.AnalyticsCollector(oauth_manager=Mgr(), db_path=":memory:")
    assert col.collect()["state"] == "already_running"


def test_provider_audit_uses_the_same_state_model(monkeypatch):
    from backend.app.services import provider_audit as pa
    import backend.app.services.youtube_publishing_engine as ype
    monkeypatch.setattr(ype, "YouTubeOAuthManager", Mgr)
    ya.record_auth_state(ya.CONNECTED, ok=True)
    assert pa._analytics_scope_granted() is True
    ya.record_auth_state(ya.AUTH_REQUIRED, reason="x")
    assert pa._analytics_scope_granted() is False


# -- routes ------------------------------------------------------------------------------------------------------
def test_routes_keep_local_same_origin_csrf_and_confirmation(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    calls = []
    monkeypatch.setattr(ya, "authorize_analytics", lambda **kw: calls.append(kw) or {"success": False, "code": "CANCELLED"})
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    hdr = {"Origin": "http://127.0.0.1:8000", main.AUTONOMY_CSRF_HEADER: main._AUTONOMY_CSRF_TOKEN}
    assert local.post("/api/youtube/analytics/authorize", json={"confirm": True},
                      headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403          # no CSRF token
    assert local.post("/api/youtube/analytics/authorize", json={}, headers=hdr).status_code == 400   # no confirm
    assert local.post("/api/youtube/analytics/authorize", json={"confirm": True, "channel": "hi"},
                      headers=hdr).status_code == 422                                          # English only
    assert local.post("/api/youtube/analytics/test", json={}, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403
    assert local.post("/api/youtube/analytics/collect", json={}, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403
    r = local.post("/api/youtube/analytics/authorize", json={"confirm": True}, headers=hdr)
    assert r.status_code == 200 and r.json()["code"] == "CANCELLED" and calls == [{"channel": "en"}]
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.post("/api/youtube/analytics/authorize", json={"confirm": True}, headers=hdr).status_code in (400, 403)
    st = local.get("/api/youtube/analytics/status").json()
    blob = json_dumps(st).lower()
    assert "overview" in st and "granted_scopes" not in st
    assert not any(k in blob for k in ("refresh_token", "access_token", "client_secret", '"code":'))


def json_dumps(x):
    import json
    return json.dumps(x)


def test_dashboard_has_the_authorization_actions():
    from pathlib import Path
    h = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for text in ("Authorize Analytics", "Reauthorize", "Test Analytics Connection", "Collect metrics now",
                 "/api/youtube/analytics/test", "/api/llm-providers/status"):
        assert text in h, text
