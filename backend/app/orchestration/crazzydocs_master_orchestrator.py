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
from collections import Counter
from contextvars import ContextVar
from backend.app.core.async_helpers import run_coro_sync
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Tuple
from unittest.mock import NonCallableMock
from zoneinfo import ZoneInfo

suppress_orchestrator_completion_email: ContextVar[bool] = ContextVar(
    "suppress_orchestrator_completion_email", default=False
)

from backend.app.core.config import settings
from backend.app.core.execution_context import (
    ExecutionContext,
    require_production_context,
    get_current_execution_context,
)
from backend.app.content.creative_production_policy import (
    CategoryDistributionTracker,
    get_creative_production_policy,
)
from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine, ScoredTopic
from backend.app.content.topic_ledger import (
    TopicLedger,
    ContentRecord,
    compute_script_fingerprint,
    compute_storyboard_fingerprint,
)
from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.content.grounded_script_writer import write_grounded_script
from backend.app.content.creative_identity_registry import (
    CreativeIdentityRegistry,
    DuplicateCreativeIdentity,
    asset_fingerprints,
)
from backend.app.content.visual_diversity_policy import compute_asset_sha256
from backend.app.orchestration.production_workspace import ProductionWorkspace, compute_file_sha256
from backend.app.orchestration.stage_artifact_cache import (
    StageArtifactCache,
    fingerprint as cache_fingerprint,
    media_files,
    storyboard_files,
)
from backend.app.rendering.render_errors import PublishingStepError, RenderFailure, is_readiness_failure
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
from backend.app.core.paths import get_ffmpeg_path as _get_ffmpeg_path
FFMPEG_EXE = _get_ffmpeg_path()

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)


