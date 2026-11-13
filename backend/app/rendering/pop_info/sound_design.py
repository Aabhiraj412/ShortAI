"""
Pop Info sound design: every sound is synthesized here (no sample files, so no licensing risk),
following the reference's rules: soft, toy-like and sparse; rounded pops, airy whooshes, wooden
taps and counter ticks; no chimes or bells; a quiet pad that ducks under the narration.

mix_audio() renders the SFX and music stems from the scene plan and mixes them under the
narration with ffmpeg: sidechain ducking keyed by the voice, then EBU R128 loudness
normalisation to -14 LUFS / -1.5 dBTP (YouTube's reference level).
"""

from __future__ import annotations

import json
import re
import math
import subprocess
import wave
from pathlib import Path
from typing import Optional, Any, Dict, List

import numpy as np

SR = 48000


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / max(decay, 1e-4))


def _pop(gain: float, f: float = 520.0) -> np.ndarray:
    n = int(0.22 * SR); t = np.arange(n) / SR
    freq = f * (0.7 + 0.55 * np.exp(-t * 30))
    return gain * np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.004, 0.05)


def _tap(gain: float, f: float = 190.0) -> np.ndarray:
    n = int(0.14 * SR); t = np.arange(n) / SR
    freq = f * (1.0 + 0.4 * np.exp(-t * 60))
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.001, 0.025)
    click = np.random.default_rng(int(f)).standard_normal(n) * _env(n, 0.0005, 0.004) * 0.3
    return gain * (body + click)


def _whoosh(gain: float, dur: float = 0.55, seed: int = 7) -> np.ndarray:
    n = int(dur * SR); rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    out = np.zeros(n); y = 0.0
    cut = 0.02 + 0.25 * np.sin(np.linspace(0, np.pi, n)) ** 2          # one-pole low-pass sweep up and down
    for i in range(n):
        y += cut[i] * (noise[i] - y); out[i] = y
    shape = np.sin(np.linspace(0, np.pi, n)) ** 1.5
    return gain * 0.9 * out * shape / (np.max(np.abs(out)) + 1e-9)


def _pad(duration: float, gain: float = 0.08) -> np.ndarray:
    n = int(duration * SR); t = np.arange(n) / SR
    chords = [(174.6, 220.0, 261.6), (146.8, 174.6, 220.0), (196.0, 246.9, 293.7), (130.8, 164.8, 196.0)]
    bar = 2.5
    out = np.zeros(n)
    for k, chord in enumerate(chords * int(math.ceil(duration / (bar * len(chords))) + 1)):
        s0 = int(k * bar * SR)
        if s0 >= n:
            break
        s1 = min(n, s0 + int((bar + 0.6) * SR)); m = s1 - s0; tt = np.arange(m) / SR
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (m / SR - tt) / 0.6)
        for f in chord:
            out[s0:s1] += (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * 2 * f * tt)) * env / 3
    fade = np.minimum(1, t / 1.0) * np.minimum(1, (duration - t) / 1.2)
    return gain * out * fade


def render_stems(plan: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    duration = float(plan["duration_sec"]) + 0.2
    n = int(duration * SR)
    sfx = np.zeros(n)
    events: List[Dict[str, Any]] = []
    for sc in plan.get("scenes", []):
        for cue in sc.get("sfx", []):
            kind, g, at = cue["kind"], float(cue.get("gain", 0.5)), float(cue["t"])
            clip = {"pop": lambda: _pop(0.35 * g), "tap": lambda: _tap(0.4 * g), "tick": lambda: _tap(0.22 * g, 420.0),
                    "whoosh": lambda: _whoosh(0.3 * g, seed=int(at * 100))}.get(kind)
            if clip is None:
                continue
            x = clip(); s0 = int(at * SR)
            if s0 >= n:
                continue
            m = min(len(x), n - s0); sfx[s0:s0 + m] += x[:m]
            events.append({"t": at, "kind": kind})
    music = _pad(duration)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, data in (("sfx", sfx), ("music", music)):
        p = out_dir / f"{name}.wav"; _write_wav(p, data); paths[name] = str(p)
    return {"paths": paths, "events": len(events), "synthesized": True,
            "license": "original synthesized audio (CrazzyDocs)", "sfx_peak": float(np.max(np.abs(sfx)) if n else 0.0)}


def _write_wav(path: Path, mono: np.ndarray) -> None:
    data = np.clip(mono, -1.0, 1.0)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def mix_audio(*, ffmpeg: str, voice_path: str, plan: Dict[str, Any], work_dir: Path, out_path: Path) -> Dict[str, Any]:
    stems = render_stems(plan, work_dir)
    dur = float(plan["duration_sec"])
    base = (
        f"[0:a]aresample={SR},apad=whole_dur={dur:.3f},asplit=2[v][key];"
        f"[1:a]volume=1.0[m];[m][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[md];"
        f"[2:a]volume=1.0[s];"
        f"[v][md][s]amix=inputs=3:normalize=0:duration=first,atrim=0:{dur:.3f}"
    )
    inputs = [ffmpeg, "-y", "-i", voice_path, "-i", stems["paths"]["music"], "-i", stems["paths"]["sfx"]]

    def measure(chain: str) -> Dict[str, Optional[float]]:
        r = subprocess.run(inputs + ["-filter_complex", base + chain + ",ebur128=peak=true[out]", "-map", "[out]",
                                     "-f", "null", "-"], capture_output=True, text=True, timeout=300)
        tail = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else ""
        i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
        tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
        return {"i": float(i.group(1)) if i else None, "tp": float(tp.group(1)) if tp else None}

    # Deterministic loudness (replaces loudnorm, whose linear mode silently fell back to dynamic and
    # undershot to -15.7 LUFS): measure, apply the exact gain, then a look-ahead limiter below the
    # true-peak ceiling. One corrective pass if limiting cost loudness. The MP4 is re-measured by the gate.
    target, limit_db = -14.0, -2.0           # -2 dBFS sample ceiling leaves room for inter-sample + AAC peaks
    limiter = f"alimiter=limit={10 ** (limit_db / 20):.4f}:attack=5:release=60:level=false"
    first = measure("")
    gain = (target - first["i"]) if first["i"] is not None else 0.0
    passes = [{"gain_db": round(gain, 2), "pre_i": first["i"], "pre_peak": first["tp"]}]
    after = measure(f",volume={gain:.2f}dB,{limiter}")
    # the limiter takes back part of each gain step on peaky sound effects, so iterate to convergence
    # (bounded: 4 corrective passes, never more than +6 dB over the first estimate)
    first_gain = gain
    for _ in range(4):
        if after["i"] is None or abs(after["i"] - target) <= 0.25:
            break
        gain = min(first_gain + 6.0, gain + (target - after["i"]) * 1.15)
        after = measure(f",volume={gain:.2f}dB,{limiter}")
        passes.append({"gain_db": round(gain, 2), "post_i": after["i"], "post_peak": after["tp"]})
    graph = base + f",volume={gain:.2f}dB,{limiter},afade=t=out:st={max(0.0, dur - 0.6):.3f}:d=0.6[out]"
    cmd = inputs + ["-filter_complex", graph, "-map", "[out]", "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"Pop Info audio mix failed: {proc.stderr[-600:]}")
    return {**stems, "mix_path": str(out_path), "loudness_target": "-14 LUFS integrated, limiter at -2 dBFS",
            "loudness_passes": passes, "measured_mix": after, "ducking": "sidechain 8:1 keyed by narration"}

