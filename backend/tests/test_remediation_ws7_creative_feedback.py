"""Remediation WS7: creative quality feedback loop and the deferred script retry.

Production evidence (171 evaluated videos): model-written scripts 89.7 overall vs extractive fallback 76.2
(storytelling 79 vs 25, hook 53 vs 24); 16 of the last 20 videos fell back because every Gemini call returned
429/503/504. Fake clients and temporary databases only; no network.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import backend.app.content.grounded_script_writer as gsw
from backend.app.core.execution_context import ExecutionContext
from backend.app.creative_intel import feedback as fb

sys.path.insert(0, str(Path(__file__).parent))
from test_gemini_writer_resilience import ScriptedClient, ServerError  # noqa: E402
from test_grounded_script_writer import GOOD, TOPIC  # noqa: E402


def _write(client, monkeypatch, deferred=150.0):
    sleeps = []
    monkeypatch.setattr(gsw.time, "sleep", lambda s: None)
    monkeypatch.setattr(gsw, "DEFERRED_RETRY_SEC", deferred)
    monkeypatch.setattr(gsw, "_deferred_sleep", lambda s: sleeps.append(s))
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup")
    out = gsw.write_grounded_script(TOPIC, "", job_id="j1", execution_context=ExecutionContext.VALIDATION,
                                    client=client, model="primary")
    return out, sleeps


def test_deferred_round_recovers_a_model_script(monkeypatch):
    busy = [ServerError(503, "UNAVAILABLE")] * 3
    c = ScriptedClient({"primary": busy + [{"sections": GOOD}], "backup": [ServerError(429, "RESOURCE_EXHAUSTED")] * 3})
    out, sleeps = _write(c, monkeypatch)
    assert out["script_source"] == "llm:primary" and sleeps == [150.0]
    marker = [a for a in out["writer_attempts"] if "deferred_retry_after_sec" in a]
    assert marker and any(a.get("round") == 2 and a.get("accepted") for a in out["writer_attempts"])


def test_only_one_deferred_round_then_fallback(monkeypatch):
    busy = [ServerError(503, "UNAVAILABLE")] * 3
    c = ScriptedClient({"primary": busy * 2, "backup": busy * 2})
    out, sleeps = _write(c, monkeypatch)
    assert out["script_source"] == "extractive_fallback" and sleeps == [150.0]
    assert len(c.calls) == 12                       # 2 rounds x 2 models x 3 calls, never more


def test_no_deferred_round_for_validation_failures_or_auth_errors(monkeypatch):
    c = ScriptedClient({"primary": [ServerError(401, "UNAUTHENTICATED")], "backup": []})
    out, sleeps = _write(c, monkeypatch)
    assert out["script_source"] == "extractive_fallback" and sleeps == []


def test_deferred_round_can_be_disabled(monkeypatch):
    busy = [ServerError(503, "UNAVAILABLE")] * 3
    c = ScriptedClient({"primary": busy, "backup": busy})
    out, sleeps = _write(c, monkeypatch, deferred=0)
    assert out["script_source"] == "extractive_fallback" and sleeps == [] and len(c.calls) == 6


# -- feedback report ---------------------------------------------------------------------------------------------

def _db(tmp_path, records, jobs=()):
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE creative_records (job_id TEXT, evaluated_at_utc TEXT, overall_score REAL,
                         decisions_json TEXT, evaluation_json TEXT);
                       CREATE TABLE persistent_jobs (id TEXT, status TEXT, error_details TEXT, created_at TEXT);""")
    for i, (src, overall, hook) in enumerate(records):
        c.execute("INSERT INTO creative_records VALUES (?,?,?,?,?)", (
            f"j{i}", f"2026-11-{10 + i // 24:02d}T{i % 24:02d}:00:00Z", overall, json.dumps({"script_source": src}),
            json.dumps({"dimensions": {"hook": {"score": hook}, "storytelling": {"score": overall - 10}},
                        "weakest": ["hook", "storytelling"]})))
    for jid, status, err in jobs:
        c.execute("INSERT INTO persistent_jobs VALUES (?,?,?,datetime('now'))", (jid, status, err))
    c.commit()
    c.close()
    return str(db)


def test_report_separates_sources_and_needs_enough_videos(tmp_path):
    recs = [("llm:gemini-3.8-flash", 90, 55)] * 25 + [("extractive_fallback", 76, 24)] * 25
    r = fb.feedback_report(_db(tmp_path, recs))
    assert r["by_script_source"]["model"]["overall"] == 90 and r["by_script_source"]["extractive_fallback"]["n"] == 25
    assert r["script_source_effect"]["hook"] == 31
    assert r["trend"]["overall"]["verdict"] == "declining"                 # last 24 mostly fallback vs model before
    assert r["recurring_weaknesses"]["hook"] == 1.0


def test_small_samples_are_never_reported_as_improvement(tmp_path):
    recs = [("extractive_fallback", 70, 20)] * 10 + [("llm:x", 95, 60)] * 10
    r = fb.feedback_report(_db(tmp_path, recs))
    assert r["trend"]["overall"]["verdict"] == "insufficient evidence"


def test_failures_are_split_creative_vs_infrastructure(tmp_path):
    jobs = [("cycle_1", "BLOCKED_QC", 'BLOCKED_QC: ["[CHECK_A_TOPIC_NARRATION] Narration lacks topic keywords"]'),
            ("cycle_2", "RECOVERY_WINDOW_EXPIRED", "Recovery window expired"),
            ("cycle_3", "FAILED", "ReadTimeout: network timeout"),
            ("test_x", "FAILED", "ignored")]
    r = fb.feedback_report(_db(tmp_path, [("llm:x", 90, 50)], jobs))
    assert r["failures"]["creative_quality"] == {"CHECK_A_TOPIC_NARRATION": 1}
    assert sum(r["failures"]["infrastructure"].values()) == 2


def test_proposals_require_operator_for_policy_changes(tmp_path):
    recs = [("llm:x", 90, 55)] * 25 + [("extractive_fallback", 76, 24)] * 25
    r = fb.feedback_report(_db(tmp_path, recs))
    ids = {p["id"]: p for p in r["proposals"]}
    assert ids["script_model_availability"]["requires_operator"] is False
    assert ids["weak_hook"]["requires_operator"] is True


def test_interventions_are_recorded_once_and_measured(tmp_path):
    recs = [("extractive_fallback", 76, 24)] * 30 + [("llm:x", 90, 55)] * 30
    db = _db(tmp_path, recs)
    from datetime import datetime, timezone
    assert fb.record_interventions(db, now=datetime(2026, 11, 11, 6, tzinfo=timezone.utc)) == 1
    assert fb.record_interventions(db) == 0                                  # idempotent
    iv = fb.feedback_report(db)["interventions"][0]
    assert iv["id"] == "ws7_deferred_script_retry" and iv["metric"] == "model_share"
    assert iv["before"]["n"] >= 20 and iv["after"]["n"] >= 20 and iv["verdict"] == "improving"


def test_report_without_records(tmp_path):
    assert fb.feedback_report(_db(tmp_path, []))["available"] is False


def test_api_route_is_local_only():
    from fastapi.testclient import TestClient
    from backend.app import main
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/creative-intel/feedback").status_code in (400, 403)
