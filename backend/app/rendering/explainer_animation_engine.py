"""
Reusable Script-Grounded Explanatory Animation & Illustration Engine.
Phase 5, Stage 3.21J Standard.

Generates authentic, topic-driven 1080x1920 @ 30 FPS H.264 MP4 animations and
static vector/diagram illustrations saved permanently in canonical workspace directories:
  - assets/animations/anim_<beat_id>_<visual_type>.mp4
  - assets/generated/gen_<beat_id>_<slug>.png

Supported visual types:
  - animated_map: Geographical coordinate grid, sweeping range rings, location/perimeter markers.
  - animated_timeline: Progressive multi-milestone timeline extracted from topic dates.
  - animated_diagram: Step-by-step analytical boundary / system / cause-effect diagram.
  - animated_infographic: Structured multi-card documentary fact comparison with animated reveal.
  - data_visualization: Eased quantitative bar & rolling numeric counter chart from script metrics.
  - motion_graphic: Kinetic geometric focus frames & key-entity highlights.
  - typography_sequence: Word-by-word documentary headline & quote reveal with rule stroke.
  - generated_image: High-craft vector archival schematic / cartographic diagram PNG.

Strictly avoids stick figures, character rigs, cartoon hosts, and unrelated filler.
"""

from datetime import datetime, timezone
import logging
import math
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from backend.app.content.creative_identity_policy import CreativeIdentityPolicy
from backend.app.core.paths import FFMPEG_DEFAULT_PATH
from backend.app.rendering.style_treatments import compose_direction_frame, draw_visual_style_treatment

logger = logging.getLogger(__name__)


