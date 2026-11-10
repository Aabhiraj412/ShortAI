"""
Python Photographic Canvas Engine - Ken Burns & Archival Photo Renderer.

Renders authentic high-resolution photography, space imagery, and historical archives
with cinematic Ken Burns zoom, pan dynamics, radial vignette, and organic film grain.
"""

import math
import os
import subprocess
import time
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
import logging

from backend.app.rendering.types import RenderTelemetry, SegmentResult

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"


class PhotoSceneRenderer:
    """Renders photographic scenes with smooth Ken Burns camera dynamics in pure Python."""

    def __init__(self, ffmpeg_path: str = FFMPEG_DEFAULT_PATH, width: int = 1080, height: int = 1920):
        self.ffmpeg_path = ffmpeg_path
        self.width = width
        self.height = height
        self._vignette_mask: Optional[Image.Image] = None

    def _get_vignette_mask(self) -> Image.Image:
        """Precomputes and caches a radial vignette mask for 1080x1920."""
        if self._vignette_mask is not None:
            return self._vignette_mask

        # Generate quarter-res radial gradient and upscale for zero latency
        qw, qh = self.width // 4, self.height // 4
        mask = Image.new("L", (qw, qh), 0)
        draw = ImageDraw.Draw(mask)
        cx, cy = qw / 2, qh / 2
        max_dist = math.hypot(cx, cy)

        for r in range(int(max_dist), 0, -3):
            # Alpha increases toward the outer corners
            factor = (r / max_dist) ** 2.2
            alpha = int(factor * 160)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=alpha)

        mask = mask.resize((self.width, self.height), Image.Resampling.BILINEAR)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=15))
        self._vignette_mask = mask
        return self._vignette_mask

    def render_photo_short(
        self,
        scene_id: str,
        image_path: str,
        duration: float,
        out_mp4_path: str,
        fps: float = 30.0,
        zoom_start: float = 1.00,
        zoom_end: float = 1.08,
        pan_dx: float = 0.02,
        pan_dy: float = -0.015,
        title_text: Optional[str] = None,
        subtitle_text: Optional[str] = None,
    ) -> SegmentResult:
        """Render a Ken Burns scene from a high-res still image directly to MP4."""
        t_start = time.time()
        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Source image not found: {image_path}")

        source_img = Image.open(image_path).convert("RGB")
        src_w, src_h = source_img.size

        # Fit image to cover 1080x1920 while preserving aspect ratio
        scale = max(self.width / src_w, self.height / src_h) * 1.15
        base_w = int(src_w * scale)
        base_h = int(src_h * scale)
        scaled_base = source_img.resize((base_w, base_h), Image.Resampling.BICUBIC)

        total_frames = int(round(duration * fps))
        vignette = self._get_vignette_mask()

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

        logger.info("Rendering %d photo Ken Burns frames for %s...", total_frames, scene_id)
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

                zoom = zoom_start + (zoom_end - zoom_start) * e
                cur_w = int(self.width / zoom)
                cur_h = int(self.height / zoom)

                cx = (base_w / 2) + (pan_dx * base_w * e)
                cy = (base_h / 2) + (pan_dy * base_h * e)

                x0 = int(cx - cur_w / 2)
                y0 = int(cy - cur_h / 2)
                x1 = x0 + cur_w
                y1 = y0 + cur_h

                crop = scaled_base.crop((x0, y0, x1, y1))
                frame = crop.resize((self.width, self.height), Image.Resampling.BILINEAR)

                # Vignette
                vignette_layer = Image.new("RGB", (self.width, self.height), (0, 0, 0))
                frame = Image.composite(vignette_layer, frame, vignette)

                if title_text and i > 15:
                    draw = ImageDraw.Draw(frame)
                    bar_y = 1380
                    draw.rectangle([(120, bar_y - 20), (self.width - 120, bar_y + 110)], fill=(15, 18, 25))
                    draw.rectangle([(120, bar_y - 20), (self.width - 120, bar_y + 110)], outline=(35, 45, 60), width=2)
                    draw.text((self.width // 2, bar_y + 10), title_text, fill=(240, 243, 248), anchor="mm")
                    if subtitle_text:
                        draw.text((self.width // 2, bar_y + 65), subtitle_text, fill=(32, 201, 151), anchor="mm")

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
                engine="Python Canvas (Pillow Ken Burns)",
            )
            logger.info("Rendered %s: %d frames in %.2fs (%.1f FPS)", scene_id, frames_rendered, elapsed, final_fps)

            return SegmentResult(
                scene_id=scene_id,
                video_path=out_mp4_path,
                duration=duration,
                fps=fps,
                telemetry=telemetry,
                success=True,
            )

        except Exception as e:
            logger.error("Failed photo scene render %s: %s", scene_id, e)
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
