"""Isolated Stage 3.21P creative-direction proof of concept.

Writes only to media/productions/val_3_21p_black_hole_* and its local report
artifacts.  It does not create a production workspace, touch the ledger, start
the scheduler, acquire network media, or invoke an uploader.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageChops, ImageDraw, ImageStat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.rendering.creative_direction import (  # noqa: E402
    CreativeDirection, FilmDirector, audit_composition_diversity,
)
from backend.app.rendering.independent_composers import composer_for  # noqa: E402

FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
NARRATION = (
    "A black hole is not an empty hole in space. It is a region where gravity becomes so strong "
    "that, past its event horizon, even light cannot return. Gas falling nearby can orbit and heat "
    "until it shines brightly. The black hole itself stays dark, but its gravity bends light and changes "
    "the paths of nearby matter. By observing those effects, scientists test how gravity shapes the universe."
)


def _run(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def synthesize_narration(out_wav: Path) -> float:
    """Use the local Windows voice only; no cloud TTS or external request."""
    safe = NARRATION.replace("'", "''")
    safe_path = str(out_wav).replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.Rate=-1; "
        f"$voice.SetOutputToWaveFile('{safe_path}'); "
        f"$voice.Speak('{safe}'); $voice.Dispose()"
    )
    _run(["powershell", "-NoProfile", "-Command", ps])
    props = json.loads(_run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out_wav)]).stdout)
    return float(props["format"]["duration"])


def render_direction(direction: CreativeDirection, treatment, audio_path: Path, out_mp4: Path) -> Dict[str, float]:
    composer = composer_for(direction.value)
    fps, width, height = 30, 1080, 1920
    duration = treatment.scenes[-1].end_sec
    frames = round(duration * fps)
    command = [str(FFMPEG), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
               "-i", "-", "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_mp4)]
    started = time.perf_counter()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    for frame_idx in range(frames):
        t = frame_idx / fps
        scene_idx = min(len(treatment.scenes) - 1, next(i for i, scene in enumerate(treatment.scenes) if t < scene.end_sec))
        proc.stdin.write(composer.render(t, scene_idx, width, height).tobytes())
    proc.stdin.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    code = proc.wait()
    if code:
        raise RuntimeError(f"FFmpeg failed for {direction.value}: {stderr[-1200:]}")
    elapsed = time.perf_counter() - started
    return {"duration_sec": duration, "frames": frames, "elapsed_sec": round(elapsed, 3), "render_fps": round(frames / elapsed, 2), "composer": composer.composer_id}


def contact_sheet(video: Path, out_png: Path, label: str, duration: float) -> List[Path]:
    samples = [duration * p for p in (.03, .16, .29, .42, .55, .68, .81, .94)]
    images: List[Image.Image] = []
    paths: List[Path] = []
    for i, second in enumerate(samples):
        png = out_png.parent / f"{label}_frame_{i+1:02d}.png"
        _run([str(FFMPEG), "-y", "-ss", f"{second:.3f}", "-i", str(video), "-frames:v", "1", str(png)])
        paths.append(png)
        images.append(Image.open(png).convert("RGB").resize((270, 480)))
    sheet = Image.new("RGB", (1080, 1040), (13, 13, 16))
    draw = ImageDraw.Draw(sheet)
    for i, image in enumerate(images):
        x, y = (i % 4) * 270, (i // 4) * 520 + 30
        sheet.paste(image, (x, y))
        draw.text((x + 10, y + 485), f"{samples[i]:04.1f}s", fill=(245, 245, 245))
    sheet.save(out_png)
    return paths


def pixel_distance(a: Path, b: Path) -> float:
    left = Image.open(a).convert("RGB").resize((96, 171))
    right = Image.open(b).convert("RGB").resize((96, 171))
    return round(sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3, 2)


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "media" / "productions" / f"val_3_21p_black_hole_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    audio = out / "identical_local_narration.wav"
    narration_duration = synthesize_narration(audio)
    if not 20 <= narration_duration <= 30:
        raise RuntimeError(f"Local narration duration {narration_duration:.2f}s is outside 20–30 second POC target")
    segments = [NARRATION.split(". ")[i].strip().rstrip(".") + "." for i in range(4)]
    durations = [narration_duration / 4] * 4
    director = FilmDirector()
    results: Dict[str, object] = {"topic": "HOW A BLACK HOLE WORKS", "narration": NARRATION, "narration_duration_sec": narration_duration,
                                  "safety": {"network_acquisition": False, "production_ledger_mutated": False, "scheduler_started": False, "publishing": "manual_approval"}}
    frames: Dict[str, List[Path]] = {}
    for direction in (CreativeDirection.SCIENTIFIC_VISUALIZATION, CreativeDirection.DOCUMENTARY_INVESTIGATION):
        treatment = director.direct(title="HOW A BLACK HOLE WORKS", category="Science", direction=direction,
                                    narration_segments=segments, scene_seconds=durations)
        video = out / f"black_hole_{direction.value}.mp4"
        render_stats = render_direction(direction, treatment, audio, video)
        label = direction.value
        frames[label] = contact_sheet(video, out / f"contact_sheet_{label}.png", label, narration_duration)
        results[label] = {"video": str(video), "contact_sheet": str(out / f"contact_sheet_{label}.png"), "treatment": treatment.to_dict(),
                           "composition_audit": audit_composition_diversity(treatment.scenes), "render": render_stats}
    comparison = [{"timestamp_sec": round(narration_duration * p, 2), "mean_absolute_rgb_difference": pixel_distance(frames[CreativeDirection.SCIENTIFIC_VISUALIZATION.value][i], frames[CreativeDirection.DOCUMENTARY_INVESTIGATION.value][i])}
                  for i, p in enumerate((.03, .16, .29, .42, .55, .68, .81, .94))]
    results["frame_comparison"] = comparison
    # A dark-space subject necessarily shares a large black background.  A score of
    # ten on the 0–255 mean-absolute RGB scale therefore denotes materially
    # different occupied/coloured geometry rather than demanding a palette swap.
    results["visual_acceptance"] = {
        "passes": all(row["mean_absolute_rgb_difference"] >= 10 for row in comparison),
        "pixel_difference_threshold": 10,
        "scientific_strategy": "model-space radial field, calibration coordinates, parameter-driven light paths",
        "documentary_strategy": "lens/image-space observatory shots, silhouette, grain and editorial film transitions",
    }
    (out / "stage_3_21p_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output_dir": str(out), "results": results}, indent=2, default=str))


if __name__ == "__main__":
    main()
