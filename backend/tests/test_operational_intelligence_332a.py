"""Stage 3.32A: read-only Operational Intelligence."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.app.services.operational_intelligence as oi
from backend.app.content import candidate_ledger as cl

NOW = datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc)          # 12:00 IST

SCHEMA = [
    """CREATE TABLE persistent_jobs (id VARCHAR(100) NOT NULL, date_str VARCHAR(20) NOT NULL, topic_title VARCHAR(255),
       status VARCHAR(50), current_stage VARCHAR(50), progress_pct FLOAT, heartbeat_at DATETIME, retry_count INTEGER,
       max_retries INTEGER, error_details TEXT, checkpoint_data_json TEXT, youtube_video_id VARCHAR(100),
       scheduled_publication_time VARCHAR(50), publishing_mode VARCHAR(50), created_at DATETIME, updated_at DATETIME,
       completed_at DATETIME, recovery_reason VARCHAR(100), recovery_policy VARCHAR(50) DEFAULT 'CATCH_UP',
       original_target_pub VARCHAR(100), revised_target_pub VARCHAR(100), actual_start_at DATETIME, PRIMARY KEY (id))""",
    """CREATE TABLE production_lifecycle_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL,
       timestamp_ist TEXT NOT NULL, cycle_id TEXT, job_id TEXT, event_type TEXT NOT NULL, execution_state TEXT NOT NULL,
       details_json TEXT, error_details TEXT)""",
    """CREATE TABLE youtube_api_requests (correlation_id TEXT PRIMARY KEY, ts_utc TEXT NOT NULL, pt_day TEXT NOT NULL,
       method TEXT NOT NULL, bucket TEXT NOT NULL, cost INTEGER NOT NULL, cost_verified INTEGER NOT NULL, cost_source TEXT,
       purpose TEXT, job_id TEXT, video_id TEXT, attempt INTEGER, retry_reason TEXT, outcome TEXT, http_status INTEGER,
       error_category TEXT)""",
    """CREATE TABLE youtube_quota_blocks (bucket TEXT PRIMARY KEY, blocked_at_utc TEXT NOT NULL, blocked_until_utc TEXT NOT NULL,
       reason TEXT, operation TEXT, job_id TEXT, video_id TEXT)""",
    """CREATE TABLE creative_identity_registry (job_id TEXT PRIMARY KEY, normalized_topic TEXT NOT NULL, topic_title TEXT NOT NULL,
       category TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SELECTED', renderer_config_json TEXT NOT NULL DEFAULT '{}',
       visual_engine TEXT NOT NULL DEFAULT '', script_source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
]


def sp(dt):   # persistent_jobs format
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def iso(dt):
    return dt.isoformat()


def make_db(path, *, ledger=True):
    conn = sqlite3.connect(path)
    for s in SCHEMA:
        conn.execute(s)
    if ledger:
        for s in cl._SCHEMA:
            conn.execute(s)
    conn.commit()
    return conn


def add_job(conn, jid, status, start, minutes=None, stage="COMPLETED", error=None, retry=0):
    done = start + timedelta(minutes=minutes) if minutes is not None else None
    conn.execute("INSERT INTO persistent_jobs (id, date_str, status, current_stage, retry_count, error_details, created_at, "
                 "updated_at, completed_at, actual_start_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (jid, start.strftime("%Y-%m-%d"), status, stage, retry, error, sp(start), sp(done or start),
                  sp(done) if done and status == "COMPLETED" else None, sp(start)))


def add_event(conn, at, job, etype, error=None):
    conn.execute("INSERT INTO production_lifecycle_events (timestamp_utc, timestamp_ist, cycle_id, job_id, event_type, "
                 "execution_state, details_json, error_details) VALUES (?,?,?,?,?,?,?,?)",
                 (iso(at), iso(at), job.split("_2026")[0], job, etype, "x", "{}", error))


