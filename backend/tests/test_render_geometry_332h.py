"""Stage 3.32H: FFmpeg scale/pad geometry regression tests (synthetic media only, no production rendering)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.rendering import ffmpeg_geometry as geo
from backend.app.scheduler.retry_policy import DETERMINISTIC, classify_error


def _find_ffmpeg():
    candidates = [os.environ.get("CRAZZYDOCS_FFMPEG", "")]
    try:
        from backend.app.core.paths import FFMPEG_DEFAULT_PATH
        candidates.append(str(FFMPEG_DEFAULT_PATH))
    except Exception:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:                      # worktrees share the main checkout's tools directory
        candidates.append(str(parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"))
    candidates.append(shutil.which("ffmpeg") or "")
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


FFMPEG = _find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg binary not available")


def _ffprobe_dims(path):
    probe = str(Path(FFMPEG).with_name("ffprobe.exe" if FFMPEG.endswith(".exe") else "ffprobe"))
    out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,pix_fmt", "-of", "json", path], capture_output=True, text=True,
                         timeout=30, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"], s["pix_fmt"]


def _make_source(tmp_path, w, h, ext="mp4"):
    path = str(tmp_path / f"src_{w}x{h}.{ext}")
    args = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=s={w}x{h}:d=1:r=10"]
    args += ([] if ext == "gif" else ["-pix_fmt", "yuv444p", "-c:v", "libx264", "-preset", "ultrafast"]) + [path]
    subprocess.run(args, capture_output=True, text=True, timeout=60, check=True)
    return path


# -- pure geometry -------------------------------------------------------------------------------------------
def test_even_floor():
    assert [geo.even_floor(v) for v in (855, 854, 3, 2, 1, 0)] == [854, 854, 2, 2, 2, 2]


@pytest.mark.parametrize("w,h", [(0, 10), (10, -2), (1, 1), (9000, 10), (10.5, 20), ("x", 10), (True, 10), (None, 4)])
def test_invalid_dimensions_rejected(w, h):
    with pytest.raises(ValueError):
        geo.validate_dimensions(w, h)


def test_filter_targets_are_even_and_force_even_scale():
    f = geo.fit_and_pad_filter(860, 855, color="0x101727")
    assert "scale=860:854:force_original_aspect_ratio=decrease:force_divisible_by=2" in f
    assert f.endswith("pad=860:854:(ow-iw)/2:(oh-ih)/2:color=0x101727")


def test_stderr_tail_is_bounded():
    assert geo.stderr_tail("x" * 5000, 100) == "..." + "x" * 100
    assert geo.stderr_tail(None) == ""


def test_padding_error_is_deterministic_not_transient():
    msg = "FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0x1] Padded dimensions cannot be smaller than input dimensions."
    assert classify_error(RuntimeError(msg))[0] == DETERMINISTIC
    assert classify_error(RuntimeError("[libx264] width not divisible by 2 (333x501)"))[0] == DETERMINISTIC


def test_all_production_pad_sites_force_even_scale():
    root = Path(__file__).resolve().parents[1] / "app" / "rendering"
    offenders = []
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if "force_original_aspect_ratio=decrease" in line and "pad=" in line and "force_divisible_by=2" not in line:
                offenders.append(f"{py.name}: {line.strip()[:80]}")
    assert offenders == []


# -- real FFmpeg on synthetic media --------------------------------------------------------------------------
SOURCES = [(320, 480), (480, 320), (400, 400), (333, 501), (501, 333), (1081, 1919), (2, 2)]


@needs_ffmpeg
@pytest.mark.parametrize("target", [(860, 855), (1080, 1920), (1920, 1080), (1080, 1080), (721, 1281)])
@pytest.mark.parametrize("src", SOURCES)
def test_fit_and_pad_any_source_into_any_target(tmp_path, src, target):
    source = _make_source(tmp_path, *src)
    out = str(tmp_path / "out.mp4")
    vf = geo.fit_and_pad_filter(*target) + ",setsar=1,format=yuv420p"
    proc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", source, "-vf", vf, "-t", "0.3", "-an",
                           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, geo.stderr_tail(proc.stderr)
    assert _ffprobe_dims(out) == (geo.even_floor(target[0]), geo.even_floor(target[1]), "yuv420p")


@needs_ffmpeg
def test_old_odd_viewport_filter_reproduces_the_defect(tmp_path):
    """Guards the root-cause analysis: the pre-3.32H filter fails exactly as production did."""
    source = _make_source(tmp_path, 320, 480)
    vf = "scale=860:855:force_original_aspect_ratio=decrease,pad=860:855:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    proc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", source, "-vf", vf, "-t", "0.3",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "o.mp4")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0 and "Padded dimensions cannot be smaller" in proc.stderr


def _engine():
    from backend.app.rendering.explainer_animation_engine import ExplainerAnimationEngine
    return ExplainerAnimationEngine(ffmpeg_path=FFMPEG)


def _adapt(engine, src, out, **kw):
    args = dict(input_anim_path=src, out_mp4_path=out, duration_sec=1.0, beat_idx=1, total_beats=4,
                stage_label="CONTEXT", topic_title="Synthetic Geometry Test", segment_text="A synthetic test clip.",
                accent_color=(80, 160, 255), fps=10.0)
    args.update(kw)
    return engine.adapt_existing_animation_to_portrait(**args)


@needs_ffmpeg
@pytest.mark.parametrize("src,ext", [((320, 480), "mp4"), ((480, 320), "mp4"), ((400, 400), "gif"), ((333, 501), "gif")])
def test_priority_b_adapter_portrait_square_landscape_and_odd(tmp_path, src, ext):
    source = _make_source(tmp_path, *src, ext=ext)
    out = str(tmp_path / "adapted.mp4")
    res = _adapt(_engine(), source, out)
    assert res["path"] == out and os.path.isfile(out)
    assert _ffprobe_dims(out) == (1080, 1920, "yuv420p")


@needs_ffmpeg
def test_priority_b_adapter_failure_is_bounded_and_cleans_up(tmp_path):
    bad = tmp_path / "not_media.gif"
    bad.write_bytes(b"this is not an animation")
    out = tmp_path / "adapted.mp4"
    before = {p.name for p in Path(os.environ.get("TEMP", str(tmp_path))).glob("cd_pB_overlay_*")}
    with pytest.raises(RuntimeError) as exc:
        _adapt(_engine(), str(bad), str(out))
    assert "Priority B adaptation failed" in str(exc.value)
    assert len(str(exc.value)) < 1700                     # stderr is truncated, not dumped whole
    assert not out.exists()                               # no partial output
    after = {p.name for p in Path(os.environ.get("TEMP", str(tmp_path))).glob("cd_pB_overlay_*")}
    assert after <= before                                # the overlay temp file was removed


def test_priority_b_adapter_timeout_kills_and_reports(tmp_path, monkeypatch):
    from backend.app.rendering import explainer_animation_engine as eae
    src = tmp_path / "a.gif"
    src.write_bytes(b"GIF89a")
    out = tmp_path / "o.mp4"

    def fake_run(cmd, **kw):
        out.write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 45))

    monkeypatch.setattr(eae.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out after 45s"):
        _adapt(eae.ExplainerAnimationEngine(ffmpeg_path="ffmpeg"), str(src), str(out))
    assert not out.exists()
