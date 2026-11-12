"""
Style Consistency System & Adaptive Per-Beat Style Architecture.

Defines the complete design identity, typography families, color palettes,
and transition languages for each of the four core visual styles,
enforcing approved, narratively justified per-beat style changes across each video.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


class VisualStyle(str, Enum):
    """The legacy primary styles retained for existing plan callers."""
    POP_INFO = "pop_info"
    DATA_STORY = "data_story"
    CHALK_TALK = "chalk_talk"
    CREATOR_MOTION = "creator_motion"


@dataclass
class StyleTheme:
    """The comprehensive design system specification for a visual style."""
    style: VisualStyle
    display_name: str
    primary_font: str
    secondary_font: str
    accent_font: Optional[str] = None

    # Colors (hex strings)
    background_color: str = "#000000"
    card_background: str = "#111111"
    primary_text: str = "#FFFFFF"
    secondary_text: str = "#AAAAAA"
    accent_primary: str = "#FFD60A"
    accent_secondary: str = "#20C997"
    border_color: str = "#333333"

    # Motion & Transition identity
    allowed_transitions: Set[str] = field(default_factory=set)
    corner_radius: int = 16
    shadow_style: str = "none"

    # Integrated media styling rule
    photo_integration_treatment: str = "standard"


# Concrete Theme Definitions for the 4 Styles
THEMES: Dict[VisualStyle, StyleTheme] = {
    VisualStyle.POP_INFO: StyleTheme(
        style=VisualStyle.POP_INFO,
        display_name="Pop Info (Illustrated Explainer)",
        primary_font="Fredoka",
        secondary_font="Fredoka",
        background_color="#D7DCCB",
        card_background="#FFFFFF",
        primary_text="#1D2A2A",
        secondary_text="#2F6F73",
        accent_primary="#E85D4A",    # Coral
        accent_secondary="#FFD86B",  # Sunny yellow
        border_color="#2F6F73",
        allowed_transitions={"iris_wipe", "soft_push", "pop_fade", "cut"},
        corner_radius=40,
        shadow_style="soft_drop",
        photo_integration_treatment="sticker_badge",  # White border, rounded 40px, subtle bounce
    ),
    VisualStyle.DATA_STORY: StyleTheme(
        style=VisualStyle.DATA_STORY,
        display_name="Data Story (Editorial Data Journalism)",
        primary_font="Archivo",
        secondary_font="IBM Plex Mono",
        background_color="#0F141C",
        card_background="#161D28",
        primary_text="#EEF1F5",
        secondary_text="#7C8BA1",
        accent_primary="#20C997",    # Teal
        accent_secondary="#F2C14E",  # Sand
        border_color="#273449",
        allowed_transitions={"slide_up", "fade", "cut"},
        corner_radius=20,
        shadow_style="subtle_elevation",
        photo_integration_treatment="editorial_card",  # Deep card container with data caption & source line
    ),
    VisualStyle.CHALK_TALK: StyleTheme(
        style=VisualStyle.CHALK_TALK,
        display_name="Chalk Talk (Hand-Drawn Slate)",
        primary_font="Cabin Sketch",
        secondary_font="Kalam",
        background_color="#24392F",
        card_background="#1A2B23",
        primary_text="#F2EFE6",
        secondary_text="#93CBEF",
        accent_primary="#F4D35E",    # Warm yellow chalk
        accent_secondary="#F39BBB",  # Pink chalk
        border_color="#4A3319",      # Oak wood
        allowed_transitions={"felt_eraser_wipe", "slate_pan", "cut"},
        corner_radius=0,
        shadow_style="chalk_glow",
        photo_integration_treatment="slate_cutout",    # Paper document taped/pinned to slate with chalk annotation
    ),
    VisualStyle.CREATOR_MOTION: StyleTheme(
        style=VisualStyle.CREATOR_MOTION,
        display_name="Creator Motion Graphics (High-Energy Kinetic)",
        primary_font="Anton",
        secondary_font="Inter",
        accent_font="Instrument Serif",
        background_color="#0B0B0D",
        card_background="#16161A",
        primary_text="#F2F0EA",
        secondary_text="#737380",
        accent_primary="#FFD60A",    # Yellow felt
        accent_secondary="#FF4D4D",  # Strikethrough red
        border_color="#2A2A32",
        allowed_transitions={"whip_horizontal", "whip_vertical", "rgb_glitch_cut", "cut"},
        corner_radius=24,
        shadow_style="deep_drop",
        photo_integration_treatment="kinetic_mockup",  # 3D tilted browser/phone container with yaw motion
    ),
}


class StyleConsistencyValidator:
    """Validates legacy single-style plans and justified per-beat adaptive style plans."""

    @staticmethod
    def validate_plan(primary_style: VisualStyle, scenes: List[Dict]) -> Tuple[bool, List[str]]:
        """
        Ensures that every scene in a storyboard plan belongs to the assigned primary style
        and does not cross-contaminate fonts, background tones, or transitions.
        """
        violations = []
        theme = THEMES.get(primary_style)
        if not theme:
            return False, [f"Unknown visual style: {primary_style}"]

        previous_beat_style: Optional[str] = None
        for i, scene in enumerate(scenes):
            scene_id = scene.get("id", f"scene_{i+1}")
            modality = scene.get("modality")
            params = scene.get("params") if isinstance(scene.get("params"), dict) else {}
            beat_style = scene.get("visual_style") or params.get("visual_style")

            if beat_style:
                style_value = getattr(beat_style, "value", str(beat_style)).lower()
                approved_styles = {
                    "documentary",
                    "pop_info",
                    "chalk_talk",
                    "creator_motion_graphics",
                    "creator_motion",
                    "data_story",
                    "cinematic_explainer",
                    "map_based_storytelling",
                }
                if style_value not in approved_styles:
                    violations.append(f"Scene {i+1} [{scene_id}] specifies unsupported visual style '{style_value}'")
                if (
                    previous_beat_style is not None
                    and style_value != previous_beat_style
                    and not str(scene.get("style_transition_rationale") or params.get("style_transition_rationale") or "").strip()
                ):
                    violations.append(f"Scene {i+1} [{scene_id}] changes visual style without a narrative transition rationale")
                previous_beat_style = style_value

            # 1. Check style modality compliance
            # If the modality is one of the 4 styles, it MUST match primary_style
            style_modalities = {
                "pop_info": VisualStyle.POP_INFO,
                "data_story": VisualStyle.DATA_STORY,
                "chalk_talk": VisualStyle.CHALK_TALK,
                "creator_motion": VisualStyle.CREATOR_MOTION,
            }
            if not beat_style and modality in style_modalities and style_modalities[modality] != primary_style:
                violations.append(
                    f"Scene {i+1} [{scene_id}] specifies modality '{modality}' which conflicts "
                    f"with the video's primary visual style '{primary_style.value}'"
                )

            # 2. Check transition family compliance
            transition = scene.get("transition_in", "cut")
            if transition not in theme.allowed_transitions and transition != "cut":
                violations.append(
                    f"Scene {i+1} [{scene_id}] uses transition '{transition}' which is not in the "
                    f"allowed transition family for {theme.display_name}: {theme.allowed_transitions}"
                )

        return len(violations) == 0, violations
