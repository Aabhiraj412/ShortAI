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
    rows = _by_name(pa.inventory())
    assert rows["Gemini API"]["status"] == pa.CONFIGURED_HEALTHY
    assert rows["Pexels"]["status"] == pa.KEY_MISSING                # implemented in code, key empty
    assert rows["GNews"]["status"] == pa.NOT_IMPLEMENTED              # key set, but no code calls it
    assert rows["YouTube Analytics API"]["status"] == pa.NOT_IMPLEMENTED
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
