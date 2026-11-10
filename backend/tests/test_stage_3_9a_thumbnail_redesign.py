"""
Automated Test Suite for Stage 3.9A — Creative Thumbnail Engine Redesign.
Verifies:
1. Four genuinely different composition concepts (Split, Extreme Scale, Monolith, Forensic Archive).
2. Exact 1080x1920 (9:16 vertical) resolution on all candidates.
3. Downscaled 120x213 mobile preview generation for feed readability checks.
4. 10-dimensional creative evaluation matrix.
5. Strict 2-4 word headline limit.
6. Preservation of previous approved thumbnail (showcase_stage_3_5_yamaguchi_refined.png).
7. Winning poster selection and audit JSON report.
"""

import json
import os
import pytest
from PIL import Image

from backend.app.rendering.engines.cinematic_thumbnail_designer import (
    CinematicThumbnailDesigner,
    CreativeDirectionType,
    CreativeEvaluationScore,
    PosterCandidate,
)

WORKSPACE = r"D:\Agents\CrazzyDocs"
AUDIT_DIR = os.path.join(WORKSPACE, "media", "audit", "thumbnails_3_9a")
PREVIEWS_DIR = os.path.join(AUDIT_DIR, "mobile_previews")
WINNER_POSTER = os.path.join(WORKSPACE, "media", "thumbnails", "showcase_stage_3_9a_yamaguchi_poster.png")
PREV_APPROVED_THUMB = os.path.join(WORKSPACE, "media", "thumbnails", "showcase_stage_3_5_yamaguchi_refined.png")
REPORT_PATH = os.path.join(AUDIT_DIR, "thumbnail_stage_3_9a_creative_report.json")

ASSETS = {
    'hiroshima_cloud': os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Atomic_cloud_over_Hiroshima_jpg.jpg"),
    'nagasaki_cloud': os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Nagasakibomb_jpg.jpg"),
    'monument': os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Hiroshima_Gembaku_Dome_1_jpg.jpg"),
    'declassified_order': os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Hiroshima_Bombing_Operations_Order_No_35_5_Aug_1945_jpg.jpg"),
    'rubble': os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Dan_A__McGovern_at_Nagasaki_ground_zero_9_September_1945_png.png"),
}


def test_four_distinct_creative_concepts():
    designer = CinematicThumbnailDesigner()
    candidates = designer.generate_all_candidates(ASSETS, audit_dir=AUDIT_DIR)

    assert len(candidates) == 4, f"Expected exactly 4 creative concepts, found {len(candidates)}"
    
    # Verify each has a distinct direction type
    types = {c.direction_type for c in candidates}
    assert len(types) == 4, "All 4 candidates must represent distinct creative direction types"
    assert CreativeDirectionType.SPLIT_JUXTAPOSITION in types
    assert CreativeDirectionType.EXTREME_SCALE_PERSPECTIVE in types
    assert CreativeDirectionType.RESILIENT_MONOLITH in types
    assert CreativeDirectionType.FORENSIC_ARCHIVE in types

    # Verify resolutions
    for c in candidates:
        assert os.path.exists(c.full_path)
        img = Image.open(c.full_path)
        assert img.size == (1080, 1920), f"Candidate {c.id} resolution {img.size} != 1080x1920"


def test_mobile_feed_previews_generated():
    designer = CinematicThumbnailDesigner()
    candidates = designer.generate_all_candidates(ASSETS, audit_dir=AUDIT_DIR)

    for c in candidates:
        assert c.mobile_path and os.path.exists(c.mobile_path), f"Mobile preview missing for {c.id}"
        mob = Image.open(c.mobile_path)
        assert mob.size == (120, 213), f"Mobile preview {c.id} resolution {mob.size} != 120x213"


def test_ten_dimension_creative_evaluation():
    score = CreativeEvaluationScore(
        curiosity=9.9,
        story_relevance=10.0,
        visual_impact=9.8,
        focal_hierarchy=9.4,
        readability=9.8,
        emotional_impact=9.7,
        composition_balance=9.6,
        branding_alignment=9.5,
        historical_accuracy=10.0,
        small_screen_visibility=9.6,
        misleading_risk_penalty=0.0
    )
    comp = score.compute_composite()
    assert 9.5 <= comp <= 10.0, f"Expected composite >= 9.5, got {comp}"

    # Penalty deduction test
    score_penalized = CreativeEvaluationScore(
        curiosity=9.9,
        story_relevance=10.0,
        visual_impact=9.8,
        focal_hierarchy=9.4,
        readability=9.8,
        emotional_impact=9.7,
        composition_balance=9.6,
        branding_alignment=9.5,
        historical_accuracy=10.0,
        small_screen_visibility=9.6,
        misleading_risk_penalty=2.0
    )
    comp_pen = score_penalized.compute_composite()
    assert comp_pen <= comp - 4.0, "Misleading risk penalty must deduct substantially"


def test_word_count_limits():
    designer = CinematicThumbnailDesigner()
    candidates = designer.generate_all_candidates(ASSETS, audit_dir=AUDIT_DIR)

    for c in candidates:
        words = c.headline.split()
        assert 2 <= len(words) <= 4, f"Headline '{c.headline}' has {len(words)} words; must be 2-4 words"


def test_winner_selection_and_preservation():
    assert os.path.exists(PREV_APPROVED_THUMB), "Previous approved thumbnail must NOT be deleted"
    
    designer = CinematicThumbnailDesigner()
    candidates = designer.generate_all_candidates(ASSETS, audit_dir=AUDIT_DIR)
    winner, report = designer.select_best_poster(
        candidates,
        final_output_path=WINNER_POSTER,
        report_output_path=REPORT_PATH
    )

    assert os.path.exists(WINNER_POSTER)
    winner_img = Image.open(WINNER_POSTER)
    assert winner_img.size == (1080, 1920)
    assert winner.evaluation.composite_score >= 9.5

    # Check report JSON
    assert os.path.exists(REPORT_PATH)
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        rep_data = json.load(f)
    assert rep_data["winner_id"] == winner.id
    assert len(rep_data["candidates_ranking"]) == 4
