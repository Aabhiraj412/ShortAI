"""Stage 3.32F: YouTube Community publishing - capability audit, fail-closed API publishing, manual handoff.

Every YouTube call is a fake (FakeApi). No network, no real upload or publish, temporary databases only.
"""
from __future__ import annotations

import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.community import publishing as PUB
from backend.app.community import service as svc_mod
from backend.app.community import store as S
from backend.app.community.youtube_posts import ApiError, NotConnected
from test_community_posts_332e import OP, World, approve_any, gen

YT = "https://www.googleapis.com/auth/"
SECRET = "ya29.SECRET-ACCESS-TOKEN-VALUE"


class FakeCreds:
    def __init__(self, scopes=(YT + "youtube.force-ssl",), connected=True):
        self.scopes, self.connected = list(scopes), connected

    def granted_scopes(self):
        if not self.connected:
            raise NotConnected("no YouTube token is installed")
        return self.scopes

    def project_id(self):
        return "test-project"

    def access_token(self):
        return SECRET


class FakeApi:
    def __init__(self):
        self.calls = []
        self.channel = "UC" + "x" * 22
        self.channel_error = None
        self.list_error = None
        self.list_items = []
        self.upload_error = None
        self.insert_error = None
        self.insert_result = {"id": "UgkxPOSTID12345", "snippet": {"publishTime": "2026-11-20T05:00:00Z"}}

    def channel_id(self):
        self.calls.append("channels.list")
        if self.channel_error:
            raise self.channel_error
        return self.channel

    def list_posts(self, channel_id, max_results=5):
        self.calls.append("posts.list")
        if self.list_error:
            raise self.list_error
        return self.list_items

    def image_upload(self, channel_id, path, mime="image/jpeg"):
        self.calls.append("posts.imageUpload")
        if self.upload_error:
            raise self.upload_error
        return "upload-token-1"

    def insert(self, channel_id, text, tokens):
        self.calls.append("posts.insert")
        self.inserted_text = text
        if self.insert_error:
            raise self.insert_error
        return self.insert_result


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def make(world, scopes=(YT + "youtube.force-ssl",), connected=True):
    svc = world.svc()
    api = FakeApi()
    pub = PUB.PublishingService(svc, credentials_factory=lambda: FakeCreds(scopes, connected), api_factory=lambda c: api)
    return svc, pub, api


def approved(svc):
    d = gen(svc)
    return approve_any(svc, d)["draft"]


def ready(world, scopes=(YT + "youtube.force-ssl",)):
    svc, pub, api = make(world, scopes)
    d = approved(svc)
    assert pub.check_capability()["outcome"] == PUB.API_AVAILABLE
    pub.set_enabled(None, True, confirm=True)
    api.calls.clear()
    return svc, pub, api, d


def dump(world):
    conn = sqlite3.connect(world.db)
    out = " ".join(str(r) for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'community_%'")
                   for r in conn.execute(f"SELECT * FROM {t}"))
    conn.close()
    return out


# == capability detection ===========================================================================================
@pytest.mark.parametrize("case,scopes,setup,outcome,inferred", [
    ("available", [YT + "youtube.force-ssl"], {}, PUB.API_AVAILABLE, False),
    ("readonly_only", [YT + "youtube.readonly", YT + "youtube.upload"], {}, PUB.OAUTH_SCOPE_INSUFFICIENT, False),
    ("pilot_404", [YT + "youtube.readonly"], {"list_error": ApiError(404, "notFound", "Method not found.")},
     PUB.PILOT_ACCESS_REQUIRED, True),
    ("pilot_403", [YT + "youtube"], {"list_error": ApiError(403, "forbidden", "The request is not allowed.")},
     PUB.PILOT_ACCESS_REQUIRED, True),
    ("not_enabled", [YT + "youtube"], {"list_error": ApiError(403, "accessNotConfigured", "YouTube Data API v3 has not been used in project 1")},
     PUB.API_NOT_ENABLED, False),
    ("scope_error", [YT + "youtube.upload"], {"list_error": ApiError(403, "insufficientPermissions", "Insufficient Permission")},
     PUB.OAUTH_SCOPE_INSUFFICIENT, False),
    ("channel_list", [YT + "youtube"], {"list_error": ApiError(404, "channelNotFound", "Channel not found")},
     PUB.CHANNEL_PERMISSION_INSUFFICIENT, False),
    ("channel_lookup", [YT + "youtube"], {"channel_error": ApiError(403, "forbidden", "no access")},
     PUB.CHANNEL_PERMISSION_INSUFFICIENT, True),
    ("quota", [YT + "youtube"], {"list_error": ApiError(403, "rateLimitExceeded", "quota")}, PUB.UNKNOWN, True),
    ("timeout", [YT + "youtube"], {"list_error": ApiError(None, "timeout", "no response")}, PUB.UNKNOWN, True),
])
def test_capability_detection(world, case, scopes, setup, outcome, inferred):
    svc, pub, api = make(world, scopes)
    for k, v in setup.items():
        setattr(api, k, v)
    cap = pub.check_capability()
    assert cap["outcome"] == outcome and cap["details"]["inferred"] is inferred
    assert cap["details"]["publishing_possible"] is (outcome == PUB.API_AVAILABLE)
    assert set(api.calls) <= {"channels.list", "posts.list"}                  # read-only probe only
    assert SECRET not in dump(world)                                         # no credential is ever stored


