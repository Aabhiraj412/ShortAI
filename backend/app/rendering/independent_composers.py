"""Native-frame composers used by the treatment-driven renderer.

These are intentionally separate image generators.  They share only output size
and safe subtitle territory; neither calls the legacy documentary backdrop.
"""

from __future__ import annotations

import math
import os
import random
from functools import lru_cache
from typing import Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont


@lru_cache(maxsize=12)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/bahnschrift.ttf") if bold else ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf")
    for path in candidates:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _stars(draw: ImageDraw.ImageDraw, width: int, height: int, seed: int, count: int = 320) -> None:
    rng = random.Random(seed)
    for _ in range(count):
        x, y = rng.randrange(width), rng.randrange(height)
        b = rng.randrange(50, 205)
        r = 1 if rng.random() < .9 else 2
        draw.ellipse((x-r, y-r, x+r, y+r), fill=(b, b, min(255, b+20)))


class ScientificFieldComposer:
    """Model-space renderer: radial axes, scale changes and explanatory labels."""

    composer_id = "scientific_field_composer"

    def render(self, time_sec: float, scene_index: int, width: int = 1080, height: int = 1920) -> Image.Image:
        frame = Image.new("RGB", (width, height), (3, 9, 18))
        draw = ImageDraw.Draw(frame, "RGBA")
        _stars(draw, width, height, 41, 180)
        cx, cy = width // 2, height // 2 - 80
        local = time_sec % 6.5
        pulse = (math.sin(time_sec * 2.0) + 1) / 2
        # Model coordinates, not a card: thin calibration lines only help the model.
        if scene_index in (0, 2):
            for radius in range(90, 620, 95):
                draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=(24, 105, 138, 85), width=2)
            for deg in range(0, 360, 30):
                a = math.radians(deg)
                draw.line((cx, cy, cx + 620*math.cos(a), cy + 620*math.sin(a)), fill=(24, 105, 138, 55), width=1)
        if scene_index == 0:
            scale = 0.82 + .18 * min(1, local/4)
            r = int(260 * scale)
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(0, 0, 0, 255))
            for offset in range(0, 6):
                rr = r + 35 + offset*26
                draw.arc((cx-rr, cy-rr*.55, cx+rr, cy+rr*.55), 185, 355, fill=(108, 223, 255, 170-offset*20), width=9)
            heading, note = "GRAVITY BENDS THE PATH", "light follows curved spacetime"
        elif scene_index == 1:
            r = 238
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(0, 0, 0, 255), outline=(113, 241, 255, 255), width=6)
            for n in range(16):
                angle = n * math.pi/8 + time_sec*.25
                x1, y1 = cx+520*math.cos(angle), cy+520*math.sin(angle)
                x2, y2 = cx+(r+8)*math.cos(angle+.68), cy+(r+8)*math.sin(angle+.68)
                draw.line((x1,y1,x2,y2), fill=(224, 248, 255, 190), width=4)
            heading, note = "THE EVENT HORIZON", "a boundary: not a surface"
        elif scene_index == 2:
            r = 205
            for n in range(150):
                a = n*.47 + time_sec*.7
                rr = r + (n % 24)*15
                x, y = cx + rr*math.cos(a), cy + .45*rr*math.sin(a)
                draw.ellipse((x-3,y-3,x+3,y+3), fill=(255, 166+(n%70), 74, 220))
            draw.ellipse((cx-r, cy-r*.45, cx+r, cy+r*.45), fill=(0,0,0,255))
            heading, note = "A HOT ORBITING DISK", "matter heats before crossing"
        else:
            r = int(145 + pulse*24)
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(0,0,0,255))
            for rr in (r+40, r+100, r+180):
                draw.arc((cx-rr,cy-rr,cx+rr,cy+rr), 215, 330, fill=(103,222,255,180), width=5)
            heading, note = "TIME RUNS DIFFERENTLY", "nearby clocks appear slower"
        # Instrument-type labels remain in the model's coordinate field.
        title_font, body_font, mono = _font(57, True), _font(31), _font(27, True)
        draw.text((72, 300), heading, font=title_font, fill=(231,249,255))
        draw.text((74, 370), note.upper(), font=body_font, fill=(133, 208, 225))
        draw.line((74, 440, 1006, 440), fill=(96, 215, 241, 150), width=2)
        draw.text((74, 1450), "MODEL: GENERAL RELATIVITY", font=mono, fill=(118,205,223))
        draw.text((74, 1500), f"FIELD STATE  {scene_index+1:02d}/04     t+{time_sec:04.1f}s", font=mono, fill=(190,223,231))
        draw.line((74, 1595, 820, 1595), fill=(92, 194, 219), width=4)
        draw.ellipse((820,1578,854,1612), fill=(235,252,255))
        return frame


