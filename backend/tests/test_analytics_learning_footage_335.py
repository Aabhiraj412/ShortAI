"""Stage 3.35: OAuth reliability, analytics ingestion, learning, providers, footage, voiceover fallback.

Isolated: temporary token files and databases, fake Google/HTTP objects, synthetic media. No network, no consent.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.app.services.youtube_analytics as ya
from backend.app.services import youtube_publishing_engine as ype
from backend.app.services.youtube_publishing_engine import YOUTUBE_SCOPES, merge_token_scopes

AN = ya.ANALYTICS_SCOPE


# == OAuth: the root cause (scope list erased by upload refreshes) ===============================================
def test_refresh_never_narrows_the_stored_scope_list():
    prev = YOUTUBE_SCOPES + [AN]
    assert merge_token_scopes(prev, YOUTUBE_SCOPES, authoritative=False) == sorted(prev)        # upload refresh
    assert merge_token_scopes(prev, [AN], authoritative=False) == sorted(prev)                  # analytics refresh
    assert merge_token_scopes(prev, YOUTUBE_SCOPES, authoritative=True) == sorted(YOUTUBE_SCOPES)  # a new consent


class _Creds:
    def __init__(self, scopes, refresh_token="rt", granted=None):
        self.scopes, self.refresh_token, self.granted_scopes = scopes, refresh_token, granted

    def to_json(self):
        return json.dumps({"token": "AT", "refresh_token": self.refresh_token, "scopes": self.scopes,
                           "client_id": "cid", "client_secret": "cs", "token_uri": "https://oauth2.googleapis.com/token"})


def _mgr(tmp_path, scopes):
    from backend.app.core.secure_storage import write_protected_file
    m = ype.YouTubeOAuthManager.__new__(ype.YouTubeOAuthManager)
    m.token_file = str(tmp_path / "token.json")
    m.client_secrets_file = str(tmp_path / "client_secrets.json")
    m.scopes = list(YOUTUBE_SCOPES)
    write_protected_file(m.token_file, json.dumps({"token": "x", "refresh_token": "rt", "scopes": scopes,
                                                   "client_id": "cid", "client_secret": "cs",
                                                   "token_uri": "https://oauth2.googleapis.com/token"}).encode())
    return m


def _stored(m):
    from backend.app.core.secure_storage import read_protected_file
    return json.loads(read_protected_file(m.token_file).decode("utf-8"))


def test_upload_refresh_keeps_the_analytics_grant_and_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ype, "_record_connection_event", lambda *a, **k: None)
    m = _mgr(tmp_path, YOUTUBE_SCOPES + [AN])
    m.save_credentials(_Creds(YOUTUBE_SCOPES, refresh_token=None), event="refresh")     # Google omitted the RT
    d = _stored(m)
    assert AN in d["scopes"] and set(YOUTUBE_SCOPES) <= set(d["scopes"]) and d["refresh_token"] == "rt"


def test_consent_is_authoritative_for_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(ype, "_record_connection_event", lambda *a, **k: None)
    m = _mgr(tmp_path, YOUTUBE_SCOPES + [AN])
    m.save_credentials(_Creds(YOUTUBE_SCOPES, granted=YOUTUBE_SCOPES), event="connect")
    assert AN not in _stored(m)["scopes"]                       # a consent without analytics really removes it


def test_record_granted_scope_is_idempotent(tmp_path):
    m = _mgr(tmp_path, YOUTUBE_SCOPES)
    assert m.record_granted_scope(AN) is True and m.record_granted_scope(AN) is False
    assert AN in _stored(m)["scopes"] and _stored(m)["token"] == "x"     # the access token is untouched


def test_concurrent_token_writes_never_lose_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(ype, "_record_connection_event", lambda *a, **k: None)
    m = _mgr(tmp_path, YOUTUBE_SCOPES)
    errors = []

    def work(i):
        try:
            if i % 2:
                m.record_granted_scope(AN)
            else:
                m.save_credentials(_Creds(YOUTUBE_SCOPES, refresh_token=None), event="refresh")
        except Exception as exc:
            errors.append(exc)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors and AN in _stored(m)["scopes"] and _stored(m)["refresh_token"] == "rt"


def test_oauth_redirect_codes_are_not_logged():
    import logging
    assert logging.getLogger("google_auth_oauthlib.flow").getEffectiveLevel() >= logging.WARNING


# == analytics auth state machine =================================================================================
class _FakeGoogleCreds:
    mode = "ok"

    def __init__(self, scopes):
        self.scopes, self.token, self.valid, self.expiry, self.granted_scopes = scopes, None, False, None, None

    @classmethod
    def from_authorized_user_info(cls, info, scopes):
        return cls(scopes)

    def refresh(self, request):
        from google.auth.exceptions import RefreshError, TransportError
        m = _FakeGoogleCreds.mode
        if m == "revoked":
            raise RefreshError("invalid_grant: Token has been expired or revoked.")
        if m == "noscope":
            raise RefreshError("invalid_scope: Bad Request")
        if m == "network":
            raise TransportError("connection reset")
        if m == "flaky":
            _FakeGoogleCreds.mode = "ok"
            raise TransportError("temporary")
        self.valid, self.token = True, "AT2"
        self.expiry = datetime.utcnow() + timedelta(hours=1)
        self.granted_scopes = list(self.scopes)


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    states = {}
    monkeypatch.setattr(ya, "_write_state", lambda k, s: states.__setitem__(k, s))
    monkeypatch.setattr(ya, "_read_state", lambda k: dict(states.get(k, {})))
    import google.oauth2.credentials as goc
    monkeypatch.setattr(goc, "Credentials", _FakeGoogleCreds)
    ya._cached.update(creds=None, token_file=None)
    _FakeGoogleCreds.mode = "ok"
    return _mgr(tmp_path, YOUTUBE_SCOPES), states


@pytest.mark.parametrize("mode,state", [("ok", ya.CONNECTED), ("revoked", ya.REVOKED), ("noscope", ya.AUTH_REQUIRED),
                                        ("network", ya.EXPIRED)])
def test_auth_states(auth_env, mode, state):
    m, states = auth_env
    _FakeGoogleCreds.mode = mode
    if mode == "ok":
        creds = ya.analytics_credentials(m, sleep=lambda s: None)
        assert creds.valid and AN in _stored(m)["scopes"]            # the confirmed grant is recorded (self-heal)
        assert set(YOUTUBE_SCOPES) <= set(_stored(m)["scopes"])      # uploads keep their scopes
    else:
        with pytest.raises(Exception):
            ya.analytics_credentials(m, sleep=lambda s: None)
    assert states[ya.AUTH_STATE_KEY]["state"] == state


def test_transient_refresh_failure_retries_with_backoff(auth_env):
    m, states = auth_env
    _FakeGoogleCreds.mode = "flaky"
    sleeps = []
    assert ya.analytics_credentials(m, sleep=sleeps.append).valid and sleeps == [1.0]


def test_concurrent_refreshes_are_serialised_and_cached(auth_env, monkeypatch):
    m, _ = auth_env
    calls = []
    orig = _FakeGoogleCreds.refresh
    monkeypatch.setattr(_FakeGoogleCreds, "refresh", lambda self, r: (calls.append(1), orig(self, r))[1])
    out = []
    ts = [threading.Thread(target=lambda: out.append(ya.analytics_credentials(m, sleep=lambda s: None))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(out) == 8 and len(calls) == 1                       # one refresh, shared by all callers


def test_missing_credentials_are_authentication_required(tmp_path, auth_env):
    m, states = auth_env
    os.remove(m.token_file)
    with pytest.raises(PermissionError):
        ya.analytics_credentials(m)
    assert states[ya.AUTH_STATE_KEY]["state"] == ya.AUTH_REQUIRED and ya.auth_status(m)["action"]


def test_analytics_never_writes_the_shared_access_token(auth_env):
    m, _ = auth_env
    ya.analytics_credentials(m, sleep=lambda s: None)
    assert _stored(m)["token"] == "x"                              # the upload client's token is untouched


# == analytics ingestion: first-7-days window, idempotence, budget ==============================================
def _db(tmp_path, published):
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, youtube_video_id TEXT, video_path TEXT, thumbnail_path TEXT, "
              "topic_title TEXT, upload_timestamp TEXT, scheduled_timestamp TEXT, upload_status TEXT)")
    for i, (vid, pub) in enumerate(published):
        c.execute("INSERT INTO publishing_ledger VALUES (?,?,?,?,?,?,?,?)",
                  (i + 1, vid, rf"media\productions\job{i}\video\v.mp4", None, f"T{i}", pub, pub, "publication_scheduled"))
    c.commit()
    c.close()
    return str(db)


def _service(rows_by_kind):
    calls = []

    def query(**kw):
        calls.append(kw)
        dims = kw.get("dimensions")
        if dims == "elapsedVideoTimeRatio":
            body = {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
                    "rows": [[0.1, 0.9], [0.5, 0.6], [1.0, 0.3]]}
        elif dims == "insightTrafficSourceType":
            body = {"columnHeaders": [{"name": "insightTrafficSourceType"}, {"name": "views"}], "rows": [["SHORTS", 7], ["YT_SEARCH", 3]]}
        else:
            metrics = kw["metrics"].split(",")
            ids = kw["filters"].split("==")[1].split(",")
            body = {"columnHeaders": [{"name": "video"}] + [{"name": m} for m in metrics],
                    "rows": [[v] + [10] * len(metrics) for v in ids]}
        return SimpleNamespace(execute=lambda: body)
    s = MagicMock()
    s.reports.return_value.query.side_effect = query
    return s, calls


def _collector(tmp_path, published, today, monkeypatch):
    monkeypatch.setattr(ya, "analytics_credentials", lambda oauth, **k: object())
    monkeypatch.setattr(ya, "_write_state", lambda k, s: None)
    monkeypatch.setattr(ya, "_read_state", lambda k: {})
    service, calls = _service({})
    col = ya.AnalyticsCollector(oauth_manager=SimpleNamespace(token_file="x"), db_path=_db(tmp_path, published),
                                service_factory=lambda c: service, today=today)
    return col, calls


def test_first_seven_days_window_with_retention_and_traffic(tmp_path, monkeypatch):
    col, calls = _collector(tmp_path, [("vOld", "2026-11-01T10:00:00Z"), ("vNew", "2026-11-19T10:00:00Z")],
                            date(2026, 11, 20), monkeypatch)
    res = col.collect()
    assert res["state"] == "collected" and res["collected"] == 2 and res["first_7_days"] == 1
    c = sqlite3.connect(col.db_path)
    w, m = c.execute("SELECT window_start || '..' || window_end, metrics_json FROM video_performance WHERE window_kind='first_7_days'").fetchone()
    assert w == "2026-11-01..2026-11-07"
    m = json.loads(m)
    assert m["retention"]["at_50pct"] == 0.6 and m["traffic_sources"] == {"SHORTS": 7, "YT_SEARCH": 3}
    again = col.collect()                                                  # same day: nothing is fetched again
    assert again["state"] == "nothing_due" and again["api_calls"] == 0


def test_call_budget_is_enforced(tmp_path, monkeypatch):
    pubs = [(f"v{i}", "2026-11-01T10:00:00Z") for i in range(5)]
    col, calls = _collector(tmp_path, pubs, date(2026, 11, 20), monkeypatch)
    res = col.collect(max_calls=4)
    assert res["api_calls"] <= 4 and res["budget_exhausted"]


def test_unavailable_metrics_are_not_zero(tmp_path, monkeypatch):
    col, _ = _collector(tmp_path, [("vNew", "2026-11-19T10:00:00Z")], date(2026, 11, 20), monkeypatch)
    resp = {"columnHeaders": [{"name": "video"}, {"name": "views"}], "rows": []}
    vals = ya.metric_values(resp, ["views", "likes"], ["vNew"])
    assert vals["vNew"]["views"] == {"value": None, "status": "not_yet_populated"}
    assert vals["vNew"]["likes"]["status"] == "unavailable" and vals["vNew"]["likes"]["value"] is None


# == learning ====================================================================================================
from backend.app.creative_intel import learning as L


def _rec(i, *, window="first_7_days", views=10, avp=40.0, hook="contradiction", cat="Science", craft=80):
    return {"video_id": f"v{i}", "job_id": f"j{i}", "window_kind": window,
            "decisions": {"category": cat, "hook_type": hook, "duration_sec": 52, "publish_hour_ist": 14,
                          "craft": {"overall": craft, "hook": craft - 10}, "composition_pct": {"photograph": 10}},
            "performance": {"views": views, "averageViewPercentage": avp, "engaged_rate": 0.5, "likes_per_100_views": 5}}


def test_insufficient_data_gives_no_claims_only_needs():
    a = L.analyse([_rec(i) for i in range(12)])
    assert a["sufficiency"] == "preliminary" and a["needed_for_claims"] == 18
    recs = L.recommendations(a)
    assert recs and all(r["status"] == "insufficient_data" and not r["applied"] and r["requires_authorization"] for r in recs)


def test_sufficient_data_produces_bounded_proposals():
    recs_in = [_rec(i, hook="direct_question", views=30, avp=60) for i in range(15)] + \
              [_rec(100 + i, hook="contradiction", views=10, avp=40) for i in range(15)]
    a = L.analyse(recs_in)
    assert a["sufficiency"] == "sufficient"
    props = [r for r in L.recommendations(a) if r["status"] == "proposal" and r["area"] == "hook_type"]
    assert props and props[0]["recommendation"].startswith("Prefer hook_type 'direct_question'")
    assert all(p["applied"] is False and p["sample_size"] == 30 and p["limitations"] for p in props)


def test_small_or_similar_groups_never_become_proposals():
    recs_in = [_rec(i, hook="a", views=10) for i in range(25)] + [_rec(100 + i, hook="b", views=11) for i in range(5)]
    props = [r for r in L.recommendations(L.analyse(recs_in)) if r["status"] == "proposal" and r["area"] == "hook_type"]
    assert props == []                                                    # group b has n < 10


def test_outliers_are_flagged_and_medians_are_robust():
    d = L.describe([10, 11, 9, 12, 10, 11, 10, 9, 500])
    assert d["median"] == 10 and d["outliers"] == 1 and d["winsorised_mean"] < 100


def test_spearman_and_conflicting_signals():
    r = L.spearman([1, 2, 3, 4, 5, 6, 7, 8], [2, 4, 6, 8, 10, 12, 14, 16])
    assert r["rho"] == 1.0 and r["p"] < 0.05 and "not causation" in r["note"]
    flat = L.spearman([1, 2, 3, 4, 5, 6], [3, 1, 4, 1, 5, 9])
    assert -1 <= flat["rho"] <= 1
    assert L.spearman([1, 2], [1, 2])["confidence"] == "insufficient"


def test_learning_run_persists_reproducibly(tmp_path):
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE video_performance (video_id TEXT, collected_date TEXT, collected_at_utc TEXT, job_id TEXT, topic TEXT, "
              "topic_category TEXT, creative_angle TEXT, script_fingerprint TEXT, hook TEXT, duration_sec REAL, renderer TEXT, "
              "visual_engine TEXT, visual_composition TEXT, thumbnail TEXT, published_at_utc TEXT, window_start TEXT, "
              "window_end TEXT, metrics_json TEXT, metric_status_json TEXT, error_category TEXT, window_kind TEXT)")
    c.execute("INSERT INTO video_performance (video_id, collected_date, collected_at_utc, job_id, topic_category, published_at_utc, "
              "metrics_json, metric_status_json, window_kind) VALUES ('v1','D7:2026-11-07','2026-11-09','j1','Science',"
              "'2026-11-01T10:00:00Z', '{\"views\": 11, \"averageViewPercentage\": 47.6}', '{}', 'first_7_days')")
    c.commit()
    c.close()
    a = L.run(str(db), tmp_path / "productions")
    b = L.run(str(db), tmp_path / "productions")
    assert a["input_hash"] == b["input_hash"] and a["analysis"]["videos_first_7_days"] == 1
    assert L.latest_run(str(db))["analysis"]["sufficiency"] == "preliminary"
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM learning_records").fetchone()[0] == 1


def test_shadow_topic_flags_are_not_touched_by_learning():
    src = (Path(__file__).resolve().parents[1] / "app" / "creative_intel" / "learning.py").read_text(encoding="utf-8")
    assert "creative_policy_config" not in src and "topic_intelligence" not in src and "set_level" not in src


# == providers =====================================================================================================
from backend.app.services import provider_audit as pa


@pytest.mark.parametrize("row,rendered,state", [
    ({"status": pa.KEY_MISSING}, None, "KEY_MISSING"),
    ({"status": pa.CONFIGURED_INVALID}, None, "AUTHENTICATION_REQUIRED"),
    ({"status": pa.INTERMITTENT, "rate_limited": True}, None, "RATE_LIMITED"),
    ({"status": pa.INTERMITTENT}, 3, "DEGRADED"),
    ({"status": pa.CONFIGURED_HEALTHY, "recent_health": {"ok": 73}}, 0, "NOT_USED"),
    ({"status": pa.NO_KEY_NEEDED, "recent_health": {"ok": 93}}, 21, "HEALTHY"),
    ({"status": pa.UNVERIFIED}, None, "CONFIGURED"),
    ({"status": pa.NOT_IMPLEMENTED}, None, "DISABLED"),
])
def test_provider_states_separate_configuration_health_and_usage(row, rendered, state):
    assert pa.state_335(row, rendered) == state


# == footage =====================================================================================================
from backend.app.media import footage as ft
from backend.app.rendering.pop_info import scene_footage as sf


def _cand(**kw):
    base = dict(provider="Pixabay Videos", asset_id="p1", title="glacier lake", tags=["glacier", "ice", "lake"], description="",
                page_url="https://pixabay.com/videos/x", download_url="https://cdn/x.mp4", width=1920, height=1080,
                duration=15, license="Pixabay Content License", creator="user", attribution_required=False)
    base.update(kw)
    return ft.FootageCandidate(**base)


@pytest.mark.parametrize("lic,ok", [("Pixabay Content License", True), ("CC BY-SA 4.0", True), ("CC0", True),
                                    ("Public domain", True), ("CC BY-NC 4.0", False), ("CC BY-ND 2.0", False),
                                    ("All rights reserved", False), ("", False)])
def test_footage_licences(lic, ok):
    assert ft.licence_ok(_cand(license=lic, attribution_required=True))[0] is ok


def test_footage_relevance_requires_subject_or_scene_words():
    assert ft.relevance(_cand(), "glacier", "The lake forms at the glacier edge")["relevant"]
    assert not ft.relevance(_cand(tags=["city", "night"], title="city night"), "glacier", "The lake forms at the glacier edge")["relevant"]


def test_footage_relevance_rejects_partial_subject_match():
    """Found in the 3.35 preview: a wind-turbine road clip matched "Turbine blade" on one word only."""
    wind = _cand(title="wind turbine fog", tags=["road", "wind turbine", "fog", "plateau", "motorcycle"])
    narr = "Superalloys keep each blade strong at extreme heat inside the jet engine"
    assert not ft.relevance(wind, "Turbine blade", narr)["relevant"]
    jet = _cand(title="jet engine turbine", tags=["jet engine", "turbine", "aircraft"])
    assert ft.relevance(jet, "Turbine blade", narr)["relevant"]                 # half the subject + a scene word
    assert ft.relevance(_cand(title="turbine blade", tags=[]), "Turbine blade", "")["relevant"]   # full subject


def test_footage_rejects_differently_qualified_subject():
    """Found in the 3.35 preview: wind-turbine clips chosen for a gas/steam turbine script."""
    script = ("an entire gas turbine or steam engine can instantly fail. These parts are turbine blades, mounted in a "
              "spinning disc inside gas and steam turbines. Because of this harsh heat, these blades are often the "
              "primary component limiting gas turbines.")
    cases = [(["wind energy", "turbine", "alternative energy", "engine"], "an entire gas turbine or steam engine can instantly fail."),
             (["windmills", "wind turbines", "renewable", "turbine", "blades"], "These parts are turbine blades, mounted in a spinning disc "
                                                            "inside gas and steam turbines.")]
    for tags, narr in cases:
        rel = ft.relevance(_cand(title=", ".join(tags), tags=tags), "Turbine blade", narr, context=script)
        assert not rel["relevant"] and "gas" in rel["qualifier_conflict"]
        assert ft.relevance(_cand(title=", ".join(tags), tags=tags), "Turbine blade", narr)["relevant"]  # without context
    narr = cases[0][1]
    gas = _cand(title="gas turbine engine", tags=["gas turbine", "engine", "power plant"])
    assert ft.relevance(gas, "Turbine blade", narr, context=script)["relevant"]
    # no qualifier in the script: nothing to conflict with
    assert ft.relevance(_cand(), "glacier", "The lake forms at the glacier edge", context="The glacier melts")["relevant"]
    # a qualifier must be a consistent compound (twice), not a one-off word before the subject
    assert ft.subject_qualifiers("gas turbines, gas turbine and steam turbine", {"turbine"}) == {"turbine": {"gas"}}
    glacier_script = "Lakes touching glaciers can accelerate retreat. When frozen glaciers border a lake, the glacier melts."
    ice = _cand(title="glacier ice", tags=["glacier", "ice", "mountain", "lake"])
    assert ft.relevance(ice, "lake glacier", "When frozen glaciers border a lake", context=glacier_script)["relevant"]


def test_footage_subject_and_queries_use_filmable_recurring_words():
    narr = ["Lakes touching glaciers can actually accelerate their retreat.",
            "When frozen glaciers border a lake, a dynamic relationship begins. The lake water melts the glacier.",
            "Scientists had not yet clarified this global picture of lake water and glacier retreat."]
    plan = {"topic": "Global analysis reveals how lakes amplify glacier ice loss",
            "scenes": [{"template": t, "narration": n, "start_sec": i * 5, "end_sec": i * 5 + 5, "scene_id": f"scene_{i + 1:02d}"}
                       for i, (t, n) in enumerate(zip(["title", "spotlight", "environment", "spotlight", "reveal"],
                                                      ["Intro."] + narr + ["End."]))]}
    subject, _ = sf.footage_subject(plan, "Global analysis")
    assert subject == "lake glacier"
    words = " ".join(q for n in sf.footage_needs(plan, "Global analysis") for q in n.queries)
    for abstract in ("global", "analysis", "actually", "accelerate", "relationship", "scientist"):
        assert abstract not in words
    assert "retreat" in words or "water" in words


def test_pixabay_title_falls_back_to_tags_for_id_slugs():
    data = {"hits": [{"id": 9, "pageURL": "https://pixabay.com/videos/id-274403/", "tags": "road, wind turbine, fog", "duration": 15,
                      "user": "u", "videos": {"small": {"url": "https://s.mp4", "width": 720, "height": 1280, "size": 5}}}]}
    c = ft.PixabayVideoProvider(api_key="k", get_json=lambda *a, **k: data).search("x")[0]
    assert c.title == "road, wind turbine, fog"


def test_pixabay_video_parsing_and_missing_key():
    data = {"hits": [{"id": 5, "pageURL": "https://pixabay.com/videos/ice-glacier-5/", "tags": "ice, glacier", "duration": 12,
                      "user": "u", "videos": {"large": {"url": "https://l.mp4", "width": 3840, "height": 2160, "size": 90},
                                              "medium": {"url": "https://m.mp4", "width": 1920, "height": 1080, "size": 20},
                                              "tiny": {"url": "https://t.mp4", "width": 640, "height": 360, "size": 2}}}]}
    p = ft.PixabayVideoProvider(api_key="k", get_json=lambda url, prov, headers=None: data)
    c = p.search("glacier")[0]
    assert c.download_url == "https://m.mp4" and c.duration == 12 and c.tags == ["ice", "glacier"]
    assert ft.PixabayVideoProvider(api_key="").search("x") == [] and ft.PexelsVideoProvider(api_key="").search("x") == []


def test_wikimedia_video_parsing_licence_and_attribution():
    data = {"query": {"pages": {"1": {"pageid": 9, "title": "File:Glacier calving.webm", "videoinfo": [{
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Glacier_calving.webm", "duration": 20,
        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": "<a>Jane</a>"}},
        "derivatives": [{"type": "video/webm; codecs=vp9", "height": 720, "width": 1280, "src": "https://d720.webm"},
                        {"type": "video/webm; codecs=vp9", "height": 360, "width": 640, "src": "https://d360.webm"}]}]}}}}
    c = ft.WikimediaVideoProvider(get_json=lambda url, prov, headers=None: data).search("glacier")[0]
    assert c.download_url == "https://d720.webm" and c.creator == "Jane" and c.attribution_required


def test_footage_needs_skip_title_payoff_data_and_photo_scenes():
    plan = {"scenes": [{"template": t, "narration": "glacier lake ice", "start_sec": i * 5, "end_sec": i * 5 + 5,
                        "scene_id": f"scene_{i + 1:02d}"} for i, t in enumerate(
        ["title", "spotlight", "stat", "environment", "photo", "kinetic", "callout", "reveal"])]}
    idx = [n.scene_index for n in sf.footage_needs(plan, "glacier", exclude=[4])]
    assert 0 not in idx and 7 not in idx and 2 not in idx and 4 not in idx and 5 not in idx and idx


def _ffmpeg():
    from test_render_geometry_332h import FFMPEG
    return FFMPEG


def _clip(path, w=1280, h=720, d=6, audio=True):
    args = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={d}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={d}", "-shortest"]
    subprocess.run(args + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)


def test_footage_validation_and_extraction(tmp_path):
    if not _ffmpeg():
        pytest.skip("FFmpeg not available")
    clip = tmp_path / "c.mp4"
    _clip(clip)
    info = ft.validate(clip, _ffmpeg(), min_duration=3)
    assert info["width"] == 1280 and info["duration"] == pytest.approx(6, abs=0.2)
    n = ft.extract_frames(clip, tmp_path / "frames", duration=2.0, ffmpeg=_ffmpeg())
    assert n == 60
    from PIL import Image
    assert Image.open(tmp_path / "frames" / "f_0001.jpg").size == (1080, 1920)          # 9:16, no letterbox
    small, short, bad = tmp_path / "s.mp4", tmp_path / "t.mp4", tmp_path / "b.mp4"
    _clip(small, 320, 180)
    _clip(short, d=1)
    bad.write_bytes(b"not a video")
    for p, why in ((small, "too small"), (short, "too short"), (bad, "not a readable video")):
        with pytest.raises(ValueError, match=why):
            ft.validate(p, _ffmpeg(), min_duration=3)


def test_select_scene_footage_end_to_end_with_dedupe_and_fallback(tmp_path):
    if not _ffmpeg():
        pytest.skip("FFmpeg not available")
    clip = tmp_path / "src.mp4"
    _clip(clip, d=8)
    data = clip.read_bytes()

    class P:
        name = "Pixabay Videos"

        def search(self, query, n):
            return [_cand(asset_id="dup", download_url="https://cdn/dup.mp4")]
    plan = {"scenes": [{"template": t, "narration": "the glacier lake grows", "start_sec": i * 4, "end_sec": i * 4 + 4,
                        "scene_id": f"scene_{i + 1:02d}"} for i, t in enumerate(["title", "spotlight", "callout", "environment", "reveal"])]}
    chosen, report = sf.select_scene_footage(plan=plan, anchor="glacier", job_id="j1", cache_root=tmp_path / "cache",
                                             ffmpeg=_ffmpeg(), providers=[P()],
                                             fetcher=lambda url, **kw: data)
    assert len(chosen) == 1                                        # the same clip is never used twice in one film
    rec = next(iter(chosen.values()))
    assert rec["kind"] == "video_footage" and rec["footage"]["count"] >= 100 and rec["license"] == "Pixabay Content License"
    fallbacks = [s for s in report["scenes"] if s.get("chosen") is None]
    assert fallbacks and all("already used" in json.dumps(s["rejected"]) for s in fallbacks)
    # cache reuse: a second film reuses the downloaded clip without fetching
    chosen2, _ = sf.select_scene_footage(plan=plan, anchor="glacier", job_id="j2", cache_root=tmp_path / "cache",
                                         ffmpeg=_ffmpeg(), providers=[P()],
                                         fetcher=lambda url, **kw: pytest.fail("downloaded twice"))
    assert next(iter(chosen2.values()))["cache_hit"] is True


def test_failed_provider_and_broken_download_keep_the_film(tmp_path):
    class Boom:
        name = "Pixabay Videos"

        def search(self, q, n):
            raise TimeoutError("provider down")
    plan = {"scenes": [{"template": t, "narration": "glacier", "start_sec": i * 4, "end_sec": i * 4 + 4, "scene_id": f"s{i}"}
                       for i, t in enumerate(["title", "spotlight", "reveal"])]}
    chosen, report = sf.select_scene_footage(plan=plan, anchor="glacier", job_id="j", cache_root=tmp_path, ffmpeg="ffmpeg",
                                             providers=[Boom()])
    assert chosen == {} and "error TimeoutError" in report["searches"][0]["note"]

    class Good:
        name = "Pixabay Videos"

        def search(self, q, n):
            return [_cand()]
    chosen, report = sf.select_scene_footage(plan=plan, anchor="glacier", job_id="j", cache_root=tmp_path, ffmpeg="ffmpeg",
                                             providers=[Good()], fetcher=lambda url, **kw: b"<html>blocked</html>")
    assert chosen == {} and "invalid or failed download" in json.dumps(report["scenes"])


def test_page_engine_loads_footage_frames_before_drawing():
    js = (Path(__file__).resolve().parents[1] / "app" / "rendering" / "pop_info" / "engine.js").read_text(encoding="utf-8")
    assert "window.render = async t => { await ensureFootage(t); render(t); };" in js
    assert "await im.decode()" in js and "case 'still'" in js


def test_asset_manifest_reports_exact_rendered_assets(tmp_path):
    from backend.app.rendering.pop_info.asset_manifest import build_manifest, composition
    plan = {"scenes": [
        {"scene_id": "s1", "template": "title", "start_sec": 0, "end_sec": 4},
        {"scene_id": "s2", "template": "photo", "start_sec": 4, "end_sec": 10,
         "photo": {"provider": "Pixabay Videos", "asset_id": "p1", "license": "Pixabay Content License", "footage": {"count": 180},
                   "duration": 15, "relevance": {"score": 0.8}, "final_score": 0.7, "cache_hit": False}},
        {"scene_id": "s3", "template": "photo", "start_sec": 10, "end_sec": 14,
         "photo": {"provider": "Wikimedia Commons", "asset_id": "w1", "license": "CC BY-SA 4.0"}},
        {"scene_id": "s4", "template": "spotlight", "start_sec": 14, "end_sec": 20}]}
    c = composition(plan)
    assert c["share_pct"] == {"text_led": 20.0, "video_footage": 30.0, "photograph": 20.0, "motion_graphic": 30.0}
    m = build_manifest(job_id="j", plan=plan, storyboard_assets=[{"asset_id": "sb1", "source_provider": "Openverse"}])
    assert [a["asset_type"] for a in m["rendered_assets"]] == ["video_footage", "photograph"]
    assert m["rendered_assets"][0]["frames_rendered"] == 180 and m["acquired_not_rendered"][0]["asset_id"] == "sb1"


def test_footage_rollback_switch(monkeypatch):
    from backend.app.rendering import render_parity
    monkeypatch.setenv("CRAZZYDOCS_FOOTAGE", "off")
    assert render_parity.footage_enabled() is False
    monkeypatch.setenv("CRAZZYDOCS_FOOTAGE", "on")
    assert render_parity.footage_enabled() is True


# == voiceover fallback (off by default) ===========================================================================
def test_tts_fallback_is_off_by_default_and_safe_quoting(monkeypatch):
    from backend.app.services import tts_fallback
    monkeypatch.delenv("CRAZZYDOCS_TTS_FALLBACK", raising=False)
    assert tts_fallback.enabled() is False
    assert tts_fallback._ps_quote("it's $(rm -rf) `x`") == "'it''s $(rm -rf) `x`'"   # literal string, no interpolation


def test_tts_fallback_used_only_when_enabled(tmp_path, monkeypatch):
    import asyncio
    from backend.app.services import expressive_tts_engine as tts
    from backend.app.services import tts_fallback

    class Fail:
        def __init__(self, **kw):
            pass

        async def save(self, path):
            raise RuntimeError("service unavailable")
    monkeypatch.setattr(tts.edge_tts, "Communicate", Fail)
    monkeypatch.setattr(tts.asyncio, "sleep", lambda s, _r=asyncio.sleep: _r(0))
    eng = tts.ExpressiveTTSEngine(request_timeout_sec=0.5)
    seg = tts.NarrationSegment(id="s1", text="Hello world, a test.", emotion="neutral")
    monkeypatch.delenv("CRAZZYDOCS_TTS_FALLBACK", raising=False)
    with pytest.raises(RuntimeError):
        asyncio.run(eng.synthesize_segment(seg, str(tmp_path)))
    monkeypatch.setenv("CRAZZYDOCS_TTS_FALLBACK", "sapi")
    called = []
    monkeypatch.setattr(tts_fallback, "synthesize", lambda text, out: called.append(out) or None)
    with pytest.raises(RuntimeError):                                    # fallback failed too: still a clear failure
        asyncio.run(eng.synthesize_segment(tts.NarrationSegment(id="s2", text="x y z", emotion="neutral"), str(tmp_path)))
    assert called
