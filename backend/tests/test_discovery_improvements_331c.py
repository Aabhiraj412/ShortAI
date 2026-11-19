"""Stage 3.31C: production query rotation, GNews integration, Wikipedia In the news, provider metrics."""

import json
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.content import candidate_ledger as cl
from backend.app.content import gnews_client as gc
from backend.app.content.curiosity_topic_engine import POOL_RESERVED_SLOTS, assemble_candidate_pool
from topic_fakes_331 import FakeWeb, make_engine, web_for_every_query


def _search_queries(web):
    out = []
    for u in web.calls:
        if "list=search" in u:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            out.append((qs["srsearch"][0], int(qs.get("sroffset", ["0"])[0])))
    return out


def _run(eng, web, run_id):
    web.calls.clear()
    eng.begin_run(run_id)
    eng.discover_and_rank_live_topics()
    return _search_queries(web)


# ---- A. rotation -------------------------------------------------------------------------------
def test_without_rotation_production_repeats_the_same_queries(tmp_path):
    """Documents the audited defect: with the flag off, each production cycle searches the same queries."""
    eng = make_engine(tmp_path)
    web = web_for_every_query(eng)
    assert _run(eng, web, "c1") == _run(eng, web, "c2") == _run(eng, web, "c3")


def test_rotation_advances_each_production_cycle(tmp_path):
    eng = make_engine(tmp_path, production_query_rotation="on")
    web = web_for_every_query(eng)
    q1, q2 = _run(eng, web, "c1"), _run(eng, web, "c2")
    assert q1 != q2
    used1 = {q for q, _ in q1}
    used2 = {q for q, _ in q2}
    assert used1 != used2


def test_rotation_persists_across_restart_and_is_deterministic(tmp_path):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    eng = make_engine(tmp_path / "a", production_query_rotation="on")
    web = web_for_every_query(eng)
    seq_a = [_run(eng, web, f"c{i}") for i in range(3)]
    restarted = make_engine(tmp_path / "a", production_query_rotation="on")      # new process, same database
    web.install(restarted)
    after_restart = _run(restarted, web, "c3")
    fresh = make_engine(tmp_path / "b", production_query_rotation="on")
    web_b = web_for_every_query(fresh)
    seq_b = [_run(fresh, web_b, f"c{i}") for i in range(4)]
    assert seq_a + [after_restart] == seq_b                                        # same inputs, same sequence
    with sqlite3.connect(str(tmp_path / "a" / "studio.db")) as c:
        assert c.execute("select count(*) from topic_discovery_rotation").fetchone()[0] >= 2


def test_failed_request_does_not_advance_rotation(tmp_path):
    from backend.app.content.candidate_ledger import RotationStore
    eng = make_engine(tmp_path, production_query_rotation="on")
    web = web_for_every_query(eng)
    _run(eng, web, "c1")

    def counters():
        with sqlite3.connect(str(tmp_path / "studio.db")) as c:
            return dict(c.execute("select provider_key, counter from topic_discovery_rotation "
                                  "where provider_key like 'wikipedia_search:%'"))
    before = counters()
    assert before and set(before.values()) == {1}
    web.fail_queries = set(web.search)                 # this cycle every search request times out
    second = _run(eng, web, "c2")
    assert second, "searches were attempted"
    assert counters() == before                        # failures neither advanced nor corrupted the state
    web.fail_queries = set()
    _run(eng, web, "c3")
    after = counters()
    assert all(after[k] >= before.get(k, 0) for k in after) and after != before
    assert RotationStore(str(tmp_path / "studio.db")).peek(next(iter(before))) >= 1


def test_search_paging_after_all_queries_used(tmp_path):
    eng = make_engine(tmp_path, production_query_rotation="on")
    web = web_for_every_query(eng)
    offsets = set()
    for i in range(6):
        offsets |= {o for _, o in _run(eng, web, f"c{i}")}
    assert 6 in offsets, "after cycling every query, the next result page is requested"


