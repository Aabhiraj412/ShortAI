"""
Controlled Pop Info test renders (never published, never written to the studio database).

usage: python backend/scripts/render_pop_info_test_videos.py <topics.json> <out_root> [index ...]
           [--scripts gemini_validation.json] [--scene-photos] [--identity-db copy_of_studio.db]

--scripts       reuse scripts already generated (e.g. by validate_gemini_scripts.py) instead of
                calling the writer again; matched by topic title, source recorded as generated.
--scene-photos  scene-level photo selection (scene_photos.py) instead of topic-level acquisition.
--identity-db   run the creative-identity near-duplicate checks against this database (pass a
                COPY of studio.db; it is written to) and record the maximum similarities.

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


def _flag(args, name, has_value=True):
    if name not in args:
        return None
    i = args.index(name)
    val = args[i + 1] if has_value else True
    del args[i:i + (2 if has_value else 1)]
    return val


def identity_report(db_path: str, job_id: str, topic, script: dict, fingerprints: dict) -> dict:
    from backend.app.content.creative_identity_registry import (
        CreativeIdentityRegistry, _shingles, _words, jaccard)
    reg = CreativeIdentityRegistry(db_path=db_path)
    secs = script.get("sections") or []
    hook = secs[0]["text"] if secs else ""
    ending = secs[-1]["text"] if secs else ""
    concepts = fingerprints.get("visual_concepts", [])
    dup = reg.near_duplicates(job_id=job_id, topic_title=topic.title, script_text=script["full_script"],
                              hook_text=hook, ending_text=ending, visual_concepts=concepts,
                              script_source=script.get("script_source", ""))
    best = {"script": 0.0, "hook": 0.0, "visual": 0.0}
    for r in reg.all():
        if r["job_id"] == job_id:
            continue
        if r.get("script_text"):
            best["script"] = max(best["script"], jaccard(_shingles(script["full_script"]), _shingles(r["script_text"])))
        if r.get("hook_text"):
            best["hook"] = max(best["hook"], jaccard(_words(hook), _words(r["hook_text"])))
        if r.get("visual_concepts_json") not in (None, "", "[]"):
            best["visual"] = max(best["visual"], jaccard(set(concepts), set(json.loads(r["visual_concepts_json"]))))
    reg.reserve(job_id, topic_title=topic.title, category=topic.category)
    reg.update(job_id, script_text=script["full_script"], hook_text=hook, ending_text=ending,
               visual_concepts_json=json.dumps(concepts), script_source=script.get("script_source", ""),
               visual_engine="pop_info_v1")
    return {"blocking": dup["blocking"], "advisory": dup["advisory"],
            "max_similarity": {k: round(v, 3) for k, v in best.items()}, "compared_rows": len(reg.all()) - 1}


def main() -> int:
    args = sys.argv[1:]
    scripts_file = _flag(args, "--scripts")
    scene_photos = bool(_flag(args, "--scene-photos", has_value=False))
    identity_db = _flag(args, "--identity-db")
    topics = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out_root = Path(args[1]).resolve()
    picks = [int(x) for x in args[2:]] or list(range(len(topics)))
    given = {}
    if scripts_file:
        for r in json.loads(Path(scripts_file).read_text(encoding="utf-8"))["results"]:
            given[r["topic"]] = r
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
            if raw["title"] in given:
                g = given[raw["title"]]
                from backend.app.content.script_quality import evaluate_script
                script = {"sections": g["sections"], "full_script": g["full_script"], "word_count": g["word_count"],
                          "script_source": g["script_source"], "model": g.get("model"), "angle": g.get("angle"),
                          "hook_type": g.get("hook_type"), "reused_from": scripts_file}
                script["quality"] = evaluate_script(script, topic)
            else:
                script = write_grounded_script(topic, raw.get("hook", ""), job_id=job_id,
                                               execution_context=ExecutionContext.VALIDATION)
            entry["script_quality"] = (script.get("quality") or {}).get("overall")
            entry["hook_score"] = (script.get("quality") or {}).get("hook_score")
            entry["script_source"] = script.get("script_source")
            entry["script"] = script["full_script"]
            (Path(ws.scripts_dir) / f"script_{job_id}.json").write_text(json.dumps(script, indent=2), encoding="utf-8")
            tts = ExpressiveTTSEngine(ffmpeg_path=get_ffmpeg_path(), ffprobe_path=get_ffprobe_path())
            voice, timings, vdur = synthesize_and_probe_narration(tts, script["sections"], ws)
            photos = [] if scene_photos else photos_for(topic, ws, ExecutionContext.VALIDATION)
            entry["photos_used_candidates"] = len(photos)
            out_mp4 = out_root / f"{job_id}.mp4"
            res = render_pop_info(workspace_root=workspace_root, prod_dir=Path(ws.prod_dir), job_id=job_id,
                                  topic=topic.title, category=topic.category, script_data=script,
                                  section_timings=timings, voice_path=voice, duration_sec=round(vdur + 1.6, 2),
                                  output_path=out_mp4, photos=photos, select_photos=scene_photos,
                                  key_entities=topic.key_entities)
            entry.update({"ok": True, "video": str(out_mp4), "quality": res.get("quality"),
                          "quality_report": res.get("quality_report_path"), "layout": res.get("layout"),
                          "fingerprints": res["film"]["fingerprints"], "asset_provenance": res["film"]["asset_provenance"],
                          "timing_sec": res["timing_sec"], "audio_mix": {k: res["audio"].get(k) for k in ("loudness_passes", "measured_mix")},
                          "photo_selection": res.get("photo_selection"), "scene_count": res["film"]["beat_count"]})
            if identity_db:
                entry["identity"] = identity_report(identity_db, job_id, topic, script, res["film"]["fingerprints"])
        except Exception as exc:
            entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:800], "trace": traceback.format_exc()[-1500:]})
        entry["wall_sec"] = round(time.perf_counter() - t0, 1)
        print(json.dumps({k: entry.get(k) for k in ("ok", "script_source", "quality", "error", "wall_sec")}, default=str))
        summary.append(entry)
        (out_root / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0 if all(e.get("ok") for e in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
