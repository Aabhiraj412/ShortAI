"""Stage 3.32C recommendation engine.

Everything runs on temporary SQLite files, a fixed / mutable clock and a fake Operational Intelligence service
(plus one end-to-end test through the real Stage 3.32A service on a seeded temporary database). No production
database, network, Gemini or YouTube call is made.
"""
import hashlib
import json
import re
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.app.autonomy.control_plane as cpm
from backend.app.autonomy.control_plane import Conflict, ControlPlane, PermissionDenied
from backend.app.autonomy.recommendations import engine as eng_mod
from backend.app.autonomy.recommendations import rules, scoring, signals
from backend.app.autonomy.recommendations.engine import GeminiExplainer, RecommendationEngine
from backend.app.autonomy.recommendations.store import RecommendationStore

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
OP = "abhi"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def sp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def iso(dt):
    return dt.isoformat()


def job(n, status, created, minutes=None, stage="COMPLETED", retry=0, day="20261117", hhmm="0000"):
    done = created + timedelta(minutes=minutes) if minutes is not None else None
    return {"id": f"cycle_{n}_{day}_{hhmm}", "status": status, "current_stage": stage, "retry_count": retry,
            "error_details": None, "created_at": sp(created), "updated_at": sp(done or created),
            "completed_at": sp(done) if done and status == "COMPLETED" else None, "actual_start_at": sp(created),
            "recovery_reason": None}