def test_featured_feed_date_rotates(tmp_path):
    eng = make_engine(tmp_path, production_query_rotation="on")
    web = web_for_every_query(eng, feed={"mostread": {"articles": []}})
    dates = set()
    for i in range(3):
        web.calls.clear()
        eng.begin_run(f"c{i}")
        eng.discover_and_rank_live_topics()
        dates |= {u.rsplit("featured/", 1)[1] for u in web.calls if "feed/featured" in u}
    assert len(dates) == 3


# ---- B. GNews ----------------------------------------------------------------------------------
def _article(title, source, url, desc=None, hours=2, image=None):
    now = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    return {"title": title, "description": desc or (f"{title}. Researchers describe a new result in detail today. "
                                                    "The team measured the effect over many months of work. "
                                                    "Independent experts say the finding could change the field."),
            "content": "", "url": url, "image": image, "publishedAt": (now - timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            "source": {"name": source}}


class FakeGNews:
    def __init__(self, by_cat, error=None):
        self.by_cat, self.error = by_cat, error
        self.last_error_category = None
        self.requests = 0

    def configured(self):
        return True

    def now(self):
        return datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)

    def top_headlines(self, cat, max_results=10):
        self.requests += 1
        self.last_error_category = self.error
        return [] if self.error else list(self.by_cat.get(cat, []))

    def corroborated_events(self, cats):
        return []


@pytest.fixture
def gnews_engine(tmp_path, monkeypatch):
    def build(by_cat, error=None, **flags):
        fake = FakeGNews(by_cat, error)
        monkeypatch.setattr(gc, "GNewsClient", lambda *a, **k: fake)
        web = FakeWeb(search={"Quantum Battery Prototype": ["Quantum battery"], "Kelvaro Comet Discovered": ["Kelvaro comet"]},
                      kinds={"Quantum battery": "probe", "Kelvaro comet": "probe"})
        eng = make_engine(tmp_path, web, gnews_candidates="on", candidate_ledger="on", **flags)
        return eng, web, fake
    return build


def test_independent_sources_and_syndication():
    syndicated = {"articles": [_article("Quantum battery charges in seconds", "AP", "https://a.com/1"),
                               _article("Quantum battery charges in seconds", "Yahoo", "https://b.com/1")]}
    independent = {"articles": [_article("Quantum battery charges in seconds", "AP", "https://a.com/1"),
                                _article("New quantum battery charges within seconds, team says", "Nature", "https://n.com/2")]}
    same_outlet = {"articles": [_article("Quantum battery A", "AP", "https://a.com/1"),
                                _article("Quantum battery B", "AP", "https://a.com/2")]}
    assert gc.independent_source_count(syndicated) == 1
    assert gc.independent_source_count(independent) == 2
    assert gc.independent_source_count(same_outlet) == 1


def test_gnews_single_source_headline_becomes_lead_with_encyclopedic_evidence(gnews_engine):
    eng, web, fake = gnews_engine({"science": [_article("Quantum Battery Prototype unveiled by lab", "Wired", "https://w.com/q")]})
    eng.begin_run("g1")
    eng._start_trace()
    cands = eng._gnews_candidates_v2(None, "2026-11-20T00:00:00Z", ())
    assert len(cands) == 1
    c = cands[0]
    assert c["title"] == "Quantum battery" and c["discovery_source"] == "gnews_lead_science"
    assert c["verification"] == "NEWS_LEAD_ENCYCLOPEDIC_EVIDENCE"
    assert any(s.startswith("Wikipedia Encyclopedia Archive") for s in c["sources"])
    assert any("discovery provenance, not evidence" in s and "https://w.com/q" in s for s in c["sources"])
    assert c["news_lead"]["outlets"] == ["Wired"]
    assert c["factual_reliability"] == 9.6        # evidence is the article, not the headline
    headline = eng.trace.candidates[cl.candidate_id("gnews_science", "Quantum Battery Prototype unveiled by lab")]
    assert headline["rejection_reason"] == cl.INSUFFICIENT_EVIDENCE   # the headline itself is not evidence


