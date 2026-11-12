from backend.app.rendering.creative_direction import (
    CreativeDirection,
    FilmDirector,
    audit_composition_diversity,
)
from backend.app.rendering.independent_composers import composer_for


def test_treatment_drives_independent_scene_grammars():
    director = FilmDirector()
    scientific = director.direct(
        title="How a Black Hole Works", category="Science",
        direction=CreativeDirection.SCIENTIFIC_VISUALIZATION,
        narration_segments=["one", "two", "three", "four"], scene_seconds=[2, 2, 2, 2],
    )
    documentary = director.direct(
        title="How a Black Hole Works", category="Science",
        direction=CreativeDirection.DOCUMENTARY_INVESTIGATION,
        narration_segments=["one", "two", "three", "four"], scene_seconds=[2, 2, 2, 2],
    )
    assert audit_composition_diversity(scientific.scenes)["passes"]
    assert audit_composition_diversity(documentary.scenes)["passes"]
    assert scientific.scenes[0].layout_geometry != documentary.scenes[0].layout_geometry
    assert scientific.scenes[0].camera_move != documentary.scenes[0].camera_move
    assert scientific.scenes[0].transition_out != documentary.scenes[0].transition_out


def test_native_composers_are_not_the_legacy_plate():
    science = composer_for("scientific_visualization")
    documentary = composer_for("documentary_investigation")
    assert science.composer_id != documentary.composer_id
    assert science.render(0.0, 0, 180, 320).size == (180, 320)
    assert documentary.render(0.0, 0, 180, 320).size == (180, 320)
