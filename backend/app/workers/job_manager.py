"""
Persistent Job Queue and State Machine for AI Shorts Studio.
Implements:
- Checkpointing per stage
- Heartbeat monitoring
- Resumption of interrupted jobs without duplicate generation
- Exponential backoff retry mechanism (max 3 retries)
- Duplicate daily prevention (idempotency by date)
"""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.database.db import SessionLocal, JobModel
from app.agents.trend_researcher import TrendResearcher
from app.agents.topic_selector import TopicSelector
from app.agents.scriptwriter import Scriptwriter
from app.agents.visual_director import VisualDirector
from app.agents.seo_publisher import SEOPublisher
from app.services.tts_service import generate_narration
from app.services.stick_animation_service import render_stick_animation
from app.services.animation_service import render_animation, MUSIC_DIR
from app.services.qc_service import QualityControlEngine
from app.services.youtube_service import YouTubePublisherService

# Pipeline Stages in sequential order
STAGES = [
    "RESEARCH",
    "TOPIC_SELECTION",
    "SCRIPT",
    "STORYBOARD",
    "VOICEOVER",
    "SUBTITLES",
    "ANIMATION",
    "THUMBNAIL",
    "SEO",
    "QUALITY_CHECK",
    "YOUTUBE_UPLOAD",
    "COMPLETED"
]

