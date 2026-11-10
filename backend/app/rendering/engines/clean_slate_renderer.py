"""
Clean-Slate Visual Scene Renderer for CrazzyDocs YouTube Shorts.

Renders authentic, high-resolution 1080x1920 @ 30 FPS video scenes from scratch
using authentic openly licensed historical photographs from Wikimedia Commons and Boston Public Library,
dynamic Ken Burns camera motion, obsidian slate cinematic styling, and strict safe-boundary validation.
Enforces 80-90% visual image storytelling with minimal punchy labels and zero text overflow.
"""

import logging
import math
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from backend.app.rendering.text.text_bounds_engine import BoundingBox, TextBoundsEngine

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
HIST_DIR = os.path.join(WORKSPACE_ROOT, "media", "images", "historical")


class CleanSlateSceneRenderer:
    """Renders authentic historical documentary scenes from scratch for vertical 1080x1920 Shorts."""

    def __init__(
        self,
        ffmpeg_path: str = FFMPEG_DEFAULT_PATH,
        width: int = 1080,
        height: int = 1920,
        fps: float = 30.0,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.width = width
        self.height = height
        self.fps = fps
        self.bounds_engine = TextBoundsEngine(canvas_width=width, canvas_height=height)

        # Preload and prepare historical assets
        self._init_historical_assets()

    def _init_historical_assets(self):
        """Loads and pre-processes background blurs and base imagery for rapid streaming."""
        self.scenes_data = {}
        scene_configs = {
            "scene_01": ("scene_01_original_tank.jpg", (32, 201, 151)),
            "scene_02": ("scene_02_tank_layout.png", (242, 193, 78)),
            "scene_03": ("scene_03_collapsed_tank.jpg", (255, 77, 77)),
            "scene_04": ("scene_04_railway_wreckage.jpg", (255, 77, 77)),
            "scene_05": ("scene_05_investigation_wreckage.jpg", (242, 193, 78)),
            "scene_06": ("scene_06_newspaper_legacy.png", (32, 201, 151)),
        }

        w, h = self.width, self.height
        for scene_key, (fname, accent_color) in scene_configs.items():
            fpath = os.path.join(HIST_DIR, fname)
            if os.path.exists(fpath):
                src = Image.open(fpath).convert("RGB")
            else:
                # High-contrast slate fallback if image is missing
                src = Image.new("RGB", (800, 600), (25, 35, 50))

            # Precompute high-res blurred documentary backdrop
            bg = src.resize((w, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(32))
            # Tint overlay for contrast
            tint = Image.new("RGBA", (w, h), (10, 14, 22, 190))
            base_bg = Image.alpha_composite(bg.convert("RGBA"), tint).convert("RGB")

            self.scenes_data[scene_key] = {
                "src": src,
                "base_bg": base_bg,
                "accent_color": accent_color,
            }

    def _apply_ken_burns(
        self,
        src_img: Image.Image,
        target_w: int,
        target_h: int,
        progress: float,
        zoom_start: float = 1.0,
        zoom_end: float = 1.10,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> Image.Image:
        """Applies smooth cinematic Ken Burns cropping to source photograph."""
        sw, sh = src_img.size
        curr_zoom = zoom_start + (zoom_end - zoom_start) * progress

        # Calculate crop window
        crop_w = int(sw / curr_zoom)
        crop_h = int(sh / curr_zoom)

        # Center with pan offset
        cx = int((sw / 2) + (sw * pan_x * progress))
        cy = int((sh / 2) + (sh * pan_y * progress))

        # Clamp crop box within bounds
        x0 = max(0, min(sw - crop_w, cx - crop_w // 2))
        y0 = max(0, min(sh - crop_h, cy - crop_h // 2))
        x1 = x0 + crop_w
        y1 = y0 + crop_h

        cropped = src_img.crop((x0, y0, x1, y1))
        return cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)

    def render_scene(
        self,
        scene_id: str,
        duration: float,
        out_mp4_path: str,
        draw_func,
        timeout: float = 300.0,
    ) -> str:
        """Streams dynamically drawn frames into FFmpeg to produce a clean 30 FPS MP4."""
        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)
        total_frames = int(round(duration * self.fps))
        frame_bytes = self.width * self.height * 3

        logger.info(
            "[%s] Render start: %.2fs, %d frames @ %.0f FPS -> %s",
            scene_id, duration, total_frames, self.fps, out_mp4_path,
        )
        t_start = time.perf_counter()

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", "rgb24",
            "-framerate", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-video_track_timescale", "30000",
            "-r", str(self.fps),
            "-movflags", "+faststart",
            out_mp4_path,
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        stderr_chunks: list = []

        def _drain_stderr():
            try:
                while True:
                    chunk = proc.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
            except Exception:
                pass

        drain_thread = threading.Thread(target=_drain_stderr, daemon=True)
        drain_thread.start()

        t_draw_start = time.perf_counter()
        log_interval = max(1, total_frames // 4)

        try:
            for frame_idx in range(total_frames):
                progress = frame_idx / max(1, total_frames - 1)
                frame_img = draw_func(frame_idx, progress)
                proc.stdin.write(frame_img.tobytes())

                if frame_idx % log_interval == 0 or frame_idx == total_frames - 1:
                    elapsed = time.perf_counter() - t_draw_start
                    logger.info(
                        "[%s] Frame %d/%d (%.0f%%) — %.1fs elapsed",
                        scene_id, frame_idx + 1, total_frames,
                        (frame_idx + 1) / total_frames * 100, elapsed,
                    )
        except BrokenPipeError:
            logger.error("[%s] FFmpeg stdin pipe broken at frame %d/%d", scene_id, frame_idx + 1, total_frames)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

        proc.wait(timeout=timeout)
        drain_thread.join(timeout=5)

        if proc.returncode != 0:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="ignore")
            raise RuntimeError(f"FFmpeg render failed for {scene_id} ({proc.returncode}): {stderr_text[-500:]}")

        t_end = time.perf_counter()
        logger.info("[%s] Render complete: %.2fs total -> %s", scene_id, t_end - t_start, out_mp4_path)
        return out_mp4_path

    # =========================================================================
    # SCENE 1: BOSTON 1919 & 50-FOOT STORAGE TANK
    # =========================================================================
    def draw_scene_01(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_01"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(32, 201, 151), width=2)
        self.bounds_engine.draw_bounded_text(draw, "HISTORICAL ARCHIVE  |  JANUARY 15, 1919", hdr_box, fill_color=(32, 201, 151), max_font_size=26)

        # 2. Dominant Historical Photograph (80% of visual weight)
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.0, zoom_end=1.09)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(32, 201, 151), width=3)

        # 3. Minimal Lower Label Pill (Bounded & Safe)
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(242, 193, 78), width=2)
        self.bounds_engine.draw_bounded_text(draw, "PURITY DISTILLING CO. — 50-FT STEEL STORAGE TANK", lbl_box, fill_color=(240, 245, 250), max_font_size=28)

        return img

    # =========================================================================
    # SCENE 2: LEAKING SEAMS & BROWN PAINT COVER-UP
    # =========================================================================
    def draw_scene_02(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_02"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(242, 193, 78), width=2)
        self.bounds_engine.draw_bounded_text(draw, "STRUCTURAL DEFECT INVESTIGATION  |  1915 — 1919", hdr_box, fill_color=(242, 193, 78), max_font_size=26)

        # 2. Dominant Blueprint & Harbor Layout Map
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.02, zoom_end=1.10, pan_x=0.03, pan_y=0.03)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(242, 193, 78), width=3)

        # 3. Minimal Lower Label Pill
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(255, 77, 77), width=2)
        self.bounds_engine.draw_bounded_text(draw, "COMMERCIAL STREET DOCKS: PAINTED BROWN TO HIDE LEAKS", lbl_box, fill_color=(255, 230, 230), max_font_size=26)

        return img

    # =========================================================================
    # SCENE 3: CATASTROPHIC RUPTURE & 25-FT WAVE AT 35 MPH
    # =========================================================================
    def draw_scene_03(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_03"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(255, 77, 77), width=2)
        self.bounds_engine.draw_bounded_text(draw, "CATASTROPHIC RUPTURE EVENT  |  12:30 PM", hdr_box, fill_color=(255, 77, 77), max_font_size=26)

        # 2. Dominant Archival Rupture Photograph
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.0, zoom_end=1.12, pan_y=0.04)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(255, 77, 77), width=3)

        # 3. Minimal Lower Label Pill
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(242, 193, 78), width=2)
        self.bounds_engine.draw_bounded_text(draw, "2.3 MILLION GALLONS UNLEASHED  •  25-FT WAVE AT 35 MPH", lbl_box, fill_color=(242, 193, 78), max_font_size=26)

        return img

    # =========================================================================
    # SCENE 4: URBAN DESTRUCTION, COLLAPSED RAILWAY & CASUALTIES
    # =========================================================================
    def draw_scene_04(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_04"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(255, 77, 77), width=2)
        self.bounds_engine.draw_bounded_text(draw, "DISASTER IMPACT ZONE  |  BOSTON WATERFRONT", hdr_box, fill_color=(255, 77, 77), max_font_size=26)

        # 2. Dominant Boston Elevated Railway Sheared Girders Photo
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.02, zoom_end=1.11, pan_x=-0.04)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(255, 77, 77), width=3)

        # 3. Minimal Lower Label Pill
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(255, 77, 77), width=2)
        self.bounds_engine.draw_bounded_text(draw, "ELEVATED RAILWAY COLLAPSED  •  21 DEAD, 150 INJURED", lbl_box, fill_color=(255, 255, 255), max_font_size=28)

        return img

    # =========================================================================
    # SCENE 5: INVESTIGATION, METALLURGY & CORPORATE NEGLIGENCE
    # =========================================================================
    def draw_scene_05(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_05"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(242, 193, 78), width=2)
        self.bounds_engine.draw_bounded_text(draw, "JUDICIAL AUDIT FINDINGS  |  1920 — 1925", hdr_box, fill_color=(242, 193, 78), max_font_size=26)

        # 2. Dominant Archival Photograph of Rescue & Inquiry Wreckage
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.0, zoom_end=1.09, pan_y=0.03)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(242, 193, 78), width=3)

        # 3. Minimal Lower Label Pill
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(32, 201, 151), width=2)
        self.bounds_engine.draw_bounded_text(draw, "AUDITOR FINDING: GROSS NEGLIGENCE (STEEL HALF AS THICK)", lbl_box, fill_color=(32, 201, 151), max_font_size=26)

        return img

    # =========================================================================
    # SCENE 6: LEGAL SETTLEMENT & MODERN ENGINEERING LICENSING
    # =========================================================================
    def draw_scene_06(self, frame_idx: int, progress: float) -> Image.Image:
        data = self.scenes_data["scene_06"]
        img = data["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Minimal Header Badge
        hdr_box = BoundingBox(100, 220, 980, 290)
        draw.rounded_rectangle([hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1], radius=10, fill=(18, 24, 34, 235), outline=(32, 201, 151), width=2)
        self.bounds_engine.draw_bounded_text(draw, "HISTORICAL LEGACY & IMPACT  |  LANDMARK VICTORY", hdr_box, fill_color=(32, 201, 151), max_font_size=26)

        # 2. Dominant Boston Daily Globe Jan 16, 1919 Front Page Photo
        photo_box = (60, 330, 1020, 1310)
        pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
        photo = self._apply_ken_burns(data["src"], pw, ph, progress, zoom_start=1.04, zoom_end=1.0, pan_y=-0.02)
        img.paste(photo, (photo_box[0], photo_box[1]))
        draw.rectangle(photo_box, outline=(32, 201, 151), width=3)

        # 3. Minimal Lower Label Pill
        lbl_box = BoundingBox(100, 1340, 980, 1420)
        draw.rounded_rectangle([lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1], radius=12, fill=(18, 24, 34, 235), outline=(242, 193, 78), width=2)
        self.bounds_engine.draw_bounded_text(draw, "$628,000 SETTLEMENT  •  MODERN P.E. BLUEPRINT LAWS BORN", lbl_box, fill_color=(242, 193, 78), max_font_size=26)

        return img