@pytest.fixture
def seeded(tmp_path):
    db = str(tmp_path / "studio.db")
    conn = make_db(db)
    t0 = NOW - timedelta(hours=10)
    add_job(conn, "cycle_1_20261120_0000", "COMPLETED", t0, 12)
    add_job(conn, "cycle_2_20261120_0100", "COMPLETED", t0 + timedelta(hours=1), 18)
    add_job(conn, "cycle_3_20261120_0200", "COMPLETED", t0 + timedelta(hours=2), 600)        # re-armed much later
    add_job(conn, "cycle_4_20261120_0300", "FAILED", t0 + timedelta(hours=3), stage="RENDERING",
            error="[TRANSIENT] RuntimeError: FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0000023fbcebd0c0] Padded dimensions cannot be smaller than input dimensions.")
    add_job(conn, "cycle_5_20261120_0400", "RECOVERY_WINDOW_EXPIRED", t0 + timedelta(hours=4), stage="RECOVERY_WINDOW_EXPIRED")
    add_job(conn, "cycle_6_20261120_0500", "RECOVERY_WINDOW_EXPIRED", t0 + timedelta(hours=5), stage="STORY_DEVELOPMENT",
            error="[PERMANENT] DuplicateCreativeIdentity: integrity gate: every discovered topic was already selected by an earlier production (1 rejected)")
    add_job(conn, "cycle_7_20261120_0600", "RUNNING", t0 + timedelta(hours=6), stage="RENDERING")
    add_job(conn, "cycle_8_20261120_0700", "QUEUED", t0 + timedelta(hours=7), stage="QUEUED")
    add_job(conn, "job_test_legacy", "FAILED", t0, error="ignored: not a production cycle")
    # cycle_2 recovered after a file-lock failure; cycle_4 failed three times with the same FFmpeg error
    add_event(conn, t0 + timedelta(hours=1, minutes=2), "cycle_2_20261120_0100", "CYCLE_DISPATCH_STARTED")
    add_event(conn, t0 + timedelta(hours=1, minutes=3), "cycle_2_20261120_0100", "CYCLE_DISPATCH_FAILED",
              "[WinError 32] The process cannot access the file because it is being used by another process: 'D:\\x\\temp_Openverse_1.tmp'")
    add_event(conn, t0 + timedelta(hours=1, minutes=4), "cycle_2_20261120_0100", "CYCLE_DISPATCH_STARTED")
    add_event(conn, t0 + timedelta(hours=1, minutes=18), "cycle_2_20261120_0100", "PRODUCTION_CYCLE_COMPLETED")
    for i, addr in enumerate(("0000023fbcebd0c0", "0000014e96daeb40", "0000021550d81ec0")):
        add_event(conn, t0 + timedelta(hours=3, minutes=i), "cycle_4_20261120_0300", "CYCLE_DISPATCH_FAILED",
                  f"FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ {addr}] Padded dimensions cannot be smaller than input dimensions.")
    add_event(conn, t0 + timedelta(hours=3, minutes=5), "cycle_4_20261120_0300", "RETRY_POLICY_REARMED")
    # YouTube requests (today's Pacific day is 2026-11-19 at 06:30 UTC)
    for i in range(3):
        conn.execute("INSERT INTO youtube_api_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"r{i}", iso(NOW - timedelta(hours=1, minutes=i)), "2026-11-19", "thumbnails.set", "general", 50, 1, "doc",
                      "thumbnail", None, None, 1, None, "ok", 200, None))
    conn.execute("INSERT INTO youtube_api_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("r9", iso(NOW - timedelta(hours=2)), "2026-11-19", "videos.update", "general", 50, 1, "doc", "metadata",
                  None, None, 1, None, "error", 403, "insufficient_scope"))
    conn.execute("INSERT INTO youtube_api_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("r10", iso(NOW - timedelta(hours=2)), "2026-11-19", "videos.insert", "videos.insert", 1, 1, "doc", "upload",
                  None, None, 1, None, "ok", 200, None))
    conn.execute("INSERT INTO youtube_quota_blocks VALUES (?,?,?,?,?,?,?)",
                 ("videos.insert", iso(NOW - timedelta(hours=1)), (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "quotaExceeded", "upload", None, None))
    for jid, src, renderer in (("cycle_1_20261120_0000", "llm:gemini-3.6-flash", "lemo-opuscar"),
                               ("cycle_2_20261120_0100", "extractive_fallback", "lemo-opuscar")):
        conn.execute("INSERT INTO creative_identity_registry (job_id, normalized_topic, topic_title, status, renderer_config_json, "
                     "script_source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (jid, jid, jid, "PRODUCED", json.dumps({"renderer": renderer}), src, iso(NOW - timedelta(hours=9)), iso(NOW)))
    # Stage 3.31 ledger: one run
    conn.execute("INSERT INTO topic_discovery_runs (run_id, job_id, attempts, started_at, candidate_count, eligible_count, "
                 "selected_candidate_id, outcome, provider_stats_json, recovery_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("cycle_1_20261120_0000", "cycle_1_20261120_0000", 1, iso(NOW - timedelta(hours=10)), 4, 1, "tc_a", "PRODUCED",
                  json.dumps({"wikipedia_search": {"queries": 4, "articles": 24, "candidates_created": 3, "accepted": 0, "rejected": 3, "errors": 0},
                              "physorg_rss": {"queries": 5, "articles": 150, "candidates_created": 1, "accepted": 1, "rejected": 0, "errors": 1}}),
                  "{}", iso(NOW)))
    for cid, status, reason, cat, v2 in (
            ("tc_a", "PRODUCED", None, "Technology", {"status": "UNKNOWN"}),
            ("tc_b", "REJECTED", "RECENTLY_USED", "History", {"status": "CLASSIFIED", "display_name": "Space and Astronomy"}),
            ("tc_c", "REJECTED", "INSUFFICIENT_EVIDENCE", "Geography", {"status": "CLASSIFIED", "display_name": "Geography"}),
            ("tc_d", "REJECTED", "INSUFFICIENT_EVIDENCE", "Science", {})):
        conn.execute("INSERT INTO topic_candidate_ledger (candidate_id, normalized_title, title, category, category_v2_json, provider_family, "
                     "first_discovered_at, last_discovered_at, selection_status, rejection_reason, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, cid, cid, cat, json.dumps(v2), "wikipedia_search", iso(NOW - timedelta(hours=10)),
                      iso(NOW - timedelta(hours=10)), status, reason, iso(NOW)))
    for i, (status, reason) in enumerate((("ELIGIBLE", None), ("REJECTED", "RECENTLY_USED"), ("REJECTED", "INSUFFICIENT_EVIDENCE"),
                                          ("REJECTED", "INSUFFICIENT_EVIDENCE"))):
        conn.execute("INSERT INTO topic_candidate_events VALUES (?,?,?,?,?,?,?,?)",
                     (f"e{i}", "cycle_1_20261120_0000", f"tc_{i}", "x", status, reason, "{}", iso(NOW - timedelta(hours=10))))
    conn.commit()
    conn.close()
    store = tmp_path / "production_progress_store.json"
    store.write_text(json.dumps({"productions": {
        "cycle_1_20261120_0000": {"execution_context": "PRODUCTION", "started_at": iso(t0), "stages": {
            "video_rendering": {"status": "completed", "started_at": iso(t0 + timedelta(minutes=3)), "completed_at": iso(t0 + timedelta(minutes=6))},
            "topic_discovery": {"status": "completed", "started_at": iso(t0), "completed_at": iso(t0 + timedelta(seconds=40))}},
            "assets": {"failed_acquisitions": 2, "unique_images": 5}},
        "cycle_4_20261120_0300": {"execution_context": "PRODUCTION", "started_at": iso(t0 + timedelta(hours=3)), "stages": {
            "video_rendering": {"status": "failed", "started_at": iso(t0 + timedelta(hours=3, minutes=3))}},
            "assets": {"failed_acquisitions": 1, "unique_images": 3}},
        "test_x": {"execution_context": "TEST", "started_at": iso(t0)}}}), encoding="utf-8")
    (tmp_path / "cache" / "gnews").mkdir(parents=True)
    (tmp_path / "cache" / "gnews" / "usage.json").write_text('{"2026-11-20": 3}', encoding="utf-8")
    svc = oi.OperationalIntelligence(db_path=db, data_dir=str(tmp_path), progress_store_path=str(store), cache_ttl=0,
                                     clock=lambda: NOW)
    return svc, db


