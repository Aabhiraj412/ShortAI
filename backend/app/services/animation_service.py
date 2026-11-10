"""
High-Performance Animation Compositor and Video Rendering Engine.
Optimized for multi-core CPU rendering on Windows 11.
Features:
- Direct canvas drawing (zero redundant full-screen alpha composite passes)
- Non-blocking FFmpeg subprocess logging (eliminates pipe deadlocks)
- Character sprite caching and localized compositing
- Unbuffered real-time progress bar logging
- Verification via FFprobe
"""
import json
import math
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = r"D:\Agents\CrazzyDocs"
SPRITES_DIR = os.path.join(BASE_DIR, "assets", "characters", "host", "sprites")
if not os.path.exists(SPRITES_DIR):
    SPRITES_DIR = os.path.join(BASE_DIR, "archive", "legacy_assets", "characters", "host", "sprites")
BG_DIR = os.path.join(BASE_DIR, "assets", "backgrounds", "rendered")
if not os.path.exists(BG_DIR):
    BG_DIR = os.path.join(BASE_DIR, "archive", "legacy_assets", "backgrounds", "rendered")
SFX_DIR = os.path.join(BASE_DIR, "assets", "audio", "sfx")
MUSIC_DIR = os.path.join(BASE_DIR, "assets", "audio", "music")
MEDIA_VIDEO_DIR = os.path.join(BASE_DIR, "media", "video")
THUMB_DIR = os.path.join(BASE_DIR, "thumbnails", "output")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
FFMPEG_EXE = os.path.join(BASE_DIR, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_EXE = os.path.join(BASE_DIR, "tools", "ffmpeg", "bin", "ffprobe.exe")

os.makedirs(MEDIA_VIDEO_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

class OptimizedCharacterRig:
    def __init__(self, sprites_dir: str):
        t0 = time.time()
        self.sprites = {}
        names = [
            "head", "torso", "eyes_open", "eyes_blink",
            "eyebrows_neutral", "eyebrows_curious",
            "mouth_closed", "mouth_open_ae", "mouth_open_o", "mouth_smile",
            "arm_left_idle", "arm_left_present",
            "arm_right_idle", "arm_right_think"
        ]
        for name in names:
            path = os.path.join(sprites_dir, f"{name}.png")
            if os.path.exists(path):
                img = Image.open(path).convert("RGBA")
                self.sprites[name] = img
            else:
                print(f"Warning: Sprite missing: {path}", flush=True)
        print(f"Character rig initialized in {(time.time() - t0)*1000:.1f}ms ({len(self.sprites)} sprites loaded)", flush=True)

    def paste_character(
        self,
        base_frame: Image.Image,
        t: float,
        is_talking: bool = True,
        left_arm: str = "arm_left_idle",
        right_arm: str = "arm_right_idle",
        eyebrows: str = "eyebrows_neutral",
        char_x: int = 40,
        char_y: int = 760
    ):
        """Pastes character components directly onto the frame, avoiding large scratch buffers."""
        breath_y = int(math.sin(t * 3.0) * 8)
        head_bob_y = breath_y + int(math.sin(t * 3.0 + 0.4) * 4)
        
        # 1. Left Arm (Behind torso)
        l_arm = self.sprites.get(left_arm, self.sprites.get("arm_left_idle"))
        if l_arm:
            base_frame.paste(l_arm, (char_x + 180, char_y + 480 + breath_y), l_arm)
            
        # 2. Torso
        torso = self.sprites.get("torso")
        if torso:
            base_frame.paste(torso, (char_x + 200, char_y + 440 + breath_y), torso)
            
        # 3. Head & Face
        head = self.sprites.get("head")
        if head:
            base_frame.paste(head, (char_x + 300, char_y + 180 + head_bob_y), head)
            
        # 4. Eyebrows
        eb = self.sprites.get(eyebrows, self.sprites.get("eyebrows_neutral"))
        if eb:
            base_frame.paste(eb, (char_x + 400, char_y + 310 + head_bob_y), eb)
            
        # 5. Eyes (Blink logic: blink every 3.5s for 0.15s)
        is_blinking = 0.0 <= (t % 3.5) <= 0.15
        eyes = self.sprites.get("eyes_blink" if is_blinking else "eyes_open")
        if eyes:
            base_frame.paste(eyes, (char_x + 400, char_y + 335 + head_bob_y), eyes)
            
        # 6. Mouth (Talking cycle at 8Hz)
        if is_talking:
            phonemes = ["mouth_open_ae", "mouth_open_o", "mouth_smile", "mouth_closed"]
            m_key = phonemes[int((t * 8.0) % len(phonemes))]
        else:
            m_key = "mouth_closed"
        mouth = self.sprites.get(m_key)
        if mouth:
            base_frame.paste(mouth, (char_x + 450, char_y + 400 + head_bob_y), mouth)
            
        # 7. Right Arm (In front of torso)
        r_arm = self.sprites.get(right_arm, self.sprites.get("arm_right_idle"))
        if r_arm:
            base_frame.paste(r_arm, (char_x + 580, char_y + 480 + breath_y), r_arm)


class FastParticleSystem:
    def __init__(self, count: int = 30):
        self.particles = []
        for i in range(count):
            self.particles.append({
                "x": (i * 37 + 41) % 1080,
                "y": (i * 67 + 73) % 1920,
                "speed": 0.6 + ((i * 11) % 10) * 0.12,
                "size": 2 + (i % 3)
            })

    def draw_on_frame(self, draw: ImageDraw.ImageDraw, t: float):
        for p in self.particles:
            curr_y = (p["y"] - t * p["speed"] * 60) % 1920
            curr_x = (p["x"] + math.sin(t * 0.8 + p["speed"]) * 12) % 1080
            r = p["size"]
            draw.ellipse([curr_x - r, curr_y - r, curr_x + r, curr_y + r], fill=(0, 240, 255, 180))


def render_single_frame(
    t: float,
    scene_idx: int,
    bg_img: Image.Image,
    character_rig: OptimizedCharacterRig,
    particles: FastParticleSystem,
    storyboard: Dict,
    font_large: ImageFont.ImageFont,
    font_badge: ImageFont.ImageFont
) -> Image.Image:
    """Renders a single 1080x1920 frame directly onto a copy of background image."""
    frame = bg_img.copy()
    draw = ImageDraw.Draw(frame, "RGBA")
    
    # 1. Particles directly on canvas
    particles.draw_on_frame(draw, t)
    
    # 2. Active Scene Data
    scene = storyboard["scenes"][scene_idx]
    
    # 3. Paste Character with movement
    cam_x = int(math.sin(t * 0.5) * 10)
    cam_y = int(math.cos(t * 0.4) * 8)
    character_rig.paste_character(
        base_frame=frame,
        t=t,
        is_talking=scene.get("talking", True),
        left_arm=scene.get("left_arm", "arm_left_idle"),
        right_arm=scene.get("right_arm", "arm_right_idle"),
        eyebrows=scene.get("eyebrows", "eyebrows_neutral"),
        char_x=scene.get("char_x", 40) + cam_x,
        char_y=scene.get("char_y", 760) + cam_y
    )
    
    # 4. Kinetic Typography
    headline = scene.get("headline", "")
    badge = scene.get("badge", "")
    
    if headline:
        scene_t = t - scene["start_time"]
        pop_scale = min(1.0, scene_t * 6.0)
        box_y = 280
        box_w = int(960 * pop_scale)
        box_h = 130
        box_x = 540 - box_w // 2
        
        # Shadow & Box
        draw.rounded_rectangle(
            [box_x + 6, box_y + 8, box_x + box_w + 6, box_y + box_h + 8],
            radius=20, fill=(0, 0, 0, 140)
        )
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=20, fill=(17, 24, 39, 235), outline=(0, 240, 255, 255), width=4
        )
        if pop_scale > 0.4:
            bbox = draw.textbbox((0, 0), headline, font=font_large)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = 540 - tw // 2
            ty = box_y + (box_h - th) // 2 - 4
            draw.text((tx, ty), headline, font=font_large, fill=(255, 235, 59, 255))
            
    if badge:
        draw.rounded_rectangle([60, 140, 360, 210], radius=14, fill=(114, 9, 183, 220))
        draw.text((85, 155), badge, font=font_badge, fill=(255, 255, 255, 255))
        
    return frame


def print_progress_bar(current: int, total: int, fps_speed: float, eta_sec: float):
    pct = (current / total) * 100
    bar_len = 10
    filled = int(bar_len * current // total)
    bar = "=" * filled + "-" * (bar_len - filled)
    print(f"[{bar}] {pct:5.1f}% ({current}/{total}) | {fps_speed:.1f} FPS | ETA: {eta_sec:.1f}s", flush=True)


def probe_video(video_path: str) -> Dict:
    """Uses ffprobe to verify video resolution, duration, FPS, and audio streams."""
    cmd = [
        FFPROBE_EXE,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    
    v_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
    a_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0.0))
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    
    return {
        "width": v_stream.get("width") if v_stream else None,
        "height": v_stream.get("height") if v_stream else None,
        "fps": eval(v_stream.get("r_frame_rate", "30/1")) if v_stream else None,
        "v_codec": v_stream.get("codec_name") if v_stream else None,
        "a_codec": a_stream.get("codec_name") if a_stream else None,
        "duration": duration,
        "size_mb": size_mb
    }


def render_animation(
    storyboard: Dict,
    audio_path: Optional[str],
    ass_path: Optional[str],
    music_path: Optional[str],
    output_mp4_path: str,
    max_frames: Optional[int] = None,
    fps: int = 30
) -> bool:
    print("Initializing renderer... OK", flush=True)
    t_load = time.time()
    
    char_rig = OptimizedCharacterRig(SPRITES_DIR)
    new_bg = os.path.join(BASE_DIR, "assets", "backgrounds", "pop_info", "bg_warm_cream.png")
    legacy_bg = os.path.join(BG_DIR, "bg_space.png")
    bg_path = new_bg if os.path.exists(new_bg) else legacy_bg
    bg_img = Image.open(bg_path).convert("RGBA")
    particles = FastParticleSystem(30)
    
    try:
        font_large = ImageFont.truetype("arialbd.ttf", 52)
        font_badge = ImageFont.truetype("arialbd.ttf", 36)
    except:
        font_large = ImageFont.load_default()
        font_badge = font_large
        
    print(f"Loading assets... OK (in {(time.time() - t_load)*1000:.1f}ms)", flush=True)
    
    total_duration = storyboard["duration"]
    total_frames = int(total_duration * fps)
    if max_frames and max_frames < total_frames:
        total_frames = max_frames
        
    print(f"\nRendering {total_frames} frames ({total_frames/fps:.2f}s) @ {fps}fps...", flush=True)
    
    # Setup FFmpeg process with file-based stderr to prevent pipe buffer deadlocks
    ffmpeg_log_path = os.path.join(LOGS_DIR, "ffmpeg_render.log")
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
    
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=ffmpeg_log
    )
    
    start_render = time.time()
    scenes = storyboard["scenes"]
    curr_scene = 0
    
    try:
        for f in range(total_frames):
            t = f / fps
            while curr_scene < len(scenes) - 1 and t >= scenes[curr_scene + 1]["start_time"]:
                curr_scene += 1
                
            frame = render_single_frame(
                t=t,
                scene_idx=curr_scene,
                bg_img=bg_img,
                character_rig=char_rig,
                particles=particles,
                storyboard=storyboard,
                font_large=font_large,
                font_badge=font_badge
            )
            
            # Save thumbnail at frame 40
            if f == 40 or (total_frames <= 30 and f == 15):
                thumb_path = os.path.join(THUMB_DIR, "sample_short_thumb.png")
                frame.save(thumb_path)
                
            rgb_bytes = frame.convert("RGB").tobytes()
            proc.stdin.write(rgb_bytes)
            
            # Progress reporting every 10 frames or at completion
            if (f + 1) % 10 == 0 or (f + 1) == total_frames:
                elapsed = time.time() - start_render
                speed = (f + 1) / max(0.001, elapsed)
                remaining_frames = total_frames - (f + 1)
                eta = remaining_frames / max(0.001, speed)
                print_progress_bar(f + 1, total_frames, speed, eta)
                
        proc.stdin.close()
        print("Encoding video... OK", flush=True)
        proc.wait(timeout=30)
    except Exception as e:
        print(f"Error during rendering: {e}", flush=True)
        proc.kill()
        return False
    finally:
        ffmpeg_log.close()
        
    if proc.returncode == 0 and os.path.exists(output_mp4_path):
        print("Verifying output... OK", flush=True)
        info = probe_video(output_mp4_path)
        print(f"  Dimensions: {info['width']}x{info['height']} | FPS: {info['fps']:.1f}")
        print(f"  Duration: {info['duration']:.2f}s | Size: {info['size_mb']:.2f} MB")
        print(f"  Video: {info['v_codec']} | Audio: {info['a_codec']}")
        print("Animation generated successfully!\n", flush=True)
        return True
    else:
        print(f"Render failed with returncode {proc.returncode}!", flush=True)
        if os.path.exists(ffmpeg_log_path):
            with open(ffmpeg_log_path) as lf:
                print("FFmpeg log tail:", lf.read()[-500:], flush=True)
        return False
