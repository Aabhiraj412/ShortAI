"""Prepare and render the isolated Stage 3.21R black-hole sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.orchestration.production_workspace import ProductionWorkspace  # noqa: E402
from backend.app.rendering.lemo_opuscar_adapter import LemoOpuscarAdapter, PINNED_COMMIT  # noqa: E402

WORKSPACE = ROOT / "media" / "productions" / "val_3_21r_black_hole_20261111_2257"
FILM_SOURCE = ROOT / "backend" / "scripts" / "stage_3_21r_film"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
FPS = 30
SIZE = (1080, 1920)
MODEL_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODEL_FILES = {
    "kokoro-v1.0.fp16.onnx": {
        "url": f"{MODEL_RELEASE}/kokoro-v1.0.fp16.onnx",
        "sha256": "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
    },
    "voices-v1.0.bin": {
        "url": f"{MODEL_RELEASE}/voices-v1.0.bin",
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    },
}
NARRATION_SENTENCES = (
    "Imagine falling into a black hole. What would someone far away see?",
    "The galaxy M eighty-seven lies fifty-five million light-years away. Its ring was reconstructed from radio-telescope data.",
    "Spitzer mapped M eighty-seven in infrared, revealing its stars and a powerful jet.",
    "The jet comes from surrounding matter, not the black hole's dark center.",
    "Gravity bends light. Near a black hole, spacetime curves.",
    "The event horizon is a boundary, not a surface. Cross it, and every future path points inward.",
    "From far away, a falling signal reddens and fades. The faller's clock ticks normally.",
    "Tidal gravity pulls unevenly; its strength at the horizon depends on mass.",
    "A supermassive black hole may have gentler horizon tides. No shell, no cosmic vacuum cleaner—just no escape.",
)
NARRATION_TEXT = " ".join(NARRATION_SENTENCES)
ASSETS = (
    {
        "key": "m87_eht",
        "filename": "m87_eht.jpg",
        "original_filename": "Black_hole_-_Messier_87.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Black_hole_-_Messier_87.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:Black_hole_-_Messier_87.jpg",
        "creator": "Event Horizon Telescope Collaboration",
        "institution": "Event Horizon Telescope / ESO",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Event Horizon Telescope Collaboration / ESO; changes: cropped and animated.",
        "media_type": "Radio-interferometric observational reconstruction, not an ordinary photograph.",
        "scene": "Hook, M87* observational image, and closing frame.",
    },
    {
        "key": "m87_polarized",
        "filename": "m87_polarized.jpg",
        "original_filename": "A_view_of_the_M87_supermassive_black_hole_in_polarised_light_(eso2105a).jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/fd/A_view_of_the_M87_supermassive_black_hole_in_polarised_light_%28eso2105a%29.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:A_view_of_the_M87_supermassive_black_hole_in_polarised_light_(eso2105a).jpg",
        "creator": "Event Horizon Telescope Collaboration",
        "institution": "European Southern Observatory (ESO)",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "ESO / Event Horizon Telescope Collaboration; changes: cropped and animated.",
        "media_type": "Polarized radio-light observational reconstruction.",
        "scene": "Polarization and magnetic-field evidence.",
    },
    {
        "key": "m87_spitzer",
        "filename": "m87_spitzer.jpg",
        "original_filename": "PIA23122~orig.jpg",
        "url": "https://images-assets.nasa.gov/image/PIA23122/PIA23122~orig.jpg",
        "source_page": "https://photojournal.jpl.nasa.gov/catalog/PIA23122",
        "creator": "NASA/JPL-Caltech/IPAC/Event Horizon Telescope Collaboration",
        "institution": "NASA Spitzer Space Telescope",
        "license": "NASA media public-use guidance (U.S. government work; no endorsement implied).",
        "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
        "attribution": "NASA/JPL-Caltech/IPAC/Event Horizon Telescope Collaboration; changes: cropped and animated.",
        "media_type": "Spitzer infrared composite of M87 and its jet.",
        "scene": "Infrared galaxy and jet evidence.",
    },
)


def _run(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {detail[-3000:]}")
    return result


def _run_bytes(command: list[str], timeout: int = 180) -> bytes:
    result = subprocess.run(command, check=False, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {detail[-3000:]}")
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp_ist() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()


def _download(url: str, destination: Path, expected_sha256: str | None = None) -> str:
    if destination.exists():
        actual = _sha256(destination)
        if expected_sha256 and actual != expected_sha256:
            raise RuntimeError(f"Existing downloaded asset has an unexpected SHA-256: {destination}")
        return actual
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-Stage-3.21R/1.0"})
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as target:
            temporary = Path(target.name)
            with urllib.request.urlopen(request, timeout=90) as response:
                shutil.copyfileobj(response, target)
            target.flush()
            os.fsync(target.fileno())
        actual = _sha256(temporary)
        if expected_sha256 and actual != expected_sha256:
            raise RuntimeError(f"SHA-256 mismatch downloading {url}: {actual}")
        os.replace(temporary, destination)
        return actual
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _probe(path: Path) -> dict:
    result = _run(
        [
            str(FFPROBE), "-v", "error", "-count_frames",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels,channel_layout:format=duration,size",
            "-of", "json", str(path),
        ]
    )
    return json.loads(result.stdout)


def _image_dimensions(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return image.size


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            json.dump(data, target, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _download_assets(workspace: Path) -> list[dict]:
    records = []
    asset_dir = workspace / "assets"
    for asset in ASSETS:
        path = asset_dir / asset["filename"]
        sha256 = _download(asset["url"], path)
        width, height = _image_dimensions(path)
        records.append({
            **asset,
            "downloaded_at_ist": _timestamp_ist(),
            "workspace_path": str(path),
            "sha256": sha256,
            "file_size_bytes": path.stat().st_size,
            "original_dimensions": {"width": width, "height": height},
        })
    _write_json(workspace / "asset_manifest.json", {"assets": records})
    return records


def _write_audio(path: Path, samples, sample_rate: int) -> None:
    import numpy as np

    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    pcm = np.clip(values, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2", copy=False)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())


def _write_score(path: Path, duration: float, sample_rate: int = 48000) -> None:
    import numpy as np

    times = np.arange(round(duration * sample_rate), dtype=np.float32) / sample_rate
    fade_in = np.clip(times / 1.2, 0, 1)
    fade_out = np.clip((duration - times) / 2.0, 0, 1)
    envelope = fade_in * fade_out * (.68 + .16 * np.sin(2 * np.pi * .035 * times))
    score = (
        .018 * np.sin(2 * np.pi * 55.0 * times)
        + .010 * np.sin(2 * np.pi * 82.4 * times + .18 * np.sin(2 * np.pi * .08 * times))
        + .006 * np.sin(2 * np.pi * 110.0 * times)
        + .003 * np.sin(2 * np.pi * 164.8 * times + .3)
    ) * envelope
    score = np.clip(score, -.08, .08)
    stereo = np.column_stack((score, np.roll(score, sample_rate // 25))) * 32767
    stereo = np.clip(stereo, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(stereo.tobytes())


def _make_cues(speech_duration: float) -> list[dict]:
    weights = [max(1, len(sentence.split())) for sentence in NARRATION_SENTENCES]
    total_weight = sum(weights)
    cursor = 0.0
    cues = []
    for index, (sentence, weight) in enumerate(zip(NARRATION_SENTENCES, weights), start=1):
        end = cursor + speech_duration * weight / total_weight
        cues.append({
            "index": index,
            "text": sentence,
            "start_sec": round(cursor, 3),
            "end_sec": round(end, 3),
        })
        cursor = end
    return cues


def prepare(workspace: Path) -> dict:
    productions = (ROOT / "media" / "productions").resolve()
    workspace = workspace.resolve()
    if productions not in workspace.parents or not workspace.name.startswith("val_3_21r_black_hole_"):
        raise ValueError(f"Refusing to operate outside a Stage 3.21R workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    for directory in ("audio", "film", "qc", "reports"):
        (workspace / directory).mkdir(exist_ok=True)

    asset_records = _download_assets(workspace)
    model_dir = workspace / "audio" / "models"
    model_paths = {}
    for filename, model in MODEL_FILES.items():
        path = model_dir / filename
        _download(model["url"], path, model["sha256"])
        model_paths[filename] = str(path)

    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    ort.set_default_logger_severity(3)
    engine = Kokoro(model_paths["kokoro-v1.0.fp16.onnx"], model_paths["voices-v1.0.bin"])
    samples, sample_rate = engine.create(NARRATION_TEXT, voice="af_bella", speed=1.1, lang="en-us")
    speech_duration = len(samples) / sample_rate
    duration = max(50.0, speech_duration + 2.8)
    if duration > 60:
        raise RuntimeError(f"Narration would exceed the 60-second target: {duration:.2f}s")

    audio_dir = workspace / "audio"
    narration_path = audio_dir / "narration.wav"
    _write_audio(narration_path, samples, sample_rate)
    pad_seconds = duration - speech_duration
    if pad_seconds > 0:
        with wave.open(str(narration_path), "rb") as source:
            params = source.getparams()
            audio = source.readframes(source.getnframes())
        with wave.open(str(narration_path), "wb") as target:
            target.setparams(params)
            target.writeframes(audio)
            target.writeframes(bytes(round(pad_seconds * sample_rate) * params.nchannels * params.sampwidth))
    _write_score(audio_dir / "score.wav", duration)
    (audio_dir / "narration.txt").write_text(NARRATION_TEXT + "\n", encoding="utf-8")
    (audio_dir / "preview.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Stage 3.21R narration preview</title>"
        "<h1>Stage 3.21R narration preview</h1><p>Listen to the complete local narration before rendering.</p>"
        "<audio controls preload='metadata' src='narration.wav'></audio>"
        "<pre style='max-width:72ch;white-space:pre-wrap;font:18px/1.6 sans-serif'>"
        + NARRATION_TEXT + "</pre>",
        encoding="utf-8",
    )

    for file in ("index.html", "main.js", "film.js"):
        shutil.copyfile(FILM_SOURCE / file, workspace / "film" / file)
    film_assets = workspace / "film" / "assets"
    film_assets.mkdir(exist_ok=True)
    for source_name, target_name in (
        ("m87_eht.jpg", "m87_eht.jpg"),
        ("m87_polarized.jpg", "m87_polarized.jpg"),
        ("m87_spitzer.jpg", "m87_spitzer.jpg"),
    ):
        shutil.copyfile(workspace / "assets" / source_name, film_assets / target_name)

    timing = {
        "duration_sec": round(duration, 3),
        "speech_duration_sec": round(speech_duration, 3),
        "fps": FPS,
        "size": {"width": SIZE[0], "height": SIZE[1]},
        "cues": _make_cues(speech_duration),
        "visual_beats": 20,
        "cue_alignment": "Estimated proportionally by sentence word count; not forced-aligned.",
    }
    _write_json(workspace / "film" / "timing.json", timing)
    result = {
        "stage": "3.21R",
        "run_id": workspace.name,
        "created_at_ist": _timestamp_ist(),
        "asset_count": len(asset_records),
        "assets": asset_records,
        "narration": {
            "provider": "Kokoro ONNX local inference",
            "provider_license": "MIT",
            "model": "Kokoro-82M v1.0 fp16",
            "model_license": "Apache-2.0",
            "voice": "af_bella",
            "language": "en-us",
            "speed": 1.1,
            "sample_rate_hz": sample_rate,
            "channels": 1,
            "duration_sec": round(speech_duration, 3),
            "text": NARRATION_TEXT,
            "audio_path": str(narration_path),
            "preview_path": str(audio_dir / "preview.html"),
            "model_files": model_paths,
        },
        "timing": timing,
        "status": "awaiting operator narration audition",
        "safety": {"scheduler_started": False, "publishing": "manual_approval", "database_mutated": False},
    }
    _write_json(workspace / "preparation_manifest.json", result)
    return result


def _decode_frames(path: Path, duration: float, sample_count: int = 12) -> list[bytes]:
    interval = max(2, round(duration / sample_count))
    result = _run_bytes(
        [
            str(FFMPEG), "-v", "error", "-i", str(path),
            "-vf", f"fps=1/{interval},scale=32:32:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        timeout=120,
    )
    frame_size = 32 * 32
    return [result[offset:offset + frame_size] for offset in range(0, len(result), frame_size) if len(result[offset:offset + frame_size]) == frame_size]


def _average_hash(frame: bytes) -> int:
    pixels = [
        sum(frame[(y * 4 + dy) * 32 + x * 4 + dx] for dy in range(4) for dx in range(4)) / 16
        for y in range(8)
        for x in range(8)
    ]
    mean = sum(pixels) / len(pixels)
    value = 0
    for index, pixel in enumerate(pixels):
        if pixel >= mean:
            value |= 1 << index
    return value


def _frame_comparison(candidate: list[bytes], previous: list[bytes]) -> dict:
    if not candidate or not previous:
        return {"comparable_frames": 0, "mean_hash_distance": None, "mean_pixel_difference": None, "near_match_fraction": 0.0}
    count = min(len(candidate), len(previous))
    distances = []
    differences = []
    for index in range(count):
        left_index = round(index * (len(candidate) - 1) / max(1, count - 1))
        right_index = round(index * (len(previous) - 1) / max(1, count - 1))
        left, right = candidate[left_index], previous[right_index]
        distances.append((_average_hash(left) ^ _average_hash(right)).bit_count())
        differences.append(sum(abs(a - b) for a, b in zip(left, right)) / (len(left) * 255))
    return {
        "comparable_frames": count,
        "mean_hash_distance": round(sum(distances) / count, 3),
        "mean_pixel_difference": round(sum(differences) / count, 5),
        "near_match_fraction": round(sum(distance <= 3 for distance in distances) / count, 3),
    }


def _audio_fingerprint(path: Path) -> list[list[float]]:
    import numpy as np

    result = _run_bytes(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
        timeout=120,
    )
    samples = np.frombuffer(result, dtype="<i2").astype(np.float32)
    window_size = 2048
    if len(samples) < window_size:
        return []
    window = np.hanning(window_size).astype(np.float32)
    edges = np.unique(np.geomspace(1, window_size // 2, 17).astype(int))
    signatures = []
    for offset in range(0, len(samples) - window_size + 1, window_size):
        spectrum = np.abs(np.fft.rfft(samples[offset:offset + window_size] * window))
        bands = [float(np.log1p(np.mean(spectrum[lo:hi]))) for lo, hi in zip(edges[:-1], edges[1:]) if hi > lo]
        scale = max(bands) if bands else 1.0
        signatures.append([value / scale for value in bands] if scale else [0.0 for _ in bands])
    return signatures


def _audio_similarity(left: list[list[float]], right: list[list[float]]) -> float | None:
    if not left or not right:
        return None
    import numpy as np

    count = min(len(left), len(right), 200)
    scores = []
    for index in range(count):
        li = round(index * (len(left) - 1) / max(1, count - 1))
        ri = round(index * (len(right) - 1) / max(1, count - 1))
        a = np.asarray(left[li], dtype=np.float32)
        b = np.asarray(right[ri], dtype=np.float32)
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        scores.append(float(np.dot(a, b) / denominator) if denominator else 0.0)
    return round(sum(scores) / len(scores), 5)


def _candidate_videos(root: Path, current_workspace: Path) -> list[Path]:
    base = root / "media"
    candidates = []
    for path in base.rglob("*.mp4"):
        if current_workspace in path.parents or path == current_workspace:
            continue
        if any(part in {"assets", "render_work", "qc", "staging"} for part in path.parts):
            continue
        if path.parent.name == "video" or path.name.startswith("black_hole_lemo_opuscar"):
            candidates.append(path)
    return sorted(set(candidates))


def _compare_previous(candidate: Path, workspace: Path, duration: float) -> list[dict]:
    candidate_hash = _sha256(candidate)
    candidate_frames = _decode_frames(candidate, duration)
    candidate_audio = _audio_fingerprint(candidate)
    comparisons = []
    for previous in _candidate_videos(ROOT, workspace):
        previous_hash = _sha256(previous)
        metadata = _probe(previous)
        video_stream = next((stream for stream in metadata["streams"] if stream.get("codec_type") == "video"), None)
        previous_duration = float(metadata.get("format", {}).get("duration") or 0)
        frame_result = _frame_comparison(candidate_frames, _decode_frames(previous, previous_duration))
        previous_audio = _audio_fingerprint(previous) if any(s.get("codec_type") == "audio" for s in metadata["streams"]) else []
        audio_similarity = _audio_similarity(candidate_audio, previous_audio)
        duplicate = (
            candidate_hash == previous_hash
            or (
                abs(duration - previous_duration) <= 2.0
                and frame_result["mean_hash_distance"] is not None
                and frame_result["mean_hash_distance"] <= 3.0
                and frame_result["mean_pixel_difference"] <= .06
                and frame_result["near_match_fraction"] >= .8
            )
        )
        comparisons.append({
            "path": str(previous),
            "sha256": previous_hash,
            "duration_sec": previous_duration,
            "frame_size": [video_stream.get("width"), video_stream.get("height")] if video_stream else None,
            "frame_comparison": frame_result,
            "audio_similarity": audio_similarity,
            "duplicate_rejected": duplicate,
        })
    if any(item["duplicate_rejected"] for item in comparisons):
        raise RuntimeError("Candidate rejected: it is identical or substantially similar to an existing video.")
    return comparisons


def _measure_loudness(path: Path) -> dict:
    result = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path),
            "-af", "loudnorm=I=-15:TP=-1:LRA=9:print_format=json", "-f", "null", "-",
        ],
        check=False, capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Loudness analysis failed ({result.returncode}): {detail[-3000:]}")
    match = re.search(r"\{\s*\"input_i\".*?\}", result.stderr, re.DOTALL)
    if not match:
        raise RuntimeError(f"FFmpeg did not return loudness metadata for {path}")
    return json.loads(match.group(0))


def render(workspace: Path, audio_reviewed: bool) -> dict:
    if not audio_reviewed:
        raise ValueError("Final rendering is gated on an operator listening to and approving the complete narration.")
    workspace = workspace.resolve()
    timing_path = workspace / "film" / "timing.json"
    if not timing_path.is_file():
        raise FileNotFoundError(f"Run --phase prepare first: {timing_path}")
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    audio_dir = workspace / "audio"
    narration = audio_dir / "narration.wav"
    mastered = audio_dir / "narration_master.wav"
    score = audio_dir / "score.wav"
    visual = workspace / "black_hole_stage_3_21r_visual_only.mp4"
    final = workspace / "black_hole_stage_3_21r_candidate.mp4"
    thumbnail = workspace / "thumbnail.jpg"

    _run(
        [
            str(FFMPEG), "-y", "-i", str(narration),
            "-af", "highpass=f=70,acompressor=threshold=0.1:ratio=2:attack=18:release=220,loudnorm=I=-16:TP=-2:LRA=8",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(mastered),
        ],
        timeout=180,
    )
    adapter = LemoOpuscarAdapter()
    adapter.render_video(workspace, workspace / "film", visual, fps=FPS, size=SIZE)
    _run(
        [
            str(FFMPEG), "-y", "-i", str(visual), "-i", str(mastered), "-i", str(score),
            "-filter_complex", "[2:a]volume=0.18[bed];[1:a][bed]amix=inputs=2:duration=longest:dropout_transition=2,loudnorm=I=-15:TP=-1:LRA=9[a]",
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-t", str(timing["duration_sec"]), "-movflags", "+faststart", str(final),
        ],
        timeout=900,
    )
    adapter.render_still(
        workspace, workspace / "film", thumbnail,
        time_sec=timing["duration_sec"] * .5, query="thumbnail=1", size=SIZE,
    )

    metadata = _probe(final)
    videos = [stream for stream in metadata["streams"] if stream.get("codec_type") == "video"]
    audios = [stream for stream in metadata["streams"] if stream.get("codec_type") == "audio"]
    duration = float(metadata["format"]["duration"])
    if (
        len(videos) != 1
        or len(audios) != 1
        or videos[0].get("codec_name") != "h264"
        or videos[0].get("width") != SIZE[0]
        or videos[0].get("height") != SIZE[1]
        or videos[0].get("avg_frame_rate") != "30/1"
        or not 50 <= duration <= 60
    ):
        raise RuntimeError(f"Video failed required metadata checks: {metadata}")
    thumb_dimensions = _image_dimensions(thumbnail)
    if thumb_dimensions != SIZE:
        raise RuntimeError(f"Thumbnail has the wrong dimensions: {thumb_dimensions}")
    _run([str(FFMPEG), "-v", "error", "-i", str(final), "-f", "null", "-"], timeout=900)

    qc_dir = workspace / "qc"
    qc_dir.mkdir(exist_ok=True)
    frame_numbers = [round((i + .5) * duration / 20 * FPS) for i in range(20)]
    select_expr = "+".join(f"eq(n\\,{frame})" for frame in frame_numbers)
    contact_sheet = qc_dir / "contact_sheet.png"
    _run(
        [
            str(FFMPEG), "-y", "-i", str(final), "-vf",
            f"select={select_expr},scale=216:384,tile=5x4", "-frames:v", "1", str(contact_sheet),
        ],
        timeout=300,
    )
    if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
        raise RuntimeError("Actual-frame contact sheet generation failed.")
    comparisons = _compare_previous(final, workspace, duration)
    narration_loudness = _measure_loudness(mastered)
    final_loudness = _measure_loudness(final)

    run_id = workspace.name
    production_workspace = ProductionWorkspace(run_id, "black-hole", "Beyond the Horizon", base_dir=str(workspace))
    delivery = production_workspace.deliver_approved_sample(str(final), str(thumbnail))
    manifest = {
        "stage": "3.21R",
        "run_id": run_id,
        "created_at_ist": _timestamp_ist(),
        "upstream_renderer": {
            "repository": "https://github.com/lemomo-ai/lemo-opuscar",
            "commit": PINNED_COMMIT,
            "license": "MIT",
            "rendered_with_pinned_upstream_video_mjs": True,
            "renderer_mode": "local Chromium capture and FFmpeg encoding",
        },
        "outputs": {
            "workspace_video": {
                "path": str(final),
                "sha256": _sha256(final),
                "file_size_bytes": final.stat().st_size,
                "ffprobe": metadata,
            },
            "workspace_thumbnail": {
                "path": str(thumbnail),
                "sha256": _sha256(thumbnail),
                "file_size_bytes": thumbnail.stat().st_size,
                "dimensions": {"width": thumb_dimensions[0], "height": thumb_dimensions[1]},
            },
            "canonical_delivery": delivery,
            "contact_sheet": str(contact_sheet),
        },
        "duplicate_comparison": comparisons,
        "audio": {
            "provider": "Kokoro ONNX local inference",
            "voice": "af_bella",
            "narration_wav": str(narration),
            "mastered_narration_wav": str(mastered),
            "final_stream": audios[0],
            "narration_loudness": narration_loudness,
            "final_mix_loudness": final_loudness,
            "mix": "48 kHz stereo; 2:1 gentle compression; speech loudness normalization; procedural ambient score at -14.9 dB gain before final loudness normalization.",
            "sound_effects": "None; no unlicensed effects used.",
        },
        "qc": {"full_decode": "passed", "video_metadata": "passed", "thumbnail_dimensions": "passed"},
        "safety": {
            "scheduler_started": False,
            "publishing": "manual_approval",
            "database_mutated": False,
            "youtube_uploads": False,
        },
    }
    _write_json(workspace / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "render"), required=True)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--audio-reviewed", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare":
        print(json.dumps(prepare(args.workspace), indent=2))
    else:
        print(json.dumps(render(args.workspace, args.audio_reviewed), indent=2))


if __name__ == "__main__":
    main()
