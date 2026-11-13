"""Creative engine dashboard: status without secrets, report listing/detail, card markup."""
import json
from pathlib import Path

import pytest

from backend.app.services import creative_engine_status as ces


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(ces, "MEDIA_DIR", tmp_path)
    film = tmp_path / "test_renders" / "run1" / "popinfo_test_1_x" / "pop_info_film"
    film.mkdir(parents=True)
    (film / "quality_report.json").write_text(json.dumps({
        "video": "v.mp4", "overall_score": 84.5, "passed": True,
        "dimensions": {"hook": {"score": 90, "basis": "measured", "evidence": "e"}}, "issues": [
            {"t": 3.0, "severity": "minor", "dimension": "typography", "problem": "p", "evidence": "e", "fix": "f"}]}),
        encoding="utf-8")
    scripts = film.parent / "scripts"
    scripts.mkdir()
    (scripts / "script_x.json").write_text(json.dumps({"script_source": "extractive_fallback"}), encoding="utf-8")
    return tmp_path


def test_status_reports_presence_only_never_the_key(media, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-very-secret-value")
    s = ces.status()
    blob = json.dumps(s)
    assert "AIza-very-secret-value" not in blob
    assert s["script_generation"]["gemini_key_configured"] is True
    assert s["last_quality"]["overall"] == 84.5 and s["last_quality"]["script_source"] == "extractive_fallback"
    assert s["feature_flag"]["name"] == "CRAZZYDOCS_VISUAL_ENGINE"


def test_status_marks_live_validation_blocked_without_a_key(media, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("backend.app.core.config.settings.GEMINI_API_KEY", "", raising=False)
    s = ces.status()
    assert s["script_generation"]["gemini_key_configured"] is False
    assert s["script_generation"]["live_validation"].startswith("BLOCKED")
    assert s["estimated_cost"]["per_video_usd"] == 0.0


def test_report_detail_round_trip(media):
    rid = ces.status()["reports"][0]["id"]
    d = ces.report_detail(rid)
    assert d["report"]["overall_score"] == 84.5 and d["summary"]["kind"] == "test"
    assert ces.report_detail("000000000000") is None


def test_dashboard_card_renders_text_only():
    html = Path(__file__).resolve().parents[1].joinpath("app", "templates", "dashboard.html").read_text(encoding="utf-8")
    assert 'id="ceCard"' in html and "refreshCreativeEngine" in html and "/api/creative-engine/status" in html
    block = html[html.index("async function ceShowReport"):html.index("function renderStorageFields")]
    assert "innerHTML" not in block                     # report content is rendered with textContent only


def test_report_endpoint_rejects_bad_ids():
    import inspect
    from backend.app import main
    src = inspect.getsource(main.api_creative_engine_report)
    assert "fullmatch" in src and "require_local_control" in inspect.getsource(main.api_creative_engine_status)


def test_status_reports_model_chain_and_provider_health_without_secrets(tmp_path, monkeypatch):
    import json as _json
    from backend.app.services import creative_engine_status as ces
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-secret-value-123")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "gemini-backup")
    monkeypatch.setattr(ces, "MEDIA_DIR", tmp_path, raising=False)
    (tmp_path / "asset_cache").mkdir()
    (tmp_path / "asset_cache" / "provider_health.json").write_text(_json.dumps({"updated": 1, "providers": {
        "Openverse": {"ok": 2, "failed": 3, "latency_ms": [9000, 16000], "consecutive_failures": 1,
                      "last_error": {"kind": "http", "status": 504, "detail": "x"}}}}), encoding="utf-8")
    monkeypatch.setattr("backend.app.core.paths.MEDIA_DIR", tmp_path)
    s = ces.status()
    assert s["script_generation"]["gemini_key_configured"] is True
    assert "gemini-backup" in s["script_generation"]["fallback_models"]
    assert s["photo_providers"]["providers"]["Openverse"]["last_error"] == {"kind": "http", "status": 504}
    assert "AIza-test-secret-value-123" not in _json.dumps(s, default=str)
