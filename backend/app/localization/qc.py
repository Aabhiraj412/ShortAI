"""Stage 4.02: Hindi-specific quality control of a localized Short.

Two kinds of failure are kept apart:
* ``creative`` (the content is not good enough; a retry of the same inputs does not fix it): script rule problems,
  duration outside the Shorts window, on-screen text that is not Devanagari, clipped / overlapping text.
* ``technical`` (infrastructure / rendering; a retry may fix it): encoding, resolution, A/V sync, decode errors,
  missing or silent audio, subtitles that libass could not render.

A video is publishable only when both lists are empty; even then publishing is a separate, authorized milestone.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from backend.app.localization.script_qc import devanagari_share

MIN_SEC, MAX_SEC = 30.0, 58.0
MAX_AV_DRIFT = 0.30


def probe(ffprobe: str, mp4: str) -> Dict[str, Any]:
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,duration,sample_rate,channels",
                        "-of", "json", mp4], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(r.stderr or '')[-200:]}")
    return json.loads(r.stdout or "{}")


def decode_errors(ffmpeg: str, mp4: str) -> str:
    r = subprocess.run([ffmpeg, "-v", "error", "-i", mp4, "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    return (r.stderr or "").strip()[-300:]


def mean_volume(ffmpeg: str, mp4: str) -> float:
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", mp4, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else -99.0


def evaluate(*, mp4: str, plan: Dict[str, Any], ffmpeg: str, ffprobe: str, script_problems: Sequence[str] = (),
             subtitle_check: Optional[Dict[str, Any]] = None, typography_issues: Sequence[Dict[str, Any]] = (),
             voice_total_sec: Optional[float] = None,
             probe_fn=probe, decode_fn=decode_errors, volume_fn=mean_volume) -> Dict[str, Any]:
    creative: List[str] = [f"script: {p}" for p in script_problems]
    technical: List[str] = []
    info = probe_fn(ffprobe, mp4)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float((info.get("format") or {}).get("duration") or 0.0)
    if v is None:
        technical.append("no video stream")
    else:
        if v.get("codec_name") != "h264":
            technical.append(f"video codec {v.get('codec_name')} (expected h264)")
        if (v.get("width"), v.get("height")) != (1080, 1920):
            technical.append(f"resolution {v.get('width')}x{v.get('height')} (expected 1080x1920)")
        if v.get("r_frame_rate") not in ("30/1", "30000/1000"):
            technical.append(f"frame rate {v.get('r_frame_rate')} (expected 30)")
    if a is None:
        technical.append("no audio stream")
    else:
        if a.get("codec_name") != "aac":
            technical.append(f"audio codec {a.get('codec_name')} (expected aac)")
        if v is not None and v.get("duration") and a.get("duration"):
            drift = abs(float(v["duration"]) - float(a["duration"]))
            if drift > MAX_AV_DRIFT:
                technical.append(f"audio/video drift {drift:.2f}s (max {MAX_AV_DRIFT}s)")
        vol = volume_fn(ffmpeg, mp4)
        if vol < -40.0:
            technical.append(f"audio is near-silent (mean {vol:.1f} dB): the Hindi voice is missing")
    if not MIN_SEC <= dur <= MAX_SEC:
        creative.append(f"duration {dur:.1f}s is outside the Shorts window {MIN_SEC:.0f}-{MAX_SEC:.0f}s")
    if voice_total_sec and abs(float(plan.get("duration_sec") or 0) - voice_total_sec) > 2.0:
        technical.append(f"film length {plan.get('duration_sec')}s does not match the Hindi voice {voice_total_sec}s")
    err = decode_fn(ffmpeg, mp4)
    if err:
        technical.append(f"decode errors: {err[:160]}")
    latin_heads = [s["scene_id"] for s in plan.get("scenes") or []
                   if s.get("headline") and devanagari_share(s["headline"]) < 0.5]
    latin_heads += [f"{s.get('scene_id')} stage label" for s in plan.get("scenes") or []
                    if s.get("stage_label") and devanagari_share(s["stage_label"]) < 0.5]
    latin_heads += [f"{s.get('scene_id')} label" for s in plan.get("scenes") or []
                    for lab in (s.get("labels") or []) if lab and devanagari_share(str(lab)) < 0.5]
    if latin_heads:
        creative.append(f"on-screen headline not in Devanagari: {', '.join(latin_heads)}")
    for issue in typography_issues:
        creative.append(f"on-screen text at {issue.get('t')}s: {issue.get('message')}")
    if subtitle_check is not None and not subtitle_check.get("ok"):
        technical.append(f"Hindi subtitles did not render cleanly: {subtitle_check}")
    passed = not creative and not technical
    return {"passed": passed,
            "classification": "PASSED" if passed else ("TECHNICAL_FAILURE" if technical else "CREATIVE_REJECTION"),
            "creative": creative, "technical": technical,
            "measured": {"duration_sec": round(dur, 2), "video": v and {k: v.get(k) for k in ("codec_name", "width", "height", "r_frame_rate")},
                         "audio": a and {k: a.get(k) for k in ("codec_name", "sample_rate", "channels")}}}
