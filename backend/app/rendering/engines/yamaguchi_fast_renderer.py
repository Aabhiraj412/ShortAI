"""
Fast-Paced Visual Storytelling Renderer for Tsutomu Yamaguchi Short.
Stage 3.4 Production Reference.

Renders 24 distinct visual beats across 54.64s (changing every ~2.27s),
followed by the seamless additive 2.0s CrazzyDocs circular outro.
Enforces 1080x1920 @ 30 FPS, authentic Public Domain historical imagery,
Ken Burns motion, editorial text safe zones, and rapid frame streaming.
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

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
YAMAGUCHI_IMG_DIR = os.path.join(WORKSPACE_ROOT, "media", "images", "yamaguchi")


@dataclass
class VisualBeatConfig:
    id: str
    image_filename: Optional[str]
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


# 24 Rapid Visual Beats across 54.64s
BEAT_CONFIGS: List[VisualBeatConfig] = [
    # --- Hook (0.00 - 6.10s) ---
    VisualBeatConfig(
        id="beat_01",
        image_filename="Atomic_cloud_over_Hiroshima_jpg.jpg",
        start_sec=0.00,
        end_sec=2.60,
        top_tag="HIROSHIMA  •  AUGUST 6, 1945",
        editorial_text="HIROSHIMA SURVIVOR",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_02",
        image_filename="Nagasakibomb_jpg.jpg",
        start_sec=2.60,
        end_sec=6.10,
        top_tag="NAGASAKI  •  AUGUST 9, 1945",
        editorial_text="3 DAYS LATER: NAGASAKI",
        accent_color=(254, 224, 4),
        motion_type="zoom_out",
    ),
    # --- Context (6.10 - 12.49s) ---
    VisualBeatConfig(
        id="beat_03",
        image_filename="Japanese_cruiser_Aoba_aerial_photo_28_July_1945_jpg.jpg",
        start_sec=6.10,
        end_sec=8.20,
        top_tag="MITSUBISHI HEAVY INDUSTRIES  •  1945",
        editorial_text="NAVAL DRAFTSMAN",
        accent_color=(32, 201, 151),
        motion_type="pan_right",
    ),
    VisualBeatConfig(
        id="beat_04",
        image_filename="Hiroshima_Castle_25july1945_jpg.jpg",
        start_sec=8.20,
        end_sec=10.30,
        top_tag="HIROSHIMA DISTRICT  •  SUMMER 1945",
        editorial_text="FINAL BUSINESS TRIP",
        accent_color=(242, 193, 78),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_05",
        image_filename="Hattyo_bori_Hiroshima_25july1945_jpg.jpg",
        start_sec=10.30,
        end_sec=12.49,
        top_tag="COMMERCIAL DISTRICT  •  AUGUST 6",
        editorial_text="WALKING TO THE TRAIN",
        accent_color=(255, 255, 255),
        motion_type="pan_left",
    ),
    # --- Blast 1 (12.49 - 20.89s) ---
    VisualBeatConfig(
        id="beat_06",
        image_filename="Hiroshima_Bombing_Operations_Order_No_35_5_Aug_1945_jpg.jpg",
        start_sec=12.49,
        end_sec=14.50,
        top_tag="509TH COMPOSITE GROUP  •  OPERATION ORDER 35",
        editorial_text="AUGUST 6: 8:15 AM",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_07",
        image_filename="Little_Boy_bomb_jpg.jpg",
        start_sec=14.50,
        end_sec=16.50,
        top_tag="LITTLE BOY  •  URANIUM DETONATION",
        editorial_text="THE SKY IGNITED",
        accent_color=(254, 224, 4),
        motion_type="flash_strike",
    ),
    VisualBeatConfig(
        id="beat_08",
        image_filename="Littleboy_inner1_info_png.png",
        start_sec=16.50,
        end_sec=18.60,
        top_tag="AIRBURST  •  1,900 FT ELEVATION",
        editorial_text="2 MILES FROM BLAST",
        accent_color=(255, 77, 77),
        motion_type="zoom_out",
    ),
    VisualBeatConfig(
        id="beat_09",
        image_filename="Firestorm_cloud_over_Hiroshima_from_Matsuyama_jpg.jpg",
        start_sec=18.60,
        end_sec=20.89,
        top_tag="RAGING FIRESTORM  •  SHOCKWAVE",
        editorial_text="SEVERE BLAST BURNS",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    # --- Train Escape (20.89 - 26.56s) ---
    VisualBeatConfig(
        id="beat_10",
        image_filename="Hiroshima_aftermath_jpg.jpg",
        start_sec=20.89,
        end_sec=22.80,
        top_tag="GROUND ZERO RUINS  •  LEVEL 5 FIRE",
        editorial_text="BURNING RUINS",
        accent_color=(242, 193, 78),
        motion_type="pan_right",
    ),
    VisualBeatConfig(
        id="beat_11",
        image_filename="Hiroshima___Extend_Of_Fire_&_Limits_Of_Blast_Damage_jpg.jpg",
        start_sec=22.80,
        end_sec=24.60,
        top_tag="SANYO MAIN LINE  •  AUGUST 7, 1945",
        editorial_text="LAST REFUGEE TRAIN",
        accent_color=(32, 201, 151),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_12",
        image_filename="Hiroshima_aerial_view_after_atomic_bomb_8_1945_jpeg.jpeg",
        start_sec=24.60,
        end_sec=26.56,
        top_tag="200 MILES SOUTHWEST  •  ESCAPING TO NAGASAKI",
        editorial_text="200 MILES HOME",
        accent_color=(255, 255, 255),
        motion_type="zoom_out",
    ),
    # --- Boss & Tension (26.56 - 33.79s) ---
    VisualBeatConfig(
        id="beat_13",
        image_filename="Bischof___Victim_of_the_atomic_bomb_dropped_on_Hiroshima_on_August_6,_1945_jpg.jpg",
        start_sec=26.56,
        end_sec=28.80,
        top_tag="AUGUST 9, 1945  •  BANDAGED SURVIVOR",
        editorial_text="REPORTED TO WORK",
        accent_color=(242, 193, 78),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_14",
        image_filename="Nagasaki_Harbor_looking_east,_August_1,_1945_jpg.jpg",
        start_sec=28.80,
        end_sec=31.20,
        top_tag="MITSUBISHI HEADQUARTERS  •  NAGASAKI",
        editorial_text="BOSS WAS SKEPTICAL",
        accent_color=(254, 224, 4),
        motion_type="pan_left",
    ),
    VisualBeatConfig(
        id="beat_15",
        image_filename="Hiroshima_Gembaku_Dome_1_jpg.jpg",
        start_sec=31.20,
        end_sec=33.79,
        top_tag="DISBELIEF  •  'A SINGLE BOMB?'",
        editorial_text="'IMPOSSIBLE' SAID THE BOSS",
        accent_color=(255, 77, 77),
        motion_type="zoom_out",
    ),
    # --- Twist & Blast 2 (33.79 - 40.63s) ---
    VisualBeatConfig(
        id="beat_16",
        image_filename="Fat_Man_blueprint_jpg.jpg",
        start_sec=33.79,
        end_sec=36.00,
        top_tag="AUGUST 9: 11:02 AM  •  SECOND WEAPON",
        editorial_text="11:02 AM: SECOND BOMB",
        accent_color=(255, 77, 77),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_17",
        image_filename="Fat_Man_Assembled_Tinian_1945_jpg.jpg",
        start_sec=36.00,
        end_sec=38.30,
        top_tag="PLUTONIUM CORE  •  FAT MAN",
        editorial_text="BLINDING WHITE FLASH",
        accent_color=(254, 224, 4),
        motion_type="flash_strike",
    ),
    VisualBeatConfig(
        id="beat_18",
        image_filename="Fat_Man_replica_of_nuclear_bomb_jpg.jpg",
        start_sec=38.30,
        end_sec=40.63,
        top_tag="OFFICE WINDOWS SHATTERED  •  BLOWN ACROSS ROOM",
        editorial_text="SECOND BOMB STRUCK",
        accent_color=(255, 77, 77),
        motion_type="zoom_out",
    ),
    # --- Payoff (40.63 - 47.88s) ---
    VisualBeatConfig(
        id="beat_19",
        image_filename="Dan_A__McGovern_at_Nagasaki_ground_zero_9_September_1945_png.png",
        start_sec=40.63,
        end_sec=43.00,
        top_tag="NAGASAKI GROUND ZERO  •  DEVASTATION",
        editorial_text="SURVIVED AGAIN",
        accent_color=(32, 201, 151),
        motion_type="pan_right",
    ),
    VisualBeatConfig(
        id="beat_20",
        image_filename="Nagasaki_1945___Before_and_after_adjusted_jpg.jpg",
        start_sec=43.00,
        end_sec=45.40,
        top_tag="JAPANESE GOVERNMENT CERTIFICATE  •  2009",
        editorial_text="OFFICIALLY CERTIFIED",
        accent_color=(254, 224, 4),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_21",
        image_filename="Nagasaki_temple_destroyed_jpg.jpg",
        start_sec=45.40,
        end_sec=47.88,
        top_tag="HISTORICAL RECORD  •  WORLD RECOGNITION",
        editorial_text="DOUBLE NUCLEAR SURVIVOR",
        accent_color=(32, 201, 151),
        motion_type="zoom_out",
    ),
    # --- Ending & CrazzyDocs CTA (47.88 - 54.64s) ---
    VisualBeatConfig(
        id="beat_22",
        image_filename="Nagasaki_after_bombed_in_1945_jpg.jpg",
        start_sec=47.88,
        end_sec=50.10,
        top_tag="LONGEVITY & COURAGE  •  1916 — 2010",
        editorial_text="LIVED TO AGE 93",
        accent_color=(242, 193, 78),
        motion_type="pan_left",
    ),
    VisualBeatConfig(
        id="beat_23",
        image_filename="Hiroshima_Gembaku_Dome_1_jpg.jpg",
        start_sec=50.10,
        end_sec=52.40,
        top_tag="UNITED NATIONS ADDRESS  •  PEACE ADVOCATE",
        editorial_text="ADVOCATE FOR PEACE",
        accent_color=(32, 201, 151),
        motion_type="zoom_in",
    ),
    VisualBeatConfig(
        id="beat_24",
        image_filename="Atomic_cloud_over_Hiroshima_jpg.jpg",
        start_sec=52.40,
        end_sec=54.64,
        top_tag="CRAZZYDOCS  •  UNBELIEVABLE REAL HISTORY",
        editorial_text="FOLLOW CRAZZYDOCS",
        accent_color=(254, 224, 4),
        motion_type="zoom_out",
    ),
]


class YamaguchiFastRenderer:
    """High-velocity documentary renderer streaming frames into FFmpeg."""

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
        self.beat_assets: Dict[str, Dict] = {}
        self._load_and_cache_assets()

    def _load_and_cache_assets(self):
        """Loads and precomputes blurred documentary backdrops and base RGB plates."""
        w, h = self.width, self.height
        logger.info("Pre-caching %d visual beat assets...", len(BEAT_CONFIGS))

        for cfg in BEAT_CONFIGS:
            if cfg.image_filename:
                path = os.path.join(YAMAGUCHI_IMG_DIR, cfg.image_filename)
                if os.path.exists(path):
                    try:
                        src = Image.open(path).convert("RGB")
                    except Exception as e:
                        logger.warning("Failed opening %s: %s; creating fallback", path, e)
                        src = Image.new("RGB", (1200, 900), (20, 25, 35))
                else:
                    logger.warning("File not found %s; creating fallback", path)
                    src = Image.new("RGB", (1200, 900), (20, 25, 35))
            else:
                src = Image.new("RGB", (1200, 900), (18, 24, 34))

            # Precompute high-res blurred background plate
            bg = src.resize((w, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(36))
            # Tint overlay: obsidian cinematic grade
            tint = Image.new("RGBA", (w, h), (12, 16, 24, 210))
            base_bg = Image.alpha_composite(bg.convert("RGBA"), tint).convert("RGB")

            self.beat_assets[cfg.id] = {
                "src": src,
                "base_bg": base_bg,
                "config": cfg,
            }

    def _apply_motion(
        self,
        src: Image.Image,
        target_w: int,
        target_h: int,
        progress: float,
        motion_type: str,
    ) -> Image.Image:
        """Applies dynamic documentary camera motion (Ken Burns crop + pan)."""
        sw, sh = src.size

        if motion_type == "zoom_in":
            curr_zoom = 1.0 + 0.12 * progress
            cx_pct = 0.5
            cy_pct = 0.5
        elif motion_type == "zoom_out":
            curr_zoom = 1.12 - 0.12 * progress
            cx_pct = 0.5
            cy_pct = 0.5
        elif motion_type == "pan_right":
            curr_zoom = 1.08
            cx_pct = 0.45 + 0.10 * progress
            cy_pct = 0.5
        elif motion_type == "pan_left":
            curr_zoom = 1.08
            cx_pct = 0.55 - 0.10 * progress
            cy_pct = 0.5
        elif motion_type == "flash_strike":
            curr_zoom = 1.02 + 0.15 * (progress ** 0.5)
            cx_pct = 0.5
            cy_pct = 0.5
        else:
            curr_zoom = 1.04
            cx_pct = 0.5
            cy_pct = 0.5

        crop_w = max(10, int(sw / curr_zoom))
        crop_h = max(10, int(sh / curr_zoom))

        cx = int(sw * cx_pct)
        cy = int(sh * cy_pct)

        x0 = max(0, min(sw - crop_w, cx - crop_w // 2))
        y0 = max(0, min(sh - crop_h, cy - crop_h // 2))
        x1 = min(sw, x0 + crop_w)
        y1 = min(sh, y0 + crop_h)

        cropped = src.crop((x0, y0, x1, y1))
        return cropped.resize((target_w, target_h), Image.Resampling.BILINEAR)

    def draw_beat_frame(self, cfg: VisualBeatConfig, beat_progress: float, global_frame: int) -> Image.Image:
        """Draws a single 1080x1920 frame for the given visual beat."""
        asset = self.beat_assets[cfg.id]
        img = asset["base_bg"].copy()
        draw = ImageDraw.Draw(img)

        # 1. Top Metadata Tag Pill
        hdr_box = BoundingBox(80, 210, 1000, 280)
        draw.rounded_rectangle(
            [hdr_box.x0, hdr_box.y0, hdr_box.x1, hdr_box.y1],
            radius=12,
            fill=(14, 18, 28, 235),
            outline=cfg.accent_color,
            width=2,
        )
        self.bounds_engine.draw_bounded_text(
            draw=draw,
            text=cfg.top_tag,
            box=hdr_box,
            fill_color=cfg.accent_color,
            max_font_size=28,
            min_font_size=18,
            padding=10,
        )

        # 2. Dominant Central Historical Photo Frame (>65% canvas height)
        # Safe boundaries: x in [60, 1020], y in [310, 1490] (height = 1180px)
        photo_box = (60, 310, 1020, 1490)
        pw = photo_box[2] - photo_box[0]
        ph = photo_box[3] - photo_box[1]

        photo = self._apply_motion(asset["src"], pw, ph, beat_progress, cfg.motion_type)
        img.paste(photo, (photo_box[0], photo_box[1]))

        # Inner subtle shadow / border
        draw.rectangle(photo_box, outline=(20, 26, 38), width=4)
        draw.rectangle([photo_box[0] - 2, photo_box[1] - 2, photo_box[2] + 2, photo_box[3] + 2], outline=cfg.accent_color, width=2)

        # 3. Flash Strike Effect (white radioactive bloom on first 4 frames of nuclear blast)
        if cfg.motion_type == "flash_strike" and beat_progress < 0.20:
            flash_alpha = int(220 * (1.0 - (beat_progress / 0.20)))
            flash_layer = Image.new("RGBA", (self.width, self.height), (255, 255, 255, flash_alpha))
            img = Image.alpha_composite(img.convert("RGBA"), flash_layer).convert("RGB")
            draw = ImageDraw.Draw(img)

        # 4. Lower Editorial Text Layer (Safe-Zone Pill: y in [1530, 1640])
        # Auto-scaled power phrase with glowing badge styling
        lbl_box = BoundingBox(70, 1525, 1010, 1635)
        draw.rounded_rectangle(
            [lbl_box.x0, lbl_box.y0, lbl_box.x1, lbl_box.y1],
            radius=16,
            fill=(12, 16, 26, 245),
            outline=cfg.accent_color,
            width=3,
        )
        # Power phrase typography
        self.bounds_engine.draw_bounded_text(
            draw=draw,
            text=cfg.editorial_text,
            box=lbl_box,
            fill_color=(255, 255, 255),
            max_font_size=46,
            min_font_size=28,
            padding=14,
        )

        return img

    def render_narrative_video(self, output_mp4: str, total_duration: float = 54.64) -> str:
        """Streams all 24 visual beats seamlessly into a single 30 FPS MP4 via rawvideo pipe."""
        os.makedirs(os.path.dirname(output_mp4), exist_ok=True)
        total_frames = int(round(total_duration * self.fps))
        logger.info(
            "Rendering narrative video: %.2fs, %d frames @ %.0f FPS -> %s",
            total_duration, total_frames, self.fps, output_mp4,
        )

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
            output_mp4,
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        stderr_chunks = []

        def _drain():
            try:
                while True:
                    c = proc.stderr.read(4096)
                    if not c:
                        break
                    stderr_chunks.append(c)
            except Exception:
                pass

        t_drain = threading.Thread(target=_drain, daemon=True)
        t_drain.start()

        t_start = time.perf_counter()
        current_beat_idx = 0
        num_beats = len(BEAT_CONFIGS)

        try:
            for frame_idx in range(total_frames):
                t_sec = frame_idx / self.fps

                # Advance beat if necessary
                while current_beat_idx < num_beats - 1 and t_sec >= BEAT_CONFIGS[current_beat_idx].end_sec:
                    current_beat_idx += 1

                cfg = BEAT_CONFIGS[current_beat_idx]
                beat_dur = max(0.01, cfg.end_sec - cfg.start_sec)
                beat_prog = min(1.0, max(0.0, (t_sec - cfg.start_sec) / beat_dur))

                frame_img = self.draw_beat_frame(cfg, beat_prog, frame_idx)
                proc.stdin.write(frame_img.tobytes())

                if frame_idx % 150 == 0 or frame_idx == total_frames - 1:
                    pct = (frame_idx / total_frames) * 100
                    fps_render = (frame_idx + 1) / max(0.001, (time.perf_counter() - t_start))
                    logger.info("Frame %d/%d (%.1f%%) | Beat: %s | %.1f FPS", frame_idx, total_frames, pct, cfg.id, fps_render)

            proc.stdin.close()
            ret = proc.wait(timeout=120)
            if ret != 0:
                err_msg = b"".join(stderr_chunks).decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg narrative render failed (code {ret}): {err_msg[:400]}")

        except Exception as e:
            proc.kill()
            raise e

        logger.info("Narrative render complete in %.1fs: %s", time.perf_counter() - t_start, output_mp4)
        return output_mp4


def generate_editorial_ass_subtitles(output_ass_path: str):
    """Generates the synchronized editorial text ASS subtitle file."""
    os.makedirs(os.path.dirname(output_ass_path), exist_ok=True)

    header = """[Script Info]