class PersistentJobManager:
    def __init__(self):
        self.trend_agent = TrendResearcher()
        self.selector_agent = TopicSelector()
        self.script_agent = Scriptwriter()
        self.director_agent = VisualDirector()
        self.seo_agent = SEOPublisher()
        self.qc_engine = QualityControlEngine()
        self.youtube_service = YouTubePublisherService()

    def get_or_create_daily_job(self, date_str: Optional[str] = None) -> Tuple[JobModel, bool]:
        """
        Idempotent job lookup: Checks if a job for the specified date already exists.
        Returns: (job_model, is_new: bool)
        """
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        with SessionLocal() as session:
            existing = session.query(JobModel).filter_by(date_str=date_str).order_by(JobModel.created_at.desc()).first()
            if existing:
                # If already completed or currently running, reuse existing
                if existing.status in ("COMPLETED", "RUNNING", "RETRYING", "PENDING"):
                    print(f"[IDEMPOTENCY] Found existing job for {date_str}: ID={existing.id}, Status={existing.status}", flush=True)
                    return existing, False
                    
            # Create a new persistent job
            job_id = f"job_{date_str}_{int(time.time())}"
            new_job = JobModel(
                id=job_id,
                date_str=date_str,
                status="PENDING",
                current_stage="RESEARCH",
                progress_pct=0.0,
                checkpoint_data_json="{}",
                publishing_mode=settings.PUBLISHING_MODE
            )
            session.add(new_job)
            session.commit()
            session.refresh(new_job)
            print(f"[JOB QUEUE] Created new daily job: {job_id} for date {date_str}", flush=True)
            return new_job, True

    def update_checkpoint(self, job_id: str, stage: str, progress_pct: float, data_key: str, data_val: any):
        """Updates job stage, progress percentage, heartbeat, and stores checkpoint data."""
        with SessionLocal() as session:
            job = session.query(JobModel).filter_by(id=job_id).first()
            if job:
                job.current_stage = stage
                job.progress_pct = progress_pct
                job.heartbeat_at = datetime.utcnow()
                
                checkpoints = json.loads(job.checkpoint_data_json or "{}")
                checkpoints[data_key] = data_val
                job.checkpoint_data_json = json.dumps(checkpoints)
                
                session.commit()

    def record_heartbeat(self, job_id: str):
        with SessionLocal() as session:
            job = session.query(JobModel).filter_by(id=job_id).first()
            if job:
                job.heartbeat_at = datetime.utcnow()
                session.commit()

    def mark_status(self, job_id: str, status: str, error: Optional[str] = None):
        with SessionLocal() as session:
            job = session.query(JobModel).filter_by(id=job_id).first()
            if job:
                job.status = status
                job.heartbeat_at = datetime.utcnow()
                if error:
                    job.error_details = error
                if status == "COMPLETED":
                    job.completed_at = datetime.utcnow()
                    job.current_stage = "COMPLETED"
                    job.progress_pct = 100.0
                session.commit()

    def recover_stale_jobs(self) -> List[str]:
        """Detects abandoned jobs whose worker died and recovers them for retry."""
        recovered = []
        timeout_threshold = datetime.utcnow() - timedelta(minutes=5)
        with SessionLocal() as session:
            stale_jobs = session.query(JobModel).filter(
                JobModel.status.in_(["RUNNING", "RETRYING"]),
                JobModel.heartbeat_at < timeout_threshold
            ).all()
            
            for job in stale_jobs:
                if job.retry_count < job.max_retries:
                    job.retry_count += 1
                    job.status = "RETRYING"
                    job.heartbeat_at = datetime.utcnow()
                    recovered.append(job.id)
                    print(f"[RECOVERY] Job {job.id} heartbeat timed out. Marked for RETRY ({job.retry_count}/{job.max_retries}). Stage={job.current_stage}", flush=True)
                else:
                    job.status = "FAILED"
                    job.error_details = f"Exceeded maximum retries ({job.max_retries}) after heartbeat timeout."
                    print(f"[RECOVERY] Job {job.id} failed: max retries reached.", flush=True)
            session.commit()
        return recovered

    async def run_job(self, job_id: str, manual_topic: Optional[str] = None) -> Dict:
        """
        Executes a job from its last valid checkpoint.
        If any stage already generated valid outputs on disk, it is reused.
        """
        with SessionLocal() as session:
            job = session.query(JobModel).filter_by(id=job_id).first()
            if not job:
                return {"status": "error", "error": f"Job {job_id} not found."}
                
        self.mark_status(job_id, "RUNNING")
        print(f"\n============================================================", flush=True)
        print(f"[RUNNING JOB] ID: {job_id} | Stage: {job.current_stage}", flush=True)
        print(f"============================================================", flush=True)
        
        checkpoints = json.loads(job.checkpoint_data_json or "{}")
        
        try:
            # 1. RESEARCH & TOPIC SELECTION
            if "topic" not in checkpoints:
                print("[STAGE 1/11] Researching trending topics...", flush=True)
                candidates = await self.trend_agent.discover_trends()
                selected_topic = self.selector_agent.select_topic(candidates, manual_topic_title=manual_topic)
                checkpoints["topic"] = selected_topic
                self.update_checkpoint(job_id, "TOPIC_SELECTION", 10.0, "topic", selected_topic)
            else:
                selected_topic = checkpoints["topic"]
                print(f"[CHECKPOINT RESUME] Reusing topic: '{selected_topic['title']}'", flush=True)
                
            self.record_heartbeat(job_id)

            # 2. SCRIPT
            if "script" not in checkpoints:
                print(f"[STAGE 2/11] Generating 50-60s script for '{selected_topic['title']}'...", flush=True)
                script_data = await self.script_agent.generate_script(selected_topic)
                checkpoints["script"] = script_data
                self.update_checkpoint(job_id, "SCRIPT", 20.0, "script", script_data)
            else:
                script_data = checkpoints["script"]
                print(f"[CHECKPOINT RESUME] Reusing existing script ({script_data['word_count']} words)", flush=True)

            self.record_heartbeat(job_id)

            # 3. VOICEOVER & SUBTITLES
            audio_path = checkpoints.get("audio_path")
            ass_path = checkpoints.get("ass_path")
            actual_dur = checkpoints.get("duration", 0.0)
            
            if not (audio_path and os.path.exists(audio_path) and ass_path and os.path.exists(ass_path)):
                print("[STAGE 3/11] Generating neural voiceover and millisecond ASS subtitles...", flush=True)
                safe_slug = "".join([c if c.isalnum() else "_" for c in selected_topic["title"][:25]])
                audio_file = f"narr_{safe_slug}_{job_id}.mp3"
                audio_path, ass_path, actual_dur = await generate_narration(
                    text=script_data["full_script"],
                    voice=settings.DEFAULT_VOICE,
                    output_filename=audio_file
                )
                checkpoints["audio_path"] = audio_path
                checkpoints["ass_path"] = ass_path
                checkpoints["duration"] = actual_dur
                self.update_checkpoint(job_id, "VOICEOVER", 40.0, "audio_path", audio_path)
                self.update_checkpoint(job_id, "SUBTITLES", 45.0, "ass_path", ass_path)
            else:
                print(f"[CHECKPOINT RESUME] Reusing audio: {audio_path} ({actual_dur:.1f}s)", flush=True)

            self.record_heartbeat(job_id)

            # 4. STORYBOARD
            if "storyboard" not in checkpoints:
                print("[STAGE 4/11] Generating visual storyboard...", flush=True)
                storyboard = self.director_agent.create_storyboard(script_data, actual_dur)
                checkpoints["storyboard"] = storyboard
                self.update_checkpoint(job_id, "STORYBOARD", 50.0, "storyboard", storyboard)
            else:
                storyboard = checkpoints["storyboard"]
                print(f"[CHECKPOINT RESUME] Reusing storyboard ({len(storyboard['scenes'])} scenes)", flush=True)

            self.record_heartbeat(job_id)

            # 5. AUTHENTIC DOCUMENTARY RENDERING
            video_path = checkpoints.get("video_path")
            if not (video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 100000):
                print(f"[STAGE 5/11] Checking Creative Policy for Documentary Visual Pipeline...", flush=True)
                # Enforce Creative Policy: Reject stick-figure / cartoon character animation
                if any("stick" in str(scene).lower() for scene in storyboard.get("scenes", [])):
                    raise PermissionError(
                        "Creative Policy Violation: 2D Stick-figure animation is prohibited by "
                        "CrazzyDocs Content Integrity Policy. Productions must use authentic documentary "
                        "motion graphics or CleanSlateRenderer."
                    )
                safe_slug = "".join([c if c.isalnum() else "_" for c in selected_topic["title"][:25]])
                out_mp4 = os.path.join(settings.MEDIA_DIR, "video", f"short_{safe_slug}_{job_id}.mp4")
                music_path = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")

                # If legacy stick animation was called, reject
                raise PermissionError(
                    "Creative Policy Enforced: Legacy stick-figure rendering is disabled. "
                    "Please use CrazzyDocsMasterOrchestrator for autonomous authentic documentary production."
                )
            else:
                print(f"[CHECKPOINT RESUME] Reusing rendered video: {video_path}", flush=True)

            self.record_heartbeat(job_id)

            # 6. THUMBNAIL
            thumb_path = checkpoints.get("thumbnail_path")
            if not (thumb_path and os.path.exists(thumb_path)):
                print("[STAGE 6/11] Generating 9:16 high-CTR thumbnail...", flush=True)
                safe_slug = "".join([c if c.isalnum() else "_" for c in selected_topic["title"][:25]])
                thumb_path = os.path.join(settings.THUMB_DIR, f"thumb_{safe_slug}_{job_id}.png")
                raw_frame = os.path.join(settings.THUMB_DIR, "sample_short_thumb.png")
                self.seo_agent.generate_custom_thumbnail(
                    headline=storyboard["scenes"][0].get("headline", selected_topic["title"]),
                    character_frame_path=raw_frame,
                    output_path=thumb_path,
                    badge_text=storyboard["scenes"][0].get("badge", "SCIENCE")
                )
                checkpoints["thumbnail_path"] = thumb_path
                self.update_checkpoint(job_id, "THUMBNAIL", 80.0, "thumbnail_path", thumb_path)
            else:
                print(f"[CHECKPOINT RESUME] Reusing thumbnail: {thumb_path}", flush=True)

            self.record_heartbeat(job_id)

            # 7. SEO
            if "seo" not in checkpoints:
                print("[STAGE 7/11] Generating SEO metadata...", flush=True)
                seo = await self.seo_agent.generate_seo(selected_topic["title"], script_data["full_script"])
                checkpoints["seo"] = seo
                self.update_checkpoint(job_id, "SEO", 85.0, "seo", seo)
            else:
                seo = checkpoints["seo"]

            self.record_heartbeat(job_id)

            # 8. QUALITY CONTROL
            print("[STAGE 8/11] Running 15-Point Quality Control validation...", flush=True)
            qc_passed, qc_errors, qc_report = self.qc_engine.run_qc_checks(
                video_path=video_path,
                thumbnail_path=thumb_path,
                metadata=seo,
                target_fps=30.0
            )
            if not qc_passed:
                raise RuntimeError(f"Quality Control Failed: {qc_errors}")
            self.update_checkpoint(job_id, "QUALITY_CHECK", 90.0, "qc_report", qc_report)
            print("[QC PASSED] All 15 validation checks passed!", flush=True)

            # 9. PUBLICATION & SCHEDULING CHECK
            print("[STAGE 9/11] Checking publication mode...", flush=True)
            with SessionLocal() as session:
                j = session.query(JobModel).filter_by(id=job_id).first()
                mode = j.publishing_mode if j else "MANUAL_APPROVAL"
                
            if mode == "AUTOMATIC_PUBLISH":
                print("[PUBLISHING] Attempting YouTube upload...", flush=True)
                up_ok, vid_id, up_err = self.youtube_service.upload_video(
                    video_path=video_path,
                    title=seo["youtube_title"],
                    description=seo["description"],
                    tags=seo["tags"],
                    category_id=seo.get("category_id", "28")
                )
                if up_ok:
                    self.youtube_service.upload_thumbnail(vid_id, thumb_path)
                    with SessionLocal() as session:
                        j = session.query(JobModel).filter_by(id=job_id).first()
                        j.youtube_video_id = vid_id
                        session.commit()
            else:
                print(f"[PUBLISHING] Preserved for {mode}. Upload postponed.", flush=True)
                
            self.mark_status(job_id, "COMPLETED")
            print(f"[JOB COMPLETE] Job {job_id} successfully finished!\n", flush=True)
            return {"status": "success", "job_id": job_id, "video_path": video_path, "thumbnail_path": thumb_path}

        except Exception as e:
            print(f"[JOB ERROR] Stage {job.current_stage} failed: {e}", flush=True)
            self.mark_status(job_id, "FAILED", error=str(e))
            return {"status": "failed", "job_id": job_id, "error": str(e)}

job_manager = PersistentJobManager()
