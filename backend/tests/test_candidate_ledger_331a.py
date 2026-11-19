"""Stage 3.31A: candidate intelligence ledger."""

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from backend.app.content import candidate_ledger as cl
from backend.app.content.topic_ledger import ContentRecord, DB_PATH
from topic_fakes_331 import FakeWeb, make_engine, web_for_every_query


def _tables(db):
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def _rows(db, sql, *a):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, a)]


def _seed_produced(eng, title, days_ago=2):
    eng.ledger.record_content(ContentRecord(
        topic_id=f"topic_{title}", topic_title=title, opening_hook=f"Hook about {title}", main_subject=title,
        key_entities=[title], script_fingerprint=f"fp_{title}", category="Geography", visual_style="pop_info",
        created_at=(datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z", publication_status="produced"),
        allow_override=True, execution_context="production")


def test_flag_off_writes_nothing(tmp_path):
    eng = make_engine(tmp_path)
    web_for_every_query(eng)
    eng.begin_run("cycle_1_off")
    ranked = eng.discover_and_rank_live_topics()
    assert ranked, "discovery itself must still work"
    assert "topic_candidate_ledger" not in _tables(str(tmp_path / "studio.db"))
    assert isinstance(eng.trace, cl.NullTrace)


def test_every_candidate_is_persisted_with_provenance_and_scores(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    eng.begin_run("cycle_1_a")
    ranked = eng.discover_and_rank_live_topics()
    db = str(tmp_path / "studio.db")
    rows = _rows(db, "select * from topic_candidate_ledger")
    assert len(rows) >= len(ranked) > 0
    eligible = [r for r in rows if r["selection_status"] == cl.ELIGIBLE]
    assert len(eligible) == len(ranked)
    for r in eligible:
        assert r["provider"].startswith("wikipedia_search:")
        assert r["provider_family"] == "wikipedia_search"
        assert r["source_url"].startswith("https://en.wikipedia.org/wiki/")
        assert r["score"] is not None and r["score"] > 0
        bd = json.loads(r["score_breakdown_json"])
        assert {"curiosity", "factual_reliability", "freshness"} <= set(bd)
        assert r["rejection_reason"] is None            # still under evaluation: no forced reason
        assert r["verification_status"] == "SINGLE_SOURCE_ENCYCLOPEDIC"
        assert r["normalized_title"] and r["first_discovered_at"]
    run = _rows(db, "select * from topic_discovery_runs where run_id='cycle_1_a'")[0]
    stats = json.loads(run["provider_stats_json"])
    assert stats["wikipedia_search"]["queries"] >= 1
    assert stats["wikipedia_search"]["accepted"] == len(ranked)
    assert run["eligible_count"] == len(ranked)


def test_rejection_reasons_are_structured(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web = web_for_every_query(eng)
    dup_title, short_title = "Duplicatepeak", "Shortpeak"
    for titles in web.search.values():           # whichever categories this run searches
        titles[:0] = [dup_title, short_title, "List of volcanoes"]
    web.kinds[short_title] = "short"
    _seed_produced(eng, dup_title)
    eng.begin_run("cycle_rej")
    eng.discover_and_rank_live_topics()
    db = str(tmp_path / "studio.db")
    by_title = {r["title"]: r for r in _rows(db, "select * from topic_candidate_ledger")}
    r = by_title[dup_title]
    assert r["selection_status"] == cl.REJECTED
    assert r["rejection_reason"] == cl.RECENTLY_USED and r["rejection_stage"] == "pre_enrichment"
    assert dup_title in (r["duplicate_match"] or "")
    s = by_title[short_title]
    assert (s["rejection_reason"], s["rejection_stage"]) == (cl.INSUFFICIENT_EVIDENCE, "enrichment")
    lst = by_title["List of volcanoes"]
    assert (lst["rejection_reason"], lst["rejection_stage"]) == (cl.LOW_RELEVANCE, "discovery_filter")
    reasons = {r["rejection_reason"] for r in by_title.values() if r["rejection_reason"]}
    assert reasons <= set(cl.REJECTION_REASONS)
    events = _rows(db, "select * from topic_candidate_events where run_id='cycle_rej'")
    assert all(e["reason"] in cl.REJECTION_REASONS for e in events if e["status"] == cl.REJECTED)
    with pytest.raises(ValueError):
        cl.DiscoveryTrace("r").reject("x", "MADE_UP", "s")


def test_duplicate_ingestion_and_retry_are_idempotent(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    db = str(tmp_path / "studio.db")
    eng.begin_run("cycle_retry")
    eng.discover_and_rank_live_topics()
    n_cand = _rows(db, "select count(*) n from topic_candidate_ledger")[0]["n"]
    n_ev = _rows(db, "select count(*) n from topic_candidate_events")[0]["n"]
    eng.begin_run("cycle_retry")                       # a retry of the same job
    eng.discover_and_rank_live_topics()
    assert _rows(db, "select count(*) n from topic_candidate_ledger")[0]["n"] == n_cand
    assert _rows(db, "select count(*) n from topic_candidate_events")[0]["n"] == n_ev
    assert {r["times_seen"] for r in _rows(db, "select times_seen from topic_candidate_ledger")} == {1}
    assert _rows(db, "select attempts from topic_discovery_runs where run_id='cycle_retry'")[0]["attempts"] == 2
    eng.begin_run("cycle_next")                        # a different job sees the same candidates again
    eng.discover_and_rank_live_topics()
    assert _rows(db, "select count(*) n from topic_candidate_ledger")[0]["n"] == n_cand
    assert max(r["times_seen"] for r in _rows(db, "select times_seen from topic_candidate_ledger")) == 2


def test_selection_status_transitions(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    db = str(tmp_path / "studio.db")
    eng.begin_run("cycle_sel")
    ranked = eng.discover_and_rank_live_topics()
    eng.record_selection(ranked[0], "cycle_sel")
    cid = cl.candidate_id(ranked[0].discovery_source, ranked[0].title)
    led = cl.CandidateLedger(db)
    assert led.get(cid)["selection_status"] == cl.SELECTED
    assert led.get(cid)["selected_job_id"] == "cycle_sel"
    assert led.run("cycle_sel")["selected_candidate_id"] == cid
    eng.record_outcome("cycle_sel", cl.PRODUCED)
    row = led.get(cid)
    assert row["selection_status"] == cl.PRODUCED and row["final_outcome"] == cl.PRODUCED
    assert led.run("cycle_sel")["outcome"] == cl.PRODUCED
    # PRODUCED is final: a later run cannot move it back, the attempt is kept as an event
    eng.begin_run("cycle_later")
    eng.discover_and_rank_live_topics()
    assert led.get(cid)["selection_status"] == cl.PRODUCED
    # an ELIGIBLE candidate cannot jump straight to PRODUCED
    other = cl.candidate_id(ranked[1].discovery_source, ranked[1].title)
    assert led._transition("cycle_sel", other, cl.PRODUCED, stage="production") is False
    assert any(e["status"] == "TRANSITION_IGNORED" for e in led.events(cid=other))


def test_attempts_count_discovery_passes_not_saves(tmp_path):
    """Regression (first live shadow run, cycle_6_20261120_0500): one discovery pass saved three
    times (discovery, identity gate, selection) was reported as attempts=3."""
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    led = cl.CandidateLedger(str(tmp_path / "studio.db"))
    eng.begin_run("cycle_att")
    ranked = eng.discover_and_rank_live_topics()
    eng.record_identity_rejections([{"title": ranked[-1].title, "conflicts_with": "cycle_x", "status": "SCRIPTED"}])
    eng.record_selection(ranked[0], "cycle_att")
    eng.record_outcome("cycle_att", cl.PRODUCED)
    assert led.run("cycle_att")["attempts"] == 1
    eng.begin_run("cycle_att")                  # a genuine retry of the same job
    eng.discover_and_rank_live_topics()
    assert led.run("cycle_att")["attempts"] == 2


def test_failed_outcome_is_recorded(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    eng.begin_run("cycle_fail")
    ranked = eng.discover_and_rank_live_topics()
    eng.record_selection(ranked[0], "cycle_fail")
    eng.record_outcome("cycle_fail", cl.FAILED, "QC Failure: duration")
    led = cl.CandidateLedger(str(tmp_path / "studio.db"))
    row = led.get(cl.candidate_id(ranked[0].discovery_source, ranked[0].title))
    assert row["final_outcome"] == cl.FAILED and "QC Failure" in row["final_outcome_reason"]


def test_provider_failure_is_recorded_and_discovery_continues(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web = web_for_every_query(eng)
    web.fail_queries = set(list(web.search)[:200])      # every search fails
    web.feed = {"tfa": {"title": "Kelvaroa"}}
    eng.begin_run("cycle_pf")
    eng.discover_and_rank_live_topics()
    db = str(tmp_path / "studio.db")
    ev = _rows(db, "select * from topic_candidate_events where reason=?", cl.PROVIDER_FAILURE)
    assert ev and all(json.loads(e["detail_json"])["provider"] == "wikipedia_search" for e in ev)
    stats = json.loads(_rows(db, "select provider_stats_json from topic_discovery_runs")[0]["provider_stats_json"])
    assert stats["wikipedia_search"]["errors"] >= 1


def test_database_recovery(tmp_path, monkeypatch):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    db = str(tmp_path / "studio.db")
    eng.begin_run("cycle_r1")
    eng.discover_and_rank_live_topics()
    with sqlite3.connect(db) as c:              # tables lost (e.g. restored older backup)
        c.execute("drop table topic_candidate_events")
    eng.begin_run("cycle_r2")
    eng.discover_and_rank_live_topics()
    assert "topic_candidate_events" in _tables(db)
    # an unusable database never breaks discovery
    bad = tmp_path / "broken.db"
    bad.write_bytes(b"this is not a sqlite database" * 50)
    led = cl.CandidateLedger(str(bad))
    t = cl.DiscoveryTrace("x")
    t.candidate({"title": "Zeta", "discovery_source": "wikipedia_tfa_1116"})
    assert led.flush(t) is False
    # a locked database during a production run: discovery, selection and outcome all carry on
    (tmp_path / "b").mkdir()
    eng2 = make_engine(tmp_path / "b", candidate_ledger="on")
    web_for_every_query(eng2)

    def locked(self):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cl.CandidateLedger, "_connect", locked)
    eng2.begin_run("cycle_locked")
    ranked = eng2.discover_and_rank_live_topics()
    assert ranked, "ledger failure must not block discovery"
    eng2.record_selection(ranked[0], "cycle_locked")
    eng2.record_outcome("cycle_locked", cl.PRODUCED)


def test_historical_records_are_preserved(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on")
    web_for_every_query(eng)
    _seed_produced(eng, "Ancientrecordtitle", days_ago=400)
    db = str(tmp_path / "studio.db")
    before = _rows(db, "select * from content_ledger")
    hist_before = (tmp_path / "topic_history.json").read_bytes()
    eng.begin_run("cycle_hist")
    eng.discover_and_rank_live_topics()
    assert _rows(db, "select * from content_ledger") == before
    assert (tmp_path / "topic_history.json").read_bytes() == hist_before


def test_live_database_is_refused_under_pytest():
    assert cl.CandidateLedger(DB_PATH).usable is False
    assert cl.RotationStore(DB_PATH).persistent is False


def test_stable_ids_and_reason_mapping():
    assert cl.candidate_id("wikipedia_tfa_1116", "Mark Satin") == cl.candidate_id("wikipedia_tfa_1120", "mark satin!")
    assert cl.candidate_id("wikipedia_tfa_1116", "Mark Satin") != cl.candidate_id("physorg_space_rss", "Mark Satin")
    assert cl.provider_family("gnews_lead_science") == "gnews"
    assert cl.provider_family("physorg_space_rss") == "physorg_rss"
    assert cl.provider_family("wikipedia_search:history") == "wikipedia_search"
    assert cl.classify_duplicate_reason("Topic 'X' was produced 3 days ago (within 30-day cooldown period)") == cl.RECENTLY_USED
    assert cl.classify_duplicate_reason("Exact normalized title match with 'X'") == cl.DUPLICATE_EXACT
    assert cl.classify_duplicate_reason("Headline subset/paraphrase match") == cl.DUPLICATE_SEMANTIC


def test_provider_metrics_count_unique_contributions():
    t = cl.DiscoveryTrace("m")
    a = t.candidate({"title": "Alpha", "discovery_source": "wikipedia_search:geography"})
    b = t.candidate({"title": "Alpha", "discovery_source": "gnews_science"})
    c = t.candidate({"title": "Beta", "discovery_source": "gnews_science"})
    t.query("gnews_science", 10)
    t.eligible(a), t.eligible(b), t.eligible(c)
    m = t.provider_metrics()
    assert m["gnews"]["articles"] == 10 and m["gnews"]["accepted"] == 2
    assert m["gnews"]["unique_accepted"] == 1 and m["wikipedia_search"]["unique_accepted"] == 0
