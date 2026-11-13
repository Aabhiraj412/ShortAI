"""
Controlled Pop Info test renders (never published, never written to the studio database).

usage: python backend/scripts/render_pop_info_test_videos.py <topics.json> <out_root> [index ...]

For each topic in topics.json (title, category, summary, extended_extract, factual_bullets,
key_entities, sources, hook): writes the script (grounded writer; records its real source),
synthesizes narration with the production TTS engine, acquires licensed photos with the production
acquisition pipeline (VALIDATION context, isolated registry), renders with the Pop Info engine
through Lemo-Opuscar, mixes synthesized sound design, and runs the visual quality gate.
A summary.json is written next to the videos.
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.content.grounded_script_writer import write_grounded_script   # noqa: E402
from backend.app.core.execution_context import ExecutionContext                 # noqa: E402


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def photos_for(topic, ws, ctx):
    from backend.app.rendering.documentary_storyboard_builder import acquire_isolated_topic_assets
    try:
        assets, _attrib, _manifest = acquire_isolated_topic_assets(topic, ws, ctx, max_assets=4, stats_out={})
    except Exception as exc:
        print(f"  photo acquisition failed ({type(exc).__name__}); procedural visuals only")
        return []
    from backend.app.rendering.pop_info.pipeline import photos_from_media_assets
    return photos_from_media_assets(assets)


def main() -> int:
    topics = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_root = Path(sys.argv[2]).resolve()
    picks = [int(x) for x in sys.argv[3:]] or list(range(len(topics)))
    out_root.mkdir(parents=True, exist_ok=True)

    from backend.app.orchestration.production_workspace import ProductionWorkspace
    from backend.app.rendering.documentary_storyboard_builder import synthesize_and_probe_narration
    from backend.app.rendering.pop_info.pipeline import render_pop_info
    from backend.app.services.expressive_tts_engine import ExpressiveTTSEngine
    from backend.app.core.paths import PROJECT_ROOT, get_ffmpeg_path, get_ffprobe_path

    workspace_root = Path(str(PROJECT_ROOT))
    if not str(out_root).lower().startswith(str(workspace_root).lower()):
        workspace_root = Path(out_root.anchor)   # the renderer only needs the film inside its workspace
    summary = []
    for idx in picks:
        raw = topics[idx]
        job_id = f"popinfo_test_{idx + 1}_{slug(raw['title'])}"
        print(f"== {job_id}")
        t0 = time.perf_counter()
        topic = SimpleNamespace(id=raw.get("id", job_id), title=raw["title"], category=raw.get("category", "Science"),
                                summary=raw.get("summary", ""), extended_extract=raw.get("extended_extract", ""),
                                factual_bullets=raw.get("factual_bullets", []), key_entities=raw.get("key_entities", []),
                                sources=raw.get("sources", []), coordinates=None, discovery_source=raw.get("discovery_source", ""),
                                composite_score=9.0, research_fingerprint="")
        entry = {"job_id": job_id, "topic": raw["title"], "category": topic.category}
        try:
            ws = ProductionWorkspace(production_id=job_id, topic_id=str(topic.id), topic_title=topic.title,
                                     category=topic.category, base_dir=str(out_root / job_id))
            ws.initialize_workspace()
            script = write_grounded_script(topic, raw.get("hook", ""), job_id=job_id,
                                           execution_context=ExecutionContext.VALIDATION)
            entry["script_source"] = script.get("script_source")
            entry["script"] = script["full_script"]
            (Path(ws.scripts_dir) / f"script_{job_id}.json").write_text(json.dumps(script, indent=2), encoding="utf-8")
            tts = ExpressiveTTSEngine(ffmpeg_path=get_ffmpeg_path(), ffprobe_path=get_ffprobe_path())
            voice, timings, vdur = synthesize_and_probe_narration(tts, script["sections"], ws)
            photos = photos_for(topic, ws, ExecutionContext.VALIDATION)
            entry["photos_used_candidates"] = len(photos)
            out_mp4 = out_root / f"{job_id}.mp4"
            res = render_pop_info(workspace_root=workspace_root, prod_dir=Path(ws.prod_dir), job_id=job_id,
                                  topic=topic.title, category=topic.category, script_data=script,
                                  section_timings=timings, voice_path=voice, duration_sec=round(vdur + 1.6, 2),
                                  output_path=out_mp4, photos=photos)
            entry.update({"ok": True, "video": str(out_mp4), "quality": res.get("quality"),
                          "quality_report": res.get("quality_report_path"), "layout": res.get("layout"),
                          "fingerprints": res["film"]["fingerprints"], "asset_provenance": res["film"]["asset_provenance"],
                          "timing_sec": res["timing_sec"]})
        except Exception as exc:
            entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:800], "trace": traceback.format_exc()[-1500:]})
        entry["wall_sec"] = round(time.perf_counter() - t0, 1)
        print(json.dumps({k: entry.get(k) for k in ("ok", "script_source", "quality", "error", "wall_sec")}, default=str))
        summary.append(entry)
        (out_root / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0 if all(e.get("ok") for e in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
