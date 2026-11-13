"""
Visual quality gate: scores a *rendered MP4* (frames and audio), not the storyboard.

Every dimension score is derived from measurements listed as evidence. Where a dimension depends on
something the analysis cannot see (for example whether a scene matches its narration), the score
uses the film plan as a declared proxy and says so; nothing is invented. A dimension with no usable
measurement is reported as "not_measured" and its weight is excluded from the total.

Measured from the video:
  frames sampled at 4 fps (scaled to 135 x 240): blank, frozen runs, contrast, motion energy,
  scene-cut cadence, near-duplicate scenes (dHash), opening and ending activity, edge activity in
  the Shorts overlay bands;
  audio via ffmpeg: EBU R128 loudness and true peak, silences, clipping;
  container via ffprobe: resolution, aspect, duration, streams.
Measured from the film page (optional): text boxes per sampled frame against the safe area.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

DEFAULT_WEIGHTS = {"hook": 15, "storytelling": 15, "visual_quality": 15, "motion": 15,
                   "pacing": 10, "audio": 10, "typography": 10, "ending": 10}
DEFAULT_THRESHOLDS = {"pass_score": 70.0, "min_duration": 15.0, "max_duration": 60.0, "target_lufs": -14.0,
                      "max_true_peak": -1.0, "max_silence_sec": 1.6, "blank_std": 6.0, "frozen_sec": 2.5,
                      "min_scene_sec": 1.5, "max_scene_sec": 9.0}
SAMPLE_FPS = 4
FW, FH = 135, 240


@dataclass
class Issue:
    t: Optional[float]
    severity: str          # critical | major | minor
    dimension: str
    problem: str
    evidence: str
    fix: str


@dataclass
class QualityReport:
    video: str
    overall_score: Optional[float]
    passed: bool
    dimensions: Dict[str, Dict[str, Any]]
    issues: List[Dict[str, Any]]
    measurements: Dict[str, Any]
    weights: Dict[str, float]
    thresholds: Dict[str, float]
    gate_version: str = "visual_qc_v1"


def _run(cmd: Sequence[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


def probe(ffprobe: str, path: str) -> Dict[str, Any]:
    out = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"width": int(v.get("width", 0)), "height": int(v.get("height", 0)),
            "duration": float(data.get("format", {}).get("duration", 0.0)), "has_audio": a is not None,
            "fps": v.get("avg_frame_rate", "")}


def sample_frames(ffmpeg: str, path: str) -> np.ndarray:
    out = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vf", f"fps={SAMPLE_FPS},scale={FW}:{FH}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=600)
    raw = np.frombuffer(out.stdout, dtype=np.uint8)
    n = raw.size // (FW * FH * 3)
    return raw[: n * FW * FH * 3].reshape(n, FH, FW, 3).astype(np.float32)


def audio_stats(ffmpeg: str, path: str) -> Dict[str, Any]:
    r = _run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true,silencedetect=noise=-40dB:d=0.6",
              "-f", "null", "-"], timeout=300)
    err = r.stderr
    def last(pat):
        m = re.findall(pat, err)
        return float(m[-1]) if m else None
    silences = [(float(a), float(b)) for a, b in re.findall(r"silence_start: ([\d.]+)[\s\S]*?silence_end: ([\d.]+)", err)]
    return {"integrated_lufs": last(r"I:\s+(-?[\d.]+) LUFS"), "lra": last(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbtp": last(r"Peak:\s+(-?[\d.]+) dBFS"), "silences": silences}


def _dhash(img: np.ndarray) -> int:
    g = img.mean(axis=2)
    small = g.reshape(16, FH // 16, 9, FW // 9)[:, :, :, :].mean(axis=(1, 3)) if (FH % 16 == 0 and FW % 9 == 0) else \
        np.array([[g[int(y * FH / 16):int((y + 1) * FH / 16), int(x * FW / 9):int((x + 1) * FW / 9)].mean()
                   for x in range(9)] for y in range(16)])
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _ham(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def analyse(video: str, *, ffmpeg: str, ffprobe: str, plan: Optional[Dict[str, Any]] = None,
            layout_audit: Optional[List[Dict[str, Any]]] = None, weights: Optional[Dict[str, float]] = None,
            thresholds: Optional[Dict[str, float]] = None) -> QualityReport:
    W = {**DEFAULT_WEIGHTS, **(weights or {})}
    TH = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    issues: List[Issue] = []
    info = probe(ffprobe, video)
    frames = sample_frames(ffmpeg, video)
    n = len(frames)
    ts = np.arange(n) / SAMPLE_FPS
    meas: Dict[str, Any] = {"container": info, "frames_sampled": n}
    dims: Dict[str, Dict[str, Any]] = {}

    # ---- container
    dur = info["duration"]
    if not (info["width"] == 1080 and info["height"] == 1920):
        issues.append(Issue(None, "critical", "visual_quality", "wrong resolution / aspect",
                            f"{info['width']}x{info['height']}", "render at 1080x1920 (9:16)"))
    if not TH["min_duration"] <= dur <= TH["max_duration"]:
        issues.append(Issue(None, "critical", "pacing", "duration outside Shorts range",
                            f"{dur:.2f}s not in [{TH['min_duration']}, {TH['max_duration']}]", "trim or extend the script"))
    if n < 4:
        issues.append(Issue(None, "critical", "visual_quality", "video could not be decoded", f"{n} frames", "re-render"))
        return _finish(video, dims, issues, meas, W, TH)

    gray = frames.mean(axis=3)
    std = gray.reshape(n, -1).std(axis=1)
    diff = np.concatenate([[0.0], np.abs(np.diff(gray, axis=0)).reshape(n - 1, -1).mean(axis=1)])
    meas["mean_contrast_std"] = round(float(std.mean()), 2)
    meas["mean_motion"] = round(float(diff[1:].mean()), 3)

    # blank frames (ignore the first/last 0.75 s where the iris wipe is intentionally solid)
    edge = int(0.75 * SAMPLE_FPS)
    blank_idx = [i for i in range(edge, n - edge) if std[i] < TH["blank_std"]]
    for i in blank_idx[:5]:
        issues.append(Issue(round(float(ts[i]), 2), "critical", "visual_quality", "blank frame",
                            f"luma std {std[i]:.1f} < {TH['blank_std']}", "check missing media or a failed scene draw"))
    # frozen runs
    run, frozen = 0, []
    for i in range(1, n):
        run = run + 1 if diff[i] < 0.15 else 0
        if run == int(TH["frozen_sec"] * SAMPLE_FPS):
            frozen.append(round(float(ts[i - run + 1]), 2))
    for t0 in frozen[:5]:
        issues.append(Issue(t0, "major", "motion", "frozen picture", f"no visible change for >= {TH['frozen_sec']} s",
                            "add continuous motion (camera push, ambient movement)"))
    meas["blank_frames"] = len(blank_idx); meas["frozen_runs"] = frozen

    # scene cuts and cadence
    # Revised 2026-11-14: compare frames 0.5 s apart and keep local maxima above the 90th percentile.
    # The old rule (adjacent frames, 97th percentile) missed most 0.5 s slide/wipe transitions: on the
    # Pop Info test renders it found 2-4 cuts where the plan had 7-10; this rule finds 7/7, 8/8, 9/10
    # with at most one false cut, and is applied identically to every video, including baselines.
    lag = 2
    lagd = np.zeros(n)
    for i in range(lag, n):
        lagd[i] = float(np.abs(gray[i] - gray[i - lag]).mean())
    thr = max(8.0, float(np.percentile(lagd[lag:], 90)) * 0.85) if n > lag + 2 else 8.0
    cuts = [round(float(ts[i]), 2) for i in range(lag, n - 1)
            if lagd[i] > thr and lagd[i] >= lagd[i - 1] and lagd[i] >= lagd[i + 1] and ts[i] >= 0.9 and ts[i] <= dur - 0.9]
    merged: List[float] = []
    for c in cuts:
        if not merged or c - merged[-1] > 1.0:
            merged.append(c)
    bounds = [0.0] + merged + [dur]
    lengths = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.3]
    meas["detected_cuts"] = merged; meas["scene_lengths"] = [round(x, 2) for x in lengths]

    # near-duplicate scenes: representative frame from the middle of each detected segment
    reps = [int(((a + b) / 2) * SAMPLE_FPS) for a, b in zip(bounds, bounds[1:]) if b - a > 0.8]
    hashes = [_dhash(frames[min(i, n - 1)]) for i in reps]
    dup_pairs = [(round(reps[i] / SAMPLE_FPS, 1), round(reps[j] / SAMPLE_FPS, 1))
                 for i in range(len(hashes)) for j in range(i + 2, len(hashes)) if _ham(hashes[i], hashes[j]) <= 8]
    meas["near_duplicate_scene_pairs"] = dup_pairs
    if hashes and len(dup_pairs) > max(1, len(hashes) // 3):
        issues.append(Issue(dup_pairs[0][1], "major", "storytelling", "repetitive visuals",
                            f"{len(dup_pairs)} near-identical non-adjacent scene pairs", "vary the scene template or subject"))

    # Shorts overlay bands: right 13% and bottom 19% should not carry dense detail (text, faces of icons)
    def edge_energy(region):
        g = region.mean(axis=2)
        return float(np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean())
    mid = frames[n // 4: 3 * n // 4] if n > 8 else frames
    band_right = np.mean([edge_energy(f[:, int(FW * 0.87):]) for f in mid])
    band_bottom = np.mean([edge_energy(f[int(FH * 0.81):, :]) for f in mid])
    centre = np.mean([edge_energy(f[int(FH * 0.12):int(FH * 0.78), int(FW * 0.08):int(FW * 0.86)]) for f in mid])
    meas["overlay_band_edge_ratio"] = {"right": round(band_right / (centre + 1e-6), 2), "bottom": round(band_bottom / (centre + 1e-6), 2)}

    # ---- audio
    au = audio_stats(ffmpeg, video) if info["has_audio"] else {}
    meas["audio"] = au
    long_sil = [(a, b) for a, b in au.get("silences", []) if b - a > TH["max_silence_sec"] and a > 0.5 and b < dur - 1.0]
    if not info["has_audio"] or au.get("integrated_lufs") is None or au["integrated_lufs"] < -35:
        issues.append(Issue(None, "critical", "audio", "missing narration", f"audio stats: {au}", "check voiceover mux"))
    for a, b in long_sil[:3]:
        issues.append(Issue(round(a, 2), "major", "audio", "unexpected silence", f"{b - a:.2f} s silent", "close gaps between sections"))
    if au.get("true_peak_dbtp") is not None and au["true_peak_dbtp"] > TH["max_true_peak"]:
        issues.append(Issue(None, "major", "audio", "clipping risk", f"true peak {au['true_peak_dbtp']} dBTP",
                            "limit to -1.5 dBTP"))

    # ---- page layout audit (text boxes)
    if layout_audit is not None:
        outside = [a for a in layout_audit if a.get("outside_safe")]
        small = [a for a in layout_audit if a.get("too_small")]
        over = [a for a in layout_audit if a.get("overlaps")]
        meas["layout_samples"] = len(layout_audit)
        for a in outside[:4]:
            issues.append(Issue(a["t"], "major", "typography", "text outside safe area",
                                json.dumps(a["outside_safe"][:2]), "shrink or move the text block"))
        for a in over[:3]:
            issues.append(Issue(a["t"], "major", "typography", "overlapping text", json.dumps(a["overlaps"][:2]), "re-space the layout"))
        for a in small[:2]:
            issues.append(Issue(a["t"], "minor", "typography", "text below mobile minimum", json.dumps(a["too_small"][:3]), "raise to >= 15 design px"))

    # ---- dimension scores (0-100) from the measurements above
    def score(v):
        return round(float(np.clip(v, 0, 100)), 1)

    open_n = min(n, 2 * SAMPLE_FPS)
    opening_motion = float(diff[1:open_n].mean()) if open_n > 2 else 0.0
    opening_contrast = float(std[edge:open_n].mean()) if open_n > edge else float(std[:open_n].mean())
    # Hook (revised 2026-11-14: the old formula saturated at ~100 for any animated opening).
    # Visual half: motion and contrast in the first 2 s on stricter scales, plus how soon the first
    # real change of picture arrives. Narrative half (needs the plan): the hook evaluator on the
    # opening narration. Without a plan the score is visual-only and says so.
    first_change = next((c for c in merged if c >= 0.9), None)
    v_cut = 1.0 if first_change is not None and first_change <= 3.5 else (
        max(0.0, 1 - ((first_change or dur) - 3.5) / 4.0))
    blank_open = any(i < open_n for i in blank_idx)
    visual = 100 * (0.4 * min(1, opening_motion / 8) + 0.3 * min(1, opening_contrast / 60) + 0.3 * v_cut)
    if blank_open:
        visual *= 0.5
    hook_ev = f"first 2 s: motion {opening_motion:.2f}, contrast {opening_contrast:.1f}; first picture change at {first_change} s"
    hook_text = ""
    if plan:
        hook_text = " ".join(s.get("narration", "") for s in plan.get("scenes", []) if s.get("stage") == "HOOK")
    if hook_text:
        from backend.app.content.hook_evaluator import evaluate_hook
        rest = " ".join(s.get("narration", "") for s in plan.get("scenes", []) if s.get("stage") != "HOOK")
        hv = evaluate_hook(hook_text, topic_title=str(plan.get("topic", "")), rest_of_script=rest)
        meas["hook_text_evaluation"] = {"score": hv["score"], "penalties": hv["penalties"]}
        dims["hook"] = {"score": score(0.5 * visual + 5.0 * hv["score"]),
                        "basis": "measured opening (50%) + hook text evaluation (50%)",
                        "evidence": f"{hook_ev}; visual {visual:.0f}/100; hook text {hv['score']}/10"}
    else:
        dims["hook"] = {"score": score(visual), "basis": "measured opening only (no plan: hook text not judged)",
                        "evidence": hook_ev}

    distinct = 1 - (len(dup_pairs) / max(1, len(hashes) * (len(hashes) - 1) / 2))
    story = 60 * distinct
    basis = "measured (visual distinctness only)"
    if plan:
        scenes = plan.get("scenes", [])
        matched = sum(1 for s in scenes if s.get("main_subject") and s.get("narration") and
                      any(k in s["narration"].lower() for k in _subject_keys(s["main_subject"])))
        varied = len({s["template"] for s in scenes}) / max(1, min(len(scenes), 6))
        story += 25 * (matched / max(1, len(scenes))) + 15 * min(1, varied)
        basis = "measured distinctness + plan proxy (subject keyword in narration, template variety)"
    else:
        story = story / 0.6 * 0.6
    dims["storytelling"] = {"score": score(story), "basis": basis, "evidence": f"distinct {distinct:.2f}, scenes {len(hashes)}"}

    vq = 40 * min(1, float(np.median(std)) / 45) + 30 * (1 - min(1, len(blank_idx) / 4)) + 30 * (1 if info["width"] == 1080 and info["height"] == 1920 else 0)
    dims["visual_quality"] = {"score": score(vq), "basis": "measured", "evidence": f"median contrast {np.median(std):.1f}, blank {len(blank_idx)}"}

    moving = float((diff[1:] > 0.4).mean())
    jitter = float(np.mean(np.abs(np.diff(diff[1:])) > 6)) if n > 3 else 0.0
    dims["motion"] = {"score": score(70 * moving + 30 * (1 - min(1, jitter * 4)) - 15 * len(frozen)),
                      "basis": "measured", "evidence": f"moving frames {moving:.0%}, jitter rate {jitter:.2%}, frozen runs {len(frozen)}"}

    good_len = [x for x in lengths if TH["min_scene_sec"] <= x <= TH["max_scene_sec"]]
    pace = 100 * len(good_len) / max(1, len(lengths)) if lengths else 0
    if not TH["min_duration"] <= dur <= TH["max_duration"]:
        pace -= 40
    dims["pacing"] = {"score": score(pace), "basis": "measured", "evidence": f"scene lengths {[round(x, 1) for x in lengths]}"}

    if au.get("integrated_lufs") is not None:
        lufs_dev = abs(au["integrated_lufs"] - TH["target_lufs"])
        tp_ok = au.get("true_peak_dbtp") is None or au["true_peak_dbtp"] <= TH["max_true_peak"]
        a_score = 50 * max(0, 1 - lufs_dev / 8) + 25 * (1 if tp_ok else 0) + 25 * max(0, 1 - len(long_sil) / 3)
        dims["audio"] = {"score": score(a_score), "basis": "measured",
                         "evidence": f"{au['integrated_lufs']} LUFS, TP {au.get('true_peak_dbtp')} dBTP, long silences {len(long_sil)}"}
    else:
        dims["audio"] = {"score": 0.0, "basis": "measured", "evidence": "no measurable audio"}

    ratio = meas["overlay_band_edge_ratio"]
    band_pen = max(0, ratio["right"] - 0.8) * 40 + max(0, ratio["bottom"] - 0.8) * 40
    if layout_audit is not None:
        bad = sum(1 for a in layout_audit if a.get("outside_safe") or a.get("overlaps"))
        small = sum(1 for a in layout_audit if a.get("too_small"))
        typo = 100 * (1 - bad / max(1, len(layout_audit))) - 5 * small - band_pen
        dims["typography"] = {"score": score(typo), "basis": "measured (page text boxes + overlay-band edges)",
                              "evidence": f"{bad}/{len(layout_audit)} sampled frames with out-of-safe or overlapping text"}
    else:
        dims["typography"] = {"score": None, "basis": "not_measured",
                              "evidence": f"no page layout audit; overlay-band edge ratios {ratio}"}

    tail = max(1, int(3 * SAMPLE_FPS))
    end_motion = float(diff[-tail:].mean())
    end_blank = any(i >= n - tail - edge for i in blank_idx)
    dims["ending"] = {"score": score(60 * min(1, end_motion / 3) + 40 * (0 if end_blank else 1)),
                      "basis": "measured", "evidence": f"last 3 s motion {end_motion:.2f}, blank {end_blank}"}
    return _finish(video, dims, issues, meas, W, TH)


def _subject_keys(icon: str) -> List[str]:
    from backend.app.rendering.pop_info.scene_planner import ICON_KEYWORDS
    return list(ICON_KEYWORDS.get(icon, (icon,)))


def _finish(video, dims, issues, meas, W, TH) -> QualityReport:
    scored = {k: v for k, v in dims.items() if v.get("score") is not None}
    wsum = sum(W[k] for k in scored)
    overall = round(sum(W[k] * scored[k]["score"] for k in scored) / wsum, 1) if wsum else None
    for k, v in dims.items():
        v["weight"] = W.get(k)
    critical = any(i.severity == "critical" for i in issues)
    passed = overall is not None and overall >= TH["pass_score"] and not critical
    return QualityReport(video=str(video), overall_score=overall, passed=passed, dimensions=dims,
                         issues=[asdict(i) for i in issues], measurements=meas, weights=W, thresholds=TH)


def write_report(report: QualityReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return path
