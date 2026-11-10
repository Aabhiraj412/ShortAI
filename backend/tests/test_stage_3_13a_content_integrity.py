"""
Automated Test Suite for Phase 5, Stage 3.13A Content Integrity Gate.

Validates:
1. Rejection of semantic and visual cross-contamination (nuclear assets/terms in Vesna Vulović story).
2. Enforcement of title integrity (<= 100 chars, no arbitrary ellipsis truncation).
3. Enforcement of description substance and factual alignment.
4. Attribution verification against authentic historical sources.
5. Rejection of identical video byte copies of prior productions (Stale Asset Gate).
6. 100% pass verification on authentic Vesna Vulović deliverable assets.
7. End-to-end integration with CrazzyDocsMasterOrchestrator.
"""

import json
import os
import shutil
import pytest

from backend.app.content.content_integrity_gate import ContentIntegrityGate, compute_sha256
from backend.app.rendering.engines.vesna_fast_renderer import BEAT_CONFIGS as VESNA_BEAT_CONFIGS, VisualBeatConfig
from backend.app.services.youtube_publishing_engine import (
    MetadataGenerator,
    YouTubeMetadata,
    compute_file_sha256,
)
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator

WORKSPACE = r"D:\Agents\CrazzyDocs"
VESNA_IMG_DIR = os.path.join(WORKSPACE, "media", "images", "vesna_vulovic")
VESNA_PROD_DIR = os.path.join(WORKSPACE, "media", "productions", "prod_20261110_vesna_vulovic")
VIDEO_DIR = os.path.join(WORKSPACE, "media", "video")
THUMB_DIR = os.path.join(WORKSPACE, "media", "thumbnails")


@pytest.fixture
def integrity_gate():
    return ContentIntegrityGate(workspace_root=WORKSPACE)


@pytest.fixture
def vesna_valid_data():
    """Provides valid editorial assets for the Vesna Vulović production."""
    manifest_path = os.path.join(VESNA_PROD_DIR, "assets", "attribution_manifest.json")
    if not os.path.isfile(manifest_path):
        manifest_path = os.path.join(VESNA_IMG_DIR, "attribution_manifest.json")
    manifest_assets = []
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
            manifest_assets = raw if isinstance(raw, list) else raw.get("assets", [])

    meta = MetadataGenerator.generate_shorts_metadata(
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        opening_hook="She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
        category="Unbelievable Real-Life Stories",
        key_entities=["Vesna Vulovic", "JAT Flight 367", "Czechoslovakia", "Srbska Kamenice", "Guinness World Record"],
        story_summary=(
            "In 1972, flight attendant Vesna Vulovic survived a 33,330-foot fall after JAT Flight 367 broke apart. "
            "Pinned in the tail by a food cart, she landed on a snow-covered mountain slope in Czechoslovakia."
        ),
        sources=[
            "Guinness World Records (Highest Fall Survived Without Parachute)",
            "Czechoslovak Civil Aviation Administration Official Crash Report (1972)",
            "Prague Central Military Hospital Medical Dossier",
        ],
        attributions=manifest_assets,
    )

    narration = (
        "She fell 33,000 feet without a parachute—and walked out of the hospital alive. "
        "In 1972, flight attendant Vesna Vulović boarded JAT Flight 367 over Czechoslovakia. "
        "At 33,000 feet, a briefcase bomb detonated, tearing the airliner apart in mid-air. "
        "While passengers were sucked into the sky, Vesna was trapped in the tail, pinned by a food cart. "
        "Plummeting at terminal velocity, the wreckage slammed into a snowy mountain, cushioned by dense pines. "
        "A wartime medic, Bruno Honke, found her in the freezing forest and kept her alive until help arrived. "
        "Despite a fractured skull and crushed spine, she miraculously learned to walk again. "
        "Guinness officially confirmed her world record: the highest fall survived without a parachute."
    )

    return {
        "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        "category": "Unbelievable Real-Life Stories",
        "narration_text": narration,
        "visual_beat_configs": VESNA_BEAT_CONFIGS,
        "video_path": os.path.join(VESNA_PROD_DIR, "video", "showcase_vesna_vulovic_verified.mp4"),
        "thumbnail_path": os.path.join(VESNA_PROD_DIR, "thumbnails", "thumb_vesna_vulovic_verified.png"),
        "thumbnail_headline": "SURVIVED 33,000 FT",
        "metadata": meta,
        "manifest_assets": manifest_assets,
    }


