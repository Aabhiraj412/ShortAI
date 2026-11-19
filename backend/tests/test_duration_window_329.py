"""
Stage 3.29 duration acceptance window: 30.0-58.0 s, both inclusive, measured on the final MP4.
"""
import json
import subprocess
from pathlib import Path

import pytest

from backend.app.rendering import duration_standards as ds

CASES = [(29.9, False), (30.0, True), (45.0, True), (50.0, True), (58.0, True), (58.1, False)]


@pytest.mark.parametrize("dur,ok", CASES)
def test_acceptance_window_boundaries(dur, ok):
    assert ds.duration_accepted(dur) is ok
    assert (ds.duration_violation(dur) is None) is ok
    valid, _ = ds.validate_production_duration(dur)
    assert valid is ok


def test_qc_service_uses_the_shared_window():
    from backend.app.services import qc_service
    src = Path(qc_service.__file__).read_text(encoding="utf-8")
    assert "PRODUCTION_MIN_DURATION_SEC" in src and "PRODUCTION_MAX_DURATION_SEC" in src
    assert "49.5" not in src and "60.5" not in src


@pytest.mark.parametrize("dur,ok", CASES)
def test_visual_quality_gate_duration_dimension(dur, ok):
    from backend.app.services import visual_quality_gate as vqg
    th = vqg.DEFAULT_THRESHOLDS
    assert (th["min_duration"] <= dur <= th["max_duration"]) is ok


def test_constants_and_configuration_defaults_agree():
    from backend.app.content.creative_production_policy import CreativeProductionPolicy
    assert ds.ACCEPTED_DURATION_RANGE_SEC == (30.0, 58.0)
    assert ds.PRODUCTION_MIN_FRAMES == 900 and ds.PRODUCTION_MAX_FRAMES == 1740
    src = Path(__file__).resolve().parents[1] / "app"
    policy_src = (src / "content" / "creative_production_policy.py").read_text(encoding="utf-8")
    assert '"target_duration_sec": [30.0, 58.0]' in policy_src
    cc = (src / "services" / "control_center_service.py").read_text(encoding="utf-8")
    assert "if not (30 <= dur <= 58):" in cc
    html = (src / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "Target Duration (30-58s)" in html and 'min="30" max="58"' in html


def test_orchestrator_measures_the_final_mp4():
    src = (Path(__file__).resolve().parents[1] / "app" / "orchestration" /
           "crazzydocs_master_orchestrator.py").read_text(encoding="utf-8")
    qc = src[src.index("Duration is measured on the final rendered MP4"):][:900]
    assert "_probe_final(final_video_path)" in qc and "duration_violation(measured_dur)" in qc
    assert "50-58s window" not in src and "dur >= 60.0 or dur < 50.0" not in src


def _ffmpeg():
    from backend.app.core.paths import get_ffmpeg_path, get_ffprobe_path
    return get_ffmpeg_path(), get_ffprobe_path()


@pytest.mark.parametrize("dur,ok", [(29.9, False), (30.0, True), (58.0, True), (58.1, False)])
def test_real_mp4_measured_duration(dur, ok, tmp_path):
    ff, fp = _ffmpeg()
    if not (ff and Path(ff).is_file() and fp and Path(fp).is_file()):
        pytest.skip("ffmpeg/ffprobe not installed in this checkout (set CRAZZYDOCS_TOOLS_DIR)")
    out = tmp_path / f"d{dur}.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x112:r=30", "-f", "lavfi",
                    "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{dur}", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(out)], check=True)
    measured = float(subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of",
                                     "default=nw=1:nk=1", str(out)], capture_output=True, text=True).stdout)
    assert abs(measured - dur) < 0.06
    assert ds.duration_accepted(round(measured, 1)) is ok