def test_gnews_corroborated_event_kept_and_irrelevant_rejected(gnews_engine):
    arts = [_article("Comet Kelvaro breaks up near the Sun", "Reuters", "https://r.com/c"),
            _article("Comet Kelvaro breaks apart near the Sun", "BBC", "https://bbc.com/c"),
            _article("Striker scores twice as club wins league championship match", "ESPN", "https://e.com/s",
                     desc="The footballer scored twice in the championship match. The league season continues next week. "
                          "Fans celebrated the tournament victory in the stadium.")]
    eng, web, fake = gnews_engine({"science": arts})
    eng.begin_run("g2")
    eng._start_trace()
    cands = eng._gnews_candidates_v2(None, "2026-11-20T00:00:00Z", ("league", "season", "footballer"))
    corroborated = [c for c in cands if c.get("verification") == "multi_source_corroborated_headline"]
    assert len(corroborated) == 1 and corroborated[0]["independent_sources"] == 2
    assert {s.split(" (")[0] for s in corroborated[0]["sources"]} == {"Reuters", "BBC"}
    sport = eng.trace.candidates[cl.candidate_id("gnews_science", "Striker scores twice as club wins league championship match")]
    assert sport["rejection_reason"] == cl.LOW_RELEVANCE


def test_gnews_stale_articles_dropped_and_lead_budget_bounded(gnews_engine):
    heads = ["Glacier melt speeds ocean currents", "Robot surgeon completes heart operation",
             "Ancient seeds sprout after millennia", "Deep sea vents host strange microbes"]
    arts = [_article(h, f"Outlet{i}", f"https://o{i}.com/x", hours=2) for i, h in enumerate(heads)]
    arts.append(_article("Old story about something", "Old", "https://old.com/x", hours=24 * 5))
    eng, web, fake = gnews_engine({"science": arts})
    eng.begin_run("g3")
    eng._start_trace()
    eng._gnews_candidates_v2(None, "2026-11-20T00:00:00Z", ())
    assert web.count("srlimit=3") <= 2                         # at most GNEWS_MAX_LEADS lead lookups
    budget_rej = [r for r in eng.trace.candidates.values()
                  if r["rejection_reason"] == cl.INSUFFICIENT_EVIDENCE and r["provider_family"] == "gnews"]
    assert len(budget_rej) == 4
    assert all("Old story" not in r["title"] for r in eng.trace.candidates.values())


def test_gnews_failure_and_rate_limit_never_break_discovery(gnews_engine):
    eng, web, fake = gnews_engine({}, error="rate_limited")
    eng.begin_run("g4")
    eng._start_trace()
    assert eng._gnews_candidates_v2(None, "2026-11-20T00:00:00Z", ()) == []
    assert eng.trace.providers["gnews"]["errors"] == 3
    assert fake.requests == 3                                  # one per category, no retry storm


def test_pool_assembly_keeps_reserved_families():
    wiki = [{"title": f"W{i}", "discovery_source": "wikipedia_search:history"} for i in range(12)]
    rss = [{"title": f"R{i}", "discovery_source": "physorg_space_rss"} for i in range(15)]
    news = [{"title": f"G{i}", "discovery_source": "gnews_science"} for i in range(4)]
    legacy = (wiki + rss + news)[:18]
    assert not any(c["discovery_source"].startswith("gnews") for c in legacy)      # the audited defect
    pool, dropped = assemble_candidate_pool([wiki, rss, news], 18, POOL_RESERVED_SLOTS)
    assert len(pool) == 18 and len(dropped) == 31 - 18
    assert sum(1 for c in pool if c["discovery_source"].startswith("gnews")) == 3
    assert pool == [c for c in wiki + rss + news if c in pool]                     # original order kept
    small, none = assemble_candidate_pool([wiki[:2], news[:1]], 18, POOL_RESERVED_SLOTS)
    assert len(small) == 3 and none == []


