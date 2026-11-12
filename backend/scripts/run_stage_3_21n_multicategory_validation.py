"""Stage 3.21N controlled four-category visual diversity and style validation."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.content.creative_production_policy import (
    CategoryDistributionTracker,
    get_creative_production_policy,
)
from backend.app.core.config import settings
from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import FFMPEG_DEFAULT_PATH
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.documentary_storyboard_builder import (
    acquire_isolated_topic_assets,
    build_20_beat_storyboard,
    compose_structured_script,
)
from backend.app.services.animation_service import probe_video

DB_PATH = PROJECT_ROOT / "backend" / "studio.db"
REPORT_JSON = PROJECT_ROOT / "docs" / "STAGE_3_21N_VALIDATION_RESULTS.json"
FULL_RENDER_CATEGORIES = ("Science", "Technology")
UPSTREAM_CATEGORIES = ("Psychology", "Nature and Wildlife")


def _db_table_counts() -> Dict[str, int]:
    tables = (
        "content_ledger",
        "publishing_ledger",
        "publishing_ledger_archive",
        "persistent_jobs",
        "media_assets",
    )
    with sqlite3.connect(str(DB_PATH)) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _extract_contact_sheet(video_path: str, out_png: str) -> str:
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    subprocess.run(
        [
            str(FFMPEG_DEFAULT_PATH),
            "-y",
            "-i",
            video_path,
            "-vf",
            "fps=1/9,scale=360:640,tile=3x2",
            "-frames:v",
            "1",
            out_png,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=30,
    )
    return out_png


def _summarize_workspace_run(production_id: str, production_dir: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    storyboard_path = Path(production_dir) / "storyboards" / f"storyboard_{production_id}.json"
    if not storyboard_path.is_file():
        raise FileNotFoundError(f"Storyboard artifact is missing: {storyboard_path}")
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    return {
        **extra,
        "production_id": production_id,
        "production_dir": production_dir,
        "visual_sourcing_summary": storyboard.get("visual_sourcing_summary", {}),
        "visual_diversity_audit": storyboard.get("visual_diversity_audit", {}),
        "narrative_arc": storyboard.get("narrative_arc", []),
        "beat_count": storyboard.get("beat_count", 0),
        "beats": storyboard.get("beats", []),
    }


def _resume_full_render(category: str, production_id: str) -> Dict[str, Any]:
    production_dir = PROJECT_ROOT / "media" / "productions" / production_id
    manifest_path = production_dir / "production_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Cannot resume without the production manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    video_path = str(manifest.get("canonical_video_path") or "")
    if not video_path or not os.path.isfile(video_path):
        raise FileNotFoundError(f"Cannot resume without the rendered video: {video_path}")

    video_probe = probe_video(video_path)
    if int(video_probe.get("width", 0)) != 1080 or int(video_probe.get("height", 0)) != 1920:
        raise RuntimeError(f"Unexpected resumed video dimensions for {production_id}: {video_probe}")

    contact_sheet = production_dir / "audit" / f"contact_sheet_{production_id}.png"
    if not contact_sheet.is_file():
        _extract_contact_sheet(video_path, str(contact_sheet))
    integrity_path = production_dir / "audit" / f"integrity_report_{production_id}.json"
    if not integrity_path.is_file():
        raise FileNotFoundError(f"Cannot resume without the integrity report: {integrity_path}")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    if not integrity.get("is_valid") or not all(item.get("passed") for item in integrity.get("checks", [])):
        raise RuntimeError(f"Resumed render failed content integrity: {integrity_path}")

    summary = _summarize_workspace_run(
        production_id,
        str(production_dir),
        {
            "mode": "full_render",
            "requested_category": category,
            "duration_sec": video_probe.get("duration"),
            "video_probe": video_probe,
            "video_path": video_path,
            "thumbnail_path": manifest.get("canonical_thumbnail_path"),
            "contact_sheet_path": str(contact_sheet),
            "integrity_report_path": str(integrity_path),
            "publishing_status": "suppressed_validation_execution",
            "resumed_verified_artifacts": True,
        },
    )
    research_path = production_dir / "research" / f"research_{production_id}.json"
    if not research_path.is_file():
        raise FileNotFoundError(f"Research dossier is missing: {research_path}")
    research = json.loads(research_path.read_text(encoding="utf-8"))
    summary["actual_category"] = research.get("category")
    summary["topic_title"] = manifest.get("topic_title")
    visual_summary = summary.get("visual_sourcing_summary", {})
    if not summary["actual_category"]:
        raise RuntimeError(f"Research dossier has no actual category: {research_path}")
    if not summary.get("visual_diversity_audit", {}).get("is_valid"):
        raise RuntimeError(f"Resumed render failed visual diversity: {summary.get('visual_diversity_audit')}")
    if visual_summary.get("reused_source_images") != 0:
        raise RuntimeError(f"Resumed render reused a source photo: {visual_summary}")
    if len([count for count in visual_summary.get("visual_style_distribution", {}).values() if count > 0]) < 2:
        raise RuntimeError(f"Resumed render has no adaptive style variation: {visual_summary}")
    return summary


def _run_upstream_category_validation(
    orchestrator: CrazzyDocsMasterOrchestrator,
    category: str,
    run_id: str,
) -> Dict[str, Any]:
    ranked = orchestrator.topic_engine.discover_and_rank_live_topics(
        filter_duplicates=True,
        preferred_category=category,
    )
    if not ranked:
        raise RuntimeError(f"Discovery yielded zero eligible topics for category '{category}'")

    topic = ranked[0]
    hooks = orchestrator.topic_engine.generate_five_hooks(topic)
    best_hook = max(hooks, key=lambda hook: hook.composite_score)
    orchestrator.ledger.validation_history.record_selection(
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

    workspace = ProductionWorkspace(
        production_id=run_id,
        topic_id=topic.id,
        topic_title=topic.title,
        category=topic.category,
    )
    workspace.initialize_workspace()
    script_data = compose_structured_script(
        topic,
        best_hook.hook_text,
        execution_context=ExecutionContext.VALIDATION,
    )
    acquisition_stats: Dict[str, Any] = {}
    acquired_assets, attribution_manifest, manifest_assets = acquire_isolated_topic_assets(
        topic,
        workspace,
        ExecutionContext.VALIDATION,
        max_assets=8,
        stats_out=acquisition_stats,
    )

    timings: List[Dict[str, Any]] = []
    current_time = 0.0
    category_policy = get_creative_production_policy(ExecutionContext.VALIDATION).get_category(topic.category)
    narrative_arc = category_policy.narrative_arc or ["hook", "context", "mechanism", "revelation"]
    for section_index, section in enumerate(script_data["sections"]):
        word_count = max(8, len(section["text"].split()))
        duration = round(max(6.0, min(14.0, word_count / 2.25)), 2)
        timings.append({
            "id": section["id"],
            "stage": section["stage"],
            "arc_phase": section.get("arc_phase", narrative_arc[min(section_index, len(narrative_arc) - 1)]),
            "text": section["text"],
            "start_sec": round(current_time, 3),
            "end_sec": round(current_time + duration, 3),
            "duration_sec": duration,
        })
        current_time += duration

    voiceover_stub = os.path.join(workspace.audio_dir, f"voiceover_{run_id}.mp3")
    subprocess.run(
        [
            str(FFMPEG_DEFAULT_PATH),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(current_time),
            "-c:a",
            "libmp3lame",
            voiceover_stub,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=20,
    )
    plan, _, subtitles_path = build_20_beat_storyboard(
        topic=topic,
        timings=timings,
        total_voice_dur=current_time,
        voiceover_path=voiceover_stub,
        acquired_assets=acquired_assets,
        prod_ws=workspace,
        script_full_text=script_data["full_script"],
        execution_context=ExecutionContext.VALIDATION,
    )
    summary = dict(plan.metadata.get("visual_sourcing_summary") or {})
    storyboard_path = Path(workspace.storyboards_dir) / f"storyboard_{run_id}.json"
    if not storyboard_path.is_file():
        raise FileNotFoundError(f"Storyboard artifact is missing: {storyboard_path}")
    storyboard_data = json.loads(storyboard_path.read_text(encoding="utf-8"))
    diversity_audit = dict(storyboard_data.get("visual_diversity_audit") or {})
    if not diversity_audit.get("is_valid"):
        raise RuntimeError(f"Visual diversity audit failed for {run_id}: {diversity_audit}")
    if int(plan.metadata.get("distinct_visual_assets", 0)) < 12:
        raise RuntimeError(f"Insufficient distinct visual assets for {run_id}: {diversity_audit}")
    if summary.get("reused_source_images") != 0:
        raise RuntimeError(f"Source-photo reuse detected for {run_id}: {summary}")
    if len([count for count in summary.get("visual_style_distribution", {}).values() if count > 0]) < 2:
        raise RuntimeError(f"Adaptive styles did not produce multiple styles for {run_id}: {summary}")

    CategoryDistributionTracker().record_selection(
        category=topic.category,
        topic_title=topic.title,
        execution_context=ExecutionContext.VALIDATION.value,
        run_id=run_id,
    )
    orchestrator.ledger.validation_history.update_outcome(
        run_id,
        "completed",
        topic_title=topic.title,
        metadata={"category": topic.category, "upstream_only": True, "manifest_path": workspace.manifest_path},
    )
    return _summarize_workspace_run(
        run_id,
        workspace.prod_dir,
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
            "acquisition_stats": acquisition_stats,
            "attribution_count": attribution_manifest.total_assets if attribution_manifest else 0,
            "subtitles_path": subtitles_path,
        },
    )


def main() -> int:
    os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = ExecutionContext.VALIDATION.value
    if settings.PUBLISHING_MODE != "manual_approval":
        raise RuntimeError(f"Validation requires manual_approval publishing mode; got {settings.PUBLISHING_MODE!r}")
    before_db = _db_table_counts()
    policy = get_creative_production_policy(ExecutionContext.VALIDATION)
    orchestrator = CrazzyDocsMasterOrchestrator()
    timestamp = os.environ.get("CRAZZYDOCS_STAGE_321N_TIMESTAMP") or datetime.now().strftime("%Y%m%d_%H%M%S")
    resume_existing = os.environ.get("CRAZZYDOCS_STAGE_321N_RESUME") == "1"
    results: List[Dict[str, Any]] = []

    for index, category in enumerate(FULL_RENDER_CATEGORIES, start=1):
        run_id = f"val_3_21n_{category.lower()}_{timestamp}_{index}"
        if resume_existing and (PROJECT_ROOT / "media" / "productions" / run_id).is_dir():
            print(f"[FULL VALIDATION {index}/2] Reusing verified artifacts for {category} ({run_id})", flush=True)
            results.append(_resume_full_render(category, run_id))
            continue
        print(f"[FULL VALIDATION {index}/2] {category} ({run_id})", flush=True)
        result = orchestrator.execute_production_pipeline(
            job_id=run_id,
            execution_context=ExecutionContext.VALIDATION,
            use_live_pipeline=True,
            preferred_category=category,
        )
        if result.get("publishing_status") != "suppressed_validation_execution":
            raise RuntimeError(f"Publishing was not suppressed for validation run {run_id}: {result.get('publishing_status')}")
        video_path = str(result.get("video_path") or "")
        if not video_path or not os.path.isfile(video_path) or os.path.getsize(video_path) <= 1024:
            raise RuntimeError(f"Full render artifact is missing or empty for {run_id}: {video_path}")
        video_probe = probe_video(video_path)
        if int(video_probe.get("width", 0)) != 1080 or int(video_probe.get("height", 0)) != 1920:
            raise RuntimeError(f"Unexpected rendered dimensions for {run_id}: {video_probe}")
        contact_sheet = _extract_contact_sheet(
            video_path,
            os.path.join(result["production_dir"], "audit", f"contact_sheet_{run_id}.png"),
        )
        if not os.path.isfile(contact_sheet) or os.path.getsize(contact_sheet) <= 1024:
            raise RuntimeError(f"Contact sheet is missing or empty for {run_id}: {contact_sheet}")
        summary = _summarize_workspace_run(
            result["production_id"],
            result["production_dir"],
            {
                "mode": "full_render",
                "requested_category": category,
                "topic_title": result["topic"],
                "duration_sec": result["duration_sec"],
                "video_probe": video_probe,
                "video_path": video_path,
                "thumbnail_path": result["thumbnail_path"],
                "contact_sheet_path": contact_sheet,
                "integrity_report_path": result["integrity_report"],
                "publishing_status": result["publishing_status"],
                "elapsed_sec": result["elapsed_sec"],
            },
        )
        research_path = Path(result["production_dir"]) / "research" / f"research_{run_id}.json"
        if not research_path.is_file():
            raise FileNotFoundError(f"Research dossier is missing: {research_path}")
        research = json.loads(research_path.read_text(encoding="utf-8"))
        summary["actual_category"] = research.get("category")
        if not summary["actual_category"]:
            raise RuntimeError(f"Research dossier has no actual category: {research_path}")
        visual_summary = summary.get("visual_sourcing_summary", {})
        if not summary.get("visual_diversity_audit", {}).get("is_valid"):
            raise RuntimeError(f"Visual diversity audit failed in full render {run_id}: {summary.get('visual_diversity_audit')}")
        if int(summary.get("visual_diversity_audit", {}).get("distinct_assets", 0)) < 12:
            raise RuntimeError(f"Insufficient distinct assets in full render {run_id}: {summary.get('visual_diversity_audit')}")
        if visual_summary.get("reused_source_images") != 0:
            raise RuntimeError(f"Source-photo reuse detected in full render {run_id}: {visual_summary}")
        if len([count for count in visual_summary.get("visual_style_distribution", {}).values() if count > 0]) < 2:
            raise RuntimeError(f"Adaptive styles did not produce multiple styles in {run_id}: {visual_summary}")
        results.append(summary)

    for index, category in enumerate(UPSTREAM_CATEGORIES, start=1):
        run_id = f"val_3_21n_{category.lower().replace(' ', '_')}_{timestamp}_{index + 2}"
        print(f"[UPSTREAM VALIDATION {index}/2] {category} ({run_id})", flush=True)
        results.append(_run_upstream_category_validation(orchestrator, category, run_id))

    after_db = _db_table_counts()
    actual_categories = sorted({str(result.get("actual_category", "")) for result in results})
    payload = {
        "stage": "3.21N",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "supported_categories_count": len(policy.list_categories()),
        "db_counts_before": before_db,
        "db_counts_after": after_db,
        "db_isolated": before_db == after_db,
        "distinct_actual_categories": actual_categories,
        "all_four_categories_distinct": len(actual_categories) == 4,
        "category_distribution_window": CategoryDistributionTracker().get_distribution_counts(window=20),
        "runs": results,
    }
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[DONE] Validation results: {REPORT_JSON}", flush=True)
    print(f"DB counts unchanged: {payload['db_isolated']}", flush=True)
    print(f"Actual categories: {actual_categories}", flush=True)

    if not payload["db_isolated"]:
        raise RuntimeError(f"Validation mutated protected database table counts: {before_db} -> {after_db}")
    if not payload["all_four_categories_distinct"]:
        raise RuntimeError(f"Validation categories were not distinct: {actual_categories}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
