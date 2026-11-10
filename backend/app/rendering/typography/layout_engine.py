"""
Professional Typography & Vertical Safe-Zone Layout Engine.

Enforces strict boundary constraints, automatic line wrapping, font fitting,
contrast validation, and collision avoidance for 1080x1920 YouTube Shorts.
"""

import math
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

# Canonical YouTube Shorts Vertical Dimensions (9:16)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

# Safe Zone Constraints
SAFE_ZONE_TOP = 280                 # Top header & channel bar
SAFE_ZONE_BOTTOM_CONTENT = 1340     # Lower boundary for primary visual content
SUBTITLE_BAND_TOP = 1340            # Start of subtitle display region
SUBTITLE_BAND_BOTTOM = 1620         # End of subtitle display region
PLATFORM_OVERLAY_BOTTOM = 1620      # Title, music marquee, sound attribution
PLATFORM_OVERLAY_RIGHT = 940        # Like, comment, share, remix action stack

# Content Safe Rectangle: (left, top, right, bottom)
CONTENT_SAFE_RECT = (80, SAFE_ZONE_TOP, PLATFORM_OVERLAY_RIGHT, SAFE_ZONE_BOTTOM_CONTENT)
CONTENT_SAFE_WIDTH = CONTENT_SAFE_RECT[2] - CONTENT_SAFE_RECT[0]   # 860 px
CONTENT_SAFE_HEIGHT = CONTENT_SAFE_RECT[3] - CONTENT_SAFE_RECT[1] # 1060 px

# Minimum readable font sizes on mobile screens
MIN_TITLE_FONT_SIZE = 48
MIN_BODY_FONT_SIZE = 24
MIN_LABEL_FONT_SIZE = 20


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """Converts a hex color string (#RRGGBB or #RGB) to (R, G, B) integers."""
    s = hex_str.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """Computes WCAG relative luminance from standard sRGB components."""
    def channel_lum(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel_lum(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def calculate_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Calculates the WCAG 2.1 contrast ratio between foreground and background colors."""
    lum1 = relative_luminance(hex_to_rgb(fg_hex))
    lum2 = relative_luminance(hex_to_rgb(bg_hex))
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


def validate_contrast(fg_hex: str, bg_hex: str, is_large_text: bool = False) -> Tuple[bool, float]:
    """
    Validates that contrast satisfies WCAG AA guidelines:
    - Normal text: >= 4.5:1
    - Large text (>= 36px bold): >= 3.0:1
    """
    ratio = calculate_contrast_ratio(fg_hex, bg_hex)
    threshold = 3.0 if is_large_text else 4.5
    return ratio >= threshold, round(ratio, 2)


def check_safe_zone(
    x: float,
    y: float,
    w: float,
    h: float,
    is_subtitle: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Verifies that a visual or text element stays strictly within permitted screen zones.
    Returns (is_safe, list_of_violations).
    """
    violations = []
    x1, y1 = x, y
    x2, y2 = x + w, y + h

    if x1 < 0 or y1 < 0 or x2 > CANVAS_WIDTH or y2 > CANVAS_HEIGHT:
        violations.append(f"Element [{x1:.0f}, {y1:.0f}, {x2:.0f}, {y2:.0f}] exceeds canvas bounds (1080x1920)")

    if is_subtitle:
        if y1 < SUBTITLE_BAND_TOP or y2 > SUBTITLE_BAND_BOTTOM:
            violations.append(f"Subtitle [{y1:.0f}-{y2:.0f}] extends outside subtitle band ({SUBTITLE_BAND_TOP}-{SUBTITLE_BAND_BOTTOM})")
    else:
        if y1 < SAFE_ZONE_TOP:
            violations.append(f"Element top ({y1:.0f}) encroaches on platform header safe zone (< {SAFE_ZONE_TOP})")
        if y2 > SAFE_ZONE_BOTTOM_CONTENT:
            violations.append(f"Element bottom ({y2:.0f}) encroaches on subtitle band or platform overlay (> {SAFE_ZONE_BOTTOM_CONTENT})")

    if x2 > PLATFORM_OVERLAY_RIGHT:
        violations.append(f"Element right edge ({x2:.0f}) overlaps right-side engagement actions (> {PLATFORM_OVERLAY_RIGHT})")

    return len(violations) == 0, violations


def wrap_and_fit_text(
    text: str,
    max_width: int,
    max_height: int,
    preferred_size: int,
    min_size: int = MIN_BODY_FONT_SIZE,
    line_spacing: float = 1.25,
) -> Dict[str, any]:
    """
    Wraps text and calculates optimal font size so text never overflows its assigned box.
    Uses Pillow ImageDraw measurement fallback.
    """
    words = text.strip().split()
    if not words:
        return {"lines": [], "font_size": preferred_size, "width": 0, "height": 0, "fitted": True}

    temp_img = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(temp_img)

    for font_size in range(preferred_size, min_size - 1, -2):
        try:
            # Try to load Windows standard font Arial for accurate measurement
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Perform greedy word wrap
        lines = []
        current_line = []
        overflow = False

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_w = bbox[2] - bbox[0]
            if line_w <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                    # Check if single word exceeds max_width
                    word_bbox = draw.textbbox((0, 0), word, font=font)
                    if (word_bbox[2] - word_bbox[0]) > max_width:
                        overflow = True
                        break
                else:
                    overflow = True
                    break

        if overflow:
            continue

        if current_line:
            lines.append(" ".join(current_line))

        # Check total height
        single_line_h = font_size * line_spacing
        total_h = len(lines) * single_line_h

        if total_h <= max_height:
            # Compute actual max line width
            max_line_w = max(draw.textbbox((0, 0), l, font=font)[2] - draw.textbbox((0, 0), l, font=font)[0] for l in lines)
            return {
                "text": text,
                "lines": lines,
                "font_size": font_size,
                "width": max_line_w,
                "height": total_h,
                "fitted": True,
            }

    # If even min_size overflows, return best effort at min_size and flag fitted=False
    return {
        "text": text,
        "lines": lines if lines else [text],
        "font_size": min_size,
        "width": max_width,
        "height": max_height,
        "fitted": False,
        "error": f"Text '{text[:30]}...' cannot fit inside {max_width}x{max_height} even at minimum font size {min_size}px",
    }
