"""
Central Creative Identity Policy & Enforcement Engine for CrazzyDocs.
Phase 5, Stage 3.18 Standard.

Governs the four approved visual styles and programmatically enforces channel-wide creative restrictions:
1. Real Images / Documentary (Historical Open Archival & Clean Slate)
2. Pop Info (Illustrated Explainer / Editorial Cards)
3. Chalk Talk (Technical Slate & Mathematical Diagrams — ZERO Stick Figures)
4. Creator Motion Graphics (Kinetic Typography, Motion Design & Geometric Transitions)

Global Invariants:
- STRICTLY ZERO stick figures, character rigs, cartoon hosts, or human-shaped avatars.
- Single-style consistency throughout every production (zero mid-production style jumping).
- Mandatory scientific diagrammatic / authentic imagery for space and astrophysics topics.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ApprovedStyle(str, Enum):
    DOCUMENTARY = "documentary"
    POP_INFO = "pop_info"
    CHALK_TALK = "chalk_talk"
    CREATOR_MOTION_GRAPHICS = "creator_motion_graphics"


# Exact banned tokens across prompts, asset metadata, manifests, and timeline beats
PROHIBITED_CHARACTER_TOKENS: Set[str] = {
    "stick_figure", "stickfigure", "stick figure", "stick_man", "stickman",
    "stick_rig", "stickfigurerig", "stick_animation", "stick animation",
    "cartoon_host", "cartoon host", "cartoon_character", "cartoon character",
    "character_rig", "character rig", "talking_head", "talking head",
    "avatar_host", "doc_pose", "human_mascot", "doodle_man", "doodle figure"
}

# Negative prompt injection enforced on all image and visual generation calls
MANDATORY_NEGATIVE_PROMPT: str = (
    "stick figure, cartoon character, cartoon host, 2D stickman, character rig, "
    "talking head, amateur drawing, ugly, distorted proportions, low resolution"
)

# Scientific visual keywords required for Black Hole / Space productions
BLACK_HOLE_SCIENTIFIC_MOTIFS: Set[str] = {
    "accretion disk", "event horizon", "singularity", "gravitational lensing",
    "spacetime", "astronomical", "telescope", "photon ring", "black hole",
    "general relativity", "scientific visualization", "astrophysics"
}


@dataclass
class CreativePolicyAuditResult:
    is_compliant: bool
    selected_style: str
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class CreativeIdentityPolicy:
    """Central programmatic enforcement engine for CrazzyDocs visual identity."""

    @staticmethod
    def get_approved_styles() -> List[str]:
        return [s.value for s in ApprovedStyle]

    @classmethod
    def validate_prompt(cls, prompt: str) -> Tuple[bool, List[str]]:
        """Ensures visual generation prompts never request prohibited character or stick animation."""
        issues = []
        p_lower = prompt.lower()
        for token in PROHIBITED_CHARACTER_TOKENS:
            if token in p_lower:
                issues.append(f"Prompt contains prohibited character/stick token: '{token}'")
        return len(issues) == 0, issues

    @classmethod
    def inject_safe_negative_prompt(cls, base_prompt: str) -> str:
        """Appends mandatory channel negative prompt safeguarding against character artifacts."""
        return f"{base_prompt} | Negative Prompt: {MANDATORY_NEGATIVE_PROMPT}"

    @classmethod
    def validate_renderer_selection(cls, renderer_name: str) -> Tuple[bool, List[str]]:
        """Blocks any renderer utilizing stick figures, character rigs, or legacy cartoon host engines."""
        issues = []
        r_lower = renderer_name.lower().replace("_", "")
        if any(term in r_lower for term in ("stick", "character", "characterrig", "hostsprite")):
            issues.append(f"Prohibited rendering engine selected: '{renderer_name}'. Only approved document/motion graphics engines allowed.")
        return len(issues) == 0, issues

    @classmethod
    def validate_timeline_beats(
        cls,
        beats: List[Any],
        selected_style: str,
        topic_title: str = "",
    ) -> CreativePolicyAuditResult:
        """
        Validates timeline scene beats against:
        1. Single-style cohesion across all scenes.
        2. Absolute absence of stick figure rigging or character pose properties.
        3. Scientific visualization standards for astrophysics / black-hole topics.
        """
        violations = []
        warnings = []
        title_lower = topic_title.lower()
        is_black_hole_topic = any(k in title_lower for k in ("black hole", "singularity", "event horizon", "spacetime"))

        if not beats:
            violations.append("Timeline has no visual beat configurations.")
            return CreativePolicyAuditResult(is_compliant=False, selected_style=selected_style, violations=violations)

        for idx, beat in enumerate(beats):
            beat_str = str(beat).lower()
            
            # 1. Prohibited character tokens
            for token in PROHIBITED_CHARACTER_TOKENS:
                if token in beat_str:
                    violations.append(f"Beat {idx+1} contains prohibited token '{token}'")

            # 2. Check for character rigging properties
            character_rig_props = ["arm_left", "arm_right", "eyebrows_", "char_x", "char_y", "mouth_open", "doc_pose"]
            for prop in character_rig_props:
                if prop in beat_str:
                    violations.append(f"Beat {idx+1} defines prohibited character rig property '{prop}'")

            # 3. Check for style leaping
            if isinstance(beat, dict):
                beat_style = beat.get("modality") or beat.get("style")
            else:
                beat_style = getattr(beat, "modality", None) or getattr(beat, "style", None)

            if beat_style and beat_style.lower() != selected_style.lower():
                violations.append(f"Beat {idx+1} specifies style '{beat_style}' which conflicts with video style '{selected_style}'")

        # 4. Black Hole / Space Policy Enforcement
        if is_black_hole_topic:
            all_text = " ".join(str(b).lower() for b in beats)
            found_motifs = [m for m in BLACK_HOLE_SCIENTIFIC_MOTIFS if m in all_text]
            if not found_motifs and not any(k in title_lower for k in ("black hole", "singularity")):
                warnings.append("Black hole story should emphasize accretion disk, event horizon, or gravitational lensing imagery.")
            # Zero character allowance on black hole content
            if any("stick" in v or "character" in v for v in violations):
                violations.append("CRITICAL: Black hole story strictly forbids cartoon/stick figures under astrophysics visual policy.")

        is_compliant = (len(violations) == 0)
        return CreativePolicyAuditResult(
            is_compliant=is_compliant,
            selected_style=selected_style,
            violations=violations,
            warnings=warnings,
        )

    @classmethod
    def audit_production_manifest(cls, manifest: Dict[str, Any]) -> CreativePolicyAuditResult:
        """Audits a complete production manifest before final publishing approval."""
        style = manifest.get("visual_style") or manifest.get("style", "documentary")
        beats = manifest.get("visual_timeline", []) or manifest.get("scenes", [])
        title = manifest.get("topic_title", "")
        renderer = manifest.get("rendering_engine", "CleanSlateRenderer")

        # Check renderer
        rend_ok, rend_issues = cls.validate_renderer_selection(renderer)
        # Check beats
        timeline_audit = cls.validate_timeline_beats(beats=beats, selected_style=style, topic_title=title)

        all_violations = rend_issues + timeline_audit.violations
        return CreativePolicyAuditResult(
            is_compliant=len(all_violations) == 0,
            selected_style=style,
            violations=all_violations,
            warnings=timeline_audit.warnings,
        )
