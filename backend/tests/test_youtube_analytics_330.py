"""Stage 3.30: YouTube Analytics collection (scopes, metric compatibility, missing vs zero, storage)."""
import json
import sqlite3
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.services import youtube_analytics as ya
from backend.app.services.youtube_publishing_engine import YOUTUBE_SCOPES


class _Resp(dict):
    def __init__(self, status):
        super().__init__(status=str(status))
        self.status = status
        self.reason = "x"


def _http_error(status, body="{}"):
    from googleapiclient.errors import HttpError
    return HttpError(_Resp(status), body.encode())


def test_upload_scopes_are_unchanged_and_analytics_is_read_only():
    assert YOUTUBE_SCOPES == ["https://www.googleapis.com/auth/youtube.upload",
                              "https://www.googleapis.com/auth/youtube.readonly"]
    assert ya.ANALYTICS_SCOPE == "https://www.googleapis.com/auth/yt-analytics.readonly"
    assert ya.ANALYTICS_SCOPE not in YOUTUBE_SCOPES                 # uploads never request it on refresh


def test_missing_metrics_are_never_zero():
    resp = {"columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "engagedViews"}],
            "rows": [["a", 120, 0]]}
    out = ya.metric_values(resp, ["views", "engagedViews", "averageViewPercentage"], ["a", "b"])
    assert out["a"]["views"] == {"value": 120, "status": "available"}
    assert out["a"]["engagedViews"] == {"value": 0, "status": "available_zero"}
    assert out["a"]["averageViewPercentage"] == {"value": None, "status": "unavailable"}
    assert out["b"]["views"] == {"value": None, "status": "not_yet_populated"}
    failed = ya.metric_values(None, ["likes"], ["a"], error="server_error")
    assert failed["a"]["likes"]["value"] is None and failed["a"]["likes"]["status"] == "request_failed"
    incompatible = ya.metric_values(None, ["likes"], ["a"], error="incompatible_query")
    assert incompatible["a"]["likes"]["status"] == "unavailable"


def test_error_categories():
    assert ya.classify_error(_http_error(403, '{"error":{"message":"Request had insufficient authentication scopes."}}')) == "insufficient_scope"
    assert ya.classify_error(_http_error(400)) == "incompatible_query"
    assert ya.classify_error(_http_error(429)) == "quota_or_rate_limit"
    assert ya.classify_error(_http_error(401)) == "auth"


def _db(tmp_path, rows):
    path = tmp_path / "s.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE publishing_ledger (id INTEGER, youtube_video_id TEXT, video_path TEXT, thumbnail_path TEXT, "
              "topic_title TEXT, upload_timestamp TEXT, scheduled_timestamp TEXT, upload_status TEXT)")
    c.executemany("INSERT INTO publishing_ledger VALUES (?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return str(path)


def _collector(tmp_path, scopes, service, rows=None, today=date(2026, 11, 20)):
    rows = rows if rows is not None else [
        (1, "vidA", str(tmp_path / "p" / "jobA" / "video" / "a.mp4"), "t.png", "Topic A", "2026-11-18T00:00:00Z",
         "2026-11-18T03:30:00Z", "uploaded_public"),
        (2, "vidFuture", "", "", "Later", None, "2026-11-25T00:00:00Z", "publication_scheduled"),
        (3, None, "", "", "Not uploaded", None, None, "approved")]
    oauth = SimpleNamespace(token_file="tok", client_secrets_file="cs")
    col = ya.AnalyticsCollector(oauth_manager=oauth, db_path=_db(tmp_path, rows),
                                service_factory=lambda creds: service, today=today)
    col.status = lambda: {"scope_granted": ya.ANALYTICS_SCOPE in scopes}
    return col


def test_without_analytics_scope_no_request_is_made(tmp_path, monkeypatch):
    service = MagicMock()
    col = _collector(tmp_path, YOUTUBE_SCOPES, service)
    # Stage 3.35: the grant is verified with Google instead of trusting the stored scope list (the list was being
    # erased by upload refreshes). Without a valid grant no report request is made and the auth state is explicit.
    res = col.collect()
    assert res["state"] == "auth_failed" and res["auth_state"] in ("AUTHENTICATION_REQUIRED", "REVOKED", "ERROR")
    assert res["collected"] == 0 and res["api_calls"] == 0
    service.reports.assert_not_called()


def test_collects_published_videos_once_per_day_with_two_metric_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(ya, "analytics_credentials", lambda oauth: object())
    calls = []

    def query(**kw):
        calls.append(kw)
        metrics = kw["metrics"].split(",")
        if "likes" in metrics:                                   # engagement group rejected by this report
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(_http_error(400)))
        return SimpleNamespace(execute=lambda: {"columnHeaders": [{"name": "video"}] + [{"name": m} for m in metrics],
                                                "rows": [["vidA"] + [10] * len(metrics)]})
    service = MagicMock()
    service.reports.return_value.query.side_effect = query
    col = _collector(tmp_path, YOUTUBE_SCOPES + [ya.ANALYTICS_SCOPE], service)
    res = col.collect()
    assert res["state"] == "collected" and res["collected"] == 1 and res["api_calls"] == 2
    assert calls[0]["filters"] == "video==vidA" and calls[0]["dimensions"] == "video" and calls[0]["sort"] == "-views"
    latest = col.store.latest()[0]
    assert latest["metrics"]["views"] == 10 and latest["metric_status"]["views"] == "available"
    assert latest["metrics"]["likes"] is None and latest["metric_status"]["likes"] == "unavailable"
    assert latest["job_id"] == "jobA"
    again = col.collect()                                       # same day: nothing requested again
    assert again == {"state": "nothing_due", "collected": 0, "api_calls": 0}


def test_insufficient_scope_is_recorded_not_converted_to_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(ya, "analytics_credentials", lambda oauth: object())
    service = MagicMock()
    service.reports.return_value.query.return_value.execute.side_effect = _http_error(
        403, '{"error":{"message":"insufficient authentication scopes"}}')
    col = _collector(tmp_path, YOUTUBE_SCOPES + [ya.ANALYTICS_SCOPE], service)
    col.collect()
    row = col.store.latest()[0]
    assert row["error_category"] == "insufficient_scope"
    assert all(v is None for v in row["metrics"].values())
    assert set(row["metric_status"].values()) == {"request_failed"}


def test_authorization_is_incremental_on_the_same_client(monkeypatch):
    """2026-11-28: the dashboard flow requests the existing scopes plus analytics (full contract in
    test_analytics_authorization.py)."""
    assert ya.REQUIRED_AUTH_SCOPES == YOUTUBE_SCOPES + [ya.ANALYTICS_SCOPE]

