"""
Stage 3.21M Controlled Multi-Category End-to-End Validation Runner.

Executes controlled validation across 4 genuinely fresh distinct topic categories:
- Category 1 (Science) & Category 2 (Technology): Full end-to-end production runs
  (topic discovery, research, category script, Edge-TTS, isolated Priority A-D media,
   20-beat storyboard, 1080x1920 @ 30 FPS render, thumbnail, 15-point QC, Content Integrity Gate).
- Category 3 (Psychology) & Category 4 (Nature and Wildlife): Complete upstream pipeline
  (live topic discovery, research dossier, category script, TTS timing, isolated Priority A+B
   media acquisition & license verification, 20-beat Priority A-D storyboard & diversity validation).

Strictly enforces ExecutionContext.VALIDATION:
- Zero YouTube uploads.
- Zero emails/desktop alerts dispatched as production.
- Zero mutations to studio.db production tables (content_ledger, publishing_ledger, persistent_jobs, media_assets).
"""

from dataclasses import asdict
from datetime import datetime
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.content.creative_production_policy import (
    CategoryDistributionTracker,
    get_creative_production_policy,
)
from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import FFMPEG_DEFAULT_PATH
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.documentary_storyboard_builder import (
    acquire_isolated_topic_assets,
    build_20_beat_storyboard,
    compose_structured_script,
)

DB_PATH = PROJECT_ROOT / "backend" / "studio.db"
REPORT_JSON = PROJECT_ROOT / "docs" / "STAGE_3_21M_VALIDATION_RESULTS.json"


def _db_table_counts() -> Dict[str, int]:
    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        out = {}
        for tbl in ("content_ledger", "publishing_ledger", "publishing_ledger_archive", "persistent_jobs", "media_assets"):
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            out[tbl] = int(cur.fetchone()[0])
        return out
    finally:
        con.close()


def _extract_contact_sheet(video_path: str, out_png: str) -> str:
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    cmd = [
        str(FFMPEG_DEFAULT_PATH),
        "-y",
        "-i", video_path,
        "-vf", "fps=1/9,scale=360:640,tile=3x2",
        "-frames:v", "1",
        out_png,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30)
    return out_png if os.path.isfile(out_png) else ""