# 1 -------------------------------------------------------------------------------------------------------
def test_production_statistics(seeded):
    svc, _ = seeded
    s = svc.production_health(days=1)
    m = s["metrics"]
    assert s["status"] == "actual"
    assert (m["total_cycles"], m["completed"], m["failed"], m["recovery_window_expired"], m["running"], m["queued"]) == (8, 3, 1, 2, 1, 1)
    assert m["finished"] == 6 and m["completion_pct"] == 50.0
    assert m["avg_production_minutes"] == 15.0 and m["long_running_completions"] == 1   # the 600-min outlier excluded
    assert m["median_production_minutes"] == 18.0
    assert m["stage_failure_distribution"] == {"RENDERING": 1, "NOT_RECORDED": 1, "STORY_DEVELOPMENT": 1}
    assert m["avg_stage_seconds"]["video_rendering"] == 180.0


# 2 -------------------------------------------------------------------------------------------------------
def test_failure_grouping_and_classes(seeded):
    svc, _ = seeded
    m = svc.failure_intelligence(days=1)["metrics"]
    pats = {p["cause"]: p for p in m["patterns"]}
    assert pats["ffmpeg"]["count"] == 3 and pats["ffmpeg"]["jobs"] == ["cycle_4_20261120_0300"]     # addresses normalized
    assert pats["file_lock"]["count"] == 1
    assert pats["topic_exhaustion"]["class"] == "PERMANENT"
    assert pats["recovery_window_expired"]["class"] == "EXPIRED"
    assert m["by_root_cause"]["ffmpeg"] == 3
    assert m["most_failure_prone_stage"] == "RENDERING"
    assert any(p["cause"] == "ffmpeg" for p in m["repeated_patterns"])
    assert "2026-11-20" in m["daily_trend"]


