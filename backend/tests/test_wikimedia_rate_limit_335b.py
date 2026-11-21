"""Stage 3.35b: Wikimedia rate limiting in topic discovery.

Root cause (2026-11-21): discovery sent about 200 unpaced requests a minute to Wikimedia, which answered HTTP 429
after about 18. Every throttled candidate was recorded as INSUFFICIENT_EVIDENCE, the eligible pool fell to zero
and cycles failed the creative-identity integrity gate. These tests pin the pacing, the Retry-After handling,
the bounded wait budget, and that a throttled candidate is reported as a provider failure, never as missing
evidence. No network: every request goes through fakes."""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from backend.app.content import candidate_ledger as cl
from backend.app.content import curiosity_topic_engine as cte
from topic_fakes_331 import FakeWeb, make_engine


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


class Resp:
    def __init__(self, body=b"{}", status=200):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    hdrs = {"Retry-After": retry_after} if retry_after is not None else {}
    import email.message
    msg = email.message.Message()
    for k, v in hdrs.items():
        msg[k] = v
    return urllib.error.HTTPError("https://en.wikipedia.org/x", code, "err", msg, io.BytesIO(b""))


def opener_from(seq):
    """An urlopen stand-in that raises/returns the queued outcomes in order."""
    calls = []

    def opener(req, timeout=10):
        calls.append(req.full_url)
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    opener.calls = calls
    return opener


def gate(fc, budget=None):
    g = cte.WikimediaGate(clock=fc.clock, sleep=fc.sleep)
    if budget is not None:
        g.reset(budget)
    return g


REQ = urllib.request.Request("https://en.wikipedia.org/api/rest_v1/page/summary/Indian_Ocean")


def test_requests_are_paced_across_calls():
    fc = FakeClock()
    g = gate(fc)
    for _ in range(3):
        g.wait_turn()
    assert fc.sleeps == [g.MIN_INTERVAL] * 2  # first request immediate, then one per MIN_INTERVAL


def test_429_honours_retry_after_then_succeeds():
    fc = FakeClock()
    g = gate(fc)
    op = opener_from([http_error(429, "5"), Resp(b'{"ok": 1}')])
    status, body = g.fetch(REQ, 10, opener=op)
    assert status == 200 and body == b'{"ok": 1}' and len(op.calls) == 2
    assert g.waited == 5.0 and g.stats["retried"] == 1 and g.stats["http_429"] == 1
    assert any(s >= 5.0 for s in fc.sleeps)  # the retry waited for Retry-After


def test_retry_after_is_capped_and_defaulted():
    fc = FakeClock()
    g = gate(fc)
    assert g.backoff("3600") and g.waited == g.MAX_RETRY_AFTER
    assert g.backoff(None) and g.waited == g.MAX_RETRY_AFTER + g.DEFAULT_RETRY_AFTER
    assert g.backoff("not-a-number") and g.waited == g.MAX_RETRY_AFTER + 2 * g.DEFAULT_RETRY_AFTER


def test_gives_up_after_bounded_retries_with_a_typed_error():
    fc = FakeClock()
    g = gate(fc)
    op = opener_from([http_error(429, "1"), http_error(429, "1"), http_error(503, "1")])
    with pytest.raises(cte.WikimediaRateLimited):
        g.fetch(REQ, 10, opener=op)
    assert len(op.calls) == g.RETRIES + 1 and g.stats["gave_up"] == 1


def test_wait_budget_bounds_total_waiting_per_run():
    fc = FakeClock()
    g = gate(fc, budget=15)
    op = opener_from([http_error(429, "10"), http_error(429, "10")])
    with pytest.raises(cte.WikimediaRateLimited):
        g.fetch(REQ, 10, opener=op)
    assert g.waited == 10.0 and g.stats["budget_exhausted"] == 1 and len(op.calls) == 2
    g.reset()                                # a new discovery run gets a fresh budget
    assert g.waited == 0.0 and g.budget == g.BUDGET and not g.stats


def test_other_http_errors_are_not_retried():
    fc = FakeClock()
    g = gate(fc)
    op = opener_from([http_error(404)])
    with pytest.raises(urllib.error.HTTPError):
        g.fetch(REQ, 10, opener=op)
    assert len(op.calls) == 1 and g.waited == 0