def test_gnews_flag_off_uses_legacy_path(tmp_path, monkeypatch):
    called = {}

    class Legacy(FakeGNews):
        def corroborated_events(self, cats):
            called["legacy"] = True
            return []
    monkeypatch.setattr(gc, "GNewsClient", lambda *a, **k: Legacy({}))
    eng = make_engine(tmp_path)
    web_for_every_query(eng)
    eng.discover_and_rank_live_topics()
    assert called.get("legacy")


# ---- C. Wikipedia In the news --------------------------------------------------------------------
def _feed_with_news():
    return {
        "news": [
            "not a dict",
            {"story": "<b>Nothing</b> linked", "links": []},
            {"story": "Astronomers report the <b>Kelvaro comet</b> breaking apart.",
             "links": [{"title": "Kelvaro_comet", "description": "Comet discovered in 2026"}]},
            {"story": "Elections are held in <b>Freedonia</b>.",
             "links": [{"title": "Freedonia", "description": "Country in Europe"},
                       {"title": "2026_Freedonia_general_election", "description": "General election"}]},
        ],
    }


def test_in_the_news_candidates_reuse_feed_and_handle_malformed(tmp_path):
    web = FakeWeb(search={}, kinds={"Kelvaro comet": "probe"}, feed=_feed_with_news())
    eng = make_engine(tmp_path, web, wikipedia_in_the_news="on", candidate_ledger="on")
    eng.begin_run("itn1")
    ranked = eng.discover_and_rank_live_topics(preferred_category="History")
    assert web.count("feed/featured") == 1                       # no extra request for In the news
    itn = [t for t in ranked if t.discovery_source.startswith("wikipedia_itn_")]
    assert [t.title for t in itn] == ["Kelvaro comet"]
    assert any("en.wikipedia.org/wiki/Kelvaro_comet" in s for s in itn[0].sources)
    assert eng.trace.notes.get("wikipedia_itn_malformed_items") == 2
    rej = [r for r in eng.trace.candidates.values() if r["provider_family"] == "wikipedia_itn" and r["status"] == cl.REJECTED]
    assert rej and rej[0]["rejection_reason"] == cl.LOW_RELEVANCE      # country and election links refused
    assert eng.trace.providers["wikipedia_itn"]["articles"] == 4


def test_in_the_news_off_ignores_section(tmp_path):
    web = FakeWeb(search={}, kinds={"Kelvaro comet": "probe"}, feed=_feed_with_news())
    eng = make_engine(tmp_path, web)
    ranked = eng.discover_and_rank_live_topics(preferred_category="History")
    assert not any(t.discovery_source.startswith("wikipedia_itn_") for t in ranked)


# ---- D. provider metrics -----------------------------------------------------------------------
def test_provider_metrics_are_persisted(tmp_path):
    eng = make_engine(tmp_path, candidate_ledger="on", production_query_rotation="on")
    web_for_every_query(eng)
    eng.begin_run("m1")
    eng.discover_and_rank_live_topics()
    with sqlite3.connect(str(tmp_path / "studio.db")) as c:
        stats_json, rot_json = c.execute("select provider_stats_json, rotation_json from topic_discovery_runs").fetchone()
    stats = json.loads(stats_json)
    ws = stats["wikipedia_search"]
    assert set(ws) == {"queries", "articles", "candidates_created", "rejected", "accepted", "duplicates", "errors", "unique_accepted"}
    assert ws["queries"] == 4 and ws["articles"] > 0 and ws["accepted"] > 0 and ws["unique_accepted"] == ws["accepted"]
    assert "wikipedia_search:" + eng._last_selected_specs[0].key in json.loads(rot_json)
