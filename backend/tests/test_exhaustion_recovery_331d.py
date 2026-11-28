"""Stage 3.31D: bounded topic-exhaustion recovery."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.content import candidate_ledger as cl
from backend.app.content.topic_ledger import ContentRecord
from backend.app.orchestration import crazzydocs_master_orchestrator as om
from topic_fakes_331 import make_engine, web_for_every_query


@pytest.fixture(autouse=True)
def no_real_pacing_sleep(monkeypatch):
    """Recovery paces its requests with real sleeps (min_request_interval_seconds, ~21 s per test here). A call that
    does not inject ``sleep`` gets a no-op (requests, bounds and outcomes are unchanged); tests that verify pacing pass
    their own recorder (test_time_budget_and_politeness) and keep it. Pacing itself: test_wikimedia_rate_limit_335b."""
    from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine
    real = CuriosityTopicEngine.recover_from_exhaustion

    def recover(self, *args, **kwargs):
        kwargs.setdefault("sleep", lambda s: None)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(CuriosityTopicEngine, "recover_from_exhaustion", recover)


def _seed(eng, title):
    eng.ledger.record_content(ContentRecord(
        topic_id=f"t_{title}", topic_title=title, opening_hook=f"Hook {title}", main_subject=title, key_entities=[title],
        script_fingerprint=f"fp_{title}", category="Geography", visual_style="pop_info",
        created_at=(datetime.utcnow() - timedelta(days=1)).isoformat() + "Z", publication_status="produced"),
        allow_override=True, execution_context="production")


def _exhaust_first_page(eng, web):
    """Every first-page title of every query is already produced: the normal pass finds nothing."""
    for titles in web.search.values():
        for t in titles[:6]:
            _seed(eng, t)


def test_flag_off_does_nothing(tmp_path):
    eng = make_engine(tmp_path)
    web = web_for_every_query(eng)
    eng.discover_and_rank_live_topics()
    n = len(web.calls)
    assert eng.recover_from_exhaustion() == []
    assert eng.last_recovery_report["outcome"] == "disabled" and len(web.calls) == n


def test_recovers_from_next_page_without_bypassing_duplicate_protection(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on", candidate_ledger="on")
    web = web_for_every_query(eng)
    _exhaust_first_page(eng, web)
    eng.begin_run("cycle_ex")
    assert eng.discover_and_rank_live_topics() == []
    recovered = eng.recover_from_exhaustion()
    rep = eng.last_recovery_report
    assert recovered and rep["outcome"] == "recovered" and rep["steps"][0]["step"] == "next_page_same_queries"
    produced = {t for titles in web.search.values() for t in titles[:6]}
    assert not ({t.title for t in recovered} & produced)
    assert all(not t.is_duplicate and t.composite_score > 0 for t in recovered)
    assert rep["requests"] <= eng.flags.exhaustion_limits.max_requests
    # the recovery is part of the run's audit trail
    run = cl.CandidateLedger(str(tmp_path / "studio.db")).run("cycle_ex")
    assert '"outcome": "recovered"' in run["recovery_json"]


def test_identity_gate_still_applies_and_failure_is_informative(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on")
    web = web_for_every_query(eng)
    _exhaust_first_page(eng, web)
    eng.discover_and_rank_live_topics()
    reject_all = lambda ranked: ([], [{"title": t.title, "conflicts_with": "cycle_old", "status": "SCRIPTED"} for t in ranked])
    assert eng.recover_from_exhaustion(reject_all) == []
    rep = eng.last_recovery_report
    assert rep["outcome"] == "exhausted"
    assert len(rep["steps"]) <= eng.flags.exhaustion_limits.max_attempts
    assert rep.summary().startswith("TOPIC_EXHAUSTED: recovery exhausted after")


@pytest.mark.parametrize("limit,key", [("max_requests", "max_requests"), ("max_candidates", "max_candidates")])
def test_request_and_candidate_bounds(tmp_path, limit, key):
    eng = make_engine(tmp_path, exhaustion_recovery="on",
                      exhaustion_limits={"max_requests": 5 if limit == "max_requests" else 60,
                                         "max_candidates": 2 if limit == "max_candidates" else 24})
    web = web_for_every_query(eng)
    _exhaust_first_page(eng, web)
    eng.discover_and_rank_live_topics()
    n = len(web.calls)
    reject_all = lambda ranked: ([], [])
    eng.recover_from_exhaustion(reject_all, sleep=lambda s: None)
    rep = eng.last_recovery_report
    assert rep["stopped_by"] == key
    if key == "max_requests":
        assert len(web.calls) - n <= 5 and rep["requests"] <= 5
    else:
        assert rep["candidates_evaluated"] <= 2


def test_time_budget_and_politeness(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on",
                      exhaustion_limits={"max_elapsed_seconds": 10, "min_request_interval_seconds": 0.5})
    web = web_for_every_query(eng)
    _exhaust_first_page(eng, web)
    eng.discover_and_rank_live_topics()
    t = {"now": 0.0}
    slept = []

    def clock():
        t["now"] += 1.0                  # every clock read advances one second
        return t["now"]
    eng.recover_from_exhaustion(lambda r: ([], []), clock=clock, sleep=slept.append)
    rep = eng.last_recovery_report
    assert rep["stopped_by"] == "time_budget"


def test_recovery_deadline_is_respected(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on")
    web = web_for_every_query(eng)
    eng.discover_and_rank_live_topics()
    n = len(web.calls)
    now = datetime(2026, 11, 20, 3, 0)
    # 15 minutes left, but a production attempt needs 20: recovery must not start
    assert eng.recover_from_exhaustion(deadline_utc=now + timedelta(minutes=15), reserve_seconds=1200, now_utc=now) == []
    assert eng.last_recovery_report["outcome"] == "skipped"
    assert eng.last_recovery_report["stopped_by"] == "recovery_deadline" and len(web.calls) == n
    # with time left the budget is capped by the deadline
    eng.recover_from_exhaustion(lambda r: ([], []), deadline_utc=now + timedelta(minutes=21), reserve_seconds=1200,
                                now_utc=now, sleep=lambda s: None)
    assert eng.last_recovery_report["budget_seconds"] <= 60


def test_recovery_restores_test_http_override(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on")
    web = web_for_every_query(eng)
    eng.discover_and_rank_live_topics()
    eng.recover_from_exhaustion(lambda r: ([], []), sleep=lambda s: None)
    assert eng._http_get_json == web.get_json


# ---- orchestrator integration --------------------------------------------------------------------
def _orch(engine):
    o = om.CrazzyDocsMasterOrchestrator.__new__(om.CrazzyDocsMasterOrchestrator)
    o.topic_engine = engine
    return o


def test_orchestrator_hook_is_inert_when_flag_off(tmp_path):
    eng = make_engine(tmp_path)
    web_for_every_query(eng)
    o = _orch(eng)
    assert o._topic_exhaustion_recovery("cycle_1", None, True) == []
    assert o._exhaustion_suffix() == ""
    assert o._topic_exhaustion_recovery("cycle_1", None, True) == []
    # an engine without the feature (older test doubles) is handled
    assert _orch(SimpleNamespace())._topic_exhaustion_recovery("cycle_1", None, True) == []


def test_orchestrator_recovers_and_reports(tmp_path):
    eng = make_engine(tmp_path, exhaustion_recovery="on")
    web = web_for_every_query(eng)
    _exhaust_first_page(eng, web)
    eng.discover_and_rank_live_topics()
    o = _orch(eng)
    ranked = o._topic_exhaustion_recovery("cycle_missing_job", None, True)
    assert ranked
    registry = SimpleNamespace(filter_fresh=lambda ranked, job_id: ([], [{"title": t.title, "conflicts_with": "x", "status": "S"} for t in ranked]))
    assert o._topic_exhaustion_recovery("cycle_missing_job", registry, True) == []
    assert o._exhaustion_suffix().startswith(" [TOPIC_EXHAUSTED")
    assert o._topic_exhaustion_recovery("cycle_missing_job", None, False) == []       # never for non-live runs


def test_orchestrator_source_order_unchanged():
    src = open(om.__file__, encoding="utf-8").read()
    assert src.index("identity_registry.filter_fresh(ranked") < src.index("self._topic_exhaustion_recovery(job_id, identity_registry, is_full_live)\n                    if not ranked:\n                        raise DuplicateCreativeIdentity")
    assert src.index("_topic_exhaustion_recovery(job_id, identity_registry, is_full_live)") < src.index("identity_registry.reserve(\n")
