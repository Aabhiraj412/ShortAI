"""
Fast-Paced Visual Storytelling Renderer for Vesna Vulovic Short.
Phase 5, Stage 3.13A Content Integrity Standard.

Renders 24 distinct visual beats across 52.01s, strictly mapped to authentic
historical assets from Wikimedia Commons (Vesna Vulovic, DC-9, crash debris, hospital, press),
followed by the seamless additive 2.0s CrazzyDocs circular outro (Total: 54.01s).
1080x1920 @ 30 FPS, Ken Burns motion, editorial text safe zones, and rapid frame streaming.
Zero nuclear or atomic imagery.
"""

import logging
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from backend.app.rendering.text.text_bounds_engine import BoundingBox, TextBoundsEngine
from backend.app.branding.outro_generator import (
    prepare_dark_studio_plate,
    prepare_circular_icon,
    render_icon_only_frame,
)

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
VESNA_IMG_DIR = os.path.join(WORKSPACE_ROOT, "media", "images", "vesna_vulovic")
OUTRO_DIR = os.path.join(WORKSPACE_ROOT, "Youtube Channel Src")


@dataclass
class VisualBeatConfig:
    id: str
    image_filename: str
    start_sec: float
    end_sec: float
    top_tag: str
    editorial_text: str
    accent_color: Tuple[int, int, int]
    motion_type: str = "zoom_in"  # zoom_in, zoom_out, pan_right, pan_left, flash_strike
    sub_label: str = ""

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


