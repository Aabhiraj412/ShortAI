"""
Test Suite for CrazzyDocs Creative Identity Policy & Enforcement.
Phase 5, Stage 3.18 Standard.

Validates:
1. Strict programmatic prohibition of stick figures, cartoon hosts, and character rigs across all 4 styles.
2. Prompt validation and mandatory negative prompt injection.
3. Timeline beat verification: rejects character rig properties and inter-scene style leaping.
4. Renderer selection verification: blocks deprecated character/stick renderers.
5. Astrophysics / Black Hole policy: mandates scientific visual motifs and zero character fallback.
"""

import pytest
from backend.app.content.creative_identity_policy import (
    ApprovedStyle,
    CreativeIdentityPolicy,
    MANDATORY_NEGATIVE_PROMPT,
)


def test_approved_styles_enumeration():
    """Verifies the four official CrazzyDocs visual styles are registered."""
    styles = CreativeIdentityPolicy.get_approved_styles()
    assert "documentary" in styles
    assert "pop_info" in styles
    assert "chalk_talk" in styles
    assert "creator_motion_graphics" in styles


def test_prompt_validation_rejects_prohibited_character_motifs():
    """Verifies prompt validator rejects stick figures and cartoon hosts."""
    valid_prompt = "A high-resolution accretion disk surrounding a supermassive black hole with gravitational lensing."
    ok, issues = CreativeIdentityPolicy.validate_prompt(valid_prompt)
    assert ok is True
    assert len(issues) == 0

    bad_prompt_1 = "Draw a stick figure falling into a black hole with panicked arms."
    ok1, issues1 = CreativeIdentityPolicy.validate_prompt(bad_prompt_1)
    assert ok1 is False
    assert any("stick figure" in i for i in issues1)

    bad_prompt_2 = "A cartoon host explaining event horizon physics with funny expressions."
    ok2, issues2 = CreativeIdentityPolicy.validate_prompt(bad_prompt_2)
    assert ok2 is False
    assert any("cartoon host" in i for i in issues2)


def test_safe_negative_prompt_injection():
    """Verifies that mandatory negative prompts are injected into generative workflows."""
    base = "Scientific diagram of black hole event horizon"
    safe = CreativeIdentityPolicy.inject_safe_negative_prompt(base)
    assert "Negative Prompt:" in safe
    assert "stick figure" in safe
    assert "cartoon character" in safe


def test_renderer_selection_blocks_prohibited_engines():
    """Verifies that legacy stick animation or character engines cannot be selected."""
    ok, _ = CreativeIdentityPolicy.validate_renderer_selection("CleanSlateRenderer")
    assert ok is True

    ok_vesna, _ = CreativeIdentityPolicy.validate_renderer_selection("VesnaFastRenderer")
    assert ok_vesna is True

    bad_rend_1, issues_1 = CreativeIdentityPolicy.validate_renderer_selection("render_stick_animation")
    assert bad_rend_1 is False
    assert any("stick" in i.lower() for i in issues_1)

    bad_rend_2, issues_2 = CreativeIdentityPolicy.validate_renderer_selection("OptimizedCharacterRigRenderer")
    assert bad_rend_2 is False
    assert any("character" in i.lower() for i in issues_2)


def test_timeline_beats_rejects_character_rig_properties():
    """Verifies timeline validator rejects forward kinematics character rig properties."""
    bad_beats = [
        {"id": "scene_01", "duration": 5.0, "arm_left": "arm_left_idle", "char_x": 540},
    ]
    audit = CreativeIdentityPolicy.validate_timeline_beats(
        beats=bad_beats,
        selected_style="chalk_talk",
        topic_title="Orbital Dynamics",
    )
    assert audit.is_compliant is False
    assert any("character rig property" in v for v in audit.violations)


def test_timeline_beats_rejects_inter_scene_style_jumping():
    """Verifies timeline validator enforces single-style cohesion and rejects style jumping."""
    mixed_beats = [
        {"id": "scene_01", "duration": 5.0, "style": "documentary"},
        {"id": "scene_02", "duration": 5.0, "style": "pop_info"},  # Leaping to pop_info
    ]
    audit = CreativeIdentityPolicy.validate_timeline_beats(
        beats=mixed_beats,
        selected_style="documentary",
        topic_title="Historical Event",
    )
    assert audit.is_compliant is False
    assert any("conflicts with video style" in v for v in audit.violations)


def test_black_hole_astrophysics_visual_standards():
    """Verifies astrophysics content strictly enforces scientific motifs and rejects cartoons."""
    # Valid scientific beats
    valid_beats = [
        {"id": "b1", "description": "Accretion disk around singularity with gravitational lensing photon ring"},
        {"id": "b2", "description": "Spacetime curvature diagram showing event horizon geometry"},
    ]
    audit = CreativeIdentityPolicy.validate_timeline_beats(
        beats=valid_beats,
        selected_style="documentary",
        topic_title="What Happens Inside a Black Hole?",
    )
    assert audit.is_compliant is True
    assert len(audit.violations) == 0

    # Cartoon beat on black hole topic
    cartoon_beats = [
        {"id": "b1", "description": "Stick figure falling into event horizon with doc_pose"},
    ]
    bad_audit = CreativeIdentityPolicy.validate_timeline_beats(
        beats=cartoon_beats,
        selected_style="documentary",
        topic_title="What Happens Inside a Black Hole?",
    )
    assert bad_audit.is_compliant is False
    assert any("black hole" in v.lower() or "stick" in v.lower() for v in bad_audit.violations)
