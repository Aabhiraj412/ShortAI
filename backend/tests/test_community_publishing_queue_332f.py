"""Stage 3.32F completion: publication authorization separate from approval, the local publishing queue, bounded
automatic retries, reauthorization detection, restart recovery, the workflow (lifecycle) state, notifications and
the schedule-editor consistency fix.

Every YouTube call is a fake. No network, nothing is published, temporary databases only.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.community import publishing as PUB
from backend.app.community import service as svc_mod
from backend.app.community import store as S
from backend.app.community.youtube_posts import ApiError, NotConnected, PostsApi
from test_community_posts_332e import OP, World, approve_any, gen
from test_community_publishing_332f import YT, FakeApi, FakeCreds, approved, gen_after_archive, make, ready


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def later(world, **kw):
    world.now[0] = world.now[0] + timedelta(**kw)


def lc(svc, did):
    return svc.detail(did)["draft"]["lifecycle"]["state"]


def kinds(svc):
    return [n["kind"] for n in svc.store.notifications(50)]


# == lifecycle ======================================================================================================
def test_lifecycle_walks_draft_validated_approved_queued_publishing_published(world):
    svc, pub, api = make(world)
    d = gen(svc)
    assert lc(svc, d["id"]) == PUB.LC_VALIDATED                     # generated and validated, not approved
    d = approve_any(svc, d)["draft"]
    assert lc(svc, d["id"]) == PUB.LC_APPROVED
    assert pub.check_capability()["outcome"] == PUB.API_AVAILABLE
    pub.set_enabled(None, True, confirm=True)
    p = pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=(world.now[0] + timedelta(hours=2)).isoformat())
    assert p["status"] == PUB.QUEUED and lc(svc, d["id"]) == PUB.LC_QUEUED
    assert "posts.insert" not in api.calls                          # authorizing never sends anything
    assert pub.process_queue()["status"] == "idle"                  # not due yet
    later(world, hours=2, minutes=1)
    out = pub.process_queue()
    assert out["status"] == "processed" and out["publication"]["status"] == PUB.PUBLISHED
    assert lc(svc, d["id"]) == PUB.LC_PUBLISHED and api.calls.count("posts.insert") == 1
    p = pub.publication(p["id"])
    assert p["youtube_post_id"] == "UgkxPOSTID12345" and p["post_url"].endswith("UgkxPOSTID12345")
    assert pub.process_queue()["status"] == "idle"                  # never sent twice
    ev = [e["event"] for e in svc.detail(d["id"])["events"]]
    assert {"DRAFT_APPROVED", "PUBLICATION_AUTHORIZED", "PUBLISH_REQUESTED", "PUBLISH_SUCCEEDED"} <= set(ev)
    assert "publish_succeeded" in kinds(svc)


def test_manual_handoff_is_never_reported_as_published(world):
    svc, pub, api = make(world)
    d = approved(svc)
    h = pub.prepare_handoff(d["id"])
    life = svc.detail(d["id"])["draft"]["lifecycle"]
    assert life["state"] == PUB.LC_MANUAL and life["handoff_completed"] is False
    pub.report_manual_post(h["id"], None, "https://www.youtube.com/post/UgkxMANUAL12345")
    life = svc.detail(d["id"])["draft"]["lifecycle"]
    assert life["state"] == PUB.LC_MANUAL and life["handoff_completed"] is True and life["verified"] is False
    st = pub.status()
    assert st["queue"]["published"] == [] and len(st["queue"]["manual_handoffs"]) == 1
    assert st["lifecycle_counts"][PUB.LC_PUBLISHED] == 0 and st["lifecycle_counts"][PUB.LC_MANUAL] == 1


# == authorization is separate from approval and fails closed =====================================================
def test_authorization_needs_confirm_and_ready_api(world):
    svc, pub, api = make(world)
    d = approved(svc)
    with pytest.raises(svc_mod.BadRequest):
        pub.authorize(d["id"], None, d["version"], confirm=False)
    with pytest.raises(PUB.PublishingDisabled) as e:                # publishing OFF (as in production today)
        pub.authorize(d["id"], None, d["version"], confirm=True)
    assert "manual handoff" in str(e.value)
    assert not PUB.tables_exist(world.db) or pub.status()["queue"]["queued"] == []


def test_authorization_requires_an_approved_unchanged_draft(world):
    svc, pub, api = make(world)
    d = gen(svc)                                                    # validated but not approved
    assert pub.check_capability()["outcome"] == PUB.API_AVAILABLE
    pub.set_enabled(None, True, confirm=True)
    with pytest.raises(PUB.PublishingDisabled):
        pub.authorize(d["id"], None, d["version"], confirm=True)
    d = approve_any(svc, d)["draft"]
    with pytest.raises(S.Conflict):                                 # stale version
        pub.authorize(d["id"], None, d["version"] - 1, confirm=True)


@pytest.mark.parametrize("when,ok", [("past", False), ("far", False), ("naive", True), ("garbage", False)])
def test_publish_at_validation(world, when, ok):
    svc, pub, api, d = ready(world)
    value = {"past": (world.now[0] - timedelta(hours=1)).isoformat(),
             "far": (world.now[0] + timedelta(days=40)).isoformat(),
             "naive": "2099-01-01T10:00:00"[:0] + (world.now[0] + timedelta(days=1)).replace(tzinfo=None).isoformat(),
             "garbage": "tomorrow-ish"}[when]
    if ok:
        p = pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=value)
        assert p["status"] == PUB.QUEUED and p["publish_at_utc"]
    else:
        with pytest.raises(svc_mod.BadRequest):
            pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=value)


def test_duplicate_authorization_and_publication_prevented(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    with pytest.raises(S.Conflict):
        pub.authorize(d["id"], None, d["version"], confirm=True)
    conn = sqlite3.connect(world.db)                                # the unique index is the last line of defence
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO community_publications (id, draft_id, mode, idem_key, status, text_fingerprint, attempts, "
                     "requested_by, created_at_utc, updated_at_utc) VALUES ('cpub_ffffffffffffffff', ?, 'api', 'x', "
                     "'QUEUED', 'f', 0, 't', 'n', 'n')", (d["id"],))
    conn.close()
    assert pub.process_queue()["publication"]["status"] == PUB.PUBLISHED
    with pytest.raises(S.Conflict):                                 # published: no second authorization or publish
        pub.authorize(d["id"], None, d["version"], confirm=True)
    with pytest.raises(S.Conflict):
        pub.publish(d["id"], None, d["version"], confirm=True)
    assert api.calls.count("posts.insert") == 1


# == queue gates ===================================================================================================
def test_queue_waits_while_a_gate_fails_and_records_why(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    (world.tmp / "COMMUNITY_DRAFTS_DISABLED").write_text("stop")   # kill switch after authorization
    out = pub.process_queue()
    assert out["status"] == "waiting" and "no_kill_switch" in out["waiting"][0]["blocked"]
    q = pub.publication(p["id"])
    assert q["status"] == PUB.QUEUED and "no_kill_switch" in q["blocked_reason"] and "posts.insert" not in api.calls
    assert svc.detail(d["id"])["draft"]["lifecycle"]["blocked_reason"]
    (world.tmp / "COMMUNITY_DRAFTS_DISABLED").unlink()
    pub.set_enabled(None, False)                                    # disabling also holds the queue
    assert pub.process_queue()["status"] == "waiting" and "posts.insert" not in api.calls
    pub.set_enabled(None, True, confirm=True)
    assert pub.process_queue()["publication"]["status"] == PUB.PUBLISHED


def test_editing_cancels_the_authorization(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=(world.now[0] + timedelta(hours=1)).isoformat())
    svc.edit(d["id"], OP, d["version"], d["text"] + " Edited!")
    q = pub.publication(p["id"])
    assert q["status"] == PUB.CANCELLED
    ev = [e for e in svc.detail(d["id"])["events"] if e["event"] == "PUBLICATION_CANCELLED"][0]
    assert "edited" in ev["detail"]["reason"]
    later(world, hours=2)
    assert pub.process_queue()["status"] == "idle" and "posts.insert" not in api.calls
    assert lc(svc, d["id"]) in (PUB.LC_VALIDATED, PUB.LC_DRAFT)    # approval withdrawn, must be re-approved


def test_stale_version_edit_does_not_cancel(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    with pytest.raises(S.StoreError):
        svc.edit(d["id"], OP, d["version"] - 1, d["text"] + " Edited!")
    assert pub.publication(p["id"])["status"] == PUB.QUEUED


def test_queue_cancels_when_content_changed_on_disk(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    img = svc.store.get_image(d["image_id"])
    path = svc._image_path(img)
    path.write_bytes(path.read_bytes() + b"tampered")
    pub.process_queue()
    assert pub.publication(p["id"])["status"] == PUB.CANCELLED and "posts.insert" not in api.calls
    assert "publish_failed" in kinds(svc)


def test_operator_cancel_and_reauthorize(world):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=(world.now[0] + timedelta(hours=1)).isoformat())
    assert pub.cancel_publication(p["id"], None, "changed my mind")["status"] == PUB.CANCELLED
    with pytest.raises(S.Conflict):
        pub.cancel_publication(p["id"], None)
    again = pub.authorize(d["id"], None, d["version"], confirm=True)
    assert again["id"] == p["id"] and again["status"] == PUB.QUEUED and again["attempts"] == 0


# == retries =======================================================================================================
def test_bounded_retries_with_backoff_then_failed(world):
    svc, pub, api, d = ready(world)
    api.insert_error = ApiError(403, "quotaExceeded", "daily quota")
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    pub.process_queue()
    r = pub.publication(p["id"])
    assert r["status"] == PUB.RETRY_PENDING and r["attempts"] == 1
    assert pub.process_queue()["status"] == "idle"                   # backoff: not before next_attempt_at
    later(world, minutes=6)
    pub.process_queue()
    r = pub.publication(p["id"])
    assert r["status"] == PUB.RETRY_PENDING and r["attempts"] == 2
    later(world, minutes=29)
    assert pub.process_queue()["status"] == "idle"                   # second backoff is 30 minutes
    later(world, minutes=2)
    pub.process_queue()
    r = pub.publication(p["id"])
    assert r["status"] == PUB.FAILED and r["attempts"] == PUB.MAX_ATTEMPTS and json.loads(r["error"])["retries_exhausted"]
    later(world, hours=5)
    assert pub.process_queue()["status"] == "idle" and api.calls.count("posts.insert") == PUB.MAX_ATTEMPTS
    assert "publish_failed" in kinds(svc)


def test_retry_succeeds_after_transient_upload_failure(world):
    svc, pub, api, d = ready(world)
    api.upload_error = ApiError(None, "network", "ConnectionResetError")
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.RETRY_PENDING and "posts.insert" not in api.calls
    assert svc.detail(d["id"])["draft"]["lifecycle"]["state"] == PUB.LC_RETRY
    api.upload_error = None
    later(world, minutes=6)
    assert pub.process_queue()["publication"]["status"] == PUB.PUBLISHED
    assert api.calls.count("posts.insert") == 1


def test_uncertain_outcome_is_never_retried_by_the_queue(world):
    svc, pub, api, d = ready(world)
    api.insert_error = ApiError(None, "timeout", "no response", ambiguous=True)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    pub.process_queue()
    assert pub.publication(p["id"])["status"] == PUB.UNCERTAIN
    life = svc.detail(d["id"])["draft"]["lifecycle"]
    assert life["state"] == PUB.LC_PUBLISHING and life["uncertain"] is True
    api.insert_error = None
    later(world, days=1)
    assert pub.process_queue()["status"] == "idle" and api.calls.count("posts.insert") == 1


# == authentication ================================================================================================
@pytest.mark.parametrize("error", [ApiError(401, "authError", "Invalid Credentials"),
                                   ApiError(403, "insufficientPermissions", "scope"),
                                   ApiError(401, "invalid_grant", "Token has been expired or revoked")])
def test_auth_failures_require_reauthorization_and_stop(world, error):
    svc, pub, api, d = ready(world)
    api.upload_error = error
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    pub.process_queue()
    r = pub.publication(p["id"])
    assert r["status"] == PUB.REQUIRES_REAUTH and lc(svc, d["id"]) == PUB.LC_REAUTH
    later(world, hours=6)
    assert pub.process_queue()["status"] == "idle" and "posts.insert" not in api.calls   # no automatic retry
    assert "auth_failure" in kinds(svc)
    assert pub.status()["queue"]["requires_reauthorization"][0]["id"] == p["id"]


def test_missing_credentials_mid_publish_do_not_leave_a_stuck_row(world):
    svc, pub, api, d = ready(world)
    pub._credentials_factory = lambda: (_ for _ in ()).throw(NotConnected("no YouTube token is installed"))
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.REQUIRES_REAUTH and "posts.imageUpload" not in api.calls


class _RefreshError(Exception):
    pass


class _TransportError(Exception):
    pass


_TransportError.__name__ = "TransportError"


@pytest.mark.parametrize("exc,status,reason", [
    (_RefreshError("invalid_grant: Token has been expired or revoked."), 401, "invalid_grant"),
    (_RefreshError("refresh refused"), 401, "authRequired"),
    (_TransportError("connection reset"), None, "network"),
    (NotConnected("no YouTube token is installed"), None, "notConnected"),
])
def test_token_refresh_failures_become_typed_api_errors(exc, status, reason):
    class Creds:
        def access_token(self):
            raise exc
    api = PostsApi(Creds(), opener=lambda *a, **k: pytest.fail("nothing may be sent without a token"))
    with pytest.raises(ApiError) as e:
        api.channel_id()
    assert e.value.status == status and e.value.reason == reason and e.value.ambiguous is False
    assert PUB.failure_status(e.value, "image_upload") == (PUB.RETRY_PENDING if reason == "network" else PUB.REQUIRES_REAUTH)


# == restart recovery ==============================================================================================
@pytest.mark.parametrize("stuck,expect", [(PUB.REQUESTED, PUB.RETRY_PENDING), (PUB.IMAGE_UPLOADED, PUB.RETRY_PENDING),
                                          (PUB.INSERTING, PUB.UNCERTAIN)])
def test_restart_during_publishing_recovers_safely(world, stuck, expect):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    conn = sqlite3.connect(world.db)                                # the process died mid-flight
    conn.execute("UPDATE community_publications SET status=?, attempts=1, updated_at_utc=? WHERE id=?",
                 (stuck, S.ts(world.now[0]), p["id"]))
    conn.commit()
    conn.close()
    assert pub.recover_interrupted() == {"retry": 0, "uncertain": 0}  # a live attempt is not touched
    later(world, minutes=16)
    out = pub.process_queue()
    r = pub.publication(p["id"])
    if expect == PUB.UNCERTAIN:
        assert out["recovered"]["uncertain"] == 1 and r["status"] == PUB.UNCERTAIN and "posts.insert" not in api.calls
        assert "publish_uncertain" in kinds(svc)
    else:                                                           # nothing was sent: retried right away, once
        assert out["recovered"]["retry"] == 1 and r["status"] == PUB.PUBLISHED and api.calls.count("posts.insert") == 1


def test_daily_limit_holds_the_queue(world):
    svc, pub, api, d = ready(world)
    pub.publish(d["id"], None, d["version"], confirm=True)
    for _ in range(PUB.DAILY_API_PUBLISH_LIMIT - 1):
        d2 = approve_any(svc, gen_after_archive(svc))["draft"]
        pub.publish(d2["id"], None, d2["version"], confirm=True)
    d3 = approve_any(svc, gen_after_archive(svc))["draft"]
    p = pub.authorize(d3["id"], None, d3["version"], confirm=True)
    out = pub.process_queue()
    assert out["status"] == "waiting" and "daily_limit" in pub.publication(p["id"])["blocked_reason"]


# == scheduler tick integration =====================================================================================
def test_tick_processes_the_queue_even_when_generation_automation_is_off(world, monkeypatch):
    svc, pub, api, d = ready(world)
    p = pub.authorize(d["id"], None, d["version"], confirm=True)
    monkeypatch.setattr(PUB.PublishingService, "_credentials", lambda self: FakeCreds())
    monkeypatch.setattr(PUB.PublishingService, "_api", lambda self, c: api)
    out = svc.tick()                                                # automation is OFF in this world
    assert out["publishing_queue"]["publication"]["status"] == PUB.PUBLISHED


def test_tick_without_publishing_tables_does_not_create_them(world):
    svc = world.svc()
    out = svc.tick()
    assert "publishing_queue" not in out and not PUB.tables_exist(world.db)


def test_tick_survives_a_queue_error(world, monkeypatch):
    svc, pub, api, d = ready(world)
    monkeypatch.setattr(PUB.PublishingService, "process_queue", lambda self: 1 / 0)
    out = svc.tick()
    assert out["publishing_queue"]["status"] == "error"


# == notifications =================================================================================================
def test_approval_notifies_manual_handoff_when_api_unavailable(world):
    svc, pub, api = make(world)
    d = approved(svc)
    n = [x for x in svc.store.notifications(20) if x["kind"] == "manual_handoff_required"]
    assert n and "Nothing has been published" in n[0]["message"]


def test_approval_notifies_awaiting_publication_when_ready(world):
    svc, pub, api = make(world)
    pub.check_capability()
    pub.set_enabled(None, True, confirm=True)
    d = approved(svc)
    assert "approved_awaiting_publication" in kinds(svc)
    assert "posts.insert" not in api.calls                          # approval still never publishes


def test_notification_failure_never_breaks_publishing(world):
    svc, pub, api, d = ready(world)
    svc.notifier = lambda *a: (_ for _ in ()).throw(RuntimeError("smtp down"))
    p = pub.publish(d["id"], None, d["version"], confirm=True)
    assert p["status"] == PUB.PUBLISHED


# == status / dashboard data ========================================================================================
def test_status_separates_approved_queued_and_published(world):
    svc, pub, api, d = ready(world)
    st = pub.status()
    assert [a["id"] for a in st["approved_awaiting"]] == [d["id"]] and st["lifecycle_counts"][PUB.LC_APPROVED] == 1
    pub.authorize(d["id"], None, d["version"], confirm=True, publish_at=(world.now[0] + timedelta(hours=3)).isoformat())
    st = pub.status()
    assert st["approved_awaiting"] == [] and len(st["queue"]["queued"]) == 1
    assert st["lifecycle_counts"][PUB.LC_QUEUED] == 1 and st["lifecycle_counts"][PUB.LC_PUBLISHED] == 0
    assert st["queue"]["published"] == [] and st["retry_policy"]["uncertain_is_never_retried"] is True
    assert st["retry_policy"]["backoff_minutes"] == [5, 30]               # only the waits that 3 attempts use
    later(world, hours=4)
    pub.process_queue()
    st = pub.status()
    assert st["lifecycle_counts"][PUB.LC_PUBLISHED] == 1 and st["queue"]["queued"] == [] and len(st["queue"]["published"]) == 1


def test_migration_keeps_existing_publication_rows(world):
    svc, pub, api = make(world)
    d = approved(svc)
    h = pub.prepare_handoff(d["id"])                                # creates the tables
    conn = sqlite3.connect(world.db)                                # simulate the pre-3.32F-completion schema
    conn.execute("DROP INDEX IF EXISTS uq_community_pub_active_api")
    conn.execute("CREATE UNIQUE INDEX uq_community_pub_live_api ON community_publications (draft_id) WHERE mode='api' "
                 "AND status IN ('REQUESTED','IMAGE_UPLOADED','INSERTING','UNCERTAIN','PUBLISHED')")
    conn.commit()
    conn.close()
    pub._ensure()
    conn = sqlite3.connect(world.db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(community_publications)")}
    conn.close()
    assert "uq_community_pub_active_api" in names and "uq_community_pub_live_api" not in names
    assert set(PUB.NEW_COLUMNS) <= cols and pub.publication(h["id"])["status"] == PUB.HANDOFF_PREPARED


def test_schedule_status_no_longer_calls_publishing_a_future_capability(world):
    svc = world.svc()
    note = svc.schedule_status()["youtube_publishing"]["note"]
    assert "future capability" not in note and "separate" in note


# == dashboard: schedule editor consistency =========================================================================
def test_dashboard_schedule_editor_is_loaded_before_it_can_be_saved():
    html = (Path(svc_mod.__file__).parents[1] / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "cmState.loaded" in html and 'id="cmSaveSchedule"' in html
    assert "Kill switch" in html and "Publishing queue" in html
    for state in ("APPROVED", "QUEUED", "PUBLISHING", "PUBLISHED", "RETRY_PENDING", "REQUIRES_REAUTHORIZATION",
                  "MANUAL_HANDOFF"):
        assert state in html
