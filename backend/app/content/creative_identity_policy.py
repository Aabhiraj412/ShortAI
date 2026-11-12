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
- Per-beat style changes require an approved style and explicit narrative rationale.
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
    DATA_STORY = "data_story"
    CHALK_TALK = "chalk_talk"
    CREATOR_MOTION_GRAPHICS = "creator_motion_graphics"
    CINEMATIC_EXPLAINER = "cinematic_explainer"
    MAP_BASED_STORYTELLING = "map_based_storytelling"


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


@dataclass
class AdaptiveStyleSelection:
    """A topic- and narrative-aware visual style decision for one storyboard beat."""
    visual_style: ApprovedStyle
    rationale: str
    transition: str
    transition_rationale: str
    scores: Dict[str, float] = field(default_factory=dict)


class AdaptiveVisualStyleSelector:
    """Scores approved styles per beat; category contributes affinity but never fixes a style."""

    _CATEGORY_AFFINITIES = {
        "history": {ApprovedStyle.DOCUMENTARY: 0.55, ApprovedStyle.MAP_BASED_STORYTELLING: 0.35, ApprovedStyle.CINEMATIC_EXPLAINER: 0.2},
        "nature": {ApprovedStyle.DOCUMENTARY: 0.45, ApprovedStyle.POP_INFO: 0.2},
        "science": {ApprovedStyle.POP_INFO: 0.3, ApprovedStyle.CHALK_TALK: 0.35},
        "technology": {ApprovedStyle.POP_INFO: 0.3, ApprovedStyle.CHALK_TALK: 0.25, ApprovedStyle.CREATOR_MOTION_GRAPHICS: 0.2},
        "psychology": {ApprovedStyle.POP_INFO: 0.4, ApprovedStyle.CHALK_TALK: 0.25},
        "biology": {ApprovedStyle.POP_INFO: 0.3, ApprovedStyle.CHALK_TALK: 0.35},
        "medicine": {ApprovedStyle.DOCUMENTARY: 0.25, ApprovedStyle.POP_INFO: 0.35, ApprovedStyle.CHALK_TALK: 0.25},
        "business": {ApprovedStyle.CREATOR_MOTION_GRAPHICS: 0.65},
        "archaeology": {ApprovedStyle.DOCUMENTARY: 0.45, ApprovedStyle.CREATOR_MOTION_GRAPHICS: 0.2},
        "engineering": {ApprovedStyle.CHALK_TALK: 0.5},
        "mathematics": {ApprovedStyle.CHALK_TALK: 0.55},
        "space": {ApprovedStyle.DOCUMENTARY: 0.4, ApprovedStyle.CHALK_TALK: 0.35},
        "geography": {ApprovedStyle.MAP_BASED_STORYTELLING: 0.7},
    }

    _MECHANISM_CUES = (
        "because", "how", "why", "mechanism", "process", "works", "causes",
        "force", "reaction", "system", "equation", "orbit", "pressure",
        "current", "circuit", "cell", "molecule", "gravity", "energy",
    )
    _REVEAL_CUES = ("revealed", "unexpected", "turning point", "changed", "surprising", "but then", "finally")

    @classmethod
    def select(
        cls,
        *,
        category: str,
        stage: str,
        narrative_purpose: str,
        segment_text: str,
        asset_type: str = "",
        previous_style: Optional[str] = None,
    ) -> AdaptiveStyleSelection:
        text = f"{segment_text or ''} {narrative_purpose or ''}".lower()
        stage_key = re.sub(r"[^a-z]+", "_", (stage or "").lower()).strip("_")
        category_key = re.sub(r"[^a-z]+", "_", (category or "").lower()).strip("_")
        assets = (asset_type or "").lower()
        scores = {style: 0.25 for style in ApprovedStyle}
        reasons: Dict[ApprovedStyle, List[str]] = {style: [] for style in ApprovedStyle}

        affinity_key = next(
            (key for key in cls._CATEGORY_AFFINITIES if key in category_key),
            "",
        )
        for style, bonus in cls._CATEGORY_AFFINITIES.get(affinity_key, {}).items():
            scores[style] += bonus
            reasons[style].append(f"{category or 'topic'} affinity")

        if any(token in assets for token in ("photo", "document", "map", "archival", "footage")):
            scores[ApprovedStyle.DOCUMENTARY] += 1.35
            reasons[ApprovedStyle.DOCUMENTARY].append("authentic visual evidence")
        if any(token in assets for token in ("map", "route", "geographic", "location")):
            scores[ApprovedStyle.MAP_BASED_STORYTELLING] += 1.8
            reasons[ApprovedStyle.MAP_BASED_STORYTELLING].append("geographic evidence is available")
        if any(token in text for token in ("map", "route", "border", "territory", "crossed", "located", "journey")):
            scores[ApprovedStyle.MAP_BASED_STORYTELLING] += 0.8
            reasons[ApprovedStyle.MAP_BASED_STORYTELLING].append("the story depends on place or movement")
        if re.search(r"\b\d[\d,.]*(?:\s*(?:%|percent|million|billion|thousand|years?|km|meters?))?\b", text):
            scores[ApprovedStyle.POP_INFO] += 1.15
            reasons[ApprovedStyle.POP_INFO].append("a factual number benefits from a visual callout")
            scores[ApprovedStyle.DATA_STORY] += 0.8
            reasons[ApprovedStyle.DATA_STORY].append("quantitative evidence benefits from a data-led composition")
        if any(cue in text for cue in cls._MECHANISM_CUES):
            scores[ApprovedStyle.CHALK_TALK] += 1.25
            reasons[ApprovedStyle.CHALK_TALK].append("the beat explains a mechanism or relationship")
        if stage_key in {"hook", "question", "discovery"}:
            scores[ApprovedStyle.POP_INFO] += 0.45
            reasons[ApprovedStyle.POP_INFO].append("the hook benefits from a compact visual hook")
        if stage_key in {"revelation", "climax", "payoff", "closing"} or any(cue in text for cue in cls._REVEAL_CUES):
            scores[ApprovedStyle.CREATOR_MOTION_GRAPHICS] += 1.2
            reasons[ApprovedStyle.CREATOR_MOTION_GRAPHICS].append("the narrative is turning toward a reveal or payoff")
            scores[ApprovedStyle.CINEMATIC_EXPLAINER] += 0.45
            reasons[ApprovedStyle.CINEMATIC_EXPLAINER].append("the reveal benefits from an immersive composition")
        if stage_key in {"escalation", "timeline", "sequence"}:
            scores[ApprovedStyle.CREATOR_MOTION_GRAPHICS] += 0.55
            reasons[ApprovedStyle.CREATOR_MOTION_GRAPHICS].append("the story is escalating")
        if len((segment_text or "").split()) > 22:
            scores[ApprovedStyle.CHALK_TALK] += 0.2
            reasons[ApprovedStyle.CHALK_TALK].append("the idea has multiple relationships to explain")

        previous = next(
            (style for style in ApprovedStyle if style.value == (previous_style or "").lower()),
            None,
        )
        if previous is not None:
            scores[previous] += 0.2

        selected = max(ApprovedStyle, key=lambda style: (scores[style], -list(ApprovedStyle).index(style)))
        rationale = "; ".join(reasons[selected]) or f"best fit for the {stage or 'current'} narrative beat"
        if previous is None:
            transition = "opening"
            transition_rationale = "Open on the hook with the style selected for this beat."
        elif selected == previous:
            transition = "continuity"
            transition_rationale = f"Continue {selected.value} through the same visual idea."
        else:
            transition = "narrative_cut"
            transition_rationale = (
                f"Cut from {previous.value} to {selected.value} at the {stage or 'story'} turn: {rationale}."
            )
        return AdaptiveStyleSelection(
            visual_style=selected,
            rationale=rationale,
            transition=transition,
            transition_rationale=transition_rationale,
            scores={style.value: round(score, 3) for style, score in scores.items()},
        )


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
    def validate_production_renderer(
        cls,
        renderer_name: str,
        *,
        execution_context: Optional[str] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str]]:
        """Enforces Lemo-Opuscar as the sole approved renderer for PRODUCTION/VALIDATION runs."""
        ok, issues = cls.validate_renderer_selection(renderer_name or "")
        norm = str(renderer_name or "").strip().lower().replace("_", "-")
        ctx = str(execution_context or "").strip().upper()
        disallowed_fallbacks = (
            "hybridvisualengine",
            "hybrid-visual-engine",
            "python canvas",
            "pillow ken burns",
            "explainer animation",
            "cleanslaterenderer",
        )
        is_explicit_disallowed = any(bad in norm for bad in disallowed_fallbacks)
        if ctx in ("PRODUCTION", "VALIDATION") or is_explicit_disallowed:
            if not (norm == "lemo-opuscar" or norm.startswith("lemo-opuscar")):
                issues.append(
                    f"Renderer Compliance Failure: '{renderer_name}' is not an approved production renderer. "
                    "Only 'lemo-opuscar' is permitted; silent fallback to Python Canvas / HybridVisualEngine is blocked."
                )
        if telemetry and isinstance(telemetry, dict):
            t_rend = str(telemetry.get("renderer") or telemetry.get("engine") or "").strip().lower()
            if t_rend and "lemo" not in t_rend:
                issues.append(
                    f"Renderer Telemetry Mismatch: telemetry reports '{t_rend}' instead of 'lemo-opuscar'."
                )
            if telemetry.get("fallback_used"):
                issues.append("Renderer Compliance Failure: silent renderer fallback flag detected in telemetry.")
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
        1. Approved per-beat styles and narrative justification for style transitions.
        2. Absolute absence of stick figure rigging or character pose properties.
        3. Scientific visualization standards for astrophysics / black-hole topics.
        """
        violations = []
        warnings = []
        title_lower = topic_title.lower()
        is_black_hole_topic = any(k in title_lower for k in ("black hole", "singularity", "event horizon", "spacetime"))
        previous_style = selected_style.lower()

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
                params = beat.get("params") if isinstance(beat.get("params"), dict) else {}
                adaptive_style = beat.get("visual_style") or params.get("visual_style")
                beat_style = beat.get("visual_style") or params.get("visual_style") or beat.get("modality") or beat.get("style")
            else:
                adaptive_style = None
                beat_style = getattr(beat, "modality", None) or getattr(beat, "style", None)

            if beat_style:
                style_value = getattr(beat_style, "value", str(beat_style)).lower()
                if isinstance(beat, dict) and adaptive_style:
                    approved = {style.value for style in ApprovedStyle}
                    if style_value not in approved:
                        violations.append(f"Beat {idx+1} specifies unsupported visual style '{style_value}'")
                    elif (idx == 0 or style_value != previous_style) and not str(
                        beat.get("style_selection_rationale") or params.get("style_selection_rationale") or ""
                    ).strip():
                        violations.append(f"Beat {idx+1} changes visual style without a selection rationale")
                    if idx > 0 and style_value != previous_style and not str(beat.get("style_transition_rationale") or params.get("style_transition_rationale") or "").strip():
                        violations.append(f"Beat {idx+1} changes visual style without a transition rationale")
                    previous_style = style_value
                elif style_value != selected_style.lower():
                    violations.append(f"Beat {idx+1} specifies style '{style_value}' which conflicts with video style '{selected_style}'")

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
