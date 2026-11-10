"""
Automated Pipeline Orchestrator.
Executes the full 15-step autonomous production workflow:
Trend Discovery -> Topic Selection -> Scriptwriting -> Storyboard ->
TTS Audio & ASS Subtitles -> Video Animation Composition -> Thumbnail Generation ->
SEO Metadata -> 15-Point Quality Control -> Scheduled YouTube Publishing.
"""
import asyncio
import json
import os
import time
from datetime import datetime
from typing import Dict, Optional

from app.core.config import settings
from app.database.db import (
    SessionLocal, TopicModel, ScriptModel, StoryboardModel, VideoJobModel, PublicationModel
)
from app.agents.trend_researcher import TrendResearcher
from app.agents.topic_selector import TopicSelector
from app.agents.scriptwriter import Scriptwriter
from app.agents.visual_director import VisualDirector
from app.agents.seo_publisher import SEOPublisher
from app.services.tts_service import generate_narration
from app.services.animation_service import render_animation, MUSIC_DIR
from app.services.qc_service import QualityControlEngine
from app.services.youtube_service import YouTubePublisherService

class ProductionPipeline:
    def __init__(self):
        self.trend_agent = TrendResearcher()
        self.selector_agent = TopicSelector()
        self.script_agent = Scriptwriter()
        self.director_agent = VisualDirector()
        self.seo_agent = SEOPublisher()
        self.qc_engine = QualityControlEngine()
        self.youtube_service = YouTubePublisherService()
        self.is_running = False
        self.current_stage = "idle"
        self.logs = []

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        self.logs.append(entry)
        print(entry, flush=True)

    async def run_pipeline(
        self,
        manual_topic: Optional[str] = None,
        manual_script: Optional[str] = None,
        auto_upload: bool = False
    ) -> Dict:
        """Runs the complete production pipeline end-to-end."""
        if self.is_running:
            return {"status": "error", "message": "Pipeline is already running."}
            
        self.is_running = True
        self.logs = []
        t0 = time.time()
        
        try:
            with SessionLocal() as session:
                # 1. Discover Trends
                self.current_stage = "researching_trends"
                self.log("Stage 1/9: Discovering trending topics...")
                candidates = await self.trend_agent.discover_trends()
                self.log(f"Found {len(candidates)} candidate topics.")
                
                # 2. Select Topic
                self.current_stage = "selecting_topic"
                self.log("Stage 2/9: Selecting highest-potential topic...")
                selected_topic = self.selector_agent.select_topic(
                    candidates, manual_topic_title=manual_topic
                )
                self.log(f"Topic Selected: '{selected_topic['title']}' (Score: {selected_topic.get('score', 0)})")
                
                # Save Topic to DB
                t_model = TopicModel(
                    title=selected_topic["title"],
                    category=selected_topic.get("category", "Science"),
                    score=selected_topic.get("score", 0.0),
                    sources_json=json.dumps(selected_topic.get("sources", [])),
                    is_selected=True
                )
                session.add(t_model)
                session.commit()
                session.refresh(t_model)
                
                # 3. Write Script
                self.current_stage = "writing_script"
                self.log("Stage 3/9: Crafting engaging 50-60s script...")
                if manual_script:
                    script_data = {
                        "hook": manual_script[:80],
                        "body": manual_script,
                        "cta": "Subscribe for more!",
                        "full_script": manual_script,
                        "word_count": len(manual_script.split()),
                        "estimated_duration_sec": len(manual_script.split()) / 2.3,
                        "verified_references": selected_topic.get("sources", [])
                    }
                else:
                    script_data = await self.script_agent.generate_script(selected_topic)
                self.log(f"Script Written: {script_data['word_count']} words (~{script_data['estimated_duration_sec']}s)")
                
                # Save Script to DB
                s_model = ScriptModel(
                    topic_id=t_model.id,
                    title=selected_topic["title"],
                    hook=script_data["hook"],
                    content=script_data["full_script"],
                    word_count=script_data["word_count"],
                    estimated_duration=script_data["estimated_duration_sec"],
                    references_json=json.dumps(script_data.get("verified_references", [])),
                    is_approved=True
                )
                session.add(s_model)
                session.commit()
                session.refresh(s_model)
                
                # 4. Generate Voiceover & Subtitles
                self.current_stage = "generating_audio"
                self.log("Stage 4/9: Generating neural voiceover and millisecond ASS subtitles...")
                safe_slug = "".join([c if c.isalnum() else "_" for c in selected_topic["title"][:25]])
                audio_filename = f"narr_{safe_slug}_{int(time.time())}.mp3"
                audio_path, ass_path, actual_duration = await generate_narration(
                    text=script_data["full_script"],
                    voice=settings.DEFAULT_VOICE,
                    output_filename=audio_filename
                )
                self.log(f"Voiceover Audio: {audio_path} ({actual_duration:.1f}s)")
                
                # 5. Direct Storyboard
                self.current_stage = "directing_storyboard"
                self.log("Stage 5/9: Creating structured visual storyboard...")
                storyboard_data = self.director_agent.create_storyboard(
                    script_data=script_data, total_duration=actual_duration
                )
                self.log(f"Storyboard Created: {len(storyboard_data['scenes'])} scenes.")
                
                sb_model = StoryboardModel(
                    script_id=s_model.id,
                    title=selected_topic["title"],
                    total_duration=actual_duration,
                    scenes_json=json.dumps(storyboard_data["scenes"]),
                    is_approved=True
                )
                session.add(sb_model)
                session.commit()
                session.refresh(sb_model)
                
                # 6. Animation Composition & Video Render
                self.current_stage = "rendering_video"
                self.log(f"Stage 6/9: Rendering 1080x1920 @ 30 FPS video via direct CPU compositor...")
                out_mp4_path = os.path.join(settings.MEDIA_DIR, "video", f"short_{safe_slug}_{int(time.time())}.mp4")
                music_path = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
                
                render_ok = render_animation(
                    storyboard=storyboard_data,
                    audio_path=audio_path,
                    ass_path=ass_path,
                    music_path=music_path,
                    output_mp4_path=out_mp4_path,
                    max_frames=None,
                    fps=30
                )
                if not render_ok:
                    raise RuntimeError("Animation rendering failed.")
                self.log(f"Video Rendered: {out_mp4_path} ({os.path.getsize(out_mp4_path)/(1024*1024):.2f} MB)")
                
                # 7. Generate High-CTR Thumbnail
                self.current_stage = "generating_thumbnail"
                self.log("Stage 7/9: Generating programmatic 9:16 high-CTR thumbnail...")
                thumb_path = os.path.join(settings.THUMB_DIR, f"thumb_{safe_slug}_{int(time.time())}.png")
                raw_frame_path = os.path.join(settings.THUMB_DIR, "sample_short_thumb.png")
                self.seo_agent.generate_custom_thumbnail(
                    headline=storyboard_data["scenes"][0].get("headline", selected_topic["title"]),
                    character_frame_path=raw_frame_path,
                    output_path=thumb_path,
                    badge_text=storyboard_data["scenes"][0].get("badge", "VIRAL SCIENCE")
                )
                self.log(f"Thumbnail Generated: {thumb_path}")
                
                # 8. SEO Metadata Generation
                self.current_stage = "optimizing_seo"
                self.log("Stage 8/9: Generating YouTube SEO metadata...")
                seo_meta = await self.seo_agent.generate_seo(
                    title=selected_topic["title"],
                    script_text=script_data["full_script"],
                    category=selected_topic.get("category", "Science")
                )
                self.log(f"SEO Title: {seo_meta['youtube_title']}")
                
                # Save Video Job to DB
                v_job = VideoJobModel(
                    storyboard_id=sb_model.id,
                    status="completed",
                    video_path=out_mp4_path,
                    thumbnail_path=thumb_path,
                    duration=actual_duration,
                    file_size_mb=round(os.path.getsize(out_mp4_path) / (1024 * 1024), 2)
                )
                session.add(v_job)
                session.commit()
                session.refresh(v_job)
                
                # 9. Quality Control Validation
                self.current_stage = "quality_control"
                self.log("Stage 9/9: Running 15-Point Quality Control Engine...")
                qc_passed, qc_errors, qc_report = self.qc_engine.run_qc_checks(
                    video_path=out_mp4_path,
                    thumbnail_path=thumb_path,
                    metadata=seo_meta,
                    target_fps=30.0
                )
                if not qc_passed:
                    self.log(f"QC ALERT: {len(qc_errors)} checks failed: {qc_errors}")
                    v_job.status = "qc_failed"
                    v_job.error_log = json.dumps(qc_errors)
                    session.commit()
                    return {"status": "qc_failed", "errors": qc_errors, "report": qc_report}
                self.log("QC Status: ALL 15 QUALITY CHECKS PASSED!")
                
                # Publication Record
                pub_record = PublicationModel(
                    video_job_id=v_job.id,
                    title=seo_meta["youtube_title"],
                    description=seo_meta["description"],
                    tags_json=json.dumps(seo_meta["tags"]),
                    status="ready_for_approval" if not auto_upload else "uploading",
                    scheduled_time=settings.DAILY_PUBLISH_TIME_IST
                )
                session.add(pub_record)
                session.commit()
                
                # YouTube Upload (if configured and auto_upload enabled)
                if auto_upload:
                    self.log("Attempting scheduled YouTube upload...")
                    up_ok, vid_id, up_err = self.youtube_service.upload_video(
                        video_path=out_mp4_path,
                        title=seo_meta["youtube_title"],
                        description=seo_meta["description"],
                        tags=seo_meta["tags"],
                        category_id=seo_meta.get("category_id", "28")
                    )
                    if up_ok:
                        pub_record.youtube_video_id = vid_id
                        pub_record.status = "published"
                        pub_record.published_at = datetime.utcnow()
                        self.log(f"Published to YouTube with Video ID: {vid_id}")
                        self.youtube_service.upload_thumbnail(vid_id, thumb_path)
                    else:
                        pub_record.status = "upload_failed"
                        self.log(f"Upload postponed: {up_err}")
                    session.commit()
                    
                total_time = round(time.time() - t0, 1)
                self.log(f"PIPELINE COMPLETE in {total_time}s! Video ready for review/publication.")
                self.current_stage = "completed"
                return {
                    "status": "success",
                    "video_path": out_mp4_path,
                    "thumbnail_path": thumb_path,
                    "duration": actual_duration,
                    "title": seo_meta["youtube_title"],
                    "qc_passed": True,
                    "total_time_seconds": total_time,
                    "logs": self.logs
                }
        except Exception as e:
            self.current_stage = "error"
            self.log(f"FATAL PIPELINE ERROR: {e}")
            return {"status": "error", "error": str(e), "logs": self.logs}
        finally:
            self.is_running = False

pipeline = ProductionPipeline()