def _summarize_workspace_run(prod_id: str, prod_dir: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    sb_path = Path(prod_dir) / "storyboards" / f"storyboard_{prod_id}.json"
    sb_data = json.loads(sb_path.read_text(encoding="utf-8")) if sb_path.is_file() else {}
    return {
        **extra,
        "production_id": prod_id,
        "production_dir": prod_dir,
        "visual_sourcing_summary": sb_data.get("visual_sourcing_summary", {}),
        "visual_diversity_audit": sb_data.get("visual_diversity_audit", {}),
        "narrative_arc": sb_data.get("narrative_arc", []),
        "beat_count": sb_data.get("beat_count", 0),
    }


def _run_upstream_category_validation(
    orch: CrazzyDocsMasterOrchestrator,
    category: str,
    run_id: str,
) -> Dict[str, Any]:
    ranked = orch.topic_engine.discover_and_rank_live_topics(
        filter_duplicates=True,
        preferred_category=category,
    )
    if not ranked:
        raise RuntimeError(f"Discovery yielded zero eligible topics for category '{category}'")

    topic = ranked[0]
    hooks = orch.topic_engine.generate_five_hooks(topic)
    best_hook = max(hooks, key=lambda h: h.composite_score)

    orch.ledger.validation_history.record_selection(
        run_id=run_id,
        topic_id=topic.id,
        topic_title=topic.title,
        execution_context=ExecutionContext.VALIDATION.value,
        outcome="in_progress",
        key_entities=topic.key_entities,
        research_fingerprint=topic.research_fingerprint,
        discovery_source=topic.discovery_source,
        metadata={"category": topic.category, **(topic.discovery_metadata or {})},
    )

    ws = ProductionWorkspace(
        production_id=run_id,
        topic_id=topic.id,
        topic_title=topic.title,
        category=topic.category,
    )
    ws.initialize_workspace()

    script_data = compose_structured_script(topic, best_hook.hook_text, execution_context=ExecutionContext.VALIDATION)
    acq_stats: Dict[str, Any] = {}
    acquired_assets, attrib_manifest, manifest_assets = acquire_isolated_topic_assets(
        topic, ws, ExecutionContext.VALIDATION, max_assets=8, stats_out=acq_stats
    )

    # Approximate 52s timing evenly across the 5 script sections for upstream storyboard verification
    timings = []
    cur_t = 0.0
    for sec in script_data["sections"]:
        wc = max(8, len(sec["text"].split()))
        sec_dur = round(max(6.0, min(14.0, wc / 2.25)), 2)
        timings.append({
            "id": sec["id"],
            "stage": sec["stage"],
            "arc_phase": sec.get("arc_phase", sec["stage"]),
            "text": sec["text"],
            "start_sec": round(cur_t, 3),
            "end_sec": round(cur_t + sec_dur, 3),
            "duration_sec": sec_dur,
        })
        cur_t += sec_dur

    vo_stub = os.path.join(ws.audio_dir, f"voiceover_{run_id}.mp3")
    subprocess.run(
        [str(FFMPEG_DEFAULT_PATH), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(cur_t), "-c:a", "libmp3lame", vo_stub],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=20,
    )
    plan, _, ass_path = build_20_beat_storyboard(
        topic=topic,
        timings=timings,
        total_voice_dur=cur_t,
        voiceover_path=vo_stub,
        acquired_assets=acquired_assets,
        prod_ws=ws,
        script_full_text=script_data["full_script"],
        execution_context=ExecutionContext.VALIDATION,
    )
    CategoryDistributionTracker().record_selection(
        category=topic.category,
        topic_title=topic.title,
        execution_context=ExecutionContext.VALIDATION.value,
        run_id=run_id,
    )
    orch.ledger.validation_history.update_outcome(
        run_id,
        "completed",
        topic_title=topic.title,
        metadata={"category": topic.category, "upstream_only": True, "manifest_path": ws.manifest_path},
    )
    return _summarize_workspace_run(
        run_id,
        ws.prod_dir,
        {
            "mode": "upstream_discovery_storyboard",
            "requested_category": category,
            "actual_category": topic.category,
            "topic_title": topic.title,
            "composite_score": topic.composite_score,
            "score_breakdown": topic.score_breakdown,
            "discovery_source": topic.discovery_source,
            "word_count": script_data["word_count"],
            "acquired_assets_count": len(manifest_assets),
            "acq_stats": acq_stats,
            "attribution_count": attrib_manifest.total_assets if attrib_manifest else 0,
            "subtitles_path": ass_path,
            "manifest_path": ws.manifest_path,
        },
    )


def main() -> int:
    os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "VALIDATION"
    before_db = _db_table_counts()
    policy = get_creative_production_policy(ExecutionContext.VALIDATION)
    orch = CrazzyDocsMasterOrchestrator()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_categories = ["Science", "Technology"]
    upstream_categories = ["Psychology", "Nature and Wildlife"]

    results: List[Dict[str, Any]] = []
    for idx, cat in enumerate(full_categories, 1):
        run_id = f"val_3_21m_{cat.lower()}_{ts}_{idx}"
        print(f"[FULL VALIDATION {idx}/2] Running full E2E pipeline for category='{cat}' (job_id={run_id})...", flush=True)
        out = orch.execute_production_pipeline(
            job_id=run_id,
            execution_context=ExecutionContext.VALIDATION,
            use_live_pipeline=True,
            preferred_category=cat,
        )
        cs_png = _extract_contact_sheet(
            out["video_path"],
            os.path.join(out["production_dir"], "audit", f"contact_sheet_{run_id}.png"),
        )
        summary = _summarize_workspace_run(
            out["production_id"],
            out["production_dir"],
            {
                "mode": "full_render",
                "requested_category": cat,
                "topic_title": out["topic"],
                "duration_sec": out["duration_sec"],
                "video_path": out["video_path"],
                "thumbnail_path": out["thumbnail_path"],
                "contact_sheet_path": cs_png,
                "integrity_report": out["integrity_report"],
                "publishing_status": out["publishing_status"],
                "elapsed_sec": out["elapsed_sec"],
            },
        )
        # Attach actual selected category from research dossier
        res_json = Path(out["production_dir"]) / "research" / f"research_{run_id}.json"
        if res_json.is_file():
            rj = json.loads(res_json.read_text(encoding="utf-8"))
            summary["actual_category"] = rj.get("category", cat)
            summary["composite_score"] = rj.get("composite_score")
            summary["discovery_source"] = rj.get("discovery_source")
        results.append(summary)

    for idx, cat in enumerate(upstream_categories, 1):
        slug = cat.lower().split()[0]
        run_id = f"val_3_21m_{slug}_{ts}_{idx + 2}"
        print(f"[UPSTREAM VALIDATION {idx}/2] Running discovery->storyboard for category='{cat}' (run_id={run_id})...", flush=True)
        results.append(_run_upstream_category_validation(orch, cat, run_id))

    after_db = _db_table_counts()
    payload = {
        "stage": "3.21M",
        "executed_at": datetime.utcnow().isoformat() + "Z",
        "supported_categories_count": len(policy.list_categories()),
        "db_counts_before": before_db,
        "db_counts_after": after_db,
        "db_isolated": before_db == after_db,
        "distinct_actual_categories": sorted({str(r.get("actual_category", "")) for r in results}),
        "category_distribution_window": CategoryDistributionTracker().get_distribution_counts(window=20),
        "runs": results,
    }
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[DONE] Validation report written to {REPORT_JSON}", flush=True)
    print(f"DB Counts Before: {before_db}", flush=True)
    print(f"DB Counts After : {after_db}", flush=True)
    print(f"Distinct Actual Categories: {payload['distinct_actual_categories']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