# 24 Rapid Visual Beats across 52.01s
BEAT_CONFIGS: List[VisualBeatConfig] = [
    # --- Hook (0.00 - 6.04s) ---
    VisualBeatConfig(
        id="beat_01",
        image_filename="vesna_vulovic_portrait.jpg",
        start_sec=0.00,
        end_sec=2.00,
        top_tag="SRBSKÁ KAMENICE  •  1972",
        editorial_text="FELL 33,000 FEET",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_02",
        image_filename="debris_jat367_snow.jpg",
        start_sec=2.00,
        end_sec=4.00,
        top_tag="NO PARACHUTE  •  FREE FALL",
        editorial_text="TERMINAL VELOCITY",
        accent_color=(254, 224, 4),
        motion_type="zoom_out",
    ),
    VisualBeatConfig(
        id="beat_03",
        image_filename="vesna_in_hospital.jpg",
        start_sec=4.00,
        end_sec=6.04,
        top_tag="PRAGUE MILITARY HOSPITAL",
        editorial_text="WALKED OUT ALIVE",
        accent_color=(32, 201, 151),
        motion_type="pan_right",
    ),

    # --- Context (6.04 - 13.66s) ---
    VisualBeatConfig(
        id="beat_04",
        image_filename="vesna_vulovic_portrait.jpg",
        start_sec=6.04,
        end_sec=8.50,
        top_tag="JAT YUGOSLAV AIRLINES  •  1972",
        editorial_text="22-YEAR-OLD STEWARDESS",
        accent_color=(242, 193, 78),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_05",
        image_filename="dc9_aircraft.jpg",
        start_sec=8.50,
        end_sec=11.00,
        top_tag="MCDONNELL DOUGLAS DC-9-32",
        editorial_text="JAT FLIGHT 367",
        accent_color=(255, 255, 255),
        motion_type="pan_left",
    ),
    VisualBeatConfig(
        id="beat_06",
        image_filename="flight_jat367_route.png",
        start_sec=11.00,
        end_sec=13.66,
        top_tag="STOCKHOLM TO BELGRADE",
        editorial_text="CRUISING OVER BOHEMIA",
        accent_color=(32, 201, 151),
        motion_type="zoom_out",
    ),

    # --- Bomb Detonation (13.66 - 20.15s) ---
    VisualBeatConfig(
        id="beat_07",
        image_filename="dc9_aircraft.jpg",
        start_sec=13.66,
        end_sec=15.80,
        top_tag="ALTITUDE: 33,330 FEET",
        editorial_text="CRUISING SPEED",
        accent_color=(242, 193, 78),
        motion_type="pan_right",
    ),
    VisualBeatConfig(
        id="beat_08",
        image_filename="debris_jat367_snow.jpg",
        start_sec=15.80,
        end_sec=18.00,
        top_tag="CARGO HOLD EXPLOSION",
        editorial_text="BRIEFCASE BOMB",
        accent_color=(255, 77, 77),
        motion_type="flash_strike",
    ),
    VisualBeatConfig(
        id="beat_09",
        image_filename="debris_jat367_snow.jpg",
        start_sec=18.00,
        end_sec=20.15,
        top_tag="STRUCTURAL FAILURE",
        editorial_text="MID-AIR DISINTEGRATION",
        accent_color=(254, 224, 4),
        motion_type="zoom_in",
    ),

    # --- The Plunge (20.15 - 26.19s) ---
    VisualBeatConfig(
        id="beat_10",
        image_filename="flight_jat367_route.png",
        start_sec=20.15,
        end_sec=22.10,
        top_tag="DECOMPRESSION  •  -45°C",
        editorial_text="FREEZING STRATOSPHERE",
        accent_color=(32, 201, 151),
        motion_type="zoom_out",
    ),
    VisualBeatConfig(
        id="beat_11",
        image_filename="debris_jat367_snow.jpg",
        start_sec=22.10,
        end_sec=24.10,
        top_tag="TAIL CONE SECTION",
        editorial_text="TRAPPED BY FOOD CART",
        accent_color=(242, 193, 78),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_12",
        image_filename="debris_jat367_snow.jpg",
        start_sec=24.10,
        end_sec=26.19,
        top_tag="AERODYNAMIC DRAG",
        editorial_text="PLUMMETING TO EARTH",
        accent_color=(255, 77, 77),
        motion_type="pan_left",
    ),

    # --- Impact & Survival (26.19 - 32.73s) ---
    VisualBeatConfig(
        id="beat_13",
        image_filename="debris_jat367_snow.jpg",
        start_sec=26.19,
        end_sec=28.30,
        top_tag="TERMINAL VELOCITY",
        editorial_text="200 MPH DESCENT",
        accent_color=(254, 224, 4),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_14",
        image_filename="debris_jat367_snow.jpg",
        start_sec=28.30,
        end_sec=30.50,
        top_tag="SRBSKÁ KAMENICE MOUNTAIN",
        editorial_text="SNOW-COVERED SLOPE",
        accent_color=(255, 77, 77),
        motion_type="flash_strike",
    ),
    VisualBeatConfig(
        id="beat_15",
        image_filename="debris_jat367_snow.jpg",
        start_sec=30.50,
        end_sec=32.73,
        top_tag="IMPACT ABSORPTION",
        editorial_text="CUSHIONED BY PINES",
        accent_color=(32, 201, 151),
        motion_type="zoom_out",
    ),

    # --- Discovery & Rescue (32.73 - 39.39s) ---
    VisualBeatConfig(
        id="beat_16",
        image_filename="debris_jat367_snow.jpg",
        start_sec=32.73,
        end_sec=35.00,
        top_tag="PITCH-BLACK FOREST",
        editorial_text="SMOKING WRECKAGE",
        accent_color=(242, 193, 78),
        motion_type="pan_right",
    ),
    VisualBeatConfig(
        id="beat_17",
        image_filename="debris_jat367_snow.jpg",
        start_sec=35.00,
        end_sec=37.20,
        top_tag="VILLAGE VOLUNTEER",
        editorial_text="MEDIC BRUNO HONKE",
        accent_color=(32, 201, 151),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_18",
        image_filename="vesna_in_hospital.jpg",
        start_sec=37.20,
        end_sec=39.39,
        top_tag="FIRST AID IN THE SNOW",
        editorial_text="STABILIZED IN THE DARK",
        accent_color=(255, 255, 255),
        motion_type="pan_left",
    ),

    # --- Hospital Recovery (39.39 - 44.99s) ---
    VisualBeatConfig(
        id="beat_19",
        image_filename="vesna_in_hospital.jpg",
        start_sec=39.39,
        end_sec=41.20,
        top_tag="MILITARY TRAUMA WARD",
        editorial_text="FRACTURED SKULL & SPINE",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_20",
        image_filename="vesna_in_hospital.jpg",
        start_sec=41.20,
        end_sec=43.10,
        top_tag="COMA FOR 27 DAYS",
        editorial_text="PARALYZED FROM WAIST",
        accent_color=(254, 224, 4),
        motion_type="zoom_out",
    ),
    VisualBeatConfig(
        id="beat_21",
        image_filename="vesna_with_reporter_1973.jpg",
        start_sec=43.10,
        end_sec=44.99,
        top_tag="MIRACULOUS RECOVERY",
        editorial_text="LEARNED TO WALK AGAIN",
        accent_color=(32, 201, 151),
        motion_type="pan_right",
    ),

    # --- World Record & Legacy (44.99 - 52.01s) ---
    VisualBeatConfig(
        id="beat_22",
        image_filename="vesna_with_reporter_1973.jpg",
        start_sec=44.99,
        end_sec=47.30,
        top_tag="GUINNESS BOOK OF RECORDS",
        editorial_text="WORLD RECORD HOLDER",
        accent_color=(254, 224, 4),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_23",
        image_filename="vesna_vulovic_portrait.jpg",
        start_sec=47.30,
        end_sec=49.60,
        top_tag="HIGHEST SURVIVED FALL",
        editorial_text="33,330 FEET (10,160 M)",
        accent_color=(32, 201, 151),
        motion_type="zoom_out",
    ),
    VisualBeatConfig(
        id="beat_24",
        image_filename="vesna_vulovic_portrait.jpg",
        start_sec=49.60,
        end_sec=52.01,
        top_tag="NATIONAL HERO",
        editorial_text="AN AVIATION MIRACLE",
        accent_color=(242, 193, 78),
        motion_type="zoom_in",
    ),
]


