"""Small per-beat style treatments shared by still plates and procedural animations."""

import os
from functools import lru_cache
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps


_STYLE_LABELS = {
    "documentary": "DOCUMENTARY",
    "pop_info": "POP INFO",
    "chalk_talk": "CHALK TALK",
    "creator_motion_graphics": "CREATOR MOTION",
    "data_story": "DATA STORY",
    "cinematic_explainer": "CINEMATIC EXPLAINER",
    "map_based_storytelling": "MAP STORY",
}


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "arialbd.ttf",
    ):
        if os.path.isfile(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def draw_visual_style_treatment(
    image: Image.Image,
    visual_style: str,
    *,
    stage_label: str,
    callout_text: str,
    stat_text: str,
    accent_color: Tuple[int, int, int],
    beat_idx: int,
    total_beats: int,
    progress: float = 0.0,
) -> None:
    """Adds restrained, recognizable style cues without replacing or obscuring source imagery."""
    style = (visual_style or "documentary").lower()
    if style not in _STYLE_LABELS:
        raise ValueError(f"Unsupported adaptive visual style: {visual_style}")

    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(max(18, width // 38))
    small_font = _font(max(14, width // 55))
    label = _STYLE_LABELS[style]
    pad = max(18, width // 12)

    if style == "documentary":
        label_y = height // 5
        draw.rounded_rectangle((pad, label_y, pad + width // 3, label_y + 34), radius=12, fill=(9, 14, 22, 210))
        draw.text((pad + 12, label_y + 17), f"{label}  /  {stage_label[:18].upper()}", font=small_font, fill=(236, 241, 248, 255), anchor="lm")
        return

    if style == "pop_info":
        coral = (232, 93, 74, 245)
        yellow = (255, 216, 107, 245)
        tag_y = height // 5
        draw.rounded_rectangle((pad, tag_y, pad + width // 3, tag_y + 42), radius=18, fill=coral)
        draw.text((pad + 16, tag_y + 21), label, font=small_font, fill=(255, 255, 255, 255), anchor="lm")
        stat = (stat_text or "QUICK FACT").strip()[:26].upper()
        draw.rounded_rectangle((width - pad - width // 3, tag_y, width - pad, tag_y + 42), radius=18, fill=yellow)
        draw.text((width - pad - width // 3 + 14, tag_y + 21), stat, font=small_font, fill=(38, 63, 64, 255), anchor="lm")
        draw.rounded_rectangle((pad - 5, height - height // 4 - 10, width - pad + 5, height - height // 4 + 8), radius=9, fill=coral)
        return

    if style == "chalk_talk":
        board = (29, 54, 45, 205)
        chalk = (245, 242, 228, 230)
        box = (pad - 12, height // 5 - 12, width - pad + 12, height - height // 4 + 16)
        draw.rounded_rectangle(box, radius=8, outline=chalk, width=max(2, width // 500), fill=(board[0], board[1], board[2], 75))
        draw.text((pad + 8, height // 5 + 8), label, font=small_font, fill=(255, 222, 125, 255), anchor="lm")
        line_y = height - height // 4 - 16
        dash = max(8, width // 80)
        for x in range(pad, width - pad, dash * 2):
            draw.line((x, line_y, min(width - pad, x + dash), line_y), fill=chalk, width=2)
        draw.line((width - pad - 46, line_y - 18, width - pad - 6, line_y), fill=chalk, width=3)
        draw.line((width - pad - 46, line_y + 18, width - pad - 6, line_y), fill=chalk, width=3)
        return

    if style == "data_story":
        grid = (82, 112, 135, 170)
        for y in range(height // 3, height - height // 4, max(24, height // 18)):
            draw.line((pad, y, width - pad, y), fill=grid, width=1)
        draw.rectangle((pad, height // 5 - 8, pad + width // 6, height // 5 + 38), fill=(32, 201, 151, 235))
        draw.text((pad + 10, height // 5 + 8), label, font=small_font, fill=(8, 20, 27, 255), anchor="lm")
        return

    if style == "cinematic_explainer":
        draw.rectangle((0, 0, width, max(12, height // 14)), fill=(5, 7, 10, 255))
        draw.rectangle((0, height - max(12, height // 14), width, height), fill=(5, 7, 10, 255))
        draw.text((pad, height - height // 5), label, font=small_font, fill=(233, 210, 170, 255))
        return

    if style == "map_based_storytelling":
        route = (84, 203, 183, 235)
        y = height // 2
        points = [(pad, y + height // 12), (width // 2, y - height // 15), (width - pad, y + height // 20)]
        draw.line(points, fill=route, width=max(3, width // 180), joint="curve")
        for x, py in points:
            r = max(8, width // 60)
            draw.ellipse((x-r, py-r, x+r, py+r), fill=route, outline=(245, 245, 230, 240), width=2)
        draw.text((pad, height // 5), label, font=small_font, fill=(230, 240, 232, 255))
        return

    yellow = (255, 214, 10, 235)
    coral = (255, 77, 77, 220)
    top = height // 5
    drift = int((progress % 1.0) * (width // 8))
    draw.rectangle((pad, top, width - pad, top + 8), fill=yellow)
    draw.rectangle((pad + drift, top - 8, min(width - pad, pad + drift + width // 7), top + 2), fill=coral)
    draw.text((pad, top + 28), label, font=small_font, fill=(245, 242, 234, 255))
    mark_y = height - height // 4 - 18
    draw.line((pad, mark_y, width - pad, mark_y), fill=yellow, width=max(3, width // 240))
    draw.line((pad + 8, mark_y + 10, width - pad - 18, mark_y + 10), fill=coral, width=max(2, width // 360))


def compose_direction_frame(
    frame: Image.Image,
    visual_style: str,
    *,
    topic_title: str,
    segment_text: str,
    stage_label: str,
    stat_text: str,
    beat_idx: int,
) -> Image.Image:
    """Recompose animation frames through direction-specific layout adapters."""
    style = (visual_style or "documentary").lower()
    if style not in _STYLE_LABELS:
        raise ValueError(f"Unsupported adaptive visual style: {visual_style}")
    w, h = frame.size
    # Preserve the animated visual itself and rebuild the surrounding editorial geometry.
    source = frame.convert("RGB").crop((max(0, w//10), h//5, w-max(0,w//10), h*13//20))
    source = ImageOps.fit(source, (w, h), method=Image.Resampling.BILINEAR)
    palettes = {
        "documentary": ((8, 12, 16), (245, 242, 232), (222, 181, 105)),
        "pop_info": ((248, 241, 220), (26, 43, 43), (232, 93, 74)),
        "data_story": ((12, 20, 31), (236, 241, 245), (32, 201, 151)),
        "chalk_talk": ((31, 57, 47), (244, 240, 222), (244, 211, 94)),
        "creator_motion_graphics": ((11, 11, 14), (245, 242, 234), (255, 214, 10)),
        "cinematic_explainer": ((7, 10, 16), (246, 241, 229), (197, 163, 111)),
        "map_based_storytelling": ((18, 29, 33), (238, 239, 224), (84, 203, 183)),
    }
    bg, fg, accent = palettes[style]
    canvas = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(canvas)
    small = _font(max(17, w//40))
    label = (topic_title or "CRAZZYDOCS")[:34].upper()
    variant = beat_idx % 3

    if style in {"documentary", "cinematic_explainer"}:
        canvas = source.copy()
        draw = ImageDraw.Draw(canvas, "RGBA")
        if style == "documentary":
            draw.rectangle((0, h-500, w, h-340), fill=(5, 8, 12, 205))
            draw.text((60,h-445), f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label}", font=small, fill=accent+(255,))
        else:
            draw.rectangle((0,0,w,170), fill=(0,0,0,245))
            draw.rectangle((0,h-170,w,h), fill=(0,0,0,245))
            draw.text((60, 74), f"SCENE {beat_idx + 1:02d}  •  {stage_label.upper()}", font=small, fill=accent+(255,))
    elif style == "pop_info":
        card = (42, 220, w-42, 1050) if variant != 1 else (190, 220, w-35, 1030)
        canvas.paste(source.resize((card[2]-card[0], card[3]-card[1])), card[:2])
        draw.rounded_rectangle(card, 34, outline=accent, width=8)
        draw.rounded_rectangle((55, 1120, w-55, 1460), 42, fill=(255,255,250), outline=fg, width=4)
        draw.text((92, 1190), (stat_text or "QUICK FACT").upper()[:24], font=_font(max(38,w//18)), fill=accent)
        draw.text((92, 1325), f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label[:16]}", font=small, fill=fg)
        draw.text((62, 94), f"POP INFO  /  {stage_label.upper()}  •  #{beat_idx + 1:02d}", font=small, fill=fg)
    elif style == "data_story":
        draw.text((66, 110), f"DATA STORY  /  {stage_label.upper()}  •  SCENE {beat_idx + 1:02d}", font=small, fill=accent)
        panel=(74,220,w-74,1030)
        canvas.paste(source.resize((panel[2]-panel[0],panel[3]-panel[1])),panel[:2])
        draw.rectangle(panel, outline=accent, width=4)
        for y in range(360,1200,140): draw.line((100,y,w-100,y),fill=(75,96,110),width=2)
        draw.rounded_rectangle((72,1110,w-72,1460),22,fill=(22,32,44),outline=(55,78,91),width=3)
        draw.text((110,1180),(stat_text or "MEASURED RESULT").upper()[:24],font=_font(max(42,w//18)),fill=accent)
        draw.text((110,1325),f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label[:16]}",font=small,fill=fg)
    elif style == "chalk_talk":
        canvas.paste(source.resize((w-150,900)),(75,260))
        draw.rectangle((75,260,w-75,1160),outline=fg,width=4)
        draw.text((78,90),f"CHALK TALK  /  {stage_label.upper()}  •  SCENE {beat_idx + 1:02d}",font=small,fill=accent)
        draw.line((140,1285,w-170,1285),fill=accent,width=5)
        draw.polygon([(w-190,1267),(w-155,1285),(w-190,1303)],fill=accent)
        draw.text((85,1360),f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label[:16]}",font=small,fill=fg)
    elif style == "creator_motion_graphics":
        x=80 if variant != 1 else 170
        canvas.paste(source.resize((w-150,850)),(x,240))
        draw.rectangle((0,1130,w,1300),fill=accent)
        draw.text((55,1180),(stat_text or stage_label).upper()[:26],font=_font(max(32,w//22)),fill=(12,12,14))
        draw.text((65,1390),f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label[:16]}",font=small,fill=fg)
        draw.polygon([(0,160),(w,0),(w,200),(0,350)],fill=(35,35,41))
    else:
        canvas.paste(source,(0,0))
        draw.rectangle((0,0,w,220),fill=(18,29,33))
        draw.text((70,85),f"MAP STORY  /  {stage_label.upper()}  •  SCENE {beat_idx + 1:02d}",font=small,fill=fg)
        offset = ((beat_idx % 5) - 2) * 28
        pts=[(110,h*3//5 + offset),(w//3,h//2 - offset),(w*2//3,h*3//5 + offset//2),(w-100,h//2 - offset//2)]
        draw.line(pts,fill=accent,width=8)
        for x,y in pts: draw.ellipse((x-18,y-18,x+18,y+18),fill=accent,outline=fg,width=4)
        draw.rectangle((0,h-570,w,h-330),fill=(18,29,33))
        draw.text((64,h-500),f"{stage_label.upper()}  •  SCENE {beat_idx + 1:02d}  /  {label[:16]}",font=small,fill=fg)
    return canvas
