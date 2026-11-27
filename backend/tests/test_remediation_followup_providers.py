"""Remediation follow-up: Pixabay HTTP 400s were misreported as invalid_key; the real cause was overlong queries.

Production log 2026-11-27 05:04: 'Pixabay images query failed: invalid_key' (HTTP 400) seconds before a successful
Pixabay download with the same key; the queries were whole headlines ('How much CO₂ will stay? New study ...').
No network: the HTTP layer is faked.
"""
from __future__ import annotations

from backend.app.media.net import ProviderError
from backend.app.media.providers import stock_provider as sp
from backend.app.services import provider_audit as pa


def test_queries_are_sanitised_to_the_pixabay_limit():
    q = sp.pixabay_query("How much CO₂ will stay? New study estimates the long-term carbon budget of the world's "
                         "oceans under several warming scenarios")
    assert len(q) <= 100 and "CO2" in q and "?" not in q and "'" not in q
    assert not q.endswith(" ") and q.split()[-1] in q            # cut on a word boundary
    assert sp.pixabay_query("  café   noir  ") == "cafe noir"


def _provider(tmp_path, monkeypatch, responses):
    monkeypatch.setattr(sp, "_pixabay_cache_dir", lambda: tmp_path)
    sent = []

    def fake_fetch_json(url, **kw):
        sent.append(url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(sp, "fetch_json", fake_fetch_json)
    prov = sp.PixabayProvider(api_key="test-key")
    prov.clock = lambda: 1000.0 + len(sent)
    return prov, sent


def test_http_400_after_a_success_is_a_bad_query_not_an_invalid_key(tmp_path, monkeypatch):
    prov, sent = _provider(tmp_path, monkeypatch, [{"hits": []}, ProviderError("Pixabay", "http", status=400)])
    assert prov._request("images", {"q": "moon"}) == {"hits": []}
    assert prov._request("images", {"q": "x" * 300}) is None
    assert prov.last_error_category == "bad_query"
    assert all("q=" + "x" * 101 not in u for u in sent)                  # the long query was cut before sending


def test_http_400_without_a_prior_success_is_ambiguous(tmp_path, monkeypatch):
    prov, _ = _provider(tmp_path, monkeypatch, [ProviderError("Pixabay", "http", status=400)])
    assert prov._request("images", {"q": "moon"}) is None
    assert prov.last_error_category == "invalid_key_or_query"


def test_401_is_still_an_invalid_key(tmp_path, monkeypatch):
    prov, _ = _provider(tmp_path, monkeypatch, [ProviderError("Pixabay", "http", status=401)])
    prov._request("images", {"q": "moon"})
    assert prov.last_error_category == "invalid_key"


def test_degraded_provider_gets_an_actionable_reason():
    row = {"key": "pixabay", "status": pa.INTERMITTENT, "rate_limited": False, "error_category": "bad_query",
           "recent_health": {"ok": 17, "failed": 3, "last_error": {"kind": "http", "status": 400}}}
    state, why = pa.state_with_evidence(row, {}, 11)
    assert state == "DEGRADED" and "bad_query (HTTP 400)" in why and "sanitised" in why


def test_missing_pexels_key_is_surfaced_not_hidden():
    row = {"key": "pexels", "status": pa.KEY_MISSING, "rate_limited": False, "recent_health": None}
    assert pa.state_with_evidence(row, {}, 0)[0] == "KEY_MISSING"
    from backend.app.services import operator_attention as oa
    from test_autonomy_control_plane_332b import make_cp
    import tempfile, pathlib
    cp = make_cp(pathlib.Path(tempfile.mkdtemp()), startup=False, policy=True)
    a = oa.attention(cp=cp, providers=lambda: {"providers": [{"name": "Pexels", "state": "KEY_MISSING", "production": True,
                                                              "env": "PEXELS_API_KEY", "fallback": "other photo providers"}]},
                     creative=lambda: {}, topic_supply=lambda: {}, localizations=lambda: {})
    # Surfaced, never hidden: with a working fallback it is an optional improvement, not a blocking request.
    assert any(n["kind"] == "credential" and "PEXELS_API_KEY" in n["action"] for n in a["optional_improvements"])
    assert not any("PEXELS_API_KEY" in (n.get("action") or "") for n in a["needs_you"])
