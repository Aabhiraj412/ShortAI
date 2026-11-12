from PIL import Image, ImageDraw

from backend.app.content.creative_identity_policy import (
    AdaptiveVisualStyleSelector,
    CreativeIdentityPolicy,
)
from backend.app.content.visual_diversity_policy import (
    VisualDiversityValidator,
    compute_asset_sha256,
    find_image_duplicate_reason,
)
from backend.app.rendering.styles.style_system import StyleConsistencyValidator, VisualStyle


def _patterned_photo(path, size=(640, 480)):
    image = Image.new("RGB", size, (28, 54, 72))
    draw = ImageDraw.Draw(image)
    for index in range(12):
        x = 25 + index * 49
        y = 35 + (index % 4) * 92
        color = (
            55 + (index * 17) % 170,
            45 + (index * 29) % 180,
            35 + (index * 37) % 190,
        )
        draw.rectangle((x, y, x + 34, y + 54), fill=color)
        draw.ellipse((x + 5, y + 12, x + 29, y + 36), outline=(238, 230, 205), width=3)
    image.save(path)
    return image


def _photo_beat(beat_id, path):
    return {
        "beat_id": beat_id,
        "start_sec": 0.0,
        "end_sec": 2.0,
        "script_segment": "The archival photograph records the scientific expedition.",
        "visual_type": "historical_photo",
        "visual_description": "Authentic expedition photograph with documented visual evidence.",
        "asset_path": str(path),
        "asset_hash": compute_asset_sha256(str(path)),
        "visual_generation_instructions": "Frame the authentic source photograph.",
        "transition": "cut",
        "relevance_score": 0.9,
        "reuse_count": 1,
    }


def test_perceptual_matching_detects_crop_resize_and_tint_but_not_unrelated_photos(tmp_path):
    original_path = tmp_path / "source.png"
    original = _patterned_photo(original_path)

    cropped = original.crop((64, 48, 576, 432)).resize((800, 600), Image.Resampling.LANCZOS)
    cropped_path = tmp_path / "crop_resize.png"
    cropped.save(cropped_path)

    tinted = Image.blend(cropped, Image.new("RGB", cropped.size, (85, 35, 25)), 0.12)
    tinted_path = tmp_path / "crop_resize_tint.png"
    tinted.save(tinted_path)

    unrelated_path = tmp_path / "unrelated.png"
    unrelated = Image.new("RGB", (640, 480), (24, 68, 42))
    unrelated_draw = ImageDraw.Draw(unrelated)
    for index in range(15):
        x = 12 + index * 41
        unrelated_draw.line((x, 0, 620 - x // 2, 479), fill=(240, 190 - index * 5, 65), width=7)
    unrelated.save(unrelated_path)

    assert find_image_duplicate_reason(str(original_path), str(cropped_path))
    assert find_image_duplicate_reason(str(original_path), str(tinted_path))
    assert find_image_duplicate_reason(str(original_path), str(unrelated_path)) is None


def test_flat_color_images_do_not_false_match_and_source_identity_still_matches(tmp_path):
    first = tmp_path / "flat_a.png"
    second = tmp_path / "flat_b.png"
    Image.new("RGB", (320, 240), (20, 40, 60)).save(first)
    Image.new("RGB", (320, 240), (210, 170, 125)).save(second)

    assert find_image_duplicate_reason(str(first), str(second)) is None
    assert find_image_duplicate_reason(
        str(first),
        str(second),
        first_source_identity="https://commons.wikimedia.org/wiki/File:Source.jpg",
        second_source_identity="https://commons.wikimedia.org/wiki/File:Source.jpg?width=640",
    ) == "source_identity"


def test_storyboard_audit_groups_near_duplicate_source_photos(tmp_path):
    original_path = tmp_path / "photo.png"
    original = _patterned_photo(original_path)
    crop_path = tmp_path / "photo_crop.png"
    original.crop((64, 48, 576, 432)).resize((800, 600), Image.Resampling.LANCZOS).save(crop_path)

    audit = VisualDiversityValidator.validate_storyboard(
        [
            _photo_beat("first", original_path),
            _photo_beat("second", crop_path),
        ],
        check_files_on_disk=True,
        min_distinct_assets=1,
    )

    assert not audit.is_valid
    assert audit.unique_photographic_assets == 1
    assert audit.reused_source_images == 1
    assert audit.max_source_image_reuse == 2
    assert audit.near_duplicate_groups == 1
    assert any("narrative reason" in violation for violation in audit.violations)


def test_adaptive_style_selection_and_justified_transitions():
    documentary = AdaptiveVisualStyleSelector.select(
        category="History",
        stage="CONTEXT",
        narrative_purpose="Show primary evidence",
        segment_text="An archival photograph documents the expedition.",
        asset_type="authentic_photo",
    )
    pop_info = AdaptiveVisualStyleSelector.select(
        category="Biology",
        stage="CONTEXT",
        narrative_purpose="Emphasize a measured result",
        segment_text="Researchers found 76 percent of organisms emitted light.",
        previous_style=documentary.visual_style.value,
    )
    chalk_talk = AdaptiveVisualStyleSelector.select(
        category="Engineering",
        stage="MECHANISM",
        narrative_purpose="Explain the process",
        segment_text="This force causes the circuit to transfer energy.",
        previous_style=pop_info.visual_style.value,
    )
    creator_motion = AdaptiveVisualStyleSelector.select(
        category="Business",
        stage="PAYOFF",
        narrative_purpose="Land the reveal",
        segment_text="The discovery changed the industry.",
        previous_style=chalk_talk.visual_style.value,
    )

    selections = [documentary, pop_info, chalk_talk, creator_motion]
    assert [selection.visual_style.value for selection in selections] == [
        "documentary",
        "pop_info",
        "chalk_talk",
        "creator_motion_graphics",
    ]
    assert all(selection.rationale for selection in selections)
    assert all(selection.transition_rationale for selection in selections)

    beats = [
        {
            "id": f"beat_{index}",
            "visual_style": selection.visual_style.value,
            "style_selection_rationale": selection.rationale,
            "style_transition_rationale": selection.transition_rationale,
        }
        for index, selection in enumerate(selections)
    ]
    audit = CreativeIdentityPolicy.validate_timeline_beats(
        beats=beats,
        selected_style="documentary",
        topic_title="Measured Scientific Discovery",
    )
    assert audit.is_compliant, audit.violations

    style_ok, style_violations = StyleConsistencyValidator.validate_plan(
        VisualStyle.POP_INFO,
        [{"id": beat["id"], **beat} for beat in beats],
    )
    assert style_ok, style_violations

    beats[1]["style_transition_rationale"] = ""
    rejected = CreativeIdentityPolicy.validate_timeline_beats(
        beats=beats,
        selected_style="documentary",
        topic_title="Measured Scientific Discovery",
    )
    assert not rejected.is_compliant
    assert any("without a transition rationale" in violation for violation in rejected.violations)