def rich_data(now=NOW):
    """A data set in which every rule has something to find (modelled on the live 2026-11-20 data)."""
    base = now - timedelta(days=3)
    jobs = [job(i % 6 + 1, "COMPLETED", base + timedelta(hours=i), 12, hhmm=f"{i:02d}00") for i in range(12)]
    jobs += [job(1, "COMPLETED", base + timedelta(hours=13), 120, hhmm="1300"), job(3, "COMPLETED", base + timedelta(hours=14), 130, hhmm="1400"),
             job(4, "COMPLETED", base + timedelta(hours=15), 140, hhmm="1500")]
    jobs += [job(5, "COMPLETED", base + timedelta(hours=16 + i), 40, hhmm=f"{16 + i:02d}00") for i in range(3)]
    jobs += [job(2, "RECOVERY_WINDOW_EXPIRED", base + timedelta(hours=20 + i), stage="NOT_RECORDED", day=f"2026111{7 + i}") for i in range(3)]
    jobs += [job(6, "RECOVERY_WINDOW_EXPIRED", base + timedelta(hours=24 + i), stage="NOT_RECORDED", day=f"2026111{7 + i}") for i in range(2)]
    jobs += [job(7, "FAILED", now - timedelta(hours=2 + i), stage="THUMBNAIL", retry=2, day="20261120", hhmm=f"0{i}00") for i in range(4)]
    jobs += [job(8, "COMPLETED", now - timedelta(hours=1), 10, day="20261120", hhmm="0500")]
    pad = "FFmpeg Priority B adaptation failed: [Parsed_pad_<n> <id>] Padded dimensions cannot be smaller than input dimensions."
    patterns = [
        {"cause": "ffmpeg", "template": pad, "class": "TRANSIENT", "classes": ["TRANSIENT"], "count": 3,
         "jobs": ["cycle_1_20261118_0100", "cycle_3_20261118_0300", "cycle_4_20261119_0400"], "first_seen": iso(now - timedelta(days=2)), "last_seen": iso(now - timedelta(hours=3))},
        {"cause": "file_lock", "template": "[WinError <n>] The process cannot access the file because it is being used by another process: <s>",
         "class": "PERMANENT", "classes": ["TRANSIENT", "PERMANENT"], "count": 2, "jobs": ["cycle_2_20261118_0200"],
         "first_seen": iso(now - timedelta(days=2)), "last_seen": iso(now - timedelta(days=1))},
        {"cause": "qc", "template": "QC Failure: Duration <n>s is outside Shorts <n>-<n>s window.", "class": "DETERMINISTIC",
         "classes": ["DETERMINISTIC"], "count": 6, "jobs": ["cycle_7_20261120_0000"], "first_seen": iso(now - timedelta(days=3)), "last_seen": iso(now - timedelta(hours=2))},
        {"cause": "ffmpeg", "template": "single ffmpeg glitch", "class": "TRANSIENT", "classes": ["TRANSIENT"], "count": 1, "jobs": ["x"]},
    ]
    failures = ([{"job_id": f"cycle_9_2026111{7 + i}_0900", "at": iso(now - timedelta(days=i)), "cause": "topic_exhaustion",
                  "template": "integrity gate: every discovered topic was already selected", "message": "", "stage": "TOPIC_DISCOVERY"} for i in range(3)]
                + [{"job_id": f"cycle_10_2026111{8 + i}_1000", "at": iso(now - timedelta(hours=5 + i)), "cause": "network",
                    "template": "upstream returned <n>", "message": "HTTP Error 504: Gateway Time-out", "stage": "TOPIC_DISCOVERY"} for i in range(2)])
    prov = {"physorg_rss": {"queries": 30, "articles": 900, "candidates_created": 90, "accepted": 19, "rejected": 71, "duplicates": 10},
            "wikipedia_search": {"queries": 24, "articles": 100, "candidates_created": 40, "accepted": 2, "rejected": 38, "duplicates": 5},
            "nasa_library": {"queries": 6, "articles": 24, "candidates_created": 18, "accepted": 0, "rejected": 18, "duplicates": 0},
            "wikipedia_featured": {"queries": 6, "articles": 320, "candidates_created": 0, "accepted": 0, "rejected": 0, "duplicates": 0}}
    latest = {"wikipedia_search": {"queries": 4, "articles": 24, "candidates_created": 24, "accepted": 0, "rejected": 24},
              "physorg_rss": {"queries": 5, "articles": 150, "candidates_created": 15, "accepted": 3, "rejected": 12}}
    runs = [{"run_id": "cycle_6_20261120_0500", "job_id": "cycle_6_20261120_0500", "provider_stats_json": json.dumps(prov)},
            {"run_id": "cycle_11_20261120_1000", "job_id": "cycle_11_20261120_1000", "provider_stats_json": json.dumps(latest)}]
    productions = [{"id": f"cycle_{i}_x", "stages": {"video_rendering": {"status": "completed", "started_at": iso(now - timedelta(hours=i + 10)),
                                                                       "completed_at": iso(now - timedelta(hours=i + 10) + timedelta(seconds=200 if i < 6 else 900))}}}
                   for i in range(8)]
    registry = [{"category": "Science" if i < 7 else "History", "status": "PRODUCED"} for i in range(9)]
    sections = {
        "production_health": {"status": "actual", "metrics": {"recovery_window_expired": 30, "finished": 80,
                              "stage_failure_distribution": {"NOT_RECORDED": 20, "THUMBNAIL": 6, "VOICEOVER": 2}}},
        "failure_intelligence": {"status": "actual", "metrics": {"patterns": patterns}},
        "renderer_reliability": {"status": "actual", "metrics": {"media_acquisitions_failed": 15, "media_unique_images": 100}},
        "topic_intelligence": {"status": "actual", "metrics": {
            "acceptance_pct": 10.7, "rejection_reasons": {"INSUFFICIENT_EVIDENCE": 120, "RECENTLY_USED": 30, "LOW_RELEVANCE": 20},
            "provider_contribution": prov, "classifier_v1_vs_v2": {"agree": 1, "disagree": 11, "unknown": 1},
            "classifier_disagreement_pct": 92.3, "classifier_disagreements": [{"candidate_id": "tc_1", "v1": "Science", "v2": "Space"}]}},
        "api_intelligence": {"status": "actual", "metrics": {
            "youtube_errors": {"videos.update:insufficient_scope": 2, "videos.list:backend_error": 3},
            "youtube_quota_today_pacific": {"videos.insert": {"label": "Uploads", "used_estimate": 6, "daily_limit_default": 100,
                                                               "used_pct_estimate": 6.0, "google_block_active": True},
                                            "general": {"label": "General", "used_estimate": 500, "daily_limit_default": 10000,
                                                        "used_pct_estimate": 5.0, "google_block_active": False}},
            "youtube_daily_units": {"2026-11-16": {"general": 100}, "2026-11-17": {"general": 120}, "2026-11-18": {"general": 110},
                                    "2026-11-19": {"general": 900}},
            "research_provider_requests": {"gnews": {"queries": 10, "errors": 5}, "physorg_rss": {"queries": 30, "errors": 0}},
            "scripts_by_source": {"extractive_fallback": 6, "llm:gemini": 2}}},
        "recovery_analytics": {"status": "actual", "metrics": {"jobs_needing_recovery": 10, "failed_after_recovery": 6, "recovered": 4,
                               "median_time_to_recover_minutes": 900, "recovered_within_60_min": 1, "rearm_events": 3,
                               "retry_count_distribution": {"0": 20, "2": 4}}},
    }
    bundle = {"db_available": True, "jobs": jobs, "runs": runs, "productions": productions, "registry": registry}
    return sections, bundle, failures


class FakeIntelligence:
    def __init__(self, data=None, fail=()):
        self.sections, self.b, self.failures = data or rich_data()
        self.fail = set(fail)
        self.calls = 0

    def __getattr__(self, name):
        if name in signals.SECTION_NAMES:
            def section(days=7):
                self.calls += 1
                if name in self.fail:
                    raise RuntimeError("section broken")
                return self.sections[name]
            return section
        raise AttributeError(name)

    def bundle(self, days=7):
        return self.b

    def _failures(self, b):
        return self.failures


RECOVERY = {f"cycle_{i}": 45 for i in range(1, 25)}


