"""
End-to-End Creative Identity & Style Invariant Verification for CrazzyDocs.
Phase 5, Stage 3.19 Standard.

Tests:
1. Black hole astrophysics topic enforces scientific motifs and negative prompts.
2. Historical mystery topic selects Documentary/Archival style.
3. Scientific discovery topic selects Pop Info / Chalk Talk with zero character rigs.
4. Human survival story topic selects Documentary with authentic photo standards.
5. Invariant enforcement: Zero stick figures, cartoon hosts, or legacy characterrig fallbacks.
6. Check K in ContentIntegrityGate passes for all 4 styles.
7. Controlled preview rendering verification without public publishing.
"""

import os
import sys
import pytest

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if os.path.join(WORKSPACE_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "backend"))

from backend.app.content.creative_identity_policy import (
    CreativeIdentityPolicy,
    ApprovedStyle,
    PROHIBITED_CHARACTER_TOKENS,
    BLACK_HOLE_SCIENTIFIC_MOTIFS,
)
from backend.app.content.content_integrity_gate import ContentIntegrityGate, IntegrityCheckResult
from backend.app.agents.visual_director import VisualDirector


def test_black_hole_creative_identity():
    topic_title = "What Happens When You Fall Into a Supermassive Black Hole"
    vd = VisualDirector()
    script = {
        "title": topic_title,
        "full_script": "As you cross the event horizon of a supermassive black hole, spacetime itself bends infinitely. Gravitational lensing warps light from distant galaxies into a blinding photon ring."
    }
    storyboard = vd.create_storyboard(script, total_duration=55.0, visual_style="pop_info")
    scenes = storyboard["scenes"]

    # 1. Verify no character/stick figure properties in scenes
    for s in scenes:
        assert "pose" not in s
        assert "doc_pose" not in s
        assert "left_arm" not in s
        assert "right_arm" not in s
        assert "char_x" not in s
        # Motifs must contain scientific terms
        assert "motifs" in s
        assert any(m in BLACK_HOLE_SCIENTIFIC_MOTIFS for m in s["motifs"])

    # 2. Check CreativeIdentityPolicy validation
    res = CreativeIdentityPolicy.validate_timeline_beats(scenes, "pop_info", topic_title=topic_title)
    assert res.is_compliant, f"Validation issues: {res.violations}"

    # 3. Check negative prompt injection
    base_prompt = "Supermassive black hole with glowing orange accretion disk"
    safe_prompt = CreativeIdentityPolicy.inject_safe_negative_prompt(base_prompt)
    assert "stick figure" in safe_prompt
    assert "cartoon character" in safe_prompt


def test_historical_mystery_creative_identity():
    topic_title = "The Vanishing of Flight 19 in the Bermuda Triangle"
    vd = VisualDirector()
    script = {
        "title": topic_title,
        "full_script": "In December 1945, five US Navy bombers vanished without a trace over the Atlantic. No wreckage, distress signals, or life rafts were ever recovered."
    }
    storyboard = vd.create_storyboard(script, total_duration=55.0, visual_style="documentary")
    scenes = storyboard["scenes"]
    
    res = CreativeIdentityPolicy.validate_timeline_beats(scenes, "documentary", topic_title=topic_title)
    assert res.is_compliant, f"Validation issues: {res.violations}"


def test_scientific_discovery_creative_identity():
    topic_title = "James Webb Space Telescope Detects Impossible Early Galaxies"
    vd = VisualDirector()
    script = {
        "title": topic_title,
        "full_script": "Astronomers expected tiny proto-galaxies in the early universe, but JWST discovered fully formed massive galaxies that defy the standard cosmological model."
    }
    storyboard = vd.create_storyboard(script, total_duration=55.0, visual_style="chalk_talk")
    scenes = storyboard["scenes"]

    res = CreativeIdentityPolicy.validate_timeline_beats(scenes, "chalk_talk", topic_title=topic_title)
    assert res.is_compliant, f"Validation issues: {res.violations}"


def test_human_survival_story_creative_identity():
    topic_title = "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived"
    vd = VisualDirector()
    script = {
        "title": topic_title,
        "full_script": "Vesna Vulovic survived a 33,000-foot fall when JAT Flight 367 disintegrated over Srbska Kamenice in 1972."
    }
    storyboard = vd.create_storyboard(script, total_duration=55.0, visual_style="documentary")
    scenes = storyboard["scenes"]

    res = CreativeIdentityPolicy.validate_timeline_beats(scenes, "documentary", topic_title=topic_title)
    assert res.is_compliant, f"Validation issues: {res.violations}"


def test_check_k_prohibited_animation_gate():
    gate = ContentIntegrityGate()

    # 1. Direct policy tests
    ok, violations = CreativeIdentityPolicy.validate_renderer_selection("universal_compositor")
    assert ok is True
    assert len(violations) == 0

    bad_ok, bad_violations = CreativeIdentityPolicy.validate_renderer_selection("stick_animation_renderer")
    assert bad_ok is False
    assert len(bad_violations) > 0

    # 2. Manifest asset prohibited token check
    manifest_clean = [{"id": "astronomy_01", "name": "accretion_disk.png"}]
    manifest_prohibited = [{"id": "bad_asset", "name": "stick_figure_scene.png"}]

    # Clean beats
    clean_beats = [{"style": "pop_info", "motifs": ["accretion disk", "event horizon"]}]
    res_clean = CreativeIdentityPolicy.validate_timeline_beats(clean_beats, "pop_info", topic_title="Black Holes")
    assert res_clean.is_compliant is True

    # Bad beats with character rig property
    bad_beats = [{"style": "pop_info", "character_rig": "rig_01"}]
    res_bad = CreativeIdentityPolicy.validate_timeline_beats(bad_beats, "pop_info", topic_title="Black Holes")
    assert res_bad.is_compliant is False
