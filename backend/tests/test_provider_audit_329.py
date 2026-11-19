"""Stage 3.29 provider inventory: status classes, credential detection without values, fallbacks."""
import json

from backend.app.services import provider_audit as pa


def _env_local(tmp_path, monkeypatch, content):
    p = tmp_path / ".env.local"
    p.write_text(content, encoding="utf-8")
    monkeypatch.setattr("backend.app.core.paths.ENV_LOCAL_PATH", p, raising=False)
    for k in ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GNEWS_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_status_classes(tmp_path, monkeypatch):
    _env_local(tmp_path, monkeypatch, "GEMINI_API_KEY=secret-value\nPIXABAY_API_KEY=x\nPEXELS_API_KEY=\nGNEWS_API_KEY=y\n")
    monkeypatch.setattr(pa, "_photo_health", lambda: {"Openverse": {"ok": 0, "failed": 6, "last_error": {"status": 504}},
                                                      "Wikimedia Commons": {"ok": 9, "failed": 0, "last_error": None}})
    monkeypatch.setattr(pa, "_analytics_scope_granted", lambda: False)
    rows = _by_name(pa.inventory(probe_path=tmp_path / "none.json"))
    # Stage 3.30: a populated variable alone is not "healthy"; it is UNVERIFIED until a real request succeeds
    assert rows["Gemini API"]["status"] == pa.UNVERIFIED
    assert rows["Pexels"]["status"] == pa.KEY_MISSING                # implemented in code, key empty
    assert rows["GNews"]["status"] == pa.UNVERIFIED                   # implemented in 3.30, key set, not yet checked
    assert rows["YouTube Analytics API"]["status"] == pa.AUTHORIZATION_REQUIRED   # implemented; scope not granted
    assert rows["Openverse"]["status"] == pa.UNAVAILABLE
    assert rows["Wikimedia Commons"]["status"] == pa.NO_KEY_NEEDED
    assert rows["Sound, illustrations, fonts"]["status"] == pa.NOT_REQUIRED_LOCAL


def test_invalid_credential_is_reported(tmp_path, monkeypatch):
    _env_local(tmp_path, monkeypatch, "PIXABAY_API_KEY=bad\n")
    monkeypatch.setattr(pa, "PROVIDERS", [dict(p, health_key="Pixabay") if p["name"] == "Pixabay" else p for p in pa.PROVIDERS])
    monkeypatch.setattr(pa, "_photo_health", lambda: {"Pixabay": {"ok": 1, "failed": 3, "last_error": {"status": 403}}})
    assert _by_name(pa.inventory())["Pixabay"]["status"] == pa.CONFIGURED_INVALID


def test_no_credential_values_are_ever_returned(tmp_path, monkeypatch):
    _env_local(tmp_path, monkeypatch, "GEMINI_API_KEY=super-secret-123\nPIXABAY_API_KEY=pix-secret-456\n")
    dumped = json.dumps(pa.summary())
    assert "super-secret-123" not in dumped and "pix-secret-456" not in dumped


def test_every_implemented_external_provider_declares_a_fallback():
    for p in pa.PROVIDERS:
        if p["implemented"] and not p.get("local"):
            assert p["fallback"] and p["fallback"] != "-", p["name"]


# ---- Stage 3.30: on-demand probes ---------------------------------------------------------------------------
def test_probe_records_success_failure_category_and_is_throttled(tmp_path):
    from backend.app.media.net import ProviderError
    calls = []

    def ok():
        calls.append("ok")
        return {"ok": True}

    def bad_key():
        raise ProviderError("Pixabay", "http", status=400)

    def outage():
        raise ProviderError("Openverse", "http", status=504)
    reqs = {"gemini": ok, "pixabay": bad_key, "openverse": outage}
    path = tmp_path / "probes.json"
    res = pa.probe(path=path, now=1000.0, requests=reqs)
    assert res["gemini"]["last_success_utc"] and res["pixabay"]["last_failure_category"] == "invalid_credential"
    assert res["openverse"]["last_failure_category"] == "server_error"            # outage, not a credential issue
    again = pa.probe(path=path, now=1100.0, requests=reqs)                         # within 10 min: no new request
    assert again["gemini"]["throttled"] and calls == ["ok"]


def test_probe_results_drive_status_and_missing_vs_outage(tmp_path, monkeypatch):
    _env_local(tmp_path, monkeypatch, "GEMINI_API_KEY=k\nPIXABAY_API_KEY=k\n")
    monkeypatch.setattr(pa, "_photo_health", lambda: {})
    monkeypatch.setattr(pa, "_analytics_scope_granted", lambda: True)
    path = tmp_path / "probes.json"
    path.write_text(json.dumps({
        "gemini": {"recent": [True, True, True], "last_result": {"ok": True}},
        "pixabay": {"recent": [False], "last_result": {"ok": False, "category": "invalid_credential"},
                    "last_failure_category": "invalid_credential"},
        "openverse": {"recent": [True, False, True, False], "last_result": {"ok": False, "category": "server_error"},
                      "last_failure_category": "server_error"}}), encoding="utf-8")
    rows = _by_name(pa.inventory(probe_path=path))
    assert rows["Gemini API"]["status"] == pa.CONFIGURED_HEALTHY and rows["Gemini API"]["usable_by_production"]
    assert rows["Pixabay"]["status"] == pa.CONFIGURED_INVALID and not rows["Pixabay"]["usable_by_production"]
    assert rows["Openverse"]["status"] == pa.INTERMITTENT and rows["Openverse"]["usable_by_production"]
    assert rows["YouTube Analytics API"]["status"] == pa.CONFIGURED_HEALTHY
    assert rows["Pexels"]["credential"] == "missing" and rows["Wikimedia Commons"]["credential"] == "not required"


def test_dashboard_refresh_never_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_probe_requests", lambda: (_ for _ in ()).throw(AssertionError("probe called")))
    monkeypatch.setattr(pa, "_analytics_scope_granted", lambda: None)
    pa.summary()                                                      # must not call any provider