def make(tmp_path, *, level="RECOMMEND", startup=True, intel=None, clock=None, environ=None, explainer=None, name="studio.db"):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    db = tmp_path / name
    if not db.exists():
        sqlite3.connect(db).close()
    if startup:
        (data / "autonomy_startup.json").write_text('{"autonomy_execution": "enabled"}', encoding="utf-8")
    intel = intel or FakeIntelligence()
    clock = clock or Clock()
    cp = ControlPlane(str(db), str(data), environ={}, intelligence=lambda: intel, clock=clock)
    if level:
        cp.set_level("global", level, OP, "test")
        cp.set_level("recommendations", level, OP, "test")
    e = RecommendationEngine(cp, schedule_provider=lambda: RECOVERY, environ=environ or {}, explainer=explainer, clock=clock)
    return cp, e, intel, clock


def sig(data=None, now=NOW, recovery=RECOVERY):
    return signals.collect(FakeIntelligence(data), days=7, now=now, schedule_provider=lambda: recovery)


def found(data=None):
    return {f.rule_id: f for f in rules.evaluate(sig(data)).findings} | {f.key: f for f in rules.evaluate(sig(data)).findings}


# -- 1. categories and detection ---------------------------------------------------------------------------------
def test_production_reliability_rules():
    f = found()
    exp = f["prod.recovery_window_expirations"]
    assert exp.category == "production_reliability" and exp.severity == "critical" and exp.occurrences == 30   # 37.5% ≥ 35%
    assert exp.hypothesis_strength == "WEAK" and "cannot be attributed" in exp.hypothesis       # stage mostly not recorded
    assert "Do not change recovery deadlines automatically" in exp.action
    assert f["prod.repeated_stage_failure:THUMBNAIL"].severity == "high"
    rising = f["prod.failure_rate_rising"]
    assert rising.occurrences == 4 and "last 24 h" in rising.description
    assert f["prod.abnormal_duration"].occurrences == 3
    near = f["prod.approaching_deadline"]
    assert near.occurrences >= 3 and "nothing is changed automatically" in near.action


def test_renderer_rules_distinguish_deterministic_from_transient():
    f = found()
    pad = next(x for x in f.values() if x.rule_id == "render.failure_pattern" and "padding" in x.title.lower())
    assert pad.hypothesis_strength == "CONFIRMED" and "deterministic" in pad.hypothesis and "not transient" in pad.hypothesis
    assert "Priority B adaptation dimension calculation" in pad.action
    assert "classifies it as TRANSIENT" in pad.description and "not changed automatically" in pad.description
    lock = next(x for x in f.values() if x.rule_id == "render.failure_pattern" and "file-locking" in x.title)
    assert lock.hypothesis_strength == "WEAK" and "transient" in lock.hypothesis
    assert not any(x.rule_id == "render.failure_pattern" and "glitch" in x.description for x in f.values())   # count 1: below threshold
    assert f["render.abnormal_duration"].occurrences == 2
    assert f["render.media_acquisition"].occurrences == 15


def test_topic_rules():
    f = found()
    assert f["topic.exhaustion"].severity == "high" and f["topic.exhaustion"].occurrences == 3
    assert f["topic.low_acceptance"].occurrences == 170
    assert f["topic.dominant_rejection_reason:INSUFFICIENT_EVIDENCE"].hypothesis_strength == "STRONG"
    conc = f["topic.provider_concentration:physorg_rss"]
    assert "19 of 21" in conc.description
    latest = f["topic.latest_run_rejections:wikipedia_search"]
    assert latest.title == "All 24 wikipedia_search candidates were rejected in the latest run"
    assert "physorg_rss" in latest.description                                         # concentration context from data
    assert f["topic.unproductive_provider:nasa_library"].supersedes == ["topic.unproductive_queries:nasa_library"]
    assert "topic.unproductive_queries:wikipedia_featured" in f
    assert f["topic.classifier_disagreement"].hypothesis_strength == "WEAK" and "stays in shadow" in f["topic.classifier_disagreement"].action
    dup = f["topic.duplicate_detection"]
    assert dup.hypothesis_strength == "WEAK" and "Neither check is changed or bypassed" in dup.action


def test_api_rules_do_not_invent_quota():
    f = found()
    scope = f["api.youtube_errors:videos.update:insufficient_scope"]
    assert scope.hypothesis_strength == "CONFIRMED" and scope.risk == "HIGH"
    assert "OAuth scopes are not changed automatically" in scope.action
    assert f["api.youtube_errors:videos.list:backend_error"].hypothesis_strength == "WEAK"
    block = f["api.quota_pressure:videos.insert"]
    assert block.hypothesis_strength == "CONFIRMED" and "explicit decision" in block.action
    assert "api.quota_pressure:general" not in f                                          # 5% estimate: no anomaly
    assert f["api.request_volume"].occurrences == 900
    assert f["api.provider_errors:gnews"].occurrences == 5
    assert f["api.http_504"].occurrences == 2