def test_metadata_generator_title_length_and_no_arbitrary_truncation():
    """Verifies that titles stay <= 100 chars and never end with '...'."""
    long_title = "The Incredible Long Historical Tale: A Very Long Detailed Description of an Extraordinary Survival Event in History #ExtraWords"
    meta = MetadataGenerator.generate_shorts_metadata(
        topic_title=long_title,
        opening_hook="Did you know this happened?",
        category="History",
        key_entities=["History"],
    )
    assert len(meta.title) <= 100
    assert not meta.title.endswith("...")
    assert "#Shorts" in meta.title


def test_metadata_validation_against_story(vesna_valid_data):
    """Verifies MetadataGenerator.validate_metadata_against_story accepts valid metadata."""
    data = vesna_valid_data
    is_valid, issues = MetadataGenerator.validate_metadata_against_story(
        metadata=data["metadata"],
        topic_title=data["topic_title"],
        story_text=data["narration_text"],
        sources=["Guinness World Records", "Czechoslovak Civil Aviation Administration"],
    )
    assert is_valid, f"Metadata validation failed: {issues}"
    assert len(issues) == 0


def test_integrity_gate_passes_verified_vesna_deliverable(integrity_gate, vesna_valid_data):
    """Verifies that authentic Vesna Vulović production assets pass all 9 integrity checks."""
    data = vesna_valid_data
    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
        manifest_assets=data["manifest_assets"],
    )
    assert report.is_valid, f"Gate rejected authentic deliverable: {report.rejection_reasons}"
    assert len(report.rejection_reasons) == 0
    assert len(report.checks) == 11
    assert all(c.passed for c in report.checks)


