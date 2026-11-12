"""Treatment-first direction contracts for independent CrazzyDocs composers.

The common pipeline owns provenance, narration, subtitles and FFmpeg delivery.
This module deliberately does *not* prescribe a common visual frame: a direction
owns a coordinate system, camera grammar, density and transition language.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Sequence


class CreativeDirection(str, Enum):
    DOCUMENTARY_INVESTIGATION = "documentary_investigation"
    SCIENTIFIC_VISUALIZATION = "scientific_visualization"
    DATA_STORYTELLING = "data_storytelling"
    CINEMATIC_MOTION_GRAPHICS = "cinematic_motion_graphics"
    CHALKBOARD_BLUEPRINT = "chalkboard_blueprint"
    ARCHIVE_EDITORIAL_MONTAGE = "archive_editorial_montage"


@dataclass(frozen=True)
class ComposerContract:
    direction: CreativeDirection
    composer_id: str
    coordinate_system: str
    layout_strategy: str
    asset_presentation: str
    typography_strategy: str
    camera_grammar: str
    animation_grammar: str
    transitions: Sequence[str]
    density: str
    background_treatment: str
    narrative_purpose: str
    prohibited_scaffolding: Sequence[str]


COMPOSER_CONTRACTS: Mapping[CreativeDirection, ComposerContract] = {
    CreativeDirection.DOCUMENTARY_INVESTIGATION: ComposerContract(
        CreativeDirection.DOCUMENTARY_INVESTIGATION, "evidence_documentary_composer",
        "source-relative image/document space", "evidence-led full-frame, dossier and detail shots",
        "rights-cleared evidence or explicitly marked simulation; annotations pin to evidence",
        "minimal lower-third/source labels, never a permanent title card",
        "slow investigative dolly, crop-to-detail and handheld drift", "parallax evidence layers and restrained highlight traces",
        ("match_cut", "film_burn", "evidence_wipe"), "sparse", "film grain, practical darkness and source texture",
        "make a claim feel observed and then evidenced", ("permanent progress strip", "central card well", "generic stat card")),
    CreativeDirection.SCIENTIFIC_VISUALIZATION: ComposerContract(
        CreativeDirection.SCIENTIFIC_VISUALIZATION, "scientific_field_composer",
        "diagram/world coordinates with measured radial or Cartesian axes", "one phenomenon fills the frame; labels anchor to the model",
        "procedural model, measured diagram and cited values", "instrument labels at model coordinates; editorial type is secondary",
        "model-space zoom, orbit and sectional cut", "parameter-driven reveals, particle paths and scale changes",
        ("phase_change", "radial_reveal", "section_cut"), "precise", "dark field, calibration grid only when explanatory",
        "show mechanism, scale and causality", ("documentary lower third", "photo-card frame", "decorative bouncy text")),
    CreativeDirection.DATA_STORYTELLING: ComposerContract(
        CreativeDirection.DATA_STORYTELLING, "data_plane_composer",
        "continuous chart/paper coordinates", "one chart world with a memory zone for prior marks",
        "verified series, units, definitions and source annotations", "chart labels, axes and annotations are the typography",
        "pan and zoom across data plane", "draw/morph/rescale marks in data space",
        ("axis_morph", "data_wipe", "scale_shift"), "measured", "paper or quiet plotting surface",
        "make comparison and change legible", ("photo montage", "unrelated decorative card", "generic grid overlay")),
    CreativeDirection.CINEMATIC_MOTION_GRAPHICS: ComposerContract(
        CreativeDirection.CINEMATIC_MOTION_GRAPHICS, "cinematic_graphics_composer",
        "screen-space layers and depth planes", "large silhouette, type beam and negative-space composition",
        "procedural shapes or licensed hero imagery as layers", "few large editorial words with intentional cadence",
        "dolly through layers, snap reframes", "kinetic type, masks and shape choreography",
        ("knife_cut", "shape_wipe", "hard_cut"), "bold", "high-contrast field with directional light",
        "turn an idea into a felt sequence", ("permanent data readout", "caption-card stack", "generic pastel UI")),
    CreativeDirection.CHALKBOARD_BLUEPRINT: ComposerContract(
        CreativeDirection.CHALKBOARD_BLUEPRINT, "drafting_surface_composer",
        "persistent board or orthographic drawing sheet", "one evolving surface, construction-to-detail order",
        "authored strokes, diagrams and dimensions", "handwritten notes / technical title block",
        "board-space pan and drawing-detail zoom", "stroke-order reveal, erase and construction-line build",
        ("erase_wipe", "sheet_pan", "draft_cut"), "deliberate", "physical chalkboard or cyanotype paper",
        "reason visibly from first principles", ("photographic frame", "3D neon wireframe", "permanent header")),
    CreativeDirection.ARCHIVE_EDITORIAL_MONTAGE: ComposerContract(
        CreativeDirection.ARCHIVE_EDITORIAL_MONTAGE, "archive_montage_composer",
        "physical table / print-collage coordinates", "scans, cut-outs and dates form an asymmetric argument",
        "rights-cleared scans/photos with catalogue metadata", "source tag and date are structural, display type is sparse",
        "table dolly, loupe push and print rotation", "cut-out collision, loupe reveal and print lift",
        ("print_slam", "loupe_match", "paper_wipe"), "editorial", "paper, light table or one-ink print texture",
        "make provenance and juxtaposition carry the argument", ("generic evidence card", "fake archive metadata", "uniform tile grid")),
}


@dataclass(frozen=True)
class TimelineCue:
    start_sec: float
    end_sec: float
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TreatmentScene:
    scene_id: str
    purpose: str
    narration: str
    start_sec: float
    end_sec: float
    composition_family: str
    layout_geometry: str
    camera_move: str
    animation_technique: str
    transition_out: str
    asset_requirements: Sequence[str]


@dataclass(frozen=True)
class FilmTreatment:
    title: str
    category: str
    direction: CreativeDirection
    narrative_structure: str
    emotional_progression: Sequence[str]
    visual_opportunities: Sequence[str]
    required_evidence: Sequence[str]
    pacing: str
    typography_strategy: str
    transition_strategy: str
    sound_design: Sequence[str]
    scenes: Sequence[TreatmentScene]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FilmDirector:
    """Creates deterministic, inspectable treatments before any storyboard render."""

    def direct(self, *, title: str, category: str, direction: CreativeDirection,
               narration_segments: Sequence[str], scene_seconds: Sequence[float]) -> FilmTreatment:
        if not narration_segments or len(narration_segments) != len(scene_seconds):
            raise ValueError("Narration and scene durations must be non-empty and aligned.")
        contract = COMPOSER_CONTRACTS[direction]
        starts: List[float] = []
        cursor = 0.0
        for duration in scene_seconds:
            if duration <= 0:
                raise ValueError("Scene durations must be positive.")
            starts.append(cursor)
            cursor += duration
        scene_purposes = ("hook", "mechanism", "consequence", "resolution")
        families = self._families_for(direction)
        scenes = tuple(TreatmentScene(
            scene_id=f"scene_{idx + 1:02d}", purpose=scene_purposes[min(idx, len(scene_purposes)-1)],
            narration=text, start_sec=starts[idx], end_sec=starts[idx] + scene_seconds[idx],
            composition_family=families[idx % len(families)], layout_geometry=contract.layout_strategy,
            camera_move=contract.camera_grammar, animation_technique=contract.animation_grammar,
            transition_out=contract.transitions[idx % len(contract.transitions)],
            asset_requirements=tuple(contract.asset_presentation.split("; ")),
        ) for idx, text in enumerate(narration_segments))
        return FilmTreatment(
            title=title, category=category, direction=direction,
            narrative_structure="hook → mechanism → consequence → resolution",
            emotional_progression=("curiosity", "orientation", "awe", "clarity"),
            visual_opportunities=(contract.narrative_purpose, contract.coordinate_system),
            required_evidence=tuple(contract.asset_presentation.split("; ")),
            pacing="narration-led; scene boundaries align to spoken idea changes",
            typography_strategy=contract.typography_strategy,
            transition_strategy=", ".join(contract.transitions),
            sound_design=("narration first", "quiet directional ambience", "transitions punctuate meaning"),
            scenes=scenes,
        )

    @staticmethod
    def _families_for(direction: CreativeDirection) -> Sequence[str]:
        return {
            CreativeDirection.SCIENTIFIC_VISUALIZATION: ("radial_model", "sectional_model", "particle_field", "scale_readout"),
            CreativeDirection.DOCUMENTARY_INVESTIGATION: ("observatory_wide", "aperture_detail", "evidence_dossier", "night_sky_close"),
            CreativeDirection.DATA_STORYTELLING: ("chart_build", "comparison_plane", "annotated_result"),
            CreativeDirection.CINEMATIC_MOTION_GRAPHICS: ("type_beam", "silhouette", "layer_dolly"),
            CreativeDirection.CHALKBOARD_BLUEPRINT: ("construction_sheet", "detail_zoom", "annotated_mechanism"),
            CreativeDirection.ARCHIVE_EDITORIAL_MONTAGE: ("light_table", "print_collision", "loupe_detail"),
        }[direction]


def audit_composition_diversity(scenes: Iterable[TreatmentScene]) -> Dict[str, Any]:
    """Audits actual planned composition variables, not image/style labels."""
    items = list(scenes)
    fields = ("composition_family", "layout_geometry", "camera_move", "animation_technique", "transition_out")
    distribution = {field: sorted({getattr(scene, field) for scene in items}) for field in fields}
    adjacent_repeats = [items[i].scene_id for i in range(1, len(items))
                        if items[i].composition_family == items[i-1].composition_family]
    return {
        "scene_count": len(items), "distribution": distribution,
        "adjacent_composition_repeats": adjacent_repeats,
        "passes": bool(items) and not adjacent_repeats and len(distribution["composition_family"]) > 1,
    }
