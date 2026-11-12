from pathlib import Path

import pytest

from backend.scripts.run_stage_3_21r_black_hole import (
    _audio_fingerprint,
    _audio_similarity,
    _frame_comparison,
    render,
)


def test_silent_audio_fingerprint_does_not_divide_by_zero(monkeypatch):
    monkeypatch.setattr(
        "backend.scripts.run_stage_3_21r_black_hole._run_bytes",
        lambda *args, **kwargs: bytes(4096 * 2),
    )

    silence = _audio_fingerprint(Path("unused.mp4"))

    assert len(silence) == 2
    assert all(value == 0 for band in silence for value in band)
    assert _audio_similarity([[1.0] * len(silence[0])], silence[:1]) == 0


def test_visual_similarity_uses_hashes_and_pixel_differences():
    same = bytes([32] * (32 * 32))
    changed = bytes([220] * (32 * 32))

    identical = _frame_comparison([same] * 4, [same] * 4)
    different = _frame_comparison([same] * 4, [changed] * 4)

    assert identical["mean_hash_distance"] == 0
    assert identical["mean_pixel_difference"] == 0
    assert identical["near_match_fraction"] == 1
    assert different["mean_pixel_difference"] > .7


def test_final_render_is_gated_on_narration_audition(tmp_path):
    with pytest.raises(ValueError, match="operator listening"):
        render(tmp_path, audio_reviewed=False)


def test_dashboard_live_panel_does_not_fall_back_to_history():
    template = Path(r"D:\Agents\CrazzyDocs\backend\app\templates\dashboard.html").read_text(encoding="utf-8")

    assert "progressRes.active_production || progressRes.displayed_production" not in template
    assert "Worker idle. Historical runs remain available in run history." in template
