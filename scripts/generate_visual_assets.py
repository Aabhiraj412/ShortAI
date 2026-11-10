"""
Generates high-quality style-specific visual assets for CrazzyDocs Shorts Studio.
Supports: Pop Info, Data Story, Chalk Talk, Creator Motion Graphics.
Includes character concept sheets, expression/pose matrices, 1080x1920 backgrounds,
and storytelling props.
"""

import math
import os
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

random.seed(42)

WORKSPACE = r"D:\Agents\CrazzyDocs"
ASSETS_DIR = os.path.join(WORKSPACE, "assets")
PREVIEWS_DIR = os.path.join(WORKSPACE, "docs", "previews")

os.makedirs(PREVIEWS_DIR, exist_ok=True)

def get_font(size: int, bold: bool = False):
    """Attempts to find a clean system TTF font, falls back to default."""
    candidates = [
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\calibri.ttf"
    ]
    if bold:
        candidates = [
            "C:\\Windows\\Fonts\\segoeuib.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\calibrib.ttf"
        ] + candidates
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()

# ==============================================================================
# 1. CHARACTER CONCEPT SHEET
# ==============================================================================

def generate_character_concept_sheet():
    """Generates 3 distinct character design directions for CrazzyDocs."""
    print("Generating Character Concept Sheet...")
    w, h = 1920, 1080
    sheet = Image.new("RGBA", (w, h), (18, 22, 32, 255))
    draw = ImageDraw.Draw(sheet)

    title_font = get_font(46, bold=True)
    sub_font = get_font(24)
    card_title_font = get_font(32, bold=True)
    body_font = get_font(20)
    badge_font = get_font(18, bold=True)

    # Title header
    draw.text((60, 40), "CRAZZYDOCS — CHARACTER CONCEPT DIRECTIONS", fill=(255, 255, 255), font=title_font)
    draw.text((60, 100), "Comparative evaluation of 3 design directions for the Pop Info host personality.", fill=(160, 175, 200), font=sub_font)

    concepts = [
        {
            "id": "doc",
            "name": "DIRECTION 1: 'Dr. Doc'",
            "archetype": "The Quirky Researcher & Inquisitive Scientist",
            "palette": ["#F8D7B8", "#2F6F73", "#E85D4A", "#2A221E", "#FFE004"],
            "pros": "Direct alignment with CrazzyDocs name. Warm, energetic, round horn-rimmed specs, expressive hair tuft.",
            "verdict": "SELECTED FOR POP INFO",
            "selected": True,
            "card_x": 60,
        },
        {
            "id": "nova",
            "name": "DIRECTION 2: 'Dr. Nova'",
            "archetype": "The Modern Tech Documentarian",
            "palette": ["#F8D7B8", "#ECA82C", "#1D262B", "#4A6FA5", "#FFFFFF"],
            "pros": "Contemporary creator aesthetic, mustard hoodie, geometric styling, highly relatable for Gen-Z.",
            "verdict": "ALTERNATIVE / MODERN RUNNER-UP",
            "selected": False,
            "card_x": 680,
        },
        {
            "id": "barnaby",
            "name": "DIRECTION 3: 'Prof. Barnaby'",
            "archetype": "The Whimsical Classic Professor",
            "palette": ["#E8B896", "#5C4033", "#C0392B", "#F5EFEB", "#7F8C8D"],
            "pros": "Exaggerated tweed vest and bow-tie. High caricature, but less relatable for fast-paced 50s Shorts.",
            "verdict": "NOT SELECTED (Too Dated)",
            "selected": False,
            "card_x": 1300,
        }
    ]

    card_w, card_h = 560, 880
    card_y = 150

    for c in concepts:
        cx = c["card_x"]
        # Card background
        bg_col = (28, 35, 52, 255) if not c["selected"] else (34, 48, 70, 255)
        border_col = (50, 65, 95, 255) if not c["selected"] else (232, 93, 74, 255)
        draw.rounded_rectangle([cx, card_y, cx + card_w, card_y + card_h], radius=16, fill=bg_col, outline=border_col, width=3 if c["selected"] else 1)

        # Badge
        badge_bg = (46, 111, 115) if c["selected"] else (70, 80, 100)
        draw.rounded_rectangle([cx + 24, card_y + 24, cx + card_w - 24, card_y + 64], radius=8, fill=badge_bg)
        draw.text((cx + 36, card_y + 32), c["verdict"], fill=(255, 255, 255), font=badge_font)

        # Name & Archetype
        draw.text((cx + 24, card_y + 80), c["name"], fill=(255, 255, 255), font=card_title_font)
        draw.text((cx + 24, card_y + 120), c["archetype"], fill=(180, 200, 225), font=body_font)

        # Character portrait area (360x360)
        port_x = cx + (card_w - 360) // 2
        port_y = card_y + 160
        draw.rounded_rectangle([port_x, port_y, port_x + 360, port_y + 380], radius=12, fill=(20, 25, 38, 255), outline=(45, 55, 80), width=2)

        # Draw concept portrait illustration
        if c["id"] == "doc":
            # Dr. Doc: Rounded head, teal jacket, coral accent, round glasses, messy smart hair
            # Head shadow
            draw.ellipse([port_x + 110, port_y + 90, port_x + 250, port_y + 240], fill=(248, 215, 184))
            # Hair (espresso brown with messy tuft)
            draw.polygon([(port_x + 95, port_y + 130), (port_x + 120, port_y + 60), (port_x + 180, port_y + 40), (port_x + 210, port_y + 25), (port_x + 250, port_y + 60), (port_x + 265, port_y + 130), (port_x + 230, port_y + 90), (port_x + 180, port_y + 85), (port_x + 130, port_y + 95)], fill=(42, 34, 30))
            # Round Horn-rimmed Glasses (Teal / tortoiseshell)
            draw.ellipse([port_x + 125, port_y + 135, port_x + 175, port_y + 185], outline=(47, 111, 115), width=5)
            draw.ellipse([port_x + 185, port_y + 135, port_x + 235, port_y + 185], outline=(47, 111, 115), width=5)
            draw.line([(port_x + 175, port_y + 160), (port_x + 185, port_y + 160)], fill=(47, 111, 115), width=4)
            # Eyes behind glasses
            draw.ellipse([port_x + 145, port_y + 152, port_x + 158, port_y + 168], fill=(29, 38, 43))
            draw.ellipse([port_x + 205, port_y + 152, port_x + 218, port_y + 168], fill=(29, 38, 43))
            # Friendly smile
            draw.arc([port_x + 160, port_y + 190, port_x + 200, port_y + 215], start=10, end=170, fill=(29, 38, 43), width=4)
            # Torso: Teal jacket + Coral collar
            draw.polygon([(port_x + 80, port_y + 380), (port_x + 120, port_y + 250), (port_x + 240, port_y + 250), (port_x + 280, port_y + 380)], fill=(47, 111, 115))
            draw.polygon([(port_x + 150, port_y + 250), (port_x + 180, port_y + 320), (port_x + 210, port_y + 250)], fill=(232, 93, 74))

        elif c["id"] == "nova":
            # Dr. Nova: Sleek modern hoodie, mustard sweater, sleek specs
            draw.ellipse([port_x + 115, port_y + 100, port_x + 245, port_y + 240], fill=(248, 215, 184))
            # Modern side-part hair
            draw.polygon([(port_x + 105, port_y + 120), (port_x + 130, port_y + 60), (port_x + 240, port_y + 60), (port_x + 255, port_y + 130)], fill=(29, 38, 43))
            # Sleek rectangular specs
            draw.rounded_rectangle([port_x + 125, port_y + 140, port_x + 175, port_y + 175], radius=4, outline=(29, 38, 43), width=4)
            draw.rounded_rectangle([port_x + 185, port_y + 140, port_x + 235, port_y + 175], radius=4, outline=(29, 38, 43), width=4)
            draw.line([(port_x + 175, port_y + 155), (port_x + 185, port_y + 155)], fill=(29, 38, 43), width=3)
            # Mustard hoodie
            draw.polygon([(port_x + 70, port_y + 380), (port_x + 115, port_y + 250), (port_x + 245, port_y + 250), (port_x + 290, port_y + 380)], fill=(236, 168, 44))

        elif c["id"] == "barnaby":
            # Prof. Barnaby: Tweed vest, bowtie, white side hair
            draw.ellipse([port_x + 115, port_y + 110, port_x + 245, port_y + 240], fill=(232, 184, 150))
            # Fluffy white side hair
            draw.ellipse([port_x + 90, port_y + 110, port_x + 130, port_y + 170], fill=(230, 230, 235))
            draw.ellipse([port_x + 230, port_y + 110, port_x + 270, port_y + 170], fill=(230, 230, 235))
            # Bowtie
            draw.polygon([(port_x + 160, port_y + 255), (port_x + 175, port_y + 265), (port_x + 160, port_y + 275)], fill=(192, 57, 43))
            draw.polygon([(port_x + 200, port_y + 255), (port_x + 185, port_y + 265), (port_x + 200, port_y + 275)], fill=(192, 57, 43))
            # Tweed vest
            draw.polygon([(port_x + 80, port_y + 380), (port_x + 120, port_y + 260), (port_x + 240, port_y + 260), (port_x + 280, port_y + 380)], fill=(92, 64, 51))

        # Color Palette Swatches
        swatch_y = port_y + 400
        draw.text((cx + 24, swatch_y), "Color Palette:", fill=(150, 165, 185), font=badge_font)
        for i, col in enumerate(c["palette"]):
            sx = cx + 24 + i * 48
            draw.rounded_rectangle([sx, swatch_y + 30, sx + 38, swatch_y + 68], radius=6, fill=col)

        # Rationale / Description
        draw.text((cx + 24, swatch_y + 85), "Analysis & Fit:", fill=(255, 255, 255), font=badge_font)
        # Word wrap pros
        words = c["pros"].split()
        cur_line = []
        ly = swatch_y + 115
        for w_word in words:
            cur_line.append(w_word)
            test_line = " ".join(cur_line)
            if len(test_line) > 38:
                draw.text((cx + 24, ly), " ".join(cur_line[:-1]), fill=(180, 195, 215), font=body_font)
                ly += 26
                cur_line = [w_word]
        if cur_line:
            draw.text((cx + 24, ly), " ".join(cur_line), fill=(180, 195, 215), font=body_font)

    sheet.save(os.path.join(PREVIEWS_DIR, "character_concept_sheet.png"), "PNG")
    print("  -> Saved docs/previews/character_concept_sheet.png")

