"""Stage 4.02: Hindi voice generation (zero cost), per scene, with pronunciation corrections and validation.

Audit (2026-11-27, this laptop):
* Microsoft neural Hindi voices through ``edge-tts`` (``hi-IN-SwaraNeural``, ``hi-IN-MadhurNeural``): free, no key,
  ~2 s per sentence, negligible CPU/RAM; it is the same service the English pipeline already depends on, so it is
  not a new dependency. It needs the network.
* Offline: no Hindi Windows SAPI voice is installed (only English David/Zira/Heera/Ravi/Mark), and no offline neural
  TTS (Piper/Coqui) is installed. Both would need an install or download (operator decision).

Provider order: Swara -> Madhur (edge-tts) -> any installed Windows SAPI hi-IN voice. If none can speak, the
localization waits (VoiceUnavailable is an infrastructure condition, retried later; never a paid fallback).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_VOICES = ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural")
RATES = ("+8%", "+18%", "+25%")   # faster rates are used only when a Short would be too long
MAX_TOTAL_SEC = 57.0
MIN_CLIP_SEC = 0.35
SILENCE_DB = -45.0
SCENE_GAP_SEC = 0.12


class VoiceUnavailable(RuntimeError):
    """No Hindi voice could speak (network down, provider refusing, no offline voice): retry later."""


class ScriptTooLong(RuntimeError):
    """The voice works, but the script is too long even at the fastest rate (a content problem, never rendered)."""

    def __init__(self, seconds: float, voice: str, rate: str):
        super().__init__(f"spoken Hindi is {seconds:.1f} s with {voice} at {rate}, over the {MAX_TOTAL_SEC:.0f} s limit")
        self.seconds, self.voice, self.rate = seconds, voice, rate


def apply_pronunciation(text: str, terms: Sequence[Dict[str, Any]]) -> str:
    """Replaces display spellings (Hindi or English) with their spoken Devanagari forms."""
    out = text
    for t in sorted((t for t in terms if t.get("spoken")), key=lambda t: -len(str(t.get("hi") or t.get("en") or ""))):
        for form in (t.get("hi"), t.get("en")):
            if form and form != t["spoken"]:
                out = re.sub(rf"(?<!\w){re.escape(str(form))}(?!\w)", str(t["spoken"]), out)
    return out


def _edge_synth(text: str, voice: str, rate: str, out_mp3: Path) -> None:
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out_mp3))
    asyncio.run(go())


def sapi_hindi_voices() -> List[str]:
    if os.name != "nt":
        return []
    ps = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
          ".GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'hi-IN' } | "
          "ForEach-Object { $_.VoiceInfo.Name }")
    try:
        from backend.app.core.process_utils import run_hidden
        out = run_hidden(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
        return [l.strip() for l in (out or "").splitlines() if l.strip()]
    except Exception:
        return []


def _sapi_synth(text: str, voice: str, out_wav: Path) -> None:
    from backend.app.core.process_utils import run_hidden
    safe = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.SetOutputToWaveFile('{out_wav}'); $s.Speak('{safe}'); $s.Dispose()")
    r = run_hidden(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not out_wav.is_file():
        raise RuntimeError("SAPI synthesis failed")


def _probe(ffmpeg: str, path: Path) -> Tuple[float, float]:
    """(duration seconds, mean volume dB) via ffmpeg volumedetect."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=120)
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    return dur, float(v.group(1)) if v else -99.0


def _to_wav(ffmpeg: str, src: Path, dst: Path) -> None:
    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "1",
                        str(dst)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError(f"audio conversion failed: {(r.stderr or '')[-200:]}")