def test_root_cause_catalog_and_templates():
    assert oi.root_cause("[WinError 32] being used by another process: 'temp_Openverse.tmp'") == "file_lock"
    assert oi.root_cause("BLOCKED_QC: Renderer Compliance Failure: 'X' is not an approved production renderer") == "renderer_compliance"
    assert oi.root_cause("Daily YouTube API quota limit reached") == "quota"
    assert oi.root_cause("", "RECOVERY_WINDOW_EXPIRED") == "recovery_window_expired"
    assert oi.root_cause("") == "unrecorded"
    a = oi.error_template("FFmpeg failed: [Parsed_pad_2 @ 0000023fbcebd0c0] in 'D:\\a\\b.mp4' after 12.5 s")
    b = oi.error_template("FFmpeg failed: [Parsed_pad_7 @ 0000014e96daeb40] in 'D:\\c\\d.mp4' after 3 s")
    assert a == b
    assert oi.pattern_key("QC Failure: Duration <n>s is outside. (failed after rendering; not retried)") == \
        oi.pattern_key("QC Failure: Duration <n>s is outside.")
    assert oi.failure_class("[POST_RENDER_FAILURE] ValueError: QC")[0] == "POST_RENDER_FAILURE"


# 3 -------------------------------------------------------------------------------------------------------
def test_recovery_metrics(seeded):
    svc, _ = seeded
    m = svc.recovery_analytics(days=1)["metrics"]
    assert m["jobs_needing_recovery"] == 2                  # cycle_2 (dispatched twice, recovered) and cycle_4 (failed, re-armed)
    assert m["recovered"] == 1 and m["failed_after_recovery"] == 1 and m["recovery_success_pct"] == 50.0
    assert m["avg_time_to_recover_minutes"] == 15.0 and m["recovered_within_60_min"] == 1
    assert m["recovery_window_expiration_pct"] == round(100 * 2 / 6, 1)
    assert m["rearm_events"] == 1


