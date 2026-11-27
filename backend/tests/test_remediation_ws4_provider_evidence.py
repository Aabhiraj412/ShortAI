"""Remediation WS4: provider status reflects what production actually observed.

Configured is not healthy, and healthy is not used. Pure functions plus a temporary database; no provider is
ever called.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from backend.app.services import provider_audit as pa


def row(key, status="NO_KEY_NEEDED", ok=0, rendered_probe=None):
    return {"key": key, "status": status, "recent_health": ({"ok": ok, "failed": 0} if ok else None),
            "probe": rendered_probe, "rate_limited": False}


def test_gemini_mostly_fallback_is_degraded_not_configured():
    ev = {"scripts_last_n": 20, "written_by_model": 4, "extractive_fallback": 16, "model_share": 0.2}
    state, why = pa.state_with_evidence(row("gemini", status="UNVERIFIED"), ev, None)
    assert state == "DEGRADED" and "16 used the extractive fallback" in why


def test_gemini_never_used_is_degraded_and_healthy_when_mostly_model():
    assert pa.state_with_evidence(row("gemini", "UNVERIFIED"), {"scripts_last_n": 5, "written_by_model": 0,
                                                               "extractive_fallback": 5, "model_share": 0.0}, None)[0] == "DEGRADED"
    assert pa.state_with_evidence(row("gemini", "UNVERIFIED"), {"scripts_last_n": 10, "written_by_model": 8,
                                                               "extractive_fallback": 2, "model_share": 0.8}, None)[0] == "HEALTHY"


def test_missing_key_still_wins_over_evidence():
    ev = {"scripts_last_n": 10, "written_by_model": 8, "extractive_fallback": 2, "model_share": 0.8}
    assert pa.state_with_evidence(row("gemini", status=pa.KEY_MISSING), ev, None)[0] == "KEY_MISSING"


def test_reachable_but_never_selected_is_explained():
    state, why = pa.state_with_evidence(row("internet_archive", ok=17), {}, 0)
    assert state == "AVAILABLE_NOT_SELECTED" and "17 successful requests" in why


def test_rendered_assets_prove_a_provider_works():
    state, why = pa.state_with_evidence(row("nasa"), {}, 1)
    assert state == "HEALTHY" and "1 of its assets" in why


def test_tts_and_discovery_and_smtp_from_production_records():
    assert pa.state_with_evidence(row("edge_tts"), {"productions_voiced_24h": 19}, None)[0] == "HEALTHY"
    assert pa.state_with_evidence(row("edge_tts"), {"productions_voiced_24h": 0}, None)[0] == "CONFIGURED"
    disc = {"topic_discovery_last_runs": {"candidates_created": 7, "accepted": 6, "errors": 0, "runs": 24}}
    assert pa.state_with_evidence(row("gnews", "UNVERIFIED"), disc, None)[0] == "HEALTHY"
    bad = {"topic_discovery_last_runs": {"candidates_created": 0, "accepted": 0, "errors": 5, "runs": 24}}
    assert pa.state_with_evidence(row("gnews", "UNVERIFIED"), bad, None)[0] == "DEGRADED"
    assert pa.state_with_evidence(row("smtp", "UNVERIFIED"), {"notifications_24h": {"SENT": 21}}, None)[0] == "HEALTHY"
    assert pa.state_with_evidence(row("smtp", "UNVERIFIED"), {"notifications_24h": {"SENT": 3, "FAILED": 2}}, None)[0] == "DEGRADED"


def test_no_evidence_says_so():
    state, why = pa.state_with_evidence(row("gnews", "UNVERIFIED"), {}, None)
    assert state == "CONFIGURED" and "no production evidence" in why


def test_production_evidence_reads_records(tmp_path, monkeypatch):
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE creative_records (job_id TEXT, evaluated_at_utc TEXT, decisions_json TEXT);
      CREATE TABLE persistent_jobs (id TEXT, status TEXT, updated_at TEXT);
      CREATE TABLE topic_discovery_runs (run_id TEXT, started_at TEXT, provider_stats_json TEXT);
      CREATE TABLE persistent_notifications (id INTEGER, status TEXT, created_at TEXT);
    """)
    for i, s in enumerate(["llm:gemini-3.8-flash", "extractive_fallback", "extractive_fallback", "extractive_fallback"]):
        c.execute("INSERT INTO creative_records VALUES (?,?,?)", (f"j{i}", f"2026-11-26T0{i}:00:00Z",
                                                                json.dumps({"script_source": s})))
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO persistent_jobs VALUES ('cycle_1_x','COMPLETED',?)", (now,))
    c.execute("INSERT INTO persistent_jobs VALUES ('test_x','COMPLETED',?)", (now,))
    c.execute("INSERT INTO topic_discovery_runs VALUES ('r1','2026-11-26',?)", (json.dumps({
        "gnews": {"candidates_created": 3, "accepted": 2}, "nasa_library": {"candidates_created": 4, "accepted": 0},
        "physorg_rss": {"candidates_created": 10, "accepted": 1}, "wikipedia_search": {"candidates_created": 5, "accepted": 1}}),))
    c.execute("INSERT INTO persistent_notifications VALUES (1,'SENT',?)", (now,))
    c.commit()
    c.close()
    import backend.app.core.paths as paths
    monkeypatch.setattr(paths, "DATABASE_PATH", db)
    ev = pa.production_evidence()
    assert ev["gemini"] == {"scripts_last_n": 4, "written_by_model": 1, "extractive_fallback": 3, "model_share": 0.25}
    assert ev["edge_tts"]["productions_voiced_24h"] == 1                     # test jobs excluded
    assert ev["nasa"]["topic_discovery_last_runs"]["accepted"] == 0
    assert ev["wikipedia"]["topic_discovery_last_runs"]["candidates_created"] == 15
    assert ev["smtp"]["notifications_24h"] == {"SENT": 1}


def test_summary_never_exposes_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-not-a-real-key-123456")
    text = json.dumps(pa.summary())
    assert "sk-test-not-a-real-key-123456" not in text
