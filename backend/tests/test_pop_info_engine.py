"""
Pop Info engine (pop_info_v1): scene planning, film packaging, sound design, feature flag.
Rendering itself is exercised by the controlled test renders (real MP4s); these tests pin the
contracts those renders depend on. Temporary directories only.
"""
import json
import re
import shutil
import wave
from pathlib import Path

import pytest

from backend.app.rendering.pop_info import scene_planner as sp
from backend.app.rendering.pop_info import pipeline as pl
from backend.app.rendering.pop_info.film_builder import ENGINE_JS, LEMO_ROOT, build_pop_info_film
from backend.app.rendering.pop_info.sound_design import SR, render_stems

SECTIONS = [
    {"id": "seg_01_hook", "stage": "HOOK", "text": "One physics prize pays three times more than a Nobel. Why have most people never heard of it?"},
    {"id": "seg_02_context", "stage": "CONTEXT", "text": "Yuri Milner founded it in 2012, and each award is worth 3 million US dollars."},
    {"id": "seg_03_escalation", "stage": "ESCALATION", "text": "The money goes to theorists working on strings, black holes and the first moments of the universe, because their ideas take decades to prove."},
    {"id": "seg_04_revelation", "stage": "REVELATION", "text": "Some winners have ideas that no experiment can test yet."},
    {"id": "seg_05_payoff", "stage": "PAYOFF", "text": "The biggest reward in physics is for questions, not answers. Subscribe to CrazzyDocs for more."},
]
TIMINGS = [{"id": s["id"], "start_sec": i * 9.0, "end_sec": (i + 1) * 9.0} for i, s in enumerate(SECTIONS)]


def _plan(job="job_a", sections=SECTIONS, photos=()):
    return sp.plan_scenes(job_id=job, topic_title="Breakthrough Prize in Fundamental Physics", category="Science",
                          sections=sections, timings=TIMINGS, total_duration=46.6, photos=photos)


# ------------------------------------------------------------------ planner
def test_numbers_units_and_years_come_from_the_narration():
    nums = sp.extract_numbers("Founded in 2012, each award is worth 3 million US dollars and 76 percent agree.")
    assert [(n.text, n.unit, n.is_year) for n in nums] == [("2012", "", True), ("3", "MILLION US DOLLARS", False), ("76", "%", False)]


def test_key_phrase_never_cuts_a_name_and_highlights_numbers_first():
    h, hi = sp.key_phrase("It was founded in 2012 by Yuri Milner.")
    assert h.endswith("Yuri Milner") and hi == "2012"


def test_scenes_cover_the_timeline_contiguously_and_stay_short():
    plan = _plan()
    scenes = plan["scenes"]
    assert scenes[0]["start_sec"] == 0 and scenes[-1]["end_sec"] >= 45
    for a, b in zip(scenes, scenes[1:]):
        assert abs(a["end_sec"] - b["start_sec"]) < 1e-6
    assert max(s["end_sec"] - s["start_sec"] for s in scenes[:-1]) <= sp.MAX_SCENE_SEC * 1.6


def test_templates_follow_content_and_never_repeat_back_to_back():
    scenes = _plan()["scenes"]
    t = [s["template"] for s in scenes]
    assert t[0] == "title" and t[-1] == "reveal"
    assert "timeline" in t and "stat" in t          # 2012 and 3 million are visualised
    assert all(a != b for a, b in zip(t, t[1:]))
    tr = [s["transition_in"] for s in scenes]
    assert tr[0] == "iris_open" and all(a != b for a, b in zip(tr, tr[1:]))


def test_call_to_action_is_never_the_emphasis_text():
    for s in _plan()["scenes"]:
        assert "subscribe" not in s["headline"].lower()


def test_every_brief_is_complete():
    for s in _plan()["scenes"]:
        for key in ("narration", "main_subject", "supporting_objects", "background", "composition", "headline",
                    "animation_direction", "transition_in", "asset_requirements", "fallback"):
            assert s[key], (s["scene_id"], key)
        assert s["main_subject"] in sp.ICON_KEYWORDS or s["main_subject"] in ("lightbulb", "magnifier", "star")


def test_plan_is_deterministic_per_job_and_varies_between_jobs():
    a1, a2, b = _plan("job_a"), _plan("job_a"), _plan("job_b")
    assert a1 == a2
    assert (a1["seed"] != b["seed"]) and ([s["background"] for s in a1["scenes"]] != [s["background"] for s in b["scenes"]]
                                          or [s["transition_in"] for s in a1["scenes"]] != [s["transition_in"] for s in b["scenes"]])


def test_photo_scene_records_provenance():
    photo = {"path": "x.jpg", "license": "CC BY 4.0", "creator": "A", "provider": "Wikimedia Commons",
             "source_url": "https://commons.wikimedia.org/wiki/File:X.jpg", "sha256": "ab" * 32, "src": "assets/x.jpg"}
    scenes = _plan(photos=[photo])["scenes"]
    ph = [s for s in scenes if s["template"] == "photo"]
    assert ph and ph[0]["asset_requirements"][0].startswith("photo:") and "procedural icon" in ph[0]["fallback"]


