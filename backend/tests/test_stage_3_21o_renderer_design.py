from PIL import Image, ImageDraw

from backend.app.rendering.documentary_storyboard_builder import _choose_scene_layout
from backend.app.rendering.style_treatments import compose_direction_frame


def test_each_creative_direction_has_distinct_layout_family():
    expected = {
        "documentary": {"full_bleed_evidence", "archival_caption", "evidence_detail"},
        "pop_info": {"hero_stat_card", "editorial_split", "kinetic_type_burst"},
        "data_story": {"chart_canvas", "metric_progress", "evidence_timeline"},
        "chalk_talk": {"mechanism_flow", "relationship_diagram", "equation_board"},
        "creator_motion_graphics": {"layered_photo_mask", "offset_kinetic_type", "reveal_banner"},
        "cinematic_explainer": {"immersive_full_frame", "chapter_reveal", "negative_space_title"},
        "map_based_storytelling": {"animated_route", "location_focus", "territory_sequence"},
    }
    for style, layouts in expected.items():
        actual = {
            _choose_scene_layout(style, "CONTEXT", index)
            for index in range(3)
        }
        assert actual == layouts


def test_direction_compositor_produces_real_composition_variants():
    base = Image.new("RGB", (360, 640), (15, 25, 35))
    draw = ImageDraw.Draw(base)
    draw.rectangle((36, 128, 324, 416), fill=(220, 110, 55))
    styles = (
        "documentary", "pop_info", "data_story", "chalk_talk",
        "creator_motion_graphics", "cinematic_explainer", "map_based_storytelling",
    )
    outputs = [
        compose_direction_frame(
            base,
            style,
            topic_title="A Story About Evidence",
            segment_text="New evidence changes how the story is understood",
            stage_label="REVELATION",
            stat_text="76 PERCENT",
            beat_idx=index,
        )
        for index, style in enumerate(styles)
    ]
    assert all(output.size == base.size for output in outputs)
    assert len({output.tobytes() for output in outputs}) == len(styles)
