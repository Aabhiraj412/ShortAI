"""
Automated Test Suite for Stage 3.5 — Professional Thumbnail Refinement.
Verifies:
1. Multi-candidate generation across story archetypes.
2. Strict 2-4 impactful word limit.
3. 8-metric candidate evaluation matrix.
4. Safe zone clearance (top > 180, bottom < 1660, margins 60-1020).
5. Misleading content penalty deduction.
6. Exact 1080x1920 (9:16 vertical) output dimensions.
7. Thumbnail audit report generation.
"""

import json
import os
import pytest
from PIL import Image

from backend.app.rendering.engines.creative_thumbnail_engine import (
    CreativeThumbnailEngine,
    StoryArchetype,
    CandidateScore,
    ThumbnailCandidate,
)

WORKSPACE = r"D:\Agents\CrazzyDocs"
TEST_IMG = os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Atomic_cloud_over_Hiroshima_jpg.jpg")
REPORT_PATH = os.path.join(WORKSPACE, "media", "audit", "thumbnail_stage_3_5_report.json")
FINAL_THUMB_PATH = os.path.join(WORKSPACE, "media", "thumbnails", "showcase_stage_3_5_yamaguchi_refined.png")


def test_archetype_detection():
    engine = CreativeThumbnailEngine()
    arch1 = engine.detect_archetype("Unbelievable Real-Life Stories", "Tsutomu Yamaguchi Survived Both Atomic Bombs", "Hiroshima and Nagasaki survivor")
    assert arch1 == StoryArchetype.HISTORICAL_SURVIVAL

    arch2 = engine.detect_archetype("Science & Technology", "The Demon Core Plutonium Sphere", "Radiation slip")
    assert arch2 == StoryArchetype.SCIENTIFIC_PHENOMENON

    arch3 = engine.detect_archetype("Corporate & Financial Stories", "The Man Who Bought a Bank for $1", "Acquisition")
    assert arch3 == StoryArchetype.CORPORATE_DISASTER

    arch4 = engine.detect_archetype("Mysteries & Unexplained Events", "The Mary Celeste Ghost Ship", "Found adrift")
    assert arch4 == StoryArchetype.STRANGE_EVENT


def test_candidate_word_limit():
    engine = CreativeThumbnailEngine()
    candidates = engine.create_candidate_proposals(
        topic_title="Tsutomu Yamaguchi",
        topic_summary="Double atomic bomb survivor",
        category="Unbelievable Real-Life Stories",
        available_assets=[TEST_IMG]
    )
    assert 3 <= len(candidates) <= 4, "Must generate 3 to 4 candidates"
    for c in candidates:
        assert 2 <= c.word_count <= 4, f"Headline '{c.headline_text}' must have 2-4 words, got {c.word_count}"


def test_evaluation_scoring_and_misleading_penalty():
    score_clean = CandidateScore(
        topic_relevance=9.5,
        curiosity=9.5,
        visual_clarity=9.0,
        focal_point=9.0,
        readability=10.0,
        emotional_impact=9.5,
        branding_consistency=9.5,
        misleading_risk=0.0
    )
    comp_clean = score_clean.compute_composite()
    assert comp_clean >= 9.0

    # Misleading claim penalty
    score_misleading = CandidateScore(
        topic_relevance=9.5,
        curiosity=9.5,
        visual_clarity=9.0,
        focal_point=9.0,
        readability=10.0,
        emotional_impact=9.5,
        branding_consistency=9.5,
        misleading_risk=3.0
    )
    comp_misleading = score_misleading.compute_composite()
    assert comp_misleading < comp_clean - 4.0, "Misleading risk penalty must heavily reduce score"


def test_end_to_end_thumbnail_generation():
    engine = CreativeThumbnailEngine()
    winner, report = engine.select_and_render_best_thumbnail(
        topic_title="Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs",
        topic_summary="Survived Hiroshima and Nagasaki",
        category="Unbelievable Real-Life Stories",
        available_assets=[TEST_IMG],
        final_output_path=FINAL_THUMB_PATH,
        report_output_path=REPORT_PATH
    )
    assert os.path.exists(FINAL_THUMB_PATH)
    img = Image.open(FINAL_THUMB_PATH)
    assert img.size == (1080, 1920), f"Expected 1080x1920, got {img.size}"
    assert winner.score.composite_score >= 9.0
    assert 2 <= winner.word_count <= 4

    assert os.path.exists(REPORT_PATH)
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["winner_candidate_id"] == winner.id
    assert rep["safe_zone_compliance"]["passed"] is True