def synthesize(scenes: Sequence[Dict[str, Any]], terms: Sequence[Dict[str, Any]], out_dir: Path, *, ffmpeg: str,
               voices: Sequence[str] = DEFAULT_VOICES, edge: Callable = _edge_synth, sapi_list: Callable = sapi_hindi_voices,
               sapi: Callable = _sapi_synth) -> Dict[str, Any]:
    """One clip per scene, concatenated with SCENE_GAP_SEC between scenes. Returns the timing and the provider."""
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: List[str] = []
    shortest: Optional[Tuple[float, str, str]] = None      # the best over-length measurement (never returned)
    providers: List[Tuple[str, str]] = [("edge-tts", v) for v in voices] + [("sapi", v) for v in (sapi_list() or [])]
    for kind, voice in providers:
        for rate in (RATES if kind == "edge-tts" else ("0",)):
            try:
                clips = []
                for sc in scenes:
                    # the voice form: Hindi words in Devanagari, English words in Latin (never shown on screen)
                    spoken = apply_pronunciation(sc.get("narration_tts") or sc["narration"], terms)
                    base = out_dir / f"{sc['scene_id']}_{voice}_{rate.replace('%', 'p').replace('+', '')}"
                    wav = base.with_suffix(".wav")
                    if not wav.is_file():
                        if kind == "edge-tts":
                            mp3 = base.with_suffix(".mp3")
                            edge(spoken, voice, rate, mp3)
                            _to_wav(ffmpeg, mp3, wav)
                        else:
                            sapi(spoken, voice, wav)
                    dur, vol = _probe(ffmpeg, wav)
                    if dur < MIN_CLIP_SEC or vol < SILENCE_DB:
                        raise RuntimeError(f"{sc['scene_id']}: clip invalid (duration {dur:.2f}s, mean {vol:.1f} dB)")
                    clips.append({"scene_id": sc["scene_id"], "path": str(wav), "duration": round(dur, 3),
                                  "mean_volume_db": vol, "spoken": spoken})
                total = sum(c["duration"] for c in clips) + SCENE_GAP_SEC * (len(clips) - 1)
                if total > MAX_TOTAL_SEC:
                    if rate != RATES[-1] and kind == "edge-tts":
                        errors.append(f"{voice} at {rate}: {total:.1f}s is too long; trying a faster rate")
                        continue
                    # Even the fastest rate is over the limit: never hand an over-length voice to the renderer.
                    errors.append(f"{voice} at {rate}: {total:.1f}s is too long even at the fastest rate")
                    if shortest is None or total < shortest[0]:
                        shortest = (total, voice, rate)
                    break
                voice_wav = out_dir / "voice_hi.wav"
                _concat(ffmpeg, clips, voice_wav)
                start, timings = 0.0, []
                for c in clips:
                    timings.append({"scene_id": c["scene_id"], "start": round(start, 3), "duration": c["duration"]})
                    start += c["duration"] + SCENE_GAP_SEC
                result = {"provider": kind, "voice": voice, "rate": rate, "total_sec": round(total, 3),
                          "voice_path": str(voice_wav), "timings": timings, "clips": clips, "errors": errors}
                (out_dir / "voice_hi.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
                return result
            except Exception as exc:              # next rate/voice/provider
                errors.append(f"{kind}:{voice}@{rate}: {type(exc).__name__}: {str(exc)[:160]}")
                break
    if shortest is not None:
        raise ScriptTooLong(*shortest)
    raise VoiceUnavailable("no Hindi voice could speak: " + " | ".join(errors[-4:]))


def _concat(ffmpeg: str, clips: Sequence[Dict[str, Any]], out: Path) -> None:
    gap = out.with_name("gap.wav")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"anullsrc=r=48000:cl=mono", "-t", f"{SCENE_GAP_SEC}", str(gap)], capture_output=True, timeout=60)
    lst = out.with_name("concat.txt")
    lines = []
    for i, c in enumerate(clips):
        if i:
            lines.append(f"file '{gap.as_posix()}'")
        lines.append(f"file '{Path(c['path']).as_posix()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-ar", "48000", "-ac", "1", str(out)], capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError(f"voice concatenation failed: {(r.stderr or '')[-200:]}")