class CinematicDocumentaryComposer:
    """Lens/observatory renderer: image-space shots, grain and editorial cuts."""

    composer_id = "evidence_documentary_composer"

    def render(self, time_sec: float, scene_index: int, width: int = 1080, height: int = 1920) -> Image.Image:
        frame = Image.new("RGB", (width, height), (7, 5, 5))
        draw = ImageDraw.Draw(frame, "RGBA")
        _stars(draw, width, height, 909 + scene_index, 580)
        rng = random.Random(int(time_sec * 30) + scene_index)
        # A cinematic visual surface: no grid, no scientific labels attached to axes.
        if scene_index == 0:
            draw.rectangle((0, 0, width, height), fill=(9, 10, 18, 180))
            horizon = 1100 + int(math.sin(time_sec)*28)
            draw.polygon([(0,horizon),(300,horizon-80),(640,horizon+40),(1080,horizon-50),(1080,1920),(0,1920)], fill=(5,6,8,255))
            # observatory dome silhouette
            draw.ellipse((250,740,850,1340), fill=(2,2,3,255))
            draw.rectangle((250,1040,850,1360), fill=(2,2,3,255))
            draw.arc((375,865,725,1215), 205, 333, fill=(241,174,97,210), width=15)
            heading, kicker = "WE LOOK INTO THE DARK", "OBSERVATORY / NIGHT STUDY"
        elif scene_index == 1:
            # Warm telescope aperture and a moving flare: photographic, not diagrammatic.
            radius = 490
            draw.ellipse((width//2-radius, 610-radius, width//2+radius, 610+radius), fill=(0,0,0,255), outline=(112,78,50,255), width=28)
            flare_x = 530 + int(math.sin(time_sec*.7)*165)
            for rr, alpha in ((130,30),(75,55),(24,250)):
                draw.ellipse((flare_x-rr,610-rr,flare_x+rr,610+rr), fill=(255,180,94,alpha))
            draw.polygon([(0,1440),(1080,1280),(1080,1920),(0,1920)], fill=(7,5,5,250))
            heading, kicker = "NOTHING ESCAPES", "A BOUNDARY IN SPACE"
        elif scene_index == 2:
            # Slow full-frame close observation, with an asymmetrical amber ring.
            cx,cy=560,860
            for rr in range(500,170,-26):
                draw.arc((cx-rr,cy-rr*.54,cx+rr,cy+rr*.54), 195, 350, fill=(255,102+rr//8,65,75), width=16)
            draw.ellipse((360,660,760,1060), fill=(0,0,0,255))
            draw.rectangle((0,0,width,190),fill=(3,3,4,230))
            heading, kicker = "MATTER LIGHTS UP FIRST", "ORBITING GAS / ARTIST'S VISUALIZATION"
        else:
            # Ending pulls to an empty, large star field with one light trail.
            draw.line((110,1060,970,830), fill=(247,184,109,185), width=4)
            draw.ellipse((710,550,735,575), fill=(255,221,156,255))
            draw.rectangle((0,1280,width,1920), fill=(4,4,7,205))
            heading, kicker = "THE BLACK HOLE IS A CLUE", "TO HOW GRAVITY SHAPES THE UNIVERSE"
        # editorial type is a late overlay in image space, uneven per shot.
        f_big, f_small = _font(63, True), _font(27, True)
        y = 142 if scene_index in (0,2) else 1320
        draw.text((72,y), kicker, font=f_small, fill=(239,181,106))
        draw.multiline_text((70,y+50), heading, font=f_big, fill=(247,241,231), spacing=3)
        draw.rectangle((70, height-160, 390, height-154), fill=(236,175,95))
        draw.text((70,height-130), "SIMULATED NIGHT-SKY VISUALIZATION", font=_font(22, True), fill=(204,193,179))
        # Film grain; deterministic enough for a frame and purposely tactile.
        for _ in range(900):
            x,y2 = rng.randrange(width), rng.randrange(height)
            draw.point((x,y2), fill=(255,240,215,rng.randrange(5,28)))
        return frame


def composer_for(direction: str):
    if direction == "scientific_visualization":
        return ScientificFieldComposer()
    if direction == "documentary_investigation":
        return CinematicDocumentaryComposer()
    raise ValueError(f"No native composer implemented for {direction!r}")