def test_pilot_denied_also_reports_missing_insert_scope(world):
    svc, pub, api = make(world, [YT + "youtube.upload", YT + "youtube.readonly"])
    api.list_error = ApiError(404, "notFound", "Method not found.")
    cap = pub.check_capability()
    assert cap["outcome"] == PUB.PILOT_ACCESS_REQUIRED
    assert [b["outcome"] for b in cap["details"]["additional_blockers"]] == [PUB.OAUTH_SCOPE_INSUFFICIENT]
    assert cap["details"]["has_list_scope"] is True and cap["details"]["has_insert_scope"] is False


def test_not_connected_and_unexpected_errors_fail_closed(world):
    svc, pub, api = make(world, connected=False)
    assert pub.check_capability()["outcome"] == PUB.UNKNOWN and api.calls == []

    def boom(creds):
        raise RuntimeError("explode key=AIzaSyA1234567890abcdefghijklmnop")
    pub2 = PUB.PublishingService(svc, credentials_factory=lambda: FakeCreds(), api_factory=boom)
    cap = pub2.check_capability()
    assert cap["outcome"] == PUB.UNKNOWN and "AIzaSy" not in json.dumps(cap)


# == enabling ======================================================================================================
def test_enabling_requires_fresh_api_available(world):
    svc, pub, api = make(world, [YT + "youtube.readonly"])
    with pytest.raises(PUB.PublishingDisabled):
        pub.set_enabled(None, True, confirm=True)                            # never checked
    api.list_error = ApiError(404, "notFound", "Method not found.")
    pub.check_capability()
    with pytest.raises(PUB.PublishingDisabled):
        pub.set_enabled(None, True, confirm=True)                            # pilot denied
    svc2, pub2, api2 = make(world, [YT + "youtube"])
    pub2.check_capability()
    with pytest.raises(svc_mod.BadRequest):
        pub2.set_enabled(None, True)                                         # confirm required
    world.now[0] += timedelta(days=8)
    with pytest.raises(PUB.PublishingDisabled):
        pub2.set_enabled(None, True, confirm=True)                           # stale check
    world.now[0] -= timedelta(days=8)
    assert pub2.set_enabled(None, True, confirm=True)["publishing_enabled"] is True
    assert pub2.set_enabled(None, False)["publishing_enabled"] is False
    with pytest.raises(svc_mod.BadRequest):
        svc.update_config(OP, {"publishing_enabled": True})                  # the schedule path cannot switch it on


# == publishing gates ==============================================================================================
def test_publishing_disabled_by_default_makes_no_calls(world):
    svc, pub, api = make(world)
    d = approved(svc)
    pub.check_capability()
    api.calls.clear()
    with pytest.raises(PUB.PublishingDisabled) as e:
        pub.publish(d["id"], None, d["version"], confirm=True)
    assert "publishing_enabled" in str(e.value) and api.calls == []
    assert pub.status()["api_publishing_ready"] is False


def test_pilot_denied_blocks_publishing(world):
    svc, pub, api = make(world, [YT + "youtube.readonly"])
    d = approved(svc)
    api.list_error = ApiError(404, "notFound", "Method not found.")
    pub.check_capability()
    with svc.store.tx() as conn:                                              # even a forged enable flag is not enough
        svc.store.set_config(conn, {"publishing_enabled": True}, "forged", world.now[0])
    api.calls.clear()
    with pytest.raises(PUB.PublishingDisabled) as e:
        pub.publish(d["id"], None, d["version"], confirm=True)
    assert "capability_api_available" in str(e.value) and api.calls == []


