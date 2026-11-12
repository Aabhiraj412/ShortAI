"""Render and verify the isolated Stage 3.21Q Lemo-Opuscar sample."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.rendering.lemo_opuscar_adapter import (  # noqa: E402
    LemoOpuscarAdapter,
    PINNED_COMMIT,
)

WORKSPACE = ROOT / "media" / "productions" / "val_3_21q_black_hole_20261111_2138"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
FPS = 30
SIZE = (1080, 1920)
VOICE_LINES = (
    "Watch this line. In empty space, it travels straight.",
    "Near a black hole, gravity curves spacetime, and the paths light can take bend.",
    "A black hole packs matter into a tiny region. Its event horizon is a boundary, not a solid surface.",
    "Past that boundary, even light has no path back out.",
    "Hot gas can orbit outside the horizon in an accretion disk, glowing as it falls inward. That light comes from the gas, not the black hole.",
    "And a black hole is no cosmic vacuum cleaner. Far away, its gravity acts like that of any object with the same mass.",
)


def _run(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {detail[-3000:]}")
    return result


def _wave_frames(path: Path) -> tuple[wave._wave_params, bytes]:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if params.comptype != "NONE" or params.sampwidth != 2:
            raise RuntimeError(f"Unexpected local speech WAV format in {path}: {params}")
        return params, source.readframes(params.nframes)


def _synthesize_line(text: str, output: Path) -> None:
    safe_text = text.replace("'", "''")
    safe_output = str(output).replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.Rate=-1; "
        f"$voice.SetOutputToWaveFile('{safe_output}'); "
        f"$voice.Speak('{safe_text}'); $voice.Dispose()"
    )
    _run(["powershell", "-NoProfile", "-Command", command], timeout=120)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Local speech synthesis produced no audio for {output.name}")


def _write_score(path: Path, duration: float, cues: list[dict[str, Any]]) -> None:
    rate = 24000
    total = round(duration * rate)
    samples = array.array("h")
    beat = 60 / 100
    cue_times = [item["start_sec"] for item in cues]
    for i in range(total):
        t = i / rate
        envelope = min(1.0, t / .7, max(0.0, (duration - t) / 1.2))
        value = .022 * math.sin(2 * math.pi * 55 * t)
        value += .009 * math.sin(2 * math.pi * 110 * t)
        beat_phase = t % beat
        if beat_phase < .055:
            value += .025 * math.exp(-beat_phase * 74) * math.sin(2 * math.pi * 880 * beat_phase)
        for cue_time in cue_times:
            elapsed = t - cue_time
            if 0 <= elapsed < .16:
                value += .018 * math.exp(-elapsed * 31) * math.sin(2 * math.pi * 640 * elapsed)
        value *= envelope
        samples.append(max(-32768, min(32767, int(value * 32767))))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())


def prepare_audio(workspace: Path) -> dict[str, Any]:
    audio_dir = workspace / "audio"
    audio_dir.mkdir(exist_ok=False)
    cues: list[dict[str, Any]] = []
    clips: list[tuple[wave._wave_params, bytes]] = []
    cursor = .45
    for index, text in enumerate(VOICE_LINES, start=1):
        clip_path = audio_dir / f"narration_{index:02d}.wav"
        _synthesize_line(text, clip_path)
        params, frames = _wave_frames(clip_path)
        wave_format = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
        first_format = (
            (clips[0][0].nchannels, clips[0][0].sampwidth, clips[0][0].framerate,
             clips[0][0].comptype, clips[0][0].compname)
            if clips
            else None
        )
        if clips and wave_format != first_format:
            raise RuntimeError("Windows speech clips have inconsistent WAV formats")
        clip_duration = len(frames) / (params.framerate * params.nchannels * params.sampwidth)
        cues.append(
            {
                "index": index,
                "text": text,
                "start_sec": round(cursor, 3),
                "end_sec": round(cursor + clip_duration, 3),
            }
        )
        clips.append((params, frames))
        cursor += clip_duration + (.34 if index < len(VOICE_LINES) else 0)

    narration_duration = cursor
    total_duration = max(50.0, narration_duration + 4.8)
    if total_duration > 60:
        raise RuntimeError(
            f"Local narration would make the sample {total_duration:.2f}s; revise the script or speech pace."
        )
    params = clips[0][0]
    silence_frame_count = round(.34 * params.framerate)
    silence = bytes(silence_frame_count * params.nchannels * params.sampwidth)
    narration_path = audio_dir / "narration.wav"
    with wave.open(str(narration_path), "wb") as target:
        target.setparams(params)
        for index, (_, frames) in enumerate(clips):
            target.writeframes(frames)
            if index < len(clips) - 1:
                target.writeframes(silence)
        pad_frames = round((total_duration - cursor) * params.framerate)
        target.writeframes(bytes(max(0, pad_frames) * params.nchannels * params.sampwidth))

    timing = {
        "duration_sec": round(total_duration, 3),
        "narration_duration_sec": round(narration_duration, 3),
        "fps": FPS,
        "size": {"width": SIZE[0], "height": SIZE[1]},
        "cues": cues,
    }
    timing_path = workspace / "film" / "timing.json"
    if timing_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing timing data: {timing_path}")
    timing_path.write_text(json.dumps(timing, indent=2), encoding="utf-8")
    _write_score(audio_dir / "score.wav", total_duration, cues)
    (audio_dir / "narration.txt").write_text("\n".join(VOICE_LINES) + "\n", encoding="utf-8")
    return timing


def _load_timing(workspace: Path) -> dict[str, Any]:
    path = workspace / "film" / "timing.json"
    if not path.is_file():
        raise FileNotFoundError(f"Run the prepare phase before rendering: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def render_style_frames(workspace: Path, timing: dict[str, Any]) -> list[Path]:
    adapter = LemoOpuscarAdapter()
    cues = timing["cues"]
    moments = (
        ("style_frame_01_spacetime.jpg", cues[1]["start_sec"] + 1.6),
        ("style_frame_02_horizon.jpg", cues[3]["start_sec"] + 1.6),
        ("style_frame_03_accretion.jpg", cues[4]["start_sec"] + 2.2),
    )
    outputs = []
    for filename, second in moments:
        output = workspace / "qc" / filename
        adapter.render_still(workspace, workspace / "film", output, time_sec=second, size=SIZE)
        outputs.append(output)
    return outputs


def _probe(path: Path) -> dict[str, Any]:
    result = _run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration,size",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_sample(workspace: Path, timing: dict[str, Any]) -> dict[str, Any]:
    adapter = LemoOpuscarAdapter()
    visual = workspace / "black_hole_lemo_opuscar_visual_only.mp4"
    final = workspace / "black_hole_lemo_opuscar.mp4"
    thumbnail = workspace / "thumbnail.jpg"
    adapter.render_video(workspace, workspace / "film", visual, fps=FPS, size=SIZE)
    _run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(visual),
            "-i",
            str(workspace / "audio" / "narration.wav"),
            "-i",
            str(workspace / "audio" / "score.wav"),
            "-filter_complex",
            "[2:a]volume=0.14[bed];[1:a][bed]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.2:LRA=11[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final),
        ],
        timeout=600,
    )
    adapter.render_still(
        workspace,
        workspace / "film",
        thumbnail,
        time_sec=timing["cues"][2]["start_sec"] + 2,
        query="thumbnail=1",
        size=SIZE,
    )

    metadata = _probe(final)
    video_streams = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in metadata["streams"] if stream["codec_type"] == "audio"]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise RuntimeError(f"Expected one video and one audio stream, found {metadata['streams']}")
    video = video_streams[0]
    duration = float(metadata["format"]["duration"])
    if (
        video.get("codec_name") != "h264"
        or video.get("width") != SIZE[0]
        or video.get("height") != SIZE[1]
        or video.get("avg_frame_rate") != "30/1"
        or not 50 <= duration <= 60
    ):
        raise RuntimeError(f"Output failed required video metadata checks: {metadata}")
    if not thumbnail.is_file() or thumbnail.stat().st_size == 0:
        raise RuntimeError("The separate Lemo-Opuscar thumbnail was not produced")
    thumb_metadata = _probe(thumbnail)
    thumb_video = thumb_metadata["streams"][0]
    if thumb_video.get("width") != SIZE[0] or thumb_video.get("height") != SIZE[1]:
        raise RuntimeError(f"Thumbnail has the wrong dimensions: {thumb_metadata}")

    _run([str(FFMPEG), "-v", "error", "-i", str(final), "-f", "null", "-"], timeout=600)
    frame_numbers = [round(second * FPS) for second in (1, 7, 15, 23, 31, 39, 47, min(55, duration - 1))]
    selected = "+".join(f"eq(n\\,{frame})" for frame in frame_numbers)
    contact_sheet = workspace / "qc" / "contact_sheet.png"
    _run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(final),
            "-vf",
            f"select={selected},scale=270:480,tile=4x2",
            "-frames:v",
            "1",
            str(contact_sheet),
        ],
        timeout=600,
    )
    if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not produce the actual-frame contact sheet")

    manifest = {
        "stage": "3.21Q",
        "run_id": workspace.name,
        "upstream": {
            "repository": "https://github.com/lemomo-ai/lemo-opuscar",
            "commit": PINNED_COMMIT,
            "license": "MIT",
            "style": "Blueprint",
        },
        "render_invocation": {
            "executable": str(adapter.node_executable),
            "script": str(adapter.library_root / "core" / "render" / "video.mjs"),
            "arguments": [
                str(workspace / "film"),
                "--fps",
                str(FPS),
                "--workers",
                "1",
                "--size",
                f"{SIZE[0]}x{SIZE[1]}",
                "--out",
                str(visual),
            ],
        },
        "outputs": {
            "video": str(final),
            "video_sha256": _sha256(final),
            "visual_only_video": str(visual),
            "thumbnail": str(thumbnail),
            "thumbnail_sha256": _sha256(thumbnail),
            "contact_sheet": str(contact_sheet),
        },
        "video_probe": metadata,
        "thumbnail_probe": thumb_metadata,
        "full_decode": "passed",
        "duration_sec": duration,
        "safety": {
            "scheduler_started": False,
            "publishing": "manual_approval",
            "external_asset_downloads": False,
            "production_ledger_mutated": False,
        },
    }
    manifest_path = workspace / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite run manifest: {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "styleframes", "render"), required=True)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    productions = (ROOT / "media" / "productions").resolve()
    if productions not in workspace.parents or not workspace.name.startswith("val_3_21q_black_hole_"):
        raise ValueError(f"Refusing to operate outside a Stage 3.21Q validation workspace: {workspace}")
    if args.phase == "prepare":
        print(json.dumps(prepare_audio(workspace), indent=2))
    elif args.phase == "styleframes":
        print(json.dumps([str(path) for path in render_style_frames(workspace, _load_timing(workspace))], indent=2))
    else:
        print(json.dumps(render_sample(workspace, _load_timing(workspace)), indent=2))


if __name__ == "__main__":
    main()
