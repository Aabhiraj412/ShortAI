"""Remediation WS3: topic discovery root causes.

1. Re-sighting a candidate that is rejected before enrichment/classification overwrote its stored summary,
   category, entities, score breakdown and v2 prediction with empty values (3.31 report 9.10 item 1).
2. Subjects another job had already selected passed ranking and enrichment, inflated eligible_count, and were
   removed only by the final creative-identity integrity gate (production evidence: 4-6 identity rejections per
   run on 2026-11-26, leaving 0-1 eligible topics). They are now dropped before enrichment by the same rule; the
   final integrity gate is unchanged.

Temporary databases and the 3.31 fake web only (no network).
"""
from __future__ import annotations

import json
import sqlite3

from backend.app.content import candidate_ledger as cl
from topic_fakes_331 import make_engine, web_for_every_query


def _flush_one(db, run_id, cand, *, status=None):
    led = cl.CandidateLedger(db)
    t = cl.DiscoveryTrace(run_id)
    cid = t.candidate(cand)
    if status:
        t.reject(cid, status, "pre_enrichment")
    assert led.flush(t) is True
    return led, cid


def test_resighting_keeps_stored_prediction_and_details(tmp_path):
    db = str(tmp_path / "studio.db")
    full = {"title": "Tunguska event", "discovery_source": "wikipedia_tfa_1120", "summary": "A 1908 explosion.",
            "category": "Science", "category_code": "SCI", "key_entities": ["Tunguska", "Siberia"],
            "category_v2": {"category": "SCIENCE", "confidence": 0.82}}
    led, cid = _flush_one(db, "run_1", full)
    before = led.get(cid)
    assert json.loads(before["category_v2_json"])["category"] == "SCIENCE"
    # Re-sighted next run and rejected before enrichment: the trace carries no summary/category/prediction.
    led, cid2 = _flush_one(db, "run_2", {"title": "Tunguska event", "discovery_source": "wikipedia_tfa_1120"},
                           status=cl.RECENTLY_USED)
    assert cid2 == cid
    after = led.get(cid)
    assert json.loads(after["category_v2_json"])["category"] == "SCIENCE"
    assert after["summary"] == "A 1908 explosion." and after["category"] == "Science"
    assert json.loads(after["entities_json"]) == ["Tunguska", "Siberia"]
    assert after["times_seen"] == 2 and after["last_run_id"] == "run_2"


def test_new_prediction_still_replaces_the_old_one_and_is_kept_per_run(tmp_path):
    db = str(tmp_path / "studio.db")
    base = {"title": "Halley's Comet", "discovery_source": "wikipedia_mostread_1120"}
    _flush_one(db, "run_a", dict(base, category_v2={"category": "SPACE", "confidence": 0.7}))
    led, cid = _flush_one(db, "run_b", dict(base, category_v2={"category": "SCIENCE", "confidence": 0.9}))
    assert json.loads(led.get(cid)["category_v2_json"])["category"] == "SCIENCE"
    preds = [json.loads(e["detail_json"])["category_v2"]["category"] for e in led.events(cid=cid)
             if e["stage"] == "classification"]
    assert "SCIENCE" in preds


def test_identity_precheck_drops_used_subjects_before_enrichment(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    eng.begin_run("cycle_base")
    baseline = eng.discover_and_rank_live_topics()
    assert len(baseline) >= 3
    used = {baseline[0].title, baseline[1].title}
    enriched = []
    orig = eng.enrich_wikipedia_candidate

    def spy(title, *a, **k):
        enriched.append(title)
        return orig(title, *a, **k)

    eng.enrich_wikipedia_candidate = spy
    eng.identity_precheck = lambda title: ({"job_id": "cycle_old", "status": "PRODUCED"} if title in used else None)
    eng.begin_run("cycle_next")
    ranked = eng.discover_and_rank_live_topics()
    eng.identity_precheck = None
    titles = {t.title for t in ranked}
    assert not (titles & used)                               # never ranked, so never counted as eligible
    assert not (set(enriched) & used)                        # and never enriched (no wasted fetches)
    led = cl.CandidateLedger(str(tmp_path / "studio.db"))
    stages = [e for e in led.events(run_id="cycle_next") if e["stage"] == "identity_precheck"]
    assert stages and all(e["reason"] == cl.RECENTLY_USED for e in stages)
    assert all("already selected" in json.loads(e["detail_json"])["rule"] for e in stages)


def test_identity_precheck_errors_never_block_discovery(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)

    def broken(title):
        raise RuntimeError("registry unavailable")

    eng.identity_precheck = broken
    eng.begin_run("cycle_err")
    assert eng.discover_and_rank_live_topics()               # the final integrity gate remains the backstop


def test_orchestrator_sets_and_clears_the_precheck():
    import inspect
    from backend.app.orchestration import crazzydocs_master_orchestrator as mo
    src = inspect.getsource(mo)
    assert "self.topic_engine.identity_precheck = (" in src
    assert "self.topic_engine.identity_precheck = None" in src
    # the final integrity gate is still there and unchanged
    assert "integrity gate: every discovered topic was already selected by an earlier" in src
    assert "identity_registry.filter_fresh(ranked, job_id=job_id)" in src


def test_flags_that_would_widen_discovery_stay_off_by_default():
    """Query rotation and exhaustion recovery exist (3.31C/D) but activating them is an operator decision."""
    from backend.app.content.topic_intelligence_flags import TopicIntelligenceFlags
    f = TopicIntelligenceFlags.from_mapping({})
    assert f.production_query_rotation == "off" and f.exhaustion_recovery == "off"
