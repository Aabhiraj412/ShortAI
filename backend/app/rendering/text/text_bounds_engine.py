"""
Text Boundary and Safe-Zone Layout Engine for CrazzyDocs Shorts Studio.

Enforces strict visual bounds for vertical 1080x1920 video frames:
1. Dynamic font auto-scaling: scales down until text fits box with padding.
2. Intelligent word wrapping based on actual measured glyph bounding boxes.
3. Strict safe-zone enforcement (Shorts interface margins: top 180px, bottom 260px, sides 60px).
4. Text boundary validation: verifies zero text clipping or canvas edge violations.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Standard 1080x1920 YouTube Shorts Safe Zone Boundaries
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

SAFE_MARGIN_LEFT = 60
SAFE_MARGIN_RIGHT = 1020  # 1080 - 60
SAFE_MARGIN_TOP = 180     # Clearance for channel info & sound pills
SAFE_MARGIN_BOTTOM = 1660 # Clearance for bottom captions, like/share buttons, title


@dataclass
class BoundingBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class TextRenderResult:
    text: str
    box: BoundingBox
    font_size: int
    lines: List[str]
    actual_width: int
    actual_height: int
    fits_safely: bool


@dataclass
class TextValidationReport:
    is_valid: bool
    violations: List[str] = field(default_factory=list)
    rendered_elements: List[TextRenderResult] = field(default_factory=list)


class TextBoundsEngine:
    """Calculates, auto-scales, and validates text layout within safe boundaries."""

    def __init__(
        self,
        canvas_width: int = CANVAS_WIDTH,
        canvas_height: int = CANVAS_HEIGHT,
        margin_left: int = SAFE_MARGIN_LEFT,
        margin_right: int = SAFE_MARGIN_RIGHT,
        margin_top: int = SAFE_MARGIN_TOP,
        margin_bottom: int = SAFE_MARGIN_BOTTOM,
    ):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.margin_left = margin_left
        self.margin_right = margin_right
        self.margin_top = margin_top
        self.margin_bottom = margin_bottom

    def get_safe_canvas_box(self) -> BoundingBox:
        return BoundingBox(self.margin_left, self.margin_top, self.margin_right, self.margin_bottom)

    def load_font(self, font_name: str, size: int) -> ImageFont.ImageFont:
        """Loads TTF font with fallback to standard system fonts."""
        candidates = [font_name, "arialbd.ttf", "arial.ttf", "segoeui.ttf", "consola.ttf"]
        for cand in candidates:
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def wrap_text_to_width(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        draw: Optional[ImageDraw.ImageDraw] = None,
    ) -> List[str]:
        """Wraps text into lines using actual measured pixel bounding boxes."""
        if not text:
            return []

        words = text.split()
        lines: List[str] = []
        current_line: List[str] = []

        for word in words:
            test_line = " ".join(current_line + [word])
            # Measure actual pixel width
            if draw:
                bbox = draw.textbbox((0, 0), test_line, font=font)
                line_w = bbox[2] - bbox[0]
            else:
                dummy_img = Image.new("RGB", (1, 1))
                d = ImageDraw.Draw(dummy_img)
                bbox = d.textbbox((0, 0), test_line, font=font)
                line_w = bbox[2] - bbox[0]

            if line_w <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    # Single word exceeds width; force include
                    lines.append(word)
                    current_line = []

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    def auto_scale_and_fit(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: BoundingBox,
        max_font_size: int = 48,
        min_font_size: int = 18,
        padding: int = 12,
        font_name: str = "arialbd.ttf",
        line_spacing_mult: float = 1.25,
    ) -> TextRenderResult:
        """
        Dynamically finds the optimal font size and word wrapping that fits
        entirely inside the bounding box with padding.
        """
        avail_w = box.width - 2 * padding
        avail_h = box.height - 2 * padding

        best_lines: List[str] = [text]
        best_size = min_font_size
        best_w = 0
        best_h = 0
        fits = False

        # Step down font size from max to min
        for font_size in range(max_font_size, min_font_size - 1, -2):
            font = self.load_font(font_name, font_size)
            lines = self.wrap_text_to_width(text, font, avail_w, draw=draw)

            # Measure total height and maximum line width
            total_h = 0
            max_line_w = 0
            for idx, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font)
                lw = bbox[2] - bbox[0]
                lh = bbox[3] - bbox[1]
                max_line_w = max(max_line_w, lw)
                total_h += int(lh * line_spacing_mult)

            if max_line_w <= avail_w and total_h <= avail_h:
                best_lines = lines
                best_size = font_size
                best_w = max_line_w
                best_h = total_h
                fits = True
                break

        if not fits:
            # Fallback to minimum font size
            font = self.load_font(font_name, min_font_size)
            best_lines = self.wrap_text_to_width(text, font, avail_w, draw=draw)
            best_size = min_font_size
            total_h = 0
            max_line_w = 0
            for line in best_lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                max_line_w = max(max_line_w, bbox[2] - bbox[0])
                total_h += int((bbox[3] - bbox[1]) * line_spacing_mult)
            best_w = max_line_w
            best_h = total_h
            fits = (best_w <= box.width and best_h <= box.height)

        return TextRenderResult(
            text=text,
            box=box,
            font_size=best_size,
            lines=best_lines,
            actual_width=best_w,
            actual_height=best_h,
            fits_safely=fits,
        )

    def draw_bounded_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: BoundingBox,
        font_name: str = "arialbd.ttf",
        max_font_size: int = 44,
        min_font_size: int = 20,
        fill_color: Tuple[int, int, int] = (255, 255, 255),
        padding: int = 12,
        align: str = "center",  # "left", "center", "right"
        line_spacing_mult: float = 1.25,
    ) -> TextRenderResult:
        """Renders text strictly inside box using optimal font scaling and wrapping."""
        res = self.auto_scale_and_fit(
            draw=draw,
            text=text,
            box=box,
            max_font_size=max_font_size,
            min_font_size=min_font_size,
            padding=padding,
            font_name=font_name,
            line_spacing_mult=line_spacing_mult,
        )

        font = self.load_font(font_name, res.font_size)

        # Compute vertical starting coordinate (centered in box)
        y_cursor = box.y0 + padding + max(0, (box.height - 2 * padding - res.actual_height) // 2)

        for line in res.lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]

            if align == "center":
                x_pos = box.x0 + padding + max(0, (box.width - 2 * padding - lw) // 2)
            elif align == "right":
                x_pos = box.x1 - padding - lw
            else:
                x_pos = box.x0 + padding

            draw.text((x_pos, y_cursor), line, fill=fill_color, font=font)
            y_cursor += int(lh * line_spacing_mult)

        return res

    def validate_rendered_elements(self, elements: List[TextRenderResult]) -> TextValidationReport:
        """Validates that all text elements respect container and canvas boundaries."""
        violations = []
        for el in elements:
            # 1. Internal Box Overflow Check
            if not el.fits_safely:
                violations.append(
                    f"Text '{el.text[:25]}...' ({el.actual_width}x{el.actual_height}px) overflows container "
                    f"box ({el.box.width}x{el.box.height}px)."
                )

            # 2. Canvas Boundary Check
            if el.box.x0 < 0 or el.box.y0 < 0 or el.box.x1 > self.canvas_width or el.box.y1 > self.canvas_height:
                violations.append(
                    f"Container box for '{el.text[:25]}...' ({el.box.x0},{el.box.y0},{el.box.x1},{el.box.y1}) "
                    f"extends beyond canvas ({self.canvas_width}x{self.canvas_height})."
                )

            # 3. Shorts Safe Margin Check (Warning)
            if el.box.x0 < self.margin_left or el.box.x1 > self.margin_right:
                violations.append(
                    f"Text box for '{el.text[:25]}...' infringes horizontal safe margin "
                    f"([{self.margin_left}, {self.margin_right}])."
                )

        return TextValidationReport(is_valid=(len(violations) == 0), violations=violations, rendered_elements=elements)


# Global singleton
text_bounds_engine = TextBoundsEngine()