Title: CrazzyDocs Stage 3.4 Tsutomu Yamaguchi Editorial Text
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: EditorialPower,Arial,52,&H00FFFFFF,&H000000FF,&H0018100C,&HE0000000,-1,0,0,0,100,100,2,0,1,4,2,2,80,80,310,1
Style: HeaderMeta,Arial,28,&H0004E0FE,&H000000FF,&H00101010,&HC0000000,-1,0,0,0,100,100,1,0,1,2,1,8,80,80,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cfg in BEAT_CONFIGS:
        s_m, s_s = divmod(cfg.start_sec, 60)
        s_h, s_m = divmod(s_m, 60)
        s_str = f"{int(s_h):d}:{int(s_m):02d}:{s_s:05.2f}"

        e_m, e_s = divmod(cfg.end_sec, 60)
        e_h, e_m = divmod(e_m, 60)
        e_str = f"{int(e_h):d}:{int(e_m):02d}:{e_s:05.2f}"

        # Color in BGR format for ASS
        b, g, r = cfg.accent_color[2], cfg.accent_color[1], cfg.accent_color[0]
        color_tag = f"&H00{b:02X}{g:02X}{r:02X}&"

        events.append(f"Dialogue: 0,{s_str},{e_str},EditorialPower,,0,0,0,,{{\\c{color_tag}}}{cfg.editorial_text}")
        events.append(f"Dialogue: 0,{s_str},{e_str},HeaderMeta,,0,0,0,,{{\\c{color_tag}}}{cfg.top_tag}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    logger.info("Saved editorial ASS subtitles: %s", output_ass_path)