# 4 -------------------------------------------------------------------------------------------------------
def test_renderer_reliability(seeded):
    svc, _ = seeded
    s = svc.renderer_reliability(days=1)
    m = s["metrics"]
    assert (m["renders_completed"], m["renders_failed"], m["render_success_pct"]) == (1, 1, 50.0)
    assert m["avg_render_seconds"] == 180.0
    assert m["produced_by_renderer"] == {"lemo-opuscar": 2} and m["lemo_opuscar_produced"] == 2
    assert (m["ffmpeg_errors"], m["file_lock_errors"]) == (3, 1)
    assert (m["media_acquisitions_failed"], m["media_unique_images"]) == (3, 8)


# 5 -------------------------------------------------------------------------------------------------------
def test_candidate_ledger_analytics(seeded):
    svc, _ = seeded
    s = svc.topic_intelligence(days=1)
    m = s["metrics"]
    assert s["status"] == "actual"
    assert m["discovery_runs"] == 1 and m["candidates_recorded"] == 4 and m["avg_eligible_per_run"] == 1.0
    assert m["acceptance_pct"] == 25.0
    assert m["rejection_reasons"] == {"INSUFFICIENT_EVIDENCE": 2, "RECENTLY_USED": 1}
    assert m["provider_contribution"]["physorg_rss"]["accepted"] == 1
    assert m["classifier_v1_vs_v2"] == {"unknown": 1, "disagree": 1, "agree": 1}
    assert m["classifier_disagreement_pct"] == round(100 * 2 / 3, 1)
    assert m["selected_category_distribution"] == {"Technology": 1}
    assert m["topic_exhaustion_failures"] == 1


# 6 -------------------------------------------------------------------------------------------------------
def test_api_quota_calculations(seeded):
    svc, _ = seeded
    s = svc.api_intelligence(days=1)
    m = s["metrics"]
    q = m["youtube_quota_today_pacific"]
    assert m["youtube_quota_day"] == "2026-11-19"
    assert q["general"]["used_estimate"] == 200 and q["general"]["requests"] == 4
    assert q["general"]["used_pct_estimate"] == 2.0 and q["general"]["remaining_estimate"] == 9800
    assert q["videos.insert"]["google_block_active"] is True
    assert m["youtube_errors"] == {"videos.update:insufficient_scope": 1}
    assert m["scripts_by_source"] == {"gemini": 1, "extractive_fallback": 1}
    assert m["research_provider_requests"]["wikipedia_search"] == {"queries": 4, "errors": 0}
    assert m["gnews_requests_by_utc_day"] == {"2026-11-20": 3}
    assert "youtube_quota_today_pacific" in s["estimated"]
    assert "google_reported_quota" in s["unavailable"] and "gemini_call_count" in s["unavailable"]


# 7 -------------------------------------------------------------------------------------------------------
def test_missing_historical_data(tmp_path):
    db = str(tmp_path / "studio.db")
    make_db(db, ledger=False).close()
    svc = oi.OperationalIntelligence(db_path=db, data_dir=str(tmp_path), progress_store_path=str(tmp_path / "none.json"),
                                     cache_ttl=0, clock=lambda: NOW)
    t = svc.topic_intelligence()
    assert t["status"] == "unavailable" and "candidate ledger" in t["unavailable"]["all"]
    r = svc.renderer_reliability()
    assert r["metrics"]["renders_completed"] == 0 and "renders_completed" in r["unavailable"]
    assert r["metrics"]["media_acquisitions_failed"] is None            # unknown, not zero
    p = svc.production_health()
    assert p["metrics"]["completion_pct"] is None and p["metrics"]["avg_production_minutes"] is None
    a = svc.api_intelligence()
    assert "gnews_requests_by_utc_day" in a["unavailable"] and "research_provider_requests" in a["unavailable"]
    ov = svc.overview()
    assert ov["daily_summary"]["health"] == "UNKNOWN"


