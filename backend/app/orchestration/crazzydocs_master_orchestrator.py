"""
CrazzyDocs Master Production Pipeline Orchestrator.
Phase 5, Stage 3.8 Production Standard.

Executes the unified 14-stage autonomous production lifecycle:
1. Topic Discovery (Curiosity Topic Engine)
2. Research and Verification (Factual gate check >= 8.0)
3. Topic Selection (Highest-ranked unique topic from TopicLedger)
4. Story Development (Winning hook formulation + 6-stage narrative)
5. Creative Direction (Archetype, color palette, camera choreography)
6. Media Acquisition (Authentic public domain assets + attribution manifest)
7. Storyboard (Dynamic visual timeline choreography)
8. Expressive Narration (Prosody modulation + ambient audio bed)
9. Video Rendering (1080x1920 @ 30 FPS fast renderer + 2.0s branded outro)
10. Thumbnail Generation (CreativeThumbnailEngine 8-metric selection)
11. Quality Control (Duration, aspect ratio, audio/video synchronization)
12. Production Database (Job persistence and checkpointing)
13. Email and Desktop Notification (Formatted alert dispatch)
14. YouTube Upload Queue (Decoupled queueing with strict safety gate)
"""

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from backend.app.core.config import settings
from backend.app.core.execution_context import (
    ExecutionContext,
    require_production_context,
    get_current_execution_context,
)
from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine, ScoredTopic
from backend.app.content.topic_ledger import (
    TopicLedger,
    ContentRecord,
    compute_script_fingerprint,
    compute_storyboard_fingerprint,
)
from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.content.visual_diversity_policy import compute_asset_sha256
from backend.app.orchestration.production_workspace import ProductionWorkspace, compute_file_sha256
from backend.app.services.expressive_tts_engine import ExpressiveTTSEngine, NarrationSegment
from backend.app.services.production_observability import production_observability
from backend.app.services.qc_service import QualityControlEngine
from backend.app.rendering.engine import HybridVisualEngine
from backend.app.rendering.documentary_storyboard_builder import (
    acquire_isolated_topic_assets,
    build_20_beat_storyboard,
    compose_structured_script,
    synthesize_and_probe_narration,
)
from backend.app.rendering.engines.creative_thumbnail_engine import CreativeThumbnailEngine
from backend.app.rendering.engines.vesna_fast_renderer import VisualBeatConfig
from backend.app.services.notification_service import NotificationManager, notification_service
from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    MetadataGenerator,
    YouTubeMetadata,
    PublishingDatabase,
    PublicationRecord,
    compute_file_sha256,
    youtube_publishing_engine,
)
from backend.app.database.db import SessionLocal, JobModel

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
VIDEO_DIR = os.path.join(WORKSPACE, "media", "video")
THUMB_DIR = os.path.join(WORKSPACE, "media", "thumbnails")
AUDIT_DIR = os.path.join(WORKSPACE, "media", "audit")
FFMPEG_EXE = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffmpeg.exe")

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)


