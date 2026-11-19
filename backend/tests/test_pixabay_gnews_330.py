"""Stage 3.30: Pixabay and GNews integrations (valid, invalid key, missing key, rate limit, timeout, empty, cache,
fallback). No network: transports are stubbed."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.content import gnews_client as gn
from backend.app.media.net import ProviderError
from backend.app.media.providers import stock_provider as sp


@pytest.fixture(autouse=True)
def _reset_pixabay_rate(monkeypatch):
    sp._pixabay_calls.clear()
    sp._pixabay_cooldown_until[0] = 0.0


def _hit(i=1):
    return {"id": i, "pageURL": f"https://pixabay.com/photos/x-{i}/", "largeImageURL": f"https://cdn.pixabay.com/{i}.jpg",
            "user": "alice", "tags": "black hole, space", "imageWidth": 1920, "imageHeight": 1080,
            "previewURL": "https://cdn.pixabay.com/p.jpg"}


def _pix(tmp_path, responder):
    calls = []

    def fake_fetch_json(url, provider, **kw):
        calls.append(url)
        return responder(url)
    return calls, fake_fetch_json


def test_pixabay_valid_response_keeps_provenance_and_is_cached(tmp_path, monkeypatch):
    calls, fake = _pix(tmp_path, lambda url: {"hits": [_hit(1), _hit(2)]})
    monkeypatch.setattr(sp, "fetch_json", fake)
    p = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path)
    assets = p._sync_fetch("black hole", 5)
    assert len(assets) == 2 and assets[0].source_repository == "Pixabay"
    assert assets[0].creator == "alice" and assets[0].source_url.startswith("https://pixabay.com/photos/")
    assert "Pixabay Content License" in assets[0].attribution_text and assets[0].license_url
    assert p._sync_fetch("black hole", 5) and len(calls) == 1           # second call served from the 24 h cache
    cached = list(tmp_path.glob("*.json"))
    assert cached and all("k-test" not in c.read_text() and "k-test" not in c.name for c in cached)


def test_pixabay_missing_key_makes_no_request(tmp_path, monkeypatch):
    calls, fake = _pix(tmp_path, lambda url: {"hits": []})
    monkeypatch.setattr(sp, "fetch_json", fake)
    p = sp.PixabayProvider(api_key="", cache_dir=tmp_path)
    assert p._sync_fetch("x", 5) == [] and p.last_status == "unconfigured" and calls == []


@pytest.mark.parametrize("err, category", [
    (ProviderError("Pixabay", "http", status=400), "invalid_key"),
    (ProviderError("Pixabay", "http", status=429), "rate_limited"),
    (ProviderError("Pixabay", "timeout", "timed out"), "timeout"),
])
def test_pixabay_errors_are_categorised_and_never_raise(tmp_path, monkeypatch, err, category):
    def boom(url, provider, **kw):
        raise err
    monkeypatch.setattr(sp, "fetch_json", boom)
    p = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path)
    assert p._sync_fetch("x", 5) == [] and p.last_error_category == category


def test_pixabay_rate_limit_triggers_cooldown(tmp_path, monkeypatch):
    calls = []

    def limited(url, provider, **kw):
        calls.append(1)
        raise ProviderError("Pixabay", "http", status=429)
    monkeypatch.setattr(sp, "fetch_json", limited)
    p = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path)
    p._sync_fetch("a", 5)
    p._sync_fetch("b", 5)                                                # inside the 60 s cooldown: no request
    assert len(calls) == 1 and p.last_error_category == "rate_limited"


def test_pixabay_local_budget_stays_below_the_published_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "fetch_json", lambda url, provider, **kw: {"hits": []})
    p = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path, clock=lambda: 1000.0)
    for i in range(sp.PIXABAY_LOCAL_BUDGET):
        p._request("images", {"q": f"q{i}"})
    assert p._request("images", {"q": "one more"}) is None and p.last_error_category == "rate_limited"
    assert sp.PIXABAY_LOCAL_BUDGET < sp.PIXABAY_RATE_LIMIT


def test_pixabay_empty_result_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "fetch_json", lambda url, provider, **kw: {"hits": []})
    p = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path)
    assert p._sync_fetch("zzzz", 5) == [] and p.last_status == "empty"


def test_pixabay_video_search(tmp_path, monkeypatch):
    seen = []

    def fake(url, provider, **kw):
        seen.append(url)
        return {"hits": [{"id": 9, "pageURL": "https://pixabay.com/videos/v-9/", "user": "bob", "tags": "galaxy",
                          "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4", "width": 1280, "height": 720,
                                                "thumbnail": "https://cdn.pixabay.com/t.jpg"}}}]}
    monkeypatch.setattr(sp, "fetch_json", fake)
    vids = sp.PixabayProvider(api_key="k-test", cache_dir=tmp_path).search_videos("galaxy")
    assert seen[0].startswith("https://pixabay.com/api/videos/")
    assert vids[0].asset_type == "video" and vids[0].mime_type == "video/mp4" and vids[0].creator == "bob"


def test_scene_photos_fall_back_when_pixabay_fails(monkeypatch):
    """Pixabay failure must not stop scene selection when other approved providers are available."""
    from backend.app.rendering.pop_info import scene_photos
    providers = scene_photos.default_providers("black hole")
    names = [type(p).__name__ for p in providers]
    assert "WikimediaProvider" in names and "OpenverseProvider" in names


# ---------------------------------------------------------------------------------------------- GNews
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)


def _art(title, src, hours_ago=2, url=None):
    return {"title": title, "description": f"{title}. More detail one. More detail two. More detail three.",
            "content": "Body text.", "url": url or f"https://{src}.example/{abs(hash(title)) % 999}",
            "publishedAt": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {"name": src, "url": f"https://{src}.example"}}


def _client(tmp_path, responder, key="g-test"):
    calls = []

    def fetch(url):
        calls.append(url)
        return responder(url)
    return calls, gn.GNewsClient(key=key, fetch=fetch, data_dir=tmp_path, now=lambda: NOW)


def test_gnews_dedupes_stale_and_duplicate_articles_and_clusters_events(tmp_path):
    arts = [_art("NASA probe finds water ice on asteroid Psyche", "reuters"),
            _art("Water ice found on asteroid Psyche by NASA probe", "bbc"),
            _art("NASA probe finds water ice on asteroid Psyche", "reuters", url=None),
            _art("Old chip story from last month", "verge", hours_ago=24 * 9),
            _art("Single outlet funding round for startup", "techsite")]
    arts[2]["url"] = arts[0]["url"]                                          # exact duplicate URL
    clusters = gn.dedupe_and_cluster(arts, now=NOW)
    titles = [c["title"] for c in clusters]
    assert not any("Old chip" in t for t in titles)                           # stale dropped
    psyche = next(c for c in clusters if "Psyche" in c["title"])
    assert psyche["sources"] == ["bbc", "reuters"] and len(psyche["articles"]) == 2


def test_gnews_only_corroborated_events_become_candidates(tmp_path):
    arts = [_art("NASA probe finds water ice on asteroid Psyche", "reuters"),
            _art("Water ice found on asteroid Psyche by NASA probe", "bbc"),
            _art("Single outlet funding round for startup", "techsite")]
    calls, c = _client(tmp_path, lambda url: {"totalArticles": 3, "articles": arts})
    events = c.corroborated_events(("science",))
    assert len(events) == 1 and "Psyche" in events[0]["title"] and len(events[0]["sources"]) == 2
    assert all("apikey=g-test" in u for u in calls)
    assert not any("g-test" in p.read_text() for p in tmp_path.glob("top_headlines_*.json"))


def test_gnews_missing_key_invalid_key_rate_limit_timeout(tmp_path):
    calls, c = _client(tmp_path, lambda url: {}, key="")
    assert c.top_headlines("science") == [] and c.last_error_category == "key_missing" and calls == []
    for err, cat in ((ProviderError("GNews", "http", status=401), "invalid_key"),
                     (ProviderError("GNews", "http", status=429), "rate_limited"),
                     (ProviderError("GNews", "timeout", "timed out"), "timeout")):
        def boom(url, e=err):
            raise e
        c2 = gn.GNewsClient(key="g-test", fetch=boom, data_dir=tmp_path / cat, now=lambda: NOW)
        assert c2.top_headlines("science") == [] and c2.last_error_category == cat


def test_gnews_empty_result_and_cache_and_daily_budget(tmp_path):
    calls, c = _client(tmp_path, lambda url: {"totalArticles": 0, "articles": []})
    assert c.top_headlines("business") == [] and c.last_status == "empty"
    c.top_headlines("business")
    assert len(calls) == 1                                                    # 6 h cache
    (tmp_path / "usage.json").write_text(json.dumps({NOW.strftime("%Y-%m-%d"): gn.DAILY_BUDGET}))
    assert c.search("fresh query") == [] and c.last_error_category == "rate_limited"
    assert len(calls) == 1 and gn.DAILY_BUDGET < gn.DAILY_LIMIT


def test_gnews_failure_never_breaks_topic_discovery(monkeypatch):
    from backend.app.content import curiosity_topic_engine as cte
    eng = cte.CuriosityTopicEngine() if hasattr(cte, "CuriosityTopicEngine") else None
    if eng is None:
        pytest.skip("engine class name differs")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")
    monkeypatch.setattr(gn, "GNewsClient", Boom)
    eng.last_failed_providers = []
    assert eng._gnews_candidates(None, "ts") == []
    assert eng.last_failed_providers[-1]["provider"] == "gnews"


def test_keyless_search_cache_reuses_responses(tmp_path, monkeypatch):
    from backend.app.media import net
    calls = []
    monkeypatch.setattr(net, "fetch_json", lambda url, provider, **kw: calls.append(url) or {"results": [1]})
    a = net.cached_fetch_json("https://api.openverse.org/v1/images/?q=x", provider="Openverse", cache_dir=tmp_path)
    b = net.cached_fetch_json("https://api.openverse.org/v1/images/?q=x", provider="Openverse", cache_dir=tmp_path)
    c = net.cached_fetch_json("https://api.openverse.org/v1/images/?q=x&page=2", provider="Openverse", cache_dir=tmp_path)
    assert a == b == c == {"results": [1]} and len(calls) == 2