def test_successful_publish_records_everything(world):
    svc, pub, api, d = ready(world)
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.PUBLISHED and p["youtube_post_id"] == "UgkxPOSTID12345"
    assert p["post_url"] == "https://www.youtube.com/post/UgkxPOSTID12345" and p["attempts"] == 1
    assert api.calls == ["posts.imageUpload", "posts.insert"] and api.inserted_text == d["text"]
    assert p["request"]["image_sha256"] and p["response"]["id"] == "UgkxPOSTID12345"
    events = [e["event"] for e in svc.detail(d["id"])["events"]]
    assert "PUBLISH_REQUESTED" in events and "PUBLISH_SUCCEEDED" in events
    st = pub.status()
    assert [x["id"] for x in st["published"]] == [p["id"]] and st["approved_awaiting"] == []
    with pytest.raises(S.Conflict):                                           # a published draft is frozen
        svc.edit(d["id"], OP, svc.detail(d["id"])["draft"]["version"], d["text"] + " More?")


def test_duplicate_and_idempotent_publication(world):
    svc, pub, api, d = ready(world)
    pub.publish(d["id"], None, d["version"], confirm=True)
    with pytest.raises(S.Conflict):
        pub.publish(d["id"], None, d["version"], confirm=True)
    assert api.calls.count("posts.insert") == 1
    conn = sqlite3.connect(world.db)
    with pytest.raises(sqlite3.IntegrityError):                               # DB enforces one live API publication
        conn.execute("INSERT INTO community_publications (id, draft_id, mode, idem_key, status, text_fingerprint, attempts, "
                     "requested_by, created_at_utc, updated_at_utc) VALUES ('cpub_0000000000000001', ?, 'api', 'other', "
                     "'REQUESTED', 'x', 0, 't', 't', 't')", (d["id"],))
    conn.close()


@pytest.mark.parametrize("case", ["not_approved", "stale_version", "no_confirm", "image_tampered", "text_tampered"])
def test_invalid_or_stale_drafts_are_refused(world, case):
    svc, pub, api, d = ready(world)
    version = d["version"]
    if case == "not_approved":
        d2 = svc.edit(d["id"], OP, d["version"], d["text"] + " What do you think?")["draft"]   # approval withdrawn
        version = d2["version"]
    elif case == "stale_version":
        version = d["version"] - 1
    elif case == "image_tampered":
        img = svc.detail(d["id"])["image"]
        p = svc._image_path(img)
        p.write_bytes(p.read_bytes() + b"x")
    elif case == "text_tampered":
        conn = sqlite3.connect(world.db)
        conn.execute("UPDATE community_drafts SET text = text || ' changed' WHERE id=?", (d["id"],))
        conn.commit()
        conn.close()
    with pytest.raises((PUB.PublishingDisabled, S.Conflict, svc_mod.BadRequest)):
        pub.publish(d["id"], None, version, confirm=case != "no_confirm")
    assert "posts.insert" not in api.calls and "posts.imageUpload" not in api.calls


def test_timeout_after_insert_is_uncertain_and_never_retried(world):
    svc, pub, api, d = ready(world)
    api.insert_error = ApiError(None, "timeout", "no response within 30 s", ambiguous=True)
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.UNCERTAIN and api.calls.count("posts.insert") == 1
    with pytest.raises(S.Conflict):                                           # no blind retry
        pub.publish(d["id"], None, d["version"], confirm=True)
    assert api.calls.count("posts.insert") == 1
    assert any("unknown" in s.lower() for s, _, _ in world.notes)
    with pytest.raises(S.Conflict):
        svc.edit(d["id"], OP, svc.detail(d["id"])["draft"]["version"], "x" * 50 + "?")   # frozen while uncertain
    api.list_items = []                                                        # not visible yet
    r = pub.reconcile(p["id"])
    assert r["reconciled"] is False and pub.publication(p["id"])["status"] == PUB.UNCERTAIN
    api.list_items = [{"id": "UgkxFOUND123456", "snippet": {"textDisplay": d["text"], "publishTime": "2026-11-20T05:00:30Z"}}]
    r = pub.reconcile(p["id"])
    assert r["reconciled"] is True and r["publication"]["status"] == PUB.PUBLISHED
    assert r["publication"]["youtube_post_id"] == "UgkxFOUND123456" and api.calls.count("posts.insert") == 1


