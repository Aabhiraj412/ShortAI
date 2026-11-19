"""
Stage 3.29 production-vs-test parity: one approved configuration, production differences surfaced,
every render writes a complete manifest. No rendering, no live DB.
"""
import json

import pytest

from backend.app.rendering import render_parity as rp
from backend.app.rendering.pop_info import pipeline as pl


@pytest.fixture
def flag(monkeypatch, tmp_path):
    env_local = tmp_path / ".env.local"
    env_local.write_text("", encoding="utf-8")
    monkeypatch.setattr("backend.app.core.paths.ENV_LOCAL_PATH", env_local, raising=False)

    def set_flag(value):
        if value is None:
            monkeypatch.delenv(pl.FEATURE_FLAG_ENV, raising=False)
        else:
            monkeypatch.setenv(pl.FEATURE_FLAG_ENV, value)
    return set_flag


def test_legacy_flag_is_reported_as_a_parity_difference(flag):
    flag(None)
    rep = rp.parity_report()
    assert rep["matches"] is False
    keys = {d["key"] for d in rep["differences"]}
    assert {"visual_engine", "engine_revision", "scene_photo_selection", "illustrated_environments"} <= keys
    assert rep["production"]["feature_flag"]["value"] == "legacy"


def test_pop_info_flag_with_approved_options_matches(flag):
    flag("pop_info_v1")
    rep = rp.parity_report()
    assert rep["matches"] is True, rep["differences"]
    assert rep["production"]["feature_flag"]["source"] == "environment"


def test_unknown_flag_value_falls_back_to_legacy_and_is_surfaced(flag):
    flag("something_else")
    assert pl.active_visual_engine() == "legacy"
    assert rp.parity_report()["matches"] is False


def test_switching_off_scene_photos_breaks_parity(flag, monkeypatch):
    flag("pop_info_v1")
    monkeypatch.setitem(rp.APPROVED_RENDER_OPTIONS, "select_photos", False)
    assert "scene_photo_selection" in {d["key"] for d in rp.parity_report()["differences"]}


def test_production_branch_and_test_script_use_the_same_options():
    from pathlib import Path
    root = Path(rp.__file__).resolve().parents[3]
    orch = (root / "backend/app/orchestration/crazzydocs_master_orchestrator.py").read_text(encoding="utf-8")
    script = (root / "backend/scripts/render_pop_info_test_videos.py").read_text(encoding="utf-8")
    assert 'select_photos=_approved["select_photos"]' in orch and "key_entities=list(" in orch
    assert "render_parity.log_parity(job_id)" in orch
    assert "APPROVED_RENDER_OPTIONS" in script


def test_manifest_is_complete_for_both_engines(tmp_path, flag):
    flag(None)
    film_pop = {"renderer": "lemo-opuscar", "asset_provenance": [{"kind": "procedural_vector"}],
                "plan": {"engine_revision": "3.28", "scenes": [{"template": "title"}, {"template": "stat"}],
                         "fingerprints": {"visual_modes": ["illustration", "diagram"]}, "direction": {"primary_environment": "lab"}}}
    m = rp.write_render_manifest(tmp_path / "m.json", job_id="j", execution_context="VALIDATION", visual_engine="pop_info_v1",
                                 script_data={"full_script": "one two three", "script_source": "llm:gemini"},
                                 film=film_pop, quality={"overall": 90}, measured_duration_sec=45.0, video_path="v.mp4")
    for k in rp.REQUIRED_MANIFEST_KEYS:
        assert k in m and m[k] not in (None, ""), k
    assert m["scene_types"] == ["title", "stat"] and m["duration"]["accepted"] is True
    assert m["script_fingerprint"]["words"] == 3
    legacy = rp.write_render_manifest(tmp_path / "l.json", job_id="k", execution_context="PRODUCTION", visual_engine="legacy",
                                      script_data={}, film={"beats": [{"asset": "assets/beat_01_anim_x.mp4"}, {"asset": "b.png"}]},
                                      quality=None, measured_duration_sec=49.2, video_path="v.mp4")
    assert legacy["scene_types"] == ["legacy:animation", "legacy:still"]
    assert legacy["parity"]["matches"] is False and legacy["engine_revision"] == "legacy_film_builder"
    assert json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))["duration"]["accepted"] is True


def test_manifest_reads_pop_info_plan_from_plan_path(tmp_path, flag):
    flag("pop_info_v1")
    plan = {"engine_revision": "3.28", "scenes": [{"template": "title"}, {"template": "reveal"}],
            "fingerprints": {"visual_modes": ["a"]}, "direction": {"pacing": "fast", "principles": ["x"]}}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    m = rp.write_render_manifest(tmp_path / "m.json", job_id="j", execution_context="VALIDATION",
                                 visual_engine="pop_info_v1", script_data={"script": "a b c"},
                                 film={"plan_path": str(tmp_path / "plan.json")}, quality=None,
                                 measured_duration_sec=45.0, video_path="v.mp4")
    assert m["scene_types"] == ["title", "reveal"]
    assert m["engine_revision"] == "3.28"
    assert m["creative_direction"] == {"pacing": "fast"}
