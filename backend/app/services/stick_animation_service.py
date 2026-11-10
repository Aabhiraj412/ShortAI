"""
Polished 2D Stick-Figure & Motion Graphics Video Rendering Engine.
Generates engaging 1080x1920 @ 30 FPS vertical video with clean character silhouettes,
forward kinematics poses, motion graphics diagrams (singularity, spacetime grid),
dynamic camera movement, kinetic typography, particle physics, and ASS burn-in subtitles.
"""
import json
import math
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = r"D:\AI Arts\AI"
MEDIA_VIDEO_DIR = os.path.join(BASE_DIR, "media", "video")
THUMB_DIR = os.path.join(BASE_DIR, "thumbnails", "output")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
MUSIC_DIR = os.path.join(BASE_DIR, "assets", "audio", "music")
FFMPEG_EXE = os.path.join(BASE_DIR, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_EXE = os.path.join(BASE_DIR, "tools", "ffmpeg", "bin", "ffprobe.exe")

os.makedirs(MEDIA_VIDEO_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Proportions for 1080x1920 vertical canvas
PROPORTIONS = {
    "head_radius": 46,
    "torso_len": 135,
    "arm_seg1": 80,
    "arm_seg2": 75,
    "leg_seg1": 100,
    "leg_seg2": 95,
    "line_width": 16
}

class StickFigureRig:
    def __init__(self, color=(240, 245, 255, 255), outline_color=(0, 240, 255, 255)):
        self.color = color
        self.glow_color = (0, 240, 255, 60)

    def draw_bone(self, draw: ImageDraw.ImageDraw, p1: Tuple[int, int], p2: Tuple[int, int], width: int = 16):
        # Subtle glowing under-stroke
        draw.line([p1, p2], fill=self.glow_color, width=width + 8)
        # Main solid limb stroke with rounded caps
        draw.line([p1, p2], fill=self.color, width=width)
        # Joint cap
        r = width // 2
        draw.ellipse([p1[0]-r, p1[1]-r, p1[0]+r, p1[1]+r], fill=self.color)
        draw.ellipse([p2[0]-r, p2[1]-r, p2[0]+r, p2[1]+r], fill=self.color)

    def draw_character(
        self,
        draw: ImageDraw.ImageDraw,
        t: float,
        pose: str = "standing",
        base_x: int = 540,
        base_y: int = 1380,
        expression: str = "neutral",
        is_talking: bool = True
    ):
        breath_y = int(math.sin(t * 3.2) * 6)
        hip_x = base_x
        hip_y = base_y + breath_y
        neck_x = hip_x
        neck_y = hip_y - PROPORTIONS["torso_len"]
        head_x = neck_x
        head_y = neck_y - int(PROPORTIONS["head_radius"] * 1.35)
        
        lw = PROPORTIONS["line_width"]
        
        # Calculate Joint Coordinates based on Pose
        if pose == "thinking":
            # Hand on chin, head tilted, other arm relaxed
            head_x += 8
            l_sh = (neck_x - 32, neck_y + 15)
            l_elb = (neck_x - 55, neck_y + 85)
            l_hand = (neck_x - 45, neck_y + 145)
            
            r_sh = (neck_x + 32, neck_y + 15)
            r_elb = (neck_x + 75, neck_y + 70)
            r_hand = (head_x + 15, head_y + 35) # touching chin
            
            l_hip = (hip_x - 28, hip_y)
            l_knee = (hip_x - 38, hip_y + PROPORTIONS["leg_seg1"])
            l_foot = (hip_x - 42, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
            r_hip = (hip_x + 28, hip_y)
            r_knee = (hip_x + 38, hip_y + PROPORTIONS["leg_seg1"])
            r_foot = (hip_x + 42, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
        elif pose == "pointing":
            # Right arm extended up/right pointing at infographic
            l_sh = (neck_x - 32, neck_y + 15)
            l_elb = (neck_x - 60, neck_y + 80)
            l_hand = (neck_x - 50, neck_y + 145)
            
            r_sh = (neck_x + 32, neck_y + 15)
            r_elb = (neck_x + 105, neck_y - 20)
            r_hand = (neck_x + 185, neck_y - 85)
            
            l_hip = (hip_x - 28, hip_y)
            l_knee = (hip_x - 40, hip_y + PROPORTIONS["leg_seg1"])
            l_foot = (hip_x - 45, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
            r_hip = (hip_x + 28, hip_y)
            r_knee = (hip_x + 40, hip_y + PROPORTIONS["leg_seg1"])
            r_foot = (hip_x + 45, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
        elif pose == "presenting":
            # Both arms open wide presenting screen
            l_sh = (neck_x - 32, neck_y + 15)
            l_elb = (neck_x - 95, neck_y + 35)
            l_hand = (neck_x - 155, neck_y - 15)
            
            r_sh = (neck_x + 32, neck_y + 15)
            r_elb = (neck_x + 95, neck_y + 35)
            r_hand = (neck_x + 155, neck_y - 15)
            
            l_hip = (hip_x - 28, hip_y)
            l_knee = (hip_x - 38, hip_y + PROPORTIONS["leg_seg1"])
            l_foot = (hip_x - 45, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
            r_hip = (hip_x + 28, hip_y)
            r_knee = (hip_x + 38, hip_y + PROPORTIONS["leg_seg1"])
            r_foot = (hip_x + 45, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
        elif pose == "falling":
            # Tumbling into gravity singularity!
            tumble_x = int(math.sin(t * 6.0) * 25)
            tumble_y = int(math.cos(t * 6.0) * 15)
            head_x += tumble_x
            head_y += tumble_y
            
            l_sh = (neck_x - 30, neck_y)
            l_elb = (neck_x - 90, neck_y - 65)
            l_hand = (neck_x - 135, neck_y - 130) # splayed up
            
            r_sh = (neck_x + 30, neck_y)
            r_elb = (neck_x + 90, neck_y - 65)
            r_hand = (neck_x + 140, neck_y - 135) # splayed up
            
            l_hip = (hip_x - 25, hip_y)
            l_knee = (hip_x - 80, hip_y + 55)
            l_foot = (hip_x - 120, hip_y + 125)
            
            r_hip = (hip_x + 25, hip_y)
            r_knee = (hip_x + 75, hip_y + 55)
            r_foot = (hip_x + 115, hip_y + 120)
            
        else: # standing
            l_sh = (neck_x - 32, neck_y + 15)
            l_elb = (neck_x - 52, neck_y + 85)
            l_hand = (neck_x - 48, neck_y + 150)
            
            r_sh = (neck_x + 32, neck_y + 15)
            r_elb = (neck_x + 52, neck_y + 85)
            r_hand = (neck_x + 48, neck_y + 150)
            
            l_hip = (hip_x - 28, hip_y)
            l_knee = (hip_x - 32, hip_y + PROPORTIONS["leg_seg1"])
            l_foot = (hip_x - 35, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])
            
            r_hip = (hip_x + 28, hip_y)
            r_knee = (hip_x + 32, hip_y + PROPORTIONS["leg_seg1"])
            r_foot = (hip_x + 35, hip_y + PROPORTIONS["leg_seg1"] + PROPORTIONS["leg_seg2"])

        # 1. Draw Legs
        self.draw_bone(draw, l_hip, l_knee, lw)
        self.draw_bone(draw, l_knee, l_foot, lw)
        self.draw_bone(draw, r_hip, r_knee, lw)
        self.draw_bone(draw, r_knee, r_foot, lw)
        
        # 2. Draw Torso Spine
        self.draw_bone(draw, (hip_x, hip_y), (neck_x, neck_y), lw + 2)
        
        # 3. Draw Arms
        self.draw_bone(draw, l_sh, l_elb, lw)
        self.draw_bone(draw, l_elb, l_hand, lw)
        self.draw_bone(draw, r_sh, r_elb, lw)
        self.draw_bone(draw, r_elb, r_hand, lw)
        
        # 4. Draw Clean Head Silhouette
        hr = PROPORTIONS["head_radius"]
        # Glow ring
        draw.ellipse([head_x - hr - 4, head_y - hr - 4, head_x + hr + 4, head_y + hr + 4], fill=self.glow_color)
        # Solid head circle
        draw.ellipse([head_x - hr, head_y - hr, head_x + hr, head_y + hr], fill=(15, 23, 42, 255), outline=self.color, width=5)
        
        # 5. Expressive Facial Eyes & Features
        # Blinking logic (blink every 3.5s for 0.15s)
        is_blinking = 0.0 <= (t % 3.5) <= 0.15
        
        if is_blinking:
            draw.line([(head_x - 18, head_y - 2), (head_x - 6, head_y - 2)], fill=self.color, width=3)
            draw.line([(head_x + 6, head_y - 2), (head_x + 18, head_y - 2)], fill=self.color, width=3)
        elif expression == "surprised" or pose == "falling":
            # Wide surprised eyes
            draw.ellipse([head_x - 20, head_y - 12, head_x - 6, head_y + 4], fill=(255, 255, 255, 255))
            draw.ellipse([head_x + 6, head_y - 12, head_x + 20, head_y + 4], fill=(255, 255, 255, 255))
            draw.ellipse([head_x - 15, head_y - 6, head_x - 9, head_y], fill=(0, 240, 255, 255))
            draw.ellipse([head_x + 11, head_y - 6, head_x + 17, head_y], fill=(0, 240, 255, 255))
        elif expression == "thinking":
            # Eyebrow raised, eyes looking up
            draw.ellipse([head_x - 16, head_y - 8, head_x - 8, head_y], fill=self.color)
            draw.ellipse([head_x + 8, head_y - 8, head_x + 16, head_y], fill=self.color)
            draw.line([(head_x - 20, head_y - 18), (head_x - 6, head_y - 24)], fill=self.color, width=3) # Arching brow
            draw.line([(head_x + 6, head_y - 18), (head_x + 20, head_y - 18)], fill=self.color, width=3)
        else:
            # Clean dot eyes
            draw.ellipse([head_x - 16, head_y - 5, head_x - 8, head_y + 3], fill=self.color)
            draw.ellipse([head_x + 8, head_y - 5, head_x + 16, head_y + 3], fill=self.color)
            
        # Subtle Talking / Speech Visual Cue
        if is_talking:
            speech_r = int((t * 9.0) % 3)
            if speech_r == 0:
                draw.arc([head_x - 8, head_y + 12, head_x + 8, head_y + 24], start=0, end=180, fill=self.color, width=3)
            elif speech_r == 1:
                draw.ellipse([head_x - 6, head_y + 14, head_x + 6, head_y + 26], fill=(0, 240, 255, 200))
            else:
                draw.line([(head_x - 7, head_y + 18), (head_x + 7, head_y + 18)], fill=self.color, width=3)


class MotionGraphicsDirector:
    """Renders high-end animated infographic elements and backgrounds."""
    
    @staticmethod
    def draw_singularity(draw: ImageDraw.ImageDraw, t: float, cx: int = 540, cy: int = 680):
        """Renders an animated Black Hole with glowing accretion disk and gravitational lensing."""
        # Multi-layer glowing accretion disk
        angle = t * 1.5
        for r in range(240, 80, -20):
            alpha = int(45 + (240 - r) * 0.8)
            draw.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], outline=(255, 120, 0, alpha), width=4)
        
        # Swirling particle arcs
        for i in range(8):
            a_start = (angle + i * (math.pi / 4)) % (2 * math.pi)
            a_deg = int(math.degrees(a_start))
            draw.arc([cx - 200, cy - 90, cx + 200, cy + 90], start=a_deg, end=a_deg + 45, fill=(0, 240, 255, 220), width=5)
            
        # Pitch-black Event Horizon Sphere with photon ring
        draw.ellipse([cx - 95, cy - 95, cx + 95, cy + 95], fill=(255, 230, 100, 255))
        draw.ellipse([cx - 88, cy - 88, cx + 88, cy + 88], fill=(3, 5, 12, 255))

    @staticmethod
    def draw_spacetime_grid(draw: ImageDraw.ImageDraw, t: float):
        """Draws a perspective grid that warps near the center."""
        base_y = 1500
        for i in range(5):
            y = base_y + i * 85
            draw.line([(0, y), (1080, y)], fill=(0, 240, 255, 40), width=2)
        for x in range(0, 1081, 120):
            warp_x = int(math.sin(t + x) * 10)
            draw.line([(540, 1400), (x + warp_x, 1920)], fill=(0, 240, 255, 35), width=2)


def render_stick_frame(
    t: float,
    scene_idx: int,
    bg_img: Image.Image,
    stick_rig: StickFigureRig,
    storyboard: Dict,
    font_large: ImageFont.ImageFont,
    font_badge: ImageFont.ImageFont
) -> Image.Image:
    frame = bg_img.copy()
    draw = ImageDraw.Draw(frame, "RGBA")
    
    scene = storyboard["scenes"][scene_idx]
    pose = scene.get("pose", "standing")
    expression = scene.get("expression", "neutral")
    char_x = scene.get("char_x", 540)
    char_y = scene.get("char_y", 1380)
    
    # 1. Motion Graphics: Spacetime grid in background
    MotionGraphicsDirector.draw_spacetime_grid(draw, t)
    
    # 2. Motion Graphics: Singularity in Scene 1 & 2
    if scene_idx in (0, 1):
        MotionGraphicsDirector.draw_singularity(draw, t, cx=540, cy=720)
        
    # 3. Draw Character with forward kinematics
    stick_rig.draw_character(
        draw=draw,
        t=t,
        pose=pose,
        base_x=char_x,
        base_y=char_y,
        expression=expression,
        is_talking=scene.get("talking", True)
    )
    
    # 4. Kinetic Typography Overlay
    headline = scene.get("headline", "")
    badge = scene.get("badge", "")
    
    if headline:
        scene_t = t - scene["start_time"]
        pop_scale = min(1.0, scene_t * 6.0)
        box_y = 260
        box_w = int(960 * pop_scale)
        box_h = 135
        box_x = 540 - box_w // 2
        
        # Shadow & Box
        draw.rounded_rectangle([box_x + 6, box_y + 8, box_x + box_w + 6, box_y + box_h + 8], radius=22, fill=(0, 0, 0, 150))
        draw.rounded_rectangle([box_x, box_y, box_x + box_w, box_y + box_h], radius=22, fill=(13, 20, 36, 240), outline=(0, 240, 255, 255), width=4)
        
        if pop_scale > 0.4:
            bbox = draw.textbbox((0, 0), headline, font=font_large)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((540 - tw // 2, box_y + (box_h - th) // 2 - 4), headline, font=font_large, fill=(255, 235, 59, 255))
            
    if badge:
        draw.rounded_rectangle([60, 130, 380, 205], radius=16, fill=(114, 9, 183, 230))
        draw.text((85, 148), badge, font=font_badge, fill=(255, 255, 255, 255))
        
    return frame


def render_stick_animation(
    storyboard: Dict,
    audio_path: Optional[str],
    ass_path: Optional[str],
    music_path: Optional[str],
    output_mp4_path: str,
    max_frames: Optional[int] = None,
    fps: int = 30
) -> bool:
    print("Initializing Stick-Figure & Motion Graphics Renderer... OK", flush=True)
    t0 = time.time()
    
    stick_rig = StickFigureRig()
    bg_img = Image.open(os.path.join(BASE_DIR, "assets", "backgrounds", "rendered", "bg_space.png")).convert("RGBA")
    
    try:
        font_large = ImageFont.truetype("arialbd.ttf", 52)
        font_badge = ImageFont.truetype("arialbd.ttf", 36)
    except:
        font_large = ImageFont.load_default()
        font_badge = font_large
        
    total_duration = storyboard["duration"]
    total_frames = int(total_duration * fps)
    if max_frames and max_frames < total_frames:
        total_frames = max_frames
        
    print(f"Rendering {total_frames} frames ({total_frames/fps:.2f}s) @ {fps}fps...", flush=True)
    
    ffmpeg_log_path = os.path.join(LOGS_DIR, "ffmpeg_stick_render.log")
    ffmpeg_log = open(ffmpeg_log_path, "w", encoding="utf-8")
    
    ffmpeg_cmd = [
        FFMPEG_EXE,
        "-y",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", "1080x1920",
        "-pix_fmt", "rgb24",
        "-r", str(fps),
        "-i", "-"
    ]
    
    has_audio = audio_path and os.path.exists(audio_path)
    has_music = music_path and os.path.exists(music_path)
    has_ass = ass_path and os.path.exists(ass_path)
    
    if has_audio and has_music:
        ffmpeg_cmd.extend(["-i", audio_path, "-i", music_path])
        if has_ass:
            esc_ass = ass_path.replace("\\", "/").replace(":", "\\:")
            ffmpeg_cmd.extend([
                "-filter_complex",
                f"[2:a]volume=0.15[bgm];"
                f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout];"
                f"[0:v]ass='{esc_ass}'[vout]",
                "-map", "[vout]",
                "-map", "[aout]"
            ])
        else:
            ffmpeg_cmd.extend([
                "-filter_complex",
                "[2:a]volume=0.15[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v",
                "-map", "[aout]"
            ])
    else:
        ffmpeg_cmd.extend(["-map", "0:v"])
        
    ffmpeg_cmd.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_mp4_path
    ])
    
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_log)
    scenes = storyboard["scenes"]
    curr_scene = 0
    start_render = time.time()
    
    try:
        for f in range(total_frames):
            t = f / fps
            while curr_scene < len(scenes) - 1 and t >= scenes[curr_scene + 1]["start_time"]:
                curr_scene += 1
                
            frame = render_stick_frame(
                t=t,
                scene_idx=curr_scene,
                bg_img=bg_img,
                stick_rig=stick_rig,
                storyboard=storyboard,
                font_large=font_large,
                font_badge=font_badge
            )
            
            # Save thumbnail at frame 40 or 15
            if f == 40 or (total_frames <= 30 and f == 15):
                thumb_path = os.path.join(THUMB_DIR, "stick_sample_thumb.png")
                frame.save(thumb_path)
                
            rgb_bytes = frame.convert("RGB").tobytes()
            proc.stdin.write(rgb_bytes)
            
            if (f + 1) % 10 == 0 or (f + 1) == total_frames:
                elapsed = time.time() - start_render
                speed = (f + 1) / max(0.001, elapsed)
                pct = ((f + 1) / total_frames) * 100
                bar = "=" * int(10 * (f + 1) // total_frames) + "-" * (10 - int(10 * (f + 1) // total_frames))
                print(f"[{bar}] {pct:5.1f}% ({f+1}/{total_frames}) | {speed:.1f} FPS", flush=True)
                
        proc.stdin.close()
        print("Encoding video... OK", flush=True)
        proc.wait(timeout=30)
    except Exception as e:
        print(f"Error during stick render: {e}", flush=True)
        proc.kill()
        return False
    finally:
        ffmpeg_log.close()
        
    if proc.returncode == 0 and os.path.exists(output_mp4_path):
        from app.services.animation_service import probe_video
        info = probe_video(output_mp4_path)
        print("Verifying stick animation output... OK", flush=True)
        print(f"  Dimensions: {info['width']}x{info['height']} | FPS: {info['fps']:.1f}")
        print(f"  Duration: {info['duration']:.2f}s | Size: {info['size_mb']:.2f} MB")
        print("Stick figure & motion graphics video successfully generated!\n", flush=True)
        return True
    return False