# 8 -------------------------------------------------------------------------------------------------------
def test_empty_and_missing_database(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    svc = oi.OperationalIntelligence(db_path=str(empty), data_dir=str(tmp_path), cache_ttl=0, clock=lambda: NOW)
    ov = svc.overview()
    assert ov["database_available"] is True
    assert ov["production_health"]["metrics"]["total_cycles"] == 0
    assert ov["topic_intelligence"]["status"] == "unavailable"
    missing = tmp_path / "missing.db"
    svc2 = oi.OperationalIntelligence(db_path=str(missing), data_dir=str(tmp_path), cache_ttl=0, clock=lambda: NOW)
    ov2 = svc2.overview()
    assert ov2["database_available"] is False and "not found" in ov2["database_error"]
    for name in ("production_health", "failure_intelligence", "recovery_analytics"):
        assert ov2[name]["status"] == "unavailable"
    assert not missing.exists(), "a read-only service must never create the database"


# 9 -------------------------------------------------------------------------------------------------------
def test_concurrent_production_writes(seeded):
    svc, db = seeded
    stop = threading.Event()
    errors, written = [], [0]

    def writer():
        conn = sqlite3.connect(db, timeout=10)
        i = 0
        while not stop.is_set() and i < 150:
            try:
                add_job(conn, f"cycle_9_w{i}", "COMPLETED", NOW - timedelta(minutes=30), 10)
                add_event(conn, NOW - timedelta(minutes=29), f"cycle_9_w{i}", "PRODUCTION_CYCLE_COMPLETED")
                conn.commit()
                written[0] += 1
            except Exception as exc:          # pragma: no cover - reported below
                errors.append(exc)
            i += 1
        conn.close()

    t = threading.Thread(target=writer)
    t.start()
    results = []
    for _ in range(25):
        ov = svc.overview(days=1)
        results.append(ov["production_health"]["metrics"].get("total_cycles"))
    stop.set()
    t.join(30)
    assert not errors, errors                          # production writes never failed because of analytics reads
    assert written[0] > 0
    assert all(isinstance(r, int) for r in results)
    assert svc.production_health(days=1)["metrics"]["total_cycles"] == 8 + written[0]


# 10 ------------------------------------------------------------------------------------------------------
def test_dashboard_api_responses(seeded, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    svc, _ = seeded
    monkeypatch.setattr(oi, "get_service", lambda: svc)
    remote = TestClient(app)
    assert remote.get("/api/ops/intelligence").status_code == 403              # localhost only
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = client.get("/api/ops/intelligence?days=1")
    assert r.status_code == 200
    body = r.json()
    for name in oi.OperationalIntelligence.SECTIONS + ("daily_summary", "window"):
        assert name in body
    assert body["production_health"]["metrics"]["completed"] == 3
    assert client.get("/api/ops/intelligence?days=0").status_code == 400
    assert client.get("/api/ops/intelligence?days=31").status_code == 400
    s = client.get("/api/ops/intelligence/failure_intelligence?days=1")
    assert s.status_code == 200 and s.json()["metrics"]["by_root_cause"]["ffmpeg"] == 3
    assert client.get("/api/ops/intelligence/not_a_section").status_code == 404
    d = client.get("/api/ops/daily-summary?date=2026-11-20")
    assert d.status_code == 200 and d.json()["date"] == "2026-11-20" and d.json()["health"] in ("HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN")
    assert client.get("/api/ops/daily-summary?date=20-11-2026").status_code == 400


def test_daily_summary_anomalies_and_recommendations(seeded):
    svc, _ = seeded
    ds = svc.daily_summary("2026-11-20")
    texts = " ".join(a["text"] for a in ds["anomalies"])
    assert "Repeated ffmpeg failure (3x)" in texts
    assert "insufficient_scope" in texts
    assert "videos.insert" in texts                                    # active Google block
    assert any("wikipedia_search" in r for r in ds["recommendations"])
    assert ds["recommendations_note"].startswith("Recommendations are for operator review only")
    assert ds["health"] == "DEGRADED"                                  # 50% completion (< 80%) with a high-severity anomaly


# 11 ------------------------------------------------------------------------------------------------------
def test_read_only_enforcement(seeded, monkeypatch):
    svc, db = seeded
    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()
    svc.overview(days=7)
    svc.daily_summary("2026-11-20")
    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before
    conn = svc._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO persistent_jobs (id, date_str) VALUES ('x', 'y')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_not_exist (a)")
    conn.close()
    opened = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        opened.append((str(target), k.get("uri")))
        return real(target, *a, **k)
    monkeypatch.setattr(oi.sqlite3, "connect", spy)
    svc.overview(days=3)
    assert opened and all(uri and "mode=ro" in tgt for tgt, uri in opened)


def test_no_network_and_no_production_services(seeded, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")))
    src = Path(oi.__file__).read_text(encoding="utf-8")
    for forbidden in ("run_cycle_generation", "request_retry", "upload_short", "set_production_control_state",
                      "request_daemon_restart", "QuotaLedger(", "urlopen", "requests."):
        assert forbidden not in src, forbidden
    svc, _ = seeded
    svc.overview(days=7)


def test_cache_is_bounded_and_reused(seeded):
    svc, db = seeded
    svc.cache_ttl = 60
    calls = []
    orig = svc._load_bundle
    svc._load_bundle = lambda *a: (calls.append(1), orig(*a))[1]
    svc.overview(days=2)
    first = len(calls)          # the window, plus today's and the 7-day baseline for the daily summary
    assert first <= 3
    svc.overview(days=2)
    assert len(calls) == first                     # a refresh within the TTL re-reads nothing
    for d in range(1, 31):
        svc.bundle(d)
    assert len(svc._cache) <= 64


# 12 ------------------------------------------------------------------------------------------------------
def _dashboard_html():
    return (Path(oi.__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_has_ops_section_and_keeps_existing_tabs():
    html = _dashboard_html()
    for tab in ("overview", "schedule", "queue", "controls", "prodconfig", "processes", "ops"):
        assert f'data-tab="{tab}"' in html and f'id="tab-{tab}"' in html
    for el in ("opsHealth", "opsSuccess", "opsFailBody", "opsRenderBody", "opsTopicBody", "opsApiBody", "opsRecoveryBody",
               "opsSummary", "opsDailyChart", "opsHourlyChart", "opsDays"):
        assert f'id="{el}"' in html
    assert 'if (tabId === "ops") { refreshOps(); }' in html
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", html))
    names = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", js, flags=re.M)
    assert len(names) == len(set(names)), "duplicate dashboard function names"
    for existing in ("refreshQuota", "refreshProviders", "refreshJunk", "refreshDeployment", "switchTab", "fmtMinutes"):
        assert existing in names
    assert "/api/ops/intelligence?days=" in js
    # every server value is escaped before it reaches innerHTML in the ops renderer
    ops_js = js[js.index("// ---- Stage 3.32A"):js.index("refreshAll();\nrefreshDeployment();")]
    assert "p.template.slice(0, 110))" in ops_js and "escHtml(p.template" in ops_js


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_dashboard_script_parses():
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", _dashboard_html()))
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=js, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0 and res.stdout.strip() == "ok", res.stderr
