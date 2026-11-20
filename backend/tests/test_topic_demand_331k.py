"""Stage 3.31K: demand-led discovery, multi-signal scoring v2, expanded categories. Offline fakes only."""
from __future__ import annotations

import json
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.content import curiosity_topic_engine as cte
from backend.app.content import demand_signals as ds
from backend.app.content import topic_scoring_v2 as sv2
from backend.app.content.category_classifier import CLASSIFIED, OUT_OF_SCOPE, CategoryClassifierV2
from backend.app.content.creative_production_policy import CreativeProductionPolicy
from backend.app.content.expanded_categories import EXPANDED_KEYS, register_expanded_categories
from backend.app.content.topic_intelligence_flags import TopicIntelligenceFlags
from topic_fakes_331 import FakeWeb, make_engine

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>james webb telescope</title><ht:approx_traffic>200K+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>Webb finds a new galaxy</ht:news_item_title><ht:news_item_url>https://example.org/a</ht:news_item_url><ht:news_item_source>Example</ht:news_item_source></ht:news_item></item>
<item><title>celebrity divorce</title><ht:approx_traffic>500K+</ht:approx_traffic></item>
<item><title>quiet volcano</title><ht:approx_traffic>2,000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>Man killed near volcano</ht:news_item_title><ht:news_item_url>https://example.org/b</ht:news_item_url><ht:news_item_source>X</ht:news_item_source></ht:news_item></item>
</channel></rss>"""


class DemandWeb(FakeWeb):
    """FakeWeb plus the Wikimedia pageview and Google Trends endpoints."""

    def __init__(self, top=None, views=None, rss=RSS, fail=(), **kw):
        super().__init__(**kw)
        self.top = top if top is not None else [
            {"article": "Main_Page", "views": 9_000_000, "rank": 1},
            {"article": "Kilauea", "views": 400_000, "rank": 2},
            {"article": "Deaths_in_2026", "views": 300_000, "rank": 3},
            {"article": "Mount_Tambora", "views": 20_000, "rank": 4}]
        self.views = views or {"Kilauea": [100] * 7 + [400, 500]}
        self.rss, self.fail = rss, set(fail)

    def get_json(self, url, timeout=12.0):
        if any(f in url for f in self.fail):
            self.calls.append(url)
            raise TimeoutError("simulated")
        if "/pageviews/top/" in url:
            self.calls.append(url)
            return {"items": [{"articles": self.top}]}
        if "/pageviews/per-article/" in url:
            self.calls.append(url)
            title = urllib.parse.unquote(url.split("/user/")[1].split("/")[0]).replace("_", " ")
            return {"items": [{"views": v} for v in self.views.get(title, [])]}
        if "rest.php/v1/search/title" in url:
            self.calls.append(url)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            return {"pages": [{"title": "James Webb Space Telescope"}]} if "webb" in q else {"pages": []}
        return super().get_json(url, timeout)

    def get_text(self, url, timeout=12.0):
        self.calls.append(url)
        if any(f in url for f in self.fail):
            raise TimeoutError("simulated")
        return self.rss if "trends.google.com" in url else None


def collector(web, regions=("US",)):
    return ds.DemandSignalCollector(web.get_json, web.get_text, now=lambda: NOW, regions=regions)


# -- signals --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("term,ok", [("Kilauea", True), ("Main_Page", False), ("Special:Search", False),
                                     ("Deaths in 2026", False), ("List of volcanoes", False), ("2026 in film", False),
                                     ("celebrity divorce", False), ("why is nvidia stock going down", False),
                                     ("Arsenal vs Chelsea", False), ("trump rally", False), ("हिंदी", False),
                                     (".xxx", False), ("ab", False)])
def test_acceptable_term(term, ok):
    assert ds.acceptable_term(term) is ok


def test_article_unsuitable_filters_biographies_and_crime():
    assert ds.article_unsuitable("Jane Doe is an American actress and singer.") == "person biography"
    assert ds.article_unsuitable("Kenneth McElroy was an American criminal who was shot dead.") is not None
    assert ds.article_unsuitable("Kilauea is an active shield volcano in Hawaii.") is None


def test_parse_traffic():
    assert [ds.parse_traffic(x) for x in ("200K+", "2M+", "1,000+", "", "n/a")] == [200000, 2000000, 1000, 0, 0]


def test_collect_wikipedia_and_trends_with_provenance_and_velocity():
    web = DemandWeb()
    c = collector(web)
    sigs = c.collect()
    wiki = [s for s in sigs if s.source == "wikipedia_top"]
    assert [s.term for s in wiki] == ["Kilauea", "Mount Tambora"]                   # main page and lists dropped
    assert wiki[0].velocity == round(450 / 100, 2) and wiki[0].url.endswith("/Kilauea")
    assert "2026/11/19" in web.calls[0]                                              # yesterday's top list
    trends = [s for s in sigs if s.source == "google_trends"]
    assert [t.term for t in trends] == ["james webb telescope"]                      # gossip and violent news dropped
    assert trends[0].value == 200000 and trends[0].related[0]["url"] == "https://example.org/a"
    assert c.errors == {}


def test_one_failing_provider_does_not_stop_others():
    web = DemandWeb(fail=("trends.google.com",))
    c = collector(web)
    sigs = c.collect()
    assert {s.source for s in sigs} == {"wikipedia_top"} and "trends.google.com" in c.errors


def test_malformed_feed_is_reported_not_raised():
    c = collector(DemandWeb(rss="<html><body>blocked</body>"))
    assert c.google_trends() == [] and "google_trends:US" in c.errors


def test_rank_demand_terms_merges_sources():
    t0 = NOW.isoformat()
    sigs = [ds.DemandSignal("Kilauea", "wikipedia_top", "global-en", "daily_views", 400000, 2, t0, velocity=4.5,
                            wikipedia_title="Kilauea"),
            ds.DemandSignal("kilauea", "google_trends", "US", "approx_traffic", 50000, 1, t0),
            ds.DemandSignal("Mount Tambora", "wikipedia_top", "global-en", "daily_views", 2000, 9, t0, velocity=0.5,
                            wikipedia_title="Mount Tambora")]
    ranked = ds.rank_demand_terms(sigs)
    assert ranked[0]["term"] == "Kilauea" and set(ranked[0]["sources"]) == {"wikipedia_top", "google_trends"}
    assert ranked[0]["demand_index"] > ranked[1]["demand_index"]
    assert 0.0 <= ranked[-1]["demand_index"] <= 1.0


def test_signal_store_persists_provenance(tmp_path):
    db = str(tmp_path / "s.db")
    sqlite3.connect(db).close()
    st = ds.DemandSignalStore(db)
    assert st.recent() == []                                                         # reads create nothing
    sigs = collector(DemandWeb()).collect()
    assert st.record(sigs, run_id="job1") == len(sigs)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT run_id, source, evidence_json FROM topic_demand_signals WHERE source='google_trends'").fetchone()
    assert row[0] == "job1" and json.loads(row[2])["related"][0]["source"] == "Example"


# -- scoring v2 -----------------------------------------------------------------------------------------------
class V1:
    def __init__(self, score=8.0, category="Geography", sim=0.2):
        self.composite_score = score
        self.category = category
        self.score_breakdown = {"curiosity": 8.0, "short_form_suitability": 7.0, "factual_reliability": 9.0,
                                "visual_availability": 8.0, "novelty": 7.0}
        self.discovery_metadata = {"semantic_similarity": sim}


def test_score_v2_dimensions_measured_and_neutral():
    plain = {"title": "Kilauea", "discovery_source": "wikipedia_search:geography", "sources": ["a", "b"],
             "image_urls": ["x"] * 4, "extended_extract": "y" * 2000, "publication_timestamp": NOW.isoformat()}
    r = sv2.score_v2(plain, V1(), now=NOW)
    d = r["dimensions"]
    assert set(d) == set(sv2.WEIGHTS)
    assert not d["demand"]["measured"] and d["demand"]["value"] == sv2.NEUTRAL["demand"]
    assert not d["freshness"]["measured"]                       # a Wikipedia timestamp is an edit, not news
    assert not d["historical_performance"]["measured"] and d["historical_performance"]["value"] == 0.5
    assert d["novelty"]["value"] == 0.8 and 0 < r["score"] <= 10
    demand = dict(plain, discovery_source="demand:wikipedia_top", demand={"demand_index": 0.9, "velocity": 3.0,
                                                                         "sources": ["wikipedia_top"]})
    r2 = sv2.score_v2(demand, V1(), now=NOW)
    assert r2["dimensions"]["demand"]["measured"] and r2["dimensions"]["trend_velocity"]["value"] > 0.9
    assert r2["score"] > r["score"]                              # demand lifts the topic
    news = dict(plain, discovery_source="gnews_science", publication_timestamp=(NOW - timedelta(days=7)).isoformat())
    assert sv2.score_v2(news, V1(), now=NOW)["dimensions"]["freshness"]["value"] == 0.5


def test_historical_performance_is_one_bounded_signal():
    hist_good = lambda c: (1.0, "great category")
    hist_bad = lambda c: (0.0, "poor category")
    base = {"title": "x", "sources": [], "image_urls": []}
    g = sv2.score_v2(base, V1(), now=NOW, historical=hist_good)["score"]
    b = sv2.score_v2(base, V1(), now=NOW, historical=hist_bad)["score"]
    assert round(g - b, 2) == round(10 * sv2.WEIGHTS["historical_performance"], 2) == 0.5   # at most 0.5 of 10


def test_blended_never_revives_a_rejection():
    assert sv2.blended(0.0, {"score": 9.9}) == 0.0
    assert sv2.blended(8.0, {"score": 6.0}) == 7.0


def test_category_performance_provider(tmp_path):
    db = str(tmp_path / "s.db")
    assert sv2.category_performance_provider(db)("Geography")[0] is None
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE video_performance (topic_category TEXT, metrics_json TEXT)")
    rows = [("Geography", 2000)] * 3 + [("History", 500)] * 3 + [("Science", 1000)]
    conn.executemany("INSERT INTO video_performance VALUES (?,?)", [(c, json.dumps({"views": v})) for c, v in rows])
    conn.commit()
    conn.close()
    look = sv2.category_performance_provider(db)
    assert look("Geography")[0] == 1.0 and look("History")[0] == 0.25
    assert look("Science")[0] is None                            # fewer than 3 videos: no signal


# -- expanded categories ---------------------------------------------------------------------------------------
def test_expanded_categories_change_scope_only_when_registered():
    plain = CreativeProductionPolicy(config_path="")
    film = ("Dune Part Two", "Dune: Part Two is a 2024 American epic science fiction film directed by Denis Villeneuve.")
    assert CategoryClassifierV2(plain).classify(*film).status == OUT_OF_SCOPE
    pol = CreativeProductionPolicy(config_path="")
    assert register_expanded_categories(pol) == list(EXPANDED_KEYS)
    assert register_expanded_categories(pol) == []                                   # idempotent
    r = CategoryClassifierV2(pol).classify(*film)
    assert r.status == CLASSIFIED and r.category_key == "cinema_and_entertainment"
    sport = ("Lionel Messi", "Lionel Messi is an Argentine professional footballer.")
    assert CategoryClassifierV2(pol).classify(*sport).category_key == "sports_and_major_events"
    pol_r = CategoryClassifierV2(pol).classify("General election", "The 2026 general election was an election held in X.")
    assert pol_r.status == OUT_OF_SCOPE                                               # politics stays out of scope


def test_new_flags_parse_and_default_off():
    f = TopicIntelligenceFlags.from_mapping({})
    assert (f.demand_signals, f.scoring_v2, f.expanded_categories) == ("off", "off", "off")
    f = TopicIntelligenceFlags.from_mapping({"demand_signals": "shadow", "scoring_v2": "on", "expanded_categories": True})
    assert (f.demand_signals, f.scoring_v2, f.expanded_categories) == ("shadow", "on", "on")
    assert TopicIntelligenceFlags.from_mapping({"expanded_categories": "shadow"}).expanded_categories == "off"


# -- engine integration ---------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cte, "DEMAND_REQUEST_GAP_SEC", 0.0)


def test_demand_candidates_resolve_verify_and_record(tmp_path):
    web = DemandWeb(kinds={"Kilauea": "volcano", "Mount Tambora": "volcano", "James Webb Space Telescope": "volcano"})
    eng = make_engine(tmp_path, web, demand_signals="on")
    sqlite3.connect(str(tmp_path / "studio.db")).close()
    cands = eng._demand_candidates(NOW.isoformat(), set())
    titles = [c["title"] for c in cands]
    assert "Kilauea" in titles and "James Webb Space Telescope" in titles
    k = next(c for c in cands if c["title"] == "Kilauea")
    assert k["discovery_source"] == "demand:wikipedia_top" and k["demand"]["velocity"] == 4.5
    s = eng.last_demand_summary
    assert s["signals"] >= 3 and s["errors"] == {}
    n = sqlite3.connect(str(tmp_path / "studio.db")).execute("SELECT COUNT(*) FROM topic_demand_signals").fetchone()[0]
    assert n == s["signals"]


def test_demand_shadow_never_reaches_the_pool(tmp_path):
    web = DemandWeb(kinds={"Kilauea": "volcano"})
    eng = make_engine(tmp_path, web, demand_signals="shadow")
    pool = eng.discover_live_candidates(max_candidates=6)
    assert not any(str(c.get("discovery_source", "")).startswith("demand:") for c in pool)
    assert eng.last_demand_summary["candidates"]                                   # but it ran and was recorded
    on = make_engine(tmp_path / "on", DemandWeb(kinds={"Kilauea": "volcano"}), demand_signals="on")
    (tmp_path / "on").mkdir(exist_ok=True)
    pool_on = on.discover_live_candidates(max_candidates=6)
    assert any(str(c.get("discovery_source", "")).startswith("demand:") for c in pool_on)


def test_demand_off_makes_no_demand_requests(tmp_path):
    web = DemandWeb()
    eng = make_engine(tmp_path, web)
    eng.discover_live_candidates(max_candidates=6)
    assert web.count("pageviews") == 0 and web.count("trends.google.com") == 0


def test_scoring_v2_shadow_records_without_changing_scores(tmp_path):
    eng = make_engine(tmp_path, DemandWeb(), scoring_v2="shadow")
    cand = eng.enrich_wikipedia_candidate("Kilauea", discovery_origin="wikipedia_search:geography")
    base = eng.score_candidate(dict(cand)).composite_score
    valid, rows, _, scored = eng._score_and_filter([dict(cand)], filter_duplicates=False)
    t = scored[0]
    assert t.composite_score == base and t.discovery_metadata["score_v2"]["version"] == sv2.VERSION
    eng_on = make_engine(tmp_path / "b", DemandWeb(), scoring_v2="on")
    (tmp_path / "b").mkdir(exist_ok=True)
    _, _, _, scored_on = eng_on._score_and_filter([dict(cand)], filter_duplicates=False)
    t2 = scored_on[0]
    assert t2.composite_score == sv2.blended(t2.discovery_metadata["score_v1"], t2.discovery_metadata["score_v2"])


def test_shadow_evidence_is_persisted_for_the_activation_decision(tmp_path):
    """Stage 3.34: shadow v2 scores and the shadow demand summary must reach the ledger and the run notes."""
    from topic_fakes_331 import web_for_every_query
    sqlite3.connect(str(tmp_path / "studio.db")).close()
    eng = make_engine(tmp_path, None, candidate_ledger="on", demand_signals="shadow", scoring_v2="shadow")
    web = DemandWeb(search=web_for_every_query(eng).search, kinds={"Kilauea": "volcano"})
    web.install(eng)
    eng.begin_run("job_shadow")
    eng.discover_and_rank_live_topics(max_candidates=6)
    conn = sqlite3.connect(str(tmp_path / "studio.db"))
    notes = json.loads(conn.execute("SELECT notes_json FROM topic_discovery_runs WHERE run_id='job_shadow'").fetchone()[0])
    assert notes["demand_shadow"]["signals"] >= 1 and "candidates" in notes["demand_shadow"]
    assert notes["scoring_v2"]["mode"] == "shadow" and notes["scoring_v2"]["candidates"]
    assert all({"v1", "v2", "dims"} <= set(c) for c in notes["scoring_v2"]["candidates"])
    rows = [json.loads(r[0] or "{}") for r in conn.execute("SELECT score_breakdown_json FROM topic_candidate_ledger "
                                                          "WHERE score_breakdown_json IS NOT NULL")]
    scored = [r for r in rows if r]
    assert scored and all("_v2" in r and "_v1_score" in r for r in scored)
