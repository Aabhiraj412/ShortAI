"""
High-Impact YouTube Thumbnail Generator for CrazzyDocs.

Produces broadcast-quality thumbnails tailored for mobile click-through rate (CTR):
1. Default Shorts Profile: 1080x1920 (9:16 vertical) with interface safe zones.
2. Standard Video Profile: 1280x720 (16:9 widescreen) with duration badge safe zones.
"""

from enum import Enum
import logging
import math
import os
import random
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
THUMBNAIL_DIR = os.path.join(WORKSPACE_ROOT, "media", "thumbnails")


class ThumbnailProfile(str, Enum):
    SHORTS_VERTICAL = "9:16"     # 1080 x 1920 (Mandatory for YouTube Shorts)
    STANDARD_HORIZONTAL = "16:9" # 1280 x 720 (Standard YouTube longform)


class ThumbnailGenerator:
    """Generates broadcast-quality, high-CTR thumbnails for YouTube Shorts and Videos."""

    def __init__(self, profile: ThumbnailProfile = ThumbnailProfile.SHORTS_VERTICAL):
        self.profile = profile
        if profile == ThumbnailProfile.SHORTS_VERTICAL:
            self.width = 1080
            self.height = 1920
        else:
            self.width = 1280
            self.height = 720

        os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        self._init_fonts()

    def _init_fonts(self):
        """Loads impactful typography tailored for vertical mobile visibility."""
        font_candidates = ["impact.ttf", "arialbd.ttf", "segoeui.ttf"]
        self.title_font = None
        self.badge_font = None
        self.sub_font = None

        size_title = 112 if self.profile == ThumbnailProfile.SHORTS_VERTICAL else 92
        size_badge = 32 if self.profile == ThumbnailProfile.SHORTS_VERTICAL else 26
        size_sub = 44 if self.profile == ThumbnailProfile.SHORTS_VERTICAL else 36

        for cand in font_candidates:
            try:
                self.title_font = ImageFont.truetype(cand, size_title)
                self.badge_font = ImageFont.truetype("arialbd.ttf", size_badge)
                self.sub_font = ImageFont.truetype("arialbd.ttf", size_sub)
                break
            except Exception:
                continue

        if not self.title_font:
            self.title_font = ImageFont.load_default()
            self.badge_font = self.title_font
            self.sub_font = self.title_font

    def generate_shorts_molasses_thumbnail(
        self,
        output_path: str = os.path.join(THUMBNAIL_DIR, "showcase_stage_3_3_molasses_flood.png"),
        title_text: str = "THE DEADLY WAVE",
    ) -> str:
        """
        Renders the mandatory 1080x1920 (9:16) vertical Shorts thumbnail:
        - Canvas: 1080 x 1920 vertical canvas.
        - Dominant Focal Subject: A towering, catastrophic 25-ft wall of molasses rising
          above the historic 1919 Boston industrial North End waterfront.
        - Depth & Cinematic Lighting: Deep obsidian atmosphere, cold winter twilight,
          amber fluid highlights, glowing fissure in the ruptured storage tank.
        - Kinetic Debris: Sheared rivets, fractured dock timbers, viscous syrup spray.
        - Minimalist High-Impact Typography: 'THE DEADLY WAVE' (3 words, huge bold white
          with double-drop shadow and amber rim light).
        - Safe Zones: Text is positioned in the upper-mid safe area (y: 350 - 540),
          completely avoiding YouTube Shorts right-side buttons (x > 940) and bottom
          channel/audio title bar (y > 1650).
        """
        w, h = 1080, 1920
        img = Image.new("RGB", (w, h), (10, 14, 22))
        draw = ImageDraw.Draw(img)

        # 1. Atmospheric Cold Winter Sky Gradient
        for y in range(h):
            ratio = y / h
            r = int(14 * (1 - ratio) + 48 * ratio)
            g = int(20 * (1 - ratio) + 26 * ratio)
            b = int(30 * (1 - ratio) + 16 * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

        # 2. Historical Boston North End Skyline Silhouettes (y: 650 - 1100)
        buildings = [
            (50, 720, 220, 1150),
            (240, 680, 410, 1150),
            (700, 700, 880, 1150),
            (900, 640, 1040, 1150),
        ]
        for bx0, by0, bx1, by1 in buildings:
            draw.rectangle([bx0, by0, bx1, by1], fill=(18, 24, 34))
            for wy in range(by0 + 35, by1 - 50, 45):
                for wx in range(bx0 + 25, bx1 - 25, 38):
                    draw.rectangle([wx, wy, wx + 12, wy + 20], fill=(75, 60, 40))

        # Ruptured 50-Foot Industrial Steel Tank Silhouette (Center background)
        tx0, ty0, tx1, ty1 = 360, 560, 780, 1050
        draw.rectangle([tx0, ty0 + 40, tx1, ty1], fill=(24, 30, 42), outline=(40, 52, 70), width=3)
        draw.ellipse([tx0, ty0, tx1, ty0 + 80], fill=(32, 40, 56))

        # Glowing rupture crack bursting through steel wall
        fissure = [(570, ty0 + 60), (550, 680), (590, 780), (540, 900), (580, 1020)]
        draw.line(fissure, fill=(255, 100, 30), width=8)
        draw.line(fissure, fill=(255, 210, 80), width=3)

        # 3. Dominant Foreground: Massive Curling Molasses Tsunami Wave (y: 950 - 1920)
        wave_pts = [
            (0, h),
            (0, 1150),
            (180, 1080),
            (420, 1000),
            (680, 940),
            (880, 980),
            (w, 1050),
            (w, h),
        ]
        draw.polygon(wave_pts, fill=(22, 10, 4))

        crest_pts = [
            (0, h),
            (0, 1220),
            (240, 1140),
            (520, 1060),
            (760, 1020),
            (960, 1060),
            (w, 1120),
            (w, h),
        ]
        draw.polygon(crest_pts, fill=(56, 23, 8))

        # Viscous fluid sheen highlights
        for i in range(18):
            sy0 = 1040 + i * 42
            curve = [
                (0, sy0 + 25),
                (280, sy0 - 20),
                (580, sy0 - 35),
                (860, sy0 - 10),
                (w, sy0 + 30),
            ]
            highlight_color = (210 + i * 2, 110 + i * 4, 35) if i < 5 else (110, 50, 18)
            draw.line(curve, fill=highlight_color, width=max(3, 8 - i // 2))

        # Foam and spray droplets
        random.seed(1919)
        for _ in range(450):
            rx = random.randint(40, w - 40)
            ry = random.randint(920, 1750)
            rad = random.randint(3, 12)
            color = random.choice([(242, 193, 78), (217, 130, 43), (140, 65, 20), (255, 225, 140)])
            draw.ellipse([rx - rad, ry - rad, rx + rad, ry + rad], fill=color)

        # Splintered dock beams
        beams = [
            [(220, 1180), (380, 1280)],
            [(740, 1120), (890, 1220)],
            [(480, 1320), (620, 1420)],
        ]
        for p0, p1 in beams:
            draw.line([p0, p1], fill=(130, 95, 60), width=14)
            draw.line([p0, p1], fill=(60, 40, 20), width=6)

        # Sheared steel rivets
        for rvx, rvy in [(420, 1060), (580, 1100), (760, 1040), (320, 1140), (850, 1110)]:
            draw.ellipse([rvx - 7, rvy - 7, rvx + 7, rvy + 7], fill=(230, 235, 245))
            draw.line([(rvx, rvy), (rvx - 30, rvy - 20)], fill=(255, 200, 100), width=3)

        # 4. Cinematic Vignette
        vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        v_draw = ImageDraw.Draw(vignette)
        for vy in range(h - 350, h):
            alpha = int(220 * (vy - (h - 350)) / 350)
            v_draw.line([(0, vy), (w, vy)], fill=(8, 10, 14, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")
        draw = ImageDraw.Draw(img)

        # 5. Top Information Header
        year_box = (w // 2 - 140, 220, w // 2 + 140, 285)
        draw.rounded_rectangle(year_box, radius=12, fill=(18, 24, 34, 230), outline=(242, 193, 78), width=2)
        draw.text((w // 2 - 110, 235), "BOSTON 1919", fill=(242, 193, 78), font=self.badge_font)

        # 6. Minimalist High-Impact Typography ("THE DEADLY WAVE")
        title_bbox = draw.textbbox((0, 0), title_text, font=self.title_font)
        title_w = title_bbox[2] - title_bbox[0]
        tx = (w - title_w) // 2
        ty = 345

        for ox, oy in [(8, 8), (6, 6), (4, 4), (2, 2)]:
            draw.text((tx + ox, ty + oy), title_text, fill=(4, 6, 10), font=self.title_font)

        for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((tx + dx, ty + dy), title_text, fill=(242, 193, 78), font=self.title_font)

        draw.text((tx, ty), title_text, fill=(255, 255, 255), font=self.title_font)

        # 7. Subtitle Metric Pill
        sub_text = "2.3 MILLION GALLONS • 35 MPH"
        sub_bbox = draw.textbbox((0, 0), sub_text, font=self.sub_font)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_x = (w - sub_w) // 2
        sub_y = 485

        pill_box = (sub_x - 24, sub_y - 8, sub_x + sub_w + 24, sub_y + 54)
        draw.rounded_rectangle(pill_box, radius=10, fill=(22, 28, 38), outline=(255, 77, 77), width=2)
        draw.text((sub_x, sub_y), sub_text, fill=(255, 230, 110), font=self.sub_font)

        # 8. Save and validate
        img.save(output_path, "PNG", quality=95)
        logger.info("Saved 9:16 Shorts thumbnail to %s (size: %s)", output_path, img.size)
        return output_path

    def generate_horizontal_thumbnail(
        self,
        output_path: str = os.path.join(THUMBNAIL_DIR, "showcase_stage_3_3_molasses_flood_16x9.png"),
        title_text: str = "THE DEADLY WAVE",
    ) -> str:
        """16:9 profile available for horizontal YouTube videos."""
        gen_16x9 = ThumbnailGenerator(profile=ThumbnailProfile.STANDARD_HORIZONTAL)
        w, h = 1280, 720
        img = Image.new("RGB", (w, h), (12, 16, 24))
        draw = ImageDraw.Draw(img)

        for y in range(h):
            ratio = y / h
            r = int(18 * (1 - ratio) + 55 * ratio)
            g = int(28 * (1 - ratio) + 32 * ratio)
            b = int(42 * (1 - ratio) + 20 * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

        wave_pts = [(0, h), (0, 320), (350, 280), (700, 240), (1000, 290), (w, 340), (w, h)]
        draw.polygon(wave_pts, fill=(35, 16, 7))

        draw.text((70, 140), title_text, fill=(5, 5, 8), font=gen_16x9.title_font)
        draw.text((64, 134), title_text, fill=(255, 255, 255), font=gen_16x9.title_font)

        img.save(output_path, "PNG", quality=95)
        return output_path

    def generate_custom_shorts_thumbnail(
        self,
        headline: str,
        badge: str,
        bg_image_path: Optional[str] = None,
        accent_color: Tuple[int, int, int] = (254, 224, 4),
        output_path: str = os.path.join(THUMBNAIL_DIR, "custom_shorts_thumb.png"),
    ) -> str:
        """Generates a high-CTR vertical 1080x1920 thumbnail for any documentary topic."""
        w, h = 1080, 1920
        if bg_image_path and os.path.exists(bg_image_path):
            src = Image.open(bg_image_path).convert("RGB")
            # Fill canvas with aspect ratio preservation crop
            sw, sh = src.size
            scale = max(w / sw, h / sh)
            nw, nh = int(sw * scale), int(sh * scale)
            resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
            x_off = (nw - w) // 2
            y_off = (nh - h) // 2
            img = resized.crop((x_off, y_off, x_off + w, y_off + h))
        else:
            img = Image.new("RGB", (w, h), (14, 18, 28))

        # Vignette & dramatic dark gradient overlay for text readability
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d_ov = ImageDraw.Draw(overlay)
        # Top gradient for badge
        for y in range(0, 350):
            a = int(190 * (1.0 - y / 350))
            d_ov.line([(0, y), (w, y)], fill=(10, 14, 22, a))
        # Bottom gradient for massive headline
        for y in range(1100, h):
            a = int(230 * ((y - 1100) / (h - 1100)))
            d_ov.line([(0, y), (w, y)], fill=(8, 10, 16, a))

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # 1. Top Category / Channel Badge
        badge_box = (w // 2 - 200, 220, w // 2 + 200, 290)
        draw.rounded_rectangle(badge_box, radius=14, fill=(16, 22, 34, 235), outline=accent_color, width=3)
        draw.text((w // 2 - 160, 240), badge, fill=accent_color, font=self.badge_font)

        # 2. Headline Auto-Fit & Shadow
        # Split headline into 2-3 lines
        words = headline.split()
        if len(words) >= 4:
            mid = len(words) // 2
            lines = [" ".join(words[:mid]), " ".join(words[mid:])]
        else:
            lines = [headline]

        y_cursor = 1350
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=self.title_font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            lx = (w - lw) // 2
            # Drop shadow
            draw.text((lx + 4, y_cursor + 4), line, fill=(0, 0, 0), font=self.title_font)
            draw.text((lx, y_cursor), line, fill=(255, 255, 255), font=self.title_font)
            y_cursor += lh + 20

        # Outer accent border
        draw.rectangle([10, 10, w - 10, h - 10], outline=accent_color, width=4)

        img.save(output_path, "PNG", quality=95)
        logger.info("Saved custom 9:16 Shorts thumbnail to %s", output_path)
        return output_path


# Global instance defaulted to 9:16 vertical Shorts profile
thumbnail_generator = ThumbnailGenerator(profile=ThumbnailProfile.SHORTS_VERTICAL)
