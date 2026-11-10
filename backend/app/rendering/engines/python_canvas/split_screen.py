"""
Python Canvas Engine - Comparative Split-Screen Renderer.

Renders multi-panel comparative layouts (e.g. Before vs. After, 1950 vs. 2026,
Theory vs. Reality) with animated dividing borders and independent camera pushes.
"""

import math
import os
import subprocess
import time
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter
import logging

from backend.app.rendering.types import RenderTelemetry, SegmentResult

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"


class SplitScreenRenderer:
    """Renders dual-panel comparative video scenes with motion in Python Pillow."""

    def __init__(self, ffmpeg_path: str = FFMPEG_DEFAULT_PATH, width: int = 1080, height: int = 1920):
        self.ffmpeg_path = ffmpeg_path
        self.width = width
        self.height = height

    def render_split_short(
        self,
        scene_id: str,
        top_image_path: str,
        bottom_image_path: str,
        duration: float,
        out_mp4_path: str,
        fps: float = 30.0,
        top_label: str = "PERSPECTIVE A",
        bottom_label: str = "PERSPECTIVE B",
    ) -> SegmentResult:
        """Render a stacked split-screen short with animated division."""
        t_start = time.time()
        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)

        top_img = Image.open(top_image_path).convert("RGB")
        bot_img = Image.open(bottom_image_path).convert("RGB")

        panel_h = self.height // 2

        # Scale images to cover each panel
        top_scale = max(self.width / top_img.width, panel_h / top_img.height) * 1.15
        top_scaled = top_img.resize((int(top_img.width * top_scale), int(top_img.height * top_scale)), Image.Resampling.BICUBIC)

        bot_scale = max(self.width / bot_img.width, panel_h / bot_img.height) * 1.15
        bot_scaled = bot_img.resize((int(bot_img.width * bot_scale), int(bot_img.height * bot_scale)), Image.Resampling.BICUBIC)

        total_frames = int(round(duration * fps))

        ffmpeg_cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", "rgb24",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_mp4_path,
        ]

        logger.info("Rendering %d split-screen frames for %s...", total_frames, scene_id)
        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        frames_rendered = 0
        try:
            for i in range(total_frames):
                u = i / total_frames if total_frames > 1 else 0.0
                e = u * u * (3.0 - 2.0 * u)

                frame = Image.new("RGB", (self.width, self.height), (15, 18, 25))

                # Top panel crop & paste (gentle push in)
                top_zoom = 1.0 + 0.06 * e
                tw, th = int(self.width / top_zoom), int(panel_h / top_zoom)
                tcx, tcy = top_scaled.width // 2, top_scaled.height // 2
                top_crop = top_scaled.crop((tcx - tw//2, tcy - th//2, tcx + tw//2, tcy + th//2))
                frame.paste(top_crop.resize((self.width, panel_h), Image.Resampling.BILINEAR), (0, 0))

                # Bottom panel crop & paste (gentle push out)
                bot_zoom = 1.06 - 0.06 * e
                bw, bh = int(self.width / bot_zoom), int(panel_h / bot_zoom)
                bcx, bcy = bot_scaled.width // 2, bot_scaled.height // 2
                bot_crop = bot_scaled.crop((bcx - bw//2, bcy - bh//2, bcx + bw//2, bcy + bh//2))
                frame.paste(bot_crop.resize((self.width, panel_h), Image.Resampling.BILINEAR), (0, panel_h))

                # Animated Glowing Divider Line
                draw = ImageDraw.Draw(frame)
                div_y = panel_h
                draw.line([(0, div_y), (self.width, div_y)], fill=(32, 201, 151), width=6)
                draw.line([(0, div_y - 3), (self.width, div_y - 3)], fill=(15, 18, 25), width=2)
                draw.line([(0, div_y + 3), (self.width, div_y + 3)], fill=(15, 18, 25), width=2)

                # Center "VS" Badge
                badge_w, badge_h = 130, 60
                draw.rectangle([(self.width // 2 - badge_w // 2, div_y - badge_h // 2),
                                (self.width // 2 + badge_w // 2, div_y + badge_h // 2)],
                               fill=(15, 18, 25), outline=(32, 201, 151), width=3)
                draw.text((self.width // 2, div_y), "VS", fill=(255, 255, 255), anchor="mm")

                # Top label
                draw.rectangle([(80, 80), (80 + 380, 80 + 60)], fill=(15, 18, 25, 220), outline=(50, 60, 80), width=2)
                draw.text((80 + 190, 80 + 30), top_label, fill=(240, 243, 248), anchor="mm")

                # Bottom label
                draw.rectangle([(80, div_y + 80), (80 + 380, div_y + 80 + 60)], fill=(15, 18, 25, 220), outline=(50, 60, 80), width=2)
                draw.text((80 + 190, div_y + 80 + 30), bottom_label, fill=(32, 201, 151), anchor="mm")

                proc.stdin.write(frame.tobytes())
                frames_rendered += 1

            proc.stdin.close()
            proc.wait(timeout=25)

            if proc.returncode != 0:
                err = proc.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg failed with code {proc.returncode}: {err}")

            t_end = time.time()
            elapsed = t_end - t_start
            final_fps = frames_rendered / elapsed if elapsed > 0 else 0.0

            telemetry = RenderTelemetry(
                frames_rendered=frames_rendered,
                total_duration_sec=duration,
                elapsed_time_sec=elapsed,
                fps=round(final_fps, 2),
                engine="Python Canvas (Split Screen)",
            )

            return SegmentResult(
                scene_id=scene_id,
                video_path=out_mp4_path,
                duration=duration,
                fps=fps,
                telemetry=telemetry,
                success=True,
            )

        except Exception as e:
            logger.error("Failed split-screen render %s: %s", scene_id, e)
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.kill()
            return SegmentResult(
                scene_id=scene_id,
                video_path=out_mp4_path,
                duration=duration,
                fps=fps,
                success=False,
                error_message=str(e),
            )
