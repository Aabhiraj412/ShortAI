"""Stage 3.30: YouTube Data API quota model, request ledger and call reduction."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.services import youtube_quota as yq
from backend.app.services.youtube_publishing_engine import (
    PublishingDatabase, YouTubePublishingEngine, remote_matches_update)


class _Resp(dict):
    def __init__(self, status):
        super().__init__(status=str(status))
        self.status = status
        self.reason = "x"


def _http_error(status, reason):
    from googleapiclient.errors import HttpError
    return HttpError(_Resp(status), ('{"error":{"errors":[{"reason":"%s"}]}}' % reason).encode())


class _Req:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    def execute(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def ledger(tmp_path):
    clock = {"now": datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)}
    led = yq.QuotaLedger(str(tmp_path / "q.db"), clock=lambda: clock["now"])
    led._clock_box = clock
    return led


def test_verified_costs_and_separate_buckets():
    assert yq.method_cost("videos.insert") == {**yq.method_cost("videos.insert"), "bucket": "videos.insert", "cost": 1}
    assert yq.method_cost("search.list")["bucket"] == "search.list"
    assert yq.method_cost("videos.batchGetStats")["bucket"] == "videos.batchGetStats"
    for m, c in (("videos.update", 50), ("thumbnails.set", 50), ("videos.list", 1), ("channels.list", 1),
                 ("playlistItems.list", 1)):
        info = yq.method_cost(m)
        assert info["bucket"] == "general" and info["cost"] == c and info["verified"]
    assert yq.BUCKETS["videos.insert"]["daily_limit"] == 100
    assert yq.BUCKETS["general"]["daily_limit"] == 10_000
    unknown = yq.method_cost("captions.download")
    assert unknown["verified"] is False


def test_pacific_day_and_reset_in_ist():
    at = datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)          # 21:00 PST on 11-19
    assert yq.pacific_day(at) == "2026-11-19"
    r = yq.next_reset(at)
    assert r.astimezone(timezone.utc) == datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)   # PST = UTC-8
    assert r.astimezone(yq.IST).strftime("%H:%M") == "13:30"
    summer = yq.next_reset(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))                  # PDT = UTC-7
    assert summer.astimezone(timezone.utc).hour == 7


def test_only_sent_requests_are_recorded(ledger):
    ok = _Req({"items": []})
    ledger.execute(ok, "videos.list", purpose="t")
    local_fail = _Req(exc=ValueError("bad local argument"))           # never reached Google
    with pytest.raises(ValueError):
        ledger.execute(local_fail, "videos.list", purpose="t")
    google_fail = _Req(exc=_http_error(500, "backendError"))           # reached Google: counts
    with pytest.raises(Exception):
        ledger.execute(google_fail, "videos.list", purpose="t")
    u = ledger.usage()["general"]
    assert u["requests"] == 2 and u["used_estimate"] == 2


def test_replayed_event_is_idempotent(ledger):
    ledger.record("thumbnails.set", purpose="t", outcome="ok", correlation_id="abc")
    ledger.record("thumbnails.set", purpose="t", outcome="ok", correlation_id="abc")
    assert ledger.usage()["general"]["used_estimate"] == 50


def test_six_uploads_no_longer_block_and_legacy_9900_is_ignored(tmp_path):
    db = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    db.add_quota_consumption(9900)                                     # the old counter's false value
    eng = YouTubePublishingEngine(oauth_manager=MagicMock(), db=db)
    for _ in range(6):
        eng.quota.record("videos.insert", purpose="video_upload", outcome="ok")
        eng.quota.record("thumbnails.set", purpose="thumbnail_upload", outcome="ok")
    assert eng.quota.check("videos.insert", 1)["allowed"]
    assert eng.quota.check("general", 50)["allowed"]
    assert eng.quota.usage()["general"]["used_estimate"] == 300


def test_quota_exhaustion_blocks_only_its_bucket_until_pacific_midnight(ledger):
    with pytest.raises(Exception):
        ledger.execute(_Req(exc=_http_error(403, "quotaExceeded")), "thumbnails.set", purpose="thumbnail_upload",
                       video_id="v1")
    assert not ledger.check("general", 50)["allowed"]
    assert ledger.check("videos.insert", 1)["allowed"]                 # uploads have their own bucket
    assert ledger.blocked("general")["operation"] == "thumbnail_upload"
    ledger._clock_box["now"] = datetime(2026, 11, 20, 8, 0, 1, tzinfo=timezone.utc)   # after PT midnight
    assert ledger.check("general", 50)["allowed"]
    assert ledger.usage()["general"]["used_estimate"] == 0            # new Pacific day


def test_upload_bucket_limit_counts_calls(ledger):
    for _ in range(100):
        ledger.record("videos.insert", purpose="video_upload", outcome="ok")
    assert not ledger.check("videos.insert", 1)["allowed"]
    assert ledger.check("general", 1)["allowed"]


def test_summary_never_claims_google_reported_usage(ledger):
    s = ledger.summary()
    assert s["google_reported"]["available"] is False
    assert "estimate" in s["source"]
    assert s["next_reset_ist"].endswith("IST")


def test_status_reconciliation_batches_videos_list(tmp_path):
    db = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    eng = YouTubePublishingEngine(oauth_manager=MagicMock(), db=db)
    rows = [{"id": i, "youtube_video_id": f"v{i}", "scheduled_timestamp": None} for i in range(60)]
    db.list_publications = lambda **kw: rows
    calls = []

    def _list(part, id, maxResults=None):
        ids = id.split(",")
        calls.append(len(ids))
        return _Req({"items": [{"id": v, "status": {"privacyStatus": "private"}} for v in ids]})
    service = MagicMock()
    service.videos.return_value.list.side_effect = _list
    eng.oauth.get_authenticated_service.return_value = service
    out = eng.verify_scheduled_publications()
    assert calls == [50, 10]                                         # 2 calls instead of 60
    assert out["checked"] == 60 and all(r.get("state") == "publication_scheduled" for r in out["results"])
    assert eng.quota.usage()["general"]["requests"] == 2


def test_unchanged_video_is_not_updated():
    body = {"id": "v", "snippet": {"title": "T", "description": "D", "tags": ["a"], "categoryId": "28"},
            "status": {"privacyStatus": "private", "publishAt": "2026-11-21T00:30:00+05:30"}}
    remote = {"snippet": {"title": "T", "description": "D", "tags": ["a"], "categoryId": "28"},
              "status": {"privacyStatus": "private", "publishAt": "2026-11-20T19:00:00Z"}}
    assert remote_matches_update(remote, body, metadata_only=False)
    remote["status"]["publishAt"] = "2026-11-20T20:00:00Z"
    assert not remote_matches_update(remote, body, metadata_only=False)
    assert remote_matches_update(remote, body, metadata_only=True)
    remote["snippet"]["title"] = "Other"
    assert not remote_matches_update(remote, body, metadata_only=True)


def test_successful_thumbnail_is_not_resent(tmp_path):
    db = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    eng = YouTubePublishingEngine(oauth_manager=MagicMock(), db=db)
    from backend.app.services import thumbnail_pipeline as tpl
    art = str(tmp_path / "t_youtube_1280x720.jpg")
    store = eng.thumbnail_store()
    store.update(7, video_id="vid7", artifact_path=art, upload_status="uploaded", verify_status="pending")
    orig = tpl.youtube_artifact_for
    tpl.youtube_artifact_for = lambda p: art
    try:
        service = MagicMock()
        res = eng._upload_thumbnail(service, 7, "vid7", str(tmp_path / "t.png"))
    finally:
        tpl.youtube_artifact_for = orig
    assert res["status"] == "already_uploaded" and res["uploaded"]
    service.thumbnails.assert_not_called()
    assert eng.quota.usage()["general"]["requests"] == 0