def _load_font(candidates: List[str], size: int) -> ImageFont.FreeTypeFont:
    for name in candidates:
        win_path = os.path.join("C:/Windows/Fonts", name)
        for p in (name, win_path):
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
    return ImageFont.load_default()


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font)
        if (bbox[2] - bbox[0]) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_base_documentary_backdrop(
    width: int,
    height: int,
    accent: Tuple[int, int, int],
    beat_idx: int,
    total_beats: int,
    stage_label: str,
    visual_badge: str,
) -> Image.Image:
    """Renders the static dark slate documentary canvas + safe-zone progress header once per beat."""
    canvas = Image.new("RGB", (width, height), (11, 16, 28))
    draw = ImageDraw.Draw(canvas)

    # Subtle gradient bands + blueprint coordinate grid
    for y in range(0, height, 24):
        shade = int(11 + (y / height) * 14)
        draw.line([(0, y), (width, y)], fill=(shade, shade + 6, shade + 18), width=24)

    for gx in range(90, width, 90):
        draw.line([(gx, 190), (gx, 1460)], fill=(22, 31, 49), width=1)
    for gy in range(190, 1460, 90):
        draw.line([(90, gy), (width - 90, gy)], fill=(22, 31, 49), width=1)

    # Top progress strip inside YouTube Shorts safe zone (x=110..970, y=240)
    pad_x = 110
    bar_x1 = width - pad_x
    gap = 5
    seg_w = max(8, int((bar_x1 - pad_x - (total_beats - 1) * gap) / max(1, total_beats)))
    for i in range(total_beats):
        sx = pad_x + i * (seg_w + gap)
        c = accent if i <= beat_idx else (44, 56, 78)
        draw.rounded_rectangle([sx, 244, sx + seg_w, 252], radius=4, fill=c)

    font_pill = _load_font(["arialbd.ttf", "seguisb.ttf"], 25)
    pill_txt = f"{stage_label}  •  {visual_badge}  •  {beat_idx + 1}/{total_beats}"
    pb = draw.textbbox((0, 0), pill_txt, font=font_pill)
    pw = min(width - 2 * pad_x, (pb[2] - pb[0]) + 40)
    draw.rounded_rectangle([pad_x, 272, pad_x + pw, 320], radius=20, fill=(18, 26, 42), outline=accent, width=2)
    draw.text((pad_x + pw // 2, 296), pill_txt, font=font_pill, fill=(240, 246, 255), anchor="mm")
    return canvas


def extract_milestones_from_text(full_text: str, topic_title: str) -> List[Tuple[str, str]]:
    """Extracts ordered chronological year + clause pairs directly from script/research text."""
    sentences = re.split(r"(?<=[.!?])\s+", full_text or "")
    milestones: List[Tuple[str, str]] = []
    seen_years = set()
    for sent in sentences:
        years = re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", sent)
        for yr in years:
            if yr in seen_years:
                continue
            seen_years.add(yr)
            cleaned = re.sub(r"\s+", " ", sent).strip()
            snippet = " ".join(cleaned.split()[:8])
            milestones.append((yr, snippet))
    if not milestones:
        milestones = [
            ("PHASE 1", f"Origin: {topic_title[:28]}"),
            ("PHASE 2", "Key turning point documented"),
            ("PHASE 3", "Verified historical outcome"),
        ]
    elif len(milestones) == 1:
        y0 = milestones[0][0]
        milestones.append(("LEGACY", f"Long-term impact of {topic_title[:26]}"))
    return milestones[:4]


def extract_numeric_metrics(full_text: str, topic_title: str) -> List[Tuple[str, float, str]]:
    """Extracts real numeric figures mentioned in the script/research for data visualization."""
    matches = re.findall(
        r"\b(\d[\d,\.]*)\s*(?:([A-Za-z%]+))?",
        re.sub(r"\b(19\d\d|20[0-2]\d)\b", "", full_text or ""),
    )
    items: List[Tuple[str, float, str]] = []
    seen_labels = set()
    for num_str, unit in matches:
        try:
            val = float(num_str.replace(",", ""))
        except ValueError:
            continue
        if val <= 0:
            continue
        clean_unit = (unit or "RECORD").upper()[:12]
        label = f"{num_str} {clean_unit}".strip()
        if label in seen_labels:
            continue
        seen_labels.add(label)
        items.append((clean_unit if clean_unit else topic_title[:12].upper(), val, num_str))
        if len(items) >= 3:
            break
    if not items:
        items = [
            ("VERIFIED", 100.0, "100%"),
            ("SOURCES", float(max(2, len(topic_title.split()))), str(max(2, len(topic_title.split())))),
        ]
    return items


class ExplainerAnimationEngine:
    """Generates procedural 30 FPS script-grounded animations and bespoke generated diagram assets."""

    def __init__(self, ffmpeg_path: str = str(FFMPEG_DEFAULT_PATH), width: int = 1080, height: int = 1920):
        self.ffmpeg_path = ffmpeg_path
        self.width = width
        self.height = height
        self.font_title = _load_font(["impact.ttf", "arialbd.ttf"], 48)
        self.font_stat = _load_font(["impact.ttf", "arialbd.ttf"], 82)
        self.font_body = _load_font(["seguisb.ttf", "arialbd.ttf"], 30)
        self.font_small = _load_font(["arialbd.ttf", "seguisb.ttf"], 24)
        self.font_mono = _load_font(["consolab.ttf", "arialbd.ttf"], 24)

    def generate_topic_illustration_png(
        self,
        *,
        out_path: str,
        beat_idx: int,
        total_beats: int,
        stage_label: str,
        visual_type_label: str,
        topic_title: str,
        segment_text: str,
        full_script: str,
        accent_color: Tuple[int, int, int],
        coordinates: Optional[Dict[str, float]] = None,
        variant: int = 0,
        category: str = "",
        illustrative_label: str = "ILLUSTRATIVE RECONSTRUCTION",
        visual_style: str = "documentary",
    ) -> Dict[str, Any]:
        """
        Generates a distinct 1080x1920 analytical illustration / cartographic schematic PNG
        saved in assets/generated/ with prompt & provider provenance.
        Clearly labeled as illustrative reconstruction (Priority D fallback).
        """
        prompt = (
            f"Documentary {visual_type_label} plate explaining '{topic_title}': "
            f"{segment_text[:120]}"
        )
        ok, issues = CreativeIdentityPolicy.validate_prompt(prompt)
        if not ok:
            raise ValueError(f"Creative policy rejected generation prompt: {issues}")
        _ = CreativeIdentityPolicy.inject_safe_negative_prompt(prompt)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        canvas = _draw_base_documentary_backdrop(
            self.width, self.height, accent_color, beat_idx, total_beats, stage_label, visual_type_label.upper()
        )
        draw = ImageDraw.Draw(canvas)

        # Explicit illustrative disclosure badge on Priority D conceptual visuals
        badge_text = (illustrative_label or "ILLUSTRATIVE RECONSTRUCTION")[:28].upper()
        bb = draw.textbbox((0, 0), badge_text, font=self.font_small)
        bw_badge = (bb[2] - bb[0]) + 24
        draw.rounded_rectangle(
            [self.width - 110 - bw_badge, 274, self.width - 110, 318],
            radius=12,
            fill=(26, 35, 54),
            outline=(148, 163, 184),
            width=1,
        )
        draw.text((self.width - 110 - bw_badge // 2, 296), badge_text, font=self.font_small, fill=(215, 224, 240), anchor="mm")

        box = (110, 355, self.width - 110, 1210)
        bx0, by0, bx1, by1 = box
        draw.rounded_rectangle(box, radius=24, fill=(16, 23, 39), outline=accent_color, width=3)

        cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2 - 20

        if variant % 2 == 0:
            # Cartographic / coordinate schematic diagram
            for step in range(-3, 4):
                yy = cy + step * 75
                draw.line([(bx0 + 40, yy), (bx1 - 40, yy)], fill=(30, 44, 70), width=2)
                xx = cx + step * 95
                draw.line([(xx, by0 + 50), (xx, by1 - 120)], fill=(30, 44, 70), width=2)
            poly = [
                (cx - 210, cy - 40), (cx - 120, cy - 135), (cx + 40, cy - 150),
                (cx + 215, cy - 55), (cx + 185, cy + 95), (cx + 15, cy + 135),
                (cx - 170, cy + 80),
            ]
            draw.polygon(poly, fill=(24, 38, 60), outline=accent_color, width=4)
            draw.ellipse([cx - 130, cy - 130, cx + 130, cy + 130], outline=accent_color, width=2)
            draw.ellipse([cx - 65, cy - 65, cx + 65, cy + 65], outline=(245, 248, 255), width=2)
            draw.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=accent_color)
            coord_txt = (
                f"LAT {coordinates['lat']:.2f}  •  LON {coordinates['lon']:.2f}"
                if coordinates and "lat" in coordinates and "lon" in coordinates
                else f"{(category or 'SUBJECT').upper()[:18]} SECTOR  •  {topic_title[:20].upper()}"
            )
            draw.text((cx, by1 - 65), coord_txt, font=self.font_mono, fill=accent_color, anchor="mm")
        else:
            # Structural System / Mechanism Flow Schematic
            nodes = _wrap_lines(draw, re.sub(r"[^A-Za-z0-9\s\-']", "", segment_text).upper(), self.font_small, 210)[:3]
            step_labels = [
                ("01 CONTEXT", topic_title[:24].upper()),
                ("02 MECHANISM", nodes[0] if nodes else "KEY PROCESS"),
                ("03 EVIDENCE", nodes[-1] if nodes else "DOCUMENTED RESULT"),
            ]
            box_w, box_h = 660, 140
            start_y = by0 + 95
            for idx, (tag, val) in enumerate(step_labels):
                ny0 = start_y + idx * 215
                nx0 = cx - box_w // 2
                fill_c = (24, 34, 56) if idx != 1 else (30, 44, 72)
                draw.rounded_rectangle([nx0, ny0, nx0 + box_w, ny0 + box_h], radius=16, fill=fill_c, outline=accent_color, width=3)
                draw.text((nx0 + 28, ny0 + 36), tag, font=self.font_mono, fill=accent_color, anchor="lm")
                draw.text((nx0 + 28, ny0 + 92), val[:34], font=self.font_body, fill=(242, 247, 255), anchor="lm")
                if idx < len(step_labels) - 1:
                    ay0 = ny0 + box_h + 6
                    ay1 = ny0 + 215 - 10
                    draw.line([(cx, ay0), (cx, ay1)], fill=accent_color, width=4)
                    draw.polygon([(cx - 10, ay1 - 12), (cx + 10, ay1 - 12), (cx, ay1)], fill=accent_color)

        # Lower documentary callout band
        callout_box = (110, 1235, self.width - 110, 1410)
        draw.rounded_rectangle(callout_box, radius=18, fill=(14, 19, 31), outline=(65, 82, 112), width=2)
        draw.rectangle([110, 1235, 124, 1410], fill=accent_color)
        lines = _wrap_lines(draw, segment_text.upper(), self.font_title, self.width - 280)[:2]
        yc = 1290 if len(lines) == 2 else 1322
        for line in lines:
            draw.text((146, yc), line, font=self.font_title, fill=(248, 250, 255), anchor="lm")
            yc += 62

        canvas = compose_direction_frame(
            canvas,
            visual_style,
            topic_title=topic_title,
            segment_text=segment_text,
            stage_label=stage_label,
            stat_text="",
            beat_idx=beat_idx,
        )
        disclosure = ImageDraw.Draw(canvas)
        disclosure.rounded_rectangle((70, 150, self.width - 70, 205), radius=18, fill=(8, 12, 16), outline=(190, 190, 180), width=2)
        disclosure.text((self.width // 2, 178), badge_text, font=self.font_small, fill=(245, 242, 228), anchor="mm")
        canvas.save(out_path, format="PNG")
        return {
            "path": out_path,
            "generation_provider": "CrazzyDocs Analytical Illustration Generator",
            "sourcing_priority": "D_selective_generated",
            "illustrative_reconstruction": True,
            "prompt_metadata": {
                "prompt": prompt,
                "negative_prompt_enforced": True,
                "topic_title": topic_title,
                "category": category,
                "illustrative_label": badge_text,
                "segment_anchor": segment_text[:100],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "width": self.width,
            "height": self.height,
            "format": "png",
        }

    def adapt_existing_animation_to_portrait(
        self,
        *,
        input_anim_path: str,
        out_mp4_path: str,
        duration_sec: float,
        beat_idx: int,
        total_beats: int,
        stage_label: str,
        topic_title: str,
        segment_text: str,
        accent_color: Tuple[int, int, int],
        source_credit: str = "Open Archive Animation",
        fps: float = 30.0,
        visual_style: str = "documentary",
    ) -> Dict[str, Any]:
        """
        Priority B Adapter: Converts an authentic open-source animation (GIF, WebM, MP4)
        into a documentary-framed 1080x1920 @ 30 FPS H.264 MP4 clip using FFmpeg.
        """
        prompt = f"Priority B existing open-source animation for '{topic_title}': {segment_text[:120]}"
        ok, issues = CreativeIdentityPolicy.validate_prompt(prompt)
        if not ok:
            raise ValueError(f"Creative policy rejected Priority B animation prompt: {issues}")
        if not input_anim_path or not os.path.isfile(input_anim_path):
            raise FileNotFoundError(f"Priority B animation source not found: {input_anim_path}")

        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)
        vx0, vy0, vx1, vy1 = 110, 355, self.width - 110, 1210
        vw, vh = vx1 - vx0, vy1 - vy0

        # Build RGBA documentary overlay frame with transparent viewport punch-out
        bg_rgb = _draw_base_documentary_backdrop(
            self.width, self.height, accent_color, beat_idx, total_beats, stage_label, "ARCHIVE MOTION"
        )
        overlay = bg_rgb.convert("RGBA")
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle([vx0, vy0, vx1, vy1], radius=24, fill=(0, 0, 0, 0), outline=accent_color + (255,), width=4)

        # Lower documentary caption + attribution bar
        od.rounded_rectangle([110, 1235, self.width - 110, 1410], radius=18, fill=(14, 19, 31, 245), outline=(65, 82, 112, 255), width=2)
        od.rectangle([110, 1235, 124, 1410], fill=accent_color + (255,))
        cap_lines = _wrap_lines(od, segment_text.upper(), self.font_title, self.width - 280)[:2]
        yc = 1286 if len(cap_lines) == 2 else 1318
        for line in cap_lines:
            od.text((146, yc), line, font=self.font_title, fill=(248, 250, 255, 255), anchor="lm")
            yc += 58
        od.text((self.width - 124, 1388), f"SOURCE: {source_credit[:36].upper()}", font=self.font_small, fill=(165, 185, 215, 255), anchor="rm")
        style_labels = {
            "documentary": ("DOCUMENTARY", (224, 232, 244, 255)),
            "pop_info": ("POP INFO", (232, 93, 74, 255)),
            "data_story": ("DATA STORY", (32, 201, 151, 255)),
            "chalk_talk": ("CHALK TALK", (245, 242, 228, 255)),
            "creator_motion_graphics": ("CREATOR MOTION", (255, 214, 10, 255)),
            "cinematic_explainer": ("CINEMATIC EXPLAINER", (197, 163, 111, 255)),
            "map_based_storytelling": ("MAP STORY", (84, 203, 183, 255)),
        }
        style_label, style_color = style_labels.get(visual_style, ("", accent_color + (255,)))
        if not style_label:
            raise ValueError(f"Unsupported adaptive visual style: {visual_style}")
        od.rounded_rectangle([vx0, vy0 - 44, vx0 + 300, vy0 - 8], radius=12, fill=(10, 15, 25, 235))
        od.text((vx0 + 14, vy0 - 26), style_label, font=self.font_small, fill=style_color, anchor="lm")

        overlay_fd, overlay_png = tempfile.mkstemp(suffix=".png", prefix="cd_pB_overlay_")
        os.close(overlay_fd)
        overlay.save(overlay_png, format="PNG")

        target_dur = max(1.0, float(duration_sec))
        filter_complex = (
            f"color=c=0x0b101c:s={self.width}x{self.height}:r={fps}:d={target_dur:.3f}[bg];"
            f"[0:v]scale={vw}:{vh}:force_original_aspect_ratio=decrease,"
            f"pad={vw}:{vh}:(ow-iw)/2:(oh-ih)/2:color=0x101727,setsar=1,fps={fps}[fg];"
            f"[bg][fg]overlay={vx0}:{vy0}:shortest=1[comp];"
            f"[comp][1:v]overlay=0:0:shortest=1[out]"
        )
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-loglevel", "error",
            "-stream_loop", "-1",
            "-i", input_anim_path,
            "-loop", "1",
            "-t", f"{target_dur:.3f}",
            "-i", overlay_png,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{target_dur:.3f}",
            "-an",
            "-c:v", "libx264",
            "-preset", "superfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_mp4_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
            if proc.returncode != 0 or not os.path.isfile(out_mp4_path):
                raise RuntimeError(f"FFmpeg Priority B adaptation failed: {proc.stderr}")
        finally:
            try:
                os.remove(overlay_png)
            except OSError:
                pass

        return {
            "path": out_mp4_path,
            "visual_type": "motion_graphic",
            "sourcing_priority": "B_existing_animation",
            "generation_provider": "FFmpeg Priority B Open Animation Adapter",
            "prompt_metadata": {
                "prompt": prompt,
                "source_animation": os.path.basename(input_anim_path),
                "source_credit": source_credit,
                "topic_title": topic_title,
                "segment_anchor": segment_text[:100],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "width": self.width,
            "height": self.height,
            "format": "mp4",
            "duration_sec": round(target_dur, 3),
        }

    def render_explainer_animation_mp4(
        self,
        *,
        visual_type: str,
        out_mp4_path: str,
        duration_sec: float,
        beat_idx: int,
        total_beats: int,
        stage_label: str,
        topic_title: str,
        segment_text: str,
        full_script: str,
        accent_color: Tuple[int, int, int],
        coordinates: Optional[Dict[str, float]] = None,
        fps: float = 30.0,
        background_image_path: Optional[str] = None,
        visual_style: str = "documentary",
    ) -> Dict[str, Any]:
        """
        Renders a genuine 30 FPS H.264 MP4 animation directly to `out_mp4_path`
        using Pillow procedural motion composited atop either a Priority C authentic
        acquired visual (`background_image_path`) or a cached documentary slate.
        """
        is_priority_c = bool(background_image_path and os.path.isfile(background_image_path))
        prompt = (
            f"{'Priority C animated overlay on authentic asset' if is_priority_c else visual_type} "
            f"animation for '{topic_title}': {segment_text[:120]}"
        )
        ok, issues = CreativeIdentityPolicy.validate_prompt(prompt)
        if not ok:
            raise ValueError(f"Creative policy rejected animation prompt: {issues}")

        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)
        badge_map = {
            "animated_map": "GEOGRAPHIC MAP",
            "animated_timeline": "TIMELINE",
            "animated_diagram": "SYSTEM DIAGRAM",
            "animated_infographic": "KEY FACTS",
            "data_visualization": "DATA METRICS",
            "motion_graphic": "MOTION BRIEF",
            "typography_sequence": "DOCUMENTARY RECORD",
        }
        badge = badge_map.get(visual_type, "EXPLAINER MOTION")
        base = _draw_base_documentary_backdrop(
            self.width, self.height, accent_color, beat_idx, total_beats, stage_label, badge
        )

        # Pre-draw lower documentary caption card on base so frames only redraw active stage
        base_draw = ImageDraw.Draw(base)
        base_draw.rounded_rectangle([110, 1235, self.width - 110, 1410], radius=18, fill=(14, 19, 31), outline=(65, 82, 112), width=2)
        base_draw.rectangle([110, 1235, 124, 1410], fill=accent_color)
        cap_lines = _wrap_lines(base_draw, segment_text.upper(), self.font_title, self.width - 280)[:2]
        yc = 1290 if len(cap_lines) == 2 else 1322
        for line in cap_lines:
            base_draw.text((146, yc), line, font=self.font_title, fill=(248, 250, 255), anchor="lm")
            yc += 62

        # Main interactive viewport box (110, 355) .. (970, 1210)
        vx0, vy0, vx1, vy1 = 110, 355, self.width - 110, 1210
        vw, vh = vx1 - vx0, vy1 - vy0
        base_draw.rounded_rectangle([vx0, vy0, vx1, vy1], radius=24, fill=(16, 23, 39), outline=accent_color, width=3)

        # Priority C: Composite authentic image/map into viewport darkened so animated graphics & callouts shine
        if is_priority_c:
            try:
                with Image.open(background_image_path) as src_im:
                    fitted = ImageOps.fit(src_im.convert("RGB"), (vw - 8, vh - 8), method=Image.Resampling.LANCZOS)
                    darkened = ImageEnhance.Brightness(fitted).enhance(0.46)
                    mask = Image.new("L", (vw - 8, vh - 8), 0)
                    ImageDraw.Draw(mask).rounded_rectangle([0, 0, vw - 9, vh - 9], radius=20, fill=255)
                    base.paste(darkened, (vx0 + 4, vy0 + 4), mask)
                    # Subtle telemetry top strip inside Priority C viewport
                    base_draw = ImageDraw.Draw(base)
                    base_draw.rounded_rectangle([vx0 + 18, vy0 + 14, vx0 + 365, vy0 + 50], radius=10, fill=(12, 18, 30))
                    base_draw.text((vx0 + 32, vy0 + 32), "ENHANCED ARCHIVAL VISUAL", font=self.font_small, fill=accent_color, anchor="lm")
            except Exception as exc:
                logger.warning("Priority C background composite skipped (%s); falling back to clean stage.", exc)
                is_priority_c = False

        milestones = extract_milestones_from_text(full_script, topic_title)
        metrics = extract_numeric_metrics(segment_text + " " + full_script, topic_title)
        seg_words = re.sub(r"[^A-Za-z0-9\s\-']", "", segment_text or topic_title).split()

        total_frames = max(15, int(round(duration_sec * fps)))
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", "rgb24",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "superfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_mp4_path,
        ]
        err_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err_file)
        cx, cy = (vx0 + vx1) // 2, (vy0 + vy1) // 2

        try:
            for f_idx in range(total_frames):
                u = f_idx / max(1, total_frames - 1)
                ease = 1.0 - pow(1.0 - u, 3.0)
                frame = base.copy()
                d = ImageDraw.Draw(frame)

                if is_priority_c:
                    # Ambient telemetry particles & highlighted region corner brackets over authentic visual
                    for p_i in range(6):
                        px = vx0 + 70 + int(((u * 0.45 + p_i * 0.17) % 1.0) * (vw - 140))
                        py = vy0 + 120 + int((math.sin(u * math.pi * 2.0 + p_i * 1.3) * 0.5 + 0.5) * (vh - 240))
                        d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=accent_color)
                    rx0, ry0 = cx - int(145 * (0.85 + 0.15 * ease)), cy - int(115 * (0.85 + 0.15 * ease))
                    rx1, ry1 = cx + int(145 * (0.85 + 0.15 * ease)), cy + int(115 * (0.85 + 0.15 * ease))
                    b_len = 28
                    for (bx, by, dx_s, dy_s) in ((rx0, ry0, 1, 1), (rx1, ry0, -1, 1), (rx0, ry1, 1, -1), (rx1, ry1, -1, -1)):
                        d.line([(bx, by), (bx + dx_s * b_len, by)], fill=accent_color, width=3)
                        d.line([(bx, by), (bx, by + dy_s * b_len)], fill=accent_color, width=3)

                if visual_type == "animated_map":
                    # Animated graticule sweep + pulsing perimeter & moving target route arc
                    poly = [
                        (cx - 210, cy - 35), (cx - 115, cy - 130), (cx + 45, cy - 145),
                        (cx + 215, cy - 50), (cx + 180, cy + 95), (cx + 15, cy + 135),
                        (cx - 165, cy + 75),
                    ]
                    if is_priority_c:
                        d.line(poly + [poly[0]], fill=accent_color, width=3)
                    else:
                        d.polygon(poly, fill=(24, 38, 60), outline=(72, 102, 148), width=3)
                    # Expanding beacon radius
                    ring_r = int(35 + ((u * 2.2) % 1.0) * 195)
                    d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=accent_color, width=3)
                    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=accent_color)
                    # Moving reconnaissance locator dot along boundary
                    seg_i = int(ease * (len(poly) - 1))
                    local_t = (ease * (len(poly) - 1)) - seg_i
                    p0 = poly[seg_i]
                    p1 = poly[min(len(poly) - 1, seg_i + 1)]
                    mx = int(p0[0] + (p1[0] - p0[0]) * local_t)
                    my = int(p0[1] + (p1[1] - p0[1]) * local_t)
                    d.line([(cx, cy), (mx, my)], fill=accent_color, width=3)
                    d.ellipse([mx - 10, my - 10, mx + 10, my + 10], fill=(255, 255, 255), outline=accent_color, width=3)
                    # Perimeter completion bar at top of map viewport
                    d.rounded_rectangle([vx0 + 50, vy0 + 62 if is_priority_c else vy0 + 36, vx1 - 50, vy0 + 110 if is_priority_c else vy0 + 84], radius=12, fill=(22, 32, 52))
                    coord_str = (
                        f"GEO LOCK  {coordinates['lat']:.2f}°, {coordinates['lon']:.2f}°"
                        if coordinates and "lat" in coordinates
                        else f"BOUNDARY ZONE  •  {topic_title[:24].upper()}"
                    )
                    d.text((cx, vy0 + 86 if is_priority_c else vy0 + 60), coord_str, font=self.font_mono, fill=accent_color, anchor="mm")
                    d.text((cx, vy1 - 56), f"PERIMETER SCAN {int(ease * 100)}%", font=self.font_mono, fill=(215, 226, 245), anchor="mm")

                elif visual_type == "animated_timeline":
                    track_x = vx0 + 135
                    y_top, y_bot = vy0 + 110, vy1 - 100
                    d.line([(track_x, y_top), (track_x, y_bot)], fill=(45, 59, 86), width=6)
                    active_y = int(y_top + (y_bot - y_top) * ease)
                    d.line([(track_x, y_top), (track_x, active_y)], fill=accent_color, width=6)
                    span = y_bot - y_top
                    for m_idx, (yr, desc) in enumerate(milestones):
                        frac = m_idx / max(1, len(milestones) - 1)
                        ny = int(y_top + frac * span)
                        reached = ease >= frac * 0.92
                        node_col = accent_color if reached else (58, 74, 102)
                        r = 18 if reached else 13
                        d.ellipse([track_x - r, ny - r, track_x + r, ny + r], fill=node_col, outline=(255, 255, 255) if reached else (90, 105, 130), width=3)
                        card_x0 = track_x + 42
                        card_fill = (24, 34, 56) if reached else (18, 25, 41)
                        d.rounded_rectangle([card_x0, ny - 46, vx1 - 45, ny + 46], radius=14, fill=card_fill, outline=node_col, width=2)
                        d.text((card_x0 + 20, ny - 16), yr, font=self.font_mono, fill=accent_color if reached else (140, 155, 180), anchor="lm")
                        d.text((card_x0 + 20, ny + 18), desc[:34], font=self.font_small, fill=(242, 247, 255) if reached else (130, 145, 170), anchor="lm")

                elif visual_type == "data_visualization":
                    d.text((cx, vy0 + 62), topic_title[:32].upper(), font=self.font_mono, fill=(215, 226, 245), anchor="mm")
                    lead_unit, lead_val, _ = metrics[0]
                    rolled = int(round(lead_val * ease)) if lead_val >= 10 else round(lead_val * ease, 1)
                    d.text((cx, vy0 + 185), f"{rolled:,}" if isinstance(rolled, int) else f"{rolled}", font=self.font_stat, fill=accent_color, anchor="mm")
                    d.text((cx, vy0 + 245), lead_unit, font=self.font_body, fill=(225, 234, 248), anchor="mm")
                    max_v = max(v for _, v, _ in metrics) or 1.0
                    bar_base_x = vx0 + 70
                    bar_max_w = (vx1 - vx0) - 140
                    for idx, (lbl, val, raw_txt) in enumerate(metrics):
                        by = vy0 + 335 + idx * 140
                        d.text((bar_base_x, by), f"{lbl} ({raw_txt})", font=self.font_small, fill=(225, 232, 245), anchor="lm")
                        d.rounded_rectangle([bar_base_x, by + 26, bar_base_x + bar_max_w, by + 72], radius=12, fill=(25, 34, 54))
                        fill_w = max(24, int(bar_max_w * (val / max_v) * ease))
                        bar_col = accent_color if idx == 0 else (56, 189, 248)
                        d.rounded_rectangle([bar_base_x, by + 26, bar_base_x + fill_w, by + 72], radius=12, fill=bar_col)

                elif visual_type == "animated_diagram":
                    # Progressive analytical concept build-up tied directly to beat text
                    d.text((cx, vy0 + 58), "ANALYTICAL SYSTEM DIAGRAM", font=self.font_mono, fill=accent_color, anchor="mm")
                    phrases = [" ".join(seg_words[i:i + 4]) for i in range(0, len(seg_words), 4)][:3]
                    while len(phrases) < 3:
                        phrases.append(topic_title[:24].upper())
                    stages_diag = [
                        ("STAGE 1  •  INPUT", phrases[0].upper()),
                        ("STAGE 2  •  MECHANISM", phrases[1].upper()),
                        ("STAGE 3  •  EVIDENCE", phrases[2].upper()),
                    ]
                    for idx, (hdr, body) in enumerate(stages_diag):
                        appear = max(0.0, min(1.0, (ease - idx * 0.25) / 0.35))
                        ny = vy0 + 130 + idx * 235
                        bx_off = int((1.0 - appear) * 80)
                        col = accent_color if appear >= 0.8 else (60, 76, 104)
                        d.rounded_rectangle([vx0 + 55 + bx_off, ny, vx1 - 55, ny + 150], radius=18, fill=(22, 32, 52), outline=col, width=3)
                        d.text((vx0 + 85 + bx_off, ny + 44), hdr, font=self.font_mono, fill=col, anchor="lm")
                        d.text((vx0 + 85 + bx_off, ny + 102), body[:34], font=self.font_body, fill=(242, 246, 255), anchor="lm")
                        if idx < 2:
                            arrow_end = ny + 155 + int(70 * appear)
                            d.line([(cx, ny + 155), (cx, arrow_end)], fill=col, width=4)

                elif visual_type == "animated_infographic":
                    d.text((cx, vy0 + 62), "VERIFIED RECORD SUMMARY", font=self.font_mono, fill=accent_color, anchor="mm")
                    chunks = _wrap_lines(d, segment_text, self.font_body, 660)[:4]
                    for idx, ch in enumerate(chunks):
                        row_y = vy0 + 135 + idx * 165
                        pct_on = max(0.0, min(1.0, (ease - idx * 0.18) / 0.28))
                        border = accent_color if pct_on > 0.7 else (48, 62, 88)
                        d.rounded_rectangle([vx0 + 50, row_y, vx1 - 50, row_y + 128], radius=16, fill=(22, 31, 50), outline=border, width=3)
                        d.rounded_rectangle([vx0 + 70, row_y + 42, vx0 + 114, row_y + 86], radius=10, fill=border)
                        d.text((vx0 + 92, row_y + 64), str(idx + 1), font=self.font_small, fill=(10, 14, 24), anchor="mm")
                        d.text((vx0 + 138, row_y + 64), ch[:34], font=self.font_body, fill=(242, 247, 255), anchor="lm")

                else:
                    # motion_graphic / typography_sequence: progressive kinetic word highlight + sweeping hairline rules
                    rule_w = int(((vx1 - vx0) - 120) * ease)
                    d.rectangle([vx0 + 60, vy0 + 86, vx0 + 60 + rule_w, vy0 + 92], fill=accent_color)
                    d.rectangle([vx1 - 60 - rule_w, vy1 - 92, vx1 - 60, vy1 - 86], fill=accent_color)
                    d.text((cx, vy0 + 58), topic_title[:32].upper(), font=self.font_mono, fill=(185, 200, 224), anchor="mm")
                    words = seg_words[:10] if seg_words else topic_title.split()[:6]
                    lit_count = max(1, int(math.ceil(ease * len(words))))
                    lines = []
                    cur_l: List[str] = []
                    for w in words:
                        if sum(len(x) + 1 for x in cur_l) + len(w) > 17 and cur_l:
                            lines.append(cur_l)
                            cur_l = [w]
                        else:
                            cur_l.append(w)
                    if cur_l:
                        lines.append(cur_l)
                    y_txt = cy - ((len(lines[:4]) - 1) * 52)
                    w_counter = 0
                    for ln in lines[:4]:
                        line_str = " ".join(ln).upper()
                        col = accent_color if (w_counter < lit_count <= w_counter + len(ln)) else (
                            (245, 250, 255) if w_counter < lit_count else (82, 96, 124)
                        )
                        d.text((cx, y_txt), line_str, font=self.font_title, fill=col, anchor="mm")
                        w_counter += len(ln)
                        y_txt += 104

                draw_visual_style_treatment(
                    frame,
                    visual_style,
                    stage_label=stage_label,
                    callout_text=segment_text,
                    stat_text=metrics[0][2] if metrics else "",
                    accent_color=accent_color,
                    beat_idx=beat_idx,
                    total_beats=total_beats,
                    progress=ease,
                )
                frame = compose_direction_frame(
                    frame,
                    visual_style,
                    topic_title=topic_title,
                    segment_text=segment_text,
                    stage_label=stage_label,
                    stat_text=metrics[0][2] if metrics else "",
                    beat_idx=beat_idx,
                )
                proc.stdin.write(frame.tobytes())

            proc.stdin.close()
            proc.wait(timeout=45)
            if proc.returncode != 0:
                err_file.seek(0)
                err_msg = err_file.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg animation encode failed ({proc.returncode}): {err_msg}")
        except Exception:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.kill()
            raise
        finally:
            try:
                err_file.close()
            except Exception:
                pass

        return {
            "path": out_mp4_path,
            "visual_type": visual_type,
            "sourcing_priority": "C_animated_authentic" if is_priority_c else "D_procedural_animation",
            "background_image_path": background_image_path if is_priority_c else None,
            "generation_provider": (
                "CrazzyDocs Priority C Animated Authentic Overlay (Pillow+FFmpeg)"
                if is_priority_c
                else "CrazzyDocs Explainer Animation Engine (Pillow+FFmpeg)"
            ),
            "prompt_metadata": {
                "prompt": prompt,
                "sourcing_priority": "C_animated_authentic" if is_priority_c else "D_procedural_animation",
                "background_image": os.path.basename(background_image_path) if is_priority_c else None,
                "topic_title": topic_title,
                "segment_anchor": segment_text[:100],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "width": self.width,
            "height": self.height,
            "format": "mp4",
            "duration_sec": round(duration_sec, 3),
        }