class ProductionGracefulStop(Exception):
    """Raised cleanly at safe stage boundaries when an operator requests graceful stop."""


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

    @staticmethod
    def _check_visual_identity(registry, job_id: str, fingerprints: Dict) -> None:
        """Rejects a film whose scene composition repeats an earlier production (before rendering)."""
        dups = registry.near_duplicates(job_id=job_id, visual_concepts=fingerprints.get("visual_concepts", []))
        visual = [d for d in dups["blocking"] if d["kind"] == "visual_sequence"]
        if visual:
            registry.update(job_id, status="REJECTED_VISUAL_DUPLICATE")
            raise DuplicateCreativeIdentity(f"integrity gate: scene composition repeats {visual[:2]}")

    def _identity_registry(self, execution_context, is_full_live: bool, job_id: Optional[str]):
        """The creative identity registry for real productions; None for tests and validation runs.

        It shares the ledger's database, so a test that hands in a temporary ledger gets a
        temporary registry, and nothing under pytest can write the live studio database.
        """
        if execution_context != ExecutionContext.PRODUCTION or not is_full_live:
            return None
        if (job_id or "").startswith("test_") or os.environ.get("CRAZZYDOCS_TEST_MODE") == "1":
            return None
        db_path = getattr(self.ledger, "db_path", None)
        if not db_path:
            return None
        from backend.app.content.topic_ledger import DB_PATH as _LIVE_DB
        if os.environ.get("PYTEST_CURRENT_TEST") and os.path.abspath(db_path) == os.path.abspath(_LIVE_DB):
            return None
        registry = CreativeIdentityRegistry(db_path)
        try:
            registry.backfill_from_history()
        except Exception as exc:   # history import is best effort; the live gates still apply
            logger.warning("Creative identity backfill skipped: %s", exc)
        return registry

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

    RENDER_ATTEMPTS = int(os.environ.get("CRAZZYDOCS_RENDER_ATTEMPTS", "2"))
    RENDER_RETRY_DELAY_SEC = 5.0

    def _render_with_bounded_retries(self, job_id: str, render, *, preserved: Dict, persist_to_db: bool):
        """
        Runs the video render up to RENDER_ATTEMPTS times with the SAME prepared inputs
        (no TTS, media or storyboard work is repeated). Raises RenderFailure when every
        attempt fails; `deterministic` is set when all attempts were readiness failures,
        so the scheduler does not spend whole-pipeline retries on a defect a rerun cannot fix.
        """
        errors: List[str] = []
        attempts = max(1, self.RENDER_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            try:
                result = render()
                if attempt > 1:
                    logger.info("[RENDER RECOVERY] Render succeeded on in-stage attempt %d/%d.", attempt, attempts)
                return result
            except DuplicateCreativeIdentity:
                raise   # a creative-identity rejection is final; re-rendering cannot change it
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
                logger.warning(
                    "[RENDER RECOVERY] Render attempt %d/%d failed (%s): %s",
                    attempt, attempts, "readiness" if is_readiness_failure(str(exc)) else type(exc).__name__,
                    str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__,
                )
                try:
                    from backend.app.services.lifecycle_events import record_lifecycle_event
                    record_lifecycle_event(
                        "RENDER_ATTEMPT_FAILED", "RENDERING",
                        cycle_id="_".join(job_id.split("_")[:2]) if "_" in job_id else None,
                        job_id=job_id,
                        details={"attempt": attempt, "of": attempts, "readiness": is_readiness_failure(str(exc))},
                        error_details=str(exc)[:500],
                    )
                except Exception:
                    pass
                if attempt < attempts:
                    time.sleep(self.RENDER_RETRY_DELAY_SEC)
        readiness = all(is_readiness_failure(e) for e in errors)
        if persist_to_db:
            self._update_checkpoint(job_id, "RENDER_FAILED", 60.0, {"render_inputs_preserved": preserved})
        raise RenderFailure(
            f"Video render failed {attempts}x"
            + (" (page never signalled readiness)" if readiness else "")
            + f": {errors[-1][:1500]}",
            attempts=attempts,
            readiness=readiness,
            deterministic=readiness and attempts >= 2,
            preserved=preserved,
        )

    def _update_checkpoint(self, job_id: str, stage: str, progress: float, stage_data: Dict):
        """Persists incremental stage checkpoint to SQLite JobModel."""
        try:
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if job:
                    job.current_stage = stage
                    job.progress_pct = progress
                    job.heartbeat_at = datetime.utcnow()
                    try:
                        from backend.app.services.lifecycle_events import record_lifecycle_event
                        cid = "_".join(job_id.split("_")[:2]) if "_" in job_id else None
                        record_lifecycle_event(
                            "PIPELINE_STAGE_CHANGED",
                            "RUNNING",
                            cycle_id=cid,
                            job_id=job_id,
                            details={"stage": stage, "progress_pct": progress},
                        )
                    except Exception:
                        pass
                    if stage_data.get("topic_title"):
                        job.topic_title = str(stage_data["topic_title"])[:255]
                    try:
                        from backend.app.services.control_center_service import get_publishing_policy
                        job.publishing_mode = get_publishing_policy().upper()
                    except Exception:
                        pass
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

    def _check_control_signal(self, job_id: str, persist_to_db: bool) -> None:
        """Honors operator pause/graceful-stop signals cleanly between stages."""
        try:
            from backend.app.services import control_center_service
            signal = control_center_service.wait_or_check_production_control(
                job_id=job_id if persist_to_db else None,
            )
            if signal == "STOP":
                raise ProductionGracefulStop("Operator requested graceful stop")
        except ProductionGracefulStop:
            raise
        except Exception:
            pass

    def execute_production_pipeline(
        self,
        job_id: Optional[str] = None,
        force_topic_id: Optional[str] = None,
        execution_context: Optional[ExecutionContext] = None,
        use_live_pipeline: Optional[bool] = None,
        preferred_category: Optional[str] = None,
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
        identity_registry = self._identity_registry(execution_context, is_full_live, job_id)

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_id = job_id or (
            f"val_3_21j_{now_str}" if execution_context == ExecutionContext.VALIDATION else f"job_{now_str}"
        )
        t_start = time.perf_counter()
        start_time_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S IST")
        attrib_json_path = ""
        subtitle_path = ""
        winning_thumb_score: Optional[float] = None
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
                fallback_wiki = f"https://en.wikipedia.org/wiki/{existing_cp['topic_title'].replace(' ', '_')}"
                selected_topic = ScoredTopic(
                    id=f"topic_{job_id}",
                    title=existing_cp["topic_title"],
                    category=existing_cp.get("category", "history"),
                    category_code="H",
                    summary=existing_cp.get("topic_summary", existing_cp["topic_title"]),
                    extended_extract=existing_cp.get("extended_extract", ""),
                    factual_bullets=existing_cp.get("factual_bullets", []),
                    coordinates=existing_cp.get("coordinates"),
                    discovery_source=existing_cp.get("discovery_source", "checkpoint_recovery"),
                    curiosity=9.5,
                    surprise=9.0,
                    storytelling=9.2,
                    visual_potential=9.4,
                    emotional_connection=9.0,
                    factual_reliability=9.5,
                    composite_score=9.3,
                    sources=existing_cp.get("sources") or [fallback_wiki],
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

                creative_policy = get_creative_production_policy(execution_context)
                if is_full_live:
                    ranked = self.topic_engine.discover_and_rank_live_topics(
                        filter_duplicates=filter_dups,
                        preferred_category=preferred_category,
                    )
                else:
                    ranked = self.topic_engine.discover_and_rank_topics(filter_duplicates=filter_dups)

                if not ranked:
                    raise RuntimeError("No eligible candidate topics found passing duplicate and factual gates.")

                # A failed production never reaches the ledger, so without this the same
                # top-ranked subject is re-selected cycle after cycle (2026-11-13 audit).
                if identity_registry is not None:
                    ranked, identity_rejections = identity_registry.filter_fresh(ranked, job_id=job_id)
                    for rej in identity_rejections:
                        logger.info("[CREATIVE IDENTITY] Skipping '%s': already selected by %s (%s)",
                                    rej["title"], rej["conflicts_with"], rej["status"])
                    if not ranked:
                        raise DuplicateCreativeIdentity(
                            "integrity gate: every discovered topic was already selected by an earlier "
                            f"production ({len(identity_rejections)} rejected); refusing to repeat a subject."
                        )

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
                if identity_registry is not None:
                    identity_registry.reserve(
                        job_id,
                        topic_title=selected_topic.title,
                        category=selected_topic.category,
                        topic_angle=best_hook.approach,
                        hook_fingerprint=compute_script_fingerprint(best_hook.hook_text),
                    )

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
                        "extended_extract": getattr(selected_topic, "extended_extract", ""),
                        "factual_bullets": getattr(selected_topic, "factual_bullets", []),
                        "sources": getattr(selected_topic, "sources", []),
                        "coordinates": getattr(selected_topic, "coordinates", None),
                        "discovery_source": getattr(selected_topic, "discovery_source", "live"),
                        "hook_text": best_hook.hook_text,
                        "category": selected_topic.category,
                        "key_entities": selected_topic.key_entities,
                    })

            # -------------------------------------------------------------
            # Stage 5 & 6 & 7: Creative Direction, Media & Storyboard
            # -------------------------------------------------------------
            self._check_control_signal(job_id, persist_to_db)
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
                production_observability.start_stage(job_id, "script_generation", "Drafting 5-stage explanatory script...", progress=30.0)
                # A retry of this production reuses script, narration, media and storyboard
                # from the previous attempt when they are still valid (stage_artifact_cache).
                stage_cache = StageArtifactCache(os.path.join(prod_ws.reports_dir, "stage_cache"))
                script_data, _script_reused = stage_cache.get_or_compute(
                    "script",
                    cache_fingerprint(job_id, selected_topic.title, best_hook.hook_text),
                    lambda: write_grounded_script(
                        selected_topic,
                        best_hook.hook_text,
                        job_id=job_id,
                        execution_context=execution_context,
                    ),
                )
                narration_text = script_data["full_script"]
                script_path = os.path.join(prod_ws.scripts_dir, f"script_{job_id}.json")
                with open(script_path, "w", encoding="utf-8") as f:
                    json.dump(script_data, f, indent=2)
                prod_ws.register_artifact("STAGE_04_SCRIPT", "script", script_path, copy_into_workspace=False)
                production_observability.complete_stage(job_id, "script_generation", f"Script finalized ({script_data.get('word_count', len(narration_text.split()))} words, {script_data.get('script_source', 'extractive')})")
                logger.info("[SCRIPT] source=%s hook_type=%s angle=%s", script_data.get("script_source"),
                            script_data.get("hook_type"), script_data.get("angle"))

                # Real Edge-TTS voiceover synthesis + ffprobe timing
                obs_stage = "narration_generation"
                production_observability.start_stage(job_id, "narration_generation", "Synthesizing expressive Neural TTS voiceover...", progress=25.0)
                (voice_path, section_timings, voice_dur), _tts_reused = stage_cache.get_or_compute(
                    "narration",
                    cache_fingerprint(script_data.get("full_script"), script_data.get("sections")),
                    lambda: synthesize_and_probe_narration(self.tts_engine, script_data["sections"], prod_ws),
                    files_of=lambda v: [v[0]],
                )
                dur = round(voice_dur + 2.0, 2)  # Main narrative + 2.0s branded outro
                production_observability.complete_stage(job_id, "narration_generation", f"Narration synthesized ({voice_dur:.2f}s)")

                # Acquire isolated openly licensed assets into workspace
                obs_stage = "asset_acquisition"
                production_observability.start_stage(job_id, "asset_acquisition", "Fetching verified Wikimedia/Openverse images...", progress=20.0)
                def _acquire_media():
                    stats: Dict = {"unique_images": 0, "failed": 0, "duplicates_skipped": 0}
                    assets, attrib, manifest = acquire_isolated_topic_assets(
                        selected_topic, prod_ws, execution_context, max_assets=10, stats_out=stats
                    )
                    return assets, attrib, manifest, stats

                (acquired_assets, attrib_manifest, manifest_assets, _acq_stats_cached), _media_reused = stage_cache.get_or_compute(
                    "media",
                    cache_fingerprint(selected_topic.title, getattr(selected_topic, "id", None), 10),
                    _acquire_media,
                    files_of=lambda v: media_files(v[0]),
                )
                acq_stats.update(_acq_stats_cached or {})
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

                failed_acq_count = int(acq_stats.get("failed_acquisitions", acq_stats.get("failed", 0)))
                unique_img_count = int(acq_stats.get("authentic_images_acquired", 0)) or (
                    len({ma.get("sha256") for ma in manifest_assets if ma.get("sha256") and ma.get("asset_type") != "existing_animation"})
                    or len(thumb_assets)
                )
                existing_anim_count = int(acq_stats.get("existing_animations_acquired", 0))
                production_observability.update_assets(
                    job_id,
                    unique_images=unique_img_count,
                    failed_acquisitions=failed_acq_count,
                    status="ACQUIRED",
                )
                production_observability.complete_stage(
                    job_id,
                    "asset_acquisition",
                    f"Acquired {unique_img_count} images + {existing_anim_count} open animations ({failed_acq_count} failed)",
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

                (plan, visual_beats, ass_path), _storyboard_reused = stage_cache.get_or_compute(
                    "storyboard",
                    cache_fingerprint(
                        voice_path, section_timings, voice_dur, narration_text,
                        [getattr(a, "local_path", None) for a in acquired_assets],
                    ),
                    lambda: build_20_beat_storyboard(
                        topic=selected_topic,
                        timings=section_timings,
                        total_voice_dur=voice_dur,
                        voiceover_path=voice_path,
                        acquired_assets=acquired_assets,
                        prod_ws=prod_ws,
                        script_full_text=narration_text,
                        progress_cb=_sb_progress_cb,
                        execution_context=execution_context,
                    ),
                    files_of=lambda v: [v[2]] + storyboard_files((v[0].metadata or {}).get("storyboard_beats", [])),
                )
                storyboard_beats = list((plan.metadata or {}).get("storyboard_beats", []))
                sb_meta = plan.metadata or {}
                src_sum = dict(sb_meta.get("visual_sourcing_summary") or {})
                total_reused_slots = int(src_sum.get("reused_source_images", 0))
                visual_diversity = {
                    key: src_sum.get(key, 0)
                    for key in (
                        "total_visual_beats",
                        "unique_photographic_assets",
                        "unique_video_assets",
                        "unique_maps",
                        "unique_diagrams",
                        "generated_images",
                        "animations",
                        "reused_source_images",
                        "near_duplicate_groups",
                        "max_source_photo_reuse",
                    )
                }
                c_policy = get_creative_production_policy(execution_context)
                production_observability.update_assets(
                    job_id,
                    unique_images=int(src_sum.get("unique_photographic_assets", src_sum.get("authentic_images_acquired", unique_img_count))),
                    generated_images=int(src_sum.get("generated_images", 0)),
                    animations=int(src_sum.get("unique_video_assets", sb_meta.get("unique_animations", 0))),
                    reused_images=total_reused_slots,
                    max_allowed_reuse=c_policy.visual_policy.max_image_reuse,
                    status="COMPLETE",
                )
                attr_complete_count = sum(
                    1 for ma in manifest_assets
                    if ma.get("license") and ma.get("source_url") and ma.get("creator")
                )
                attr_pct = (100.0 * attr_complete_count / len(manifest_assets)) if manifest_assets else 100.0
                production_observability.update_creative_intelligence(
                    job_id,
                    category=selected_topic.category,
                    supported_categories_count=len(c_policy.list_categories()),
                    category_distribution=CategoryDistributionTracker().get_distribution_counts(window=20),
                    topic_score_breakdown=getattr(selected_topic, "score_breakdown", {}) or {},
                    visual_style_distribution=dict(src_sum.get("visual_style_distribution") or {}),
                    scene_layout_distribution=dict(src_sum.get("scene_layout_distribution") or {}),
                    primary_creative_direction=str(src_sum.get("primary_creative_direction") or "documentary"),
                    composer_distribution=dict(Counter(
                        str(beat.get("composer_id") or "legacy_adaptive_composer")
                        for beat in (sb_meta.get("storyboard_beats") or [])
                    )),
                    film_treatment=(sb_meta.get("film_treatment") or {}),
                    visual_diversity=visual_diversity,
                    authentic_images_acquired=int(src_sum.get("authentic_images_acquired", unique_img_count)),
                    existing_animations_acquired=int(src_sum.get("existing_animations_acquired", existing_anim_count)),
                    animated_authentic_composites=int(src_sum.get("priority_c_animated_authentic_beats", 0)),
                    generated_images=int(src_sum.get("priority_d_generated_image_beats", 0)),
                    bespoke_animations=int(src_sum.get("priority_d_bespoke_anim_beats", 0)),
                    license_verified_count=len(manifest_assets),
                    rejected_license_count=int(acq_stats.get("rejected_license_count", 0)),
                    attribution_completeness_pct=attr_pct,
                )
                production_observability.complete_stage(job_id, "image_generation", "Analytical illustrations saved in assets/generated/")
                production_observability.complete_stage(job_id, "animation_generation", f"Rendered {sb_meta.get('unique_animations', 0)} 30 FPS explainer animations")
                production_observability.complete_stage(job_id, "storyboard_generation", f"20-beat adaptive storyboard verified ({sb_meta.get('distinct_visual_assets', 0)} distinct visual assets)")

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
                            "visual_diversity": visual_diversity,
                            "visual_style_distribution": src_sum.get("visual_style_distribution", {}),
                            "scene_layout_distribution": src_sum.get("scene_layout_distribution", {}),
                            "primary_creative_direction": src_sum.get("primary_creative_direction", "documentary"),
                        },
                        "acquired_assets": manifest_assets,
                        "beats": storyboard_beats,
                    }, f, indent=2)
                prod_ws.register_artifact("STAGE_07_ASSET_INVENTORY", "audit", asset_inv_path, copy_into_workspace=False)
                rendered_engine_name = "lemo-opuscar"
                render_telemetry: Dict[str, Any] = {}
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
            if identity_registry is not None:
                if identity_registry.get(job_id) is None:   # checkpoint resume from before the registry existed
                    identity_registry.reserve(job_id, topic_title=selected_topic.title, category=selected_topic.category)
                script_hit = identity_registry.script_conflict(script_fp, exclude_job=job_id)
                if script_hit:
                    identity_registry.update(job_id, script_fingerprint=script_fp, status="REJECTED_DUPLICATE_SCRIPT")
                    raise DuplicateCreativeIdentity(
                        f"integrity gate: script is identical to production {script_hit['job_id']} "
                        f"('{script_hit['topic_title']}'); refusing to render a repeated video."
                    )
                identity_registry.update(
                    job_id,
                    script_fingerprint=script_fp,
                    storyboard_fingerprint=storyboard_fp,
                    asset_fingerprints_json=asset_fingerprints(storyboard_beats),
                    visual_sequence_fingerprint=compute_script_fingerprint(
                        " ".join(f"{b.get('visual_style')}:{b.get('scene_layout')}" for b in storyboard_beats or [] if isinstance(b, dict))
                    ),
                    status="SCRIPTED",
                )
                if (script_data or {}).get("angle"):
                    identity_registry.update(job_id, topic_angle=script_data["angle"])
                sections_ = (script_data or {}).get("sections") or []
                hook_txt = sections_[0].get("text", "") if sections_ else ""
                end_txt = sections_[-1].get("text", "") if sections_ else ""
                dups = identity_registry.near_duplicates(
                    job_id=job_id, topic_title=selected_topic.title, script_text=narration_text,
                    hook_text=hook_txt, ending_text=end_txt,
                    script_source=(script_data or {}).get("script_source", ""),
                )
                identity_registry.update(
                    job_id, script_text=narration_text, hook_text=hook_txt, ending_text=end_txt,
                    hook_type=(script_data or {}).get("hook_type", ""),
                    narrative_structure=" > ".join(s.get("stage", "") for s in sections_),
                    script_source=(script_data or {}).get("script_source", ""),
                )
                for adv in dups["advisory"]:
                    logger.warning("[CREATIVE IDENTITY] advisory near-duplicate %s", adv)
                if dups["blocking"]:
                    identity_registry.update(job_id, status="REJECTED_NEAR_DUPLICATE")
                    raise DuplicateCreativeIdentity(
                        f"integrity gate: near-duplicate of earlier production: {dups['blocking'][:3]}"
                    )

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
            self._check_control_signal(job_id, persist_to_db)
            obs_stage = "video_rendering"
            production_observability.start_stage(job_id, "video_rendering", "Preparing FFmpeg video rendering pipeline...", progress=2.0)
            final_video_path = prod_ws.get_canonical_video_path()
            _reuse_video = False
            if prod_ws.has_verified_deliverable():
                try:
                    from backend.app.services.animation_service import probe_video as _probe_cached_video
                    _cached_dur = float((_probe_cached_video(final_video_path) or {}).get("duration", 0.0))
                    _cached_manifest = prod_ws.load_manifest() or {}
                    _cached_renderer = str(_cached_manifest.get("renderer") or "").strip().lower()
                    _renderer_ok = (not is_full_live) or (_cached_renderer == "lemo-opuscar")
                    if _renderer_ok and (not is_full_live or (50.0 <= _cached_dur < 60.0)) and abs(_cached_dur - dur) <= 1.5:
                        _reuse_video = True
                    else:
                        logger.warning(
                            "[PRODUCTION WORKSPACE IDEMPOTENCY] Ignoring stale deliverable %s (probed=%.2fs, expected=%.2fs, renderer=%s).",
                            final_video_path, _cached_dur, dur, _cached_renderer or "unknown",
                        )
                except Exception:
                    _reuse_video = False

            if _reuse_video:
                logger.info("[PRODUCTION WORKSPACE IDEMPOTENCY] Reusing canonical deliverable: %s", final_video_path)
                production_observability.complete_stage(job_id, "video_rendering", "Reused verified canonical video deliverable")
            elif (
                not is_full_live
                and existing_cp.get("video_path")
                and os.path.isfile(existing_cp["video_path"])
                and os.path.getsize(existing_cp["video_path"]) > 1024
            ):
                final_video_path = existing_cp["video_path"]
                logger.info("[CHECKPOINT RECOVERY] Reusing verified video render: %s", final_video_path)
                production_observability.complete_stage(job_id, "video_rendering", "Resumed verified video render")
            elif is_full_live and plan is not None:
                from backend.app.content.creative_identity_policy import CreativeIdentityPolicy
                from backend.app.core.process_utils import hidden_subprocess_kwargs
                from backend.app.rendering.lemo_opuscar_adapter import LemoOpuscarAdapter, PINNED_COMMIT
                from backend.app.rendering.lemo_opuscar_film_builder import LemoOpuscarFilmBuilder

                rend_ok, rend_issues = CreativeIdentityPolicy.validate_production_renderer(
                    rendered_engine_name,
                    execution_context=execution_context.value,
                )
                if not rend_ok:
                    raise RuntimeError(f"Renderer compliance error: {'; '.join(rend_issues)}")

                logger.info(
                    "[STAGE 9] Rendering %d-beat 1080x1920 @ 30 FPS documentary Short via Lemo-Opuscar (%s)...",
                    len(plan.scenes), PINNED_COMMIT[:10],
                )
                production_observability.update_rendering(
                    job_id,
                    current_operation="Building Lemo-Opuscar deterministic film package",
                    current_beat_index=1,
                    current_beat_id="beat_01",
                    completed_scenes=0,
                    total_scenes=len(plan.scenes),
                    progress_pct=15.0,
                    ffmpeg_status="PREPARING_LEMO_FILM",
                )
                from backend.app.rendering.pop_info.pipeline import (
                    active_visual_engine, photos_from_media_assets, render_pop_info,
                )
                # Feature flag: the legacy film builder runs unless the operator sets
                # CRAZZYDOCS_VISUAL_ENGINE=pop_info_v1 (environment or .env.local).
                visual_engine = active_visual_engine()
                if visual_engine != "legacy":
                    logger.info("[STAGE 9] Visual engine %s selected by feature flag", visual_engine)
                    pop_info_result = self._render_with_bounded_retries(
                        job_id,
                        lambda: render_pop_info(
                            workspace_root=Path(WORKSPACE), prod_dir=Path(prod_ws.prod_dir), job_id=job_id,
                            topic=selected_topic.title, category=selected_topic.category,
                            script_data=script_data or {}, section_timings=section_timings or [],
                            voice_path=voice_path, duration_sec=dur, output_path=Path(final_video_path),
                            photos=photos_from_media_assets(acquired_assets),
                            identity_check=(None if identity_registry is None else
                                            lambda fp: self._check_visual_identity(identity_registry, job_id, fp)),
                        ),
                        preserved={"voiceover_path": voice_path,
                                   "stage_cache_dir": os.path.join(prod_ws.reports_dir, "stage_cache")},
                        persist_to_db=persist_to_db,
                    )
                    film_manifest = pop_info_result["film"]
                    if identity_registry is not None:
                        fp = film_manifest.get("fingerprints", {})
                        identity_registry.update(
                            job_id, visual_engine=visual_engine,
                            scene_sequence_json=fp.get("scene_sequence", []),
                            visual_concepts_json=fp.get("visual_concepts", []),
                            visual_concepts_fingerprint=fp.get("visual_concepts_fingerprint", ""),
                            transition_sequence_json=fp.get("transition_sequence", []),
                            typography=fp.get("typography", ""), animation_json=fp.get("animation_choices", []),
                            audio_treatment="synthesized sfx + ducked pad, -14 LUFS",
                        )
                    if pop_info_result.get("quality_report_path"):
                        prod_ws.register_artifact("STAGE_09_VISUAL_QC", "report", pop_info_result["quality_report_path"], copy_into_workspace=False)
                else:
                    film_manifest = LemoOpuscarFilmBuilder.build_film_package(
                        prod_dir=Path(prod_ws.prod_dir),
                        job_id=job_id,
                        topic=selected_topic.title,
                        script_data=script_data or {"title": selected_topic.title, "hook": best_hook.hook_text},
                        storyboard=storyboard_beats,
                        section_timings={"sections": section_timings} if isinstance(section_timings, list) else (section_timings or {}),
                        duration_sec=dur,
                        category=selected_topic.category,
                    )
                    production_observability.update_rendering(
                        job_id,
                        current_operation="Rendering frames via Lemo-Opuscar video.mjs",
                        current_beat_index=max(1, len(plan.scenes) // 2),
                        current_beat_id="lemo_render",
                        completed_scenes=max(1, len(plan.scenes) // 2),
                        total_scenes=len(plan.scenes),
                        progress_pct=55.0,
                        ffmpeg_status="LEMO_OPUSCAR_RENDERING",
                    )
                    lemo_adapter = LemoOpuscarAdapter()
                    silent_lemo_mp4 = Path(prod_ws.prod_dir) / "lemo_film" / f"silent_{job_id}.mp4"
                    self._render_with_bounded_retries(
                        job_id,
                        lambda: lemo_adapter.render_video(
                            workspace=Path(WORKSPACE),
                            demo_dir=Path(film_manifest["demo_dir"]),
                            output_path=silent_lemo_mp4,
                            fps=30,
                            size=(1080, 1920),
                        ),
                        preserved={
                            "lemo_film_dir": film_manifest["demo_dir"],
                            "voiceover_path": voice_path,
                            "stage_cache_dir": os.path.join(prod_ws.reports_dir, "stage_cache"),
                        },
                        persist_to_db=persist_to_db,
                    )
                    production_observability.update_rendering(
                        job_id,
                        current_operation="Muxing calibrated narration & finalizing 1080x1920 MP4",
                        current_beat_index=len(plan.scenes),
                        current_beat_id="mux",
                        completed_scenes=len(plan.scenes),
                        total_scenes=len(plan.scenes),
                        progress_pct=92.0,
                        ffmpeg_status="FINALIZING_ENCODE",
                    )
                    mux_cmd = [
                        FFMPEG_EXE, "-y",
                        "-i", str(silent_lemo_mp4),
                        "-i", str(voice_path),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                        "-shortest", "-movflags", "+faststart",
                        final_video_path,
                    ]
                    mux_proc = subprocess.run(
                        mux_cmd,
                        capture_output=True,
                        text=True,
                        timeout=180,
                        **hidden_subprocess_kwargs(),
                    )
                    if mux_proc.returncode != 0 or not os.path.isfile(final_video_path):
                        raise RuntimeError(f"FFmpeg audio mux failed after Lemo-Opuscar render: {mux_proc.stderr[-500:]}")

                from backend.app.services.animation_service import probe_video
                probed = probe_video(final_video_path)
                dur = round(float(probed.get("duration", dur)), 2)
                render_telemetry = {
                    "renderer": "lemo-opuscar",
                    "engine": f"Lemo-Opuscar ({PINNED_COMMIT[:10]})",
                    "pinned_commit": PINNED_COMMIT,
                    "fallback_used": False,
                    "visual_engine": visual_engine,
                    "demo_dir": film_manifest["demo_dir"],
                    "beat_count": film_manifest["beat_count"],
                    "unique_asset_count": film_manifest["unique_asset_count"],
                }
                manifest_data = prod_ws.load_manifest() or {}
                manifest_data["renderer"] = "lemo-opuscar"
                manifest_data["pinned_commit"] = PINNED_COMMIT
                manifest_data["fallback_used"] = False
                manifest_data["visual_engine"] = visual_engine
                manifest_data["script_source"] = (script_data or {}).get("script_source", "unknown")
                prod_ws.save_manifest(manifest_data)
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
            self._check_control_signal(job_id, persist_to_db)
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
                winning_thumb_score = getattr(getattr(winning_thumb, "score", None), "composite_score", None)
                production_observability.complete_stage(job_id, "thumbnail_generation", f"Selected winning thumbnail '{winning_headline}' (Score {winning_thumb.score.composite_score:.1f})")
                if persist_to_db:
                    self._update_checkpoint(job_id, "THUMBNAIL", 85.0, {
                        "thumbnail_path": thumb_path,
                        "winning_thumbnail": winning_headline,
                        "thumbnail_score": winning_thumb_score,
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
                    rendering_engine=rendered_engine_name,
                    execution_context=execution_context.value,
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
                execution_context=execution_context.value,
                render_telemetry=locals().get("render_telemetry"),
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
            resolved_visual_style = existing_cp.get("storytelling_style") or "adaptive_multi_style"
            resolved_narrative_archetype = existing_cp.get("narrative_archetype") or "Documentary Short"
            resolved_attrib_path = attrib_json_path or existing_cp.get("attribution_manifest_path") or prod_ws.manifest_path
            resolved_subtitle_path = subtitle_path or existing_cp.get("subtitle_path") or "Burned-in / Embedded in Video"
            resolved_thumb_score = winning_thumb_score if winning_thumb_score is not None else existing_cp.get("thumbnail_score")
            elapsed = time.perf_counter() - t_start
            completion_time_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S IST")

            # Production completes only after the Publishing Queue entry exists
            # (complete_queue_handoff). Other contexts have no queue and complete here.
            requires_queue_handoff = (
                execution_context == ExecutionContext.PRODUCTION
                and not job_id.startswith(("test_", "mock_"))
            )
            if persist_to_db:
                logger.info("[STAGE 12] Recording production state in database...")
                with SessionLocal() as db_session:
                    job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                    if job:
                        if requires_queue_handoff:
                            job.current_stage = "PUBLISHING_QUEUE_HANDOFF"
                            job.progress_pct = 95.0
                        else:
                            job.status = "COMPLETED"
                            job.current_stage = "COMPLETED"
                            job.progress_pct = 100.0
                        job.topic_title = selected_topic.title
                        completed_checkpoint = json.loads(job.checkpoint_data_json or "{}")
                        completed_checkpoint.update({
                            "video_path": final_video_path,
                            "thumbnail_path": thumb_path,
                            "duration_sec": dur,
                            "total_duration_sec": round(elapsed, 2),
                            "topic": selected_topic.title,
                            "video_title": meta.title,
                            "storytelling_style": resolved_visual_style,
                            "narrative_archetype": resolved_narrative_archetype,
                            "start_time": start_time_ist,
                            "completion_time": completion_time_ist,
                            "subtitle_path": resolved_subtitle_path,
                            "attribution_manifest_path": resolved_attrib_path,
                            "thumbnail_score": resolved_thumb_score,
                            "hook": best_hook.hook_text,
                            "winning_thumbnail": winning_headline,
                        })
                        job.checkpoint_data_json = json.dumps(completed_checkpoint)
                        if not requires_queue_handoff:
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
                        "video_title": meta.title,
                        "duration_sec": dur,
                        "total_duration_sec": round(elapsed, 2),
                        "video_path": final_video_path,
                        "thumbnail_path": thumb_path,
                    }, f, indent=2)
                prod_ws.register_artifact("STAGE_12_VALIDATION_SUMMARY", "report", val_cp_path, copy_into_workspace=False)

            # Record category balance & ledger (TopicLedger strictly skipped outside PRODUCTION)
            if is_full_live and not job_id.startswith("test_") and not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    CategoryDistributionTracker().record_selection(
                        category=selected_topic.category,
                        topic_title=selected_topic.title,
                        execution_context=execution_context.value,
                        run_id=job_id,
                    )
                except Exception as dist_exc:
                    logger.warning("CategoryDistributionTracker update warning: %s", dist_exc)

            rec = ContentRecord(
                topic_id=f"topic_{job_id}",
                topic_title=selected_topic.title,
                opening_hook=best_hook.hook_text,
                main_subject=selected_topic.title,
                key_entities=selected_topic.key_entities,
                script_fingerprint=script_fp,
                category=selected_topic.category,
                visual_style=resolved_visual_style,
                created_at=datetime.utcnow().isoformat() + "Z",
                publication_status="produced",
            )
            self.ledger.record_content(rec, allow_override=False, execution_context=execution_context.value)
            if identity_registry is not None:
                try:
                    identity_registry.update(
                        job_id,
                        video_sha256=compute_file_sha256(final_video_path) if os.path.isfile(final_video_path) else "",
                        renderer_config_json={"renderer": (prod_ws.load_manifest() or {}).get("renderer", ""),
                                              "visual_style": resolved_visual_style},
                        status="PRODUCED",
                    )
                except Exception as reg_exc:   # identity bookkeeping must never fail a finished video
                    logger.warning("Creative identity update after render failed: %s", reg_exc)

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
                        "category": selected_topic.category,
                        "video_path": final_video_path,
                        "thumbnail_path": thumb_path,
                        "manifest_path": prod_ws.manifest_path,
                    },
                )

            # -------------------------------------------------------------
            # Stage 14 (pre-notification): YouTube Publishing Queue Enqueueing
            # -------------------------------------------------------------
            # QC-approved videos only ever enter the queue as private. Publication
            # always needs a user-selected time (youtube_service.edit_and_schedule).
            publishing_status = "enqueued_private"

            if requires_queue_handoff:
                logger.info("[STAGE 14] Enqueueing to YouTube Publishing Queue in PRODUCTION context...")
                from backend.app.scheduler.schedule_profiles import get_active_profile
                active_prod_prof = get_active_profile()
                prod_cycle_id = "_".join(job_id.split("_")[:2]) if "_" in job_id else job_id
                matched_cycle = next((c for c in active_prod_prof.cycles if job_id.startswith(c.cycle_id + "_")), None)
                if matched_cycle:
                    prod_cycle_id = matched_cycle.cycle_id
                prod_sched_ver = int(getattr(active_prod_prof, "schedule_version", 1))
                meta_payload = asdict(meta)
                meta_payload["_schedule_context"] = {
                    "production_cycle_id": prod_cycle_id,
                    "production_schedule_version": prod_sched_ver,
                    "publishing_mode": "user_controlled_schedule",
                }
                try:
                    from backend.app.services.lifecycle_events import record_lifecycle_event
                    record_lifecycle_event("VIDEO_GENERATED", "QC_PENDING", cycle_id=prod_cycle_id, job_id=job_id, details={"video_path": final_video_path})
                    record_lifecycle_event("QC_PASSED", "QC_PASSED", cycle_id=prod_cycle_id, job_id=job_id)
                except Exception:
                    pass
                # Persist everything the handoff needs first, so a failed queue insert
                # can be retried on its own without regenerating the video.
                handoff = {
                    "cycle_id": prod_cycle_id,
                    "topic_title": selected_topic.title,
                    "video_path": final_video_path,
                    "thumbnail_path": thumb_path,
                    "metadata_json": json.dumps(meta_payload),
                    "video_hash": compute_file_sha256(final_video_path),
                }
                if persist_to_db:
                    self._update_checkpoint(job_id, "PUBLISHING_QUEUE_HANDOFF", 95.0, {"queue_handoff": handoff})
                    ledger_id = self.complete_queue_handoff(job_id)
                else:
                    ledger_id = self._insert_queue_entry(handoff)
                publishing_status = "queued_for_user_scheduling"
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

            # -------------------------------------------------------------
            # Stage 13: Canonical Notification Dispatch
            # -------------------------------------------------------------
            logger.info("[STAGE 13] Coordinating canonical completion notification...")
            canonical_payload = {
                "job_id": job_id,
                "production_id": prod_ws.production_id,
                "production_dir": prod_ws.prod_dir,
                "manifest_path": prod_ws.manifest_path,
                "status": "COMPLETED",
                "topic": selected_topic.title,
                "video_title": meta.title,
                "storytelling_style": resolved_visual_style,
                "narrative_archetype": resolved_narrative_archetype,
                "start_time": start_time_ist,
                "completion_time": completion_time_ist,
                "total_duration_sec": round(elapsed, 2),
                "video_path": final_video_path,
                "thumbnail_path": thumb_path,
                "subtitle_path": resolved_subtitle_path,
                "attribution_manifest_path": resolved_attrib_path,
                "thumbnail_score": resolved_thumb_score,
                "duration_sec": dur,
                "beat_count": len(visual_beats),
                "elapsed_sec": round(elapsed, 1),
                "qc_checks": qc_checks_report,
                "integrity_report": integrity_audit_path,
                "publishing_status": publishing_status,
            }
            if suppress_orchestrator_completion_email.get():
                logger.info("[STAGE 13] Orchestrator notification deferred to scheduler canonical summary report.")
                notif_res = {"status": "DEFERRED_TO_CANONICAL_CYCLE_REPORT", "job_id": job_id}
            elif (
                execution_context == ExecutionContext.PRODUCTION
                and not job_id.startswith(("test_", "mock_"))
                and not isinstance(self.notifier.send_success, NonCallableMock)
            ):
                from backend.app.services.email_monitoring_service import email_monitoring_service
                inferred_cycle = "_".join(job_id.split("_")[:2]) if job_id.startswith("cycle_") else "manual_production"
                notif_res = email_monitoring_service.send_cycle_summary_report(
                    cycle_id=inferred_cycle,
                    job_id=job_id,
                    result=canonical_payload,
                )
            else:
                notif_res = self.notifier.send_success(
                    video_title=meta.title or selected_topic.title,
                    topic=selected_topic.title,
                    storytelling_style=resolved_visual_style,
                    duration_sec=dur,
                    video_path=final_video_path,
                    thumbnail_path=thumb_path,
                    qc_status="PASSED",
                    job_id=job_id,
                    execution_context=execution_context,
                )

            production_observability.complete_production(
                job_id,
                f"Completed '{selected_topic.title}' in {elapsed:.1f}s ({len(visual_beats)} beats)",
            )
            logger.info("=== MASTER PIPELINE COMPLETED IN %.1fs ===", elapsed)
            canonical_payload["notifications"] = notif_res
            return canonical_payload

        except ProductionGracefulStop:
            logger.warning("[GRACEFUL STOP] Halting pipeline cleanly at stage boundary for job %s", job_id)
            if persist_to_db:
                with SessionLocal() as db_session:
                    job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                    if job:
                        job.status = "INTERRUPTED"
                        job.recovery_reason = "Operator requested graceful stop"
                        job.heartbeat_at = datetime.utcnow()
                        db_session.commit()
            try:
                from backend.app.services import control_center_service
                control_center_service.set_production_control_state("IDLE", reason="Graceful stop complete")
            except Exception:
                pass
            return {
                "job_id": job_id,
                "status": "INTERRUPTED",
                "stopped_gracefully": True,
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

            # Scheduled runs: the scheduler applies retry_policy and sends one canonical
            # report for the terminal outcome only, so no per-attempt failure email here.
            if not suppress_orchestrator_completion_email.get():
                self.notifier.send_failure(
                    topic="Autonomous Generation",
                    stage="orchestration_pipeline",
                    error_summary=str(e),
                    job_id=job_id,
                    manual_intervention_required=True,
                    execution_context=execution_context,
                )
            raise e

    def _insert_queue_entry(self, handoff: Dict) -> int:
        """Idempotent Publishing Queue insert (private, no publication time)."""
        record = PublicationRecord(
            id=None,
            topic_title=handoff["topic_title"],
            video_path=handoff["video_path"],
            thumbnail_path=handoff.get("thumbnail_path"),
            metadata_json=handoff["metadata_json"],
            youtube_video_id=None,
            upload_status="pending_queue",
            visibility="private",
            video_hash=handoff["video_hash"],
            upload_timestamp=None,
            scheduled_timestamp=None,
        )
        return self.publishing_engine.db.record_publication(record, execution_context=ExecutionContext.PRODUCTION)

    def complete_queue_handoff(self, job_id: str) -> int:
        """
        Production completion boundary:
        VIDEO_GENERATED -> QC_PASSED -> PUBLISHING_QUEUE_ENTRY_CREATED -> PRODUCTION_CYCLE_COMPLETED.

        Reads the queue_handoff payload saved in the job checkpoint, inserts the queue
        entry idempotently (same video hash returns the existing ledger id), then marks
        the job COMPLETED. Never renders, uploads, or waits for the user. Safe to call
        again after a failure: the recovery loop does exactly that for HANDOFF_PENDING.
        """
        from backend.app.scheduler import retry_policy
        from backend.app.services.lifecycle_events import record_lifecycle_event

        with SessionLocal() as db_session:
            job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if job is None:
                raise ValueError(f"Job {job_id} not found for queue handoff.")
            cp = json.loads(job.checkpoint_data_json or "{}")
            handoff = cp.get("queue_handoff")
            if not handoff:
                raise ValueError(f"Job {job_id} has no queue_handoff checkpoint.")
            if cp.get("queue_ledger_id") and job.status == "COMPLETED":
                return int(cp["queue_ledger_id"])
        if not os.path.isfile(handoff["video_path"]):
            raise FileNotFoundError(f"Rendered video missing for queue handoff: {handoff['video_path']}")
        if compute_file_sha256(handoff["video_path"]) != handoff["video_hash"]:
            raise ValueError("Rendered video changed after QC; refusing to queue it.")

        ledger_id = self._insert_queue_entry(handoff)
        cycle_id = handoff.get("cycle_id")
        record_lifecycle_event(
            "PUBLISHING_QUEUE_ENTRY_CREATED", "COMPLETED",
            cycle_id=cycle_id, job_id=job_id, details={"ledger_id": ledger_id},
        )
        publishing = self._auto_publish_after_qc(job_id, ledger_id, cp, cycle_id)
        with SessionLocal() as db_session:
            job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            cp = json.loads(job.checkpoint_data_json or "{}")
            cp["queue_ledger_id"] = ledger_id
            if publishing is not None:
                cp.setdefault("cycle_state", {})["publishing"] = publishing
            job.checkpoint_data_json = json.dumps(cp)
            job.status = "COMPLETED"
            job.current_stage = "COMPLETED"
            job.progress_pct = 100.0
            job.completed_at = job.completed_at or datetime.utcnow()
            retry_policy.clear_failure_state(job)
            db_session.commit()
        record_lifecycle_event(
            "PRODUCTION_CYCLE_COMPLETED", "COMPLETED",
            cycle_id=cycle_id, job_id=job_id,
            details={"ledger_id": ledger_id, "video_path": handoff["video_path"]},
        )
        logger.info("[STAGE 14] Job %s completed: Publishing Queue entry #%s created.", job_id, ledger_id)
        return ledger_id

    def _auto_publish_after_qc(self, job_id: str, ledger_id: int, cp: Dict, cycle_id: Optional[str]) -> Optional[Dict]:
        """
        Under the explicit 'automatic_private_scheduled' policy: upload PRIVATE right after QC
        and schedule YouTube-native publishAt at the cycle's target time.

        VIDEO_GENERATED -> QC_PASSED -> YOUTUBE_UPLOAD_CONFIRMED -> PUBLICATION_SCHEDULE_CONFIRMED
        -> PRODUCTION_CYCLE_COMPLETED. Returns None under manual_approval (the video waits in
        the Publishing Queue). Raises PublishingStepError when an upload/scheduling/thumbnail
        step must be retried; the scheduler then retries only this publishing step (job
        HANDOFF_PENDING), never the render.
        """
        from backend.app.services.control_center_service import is_auto_private_scheduling_enabled
        from backend.app.services.lifecycle_events import record_lifecycle_event
        from backend.app.services.youtube_service import YouTubePublisherService

        if not is_auto_private_scheduling_enabled():
            return None
        cs = cp.get("cycle_state") or {}
        target = cs.get("target_publication_at_utc")
        result = YouTubePublisherService().publish_after_qc(ledger_id, target)
        state = result["state"]
        summary = {"state": state, "ledger_id": ledger_id, "target_publication_at_utc": target,
                   "video_id": result.get("video_id"), "reason": result.get("reason") or result.get("error")}
        if result.get("video_id"):
            record_lifecycle_event("YOUTUBE_UPLOAD_CONFIRMED", "RUNNING", cycle_id=cycle_id, job_id=job_id,
                                   details={"ledger_id": ledger_id, "video_id": result["video_id"], "privacy": "private"})
        if state == "SCHEDULED":
            record_lifecycle_event("PUBLICATION_SCHEDULE_CONFIRMED", "RUNNING", cycle_id=cycle_id, job_id=job_id,
                                   details={"ledger_id": ledger_id, "publish_at_utc": target})
            return summary
        if state in ("NEEDS_PUBLICATION_TIME", "NEEDS_REVIEW", "ALREADY_PUBLIC"):
            # Production is done; publishing needs the operator. Never published early.
            record_lifecycle_event("PUBLICATION_TIME_REQUIRED", "COMPLETED", cycle_id=cycle_id, job_id=job_id,
                                   details=summary)
            logger.warning("[PUBLISHING] Job %s: %s (%s).", job_id, state, summary["reason"])
            return summary
        raise PublishingStepError(f"Publishing step pending ({state}): {summary['reason'] or 'retrying'}")

    def process_publishing_queue(self, execute_upload: bool = False) -> Dict:
        """
        Background publishing-queue check. Never publishes on its own: with
        execute_upload=True it only uploads items the user already approved AND gave a
        future publication time, always as private with YouTube publishAt.
        """
        logger.info("[PUBLISHING QUEUE] Checking enqueued publications...")
        quota_used = self.publishing_engine.db.get_quota_used_today()
        logger.info("Daily YouTube quota consumed: %d units", quota_used)

        if not execute_upload:
            logger.info("Publishing is user-controlled. Background upload withheld.")
            return {
                "status": "withheld_safety_gate",
                "publishing_mode": "manual_approval",
                "quota_used": quota_used,
                "message": "Videos remain in the Publishing Queue until the user schedules or publishes them.",
            }

        # Only process items that have an explicit user-scheduled future timestamp
        conn = sqlite3.connect(self.publishing_engine.db.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic_title, video_path, thumbnail_path, metadata_json, scheduled_timestamp
            FROM publishing_ledger
            WHERE upload_status = 'approved' AND scheduled_timestamp IS NOT NULL AND scheduled_timestamp != ''
            ORDER BY id ASC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()

        if not row:
            return {
                "status": "awaiting_user_schedule",
                "message": "Videos remain in Publishing Queue until user schedules or publishes them.",
            }

        p_id, title, v_path, t_path, m_json, sched_ts = row
        if not v_path or not os.path.isfile(v_path):
            return {
                "status": "blocked_missing_artifact",
                "ledger_id": p_id,
                "message": f"Video artifact missing on disk: {v_path}",
            }
        meta_dict = json.loads(m_json)
        meta_dict.pop("_schedule_context", None)
        metadata = YouTubeMetadata(**meta_dict)

        publish_at_iso: Optional[str] = None
        if sched_ts and ("T" in str(sched_ts)):
            try:
                parsed_dt = datetime.fromisoformat(str(sched_ts).replace("Z", "+00:00"))
                if parsed_dt.tzinfo is not None and parsed_dt > datetime.now(parsed_dt.tzinfo):
                    publish_at_iso = str(sched_ts)
            except Exception:
                publish_at_iso = None
        if not publish_at_iso:
            return {
                "status": "awaiting_user_schedule",
                "ledger_id": p_id,
                "message": "A valid future user-selected publication time is required.",
            }

        upload_visibility = "private"
        res = self.publishing_engine.upload_short(
            video_path=v_path,
            topic_title=title,
            metadata=metadata,
            thumbnail_path=t_path,
            visibility=upload_visibility,
            ledger_id=p_id,
            allow_public_bypass=False,
            publish_at_iso=publish_at_iso,
            auto_policy_authorized=False,
        )
        return res


# Global orchestrator instance
master_orchestrator = CrazzyDocsMasterOrchestrator()