def test_recovery_and_content_rules():
    f = found()
    w = f["recovery.window_consumed"]
    assert w.subject == "by_cycle" and "cycle_2 (3×)" in w.description and "cycle_6 (2×)" in w.description   # one finding, not one per cycle
    assert sum(1 for x in rules.evaluate(sig()).findings if x.rule_id == "recovery.window_consumed") == 1
    assert f["recovery.ineffective_retries"].occurrences == 6
    assert f["recovery.long_duration"].hypothesis_strength == "STRONG"                      # re-arms explain it
    qc = next(x for x in f.values() if x.rule_id == "quality.qc_failures")
    assert "duration" in qc.title.lower() and "QC stays as it is" in qc.action
    assert f["quality.script_fallback"].occurrences == 6
    assert f["quality.repeated_creative_pattern:Science"].occurrences == 7
    insufficient = {i["rule_id"] for i in rules.evaluate(sig()).insufficient}
    assert "quality.engagement_and_hooks" in insufficient                                    # never invents analytics


# -- 2. missing data and insufficient evidence ---------------------------------------------------------------------
def test_missing_historical_data_produces_no_findings():
    empty = ({n: {"status": "unavailable", "metrics": {}, "unavailable": {"all": "database unavailable"}} for n in signals.SECTION_NAMES},
             {"db_available": False, "jobs": [], "runs": [], "productions": [], "registry": []}, [])
    out = rules.evaluate(sig(empty, recovery={}))
    assert out.findings == []
    reasons = {i["rule_id"] for i in out.insufficient}
    assert {"prod.recovery_window_expirations", "topic.low_acceptance", "api.youtube_errors", "prod.approaching_deadline"} <= reasons


def test_insufficient_evidence_thresholds():
    sections, bundle, failures = rich_data()
    sections["production_health"]["metrics"].update(recovery_window_expired=3, finished=8)
    sections["topic_intelligence"]["metrics"]["rejection_reasons"] = {"LOW_RELEVANCE": 5}
    bundle["jobs"] = bundle["jobs"][:3]
    out = rules.evaluate(sig((sections, bundle, failures)))
    ids = {f.rule_id for f in out.findings}
    assert not ids & {"prod.recovery_window_expirations", "topic.low_acceptance", "prod.abnormal_duration", "prod.failure_rate_rising"}
    skipped = {i["rule_id"]: i["reason"] for i in out.insufficient}
    assert "≥10 finished" in skipped["prod.recovery_window_expirations"] and "≥30" in skipped["topic.low_acceptance"]


def test_broken_section_or_rule_never_stops_others(monkeypatch):
    s = signals.collect(FakeIntelligence(fail={"topic_intelligence"}), days=7, now=NOW, schedule_provider=lambda: RECOVERY)
    assert s.status("topic_intelligence") == "unavailable" and "topic_intelligence" in s.errors
    monkeypatch.setattr(rules, "RULES", (lambda sig, out: 1 / 0,) + rules.RULES)
    out = rules.evaluate(s)
    assert any("rule error" in i["reason"] for i in out.insufficient)
    assert any(f.rule_id == "prod.recovery_window_expirations" for f in out.findings)


# -- 3. root-cause grouping through the real Stage 3.32A service -----------------------------------------------------
def test_root_cause_grouping_with_real_operational_intelligence(tmp_path):
    from backend.app.services.operational_intelligence import OperationalIntelligence
    from test_operational_intelligence_332a import add_event, add_job, make_db
    db = str(tmp_path / "studio.db")
    conn = make_db(db)
    t0 = NOW - timedelta(hours=10)
    for i, addr in enumerate(("0000021a5c", "00000ff3b1", "000007e2aa")):
        jid = f"cycle_{i + 1}_20261120_0{i}00"
        add_job(conn, jid, "FAILED", t0 + timedelta(hours=i), stage="VIDEO_RENDERING",
                error=f"[TRANSIENT] FFmpeg Priority B adaptation failed: [Parsed_pad_{i} @ {addr}] Padded dimensions cannot be smaller than input dimensions")
        add_event(conn, t0 + timedelta(hours=i), jid, "CYCLE_DISPATCH_FAILED",
                  f"FFmpeg Priority B adaptation failed: [Parsed_pad_{i} @ {addr}] Padded dimensions cannot be smaller than input dimensions")
    conn.commit()
    conn.close()
    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()
    oi = OperationalIntelligence(db_path=db, data_dir=str(tmp_path / "nodata"), clock=lambda: NOW)
    s = signals.collect(oi, days=7, now=NOW, schedule_provider=lambda: {})
    pads = [f for f in rules.evaluate(s).findings if f.rule_id == "render.failure_pattern"]
    assert len(pads) == 1 and pads[0].occurrences == 3 and pads[0].hypothesis_strength == "CONFIRMED"   # 3 messages, one root cause
    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before                                  # analysis is read-only


# -- 4. confidence and priority ----------------------------------------------------------------------------------
def _finding(**kw):
    base = dict(rule_id="t.rule", category="renderer", subject="s", title="t", description="d", problem="p", evidence=[],
                hypothesis="h", hypothesis_strength="STRONG", action="a", benefit="b")
    base.update(kw)
    return rules.Finding(**base)