def test_operator_resolution_of_uncertain(world):
    svc, pub, api, d = ready(world)
    api.insert_error = ApiError(503, "backendError", "unavailable", ambiguous=True)
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    with pytest.raises(svc_mod.BadRequest):
        pub.resolve_uncertain(p["id"], None, "maybe", "checked the channel")
    r = pub.resolve_uncertain(p["id"], None, "not_published", "checked the Posts tab: nothing there")
    assert r["status"] == PUB.FAILED
    api.insert_error = None
    again = pub.publish(d["id"], None, d["version"], confirm=True)            # explicit retry after resolution
    assert again["status"] == PUB.PUBLISHED and again["id"] == p["id"] and again["attempts"] == 2
    ev = [e for e in svc.detail(d["id"])["events"] if e["event"] == "PUBLISH_RESOLVED"][0]
    assert ev["detail"]["verified"] is False


@pytest.mark.parametrize("where,error,expect_insert", [
    ("insert", ApiError(403, "rateLimitExceeded", "quota exceeded"), True),
    ("insert", ApiError(400, "invalidMetadata", "bad text"), True),
    ("upload", ApiError(403, "forbidden", "not allowed"), False),
    ("upload", ApiError(None, "timeout", "slow"), False),
])
def test_definitive_failures_and_quota(world, where, error, expect_insert):
    svc, pub, api, d = ready(world)
    setattr(api, "insert_error" if where == "insert" else "upload_error", error)
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.FAILED and ("posts.insert" in api.calls) is expect_insert
    assert json.loads(p["error"])["reason"] == error.reason
    api.insert_error = api.upload_error = None
    assert pub.publish(d["id"], None, d["version"], confirm=True)["status"] == PUB.PUBLISHED    # retry is explicit


def test_daily_api_publication_limit(world):
    svc, pub, api, d = ready(world)
    pub.publish(d["id"], None, d["version"], confirm=True)
    for _ in range(PUB.DAILY_API_PUBLISH_LIMIT - 1):
        d2 = approve_any(svc, gen_after_archive(svc))["draft"]
        pub.publish(d2["id"], None, d2["version"], confirm=True)
    d3 = approve_any(svc, gen_after_archive(svc))["draft"]
    with pytest.raises(S.Conflict):
        pub.publish(d3["id"], None, d3["version"], confirm=True)


def gen_after_archive(svc):
    for d in svc.list_drafts(status="active"):
        svc.archive(d["id"], OP, d["version"])
    svc.store.ensure_schema()
    svc.update_config(OP, {"manual_daily_limit": 10})
    return gen(svc)


# == manual handoff ================================================================================================
def test_manual_handoff(world):
    svc, pub, api = make(world, [YT + "youtube.readonly"])
    d0 = gen(svc)
    with pytest.raises(S.InvalidTransition):
        pub.prepare_handoff(d0["id"])                                         # not approved yet
    d = approve_any(svc, d0)["draft"]
    h = pub.prepare_handoff(d["id"])
    x = h["handoff"]
    assert h["status"] == PUB.HANDOFF_PREPARED and h["mode"] == "manual" and x["text"] == d["text"]
    assert x["image_download_url"].endswith("?download=1") and x["open_url"] == "https://www.youtube.com/"
    assert any("Schedule Post" in s for s in x["steps"]) and "Nothing has been published" in x["note"]
    assert pub.prepare_handoff(d["id"])["id"] == h["id"]                      # idempotent
    assert api.calls == []                                                    # the handoff never calls YouTube
    with pytest.raises(svc_mod.BadRequest):
        pub.report_manual_post(h["id"], None, "https://evil.example/post/123")
    r = pub.report_manual_post(h["id"], None, "https://www.youtube.com/post/UgkxMANUAL12345")
    assert r["status"] == PUB.MANUAL_REPORTED and r["youtube_post_id"] is None   # no id is invented
    ev = [e for e in svc.detail(d["id"])["events"] if e["event"] == "MANUAL_POST_REPORTED"][0]
    assert ev["detail"]["verified"] is False
    with pytest.raises(S.Conflict):
        svc.regenerate_text(d["id"], OP, svc.detail(d["id"])["draft"]["version"])   # reported posted: frozen
    with pytest.raises(S.Conflict):
        pub.report_manual_post(h["id"])                                       # only once


def test_handoff_refuses_changed_content_and_can_be_cancelled(world):
    svc, pub, api = make(world)
    d = approved(svc)
    h = pub.prepare_handoff(d["id"])
    assert pub.cancel_handoff(h["id"])["status"] == PUB.HANDOFF_CANCELLED
    img = svc.detail(d["id"])["image"]
    p = svc._image_path(img)
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(S.Conflict):
        pub.prepare_handoff(d["id"])


