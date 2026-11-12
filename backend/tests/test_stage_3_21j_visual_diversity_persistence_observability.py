"""
Automated Test Suite for Stage 3.21J:
- Visual Diversity & Image Repetition Control (MAX_IMAGE_REUSE <= 2)
- Canonical Workspace Media Storage & Manifest Persistence
- Live 12-Stage Dashboard Observability & FastAPI Endpoints
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fastapi.testclient import TestClient
from PIL import Image
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.content.curiosity_topic_engine import ScoredTopic
from backend.app.content.visual_diversity_policy import (
    MAX_IMAGE_REUSE,
    VisualDiversityValidator,
    compute_asset_sha256,
)
from backend.app.main import app as fastapi_app
from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.documentary_storyboard_builder import build_20_beat_storyboard
from backend.app.rendering.explainer_animation_engine import (
    ExplainerAnimationEngine,
    extract_milestones_from_text,
    extract_numeric_metrics,
)
from backend.app.services.animation_service import probe_video
from backend.app.services.production_observability import (
    PIPELINE_STAGES,
    ProductionObservabilityStore,
    redact_secrets,
)
from backend.app.services.qc_service import QualityControlEngine


def _make_solid_png(path: Path, color: tuple, size: tuple = (800, 600)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    img.save(str(path), format="PNG")
    return str(path)


def _sample_beat(
    beat_id: str,
    visual_type: str,
    asset_path: str,
    asset_hash: str,
    canonical_id: str = "",
    desc: str = "Documented historical evidence chart.",
) -> dict:
    return {
        "beat_id": beat_id,
        "start_sec": 0.0,
        "end_sec": 2.5,
        "script_segment": "Verified historical timeline record from 1958.",
        "visual_type": visual_type,
        "visual_description": desc,
        "asset_path": asset_path,
        "asset_hash": asset_hash,
        "canonical_asset_id": canonical_id or f"cid_{beat_id}",
        "visual_generation_instructions": "Render analytical frame inside safe zone.",
        "transition": "cut",
        "relevance_score": 0.91,
        "reuse_count": 1,
    }


# ---------------------------------------------------------------------------
# 1. Visual Diversity & Repetition Policy Tests
# ---------------------------------------------------------------------------

def test_validator_rejects_third_reuse_same_file(tmp_path):
    p1 = _make_solid_png(tmp_path / "img1.png", (30, 60, 120))
    h1 = compute_asset_sha256(p1)
    beats = [_sample_beat(f"b{i}", "historical_photo", p1, h1, "img_1") for i in range(3)]
    res = VisualDiversityValidator.validate_storyboard(beats, max_image_reuse=MAX_IMAGE_REUSE, check_files_on_disk=True)
    assert not res.is_valid
    assert res.max_reuse_observed == 3
    assert any("maximum allowed: 2" in v for v in res.violations)


def test_validator_catches_duplicate_bytes_under_different_filenames(tmp_path):
    orig = _make_solid_png(tmp_path / "photo_alpha.png", (90, 40, 25))
    copy_a = str(tmp_path / "renamed_copy_a.png")
    copy_b = str(tmp_path / "totally_different_name_b.png")
    shutil.copy2(orig, copy_a)
    shutil.copy2(orig, copy_b)

    beats = [
        _sample_beat("b1", "historical_photo", orig, compute_asset_sha256(orig), "id_orig"),
        _sample_beat("b2", "historical_photo", copy_a, compute_asset_sha256(copy_a), "id_copy_a"),
        _sample_beat("b3", "historical_photo", copy_b, compute_asset_sha256(copy_b), "id_copy_b"),
    ]
    res = VisualDiversityValidator.validate_storyboard(beats, check_files_on_disk=True)
    assert not res.is_valid
    assert res.max_reuse_observed == 3


def test_validator_catches_crop_zoom_canonical_id_reuse(tmp_path):
    beats = [
        _sample_beat("b1", "historical_photo", str(tmp_path / "m_crop1.png"), "", canonical_id="wiki_42_crop1"),
        _sample_beat("b2", "historical_photo", str(tmp_path / "m_zoom2.png"), "", canonical_id="wiki_42_zoom2"),
        _sample_beat("b3", "historical_photo", str(tmp_path / "m_pan3.png"), "", canonical_id="wiki_42_pan3"),
    ]
    res = VisualDiversityValidator.validate_storyboard(beats, check_files_on_disk=False)
    assert not res.is_valid
    assert res.max_reuse_observed == 3


def test_validator_rejects_stick_figures_and_missing_files(tmp_path):
    p1 = _make_solid_png(tmp_path / "ok.png", (10, 20, 30))
    beats = [
        _sample_beat("b1", "animated_diagram", p1, compute_asset_sha256(p1), desc="Cartoon stick figure waves hands."),
        _sample_beat("b2", "historical_photo", str(tmp_path / "missing_file.png"), "deadbeef"),
    ]
    res = VisualDiversityValidator.validate_storyboard(beats, check_files_on_disk=True)
    assert not res.is_valid
    assert any("stick figure" in v.lower() for v in res.violations)
    assert any("missing or empty canonical asset file" in v.lower() for v in res.violations)


def test_explainer_animation_engine_generates_30fps_mp4_and_illustration(tmp_path):
    engine = ExplainerAnimationEngine()
    png_path = str(tmp_path / "assets" / "generated" / "gen_test_01.png")
    g_meta = engine.generate_topic_illustration_png(
        out_path=png_path,
        beat_idx=1,
        total_beats=20,
        stage_label="CONTEXT",
        visual_type_label="generated_image",
        topic_title="Mariana Trench Pressure Study",
        segment_text="In 1960 Trieste reached 10916 meters depth.",
        full_script="In 1960 Trieste reached 10916 meters depth under extreme ocean pressure.",
        accent_color=(56, 189, 248),
        coordinates={"lat": 11.37, "lon": 142.59},
    )
    assert os.path.isfile(png_path)
    assert g_meta["width"] == 1080 and g_meta["height"] == 1920

    mp4_path = str(tmp_path / "assets" / "animations" / "anim_test_map.mp4")
    a_meta = engine.render_explainer_animation_mp4(
        visual_type="animated_map",
        out_mp4_path=mp4_path,
        duration_sec=1.5,
        beat_idx=2,
        total_beats=20,
        stage_label="TWIST",
        topic_title="Mariana Trench Pressure Study",
        segment_text="At 10916 meters pressure hits 1086 bars.",
        full_script="In 1960 Trieste descended in the Pacific Ocean to 10916 meters.",
        accent_color=(245, 194, 66),
        coordinates={"lat": 11.37, "lon": 142.59},
        fps=30.0,
    )
    assert os.path.isfile(mp4_path)
    assert a_meta["width"] == 1080 and a_meta["height"] == 1920
    probed = probe_video(mp4_path)
    assert probed["width"] == 1080 and probed["height"] == 1920
    assert abs(float(probed["fps"]) - 30.0) <= 1.0


def test_storyboard_allocation_with_only_2_photos_achieves_high_diversity(tmp_path, monkeypatch):
    prod_id = "test_div_2_photos"
    ws = ProductionWorkspace(
        production_id=prod_id,
        topic_id="t_div",
        topic_title="Subterranean Lake Vostok Discovery",
        category="Science",
        base_dir=str(tmp_path / "media" / "productions"),
    )
    ws.initialize_workspace()

    p1 = _make_solid_png(Path(ws.assets_acquired_dir) / "vostok_1.png", (40, 80, 120))
    p2 = _make_solid_png(Path(ws.assets_acquired_dir) / "vostok_2.png", (120, 70, 30))

    assets = [
        MediaAsset(
            asset_id="wiki_v1",
            title="Vostok Ice Core",
            creator="NSF Archive",
            source_repository="Wikimedia Commons",
            source_url="https://commons.wikimedia.org/wiki/File:V1.jpg",
            download_url="https://upload.wikimedia.org/v1.png",
            license_type=LicenseType.PUBLIC_DOMAIN,
            attribution_text="Public domain archive photo",
            local_path=p1,
            asset_hash=compute_asset_sha256(p1),
            verification_status=LicenseStatus.APPROVED,
            width=800,
            height=600,
        ),
        MediaAsset(
            asset_id="wiki_v2",
            title="Antarctic Radar Map",
            creator="NSF Archive",
            source_repository="Wikimedia Commons",
            source_url="https://commons.wikimedia.org/wiki/File:V2.jpg",
            download_url="https://upload.wikimedia.org/v2.png",
            license_type=LicenseType.PUBLIC_DOMAIN,
            attribution_text="Public domain archive map",
            local_path=p2,
            asset_hash=compute_asset_sha256(p2),
            verification_status=LicenseStatus.APPROVED,
            width=800,
            height=600,
        ),
    ]

    topic = ScoredTopic(
        id="t_div",
        title="Subterranean Lake Vostok Discovery",
        category="Science",
        category_code="S",
        summary="Sealed beneath 4 kilometers of Antarctic ice for 15 million years.",
        curiosity=9.2,
        surprise=9.0,
        storytelling=9.1,
        visual_potential=9.0,
        emotional_connection=8.5,
        factual_reliability=9.5,
        composite_score=9.1,
        sources=["https://en.wikipedia.org/wiki/Lake_Vostok"],
        key_entities=["Lake Vostok", "Antarctica", "Vostok Station"],
        coordinates={"lat": -77.5, "lon": 106.0},
    )
    sections = [
        ("HOOK", "Four kilometers beneath Antarctica lies a liquid lake sealed for 15 million years."),
        ("CONTEXT", "In 1996 radar mapping in Russia confirmed the 250 km basin under Vostok Station."),
        ("TWIST", "Liquid water survives at minus 3 degrees Celsius under 380 atmospheres of ice pressure."),
        ("CLIMAX", "In 2012 drilling reached the surface boundary without contaminating the ecosystem."),
        ("CLOSING", "Lake Vostok offers a living analog for icy oceans on Europa and Enceladus."),
    ]
    timings = []
    cur = 0.0
    for idx, (st, txt) in enumerate(sections):
        timings.append({
            "id": f"sec_{idx}",
            "stage": st,
            "text": txt,
            "start_sec": cur,
            "end_sec": cur + 10.5,
            "duration_sec": 10.5,
        })
        cur += 10.5

    vo_path = str(Path(ws.audio_dir) / "vo.mp3")
    Path(vo_path).write_bytes(b"ID3test")

    plan, gate_beats, ass_path = build_20_beat_storyboard(
        topic=topic,
        timings=timings,
        total_voice_dur=52.5,
        voiceover_path=vo_path,
        acquired_assets=assets,
        prod_ws=ws,
    )

    assert len(plan.scenes) == 20
    assert len(gate_beats) == 20
    assert os.path.isfile(ass_path)
    sb_beats = plan.metadata["storyboard_beats"]
    audit = VisualDiversityValidator.validate_storyboard(
        sb_beats,
        topic_title=topic.title,
        max_image_reuse=2,
        check_files_on_disk=True,
        min_distinct_assets=12,
        workspace_root=ws.prod_dir,
    )
    assert audit.is_valid, f"Unexpected violations: {audit.violations}"
    assert audit.max_reuse_observed <= 2
    assert audit.distinct_assets >= 15
    assert os.path.isdir(ws.assets_generated_dir) and len(os.listdir(ws.assets_generated_dir)) >= 4
    assert os.path.isdir(ws.assets_animations_dir) and len(os.listdir(ws.assets_animations_dir)) >= 8
    assert os.path.isfile(os.path.join(ws.storyboards_dir, f"storyboard_{prod_id}.json"))
    assert os.path.isfile(ws.manifest_path)


def test_milestone_and_metric_extraction_helpers():
    text = "In 1912 tectonic drift began; by 1968 420 km shifted 85 percent faster in 2004."
    milestones = extract_milestones_from_text(text, "Tectonic Drift")
    metrics = extract_numeric_metrics(text, "Tectonic Drift")
    assert any(yr == "1912" for yr, _ in milestones)
    assert any("km" in u.lower() or "percent" in u.lower() for u, _, _ in metrics)


# ---------------------------------------------------------------------------
# 2. Canonical Persistence, QC & Integrity Gate Enforcement Tests
# ---------------------------------------------------------------------------

def test_workspace_registers_full_canonical_manifest_metadata(tmp_path):
    ws = ProductionWorkspace(
        production_id="test_manifest_01",
        topic_id="t_m1",
        topic_title="Deep Sea Hydrothermal Vents",
        category="Science",
        base_dir=str(tmp_path / "media" / "productions"),
    )
    ws.initialize_workspace()
    for folder in (
        ws.assets_acquired_dir,
        ws.assets_generated_dir,
        ws.assets_animations_dir,
        ws.audio_dir,
        ws.storyboards_dir,
        ws.visual_timeline_dir,
        ws.thumbnails_dir,
        ws.video_dir,
        ws.reports_dir,
        ws.audit_dir,
    ):
        assert os.path.isdir(folder)

    gen_png = _make_solid_png(Path(ws.assets_generated_dir) / "vent_diagram.png", (15, 25, 45), size=(1080, 1920))
    art = ws.register_artifact(
        "STAGE_6_VISUAL_ASSETS",
        "generated_asset",
        gen_png,
        asset_id="gen_vent_01",
        source_url="local://generated",
        creator="CrazzyDocs",
        license="CC0",
        generation_provider="Pillow",
        prompt_metadata={"prompt": "Hydrothermal vent diagram"},
        width=1080,
        height=1920,
        format="png",
    )
    assert art.sha256 == compute_asset_sha256(gen_png)
    assert art.width == 1080 and art.height == 1920
    with open(ws.manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["manifest_version"] == "3.21J"
    assert manifest["asset_summary"]["generated_images"] == 1


def test_content_integrity_gate_blocks_excessive_image_reuse(tmp_path):
    ws_dir = tmp_path / "prod_gate_fail"
    img = _make_solid_png(ws_dir / "assets" / "acquired" / "single.png", (100, 100, 100))
    vid = ws_dir / "video" / "out.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"fake_mp4_bytes")
    thumb = _make_solid_png(ws_dir / "thumbnails" / "th.png", (20, 20, 20), size=(1080, 1920))

    beats_3x = [
        _sample_beat(f"b{i}", "historical_photo", img, compute_asset_sha256(img), "single")
        for i in range(4)
    ]

    class FakeBeat:
        def __init__(self, idx):
            self.id = f"b{idx}"
            self.image_filename = "single.png"
            self.visual_type = "historical_photo"

    gate = ContentIntegrityGate(workspace_root=str(PROJECT_ROOT))
    rep = gate.validate_production(
        topic_title="Deep Sea Hydrothermal Vents",
        category="Science",
        narration_text="Deep sea hydrothermal vents support chemosynthetic life along tectonic ridges.",
        visual_beat_configs=[FakeBeat(i) for i in range(4)],
        video_path=str(vid),
        thumbnail_path=thumb,
        thumbnail_headline="HIDDEN LIFE",
        metadata_title="Deep Sea Hydrothermal Vents #shorts",
        metadata_description="Deep sea hydrothermal vents support chemosynthetic life along ocean ridges.",
        metadata_tags=["science", "ocean", "hydrothermal", "vents", "deep sea"],
        attribution_text="Source: Wikimedia Commons Public Archive",
        storyboard_beats=beats_3x,
        production_workspace_dir=str(ws_dir),
    )
    assert not rep.is_valid
    assert any(c.check_name == "CHECK_N_VISUAL_DIVERSITY" and not c.passed for c in rep.checks)


def test_qc_engine_blocks_missing_canonical_asset(tmp_path):
    from backend.app.core.paths import FFMPEG_DEFAULT_PATH

    vid_path = str(tmp_path / "sample_53s.mp4")
    thumb_path = _make_solid_png(tmp_path / "th.png", (50, 50, 50), size=(1080, 1920))
    subprocess.run(
        [
            FFMPEG_DEFAULT_PATH,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101828:s=1080x1920:r=30:d=52",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "52",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            vid_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    qc = QualityControlEngine()
    missing_asset_beats = [
        _sample_beat("b1", "generated_image", str(tmp_path / "ghost.png"), "abcd1234")
    ]
    passed, errors, checks = qc.run_qc_checks(
        video_path=vid_path,
        thumbnail_path=thumb_path,
        metadata={"title": "Test Title #shorts", "description": "Valid test description"},
        target_fps=30.0,
        storyboard_beats=missing_asset_beats,
        workspace_dir=str(tmp_path),
    )
    assert not passed
    assert "FAIL" in checks["visual_diversity_reuse"]
    assert any("missing or empty canonical asset file" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# 3. Live Observability, Persistence, Isolation & Dashboard API Tests
# ---------------------------------------------------------------------------

def test_observability_tracks_all_12_stages_and_persists_across_restart(tmp_path):
    prod_store = str(tmp_path / "prod_progress.json")
    test_store = str(tmp_path / "test_progress.json")
    event_log = str(tmp_path / "reports" / "progress_events_run1.jsonl")

    obs1 = ProductionObservabilityStore(store_path=prod_store, test_store_path=test_store)
    obs1.start_production(
        "prod_obs_01",
        topic="Voyager Golden Record",
        execution_context="PRODUCTION",
        event_log_path=event_log,
    )

    for stage_key, _ in PIPELINE_STAGES[:-1]:
        obs1.start_stage("prod_obs_01", stage_key, f"Running {stage_key}", progress=50.0)
        obs1.complete_stage("prod_obs_01", stage_key, f"Completed {stage_key}")

    obs1.update_assets(
        "prod_obs_01",
        unique_images=4,
        generated_images=5,
        animations=11,
        failed_acquisitions=1,
        reused_images=2,
        status="COMPLETE",
    )
    obs1.update_creative_intelligence(
        "prod_obs_01",
        visual_style_distribution={"documentary": 8, "pop_info": 7, "chalk_talk": 5},
        visual_diversity={
            "total_visual_beats": 20,
            "unique_photographic_assets": 8,
            "reused_source_images": 0,
            "near_duplicate_groups": 0,
            "max_source_photo_reuse": 1,
        },
    )
    obs1.update_rendering(
        "prod_obs_01",
        current_operation="Muxing final Short",
        current_beat_index=20,
        current_beat_id="b20",
        completed_scenes=20,
        total_scenes=20,
        progress_pct=100.0,
        ffmpeg_status="COMPLETED",
        output_path="media/productions/prod_obs_01/video/short.mp4",
    )
    obs1.update_thumbnail(
        "prod_obs_01",
        status="completed",
        image_generation_status="completed",
        composition_status="completed",
        text_layout_status="completed",
        validation_status="completed",
        headline="GOLDEN RECORD",
        preview_url="/api/dashboard/productions/prod_obs_01/thumbnail",
    )
    obs1.complete_production("prod_obs_01", "Finished Voyager Short")

    # Simulate fresh process restart loading strictly from disk
    obs2 = ProductionObservabilityStore(store_path=prod_store, test_store_path=test_store)
    loaded = obs2.get_production("prod_obs_01")
    assert loaded is not None
    assert loaded["overall_status"] == "completed"
    assert loaded["overall_progress"] == 100.0
    assert loaded["assets"]["animations"] == 11
    assert loaded["rendering"]["completed_scenes"] == 20
    assert loaded["thumbnail"]["validation_status"] == "completed"
    assert loaded["creative"]["visual_style_distribution"] == {
        "documentary": 8,
        "pop_info": 7,
        "chalk_talk": 5,
    }
    assert loaded["creative"]["visual_diversity"]["reused_source_images"] == 0
    assert os.path.isfile(event_log)
    with open(event_log, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert any(ev["event_type"] == "PRODUCTION_COMPLETED" for ev in lines)


def test_observability_isolates_test_runs_and_redacts_secrets(tmp_path):
    prod_store = str(tmp_path / "prod_progress.json")
    test_store = str(tmp_path / "test_progress.json")
    obs = ProductionObservabilityStore(store_path=prod_store, test_store_path=test_store)

    obs.start_production("test_isolated_01", topic="Secret Bearer sk_live_abc1234567890", execution_context="TEST")
    obs.fail_production(
        "test_isolated_01",
        stage="asset_acquisition",
        error_summary="HTTP 401 with api_key=MYSECRETKEY99999 failed",
        retry_possible=True,
        safe_to_resume=True,
    )

    # Normal production dashboard payload excludes TEST runs by default
    prod_payload = obs.get_dashboard_payload(include_tests=False)
    assert prod_payload["displayed_production"] is None
    assert len(prod_payload["history"]) == 0

    test_rec = obs.get_production("test_isolated_01", include_tests=True)
    assert test_rec is not None
    assert test_rec["overall_status"] == "failed"
    assert test_rec["error"]["stage"] == "asset_acquisition"
    assert test_rec["error"]["retry_possible"] is True
    assert "MYSECRETKEY99999" not in test_rec["error"]["summary"]
    assert "sk_live_abc1234567890" not in test_rec["topic"]
    assert "[REDACTED]" in redact_secrets("Authorization: Bearer abcdef1234567890xyz")


def test_fastapi_observability_endpoints_and_dashboard_html(tmp_path, monkeypatch):
    from backend.app.services import production_observability as obs_mod

    temp_obs = ProductionObservabilityStore(
        store_path=str(tmp_path / "api_prod.json"),
        test_store_path=str(tmp_path / "api_test.json"),
    )
    temp_obs.start_production("val_api_01", topic="Tunguska Event 1908", execution_context="VALIDATION")
    temp_obs.update_assets("val_api_01", unique_images=5, generated_images=4, animations=11, reused_images=2)
    temp_obs.update_creative_intelligence(
        "val_api_01",
        visual_style_distribution={"documentary": 4, "creator_motion_graphics": 3},
        visual_diversity={"total_visual_beats": 7, "reused_source_images": 0},
    )
    temp_obs.update_rendering("val_api_01", completed_scenes=7, total_scenes=20, current_beat_index=8, current_beat_id="b08", ffmpeg_status="ENCODING")

    monkeypatch.setattr(obs_mod, "production_observability", temp_obs)

    client = TestClient(fastapi_app)
    r_prog = client.get("/api/dashboard/progress")
    assert r_prog.status_code == 200
    body = r_prog.json()
    assert body["has_active"] is True
    assert body["active_production"]["production_id"] == "val_api_01"
    assert body["active_production"]["assets"]["animations"] == 11
    assert body["active_production"]["creative"]["visual_style_distribution"] == {
        "documentary": 4,
        "creator_motion_graphics": 3,
    }
    assert body["active_production"]["creative"]["visual_diversity"]["reused_source_images"] == 0
    assert len(body["stages_schema"]) == 12

    r_act = client.get("/api/dashboard/progress/active")
    assert r_act.status_code == 200 and r_act.json()["has_active"] is True

    r_single = client.get("/api/dashboard/progress/val_api_01")
    assert r_single.status_code == 200 and r_single.json()["rendering"]["completed_scenes"] == 7

    r_hist = client.get("/api/dashboard/progress/history")
    assert r_hist.status_code == 200 and len(r_hist.json()["productions"]) == 1

    r_html = client.get("/dashboard")
    assert r_html.status_code == 200
    assert "liveObservabilityPanel" in r_html.text
    assert "Live Production Observability (12-Stage Telemetry)" in r_html.text
    assert "Recent Production Observability Runs" not in r_html.text
    assert "/api/dashboard/progress" in r_html.text


def test_completed_validation_worker_is_rendered_idle():
    html_src = Path(r"D:\Agents\CrazzyDocs\backend\app\templates\dashboard.html").read_text(encoding="utf-8")
    assert "const reportedValState = String(overviewRes.validation_worker_state || 'IDLE').toUpperCase();" in html_src
    assert "const valState = reportedValState === 'COMPLETED' ? 'IDLE' : reportedValState;" in html_src