def test_integrity_gate_rejects_cross_contamination_in_narration(integrity_gate, vesna_valid_data):
    """CHECK A: Rejects unrelated nuclear references in non-nuclear narration."""
    data = vesna_valid_data
    contaminated_narration = data["narration_text"] + " An atomic bomb mushroom cloud rose over Hiroshima."

    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=contaminated_narration,
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
    )
    assert not report.is_valid
    assert any("CHECK_A_TOPIC_NARRATION" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_unrelated_assets_in_timeline(integrity_gate, vesna_valid_data):
    """CHECK C: Rejects unrelated past topic assets (e.g., Hiroshima atomic cloud) in timeline."""
    data = vesna_valid_data
    # Inject an unrelated Yamaguchi asset into beat 01
    bad_beats = [
        VisualBeatConfig(
            id="bad_beat",
            image_filename="Atomic_cloud_over_Hiroshima_jpg.jpg",
            start_sec=0.0,
            end_sec=2.0,
            top_tag="HIROSHIMA",
            editorial_text="MUSHROOM CLOUD",
            accent_color=(255, 0, 0),
        )
    ]

    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=bad_beats,
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
    )
    assert not report.is_valid
    assert any("CHECK_C_ASSET_TOPIC_MAPPING" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_unrelated_thumbnail_headline(integrity_gate, vesna_valid_data):
    """CHECK D: Rejects unrelated nuclear headlines for aviation story thumbnail."""
    data = vesna_valid_data
    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline="SURVIVED BOTH BOMBS",  # Nuclear headline on Vesna topic
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
    )
    assert not report.is_valid
    assert any("CHECK_D_THUMBNAIL" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_truncated_title(integrity_gate, vesna_valid_data):
    """CHECK E: Rejects arbitrarily truncated titles ending in '...'."""
    data = vesna_valid_data
    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title="The Flight Attendant Who Fell 33,000 Feet...",  # Arbitrary truncation
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
    )
    assert not report.is_valid
    assert any("CHECK_E_TITLE" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_unrelated_tags(integrity_gate, vesna_valid_data):
    """CHECK H: Rejects nuclear tags on non-nuclear topic."""
    data = vesna_valid_data
    bad_tags = ["vesna vulovic", "hiroshima", "atomic bomb", "shorts"]

    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=bad_tags,
        attribution_text=data["metadata"].attribution_text,
    )
    assert not report.is_valid
    assert any("CHECK_H_TAGS" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_stale_yamaguchi_video_copy(integrity_gate, vesna_valid_data):
    """CHECK I: Rejects when output video is an identical binary clone of Yamaguchi showcase video."""
    data = vesna_valid_data
    yamaguchi_video = os.path.join(WORKSPACE, "media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4")
    if not os.path.isfile(yamaguchi_video):
        yamaguchi_video = os.path.join(VIDEO_DIR, "showcase_stage_3_4_tsutomu_yamaguchi.mp4")

    if os.path.isfile(yamaguchi_video):
        report = integrity_gate.validate_production(
            topic_title=data["topic_title"],
            category=data["category"],
            narration_text=data["narration_text"],
            visual_beat_configs=data["visual_beat_configs"],
            video_path=yamaguchi_video,  # Yamaguchi video passed for Vesna topic
            thumbnail_path=data["thumbnail_path"],
            thumbnail_headline=data["thumbnail_headline"],
            metadata_title=data["metadata"].title,
            metadata_description=data["metadata"].description,
            metadata_tags=data["metadata"].tags,
            attribution_text=data["metadata"].attribution_text,
        )
        assert not report.is_valid
        assert any("CHECK_I_NO_STALE_ASSETS" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_stick_figure_rendering_engine(integrity_gate, vesna_valid_data):
    """CHECK K: Rejects production when prohibited stick animation engine is specified."""
    data = vesna_valid_data
    report = integrity_gate.validate_production(
        topic_title=data["topic_title"],
        category=data["category"],
        narration_text=data["narration_text"],
        visual_beat_configs=data["visual_beat_configs"],
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline=data["thumbnail_headline"],
        metadata_title=data["metadata"].title,
        metadata_description=data["metadata"].description,
        metadata_tags=data["metadata"].tags,
        attribution_text=data["metadata"].attribution_text,
        rendering_engine="render_stick_animation",
    )
    assert not report.is_valid
    assert any("CHECK_K_PROHIBITED_ANIMATION" in r for r in report.rejection_reasons)


def test_integrity_gate_rejects_stick_character_in_black_hole_story(integrity_gate, vesna_valid_data):
    """CHECK K: Rejects stick figure character rigs and poses in Black Hole stories."""
    data = vesna_valid_data
    bad_beats = [
        {"start_sec": 0.0, "end_sec": 5.0, "arm_left": "arm_left_idle", "doc_pose": "thinking", "char_x": 540}
    ]
    report = integrity_gate.validate_production(
        topic_title="What Happens When You Fall Into a Black Hole?",
        category="Science & The Unknown",
        narration_text="What happens when you cross the event horizon of a supermassive black hole?",
        visual_beat_configs=bad_beats,
        video_path=data["video_path"],
        thumbnail_path=data["thumbnail_path"],
        thumbnail_headline="BLACK HOLE TRUTH",
        metadata_title="What Happens Inside a Black Hole? #Shorts",
        metadata_description="Exploring the astrophysical reality of the event horizon and singularity according to general relativity.",
        metadata_tags=["black hole", "singularity", "event horizon", "astronomy", "shorts"],
        attribution_text="Public Domain NASA / ESO historical observatory imagery.",
        rendering_engine="CleanSlateRenderer",
    )
    assert not report.is_valid
    assert any("CHECK_K_PROHIBITED_ANIMATION" in r for r in report.rejection_reasons)


def test_master_orchestrator_runs_vesna_through_integrity_gate():
    """Tests master orchestrator end-to-end execution on Vesna Vulović topic with gate validation."""
    orchestrator = CrazzyDocsMasterOrchestrator()
    job_id = "test_vesna_integrity_run"
    try:
        res = orchestrator.execute_production_pipeline(
            job_id=job_id,
            force_topic_id="cand_03_vesna_fall",
        )
        assert res["status"] == "COMPLETED"
        assert "33,000" in res["topic"] or "Vesna" in res["topic"]
        assert os.path.isfile(res["video_path"])
        assert os.path.isfile(res["thumbnail_path"])
        assert res["publishing_status"] in ("enqueued_private", "enqueued_public")
    finally:
        import shutil
        test_dir = os.path.join(WORKSPACE, "media", "productions", job_id)
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
        # Clean test ledger entry so clean production slate is preserved
        import sqlite3
        conn = sqlite3.connect(os.path.join(WORKSPACE, "backend", "studio.db"))
        cur = conn.cursor()
        cur.execute("DELETE FROM publishing_ledger WHERE id > 32 AND video_path LIKE '%test_vesna_integrity_run%'")
        conn.commit()
        conn.close()