@pytest.mark.parametrize("url,ok", [
    ("https://en.wikipedia.org/api/rest_v1/page/summary/X", True),
    ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/2026/11/20", True),
    ("https://commons.wikimedia.org/w/api.php", True),
    ("https://www.wikidata.org/wiki/Q1", True),
    ("https://phys.org/rss-feed/science-news/", False),
    ("https://wikipedia.org.evil.example/x", False),
    ("https://notwikipedia.org/x", False),
])
def test_only_wikimedia_hosts_are_gated(url, ok):
    assert cte._is_wikimedia(url) is ok


def test_engine_fetch_routes_wikimedia_through_the_gate(monkeypatch):
    seen = []

    def fake_fetch(req, timeout, opener=None):
        seen.append(req.full_url)
        return 200, b'{"a": 1}'
    monkeypatch.setattr(cte.WIKIMEDIA_GATE, "fetch", fake_fetch)
    monkeypatch.setattr(cte.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Wikimedia bypassed the gate"))
    assert cte.CuriosityTopicEngine._http_get_json("https://en.wikipedia.org/w/api.php?x=1") == {"a": 1}
    assert seen == ["https://en.wikipedia.org/w/api.php?x=1"]
    # non-Wikimedia hosts are fetched directly, unpaced
    monkeypatch.setattr(cte.urllib.request, "urlopen", lambda req, timeout=0: Resp(b"<rss/>"))
    assert cte.CuriosityTopicEngine._http_get_text("https://phys.org/rss-feed/science-news/") == "<rss/>"
    assert len(seen) == 1


class ThrottledWeb(FakeWeb):
    """Wikimedia keeps answering 429 for the listed titles (after the gate's retries)."""

    def __init__(self, throttled, **kw):
        super().__init__(**kw)
        self.throttled = set(throttled)

    def get_json(self, url, timeout=12.0):
        if any(t.replace(" ", "_") in url or t.replace(" ", "%20") in url for t in self.throttled):
            self.calls.append(url)
            raise cte.WikimediaRateLimited("HTTP 429 from en.wikipedia.org")
        return super().get_json(url, timeout)


def test_throttled_enrichment_is_not_judged(tmp_path):
    web = ThrottledWeb({"Indian Ocean"})
    eng = make_engine(tmp_path, web)
    assert eng.enrich_wikipedia_candidate("Indian Ocean") is None
    assert eng._last_enrich_failure == "rate_limited"
    ok = eng.enrich_wikipedia_candidate("Cotopaxi")
    assert ok and ok["title"] == "Cotopaxi" and eng._last_enrich_failure is None


def test_stub_articles_cost_no_media_list_request(tmp_path):
    web = FakeWeb(kinds={"Tiny": "short"})
    eng = make_engine(tmp_path, web)
    assert eng.enrich_wikipedia_candidate("Tiny") is None and eng._last_enrich_failure is None
    assert web.count("/page/media-list/") == 0
    assert eng.enrich_wikipedia_candidate("Cotopaxi") and web.count("/page/media-list/") == 1


def test_throttled_media_list_is_not_scored_on_partial_data(tmp_path):
    class MediaThrottled(FakeWeb):
        def get_json(self, url, timeout=12.0):
            if "/page/media-list/" in url:
                raise cte.WikimediaRateLimited("HTTP 429")
            return super().get_json(url, timeout)
    eng = make_engine(tmp_path, MediaThrottled())
    assert eng.enrich_wikipedia_candidate("Cotopaxi") is None and eng._last_enrich_failure == "rate_limited"


def test_rejection_reason_distinguishes_rate_limit_from_missing_evidence(tmp_path):
    eng = make_engine(tmp_path, FakeWeb())
    got = []
    eng._trace_reject_new = lambda origin, title, blurb, reason, stage, detail=None, match=None: got.append((reason, stage, detail))
    eng._last_enrich_failure = "rate_limited"
    eng._reject_unenriched("wikipedia_search:geography", "Indian Ocean", "", "enrichment")
    eng._last_enrich_failure = None
    eng._reject_unenriched("wikipedia_search:geography", "Tiny", "", "enrichment")
    assert got[0][0] == cl.PROVIDER_FAILURE and "rate limit" in got[0][2]["rule"]
    assert got[1][0] == cl.INSUFFICIENT_EVIDENCE


def test_begin_run_resets_the_wait_budget(tmp_path):
    eng = make_engine(tmp_path, FakeWeb())
    cte.WIKIMEDIA_GATE.waited = 99.0
    cte.WIKIMEDIA_GATE.stats["gave_up"] = 3
    eng.begin_run("cycle_x")
    snap = cte.WIKIMEDIA_GATE.snapshot()
    assert snap["waited_seconds"] == 0.0 and snap["stats"] == {}