class CrazzyDocsMasterOrchestrator:
    """End-to-end master orchestrator coordinating autonomous production and publishing."""

    def __init__(
        self,
        topic_engine: Optional[CuriosityTopicEngine] = None,
        ledger: Optional[TopicLedger] = None,
        tts_engine: Optional[ExpressiveTTSEngine] = None,
        thumb_engine: Optional[CreativeThumbnailEngine] = None,
        notifier: Optional[NotificationManager] = None,
        publishing_engine: Optional[YouTubePublishingEngine] = None,
        integrity_gate: Optional[ContentIntegrityGate] = None,
    ):
        self.ledger = ledger or TopicLedger()
        self.topic_engine = topic_engine or CuriosityTopicEngine(ledger=self.ledger)
        self.tts_engine = tts_engine or ExpressiveTTSEngine()
        self.thumb_engine = thumb_engine or CreativeThumbnailEngine()
        self.notifier = notifier or notification_service
        self.publishing_engine = publishing_engine or youtube_publishing_engine
        self.integrity_gate = integrity_gate or ContentIntegrityGate(workspace_root=WORKSPACE)

    def _synthesize_topic_script(self, topic: ScoredTopic, hook_text: str) -> str:
        """Dynamically generates a 50-58 second factual script for the topic."""
        s1 = hook_text.strip()
        if not s1.endswith((".", "!", "?")):
            s1 += "."

        summary = topic.summary.strip()
        entities = ", ".join(topic.key_entities[:4])
        closing = "Follow CrazzyDocs for more unbelievable true stories."

        words = (s1 + " " + summary + " " + closing).split()
        if len(words) < 115:
            context = f"Documented historical records regarding {entities} demonstrate how extraordinary circumstances unfolded."
            script = f"{s1} {context} {summary} {closing}"
        else:
            script = f"{s1} {summary} {closing}"
        return script

    def _build_topic_visual_beats(self, topic: ScoredTopic, hook: str, duration: float = 54.0) -> List[VisualBeatConfig]:
        """Dynamically builds 5 distinct visual beats corresponding to 5 narrative stages."""
        tag = (topic.category[:15]).upper()
        title_short = topic.title[:45]
        summary_words = topic.summary.split()

        content_dur = max(48.0, duration - 2.0)
        t1 = round(content_dur * 0.10, 2)
        t2 = round(content_dur * 0.30, 2)
        t3 = round(content_dur * 0.65, 2)
        t4 = round(content_dur * 0.88, 2)
        t5 = round(content_dur, 2)

        beats = [
            VisualBeatConfig(
                id=f"{topic.id}_beat_1",
                image_filename="",
                start_sec=0.0,
                end_sec=t1,
                top_tag=tag,
                editorial_text=hook[:60],
                accent_color=(255, 75, 75),
                motion_type="zoom_in",
                sub_label=title_short,
            ),
            VisualBeatConfig(
                id=f"{topic.id}_beat_2",
                image_filename="",
                start_sec=t1,
                end_sec=t2,
                top_tag=tag,
                editorial_text=" ".join(summary_words[:8]),
                accent_color=(75, 180, 255),
                motion_type="pan_right",
                sub_label="HISTORICAL CONTEXT",
            ),
            VisualBeatConfig(
                id=f"{topic.id}_beat_3",
                image_filename="",
                start_sec=t2,
                end_sec=t3,
                top_tag=tag,
                editorial_text=" ".join(summary_words[8:18]) if len(summary_words) > 8 else "THE CRITICAL TURNING POINT",
                accent_color=(255, 200, 50),
                motion_type="zoom_out",
                sub_label="THE EVENT",
            ),
            VisualBeatConfig(
                id=f"{topic.id}_beat_4",
                image_filename="",
                start_sec=t3,
                end_sec=t4,
                top_tag=tag,
                editorial_text=" ".join(summary_words[18:28]) if len(summary_words) > 18 else "THE UNEXPECTED OUTCOME",
                accent_color=(100, 255, 150),
                motion_type="pan_left",
                sub_label="AFTERMATH & EVIDENCE",
            ),
            VisualBeatConfig(
                id=f"{topic.id}_beat_5",
                image_filename="",
                start_sec=t4,
                end_sec=t5,
                top_tag=tag,
                editorial_text="SUBSCRIBE TO CRAZZYDOCS",
                accent_color=(255, 255, 255),
                motion_type="zoom_in",
                sub_label="UNBELIEVABLE TRUE STORIES",
            ),
        ]
        return beats

    def _update_checkpoint(self, job_id: str, stage: str, progress: float, stage_data: Dict):
        """Persists incremental stage checkpoint to SQLite JobModel."""
        try:
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if job:
                    job.current_stage = stage
                    job.progress_pct = progress
                    job.heartbeat_at = datetime.utcnow()
                    cur_data = {}
                    if job.checkpoint_data_json:
                        try:
                            cur_data = json.loads(job.checkpoint_data_json)
                        except Exception:
                            pass
                    cur_data.update(stage_data)
                    job.checkpoint_data_json = json.dumps(cur_data)
                    db_session.commit()
        except Exception as e:
            logger.warning("[CHECKPOINT] Failed to persist stage %s checkpoint: %s", stage, e)

    def execute_production_pipeline(
        self,
        job_id: Optional[str] = None,
        force_topic_id: Optional[str] = None,
        execution_context: Optional[ExecutionContext] = None,
        use_live_pipeline: Optional[bool] = None,
    ) -> Dict:
        """
        Executes Stages 1 through 14: Discovers topic, acquires isolated assets, synthesizes
        expressive TTS narration, renders multi-beat 1080x1920 video + branded outro, generates
        thumbnail, enforces 15-point QC and Content Integrity Gate, and strictly isolates non-PRODUCTION runs.
        """
        if execution_context is None:
            execution_context = get_current_execution_context()
        elif isinstance(execution_context, str):
            execution_context = ExecutionContext.from_string(execution_context)

        # PRODUCTION and VALIDATION run the full real live pipeline unless explicitly overridden.
        # TEST context keeps lightweight unit-test defaults unless use_live_pipeline=True is passed.
        is_full_live = (
            use_live_pipeline
            if use_live_pipeline is not None
            else execution_context in (ExecutionContext.PRODUCTION, ExecutionContext.VALIDATION)
        )
        persist_to_db = execution_context != ExecutionContext.VALIDATION

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_id = job_id or (
            f"val_3_21j_{now_str}" if execution_context == ExecutionContext.VALIDATION else f"job_{now_str}"
        )
        t_start = time.perf_counter()
        logger.info(
            "=== STARTING AUTONOMOUS PRODUCTION RUN: %s (Context: %s, LivePipeline: %s) ===",
            job_id,
            execution_context.value,
            is_full_live,
        )

        obs_stage = "topic_discovery"
        production_observability.start_production(
            job_id,
            topic="Discovering topic...",
            execution_context=execution_context.value,
        )
        production_observability.start_stage(job_id, "topic_discovery", "Discovering high-curiosity topic...", progress=15.0)

        # Inspect existing checkpoint if resuming (skip studio.db mutations in VALIDATION context)
        existing_cp = {}
        if persist_to_db:
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if not job:
                    job = JobModel(
                        id=job_id,
                        date_str=datetime.now().strftime("%Y-%m-%d"),
                        status="RUNNING",
                        current_stage="TOPIC_DISCOVERY",
                        progress_pct=5.0,
                        actual_start_at=datetime.utcnow(),
                    )
                    db_session.add(job)
                    db_session.commit()
                else:
                    if job.checkpoint_data_json:
                        try:
                            existing_cp = json.loads(job.checkpoint_data_json)
                        except Exception:
                            pass
                    job.status = "RUNNING"
                    job.heartbeat_at = datetime.utcnow()
                    db_session.commit()

        try:
            # -------------------------------------------------------------
            # Stage 1 & 2 & 3: Topic Discovery, Research & Selection
            # -------------------------------------------------------------
            if existing_cp.get("topic_title") and existing_cp.get("hook_text"):
                logger.info("[CHECKPOINT RECOVERY] Resuming with saved topic: '%s'", existing_cp["topic_title"])
                selected_topic = ScoredTopic(
                    id=f"topic_{job_id}",
                    title=existing_cp["topic_title"],
                    category=existing_cp.get("category", "history"),
                    category_code="H",
                    summary=existing_cp.get("topic_summary", existing_cp["topic_title"]),
                    curiosity=9.5,
                    surprise=9.0,
                    storytelling=9.2,
                    visual_potential=9.4,
                    emotional_connection=9.0,
                    factual_reliability=9.5,
                    composite_score=9.3,
                    sources=existing_cp.get("sources", ["https://en.wikipedia.org/wiki/Tsutomu_Yamaguchi"]),
                    key_entities=existing_cp.get("key_entities", []),
                )
                from backend.app.content.curiosity_topic_engine import HookCandidate
                best_hook = HookCandidate(
                    approach="Contrarian Tension",
                    hook_text=existing_cp["hook_text"],
                    immediate_comprehension=9.5,
                    curiosity=9.8,
                    surprise=9.2,
                    credibility=9.6,
                    visual_potential=9.4,
                    story_connection=9.5,
                    composite_score=9.8,
                )
            else:
                logger.info("[STAGE 1-3] Discovering, researching & scoring topic...")
                # Duplicate & cooldown gates are NEVER bypassed in PRODUCTION or VALIDATION
                if execution_context in (ExecutionContext.PRODUCTION, ExecutionContext.VALIDATION):
                    filter_dups = True
                else:
                    filter_dups = (force_topic_id is None)

                if is_full_live:
                    ranked = self.topic_engine.discover_and_rank_live_topics(filter_duplicates=filter_dups)
                else:
                    ranked = self.topic_engine.discover_and_rank_topics(filter_duplicates=filter_dups)

                if not ranked:
                    raise RuntimeError("No eligible candidate topics found passing duplicate and factual gates.")

                selected_topic: Optional[ScoredTopic] = None
                if force_topic_id:
                    for cand in ranked:
                        if cand.id == force_topic_id:
                            selected_topic = cand
                            break
                    if selected_topic is None:
                        if execution_context in (ExecutionContext.PRODUCTION, ExecutionContext.VALIDATION):
                            raise RuntimeError(
                                f"Forced topic '{force_topic_id}' failed duplicate/cooldown or availability checks; "
                                "duplicate bypass is strictly prohibited."
                            )
                        selected_topic = ranked[0]
                else:
                    selected_topic = ranked[0]

                logger.info("Selected topic: '%s' (Composite Score: %.2f)", selected_topic.title, selected_topic.composite_score)

                # Immediately record selection in isolated validation history when not in PRODUCTION
                val_hist = getattr(self.ledger, "validation_history", None)
                if execution_context != ExecutionContext.PRODUCTION and val_hist is not None:
                    val_hist.record_selection(
                        run_id=job_id,
                        topic_id=getattr(selected_topic, "id", f"topic_{job_id}"),
                        topic_title=selected_topic.title,
                        execution_context=execution_context.value,
                        outcome="selected",
                        key_entities=getattr(selected_topic, "key_entities", []),
                        research_fingerprint=getattr(selected_topic, "research_fingerprint", ""),
                        discovery_source=getattr(selected_topic, "discovery_source", ""),
                        metadata={
                            "category": selected_topic.category,
                            **(getattr(selected_topic, "discovery_metadata", {}) or {}),
                        },
                    )

                # -------------------------------------------------------------
                # Stage 4: Story Development & Hook Formulation
                # -------------------------------------------------------------
                logger.info("[STAGE 4] Developing story structure and hook directions...")
                hooks = self.topic_engine.generate_five_hooks(selected_topic)
                best_hook = max(hooks, key=lambda h: h.composite_score)
                logger.info("Winning hook (%s): '%s'", best_hook.approach, best_hook.hook_text)

                if execution_context != ExecutionContext.PRODUCTION and val_hist is not None:
                    val_hist.update_outcome(
                        job_id,
                        "in_progress",
                        topic_title=selected_topic.title,
                        metadata={"opening_hook": best_hook.hook_text},
                    )

                if persist_to_db:
                    self._update_checkpoint(job_id, "STORY_DEVELOPMENT", 30.0, {
                        "topic_title": selected_topic.title,
                        "topic_summary": selected_topic.summary,
                        "hook_text": best_hook.hook_text,
                        "category": selected_topic.category,
                        "key_entities": selected_topic.key_entities,
                    })

            # -------------------------------------------------------------
            # Stage 5 & 6 & 7: Creative Direction, Media & Storyboard
            # -------------------------------------------------------------
            production_observability.update_topic(job_id, selected_topic.title)
            production_observability.complete_stage(job_id, "topic_discovery", f"Selected '{selected_topic.title}'")
            obs_stage = "research"
            production_observability.start_stage(job_id, "research", f"Verifying factual dossier for '{selected_topic.title}'...", progress=25.0)

            logger.info("[STAGE 5-7] Setting creative direction and media choreography...")
            prod_ws = ProductionWorkspace(
                production_id=job_id,
                topic_id=getattr(selected_topic, "id", f"topic_{job_id}"),
                topic_title=selected_topic.title,
                category=selected_topic.category,
            )
            prod_ws.initialize_workspace()
            production_observability.bind_event_log_path(
                job_id, os.path.join(prod_ws.reports_dir, f"progress_events_{job_id}.jsonl")
            )

            archetype = self.thumb_engine.detect_archetype(
                selected_topic.category, selected_topic.title, selected_topic.summary
            )

            thumb_assets: List[str] = []
            manifest_assets: List[Dict] = []
            visual_beats: List[VisualBeatConfig] = []
            storyboard_beats: List[Dict] = []
            plan = None
            script_data: Optional[Dict] = None
            section_timings: List[Dict] = []
            rendered_engine_name = "CleanSlateRenderer"
            attrib_manifest = None
            acq_stats: Dict = {"unique_images": 0, "failed": 0, "duplicates_skipped": 0}

            if is_full_live:
                # Save research dossier into workspace
                research_dossier_path = os.path.join(prod_ws.research_dir, f"research_{job_id}.json")
                with open(research_dossier_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "topic_id": selected_topic.id,
                        "title": selected_topic.title,
                        "category": selected_topic.category,
                        "discovery_source": getattr(selected_topic, "discovery_source", "live"),
                        "summary": selected_topic.summary,
                        "extended_extract": getattr(selected_topic, "extended_extract", ""),
                        "factual_bullets": getattr(selected_topic, "factual_bullets", []),
                        "coordinates": getattr(selected_topic, "coordinates", None),
                        "sources": selected_topic.sources,
                        "key_entities": selected_topic.key_entities,
                        "composite_score": selected_topic.composite_score,
                        "winning_hook": best_hook.hook_text,
                    }, f, indent=2)
                prod_ws.register_artifact("STAGE_02_RESEARCH", "research", research_dossier_path, copy_into_workspace=False)
                production_observability.complete_stage(job_id, "research", "Factual research dossier persisted")

                # Structured 5-stage factual script
                obs_stage = "script_generation"
                production_observability.start_stage(job_id, "script_generation", "Drafting 5-stage documentary script...", progress=30.0)
                script_data = compose_structured_script(selected_topic, best_hook.hook_text)
                narration_text = script_data["full_script"]
                script_path = os.path.join(prod_ws.scripts_dir, f"script_{job_id}.json")
                with open(script_path, "w", encoding="utf-8") as f:
                    json.dump(script_data, f, indent=2)
                prod_ws.register_artifact("STAGE_04_SCRIPT", "script", script_path, copy_into_workspace=False)
                production_observability.complete_stage(job_id, "script_generation", f"Script finalized ({script_data.get('word_count', len(narration_text.split()))} words)")

                # Real Edge-TTS voiceover synthesis + ffprobe timing
                obs_stage = "narration_generation"
                production_observability.start_stage(job_id, "narration_generation", "Synthesizing expressive Neural TTS voiceover...", progress=25.0)
                voice_path, section_timings, voice_dur = synthesize_and_probe_narration(
                    self.tts_engine, script_data["sections"], prod_ws
                )
                dur = round(voice_dur + 2.0, 2)  # Main narrative + 2.0s branded outro
                production_observability.complete_stage(job_id, "narration_generation", f"Narration synthesized ({voice_dur:.2f}s)")

                # Acquire isolated openly licensed assets into workspace
                obs_stage = "asset_acquisition"
                production_observability.start_stage(job_id, "asset_acquisition", "Fetching verified Wikimedia/Openverse images...", progress=20.0)
                acquired_assets, attrib_manifest, manifest_assets = acquire_isolated_topic_assets(
                    selected_topic, prod_ws, execution_context, max_assets=10, stats_out=acq_stats
                )
                thumb_assets = [
                    a.local_path for a in acquired_assets if a.local_path and os.path.isfile(a.local_path)
                ]
                for ma in manifest_assets:
                    if ma.get("path") and os.path.isfile(ma["path"]):
                        prod_ws.register_artifact(
                            "STAGE_06_MEDIA",
                            "acquired_asset",
                            ma["path"],
                            copy_into_workspace=False,
                            asset_id=ma.get("asset_id"),
                            source_url=ma.get("source_url"),
                            creator=ma.get("creator"),
                            license=ma.get("license"),
                            license_url=ma.get("license_url"),
                            width=ma.get("width"),
                            height=ma.get("height"),
                            format=ma.get("format"),
                        )

                if attrib_manifest:
                    attrib_json_path = os.path.join(prod_ws.metadata_dir, f"attribution_manifest_{job_id}.json")
                    with open(attrib_json_path, "w", encoding="utf-8") as f:
                        json.dump(asdict(attrib_manifest), f, indent=2)
                    prod_ws.register_artifact("STAGE_06_ATTRIBUTION", "metadata", attrib_json_path, copy_into_workspace=False)

                unique_img_count = len({ma.get("sha256") for ma in manifest_assets if ma.get("sha256")}) or len(thumb_assets)
                production_observability.update_assets(
                    job_id,
                    unique_images=unique_img_count,
                    failed_acquisitions=int(acq_stats.get("failed", 0)),
                    status="ACQUIRED",
                )
                production_observability.complete_stage(
                    job_id,
                    "asset_acquisition",
                    f"Acquired {unique_img_count} unique images ({acq_stats.get('failed', 0)} failed)",
                )

                # Build 20-beat multi-modal storyboard + generated images + 30 FPS explainer animations
                obs_stage = "storyboard_generation"
                production_observability.start_stage(job_id, "storyboard_generation", "Allocating multi-modal 20-beat visual storyboard...", progress=15.0)
                production_observability.start_stage(job_id, "image_generation", "Generating bespoke analytical illustrations...", progress=10.0)
                production_observability.start_stage(job_id, "animation_generation", "Rendering script-grounded 30 FPS explainer animations...", progress=10.0)

                def _sb_progress_cb(ev: Dict) -> None:
                    phase = ev.get("phase")
                    beat_i = int(ev.get("beat_index", 0))
                    tot_b = max(1, int(ev.get("total_beats", 20)))
                    pct = round((beat_i / tot_b) * 100.0, 1)
                    if phase == "image_generation":
                        gen_c = int(ev.get("generated_images", 0))
                        production_observability.update_stage(
                            job_id,
                            "image_generation",
                            status="running",
                            progress=pct,
                            operation=f"Generated illustration #{gen_c} (beat {beat_i}/{tot_b})",
                        )
                        production_observability.update_assets(job_id, generated_images=gen_c, status="GENERATING")
                    elif phase == "animation_generation":
                        anim_c = int(ev.get("animations_generated", 0))
                        vtype = ev.get("visual_type", "animation")
                        production_observability.update_stage(
                            job_id,
                            "animation_generation",
                            status="running",
                            progress=pct,
                            operation=f"Rendered {vtype} #{anim_c} @ 30 FPS (beat {beat_i}/{tot_b})",
                        )
                        production_observability.update_assets(job_id, animations=anim_c, status="ANIMATING")

                plan, visual_beats, ass_path = build_20_beat_storyboard(
                    topic=selected_topic,
                    timings=section_timings,
                    total_voice_dur=voice_dur,
                    voiceover_path=voice_path,
                    acquired_assets=acquired_assets,
                    prod_ws=prod_ws,
                    script_full_text=narration_text,
                    progress_cb=_sb_progress_cb,
                )
                storyboard_beats = list((plan.metadata or {}).get("storyboard_beats", []))
                sb_meta = plan.metadata or {}
                total_reused_slots = max(0, len([b for b in storyboard_beats if b.get("visual_type") in ("historical_photo", "documentary_image")]) - len({b.get("asset_hash") for b in storyboard_beats if b.get("visual_type") in ("historical_photo", "documentary_image")}))
                production_observability.update_assets(
                    job_id,
                    unique_images=int(sb_meta.get("unique_images", unique_img_count)),
                    generated_images=len([b for b in storyboard_beats if b.get("visual_type") == "generated_image"]),
                    animations=int(sb_meta.get("unique_animations", 0)),
                    reused_images=total_reused_slots,
                    status="COMPLETE",
                )
                production_observability.complete_stage(job_id, "image_generation", "Analytical illustrations saved in assets/generated/")
                production_observability.complete_stage(job_id, "animation_generation", f"Rendered {sb_meta.get('unique_animations', 0)} 30 FPS explainer animations")
                production_observability.complete_stage(job_id, "storyboard_generation", f"20-beat storyboard verified ({sb_meta.get('distinct_visual_assets', 0)} distinct visual assets)")

                timeline_json_path = os.path.join(prod_ws.visual_timeline_dir, f"storyboard_{job_id}.json")
                if os.path.isfile(timeline_json_path):
                    prod_ws.register_artifact("STAGE_07_STORYBOARD", "visual_timeline", timeline_json_path, copy_into_workspace=False)

                # Persist audit/asset_inventory_<id>.json
                asset_inv_path = os.path.join(prod_ws.audit_dir, f"asset_inventory_{job_id}.json")
                with open(asset_inv_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "production_id": job_id,
                        "topic": selected_topic.title,
                        "summary": prod_ws.load_manifest().get("asset_summary", {}),
                        "storyboard_diversity": {
                            "beat_count": len(storyboard_beats),
                            "distinct_visual_assets": sb_meta.get("distinct_visual_assets", 0),
                            "max_image_reuse_observed": sb_meta.get("max_image_reuse_observed", 0),
                        },
                        "acquired_assets": manifest_assets,
                        "beats": storyboard_beats,
                    }, f, indent=2)
                prod_ws.register_artifact("STAGE_07_ASSET_INVENTORY", "audit", asset_inv_path, copy_into_workspace=False)
                rendered_engine_name = "HybridVisualEngine"
            else:
                narration_text = self._synthesize_topic_script(selected_topic, best_hook.hook_text)
                dur = max(50.5, min(58.5, round(len(narration_text.split()) / 2.3, 2)))
                visual_beats = self._build_topic_visual_beats(selected_topic, best_hook.hook_text, dur)
                voice_path = os.path.join(prod_ws.audio_dir, f"voiceover_{job_id}.mp3")
                if not os.path.exists(voice_path):
                    subprocess.run([
                        FFMPEG_EXE, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(dur),
                        "-c:a", "libmp3lame", "-b:a", "192k", voice_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                production_observability.complete_stage(job_id, "research", "Unit-test factual dossier validated")
                production_observability.complete_stage(job_id, "script_generation", "Unit-test script prepared")
                production_observability.complete_stage(job_id, "asset_acquisition", "Unit-test asset preparation skipped")
                production_observability.complete_stage(job_id, "image_generation", "Unit-test image step skipped")
                production_observability.complete_stage(job_id, "animation_generation", "Unit-test animation step skipped")
                production_observability.complete_stage(job_id, "storyboard_generation", f"{len(visual_beats)}-beat test timeline prepared")
                production_observability.complete_stage(job_id, "narration_generation", "Unit-test audio track ready")

            script_fp = compute_script_fingerprint(narration_text)
            storyboard_fp = compute_storyboard_fingerprint(visual_beats)
            prod_ws.script_fingerprint = script_fp
            prod_ws.storyboard_fingerprint = storyboard_fp

            # -------------------------------------------------------------
            # Stage 8: Expressive Narration Synthesis
            # -------------------------------------------------------------
            logger.info("[STAGE 8] Calibrating expressive narration...")
            if not os.path.exists(voice_path):
                raise FileNotFoundError(f"Calibrated voice narration track missing: {voice_path}")
            if persist_to_db:
                self._update_checkpoint(job_id, "VOICEOVER", 55.0, {"audio_path": voice_path})
            prod_ws.register_artifact("STAGE_08_VOICEOVER", "narration", voice_path, copy_into_workspace=False)

            # -------------------------------------------------------------
            # Stage 9: Video Rendering & Composition (Canonical Workspace Only)
            # -------------------------------------------------------------
            obs_stage = "video_rendering"
            production_observability.start_stage(job_id, "video_rendering", "Preparing FFmpeg video rendering pipeline...", progress=2.0)
            final_video_path = prod_ws.get_canonical_video_path()
            if prod_ws.has_verified_deliverable():
                logger.info("[PRODUCTION WORKSPACE IDEMPOTENCY] Reusing canonical deliverable: %s", final_video_path)
                production_observability.complete_stage(job_id, "video_rendering", "Reused verified canonical video deliverable")
            elif existing_cp.get("video_path") and os.path.isfile(existing_cp["video_path"]) and os.path.getsize(existing_cp["video_path"]) > 1024:
                final_video_path = existing_cp["video_path"]
                logger.info("[CHECKPOINT RECOVERY] Reusing verified video render: %s", final_video_path)
                production_observability.complete_stage(job_id, "video_rendering", "Resumed verified video render")
            elif is_full_live and plan is not None:
                logger.info("[STAGE 9] Rendering %d-beat 1080x1920 @ 30 FPS documentary Short via HybridVisualEngine...", len(plan.scenes))
                hybrid_engine = HybridVisualEngine(workspace_root=WORKSPACE)
                render_work_dir = os.path.join(prod_ws.prod_dir, "render_work")

                def _render_progress_cb(
                    completed_scenes: int,
                    total_scenes: int,
                    beat_id: str,
                    operation: str,
                    ffmpeg_status: str,
                ) -> None:
                    tot = max(1, int(total_scenes or len(plan.scenes)))
                    pct = round((min(completed_scenes, tot) / tot) * 90.0, 1)
                    if ffmpeg_status == "COMPOSITING":
                        pct = 93.0
                    elif ffmpeg_status == "FINALIZING_ENCODE":
                        pct = 97.0
                    production_observability.update_rendering(
                        job_id,
                        current_operation=str(operation),
                        current_beat_index=min(tot, int(completed_scenes) + (1 if completed_scenes < tot else 0)),
                        current_beat_id=str(beat_id),
                        completed_scenes=int(completed_scenes),
                        total_scenes=tot,
                        progress_pct=pct,
                        ffmpeg_status=str(ffmpeg_status),
                    )

                render_telemetry = asyncio.run(
                    hybrid_engine.render_storyboard(
                        plan=plan,
                        output_final_mp4=final_video_path,
                        work_dir=render_work_dir,
                        progress_callback=_render_progress_cb,
                    )
                )
                from backend.app.services.animation_service import probe_video
                probed = probe_video(final_video_path)
                dur = round(float(probed.get("duration", dur)), 2)
                render_meta_path = os.path.join(prod_ws.reports_dir, f"render_telemetry_{job_id}.json")
                with open(render_meta_path, "w", encoding="utf-8") as f:
                    json.dump({"render_telemetry": render_telemetry, "probed_video": probed}, f, indent=2)
                prod_ws.register_artifact("STAGE_09_RENDERING_TELEMETRY", "report", render_meta_path, copy_into_workspace=False)
                production_observability.update_rendering(
                    job_id,
                    current_operation="Master MP4 rendered and muxed",
                    progress_pct=100.0,
                    ffmpeg_status="COMPLETED",
                    output_path=final_video_path,
                )
                production_observability.complete_stage(job_id, "video_rendering", f"Rendered 1080x1920 @ 30 FPS master MP4 ({dur:.2f}s)")
                if persist_to_db:
                    self._update_checkpoint(job_id, "RENDERING", 75.0, {"video_path": final_video_path})
            else:
                logger.info("[STAGE 9] Rendering fresh 1080x1920 video with dynamic slate and typography...")
                production_observability.update_rendering(
                    job_id,
                    current_operation="Rendering compact test video slate...",
                    total_scenes=len(visual_beats),
                    completed_scenes=0,
                    progress_pct=25.0,
                    ffmpeg_status="ENCODING",
                )
                subprocess.run([
                    FFMPEG_EXE, "-y", "-f", "lavfi", "-i", f"color=c=0x111622:s=1080x1920:d={dur}",
                    "-i", voice_path, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
                    final_video_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                production_observability.update_rendering(
                    job_id,
                    current_operation="Compact video slate complete",
                    completed_scenes=len(visual_beats),
                    progress_pct=100.0,
                    ffmpeg_status="COMPLETED",
                    output_path=final_video_path,
                )
                production_observability.complete_stage(job_id, "video_rendering", "Video render complete")
                if persist_to_db:
                    self._update_checkpoint(job_id, "RENDERING", 75.0, {"video_path": final_video_path})
            prod_ws.register_artifact("STAGE_09_RENDERING", "video", final_video_path, copy_into_workspace=False, format="mp4")

            # -------------------------------------------------------------
            # Stage 10: Professional Thumbnail Generation (Canonical Workspace Only)
            # -------------------------------------------------------------
            obs_stage = "thumbnail_generation"
            production_observability.start_stage(job_id, "thumbnail_generation", "Initializing multi-candidate thumbnail generator...", progress=10.0)
            thumb_path = prod_ws.get_canonical_thumbnail_path()
            thumb_report_path = os.path.join(prod_ws.reports_dir, f"thumb_report_{job_id}.json")
            variants_dir = os.path.join(prod_ws.thumbnails_dir, "variants")

            if prod_ws.has_verified_deliverable():
                logger.info("[PRODUCTION WORKSPACE IDEMPOTENCY] Reusing canonical thumbnail: %s", thumb_path)
                winning_headline = existing_cp.get("winning_thumbnail", selected_topic.title[:25].upper())
                production_observability.update_thumbnail(
                    job_id,
                    status="completed",
                    image_generation_status="completed",
                    composition_status="completed",
                    text_layout_status="completed",
                    validation_status="completed",
                    output_path=thumb_path,
                    preview_url=f"/api/dashboard/productions/{job_id}/thumbnail?t={int(time.time())}",
                    headline=winning_headline,
                    stage_progress=100.0,
                )
                production_observability.complete_stage(job_id, "thumbnail_generation", f"Reused verified thumbnail '{winning_headline}'")
            elif existing_cp.get("thumbnail_path") and os.path.isfile(existing_cp["thumbnail_path"]) and os.path.getsize(existing_cp["thumbnail_path"]) > 1024:
                thumb_path = existing_cp["thumbnail_path"]
                logger.info("[CHECKPOINT RECOVERY] Reusing verified thumbnail: %s", thumb_path)
                winning_headline = existing_cp.get("winning_thumbnail", selected_topic.title[:25].upper())
                production_observability.update_thumbnail(
                    job_id,
                    status="completed",
                    image_generation_status="completed",
                    composition_status="completed",
                    text_layout_status="completed",
                    validation_status="completed",
                    output_path=thumb_path,
                    preview_url=f"/api/dashboard/productions/{job_id}/thumbnail?t={int(time.time())}",
                    headline=winning_headline,
                    stage_progress=100.0,
                )
                production_observability.complete_stage(job_id, "thumbnail_generation", f"Resumed verified thumbnail '{winning_headline}'")
            else:
                logger.info("[STAGE 10] Generating multi-candidate thumbnails in production workspace...")

                def _thumb_progress_cb(ev: Dict[str, str]) -> None:
                    st = ev.get("status", "running")
                    pct = 100.0 if st == "completed" else (85.0 if ev.get("validation_status") == "validating" else 55.0)
                    production_observability.update_thumbnail(
                        job_id,
                        status=ev.get("status"),
                        image_generation_status=ev.get("image_generation_status"),
                        composition_status=ev.get("composition_status"),
                        text_layout_status=ev.get("text_layout_status"),
                        validation_status=ev.get("validation_status"),
                        output_path=ev.get("preview_image_path") or thumb_path,
                        preview_url=f"/api/dashboard/productions/{job_id}/thumbnail?t={int(time.time())}" if st == "completed" else None,
                        headline=ev.get("winner_headline"),
                        stage_progress=pct,
                    )

                winning_thumb, thumb_rep = self.thumb_engine.select_and_render_best_thumbnail(
                    topic_title=selected_topic.title,
                    topic_summary=selected_topic.summary,
                    category=selected_topic.category,
                    available_assets=thumb_assets,
                    final_output_path=thumb_path,
                    report_output_path=thumb_report_path,
                    candidate_dir=variants_dir,
                    progress_callback=_thumb_progress_cb,
                )
                winning_headline = winning_thumb.headline_text
                production_observability.complete_stage(job_id, "thumbnail_generation", f"Selected winning thumbnail '{winning_headline}' (Score {winning_thumb.score.composite_score:.1f})")
                if persist_to_db:
                    self._update_checkpoint(job_id, "THUMBNAIL", 85.0, {
                        "thumbnail_path": thumb_path,
                        "winning_thumbnail": winning_headline,
                    })
            prod_ws.register_artifact("STAGE_10_THUMBNAIL", "thumbnail", thumb_path, copy_into_workspace=False, format="png")

            # -------------------------------------------------------------
            # Stage 11: Quality Control & Mandatory Content Integrity Gate
            # -------------------------------------------------------------
            obs_stage = "quality_control"
            production_observability.start_stage(job_id, "quality_control", "Running QC & Content Integrity Gate...", progress=20.0)
            logger.info("[CONTENT INTEGRITY GATE] Generating metadata and running 15-point QC...")
            meta = MetadataGenerator.generate_shorts_metadata(
                topic_title=selected_topic.title,
                opening_hook=best_hook.hook_text,
                category=selected_topic.category,
                key_entities=selected_topic.key_entities,
                story_summary=selected_topic.summary,
                sources=getattr(selected_topic, "sources", None),
                attributions=manifest_assets,
            )
            meta_json_path = os.path.join(prod_ws.metadata_dir, f"youtube_metadata_{job_id}.json")
            with open(meta_json_path, "w", encoding="utf-8") as f:
                json.dump(asdict(meta), f, indent=2)
            prod_ws.register_artifact("STAGE_11_METADATA", "metadata", meta_json_path, copy_into_workspace=False)

            logger.info("[STAGE 11] Running quality control gate...")
            if dur >= 60.0 or dur < 50.0:
                raise ValueError(f"QC Failure: Duration {dur:.2f}s is outside Shorts 50-58s window.")

            qc_checks_report: Dict = {}
            if is_full_live:
                qc_engine = QualityControlEngine()
                qc_passed, qc_errors, qc_checks_report = qc_engine.run_qc_checks(
                    video_path=final_video_path,
                    thumbnail_path=thumb_path,
                    metadata={
                        "title": selected_topic.title,
                        "youtube_title": meta.title,
                        "description": meta.description,
                        "tags": meta.tags,
                        "category_id": meta.category_id,
                    },
                    target_fps=30.0,
                    allow_custom_duration=False,
                    storyboard_beats=storyboard_beats,
                    workspace_dir=prod_ws.prod_dir,
                    topic_title=selected_topic.title,
                )
                qc_report_path = os.path.join(prod_ws.reports_dir, f"qc_report_{job_id}.json")
                with open(qc_report_path, "w", encoding="utf-8") as f:
                    json.dump({"passed": qc_passed, "errors": qc_errors, "checks": qc_checks_report}, f, indent=2)
                prod_ws.register_artifact("STAGE_11_QC", "report", qc_report_path, copy_into_workspace=False)
                if not qc_passed:
                    raise ValueError(f"QC Failure: {'; '.join(qc_errors)}")

            integrity_report = self.integrity_gate.validate_production(
                topic_title=selected_topic.title,
                category=selected_topic.category,
                narration_text=narration_text,
                visual_beat_configs=visual_beats,
                video_path=final_video_path,
                thumbnail_path=thumb_path,
                thumbnail_headline=winning_headline,
                metadata_title=meta.title,
                metadata_description=meta.description,
                metadata_tags=meta.tags,
                attribution_text=meta.attribution_text,
                manifest_assets=manifest_assets,
                rendering_engine=rendered_engine_name,
                storyboard_beats=storyboard_beats,
                production_workspace_dir=prod_ws.prod_dir,
            )

            integrity_audit_path = os.path.join(prod_ws.prod_dir, "audit", f"integrity_report_{job_id}.json")
            os.makedirs(os.path.dirname(integrity_audit_path), exist_ok=True)
            with open(integrity_audit_path, "w", encoding="utf-8") as f:
                json.dump({
                    "is_valid": integrity_report.is_valid,
                    "topic_title": integrity_report.topic_title,
                    "rejection_reasons": integrity_report.rejection_reasons,
                    "checks": [
                        {
                            "name": c.check_name,
                            "passed": c.passed,
                            "details": c.details,
                            "severity": c.severity,
                        }
                        for c in integrity_report.checks
                    ]
                }, f, indent=2)
            prod_ws.register_artifact("STAGE_14_GATE", "report", integrity_audit_path, copy_into_workspace=False)

            if not integrity_report.is_valid:
                logger.critical("[CONTENT INTEGRITY GATE] FAILED! Production rejected:\n%s", "\n".join(integrity_report.rejection_reasons))
                if persist_to_db:
                    with SessionLocal() as db_session:
                        job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                        if job:
                            job.status = "BLOCKED_QC"
                            job.current_stage = "BLOCKED_QC"
                            job.error_details = "; ".join(integrity_report.rejection_reasons)
                            db_session.commit()
                raise ValueError(f"BLOCKED_QC: {integrity_report.rejection_reasons}")

            production_observability.complete_stage(job_id, "quality_control", f"QC and {len(integrity_report.checks)}-point Integrity Gate PASSED")
            obs_stage = "finalization"
            production_observability.start_stage(job_id, "finalization", "Finalizing canonical workspace, manifest & notifications...", progress=30.0)
            logger.info("[CONTENT INTEGRITY GATE] PASSED! %d checks verified for topic '%s'.", len(integrity_report.checks), selected_topic.title)

            # -------------------------------------------------------------
            # Stage 12: Production Database Persistence
            # -------------------------------------------------------------
            if persist_to_db:
                logger.info("[STAGE 12] Recording publication state in database...")
                with SessionLocal() as db_session:
                    job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                    if job:
                        job.status = "COMPLETED"
                        job.current_stage = "COMPLETED"
                        job.topic_title = selected_topic.title
                        job.progress_pct = 100.0
                        job.checkpoint_data_json = json.dumps({
                            "video_path": final_video_path,
                            "thumbnail_path": thumb_path,
                            "duration_sec": dur,
                            "topic": selected_topic.title,
                            "hook": best_hook.hook_text,
                            "winning_thumbnail": winning_headline,
                        })
                        job.completed_at = datetime.utcnow()
                        db_session.commit()
            else:
                logger.info("[STAGE 12] VALIDATION context: skipping studio.db JobModel mutation; checkpoint saved in workspace reports.")
                val_cp_path = os.path.join(prod_ws.reports_dir, f"validation_job_summary_{job_id}.json")
                with open(val_cp_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "job_id": job_id,
                        "execution_context": execution_context.value,
                        "status": "COMPLETED",
                        "topic": selected_topic.title,
                        "duration_sec": dur,
                        "video_path": final_video_path,
                        "thumbnail_path": thumb_path,
                    }, f, indent=2)
                prod_ws.register_artifact("STAGE_12_VALIDATION_SUMMARY", "report", val_cp_path, copy_into_workspace=False)

            # Record in TopicLedger to prevent duplicate topic generation (strictly skipped outside PRODUCTION)
            rec = ContentRecord(
                topic_id=f"topic_{job_id}",
                topic_title=selected_topic.title,
                opening_hook=best_hook.hook_text,
                main_subject=selected_topic.title,
                key_entities=selected_topic.key_entities,
                script_fingerprint=script_fp,
                category=selected_topic.category,
                visual_style="historical_documentary",
                created_at=datetime.utcnow().isoformat() + "Z",
                publication_status="produced",
            )
            self.ledger.record_content(rec, allow_override=False, execution_context=execution_context.value)

            # Update isolated ValidationTopicHistory with full completion fingerprints
            val_hist = getattr(self.ledger, "validation_history", None)
            if execution_context != ExecutionContext.PRODUCTION and val_hist is not None:
                val_hist.update_outcome(
                    job_id,
                    "completed",
                    topic_title=selected_topic.title,
                    research_fingerprint=getattr(selected_topic, "research_fingerprint", ""),
                    script_fingerprint=script_fp,
                    storyboard_fingerprint=getattr(prod_ws, "storyboard_fingerprint", ""),
                    thumbnail_headline=winning_headline,
                    metadata={
                        "video_path": final_video_path,
                        "thumbnail_path": thumb_path,
                        "manifest_path": prod_ws.manifest_path,
                    },
                )

            # -------------------------------------------------------------
            # Stage 13: Notification Dispatch
            # -------------------------------------------------------------
            logger.info("[STAGE 13] Dispatching success notifications...")
            notif_res = self.notifier.send_success(
                video_title=selected_topic.title,
                topic=selected_topic.title,
                storytelling_style="historical_documentary",
                duration_sec=dur,
                video_path=final_video_path,
                thumbnail_path=thumb_path,
                qc_status="PASSED",
                job_id=job_id,
                execution_context=execution_context,
            )

            # -------------------------------------------------------------
            # Stage 14: YouTube Publishing Queue Enqueueing
            # -------------------------------------------------------------
            is_auto_public = getattr(settings, "PUBLISHING_MODE", "") == "automatic_public"
            target_visibility = "public" if is_auto_public else "private"
            publishing_status = f"enqueued_{target_visibility}"

            if execution_context == ExecutionContext.PRODUCTION and not job_id.startswith("test_") and not job_id.startswith("mock_"):
                logger.info("[STAGE 14] Enqueueing to YouTube Publishing Queue in PRODUCTION context...")
                pub_record = PublicationRecord(
                    id=None,
                    topic_title=selected_topic.title,
                    video_path=final_video_path,
                    thumbnail_path=thumb_path,
                    metadata_json=json.dumps(asdict(meta)),
                    youtube_video_id=None,
                    upload_status="pending_queue",
                    visibility=target_visibility,
                    video_hash=compute_file_sha256(final_video_path),
                    upload_timestamp=None,
                    scheduled_timestamp="10:00 AM IST",
                )
                self.publishing_engine.db.record_publication(pub_record, execution_context=execution_context)
            else:
                publishing_status = (
                    "suppressed_validation_execution"
                    if execution_context == ExecutionContext.VALIDATION
                    else "suppressed_test_execution"
                )
                logger.info(
                    "[STAGE 14] Execution context is %s (job_id=%s). Suppressing YouTube publishing queue insertion.",
                    execution_context.value,
                    job_id,
                )

            elapsed = time.perf_counter() - t_start
            production_observability.complete_production(
                job_id,
                f"Completed '{selected_topic.title}' in {elapsed:.1f}s ({len(visual_beats)} beats)",
            )
            logger.info("=== MASTER PIPELINE COMPLETED IN %.1fs ===", elapsed)
            return {
                "job_id": job_id,
                "production_id": prod_ws.production_id,
                "production_dir": prod_ws.prod_dir,
                "manifest_path": prod_ws.manifest_path,
                "status": "COMPLETED",
                "topic": selected_topic.title,
                "video_path": final_video_path,
                "thumbnail_path": thumb_path,
                "duration_sec": dur,
                "beat_count": len(visual_beats),
                "elapsed_sec": round(elapsed, 1),
                "qc_checks": qc_checks_report,
                "integrity_report": integrity_audit_path,
                "notifications": notif_res,
                "publishing_status": publishing_status,
            }

        except Exception as e:
            logger.error("Master pipeline failed at %s: %s", locals().get("obs_stage", "orchestration_pipeline"), e)
            val_hist = getattr(self.ledger, "validation_history", None)
            if execution_context != ExecutionContext.PRODUCTION and val_hist is not None:
                try:
                    sel_t = locals().get("selected_topic")
                    if sel_t is not None:
                        val_hist.update_outcome(
                            job_id,
                            "rejected",
                            topic_title=sel_t.title,
                            rejection_reason=str(e),
                        )
                except Exception:
                    pass
            production_observability.fail_production(
                job_id,
                stage=str(locals().get("obs_stage", "orchestration_pipeline")),
                error_summary=str(e),
                retry_possible=True,
                safe_to_resume=True,
            )
            if persist_to_db:
                with SessionLocal() as db_session:
                    job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                    if job:
                        job.status = "FAILED"
                        job.error_details = str(e)
                        db_session.commit()

            # Dispatch failure alert
            self.notifier.send_failure(
                topic="Autonomous Generation",
                stage="orchestration_pipeline",
                error_summary=str(e),
                job_id=job_id,
                manual_intervention_required=True,
                execution_context=execution_context,
            )
            raise e

    def process_publishing_queue(self, execute_upload: bool = False) -> Dict:
        """
        Executes Stage 14 independently according to the publishing schedule.
        Decoupled from generation. Enforces settings.PUBLISHING_MODE.
        """
        logger.info("[PUBLISHING QUEUE] Checking enqueued publications...")
        quota_used = self.publishing_engine.db.get_quota_used_today()
        logger.info("Daily YouTube quota consumed: %d units", quota_used)

        is_auto_public = getattr(settings, "PUBLISHING_MODE", "") == "automatic_public"
        should_upload = execute_upload and is_auto_public

        if not should_upload:
            logger.info("Publishing mode is '%s'. Upload withheld at safety gate.", settings.PUBLISHING_MODE)
            return {
                "status": "withheld_safety_gate",
                "publishing_mode": settings.PUBLISHING_MODE,
                "quota_used": quota_used,
                "message": "Videos remain staged safely for manual approval."
            }

        target_visibility = "public" if is_auto_public else "private"

        # If upload authorized, fetch next pending video
        conn = sqlite3.connect(self.publishing_engine.db.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic_title, video_path, thumbnail_path, metadata_json
            FROM publishing_ledger
            WHERE upload_status = 'pending_queue'
            ORDER BY id ASC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()

        if not row:
            return {"status": "queue_empty", "message": "No pending videos in publishing queue."}

        p_id, title, v_path, t_path, m_json = row
        meta_dict = json.loads(m_json)
        metadata = YouTubeMetadata(**meta_dict)

        res = self.publishing_engine.upload_short(
            video_path=v_path,
            topic_title=title,
            metadata=metadata,
            thumbnail_path=t_path,
            visibility=target_visibility,
            allow_public_bypass=is_auto_public,
        )
        return res


# Global orchestrator instance
master_orchestrator = CrazzyDocsMasterOrchestrator()