def test_confidence_scoring_is_explainable():
    c = lambda **kw: scoring.confidence(_finding(**kw))
    confirmed, strong, weak = (c(hypothesis_strength=s, sample_size=10, min_sample=2) for s in ("CONFIRMED", "STRONG", "WEAK"))
    assert confirmed["score"] > strong["score"] > weak["score"]
    assert confirmed["evidence_type"] == "Confirmed observation" and weak["evidence_type"] == "Weak hypothesis"
    assert c(sample_size=2, min_sample=2)["score"] < c(sample_size=8, min_sample=2)["score"]                 # more evidence
    assert c(completeness=0.8)["score"] < c(completeness=1.0)["score"]
    assert c(consistency=0.1)["score"] < c(consistency=1.0)["score"]
    full = c(hypothesis_strength="CONFIRMED", sample_size=10, min_sample=1)
    assert full["label"] == "HIGH" and set(full["components"]) == {"strength_base", "evidence_factor", "consistency_factor", "completeness"}
    assert c(hypothesis_strength="WEAK", sample_size=1, min_sample=5)["label"] == "LOW"


def test_priority_scoring():
    def p(**kw):
        f = _finding(**kw)
        return scoring.priority(f, scoring.confidence(f), NOW, 7)
    crit = p(severity="critical", impact=1.0, occurrences=30, last_seen=iso(NOW - timedelta(hours=1)), affected_jobs=[str(i) for i in range(12)],
             hypothesis_strength="CONFIRMED", sample_size=10)
    low = p(severity="low", impact=0.1, occurrences=1, last_seen=iso(NOW - timedelta(days=6)))
    assert crit["label"] == "CRITICAL" and crit["score"] >= 80 and low["label"] == "LOW"
    assert p(last_seen=iso(NOW - timedelta(hours=1)))["components"]["recency"] == 1.0
    assert p(last_seen=iso(NOW - timedelta(days=6)))["components"]["recency"] < 0.5
    assert p(last_seen=None)["components"]["recency"] == 0.5
    assert p(severity="high")["score"] > p(severity="medium")["score"]
    comp = crit["components"]
    assert crit["score"] == round(sum(scoring.WEIGHTS[k] * comp[k] for k in comp), 1)