# ------------------------------------------------------------------ film package
@pytest.mark.skipif(not (LEMO_ROOT / "core" / "fonts" / "fonts.css").is_file(), reason="Lemo-Opuscar fonts not installed")
def test_film_package_is_self_contained_and_licensed(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    unlicensed = tmp_path / "nolicense.jpg"
    unlicensed.write_bytes(b"\xff\xd8\xff")
    film = build_pop_info_film(prod_dir=tmp_path, job_id="j", topic="Topic", category="Science",
                               script_data={"sections": SECTIONS}, section_timings=TIMINGS, duration_sec=46.6,
                               photos=[{"path": str(img), "license": "CC0 1.0", "creator": "C", "provider": "Openverse"},
                                       {"path": str(unlicensed)}])
    d = Path(film["demo_dir"])
    html = (d / "index.html").read_text(encoding="utf-8")
    assert "window.PLAN" in html and "engine.js" in html and "fonts/fonts.css" in html
    assert not re.search(r"https?://", html.replace("commons.wikimedia.org", ""))     # no network fetches at render
    assert (d / "fonts" / "OFL.md").is_file() and "Fredoka" in (d / "fonts" / "fonts.css").read_text(encoding="utf-8")
    assert len(list((d / "assets").glob("photo_*"))) == 1                          # the unlicensed file was refused
    kinds = {a["kind"] for a in film["asset_provenance"]}
    assert "procedural_vector" in kinds
    assert all(a.get("license") for a in film["asset_provenance"])


def test_engine_js_contract_and_determinism():
    js = ENGINE_JS.read_text(encoding="utf-8")
    for needle in ("window.DUR", "window.render", "window.READY = true", "window.layoutAudit"):
        assert needle in js
    assert "Math.random" not in js                       # every frame is a pure function of t
    assert not re.search(r"https?://", js)
    for template in sp.TEMPLATES:
        assert re.search(rf"\b{template}\(c, sc, u, d\)", js), template
    for icon in sp.ICON_KEYWORDS:
        assert re.search(rf"\b{icon}\(c", js), icon


# ------------------------------------------------------------------ sound
def test_sound_stems_are_synthesized_and_timed(tmp_path):
    plan = _plan()
    stems = render_stems(plan, tmp_path)
    assert stems["synthesized"] and stems["events"] >= len(plan["scenes"]) * 2
    for name in ("sfx", "music"):
        with wave.open(stems["paths"][name]) as w:
            assert w.getframerate() == SR and abs(w.getnframes() / SR - (plan["duration_sec"] + 0.2)) < 0.05
    assert stems["sfx_peak"] < 1.0                        # no clipping before the mix


# ------------------------------------------------------------------ feature flag
def test_feature_flag_defaults_to_legacy(monkeypatch, tmp_path):
    monkeypatch.delenv(pl.FEATURE_FLAG_ENV, raising=False)
    monkeypatch.setattr("backend.app.core.paths.ENV_LOCAL_PATH", tmp_path / "missing.env")
    assert pl.active_visual_engine() == "legacy"


def test_feature_flag_reads_env_and_env_local(monkeypatch, tmp_path):
    env_local = tmp_path / ".env.local"
    env_local.write_text("GEMINI_API_KEY=secret\nCRAZZYDOCS_VISUAL_ENGINE=pop_info_v1\n", encoding="utf-8")
    monkeypatch.setattr("backend.app.core.paths.ENV_LOCAL_PATH", env_local)
    monkeypatch.delenv(pl.FEATURE_FLAG_ENV, raising=False)
    assert pl.active_visual_engine() == "pop_info_v1"
    monkeypatch.setenv(pl.FEATURE_FLAG_ENV, "something_else")
    assert pl.active_visual_engine() == "legacy"           # unknown values never select an engine


def test_orchestrator_keeps_legacy_renderer_as_default_branch():
    from backend.app.orchestration import crazzydocs_master_orchestrator as om
    src = Path(om.__file__).read_text(encoding="utf-8")
    flag = src.index("visual_engine = active_visual_engine()")
    legacy = src.index("film_manifest = LemoOpuscarFilmBuilder.build_film_package(")
    assert flag < src.index('if visual_engine != "legacy":') < src.index("else:", flag) < legacy
    assert "except DuplicateCreativeIdentity:\n                raise" in src


def test_photo_filter_requires_license_verification_and_relevance(tmp_path):
    from types import SimpleNamespace as NS
    good = tmp_path / "g.jpg"; good.write_bytes(b"x")
    base = dict(portrait_path=None, local_path=str(good), verification_status=NS(value="VERIFIED"),
                license_type=NS(value="CC BY"), license_version="4.0", relevance_score=0.8, asset_type="image",
                creator="c", source_repository="Openverse", source_url="u", asset_hash="h", title="t")
    assets = [NS(**base), NS(**{**base, "relevance_score": 0.2}), NS(**{**base, "verification_status": NS(value="PENDING")}),
              NS(**{**base, "license_type": NS(value="")}), NS(**{**base, "local_path": str(tmp_path / "missing.jpg")})]
    out = pl.photos_from_media_assets(assets)
    assert len(out) == 1 and out[0]["license"] == "CC BY 4.0"
