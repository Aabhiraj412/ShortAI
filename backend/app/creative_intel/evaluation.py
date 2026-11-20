"""Creative evaluation of one production: plan level (script, scenes) and rendered output (the actual MP4).

Thirteen dimensions, each ``{"score": 0..100 | None, "measured": bool, "source": ..., "evidence": ...}``:

hook, narrative_progression, storytelling, ending_payoff, pacing      <- script (existing ``evaluate_script``)
visual_relevance, scene_diversity, animation, transitions             <- Pop Info plan (rendered scenes) or storyboard
text_readability                                                       <- subtitles (.ass) timing and line length
composition, narration_audio, av_sync, visual_dynamics                <- the rendered video (FFmpeg analysis)

Unmeasurable dimensions are reported with ``score: None`` and the reason; they are excluded from the overall.
These are craft signals from the artifacts. They are not audience predictions: audience learning starts in
Stage 3.35, from the analytics joined on ``job_id`` / ``youtube_video_id``.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.app.rendering.ffmpeg_geometry import stderr_tail

VERSION = "3.32-creative-eval-1"
DIMENSIONS = ("hook", "narrative_progression", "storytelling", "ending_payoff", "pacing", "visual_relevance",
              "scene_diversity", "animation", "transitions", "text_readability", "composition", "narration_audio",
              "av_sync", "visual_dynamics")
TARGET_LUFS = (-17.0, -12.0)          # spoken Shorts: about -14 LUFS integrated
MAX_CPS = 17.0                        # subtitle reading speed (characters per second)
MAX_LINE_CHARS = 36                   # portrait 1080 px at the production font size
ANALYSIS_TIMEOUT_SEC = 240
SCENE_THRESHOLD = 0.08
SCENE_MERGE_SEC = 0.3


def _dim(score: Optional[float], source: str, evidence: str, measured: bool = True) -> Dict[str, Any]:
    if score is None:
        return {"score": None, "measured": False, "source": source, "evidence": evidence}
    return {"score": round(max(0.0, min(100.0, float(score))), 1), "measured": measured, "source": source,
            "evidence": evidence}


def _load(path: Optional[Path]) -> Optional[Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).is_file() else None
    except Exception:
        return None


def find_artifacts(prod_dir: Path, job_id: str) -> Dict[str, Optional[Path]]:
    p = Path(prod_dir)

    def first(*cands: Path) -> Optional[Path]:
        return next((c for c in cands if c.is_file()), None)
    return {
        "script": first(p / "scripts" / f"script_{job_id}.json"),
        "subtitles": first(p / "scripts" / f"subtitles_{job_id}.ass"),
        "storyboard": first(p / "storyboards" / f"storyboard_{job_id}.json"),
        "plan": first(p / "pop_info_film" / "plan.json"),
        "pop_quality": first(p / "pop_info_film" / "quality_report.json"),
        "manifest": first(p / "manifests" / f"render_manifest_{job_id}.json"),
        "video": first(p / "video" / f"showcase_{job_id}.mp4", *sorted((p / "video").glob("*.mp4"))
                       if (p / "video").is_dir() else ()),
    }


# -- plan level ----------------------------------------------------------------------------------------------------
def evaluate_script_dims(script: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    q = (script or {}).get("quality") or {}
    crit = q.get("criteria") or {}
    if not crit:
        why = "script quality report missing"
        return {k: _dim(None, "script", why) for k in ("hook", "narrative_progression", "storytelling",
                                                         "ending_payoff", "pacing")}

    def c(name: str) -> Optional[float]:
        v = (crit.get(name) or {}).get("score")
        return None if v is None else float(v) * 10.0

    def why(name: str) -> str:
        return str((crit.get(name) or {}).get("reason") or "")[:200]
    story = [v for v in (c("curiosity"), c("originality"), c("spoken_naturalness")) if v is not None]
    return {
        "hook": _dim(c("hook"), "script", f"hook type {script.get('hook_type') or 'n/a'}: {why('hook')}"),
        "narrative_progression": _dim(c("narrative_progression"), "script", why("narrative_progression")),
        "storytelling": _dim(statistics.mean(story) if story else None, "script",
                             "mean of curiosity loop, originality and spoken naturalness"),
        "ending_payoff": _dim(c("payoff"), "script", why("payoff")),
        "pacing": _dim(c("pacing"), "script", why("pacing")),
    }


def _scenes(plan: Optional[Dict[str, Any]], storyboard: Optional[Dict[str, Any]]) -> tuple:
    if plan and plan.get("scenes"):
        out = []
        for s in plan["scenes"]:
            out.append({"kind": s.get("template"), "composition": json.dumps(s.get("composition"), sort_keys=True)[:80]
                        if isinstance(s.get("composition"), (dict, list)) else s.get("composition"),
                        "transition": s.get("transition_in"), "animation": s.get("animation_direction"),
                        "visual_mode": s.get("visual_mode"), "relevance": None,
                        "duration": float(s.get("end_sec") or 0) - float(s.get("start_sec") or 0)})
        return out, "pop_info_plan"
    if storyboard and storyboard.get("beats"):
        return [{"kind": b.get("visual_type"), "composition": b.get("composition_family"),
                 "transition": b.get("transition"), "animation": b.get("animation_technique"),
                 "visual_mode": b.get("scene_layout"), "relevance": b.get("relevance_score"),
                 "duration": float(b.get("duration_sec") or 0)} for b in storyboard["beats"]], "storyboard"
    return [], "none"


def evaluate_scene_dims(plan: Optional[Dict[str, Any]], storyboard: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    scenes, src = _scenes(plan, storyboard)
    if not scenes:
        why = "no Pop Info plan or storyboard"
        return {k: _dim(None, "scenes", why) for k in ("visual_relevance", "scene_diversity", "animation", "transitions")}
    n = len(scenes)
    # visual relevance: storyboard relevance scores (Pop Info plans carry none; fall back to the storyboard's)
    rel = [float(s["relevance"]) for s in scenes if isinstance(s.get("relevance"), (int, float))]
    if not rel and storyboard:
        rel = [float(b["relevance_score"]) for b in storyboard.get("beats") or []
               if isinstance(b.get("relevance_score"), (int, float))]
    relevance = _dim(100.0 * statistics.mean(rel) if rel else None, "storyboard",
                     f"mean asset relevance {statistics.mean(rel):.2f} over {len(rel)} beats" if rel
                     else "no relevance scores recorded")
    kinds = [str(s.get("kind")) for s in scenes]
    distinct = len(set(kinds)) / n
    longest_run = max(len(list(g)) for g in _runs(kinds))
    comps = len({str(s.get("composition")) for s in scenes}) / n
    diversity = 100.0 * (0.5 * min(1.0, distinct / 0.6) + 0.3 * min(1.0, comps / 0.5) + 0.2 * (1.0 if longest_run <= 2 else 0.4))
    anim = [s for s in scenes if s.get("animation") and str(s.get("animation")).lower() not in ("none", "static", "")]
    anim_kinds = len({str(s.get("animation"))[:40] for s in anim})
    trans = [str(s.get("transition") or "cut").lower() for s in scenes[1:]]
    non_cut = sum(1 for t in trans if t not in ("cut", "none", ""))
    trans_kinds = len(set(trans))
    durs = [s["duration"] for s in scenes if s.get("duration")]
    return {
        "visual_relevance": relevance,
        "scene_diversity": _dim(diversity, src, f"{len(set(kinds))} scene types in {n} scenes, longest repeat {longest_run}"),
        "animation": _dim(100.0 * (0.7 * len(anim) / n + 0.3 * min(1.0, anim_kinds / 4.0)), src,
                          f"{len(anim)}/{n} animated scenes, {anim_kinds} animation styles"),
        "transitions": _dim(100.0 * (0.6 * (non_cut / len(trans) if trans else 0.0) + 0.4 * min(1.0, trans_kinds / 3.0))
                            if trans else None, src,
                            f"{non_cut}/{len(trans)} designed transitions, {trans_kinds} kinds; scene length "
                            f"{min(durs):.1f}-{max(durs):.1f}s" if trans and durs else "single scene"),
    }


def _runs(seq: Sequence[str]) -> List[List[str]]:
    runs: List[List[str]] = []
    for x in seq:
        if runs and runs[-1][-1] == x:
            runs[-1].append(x)
        else:
            runs.append([x])
    return runs or [[]]


_ASS_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[.](\d{2})")


def _ass_sec(t: str) -> float:
    m = _ASS_TIME.match(t.strip())
    if not m:
        return 0.0
    h, mi, s, cs = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + cs / 100.0


def evaluate_subtitles(ass_path: Optional[Path]) -> Dict[str, Any]:
    if not ass_path:
        return _dim(None, "subtitles", "no subtitle file")
    events = []
    for line in Path(ass_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                text = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", "\n")
                events.append((_ass_sec(parts[1]), _ass_sec(parts[2]), text))
    if not events:
        return _dim(None, "subtitles", "subtitle file has no dialogue events")
    cps = [len(t.replace("\n", "")) / max(0.2, e - s) for s, e, t in events]
    longest = max(len(l) for _, _, t in events for l in t.split("\n"))
    too_fast = sum(1 for c in cps if c > MAX_CPS) / len(cps)
    score = 100.0 * (0.7 * (1.0 - too_fast) + 0.3 * (1.0 if longest <= MAX_LINE_CHARS else MAX_LINE_CHARS / longest))
    return _dim(score, "subtitles", f"{len(events)} captions, median {statistics.median(cps):.1f} chars/s, "
                                    f"{too_fast:.0%} above {MAX_CPS:.0f} chars/s, longest line {longest} chars")


# -- rendered output -----------------------------------------------------------------------------------------------
def probe_streams(video: Path, ffprobe: str) -> Dict[str, Any]:
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate,duration,start_time:format=duration",
                          "-of", "json", str(video)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr_tail(out.stderr, 300)}")
    return json.loads(out.stdout or "{}")


def analyse_video(video: Path, ffmpeg: str, *, timeout: float = ANALYSIS_TIMEOUT_SEC) -> Dict[str, Any]:
    """One decode pass at 270 px wide: scene changes, freezes, black frames, luma/saturation, loudness."""
    # Pop Info uses eased, designed transitions: their scene score peaks around 0.1-0.3, never a hard cut (>0.3).
    # A visual change event is a frame above SCENE_THRESHOLD, with events closer than SCENE_MERGE_SEC merged.
    vf = ("scale=270:-2,signalstats,select='gte(scene,0)',"
          "metadata=mode=print:key=lavfi.scene_score:file=-,metadata=mode=print:key=lavfi.signalstats.YAVG:file=-,"
          "freezedetect=n=0.003:d=1.0,blackdetect=d=0.4:pix_th=0.08")
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-vf", vf, "-af", "ebur128=peak=true",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)   # run() kills on timeout
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg analysis failed: {stderr_tail(proc.stderr, 400)}")
    err, out = proc.stderr, proc.stdout
    yavg = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=(\d+(?:\.\d+)?)", out)]
    freeze = sum(float(v) for v in re.findall(r"freeze_duration: (\d+(?:\.\d+)?)", err))
    black = sum(float(v) for v in re.findall(r"black_duration:(\d+(?:\.\d+)?)", err))
    scenes, last_t, cur_t = 0, -10.0, 0.0
    for line in out.splitlines():
        m = re.search(r"pts_time:(\d+(?:\.\d+)?)", line)
        if m:
            cur_t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=(\d+(?:\.\d+)?)", line)
        if m and float(m.group(1)) > SCENE_THRESHOLD and cur_t - last_t >= SCENE_MERGE_SEC:
            scenes, last_t = scenes + 1, cur_t
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)
    return {"scene_changes": scenes, "freeze_sec": round(freeze, 2), "black_sec": round(black, 2),
            "luma_mean": round(statistics.mean(yavg), 1) if yavg else None,
            "luma_stdev": round(statistics.pstdev(yavg), 1) if len(yavg) > 1 else None,
            "integrated_lufs": float(lufs[-1]) if lufs else None, "true_peak_dbfs": float(peak[-1]) if peak else None}


def evaluate_render_dims(video: Optional[Path], ffmpeg: Optional[str], ffprobe: Optional[str]) -> tuple:
    keys = ("composition", "narration_audio", "av_sync", "visual_dynamics")
    if not video or not ffmpeg or not ffprobe:
        return {k: _dim(None, "render", "rendered video or FFmpeg not available") for k in keys}, {}
    try:
        probe = probe_streams(video, ffprobe)
        stats = analyse_video(video, ffmpeg)
    except Exception as exc:
        return {k: _dim(None, "render", f"analysis failed: {str(exc)[:160]}") for k in keys}, {"error": str(exc)[:300]}
    streams = probe.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    dur = float((probe.get("format") or {}).get("duration") or v.get("duration") or 0)
    stats.update({"duration_sec": round(dur, 2), "width": v.get("width"), "height": v.get("height"),
                  "fps": v.get("r_frame_rate"), "has_audio": bool(a)})
    # composition: exposure in a readable range, frame-to-frame tonal variety, no dead frames
    lm, ls = stats["luma_mean"], stats["luma_stdev"]
    if lm is None:
        comp = _dim(None, "render", "no frame statistics")
    else:
        exposure = 1.0 if 45 <= lm <= 190 else max(0.0, 1.0 - min(abs(lm - 45), abs(lm - 190)) / 60.0)
        variety = min(1.0, (ls or 0.0) / 15.0)
        dead = min(1.0, (stats["freeze_sec"] + stats["black_sec"]) / max(1.0, dur))
        comp = _dim(100.0 * (0.5 * exposure + 0.25 * variety + 0.25 * (1.0 - dead)), "render",
                    f"mean luma {lm}, luma variety {ls}, frozen {stats['freeze_sec']}s, black {stats['black_sec']}s")
    # narration audio: integrated loudness near the Shorts target, no clipping
    lufs, peak = stats["integrated_lufs"], stats["true_peak_dbfs"]
    if not a or lufs is None:
        audio = _dim(None, "render", "no audio stream or loudness measurement")
    else:
        lo, hi = TARGET_LUFS
        dist = 0.0 if lo <= lufs <= hi else min(abs(lufs - lo), abs(lufs - hi))
        audio = _dim(100.0 * max(0.0, 1.0 - dist / 8.0) * (1.0 if (peak is None or peak <= -0.5) else 0.8), "render",
                     f"integrated {lufs} LUFS (target {lo} to {hi}), true peak {peak} dBFS")
    # A/V sync proxy: stream durations and start offsets agree (word-level sync needs forced alignment)
    if a and v:
        try:
            dv, da = float(v.get("duration") or dur), float(a.get("duration") or dur)
            off = abs(float(a.get("start_time") or 0) - float(v.get("start_time") or 0))
            gap = abs(dv - da)
            sync = _dim(100.0 * max(0.0, 1.0 - max(0.0, gap - 0.1) / 1.0) * (1.0 if off < 0.05 else 0.7), "render",
                        f"video {dv:.2f}s vs audio {da:.2f}s, start offset {off:.3f}s (stream-level check)")
        except (TypeError, ValueError):
            sync = _dim(None, "render", "stream timing unavailable")
    else:
        sync = _dim(None, "render", "missing audio or video stream")
    per_min = stats["scene_changes"] / max(dur / 60.0, 0.1)
    dyn = 1.0 if 12 <= per_min <= 45 else max(0.0, 1.0 - min(abs(per_min - 12), abs(per_min - 45)) / 25.0)
    dynamics = _dim(100.0 * dyn, "render", f"{stats['scene_changes']} visual changes ({per_min:.0f}/min; 12-45/min is lively but watchable)")
    return {"composition": comp, "narration_audio": audio, "av_sync": sync, "visual_dynamics": dynamics}, stats


def evaluate_production(prod_dir: Path, job_id: str, *, ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None,
                        analyse_render: bool = True) -> Dict[str, Any]:
    art = find_artifacts(prod_dir, job_id)
    script = _load(art["script"]) or {}
    plan = _load(art["plan"])
    storyboard = _load(art["storyboard"])
    manifest = _load(art["manifest"]) or {}
    pop_q = _load(art["pop_quality"]) or {}
    dims: Dict[str, Dict[str, Any]] = {}
    dims.update(evaluate_script_dims(script))
    dims.update(evaluate_scene_dims(plan, storyboard))
    dims["text_readability"] = evaluate_subtitles(art["subtitles"])
    render_stats: Dict[str, Any] = {}
    if analyse_render:
        rd, render_stats = evaluate_render_dims(art["video"], ffmpeg, ffprobe)
        dims.update(rd)
    else:
        dims.update({k: _dim(None, "render", "render analysis skipped") for k in
                     ("composition", "narration_audio", "av_sync", "visual_dynamics")})
    measured = [d["score"] for d in dims.values() if d["score"] is not None]
    plan_keys = ("hook", "narrative_progression", "storytelling", "ending_payoff", "pacing", "visual_relevance",
                 "scene_diversity", "animation", "transitions", "text_readability")
    render_keys = ("composition", "narration_audio", "av_sync", "visual_dynamics")

    def avg(keys: Sequence[str]) -> Optional[float]:
        vals = [dims[k]["score"] for k in keys if dims[k]["score"] is not None]
        return round(statistics.mean(vals), 1) if vals else None
    weakest = sorted(((k, d["score"]) for k, d in dims.items() if d["score"] is not None), key=lambda x: x[1])[:3]
    return {
        "version": VERSION, "job_id": job_id,
        "overall": round(statistics.mean(measured), 1) if measured else None,
        "plan_score": avg(plan_keys), "render_score": avg(render_keys),
        "dimensions": dims, "weakest": [{"dimension": k, "score": s} for k, s in weakest],
        "render_stats": render_stats,
        "pop_info_quality_gate": {"overall_score": pop_q.get("overall_score"), "passed": pop_q.get("passed")} if pop_q else None,
        "decisions": {
            "hook_type": script.get("hook_type") or None, "angle": str(script.get("angle") or "")[:200] or None,
            "hook_text": next((s.get("text") for s in script.get("sections") or [] if str(s.get("stage")).upper() == "HOOK"), None),
            "script_source": script.get("script_source"), "script_fingerprint": manifest.get("script_fingerprint"),
            "renderer": manifest.get("renderer"), "visual_engine": manifest.get("visual_engine"),
            "creative_direction": manifest.get("creative_direction"),
            "scene_templates": [s.get("template") for s in (plan or {}).get("scenes") or []],
            "design": (plan or {}).get("design"),
        },
        "artifacts": {k: (str(v) if v else None) for k, v in art.items()},
        "note": "Craft signals measured from the production's own artifacts; not an audience prediction.",
    }
