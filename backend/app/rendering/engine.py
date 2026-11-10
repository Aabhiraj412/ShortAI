"""
Master Hybrid Dual-Engine Coordinator.

Directs multi-modal video production across Headless Chromium (HTML5 Canvas)
and Python Canvas (Pillow Ken Burns & Split Screen), orchestrating rendering,
procedural sound synthesis, and final FFmpeg composition.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

from backend.app.rendering.compositing.compositor import UniversalCompositor
from backend.app.rendering.engines.browser_canvas.runner import BrowserCanvasRunner
from backend.app.rendering.engines.python_canvas.photo_scene import PhotoSceneRenderer
from backend.app.rendering.engines.python_canvas.split_screen import SplitScreenRenderer
from backend.app.rendering.types import (
    EngineType,
    RenderTelemetry,
    SegmentResult,
    StoryboardPlan,
    VisualModality,
)

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
STYLES_DIR = os.path.join(WORKSPACE_ROOT, "backend", "app", "rendering", "styles")


class HybridVisualEngine:
    """Master rendering director dispatching scenes to their optimal rendering engine."""

    def __init__(self, workspace_root: str = WORKSPACE_ROOT):
        self.workspace_root = workspace_root
        self.browser_runner = BrowserCanvasRunner()
        self.photo_renderer = PhotoSceneRenderer()
        self.split_renderer = SplitScreenRenderer()
        self.compositor = UniversalCompositor()

    def _get_adapter_url(self, modality: VisualModality) -> str:
        """Resolves the local file URL for a given browser canvas style adapter."""
        mapping = {
            VisualModality.POP_INFO: "pop_info",
            VisualModality.DATA_STORY: "data_story",
            VisualModality.CHALK_TALK: "chalk_talk",
            VisualModality.CREATOR_MOTION: "creator_motion_graphics",
        }
        folder = mapping.get(modality)
        if not folder:
            raise ValueError(f"No browser adapter found for modality: {modality}")

        adapter_path = os.path.join(STYLES_DIR, folder, "adapter.html")
        if not os.path.exists(adapter_path):
            raise FileNotFoundError(f"Adapter HTML not found: {adapter_path}")

        norm_path = os.path.abspath(adapter_path).replace("\\", "/")
        return f"file:///{norm_path}"

    async def render_scene_beat(
        self,
        beat_idx: int,
        beat,
        output_dir: str,
    ) -> SegmentResult:
        """Renders an individual scene beat using its designated engine."""
        seg_mp4 = os.path.join(output_dir, f"beat_{beat_idx:02d}_{beat.id}.mp4")
        sfx_wav = os.path.join(output_dir, f"beat_{beat_idx:02d}_{beat.id}_sfx.wav")

        logger.info("Executing Beat %02d [%s] via %s (%0.1fs)...", beat_idx, beat.id, beat.engine.value, beat.duration)

        if beat.engine == EngineType.BROWSER_CANVAS:
            adapter_url = self._get_adapter_url(beat.modality)
            return await self.browser_runner.render_scene_video(
                scene_id=beat.id,
                adapter_url=adapter_url,
                duration=beat.duration,
                out_mp4_path=seg_mp4,
                sfx_out_wav=sfx_wav,
            )

        elif beat.modality == VisualModality.REAL_PHOTO or beat.modality == VisualModality.CINEMATIC_STILL:
            img_path = beat.params.get("image_path")
            if not img_path or not os.path.exists(img_path):
                # Fallback to high-res style background, then legacy if present
                new_fallback = os.path.join(self.workspace_root, "assets", "backgrounds", "pop_info", "bg_warm_cream.png")
                legacy_fallback = os.path.join(self.workspace_root, "assets", "backgrounds", "rendered", "bg_space.png")
                img_path = new_fallback if os.path.exists(new_fallback) else legacy_fallback

            return self.photo_renderer.render_photo_short(
                scene_id=beat.id,
                image_path=img_path,
                duration=beat.duration,
                out_mp4_path=seg_mp4,
                title_text=beat.params.get("title"),
                subtitle_text=beat.params.get("subtitle"),
            )

        elif beat.modality == VisualModality.SPLIT_SCREEN:
            top_img = beat.params.get("top_image")
            if not top_img or not os.path.exists(top_img):
                new_top = os.path.join(self.workspace_root, "assets", "backgrounds", "data_story", "bg_dark_slate_grid.png")
                legacy_top = os.path.join(self.workspace_root, "assets", "backgrounds", "rendered", "bg_space.png")
                top_img = new_top if os.path.exists(new_top) else legacy_top

            bot_img = beat.params.get("bottom_image")
            if not bot_img or not os.path.exists(bot_img):
                new_bot = os.path.join(self.workspace_root, "assets", "backgrounds", "creator", "bg_studio_black.png")
                legacy_bot = os.path.join(self.workspace_root, "assets", "backgrounds", "rendered", "bg_tech_lab.png")
                bot_img = new_bot if os.path.exists(new_bot) else legacy_bot

            return self.split_renderer.render_split_short(
                scene_id=beat.id,
                top_image_path=top_img,
                bottom_image_path=bot_img,
                duration=beat.duration,
                out_mp4_path=seg_mp4,
                top_label=beat.params.get("top_label", "PERSPECTIVE A"),
                bottom_label=beat.params.get("bottom_label", "PERSPECTIVE B"),
            )

        else:
            raise ValueError(f"Unsupported visual beat configuration: {beat}")

    async def render_storyboard(
        self,
        plan: StoryboardPlan,
        output_final_mp4: str,
        work_dir: Optional[str] = None,
    ) -> Dict[str, any]:
        """Executes an entire multi-beat storyboard plan and produces the finished Short."""
        t_global_start = time.time()
        if not work_dir:
            work_dir = os.path.join(self.workspace_root, "cache", f"render_{int(t_global_start)}")
        os.makedirs(work_dir, exist_ok=True)

        logger.info("=== Starting Multi-Modal Short Render: '%s' (%.1fs, %d beats) ===", plan.title, plan.total_duration, len(plan.scenes))

        segment_results: List[SegmentResult] = []
        video_paths: List[str] = []
        sfx_paths: List[str] = []
        total_frames = 0

        try:
            for i, beat in enumerate(plan.scenes):
                res = await self.render_scene_beat(i + 1, beat, work_dir)
                if not res.success:
                    raise RuntimeError(f"Beat {i+1} ({beat.id}) failed: {res.error_message}")

                segment_results.append(res)
                video_paths.append(res.video_path)
                if res.sfx_audio_path and os.path.exists(res.sfx_audio_path):
                    sfx_paths.append(res.sfx_audio_path)
                if res.telemetry:
                    total_frames += res.telemetry.frames_rendered

            # Concatenate video segments
            stitched_video = os.path.join(work_dir, "stitched_raw.mp4")
            self.compositor.concatenate_segments(video_paths, stitched_video)

            # Final audio mix & export
            narrative_mp4 = os.path.join(work_dir, "narrative_short.mp4") if plan.include_crazzydocs_outro else output_final_mp4
            final_path = self.compositor.mix_and_finalize_short(
                video_path=stitched_video,
                voiceover_audio=plan.voiceover_path,
                sfx_audios=sfx_paths,
                music_audio=plan.music_track,
                output_final_mp4=narrative_mp4,
                ducking_db=plan.ducking_db,
                subtitles_ass_path=plan.subtitles_ass_path,
            )

            # Append CrazzyDocs Branded Outro if requested
            if plan.include_crazzydocs_outro:
                default_outro = os.path.join(self.workspace_root, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")
                outro_path = plan.crazzydocs_outro_path or default_outro
                if os.path.exists(outro_path):
                    final_path = self.compositor.append_branded_outro(
                        main_video_path=narrative_mp4,
                        outro_video_path=outro_path,
                        output_final_path=output_final_mp4,
                        subtitles_ass_path=plan.subtitles_ass_path,
                    )
                else:
                    logger.warning("CrazzyDocs outro '%s' not found, using narrative video.", outro_path)
                    final_path = narrative_mp4

            t_global_end = time.time()
            total_elapsed = t_global_end - t_global_start
            effective_fps = total_frames / total_elapsed if total_elapsed > 0 else 0.0

            telemetry = {
                "title": plan.title,
                "duration_sec": plan.total_duration,
                "total_frames": total_frames,
                "render_time_sec": round(total_elapsed, 2),
                "effective_fps": round(effective_fps, 2),
                "output_path": final_path,
                "segments": [
                    {
                        "beat_id": s.scene_id,
                        "duration": s.duration,
                        "fps": s.telemetry.fps if s.telemetry else 0.0,
                        "engine": s.telemetry.engine if s.telemetry else "Unknown",
                    }
                    for s in segment_results
                ],
            }

            logger.info("=== Finished Render '%s': %d frames in %.2fs (%.1f effective FPS) ===", plan.title, total_frames, total_elapsed, effective_fps)
            return telemetry

        finally:
            # Terminate browser runner gracefully
            self.browser_runner.stop_browser()
