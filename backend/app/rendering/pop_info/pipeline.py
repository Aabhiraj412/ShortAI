"""
Pop Info render pipeline shared by the orchestrator (behind the feature flag) and the controlled
test-render script: build film -> Lemo-Opuscar render -> sound design mix -> mux -> layout probe ->
visual quality gate. Every step's output stays in the film folder so a failure never corrupts the
production workspace, and render retries are bounded by the caller.
"""

from __future__ import annotations

import json
import os
import subprocess
from backend.app.rendering.pop_info.scene_planner import plan_scenes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.app.core.paths import TOOLS_DIR, get_ffmpeg_path, get_ffprobe_path
from backend.app.rendering.pop_info.film_builder import build_pop_info_film
from backend.app.rendering.pop_info.scene_planner import ENGINE_VERSION
from backend.app.rendering.pop_info.sound_design import mix_audio

FEATURE_FLAG_ENV = "CRAZZYDOCS_VISUAL_ENGINE"
ENGINES = ("legacy", ENGINE_VERSION)


def active_visual_engine() -> str:
    """Production uses the legacy film builder unless the operator opts in (env or .env.local)."""
    value = (os.environ.get(FEATURE_FLAG_ENV) or "").strip().lower()
    if not value:
        try:
            from backend.app.core.paths import ENV_LOCAL_PATH
            for line in Path(ENV_LOCAL_PATH).read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(FEATURE_FLAG_ENV + "="):
                    value = line.split("=", 1)[1].strip().strip("'\"").lower()
        except OSError:
            value = ""
    return value if value in ENGINES else "legacy"


def photos_from_media_assets(assets, min_relevance: float = 0.5) -> List[Dict[str, Any]]:
    """Licensed, verified, relevant photos only; a successful download alone never qualifies an asset."""
    out: List[Dict[str, Any]] = []
    for a in assets or []:
        path = getattr(a, "portrait_path", None) or getattr(a, "local_path", None)
        status = str(getattr(getattr(a, "verification_status", ""), "value", getattr(a, "verification_status", ""))).upper()
        lic = getattr(getattr(a, "license_type", ""), "value", getattr(a, "license_type", ""))
        if (not path or not Path(path).is_file() or "VERIFIED" not in status or not lic
                or float(getattr(a, "relevance_score", 0) or 0) < min_relevance
                or getattr(a, "asset_type", "image") not in ("image",)):
            continue
        out.append({"path": path, "license": f"{lic} {getattr(a, 'license_version', '')}".strip(),
                    "creator": getattr(a, "creator", None), "provider": getattr(a, "source_repository", None),
                    "source_url": getattr(a, "source_url", None), "sha256": getattr(a, "asset_hash", None),
                    "relevance": getattr(a, "relevance_score", None), "title": getattr(a, "title", None)})
    return out


def lemo_adapter():
    from backend.app.rendering.lemo_opuscar_adapter import LemoOpuscarAdapter
    return LemoOpuscarAdapter(library_root=TOOLS_DIR / "dependencies" / "lemo-opuscar",
                              node_executable=TOOLS_DIR / "nodejs" / "node.exe",
                              ffmpeg_bin=TOOLS_DIR / "ffmpeg" / "bin")


def probe_layout(film_dir: Path, times: Sequence[float]) -> Dict[str, Any]:
    adapter = lemo_adapter()
    script = Path(__file__).with_name("layout_probe.mjs")
    proc = subprocess.run([str(adapter.node_executable), str(script), str(adapter.library_root), str(film_dir),
                           ",".join(f"{t:.2f}" for t in times)],
                          capture_output=True, text=True, timeout=600, env=adapter._environment(),
                          cwd=str(adapter.library_root))
    if proc.returncode != 0:
        raise RuntimeError(f"layout probe failed: {(proc.stderr or proc.stdout)[-800:]}")
    return json.loads(proc.stdout)