# -- 5. persistence: deduplication, expiry, supersession -------------------------------------------------------------
def test_deduplication_updates_instead_of_duplicating(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    first = e.run(OP, force=True)["run"]
    ids = {r["dedupe_key"]: r["id"] for r in e.store.list(status="open")}
    clock.advance(minutes=10)
    second = e.run(OP, force=True)["run"]
    assert first["created"] == first["findings"] and second["created"] == 0 and second["updated"] == first["findings"]
    after = e.store.list(status="open")
    assert {r["dedupe_key"]: r["id"] for r in after} == ids and all(r["detections"] == 2 for r in after)
    conn = sqlite3.connect(cp.db_path)
    key = after[0]["dedupe_key"]
    with pytest.raises(sqlite3.IntegrityError):                                         # one open row per issue, enforced by the DB
        conn.execute("INSERT INTO autonomy_recommendations SELECT 'rec_ffffffffffffffff', dedupe_key, rule_id, category, subject, title, "
                     "description, problem, evidence_json, observations_json, hypothesis, hypothesis_strength, action, benefit, risk, severity, "
                     "confidence, confidence_label, priority, priority_label, scoring_json, related_json, occurrences, detections, "
                     "first_detected_at, last_detected_at, created_at, updated_at, expires_at, status, status_by, status_at, status_reason, "
                     "resolution_evidence, superseded_by, previous_id, explanation, explanation_fingerprint, last_run_id "
                     "FROM autonomy_recommendations WHERE dedupe_key=?", (key,))
    conn.close()


def test_expiry_and_reappearance(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    rid = next(r["id"] for r in e.store.list(status="open") if r["rule_id"] == "api.http_504")
    intel.failures = [f for f in intel.failures if "504" not in f.get("message", "")]           # the issue disappears
    clock.advance(hours=80)                                                                        # beyond the 72 h ttl
    run = e.run(OP, force=True)["run"]
    assert run["expired"] >= 1 and e.store.get(rid)["status"] == "EXPIRED"
    assert "RECOMMENDATION_EXPIRED" in [x["event_type"] for x in cp.audit_events(request_id=rid)]
    intel.failures = rich_data()[2]                                                                # it comes back
    clock.advance(minutes=10)
    e.run(OP, force=True)
    again = next(r for r in e.store.list(status="open") if r["rule_id"] == "api.http_504")
    assert again["id"] != rid and again["previous_id"] == rid and rid in again["observations"][0]


def test_supersession(tmp_path):
    sections, bundle, failures = rich_data()
    prov = sections["topic_intelligence"]["metrics"]["provider_contribution"]
    prov["nasa_library"].update(candidates_created=0, articles=24)                                  # queries but no candidates
    intel = FakeIntelligence((sections, bundle, failures))
    cp, e, _, clock = make(tmp_path, intel=intel)
    e.run(OP, force=True)
    old = next(r for r in e.store.list(status="open") if r["dedupe_key"] == "topic.unproductive_queries:nasa_library")
    prov["nasa_library"].update(candidates_created=18)                                              # now creates candidates, none accepted
    clock.advance(minutes=10)
    e.run(OP, force=True)
    assert e.store.get(old["id"])["status"] == "SUPERSEDED"
    new = next(r for r in e.store.list(status="open") if r["dedupe_key"] == "topic.unproductive_provider:nasa_library")
    assert e.store.get(old["id"])["superseded_by"] == new["id"]


# -- 6. status transitions -------------------------------------------------------------------------------------------
def test_status_transitions(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    recs = e.store.list(status="open")
    a, b, c = recs[0]["id"], recs[1]["id"], recs[2]["id"]
    assert e.set_status(a, "ACKNOWLEDGED", OP)["recommendation"]["status"] == "ACKNOWLEDGED"
    assert e.set_status(a, "APPROVED", OP, reason="agreed")["recommendation"]["status"] == "APPROVED"
    with pytest.raises(cpm.ControlPlaneError):
        e.set_status(a, "RESOLVED", OP)                                                            # evidence required
    done = e.set_status(a, "RESOLVED", OP, evidence="fixed in commit abc")["recommendation"]
    assert done["status"] == "RESOLVED" and done["resolution_evidence"] == "fixed in commit abc" and not done["open"]
    with pytest.raises(Conflict):
        e.set_status(a, "ACKNOWLEDGED", OP)                                                        # terminal
    with pytest.raises(cpm.ControlPlaneError):
        e.set_status(b, "REJECTED", OP)                                                            # reason required
    assert e.set_status(b, "REJECTED", OP, reason="not relevant")["recommendation"]["status_reason"] == "not relevant"
    with pytest.raises(cpm.ControlPlaneError):
        e.set_status(c, "EXPIRED", OP)                                                             # system-only states
    assert e.set_status(c, "ACKNOWLEDGED", OP)["unchanged"] is False
    assert e.set_status(c, "ACKNOWLEDGED", OP)["unchanged"] is True
    events = [x["event_type"] for x in reversed(cp.audit_events(request_id=a))]
    assert events[-3:] == ["RECOMMENDATION_ACKNOWLEDGED", "RECOMMENDATION_APPROVED", "RECOMMENDATION_RESOLVED"]
    # The engine (or any reserved identity) can never change a status, so it cannot approve its own work.
    for actor in ("recommendation-engine", "autonomy-engine", "system", "engine"):
        with pytest.raises(PermissionDenied):
            e.set_status(c, "APPROVED", actor)


def test_rejected_recommendation_is_suppressed_for_seven_days(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    rec = next(r for r in e.store.list(status="open") if r["rule_id"] == "api.http_504")
    e.set_status(rec["id"], "REJECTED", OP, reason="known issue upstream")
    clock.advance(hours=1)
    r = e.run(OP, force=True)["run"]
    assert r["suppressed"] == 1 and not any(x["rule_id"] == "api.http_504" for x in e.store.list(status="open"))
    clock.advance(days=8)
    e.run(OP, force=True)
    again = next(x for x in e.store.list(status="open") if x["rule_id"] == "api.http_504")
    assert again["previous_id"] == rec["id"]


def test_recommendation_approval_executes_nothing(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    for r in e.store.list(status="open"):
        e.set_status(r["id"], "APPROVED", OP)
    assert cp.list_requests() == [] and cp.list_executions() == []
    assert not (cp.autonomy_dir / "reports").exists() and not (cp.autonomy_dir / "operator_inbox.jsonl").exists()


# -- 7. control plane and kill switches -----------------------------------------------------------------------------
def test_level_off_does_nothing(tmp_path):
    cp, e, intel, clock = make(tmp_path, level=None)
    out = e.run(OP, force=True)
    assert out["ran"] is False and out["mode"] == "OFF" and intel.calls == 0
    tables = {r[0] for r in sqlite3.connect(cp.db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "autonomy_recommendations" not in tables and "autonomy_recommendation_runs" not in tables


def test_level_observe_records_observations_only(tmp_path):
    cp, e, intel, clock = make(tmp_path, level="OBSERVE")
    out = e.run(OP, force=True)
    assert out["ran"] and out["mode"] == "OBSERVE" and out["findings"]
    assert e.store.list(status="all") == []
    obs = [x for x in cp.audit_events() if x["event_type"] == "OBSERVATION_RECORDED"]
    assert obs and "not stored" in obs[0]["reason"]


def test_global_level_caps_category(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    cp.set_level("global", "OBSERVE", OP, "cap")
    assert e.state()["mode"] == "OBSERVE"
    cp.set_level("global", "APPROVED_EXECUTION", OP, "raise")
    cp.set_level("recommendations", "OFF", OP, "off")
    assert e.state()["mode"] == "OFF"


def test_engine_never_changes_permissions_or_creates_executable_requests(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    before = cp.permissions()
    e.run(OP, force=True)
    after = cp.permissions()
    assert (before["global_level"], before["category_levels"], before["standing_approvals"]) == \
           (after["global_level"], after["category_levels"], after["standing_approvals"])
    assert cp.list_requests() == [] and cp.list_executions() == []
    assert not any(x["event_type"] in ("PERMISSION_CHANGED", "APPROVED", "EXECUTION_STARTED")
                   for x in cp.audit_events(limit=500) if x["actor"] == "recommendation-engine")
    created = [x for x in cp.audit_events(limit=500) if x["event_type"] == "RECOMMENDATION_CREATED"]
    assert created and all(x["action_type"] == "recommendation.record" for x in created)
    assert cp.verify_audit_chain()["ok"]


@pytest.mark.parametrize("switch", ["dashboard", "sentinel", "startup"])
def test_each_kill_switch_pauses_the_engine(tmp_path, switch):
    cp, e, intel, clock = make(tmp_path, startup=(switch != "startup"))
    if switch == "dashboard":
        cp.set_dashboard_kill_switch(True, OP, "stop")
    elif switch == "sentinel":
        cp.sentinel_path.write_text("stop", encoding="utf-8")
    out = e.run(OP, force=True)
    assert out["ran"] is False and out["mode"] == "BLOCKED" and "kill switch" in out["reason"] and intel.calls == 0
    assert any(x["event_type"] == "RECOMMENDATION_RUN_BLOCKED" for x in cp.audit_events())


def test_kill_switch_keeps_existing_recommendations_readable(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    cp.set_dashboard_kill_switch(True, OP, "stop")
    ov = e.overview()
    assert ov["state"]["mode"] == "BLOCKED" and ov["summary"]["active"] > 0
    rid = ov["recommendations"][0]["id"]
    assert e.set_status(rid, "ACKNOWLEDGED", OP)["recommendation"]["status"] == "ACKNOWLEDGED"   # humans can still triage


def test_throttle_and_busy(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    assert e.run(OP)["ran"] is True
    clock.advance(minutes=2)
    out = e.run(OP)
    assert out["ran"] is False and "5 minutes" in out["reason"]
    assert e.run(OP, force=True)["ran"] is True


# -- 8. Gemini ------------------------------------------------------------------------------------------------------
def test_gemini_disabled_by_default(tmp_path):
    cp, e, intel, clock = make(tmp_path)
    assert e.explainer is None and e.state()["explainer"] == "off"
    out = e.run(OP, force=True)
    assert out["explained"] == 0 and all(r["explanation"] is None for r in e.store.list())


def test_gemini_unavailable_does_not_block_recommendations(tmp_path):
    def broken(prompt):
        raise RuntimeError("Gemini is not configured")
    cp, e, intel, clock = make(tmp_path, environ={eng_mod.EXPLAINER_ENV: "gemini"}, explainer=GeminiExplainer(broken))
    out = e.run(OP, force=True)
    assert out["run"]["created"] > 0 and out["explained"] == 0
    assert sum(1 for x in cp.audit_events(limit=500) if x["event_type"] == "RECOMMENDATION_EXPLAINER_FAILED") == eng_mod.MAX_EXPLANATIONS_PER_RUN


def test_gemini_wording_is_bounded_and_cached(tmp_path):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return "Plain summary."
    cp, e, intel, clock = make(tmp_path, environ={eng_mod.EXPLAINER_ENV: "gemini"}, explainer=GeminiExplainer(fake))
    e.run(OP, force=True)
    assert len(calls) == eng_mod.MAX_EXPLANATIONS_PER_RUN
    assert sum(1 for r in e.store.list() if r["explanation"] == "Plain summary.") == eng_mod.MAX_EXPLANATIONS_PER_RUN
    clock.advance(minutes=10)
    e.run(OP, force=True)                                                    # nothing new or materially changed
    assert len(calls) == eng_mod.MAX_EXPLANATIONS_PER_RUN


def test_no_external_calls_during_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")))
    cp, e, intel, clock = make(tmp_path)
    assert e.run(OP, force=True)["ran"] is True
    pkg = Path(eng_mod.__file__).parent
    src = "\n".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py"))
    for forbidden in ("import requests", "requests.get", "requests.post", "urlopen", "subprocess", "upload_short", "request_retry", "set_level(", "set_dashboard_kill_switch",
                      "grant_standing_approval", "cp.approve(", "cp.execute(", "cp.submit(", "cp.propose(", "control_center_service", "retry_policy.", "topic_history"):
        assert forbidden not in src, forbidden


# -- 9. API ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    cp, e, intel, clock = make(tmp_path)
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    monkeypatch.setattr(eng_mod, "get_engine", lambda: e)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    token = local.get("/api/autonomy/session").json()["csrf_token"]
    return cp, e, main, local, {"X-CrazzyDocs-CSRF": token}


def test_api_endpoints(api):
    cp, e, main, local, h = api
    from fastapi.testclient import TestClient
    assert TestClient(main.app).get("/api/recommendations").status_code == 403
    assert local.post("/api/recommendations/run", json={"operator": OP, "force": True}).status_code == 403      # no CSRF token
    assert local.post("/api/recommendations/run", json={"operator": OP}, headers={**h, "Origin": "http://evil.example"}).status_code == 403
    r = local.post("/api/recommendations/run", json={"operator": OP, "force": True}, headers=h)
    assert r.status_code == 200 and r.json()["ran"] and r.json()["run"]["triggered_by"] == f"operator:{OP}"
    ov = local.get("/api/recommendations").json()
    assert ov["summary"]["active"] == len(ov["recommendations"]) > 0 and ov["state"]["mode"] == "RECOMMEND"
    assert all(x["kind"] == "recommendation" for x in ov["recommendations"]) and "nothing is executed" in ov["note"]
    rid = ov["recommendations"][0]["id"]
    d = local.get(f"/api/recommendations/{rid}").json()
    assert d["evidence"] and d["scoring"]["confidence"]["components"] and d["history"]
    assert local.post(f"/api/recommendations/{rid}/reject", json={"operator": OP}, headers=h).status_code == 422          # reason required
    assert local.post(f"/api/recommendations/{rid}/resolve", json={"operator": OP, "evidence": ""}, headers=h).status_code == 422
    assert local.post(f"/api/recommendations/{rid}/acknowledge", json={"operator": "autonomy-engine"}, headers=h).status_code == 403
    assert local.post(f"/api/recommendations/{rid}/acknowledge", json={"operator": OP}, headers=h).json()["recommendation"]["status"] == "ACKNOWLEDGED"
    assert local.post(f"/api/recommendations/{rid}/resolve", json={"operator": OP, "evidence": "verified"}, headers=h).status_code == 200
    assert local.post(f"/api/recommendations/{rid}/acknowledge", json={"operator": OP}, headers=h).status_code == 409
    assert local.get("/api/recommendations?status=history").json()["recommendations"][0]["id"] == rid
    assert local.get("/api/recommendations?status=BOGUS").status_code == 400
    assert local.get("/api/recommendations/rec_nope").status_code == 404
    t = local.get("/api/recommendations/trend?days=14").json()
    assert t["runs"] and t["created_by_day"]
    assert local.get("/api/recommendations/trend?days=0").status_code == 400
    assert cp.list_requests() == [] and cp.list_executions() == []


def test_api_reports_blocked_engine(api):
    cp, e, main, local, h = api
    cp.sentinel_path.write_text("stop", encoding="utf-8")
    r = local.post("/api/recommendations/run", json={"operator": OP, "force": True}, headers=h).json()
    assert r["ran"] is False and r["mode"] == "BLOCKED"
    assert local.get("/api/recommendations").json()["state"]["mode"] == "BLOCKED"


# -- 10. dashboard and existing functionality -------------------------------------------------------------------------
def _html():
    return (Path(cpm.__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_recommendation_center():
    html = _html()
    for tab in ("overview", "schedule", "queue", "ops", "recs", "controls", "autonomy", "prodconfig", "processes"):
        assert f'data-tab="{tab}"' in html and f'id="tab-{tab}"' in html, tab
    sec = html[html.index('id="tab-recs"'):html.index("STAGE 3.32B: AUTONOMY CONTROL PLANE")]
    for el in ("recActive", "recCritical", "recHigh", "recNew", "recMode", "recBreakdown", "recTrend", "recList", "recInsufficient",
               "recFilter", "recCategory", "recOperator"):
        assert f'id="{el}"' in sec, el
    assert "nothing is executed" in sec and "Autonomy Control" in sec
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", html))
    names = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", js, flags=re.M)
    assert len(names) == len(set(names))
    for fn in ("refreshRecommendations", "recRun", "recAct", "recCard", "recWrite", "refreshAutonomy", "refreshOps"):
        assert fn in names
    rec_js = js[js.index("// Stage 3.32C Recommendation Center"):js.index("// Stage 3.32B Autonomy Control Plane.")]
    assert "this.dataset.id" in rec_js and "[autoState.header]: autoState.csrf" in rec_js and "escHtml(r.title)" in rec_js
    assert 'if (tabId === "recs") { refreshRecommendations(true); }' in html


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_dashboard_script_parses():
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", _html()))
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=js, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0 and res.stdout.strip() == "ok", res.stderr


def test_production_tables_untouched(tmp_path):
    db = tmp_path / "studio.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE persistent_jobs (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("CREATE TABLE studio_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO persistent_jobs VALUES ('cycle_1_20261120_0000', 'RUNNING')")
    conn.execute("INSERT INTO studio_settings VALUES ('control_center.schedule.active', '{\"revision\":\"ccf0bd04f159\"}')")
    conn.commit()
    snap = lambda: (conn.execute("SELECT * FROM persistent_jobs").fetchall(), conn.execute("SELECT * FROM studio_settings").fetchall())
    before = snap()
    cp, e, intel, clock = make(tmp_path)
    e.run(OP, force=True)
    for r in e.store.list(status="open")[:3]:
        e.set_status(r["id"], "APPROVED", OP)
    assert snap() == before
    new_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} - {"persistent_jobs", "studio_settings", "sqlite_sequence"}
    assert new_tables == {"autonomy_config", "autonomy_approval_requests", "autonomy_audit_events", "autonomy_executions",
                          "autonomy_recommendations", "autonomy_recommendation_runs",
                          "autonomy_execution_leases"}
    conn.close()