# == audit, automation, operator, API ==============================================================================
def test_audit_trail_is_append_only(world):
    svc, pub, api, d = ready(world)
    pub.publish(d["id"], None, d["version"], confirm=True)
    events = [e["event"] for e in svc.store.events(None, 200)]
    for name in ("PUBLISHING_CAPABILITY_CHECKED", "PUBLISHING_ENABLED", "PUBLISH_REQUESTED", "PUBLISH_SUCCEEDED"):
        assert name in events
    conn = sqlite3.connect(world.db)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE community_events SET actor='x' WHERE event='PUBLISH_SUCCEEDED'")
    conn.close()
    assert SECRET not in dump(world)


def test_automation_never_publishes(world, monkeypatch):
    from test_community_posts_332e import enable
    svc, pub, api, d = ready(world)
    monkeypatch.setattr(svc_mod, "_instance", svc)
    enable(world, svc)
    for _ in range(3):
        world.now[0] += timedelta(days=1)
        svc.tick()
    assert "posts.insert" not in api.calls and "posts.imageUpload" not in api.calls
    off = World(world.tmp / "off")
    svc2, pub2, api2 = make(off)
    for _ in range(3):
        off.now[0] += timedelta(days=1)
        svc2.tick()
    assert api2.calls == []


def test_no_network_when_publishing_is_unavailable(world, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    svc, pub, api = make(world)
    d = approved(svc)
    with pytest.raises(PUB.PublishingDisabled):
        pub.publish(d["id"], None, d["version"], confirm=True)
    pub.prepare_handoff(d["id"])
    assert pub.status()["api_publishing_ready"] is False


def test_api_routes_security_and_operator_optional(world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    svc, pub, api = make(world, [YT + "youtube.readonly"])
    api.list_error = ApiError(404, "notFound", "Method not found.")
    monkeypatch.setattr(svc_mod, "_instance", svc)
    monkeypatch.setattr(main, "_publishing", lambda: pub)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    remote = TestClient(main.app)
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    g = local.post("/api/community/generate", json={}, headers=h)            # no operator needed
    assert g.status_code == 200
    d = g.json()["draft"]["draft"]
    assert {e["actor"] for e in g.json()["draft"]["events"]} == {"local-dashboard"}
    a = local.post(f"/api/community/drafts/{d['id']}/approve", json={"version": d["version"], "acknowledge_warnings": True,
                                                                         "reason": "reviewed text"}, headers=h)
    assert a.status_code == 200 and a.json()["draft"]["decided_by"] == "local-dashboard"
    d = a.json()["draft"]
    assert remote.get("/api/community/publishing").status_code == 403
    assert local.post("/api/community/publishing/capability-check", json={}).status_code == 403            # no CSRF
    cap = local.post("/api/community/publishing/capability-check", json={}, headers=h)
    assert cap.status_code == 200 and cap.json()["outcome"] == PUB.PILOT_ACCESS_REQUIRED
    assert local.post("/api/community/publishing/enabled", json={"enabled": True, "confirm": True}, headers=h).status_code == 423
    assert local.post(f"/api/community/drafts/{d['id']}/publish", json={"version": d["version"]}, headers=h).status_code == 400
    r = local.post(f"/api/community/drafts/{d['id']}/publish", json={"version": d["version"], "confirm": True}, headers=h)
    assert r.status_code == 423 and "publishing refused" in r.json()["detail"]
    assert local.post(f"/api/community/drafts/{d['id']}/publish", json={"version": d["version"], "confirm": True,
                                                                         "publish_at": "09:00"}, headers=h).status_code == 422
    ho = local.post(f"/api/community/drafts/{d['id']}/handoff", json={}, headers=h)
    assert ho.status_code == 200 and ho.json()["status"] == PUB.HANDOFF_PREPARED
    img = local.get(ho.json()["handoff"]["image_download_url"])
    assert img.status_code == 200 and "attachment" in img.headers.get("content-disposition", "")
    for bad in ("cpub_nothex", "..%2Fx", "cpub_0000000000000000"):
        assert local.post(f"/api/community/publications/{bad}/reconcile", json={}, headers=h).status_code == 404
    st = local.get("/api/community/publishing").json()
    assert st["api_publishing_ready"] is False and st["capability"]["outcome"] == PUB.PILOT_ACCESS_REQUIRED
    assert st["scheduling"]["api_can_schedule"] is False
    assert "posts.insert" not in api.calls and "posts.imageUpload" not in api.calls


def test_publishing_status_before_anything_creates_nothing(world):
    svc, pub, api = make(world)
    st = pub.status()
    assert st["capability"] is None and st["api_publishing_ready"] is False and st["published"] == []
    assert PUB.tables_exist(str(world.db)) is False