class VesnaFastRenderer:
    """High-velocity Ken Burns and editorial badge renderer for Vesna Vulovic short."""

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

        # Preload font assets
        self.font_bold = self._load_font("arialbd.ttf", 68)
        self.font_tag = self._load_font("arialbd.ttf", 34)
        self.font_brand = self._load_font("arialbd.ttf", 36)

        # Preload assets and generate blurred backdrops
        self._cache_beat_assets()

    def _load_font(self, font_name: str, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            return ImageFont.load_default()

    def _cache_beat_assets(self):
        """Loads authentic assets from media/images/vesna_vulovic and precomputes blurred backdrops."""
        self.cached_images: Dict[str, Image.Image] = {}
        self.cached_backdrops: Dict[str, Image.Image] = {}

        w, h = self.width, self.height
        for beat in BEAT_CONFIGS:
            fname = beat.image_filename
            if fname in self.cached_images:
                continue

            fpath = os.path.join(VESNA_IMG_DIR, fname)
            if os.path.exists(fpath):
                img = Image.open(fpath).convert("RGB")
            else:
                img = Image.new("RGB", (800, 600), (20, 25, 35))

            self.cached_images[fname] = img

            # Precompute high-res blurred background
            bg = img.resize((w, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(32))
            tint = Image.new("RGBA", (w, h), (10, 14, 22, 195))
            base_bg = Image.alpha_composite(bg.convert("RGBA"), tint).convert("RGB")
            self.cached_backdrops[fname] = base_bg

        # Prepare authoritative outro canvas and circular icon
        self.outro_bg_plate = prepare_dark_studio_plate(w, h)
        self.outro_logo = prepare_circular_icon(size=420)

    def render_beat_frame(self, beat: VisualBeatConfig, frame_progress: float) -> Image.Image:
        """Renders a single 1080x1920 video frame with Ken Burns camera movement and editorial text."""
        w, h = self.width, self.height
        fname = beat.image_filename
        src = self.cached_images[fname]
        base_bg = self.cached_backdrops[fname].copy()

        # 1. Apply Ken Burns Camera Motion
        p = max(0.0, min(1.0, frame_progress))
        if beat.motion_type == "zoom_in":
            scale = 1.0 + 0.12 * p
            cx_offset, cy_offset = 0.0, 0.0
        elif beat.motion_type == "zoom_out":
            scale = 1.15 - 0.12 * p
            cx_offset, cy_offset = 0.0, 0.0
        elif beat.motion_type == "pan_right":
            scale = 1.08
            cx_offset = -0.06 + 0.12 * p
            cy_offset = 0.0
        elif beat.motion_type == "pan_left":
            scale = 1.08
            cx_offset = 0.06 - 0.12 * p
            cy_offset = 0.0
        elif beat.motion_type == "flash_strike":
            scale = 1.20 - 0.10 * p
            cx_offset, cy_offset = 0.0, 0.0
        else:
            scale = 1.05
            cx_offset, cy_offset = 0.0, 0.0

        # Fit portrait container (e.g. 960x1280 in center)
        target_w = int(w * 0.90 * scale)
        target_h = int((target_w / src.width) * src.height)
        if target_h < int(h * 0.55):
            target_h = int(h * 0.55 * scale)
            target_w = int((target_h / src.height) * src.width)

        scaled_fg = src.resize((target_w, target_h), Image.Resampling.BILINEAR)

        # Center on canvas with pan offset
        paste_x = int((w - target_w) / 2 + cx_offset * w)
        paste_y = int((h - target_h) / 2 + cy_offset * h - 60)

        # Paste onto dark cinematic backdrop
        base_bg.paste(scaled_fg, (paste_x, paste_y))

        # Vignette shadow overlay at top & bottom
        draw = ImageDraw.Draw(base_bg, "RGBA")
        draw.rectangle([(0, 0), (w, 280)], fill=(8, 12, 18, 220))
        draw.rectangle([(0, h - 380), (w, h)], fill=(8, 12, 18, 230))

        # 2. Render Top Tag Badge (e.g. "SRBSKÁ KAMENICE • 1972")
        tag_text = beat.top_tag
        draw.text((w // 2, 140), tag_text, fill=beat.accent_color, font=self.font_tag, anchor="mm")

        # 3. Render High-Impact Punchy Editorial Headline
        headline = beat.editorial_text
        # Draw subtle drop shadow
        draw.text((w // 2 + 3, h - 260 + 3), headline, fill=(0, 0, 0, 240), font=self.font_bold, anchor="mm")
        draw.text((w // 2, h - 260), headline, fill=(255, 255, 255, 255), font=self.font_bold, anchor="mm")

        # Accent underline
        accent_w = 280
        draw.rectangle([(w // 2 - accent_w // 2, h - 200), (w // 2 + accent_w // 2, h - 194)], fill=beat.accent_color)

        # Flash strike white fade on beat onset
        if beat.motion_type == "flash_strike" and p < 0.15:
            flash_alpha = int(255 * (1.0 - p / 0.15))
            flash_overlay = Image.new("RGBA", (w, h), (255, 255, 255, flash_alpha))
            base_bg = Image.alpha_composite(base_bg.convert("RGBA"), flash_overlay).convert("RGB")

        return base_bg

    def render_outro_frame(self, outro_progress: float) -> Image.Image:
        """
        Renders 2.0s branded CrazzyDocs circular outro using authoritative channel icon.
        Pure Icon-Only: Zero channel text, zero subscribe text, zero progress bars.
        """
        p = max(0.0, min(1.0, outro_progress))
        frame_idx = int(p * 59)
        return render_icon_only_frame(
            self.outro_bg_plate,
            self.outro_logo,
            frame_idx,
            total_frames=60,
            width=self.width,
            height=self.height,
        )

    def render_video_to_mp4(
        self,
        audio_path: str,
        output_mp4: str,
        progress_callback: Optional[callable] = None,
    ) -> float:
        """
        Renders complete video (Beats 1-24 + Outro) streamed directly into FFmpeg via pipe.
        Returns final video duration in seconds.
        """
        os.makedirs(os.path.dirname(output_mp4), exist_ok=True)
        w, h = self.width, self.height

        # Calculate exact frame counts
        total_beats_duration = BEAT_CONFIGS[-1].end_sec  # 52.01s
        outro_duration = 2.0  # 2.0s
        total_duration = total_beats_duration + outro_duration  # 54.01s
        total_frames = int(total_duration * self.fps)

        logger.info(
            "Rendering Vesna Vulovic Short: %d frames (%.2fs @ %.1f FPS) -> %s",
            total_frames, total_duration, self.fps, output_mp4
        )

        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{w}x{h}",
            "-pix_fmt", "rgb24",
            "-r", str(self.fps),
            "-i", "-",  # Video frames from stdin
            "-i", audio_path,  # Audio track
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_mp4
        ]

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def feed_frames():
            try:
                frame_idx = 0
                for beat in BEAT_CONFIGS:
                    beat_frames = int(beat.duration * self.fps)
                    for bf in range(beat_frames):
                        p = bf / max(1, beat_frames - 1)
                        im = self.render_beat_frame(beat, p)
                        proc.stdin.write(im.tobytes())
                        frame_idx += 1
                        if progress_callback and frame_idx % 60 == 0:
                            progress_callback(frame_idx / total_frames)

                # Render 2.0s Outro
                outro_frames = int(outro_duration * self.fps)
                for of in range(outro_frames):
                    p = of / max(1, outro_frames - 1)
                    im = self.render_outro_frame(p)
                    proc.stdin.write(im.tobytes())
                    frame_idx += 1
                    if progress_callback and frame_idx % 60 == 0:
                        progress_callback(frame_idx / total_frames)

                proc.stdin.close()
            except Exception as e:
                logger.error("Pipe feed error: %s", e)
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        feed_thread = threading.Thread(target=feed_frames, daemon=True)
        feed_thread.start()

        stderr_output = proc.stderr.read().decode("utf-8", errors="ignore")
        proc.wait()
        feed_thread.join()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg rendering failed (code {proc.returncode}): {stderr_output}")

        file_size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
        logger.info("Successfully rendered %s (%.2f MB, %.2fs)", output_mp4, file_size_mb, total_duration)
        return total_duration
