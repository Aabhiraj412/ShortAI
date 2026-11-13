"""
The visual quality gate analyses real video files. These tests synthesise small MP4s with ffmpeg
(a blank one, a silent one, a moving one) so the detectors are checked against actual frames and
audio, not metadata. Skipped when no ffmpeg binary is available.
"""
import os
import subprocess
from pathlib import Path

import pytest

from backend.app.services import visual_quality_gate as vqg


def _tools():
    from backend.app.core.paths import get_ffmpeg_path, get_ffprobe_path
    ff, fp = get_ffmpeg_path(), get_ffprobe_path()     # conftest points a worktree at the main tools
    if not Path(ff).is_file():
        pytest.skip("ffmpeg not available")
    return ff, fp


def _make(path, video_src, audio_src, dur, size="1080x1920"):
    ff, _ = _tools()
    sep = ":" if "=" in video_src else "="
    cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{video_src}{sep}s={size}:d={dur}:r=30"]
    if audio_src:
        cmd += ["-f", "lavfi", "-i", f"{audio_src}:d={dur}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if audio_src:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, timeout=120)
    return str(path)


def test_blank_silent_video_fails_with_critical_issues(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "blank.mp4", "color=c=black", "anullsrc=r=48000:cl=stereo", 16)
    rep = vqg.analyse(v, ffmpeg=ff, ffprobe=fp)
    problems = {i["problem"] for i in rep.issues if i["severity"] == "critical"}
    assert "blank frame" in problems and "missing narration" in problems
    assert rep.passed is False


def test_wrong_aspect_and_duration_are_critical(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "wide.mp4", "testsrc2", "sine=f=440:r=48000", 8, size="1280x720")
    rep = vqg.analyse(v, ffmpeg=ff, ffprobe=fp)
    problems = {i["problem"] for i in rep.issues}
    assert {"wrong resolution / aspect", "duration outside Shorts range"} <= problems and not rep.passed


def test_moving_video_with_audio_has_no_blank_or_frozen_findings(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "moving.mp4", "testsrc2", "sine=f=330:r=48000", 16)
    rep = vqg.analyse(v, ffmpeg=ff, ffprobe=fp)
    problems = {i["problem"] for i in rep.issues}
    assert "blank frame" not in problems and "frozen picture" not in problems and "missing narration" not in problems
    assert rep.measurements["container"]["width"] == 1080 and rep.dimensions["motion"]["score"] > 50


def test_typography_is_not_invented_without_a_layout_audit(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "m.mp4", "testsrc2", "sine=f=330:r=48000", 16)
    rep = vqg.analyse(v, ffmpeg=ff, ffprobe=fp)
    assert rep.dimensions["typography"]["score"] is None and rep.dimensions["typography"]["basis"] == "not_measured"
    rep2 = vqg.analyse(v, ffmpeg=ff, ffprobe=fp, layout_audit=[
        {"t": 1.0, "outside_safe": [{"text": "Prize", "box": [400, 100, 480, 140]}], "too_small": [], "overlaps": []},
        {"t": 2.0, "outside_safe": [], "too_small": [], "overlaps": []}])
    assert rep2.dimensions["typography"]["score"] is not None
    assert any(i["problem"] == "text outside safe area" and i["t"] == 1.0 for i in rep2.issues)


def test_weights_and_thresholds_are_configurable(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "m.mp4", "testsrc2", "sine=f=330:r=48000", 16)
    strict = vqg.analyse(v, ffmpeg=ff, ffprobe=fp, thresholds={"pass_score": 101})
    assert strict.passed is False and strict.thresholds["pass_score"] == 101
    only_motion = vqg.analyse(v, ffmpeg=ff, ffprobe=fp, weights={k: 0 for k in vqg.DEFAULT_WEIGHTS} | {"motion": 1})
    assert only_motion.overall_score == only_motion.dimensions["motion"]["score"]


def test_report_is_written_as_json(tmp_path):
    ff, fp = _tools()
    v = _make(tmp_path / "m.mp4", "testsrc2", "sine=f=330:r=48000", 16)
    p = vqg.write_report(vqg.analyse(v, ffmpeg=ff, ffprobe=fp), tmp_path / "r" / "quality_report.json")
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data["dimensions"]) == set(vqg.DEFAULT_WEIGHTS) and "issues" in data and data["gate_version"]