# ==============================================================================
# 2. POP HOST ("Dr. Doc") EXPRESSION & POSE SYSTEM
# ==============================================================================

def draw_dr_doc_head(draw, center_x, center_y, scale=1.0, expr="neutral"):
    """Renders the modular Dr. Doc head with specified facial expression."""
    hx, hy = center_x, center_y
    r = int(120 * scale)

    # Base Head
    draw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=(248, 215, 184))
    # Ear left & right
    ear_r = int(24 * scale)
    draw.ellipse([hx - r - ear_r//2, hy - ear_r, hx - r + ear_r//2, hy + ear_r], fill=(240, 190, 160))
    draw.ellipse([hx + r - ear_r//2, hy - ear_r, hx + r + ear_r//2, hy + ear_r], fill=(240, 190, 160))

    # Hair (Dark espresso with signature tuft)
    hair_color = (42, 34, 30)
    tuft_pts = [
        (hx - int(110*scale), hy - int(40*scale)),
        (hx - int(90*scale), hy - int(120*scale)),
        (hx - int(30*scale), hy - int(145*scale)),
        (hx + int(10*scale), hy - int(165*scale)), # signature crest tuft
        (hx + int(60*scale), hy - int(135*scale)),
        (hx + int(110*scale), hy - int(70*scale)),
        (hx + int(115*scale), hy - int(20*scale)),
        (hx + int(80*scale), hy - int(65*scale)),
        (hx, hy - int(75*scale)),
        (hx - int(70*scale), hy - int(65*scale))
    ]
    draw.polygon(tuft_pts, fill=hair_color)

    # Horn-rimmed teal spectacles
    spec_col = (47, 111, 115)
    lens_r = int(40 * scale)
    bridge_w = int(16 * scale)
    left_cx = hx - int(50 * scale)
    right_cx = hx + int(50 * scale)
    eye_y = hy + int(5 * scale)

    draw.ellipse([left_cx - lens_r, eye_y - lens_r, left_cx + lens_r, eye_y + lens_r], outline=spec_col, width=int(6*scale))
    draw.ellipse([right_cx - lens_r, eye_y - lens_r, right_cx + lens_r, eye_y + lens_r], outline=spec_col, width=int(6*scale))
    draw.line([(left_cx + lens_r, eye_y), (right_cx - lens_r, eye_y)], fill=spec_col, width=int(5*scale))
    # Temples going to ears
    draw.line([(left_cx - lens_r, eye_y), (hx - r + 5, eye_y - int(8*scale))], fill=spec_col, width=int(4*scale))
    draw.line([(right_cx + lens_r, eye_y), (hx + r - 5, eye_y - int(8*scale))], fill=spec_col, width=int(4*scale))

    # Eyes & Eyebrows based on expression
    pupil_col = (29, 38, 43)
    brow_col = (42, 34, 30)
    brow_w = int(5 * scale)

    if expr == "neutral":
        # Calm resting pupils
        draw.ellipse([left_cx - 9, eye_y - 12, left_cx + 9, eye_y + 12], fill=pupil_col)
        draw.ellipse([right_cx - 9, eye_y - 12, right_cx + 9, eye_y + 12], fill=pupil_col)
        # Highlights
        draw.ellipse([left_cx + 2, eye_y - 8, left_cx + 6, eye_y - 4], fill=(255, 255, 255))
        draw.ellipse([right_cx + 2, eye_y - 8, right_cx + 6, eye_y - 4], fill=(255, 255, 255))
        # Eyebrows
        draw.arc([left_cx - 30, eye_y - 65, left_cx + 30, eye_y - 35], start=200, end=340, fill=brow_col, width=brow_w)
        draw.arc([right_cx - 30, eye_y - 65, right_cx + 30, eye_y - 35], start=200, end=340, fill=brow_col, width=brow_w)
        # Gentle smile
        draw.arc([hx - 30, hy + 40, hx + 30, hy + 75], start=10, end=170, fill=pupil_col, width=int(4*scale))

    elif expr == "curious":
        # One brow sharply raised, eyes looking up-left
        draw.ellipse([left_cx - 15, eye_y - 18, left_cx + 3, eye_y + 6], fill=pupil_col)
        draw.ellipse([right_cx - 15, eye_y - 18, right_cx + 3, eye_y + 6], fill=pupil_col)
        # Left brow arch high
        draw.arc([left_cx - 30, eye_y - 85, left_cx + 30, eye_y - 50], start=190, end=350, fill=brow_col, width=brow_w + 1)
        # Right brow flat
        draw.line([(right_cx - 25, eye_y - 45), (right_cx + 25, eye_y - 48)], fill=brow_col, width=brow_w)
        # Quirky 'o' mouth
        draw.ellipse([hx - 12, hy + 50, hx + 12, hy + 72], fill=(180, 50, 40))

    elif expr == "shocked":
        # Wide round open eyes, high arched brows, open dropped jaw
        draw.ellipse([left_cx - 14, eye_y - 14, left_cx + 14, eye_y + 14], fill=pupil_col)
        draw.ellipse([right_cx - 14, eye_y - 14, right_cx + 14, eye_y + 14], fill=pupil_col)
        # Arched high brows
        draw.arc([left_cx - 30, eye_y - 85, left_cx + 30, eye_y - 55], start=190, end=350, fill=brow_col, width=brow_w)
        draw.arc([right_cx - 30, eye_y - 85, right_cx + 30, eye_y - 55], start=190, end=350, fill=brow_col, width=brow_w)
        # Dropped open mouth
        draw.rounded_rectangle([hx - 18, hy + 45, hx + 18, hy + 85], radius=14, fill=(150, 30, 30), outline=pupil_col, width=2)

    elif expr == "explaining":
        # Direct eye contact, warm talking mouth
        draw.ellipse([left_cx - 10, eye_y - 10, left_cx + 10, eye_y + 10], fill=pupil_col)
        draw.ellipse([right_cx - 10, eye_y - 10, right_cx + 10, eye_y + 10], fill=pupil_col)
        # Confident brows
        draw.line([(left_cx - 28, eye_y - 48), (left_cx + 25, eye_y - 44)], fill=brow_col, width=brow_w)
        draw.line([(right_cx - 25, eye_y - 44), (right_cx + 28, eye_y - 48)], fill=brow_col, width=brow_w)
        # Talking open mouth
        draw.chord([hx - 22, hy + 45, hx + 22, hy + 78], start=0, end=180, fill=(180, 50, 40), outline=pupil_col, width=2)

    elif expr == "smiling":
        # Curved happy eye crescents
        draw.arc([left_cx - 20, eye_y - 15, left_cx + 20, eye_y + 15], start=190, end=350, fill=pupil_col, width=int(5*scale))
        draw.arc([right_cx - 20, eye_y - 15, right_cx + 20, eye_y + 15], start=190, end=350, fill=pupil_col, width=int(5*scale))
        # Happy brows
        draw.arc([left_cx - 25, eye_y - 65, left_cx + 25, eye_y - 40], start=200, end=340, fill=brow_col, width=brow_w)
        draw.arc([right_cx - 25, eye_y - 65, right_cx + 25, eye_y - 40], start=200, end=340, fill=brow_col, width=brow_w)
        # Big warm toothy smile
        draw.chord([hx - 34, hy + 42, hx + 34, hy + 82], start=0, end=180, fill=(255, 255, 255), outline=pupil_col, width=3)


def generate_pop_host_system():
    """Generates the full Dr. Doc asset library: expressions, poses, and preview sheets."""
    print("Generating Dr. Doc Pop Host System (Expressions & Poses)...")

    expr_dir = os.path.join(ASSETS_DIR, "characters", "pop_host", "expressions")
    pose_dir = os.path.join(ASSETS_DIR, "characters", "pop_host", "poses")
    base_dir = os.path.join(ASSETS_DIR, "characters", "pop_host", "base")

    # 1. Generate Individual Expressions (600x600 transparent heads)
    expressions = ["neutral", "curious", "shocked", "explaining", "smiling"]
    expr_images = {}
    for exp in expressions:
        img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw_dr_doc_head(draw, 300, 300, scale=1.5, expr=exp)
        p = os.path.join(expr_dir, f"exp_{exp}.png")
        img.save(p, "PNG")
        expr_images[exp] = img

    # Save master base head
    expr_images["neutral"].save(os.path.join(base_dir, "head_neutral.png"), "PNG")

    # 2. Expression Sheet Preview
    sheet_w, sheet_h = 1600, 480
    exp_sheet = Image.new("RGBA", (sheet_w, sheet_h), (24, 28, 38, 255))
    s_draw = ImageDraw.Draw(exp_sheet)
    title_font = get_font(28, bold=True)
    label_font = get_font(20, bold=True)

    s_draw.text((40, 25), "DR. DOC — FACIAL EXPRESSION MATRIX (POP INFO)", fill=(255, 255, 255), font=title_font)

    for i, exp in enumerate(expressions):
        box_x = 40 + i * 300
        box_y = 80
        s_draw.rounded_rectangle([box_x, box_y, box_x + 280, box_y + 360], radius=12, fill=(32, 40, 56), outline=(60, 75, 105), width=2)
        head_thumb = expr_images[exp].resize((220, 220), Image.Resampling.LANCZOS)
        exp_sheet.paste(head_thumb, (box_x + 30, box_y + 35), head_thumb)
        s_draw.text((box_x + 40, box_y + 305), f"exp_{exp}", fill=(232, 93, 74), font=label_font)

    exp_sheet.save(os.path.join(PREVIEWS_DIR, "pop_host_expression_sheet.png"), "PNG")
    print("  -> Saved docs/previews/pop_host_expression_sheet.png")

    # 3. Generate Complete Upper-Body Poses (800x1200 transparent PNGs, native scale)
    poses = [
        {"id": "pose_idle_welcoming", "exp": "neutral", "label": "Welcoming Idle"},
        {"id": "pose_thinking_chin", "exp": "curious", "label": "Thinking / Chin Hold"},
        {"id": "pose_presenting_left", "exp": "explaining", "label": "Presenting Left"},
        {"id": "pose_presenting_right", "exp": "explaining", "label": "Presenting Right"},
        {"id": "pose_shocked_hands", "exp": "shocked", "label": "Shocked Reactions"},
        {"id": "pose_pointing_up", "exp": "smiling", "label": "Pointing High (CTA / Fact)"}
    ]

    pose_images = {}
    for p_info in poses:
        pid = p_info["id"]
        exp = p_info["exp"]
        pw, ph = 800, 1200
        pimg = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(pimg)

        # Standard Torso & Anchor
        cx, cy = 400, 480 # Head center anchor
        torso_col = (47, 111, 115) # Teal jacket
        collar_col = (232, 93, 74) # Coral accent
        shirt_col = (26, 34, 56)   # Inner indigo shirt

        # Left Arm Back / Present
        if pid == "pose_presenting_left":
            pdraw.line([(cx - 160, cy + 220), (cx - 320, cy + 120)], fill=torso_col, width=65)
            pdraw.ellipse([cx - 360, cy + 80, cx - 290, cy + 150], fill=(248, 215, 184)) # hand
        elif pid == "pose_shocked_hands":
            pdraw.line([(cx - 160, cy + 240), (cx - 190, cy + 80)], fill=torso_col, width=65)
            pdraw.ellipse([cx - 230, cy + 40, cx - 160, cy + 110], fill=(248, 215, 184)) # hand on cheek
        else: # relaxed left
            pdraw.line([(cx - 160, cy + 220), (cx - 240, cy + 500)], fill=torso_col, width=65)

        # Main Torso
        pdraw.rounded_rectangle([cx - 180, cy + 170, cx + 180, cy + 620], radius=50, fill=torso_col)
        # Inner shirt & collar
        pdraw.polygon([(cx - 60, cy + 170), (cx, cy + 320), (cx + 60, cy + 170)], fill=shirt_col)
        pdraw.polygon([(cx - 50, cy + 170), (cx, cy + 260), (cx + 50, cy + 170)], fill=collar_col)

        # Right Arm
        if pid == "pose_thinking_chin":
            pdraw.line([(cx + 160, cy + 240), (cx + 120, cy + 380), (cx + 20, cy + 140)], fill=torso_col, width=65)
            pdraw.ellipse([cx + 5, cy + 100, cx + 75, cy + 170], fill=(248, 215, 184)) # Hand to chin
        elif pid == "pose_presenting_right":
            pdraw.line([(cx + 160, cy + 220), (cx + 320, cy + 120)], fill=torso_col, width=65)
            pdraw.ellipse([cx + 290, cy + 80, cx + 360, cy + 150], fill=(248, 215, 184))
        elif pid == "pose_pointing_up":
            pdraw.line([(cx + 160, cy + 220), (cx + 260, cy + 40), (cx + 280, cy - 80)], fill=torso_col, width=65)
            pdraw.ellipse([cx + 250, cy - 130, cx + 310, cy - 60], fill=(248, 215, 184)) # Pointing hand
            pdraw.line([(cx + 280, cy - 130), (cx + 280, cy - 170)], fill=(248, 215, 184), width=18) # index finger
        elif pid == "pose_shocked_hands":
            pdraw.line([(cx + 160, cy + 240), (cx + 190, cy + 80)], fill=torso_col, width=65)
            pdraw.ellipse([cx + 160, cy + 40, cx + 230, cy + 110], fill=(248, 215, 184)) # hand on cheek
        else: # relaxed right
            pdraw.line([(cx + 160, cy + 220), (cx + 240, cy + 500)], fill=torso_col, width=65)

        # Draw Head on top of anchor
        draw_dr_doc_head(pdraw, cx, cy, scale=1.45, expr=exp)

        out_path = os.path.join(pose_dir, f"{pid}.png")
        pimg.save(out_path, "PNG")
        pose_images[pid] = pimg

    # 4. Pose Sheet Preview
    sheet_w, sheet_h = 1860, 680
    pose_sheet = Image.new("RGBA", (sheet_w, sheet_h), (24, 28, 38, 255))
    ps_draw = ImageDraw.Draw(pose_sheet)
    ps_draw.text((40, 25), "DR. DOC — REUSABLE POSE SYSTEM (POP INFO)", fill=(255, 255, 255), font=title_font)

    for i, p_info in enumerate(poses):
        pid = p_info["id"]
        box_x = 40 + i * 300
        box_y = 80
        ps_draw.rounded_rectangle([box_x, box_y, box_x + 280, box_y + 560], radius=12, fill=(32, 40, 56), outline=(60, 75, 105), width=2)
        pose_thumb = pose_images[pid].resize((240, 360), Image.Resampling.LANCZOS)
        pose_sheet.paste(pose_thumb, (box_x + 20, box_y + 70), pose_thumb)
        ps_draw.text((box_x + 20, box_y + 490), p_info["label"], fill=(255, 255, 255), font=label_font)
        ps_draw.text((box_x + 20, box_y + 520), pid, fill=(232, 93, 74), font=get_font(15))

    pose_sheet.save(os.path.join(PREVIEWS_DIR, "pop_host_pose_sheet.png"), "PNG")
    print("  -> Saved docs/previews/pop_host_pose_sheet.png")

# ==============================================================================
# 3. CHALK TALK FIGURES & DATA STORY SILHOUETTES
# ==============================================================================

def generate_chalk_and_data_characters():
    """Generates authentic chalk figures and data journalism silhouettes."""
    print("Generating Chalk Talk figures and Data Story silhouettes...")

    chalk_dir = os.path.join(ASSETS_DIR, "characters", "chalk_figures")
    sil_dir = os.path.join(ASSETS_DIR, "characters", "silhouettes")

    # A. Chalk Teacher Pointing
    img = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    chalk_white = (245, 248, 250, 240)
    chalk_yellow = (252, 211, 77, 240)

    # Hand-drawn head with chalk jitter
    d.ellipse([220, 100, 380, 260], outline=chalk_white, width=6)
    # Chalk glasses
    d.rounded_rectangle([250, 160, 295, 195], radius=6, outline=chalk_yellow, width=4)
    d.rounded_rectangle([305, 160, 350, 195], radius=6, outline=chalk_yellow, width=4)
    d.line([(295, 178), (305, 178)], fill=chalk_yellow, width=4)
    # Chalk smiling mouth
    d.arc([270, 210, 330, 240], start=10, end=170, fill=chalk_white, width=5)
    # Spine / Body
    d.line([(300, 260), (300, 560)], fill=chalk_white, width=8)
    # Left Arm resting
    d.line([(300, 320), (220, 460)], fill=chalk_white, width=7)
    # Right Arm pointing upward right
    d.line([(300, 320), (460, 240)], fill=chalk_yellow, width=8)
    # Hand & pointing finger
    d.line([(460, 240), (510, 210)], fill=chalk_yellow, width=6)
    # Legs
    d.line([(300, 560), (220, 800)], fill=chalk_white, width=7)
    d.line([(300, 560), (380, 800)], fill=chalk_white, width=7)

    # Chalk dust jitter
    for _ in range(80):
        px = random.randint(180, 520)
        py = random.randint(80, 820)
        d.point((px, py), fill=(255, 255, 255, random.randint(40, 160)))

    img.save(os.path.join(chalk_dir, "chalk_teacher_pointing.png"), "PNG")

    # B. Chalk Student Thinking
    img2 = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(img2)
    d2.ellipse([220, 120, 380, 280], outline=chalk_white, width=6)
    # Question mark above head
    d2.text((280, 40), "?", fill=chalk_yellow, font=get_font(72, bold=True))
    d2.line([(300, 280), (300, 580)], fill=chalk_white, width=8)
    # Scratching head arm
    d2.line([(300, 340), (380, 260), (350, 150)], fill=chalk_white, width=7)
    # Hip arm
    d2.line([(300, 340), (210, 420), (250, 500)], fill=chalk_white, width=7)
    # Legs
    d2.line([(300, 580), (230, 820)], fill=chalk_white, width=7)
    d2.line([(300, 580), (370, 820)], fill=chalk_white, width=7)
    img2.save(os.path.join(chalk_dir, "chalk_student_thinking.png"), "PNG")

    # C. Data Story Editorial Silhouette
    sil_img = Image.new("RGBA", (400, 800), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sil_img)
    slate_ink = (25, 35, 50, 240)
    sd.ellipse([140, 40, 260, 160], fill=slate_ink)
    sd.rounded_rectangle([100, 180, 300, 480], radius=35, fill=slate_ink)
    sd.rounded_rectangle([110, 480, 185, 780], radius=20, fill=slate_ink)
    sd.rounded_rectangle([215, 480, 290, 780], radius=20, fill=slate_ink)
    sil_img.save(os.path.join(sil_dir, "human_scale_monochrome.png"), "PNG")

    # D. Data Story Consumer Stamp
    stamp = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    std = ImageDraw.Draw(stamp)
    std.ellipse([20, 20, 380, 380], fill=(16, 185, 129, 30), outline=(16, 185, 129), width=8)
    std.ellipse([140, 80, 260, 200], fill=(16, 185, 129))
    std.chord([80, 210, 320, 420], start=180, end=360, fill=(16, 185, 129))
    stamp.save(os.path.join(sil_dir, "consumer_avatar_stamp.png"), "PNG")
    print("  -> Saved chalk figures & data silhouettes.")

# ==============================================================================
# 4. STYLE-SPECIFIC BACKGROUNDS (1080x1920)
# ==============================================================================

def generate_background_plates():
    """Generates 1080x1920 (9:16) high-resolution background plates for all 4 styles."""
    print("Generating 1080x1920 Background Plates for all 4 styles...")
    bg_dir = os.path.join(ASSETS_DIR, "backgrounds")
    bw, bh = 1080, 1920

    # ----------------------------------------------------
    # A. POP INFO: bg_warm_cream.png & bg_supermarket_aisle.png
    # ----------------------------------------------------
    pop_dir = os.path.join(bg_dir, "pop_info")

    # 1. Warm Cream Pastel
    cream = Image.new("RGB", (bw, bh), (245, 239, 235))
    cd = ImageDraw.Draw(cream)
    # Floor horizon line & warm floor
    cd.rectangle([0, 1380, bw, bh], fill=(232, 224, 216))
    cd.line([(0, 1380), (bw, 1380)], fill=(210, 198, 186), width=4)
    # Soft room shadow ellipse
    cd.ellipse([140, 1340, 940, 1460], fill=(218, 208, 198))
    # Soft warm corner vignette
    for i in range(120):
        alpha = int((1.0 - i/120.0) * 18)
        cd.rectangle([i, i, bw - i, bh - i], outline=(200, 180, 160))
    cream.save(os.path.join(pop_dir, "bg_warm_cream.png"), "PNG")

    # 2. Supermarket Aisle Isometric
    aisle = Image.new("RGB", (bw, bh), (240, 244, 246))
    ad = ImageDraw.Draw(aisle)
    # Floor perspective
    ad.polygon([(0, 1920), (bw, 1920), (bw*0.75, 1100), (bw*0.25, 1100)], fill=(225, 230, 234))
    # Left & Right shelves
    ad.polygon([(0, 300), (bw*0.25, 1100), (bw*0.25, 1920), (0, 1920)], fill=(47, 111, 115))
    ad.polygon([(bw, 300), (bw*0.75, 1100), (bw*0.75, 1920), (bw, 1920)], fill=(47, 111, 115))
    # Shelf lines
    for sy in range(600, 1700, 250):
        ad.line([(0, sy), (bw*0.25, int(1100 + (sy-1100)*0.8))], fill=(255, 255, 255), width=8)
        ad.line([(bw, sy), (bw*0.75, int(1100 + (sy-1100)*0.8))], fill=(255, 255, 255), width=8)
    # Ambient warm glow at center back
    aisle.save(os.path.join(pop_dir, "bg_supermarket_aisle.png"), "PNG")

    # ----------------------------------------------------
    # B. DATA STORY: bg_dark_slate_grid.png & bg_editorial_dots.png
    # ----------------------------------------------------
    data_dir = os.path.join(bg_dir, "data_story")

    # 1. Dark Slate Coordinate Grid
    grid = Image.new("RGB", (bw, bh), (15, 20, 28))
    gd = ImageDraw.Draw(grid)
    grid_col = (30, 42, 60)
    axis_col = (16, 185, 129, 120)
    for x in range(0, bw, 80):
        gd.line([(x, 0), (x, bh)], fill=grid_col, width=1)
    for y in range(0, bh, 80):
        gd.line([(0, y), (bw, y)], fill=grid_col, width=1)
    # Major center axes
    gd.line([(bw//2, 0), (bw//2, bh)], fill=(59, 130, 246), width=2)
    gd.line([(0, bh//2), (bw, bh//2)], fill=(59, 130, 246), width=2)
    grid.save(os.path.join(data_dir, "bg_dark_slate_grid.png"), "PNG")

    # 2. Editorial Dot Matrix
    dots = Image.new("RGB", (bw, bh), (15, 20, 28))
    dd = ImageDraw.Draw(dots)
    for x in range(40, bw, 60):
        for y in range(40, bh, 60):
            dd.point((x, y), fill=(50, 70, 95))
    dots.save(os.path.join(data_dir, "bg_editorial_dots.png"), "PNG")

    # ----------------------------------------------------
    # C. CHALK TALK: bg_slate_blackboard.png
    # ----------------------------------------------------
    chalk_bdir = os.path.join(bg_dir, "chalk_talk")

    board = Image.new("RGB", (bw, bh), (36, 57, 47)) # Deep blackboard green
    bd = ImageDraw.Draw(board)
    # Oak wooden frame (40px)
    frame_col = (58, 35, 18)
    bd.rectangle([0, 0, bw, 40], fill=frame_col)
    bd.rectangle([0, bh - 40, bw, bh], fill=frame_col)
    bd.rectangle([0, 0, 40, bh], fill=frame_col)
    bd.rectangle([bw - 40, 0, bw, bh], fill=frame_col)
    # Frame inner bevel
    bd.rectangle([38, 38, bw - 38, bh - 38], outline=(90, 55, 30), width=3)

    # Chalk dust smudges & felt eraser marks
    for _ in range(4000):
        px = random.randint(45, bw - 45)
        py = random.randint(45, bh - 45)
        v = random.randint(45, 75)
        board.putpixel((px, py), (v - 5, v + 15, v))
    board.save(os.path.join(chalk_bdir, "bg_slate_blackboard.png"), "PNG")

    # ----------------------------------------------------
    # D. CREATOR MOTION GRAPHICS: bg_studio_black.png
    # ----------------------------------------------------
    creator_dir = os.path.join(bg_dir, "creator")

    studio = Image.new("RGB", (bw, bh), (11, 11, 13)) # #0B0B0D
    sd = ImageDraw.Draw(studio)
    # Subtle top spotlight radial glow
    for r in range(600, 0, -10):
        col = int((1.0 - r/600.0) * 35)
        sd.ellipse([bw//2 - r, -100 - r//2, bw//2 + r, 500 + r], outline=(col, col, col + 5))
    # Kinetic yellow bottom rim accent
    sd.line([(0, bh - 6), (bw, bh - 6)], fill=(255, 230, 0), width=4)
    studio.save(os.path.join(creator_dir, "bg_studio_black.png"), "PNG")

    # Composite Background Preview Sheet
    psheet = Image.new("RGBA", (1920, 1080), (20, 24, 32, 255))
    pd = ImageDraw.Draw(psheet)
    pd.text((40, 30), "CRAZZYDOCS — 1080x1920 BACKGROUND PLATES ACROSS 4 STYLES", fill=(255, 255, 255), font=get_font(34, bold=True))

    thumbs = [
        ("POP INFO: Warm Cream", cream),
        ("DATA STORY: Slate Grid", grid),
        ("CHALK TALK: Blackboard", board),
        ("CREATOR: Studio Black", studio)
    ]
    for i, (name, img_plate) in enumerate(thumbs):
        bx = 40 + i * 465
        by = 100
        thumb = img_plate.resize((430, 764), Image.Resampling.LANCZOS)
        psheet.paste(thumb, (bx, by))
        pd.rectangle([bx, by, bx + 430, by + 764], outline=(70, 85, 115), width=2)
        pd.text((bx + 10, by + 785), name, fill=(255, 255, 255), font=get_font(20, bold=True))

    psheet.save(os.path.join(PREVIEWS_DIR, "backgrounds_preview_sheet.png"), "PNG")
    print("  -> Saved docs/previews/backgrounds_preview_sheet.png")

# ==============================================================================
# 5. REUSABLE STORYTELLING PROPS
# ==============================================================================

def generate_storytelling_props():
    """Generates storytelling props and iconography for each style."""
    print("Generating Storytelling Props across all 4 styles...")
    p_dir = os.path.join(ASSETS_DIR, "props")

    # A. POP INFO: Milk Carton Mascot & Lightbulb
    pop_p = os.path.join(p_dir, "pop_info")
    # Milk Carton
    milk = Image.new("RGBA", (400, 500), (0, 0, 0, 0))
    md = ImageDraw.Draw(milk)
    md.polygon([(100, 150), (200, 80), (300, 150), (300, 450), (100, 450)], fill=(245, 245, 250), outline=(47, 111, 115), width=8)
    md.polygon([(100, 150), (200, 80), (300, 150)], fill=(232, 93, 74)) # coral gable
    md.rectangle([100, 240, 300, 340], fill=(79, 180, 195)) # cyan label band
    md.text((150, 265), "MILK", fill=(255, 255, 255), font=get_font(38, bold=True))
    # Smiling cartoon eyes & mouth
    md.ellipse([160, 370, 180, 390], fill=(29, 38, 43))
    md.ellipse([220, 370, 240, 390], fill=(29, 38, 43))
    md.arc([180, 385, 220, 415], start=0, end=180, fill=(29, 38, 43), width=5)
    milk.save(os.path.join(pop_p, "milk_carton_mascot.png"), "PNG")

    # Lightbulb Idea
    bulb = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bulb)
    bd.ellipse([120, 60, 280, 220], fill=(254, 224, 4), outline=(210, 170, 0), width=6)
    bd.rectangle([160, 210, 240, 280], fill=(200, 205, 215), outline=(140, 150, 160), width=4)
    # Screw base lines
    bd.line([(160, 235), (240, 235)], fill=(120, 130, 140), width=4)
    bd.line([(160, 260), (240, 260)], fill=(120, 130, 140), width=4)
    bulb.save(os.path.join(pop_p, "lightbulb_idea.png"), "PNG")

    # B. DATA STORY: IEA Badge & Trend Surge Arrow
    ds_p = os.path.join(p_dir, "data_story")
    # IEA Institutional Badge
    badge = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse([30, 30, 370, 370], fill=(15, 23, 42), outline=(59, 130, 246), width=6)
    bd.ellipse([50, 50, 350, 350], outline=(16, 185, 129), width=2)
    bd.text((145, 150), "IEA", fill=(255, 255, 255), font=get_font(52, bold=True))
    bd.text((85, 230), "OFFICIAL DATASET", fill=(148, 163, 184), font=get_font(20, bold=True))
    badge.save(os.path.join(ds_p, "institutional_badge_iea.png"), "PNG")

    # Trend Surge Arrow
    arrow = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arrow)
    ad.polygon([(100, 320), (260, 160), (260, 240), (320, 240), (320, 80), (160, 80), (160, 140), (240, 140), (80, 300)], fill=(16, 185, 129))
    arrow.save(os.path.join(ds_p, "trend_surge_arrow.png"), "PNG")

    # C. CHALK TALK: Chalk Stick & Felt Eraser
    ct_p = os.path.join(p_dir, "chalk_talk")
    # Chalk stick
    chalk = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    cd = ImageDraw.Draw(chalk)
    cd.polygon([(50, 80), (300, 40), (320, 80), (70, 120)], fill=(245, 248, 250))
    cd.ellipse([40, 75, 75, 125], fill=(220, 225, 230)) # tip
    chalk.save(os.path.join(ct_p, "chalk_stick_white.png"), "PNG")

    # Felt eraser
    eraser = Image.new("RGBA", (400, 250), (0, 0, 0, 0))
    ed = ImageDraw.Draw(eraser)
    ed.rounded_rectangle([60, 60, 340, 140], radius=16, fill=(180, 120, 70), outline=(120, 70, 30), width=4) # Wood block
    ed.rectangle([60, 140, 340, 180], fill=(60, 60, 60)) # Felt strip
    eraser.save(os.path.join(ct_p, "felt_eraser_dust.png"), "PNG")

    # D. CREATOR: iPhone Mockup & Verified Badge
    cr_p = os.path.join(p_dir, "creator")
    # iPhone Frame Mockup
    phone = Image.new("RGBA", (500, 900), (0, 0, 0, 0))
    phd = ImageDraw.Draw(phone)
    # Bezel
    phd.rounded_rectangle([40, 30, 460, 870], radius=60, fill=(20, 20, 24), outline=(80, 80, 95), width=8)
    # Screen area
    phd.rounded_rectangle([60, 50, 440, 850], radius=45, fill=(11, 11, 13))
    # Dynamic Island pill
    phd.rounded_rectangle([200, 75, 300, 105], radius=15, fill=(0, 0, 0))
    phone.save(os.path.join(cr_p, "device_iphone_mockup.png"), "PNG")

    # Verified Badge
    vbadge = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vbadge)
    vd.ellipse([30, 30, 270, 270], fill=(0, 240, 255))
    vd.line([(90, 150), (135, 195), (210, 105)], fill=(0, 0, 0), width=24)
    vbadge.save(os.path.join(cr_p, "verified_badge.png"), "PNG")

    # Composite Props Preview Sheet
    props_sheet = Image.new("RGBA", (1600, 500), (22, 26, 36, 255))
    psd = ImageDraw.Draw(props_sheet)
    psd.text((40, 25), "CRAZZYDOCS — REUSABLE STORYTELLING PROPS", fill=(255, 255, 255), font=get_font(28, bold=True))

    prop_items = [
        ("Pop Info: Milk Mascot", milk),
        ("Pop Info: Idea Bulb", bulb),
        ("Data Story: IEA Seal", badge),
        ("Chalk Talk: Eraser", eraser),
        ("Creator: Verified", vbadge)
    ]
    for i, (pname, pimg) in enumerate(prop_items):
        bx = 40 + i * 300
        by = 80
        psd.rounded_rectangle([bx, by, bx + 280, by + 380], radius=12, fill=(32, 40, 56), outline=(60, 75, 105), width=2)
        thumb = pimg.resize((220, 220), Image.Resampling.LANCZOS)
        props_sheet.paste(thumb, (bx + 30, by + 40), thumb)
        psd.text((bx + 20, by + 320), pname, fill=(255, 255, 255), font=get_font(18, bold=True))

    props_sheet.save(os.path.join(PREVIEWS_DIR, "props_preview_sheet.png"), "PNG")
    print("  -> Saved docs/previews/props_preview_sheet.png")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

if __name__ == "__main__":
    print("==========================================================")
    print("STARTING CRAZZYDOCS VISUAL ASSET GENERATION (STAGE B)")
    print("==========================================================")
    generate_character_concept_sheet()
    generate_pop_host_system()
    generate_chalk_and_data_characters()
    generate_background_plates()
    generate_storytelling_props()
    print("==========================================================")
    print("ALL VISUAL ASSETS GENERATED & PREVIEWED SUCCESSFULLY!")
    print("==========================================================")