def render_pop_info(*, workspace_root: Path, prod_dir: Path, job_id: str, topic: str, category: str,
                    script_data: Dict[str, Any], section_timings: Sequence[Dict[str, Any]], voice_path: str,
                    duration_sec: float, output_path: Path, photos: Sequence[Dict[str, Any]] = (),
                    run_quality_gate: bool = True, identity_check=None, select_photos: bool = False,
                    key_entities: Sequence[str] = (), asset_cache_root: Optional[Path] = None) -> Dict[str, Any]:
    from backend.app.services import visual_quality_gate as vqg

    t0 = time.perf_counter()
    scene_photos, photo_report = None, None
    if select_photos:
        # draft plan -> scene-level photo selection -> final plan with each photo on its own scene.
        # Any failure keeps the all-vector film: photos are an enhancement, never a dependency.
        from backend.app.core.paths import MEDIA_DIR
        from backend.app.rendering.pop_info.scene_photos import select_scene_photos
        try:
            draft = plan_scenes(job_id=job_id, topic_title=topic, category=category,
                                sections=script_data.get("sections") or [], timings=section_timings,
                                total_duration=float(duration_sec))
            scene_photos, photo_report = select_scene_photos(
                plan=draft, topic_title=topic, key_entities=key_entities, job_id=job_id,
                cache_root=Path(asset_cache_root or (Path(MEDIA_DIR) / "asset_cache")))
        except Exception as exc:
            photo_report = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "fallback": "all-vector film"}
        Path(prod_dir).mkdir(parents=True, exist_ok=True)
        (Path(prod_dir) / "photo_selection.json").write_text(json.dumps(photo_report, indent=2, default=str), encoding="utf-8")
    t_photos = time.perf_counter()
    film = build_pop_info_film(prod_dir=prod_dir, job_id=job_id, topic=topic, category=category,
                               script_data=script_data, section_timings=section_timings,
                               duration_sec=duration_sec, photos=photos, scene_photos=scene_photos)
    film_dir = Path(film["demo_dir"])
    if identity_check is not None:
        identity_check(film["fingerprints"])   # raises before any render time is spent on a repeated composition
    silent = film_dir / f"silent_{job_id}.mp4"
    lemo_adapter().render_video(workspace=workspace_root, demo_dir=film_dir, output_path=silent, fps=30, size=(1080, 1920))
    t_render = time.perf_counter()
    ffmpeg = get_ffmpeg_path()
    audio = mix_audio(ffmpeg=ffmpeg, voice_path=voice_path, plan=film["plan"], work_dir=film_dir / "audio",
                      out_path=film_dir / "audio" / "mix.wav")
    proc = subprocess.run([ffmpeg, "-y", "-i", str(silent), "-i", audio["mix_path"], "-map", "0:v:0", "-map", "1:a:0",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                           "-shortest", "-movflags", "+faststart", str(output_path)], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not Path(output_path).is_file():
        raise RuntimeError(f"Pop Info mux failed: {proc.stderr[-600:]}")
    result: Dict[str, Any] = {"engine": ENGINE_VERSION, "film": {k: v for k, v in film.items() if k != "plan"},
                              "audio": audio, "video_path": str(output_path),
                              "timing_sec": {"photo_selection": round(t_photos - t0, 1),
                                             "build_and_render": round(t_render - t_photos, 1), "total": None},
                              "photo_selection": photo_report}
    if run_quality_gate:
        scenes = film["plan"]["scenes"]
        times = sorted({round(s["start_sec"] + min(2.4, (s["end_sec"] - s["start_sec"]) * 0.7), 2) for s in scenes})
        try:
            layout = probe_layout(film_dir, times)
            samples = layout["samples"]
            result["layout"] = {"fonts_ok": layout.get("fonts_ok"), "asset_fallbacks": layout.get("asset_fallbacks")}
        except Exception as exc:   # the gate then reports typography as not measured
            samples = None
            result["layout"] = {"error": str(exc)[:300]}
        report = vqg.analyse(str(output_path), ffmpeg=ffmpeg, ffprobe=get_ffprobe_path(), plan=film["plan"], layout_audit=samples)
        result["quality_report_path"] = str(vqg.write_report(report, film_dir / "quality_report.json"))
        result["quality"] = {"overall": report.overall_score, "passed": report.passed,
                             "dimensions": {k: v["score"] for k, v in report.dimensions.items()}}
    result["timing_sec"]["total"] = round(time.perf_counter() - t0, 1)
    return result
