"""
CrazzyDocs Branded Outro Generator (Stage 3.1 - Icon-Only Specification).

Produces a vertical 1080x1920 (9:16) 30 FPS branded outro clip using ONLY the
official circular CrazzyDocs icon.
Guarantees:
- Exactly 2.0 second duration (60 frames at 30 FPS).
- Pure Icon-Only: ZERO banner artwork, ZERO channel title text, ZERO tagline,
  ZERO subscribe pills or buttons, ZERO subtitle overlays.
- Premium dark studio background with subtle central radial luminance.
- Smooth spring entrance easing and organic idle pulse.
- High-fidelity synchronized audio chime.
"""

import math
import os
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFilter

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
FFMPEG_EXE = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffmpeg.exe")
SRC_DIR = os.path.join(WORKSPACE, "Youtube Channel Src")
BRANDING_DIR = os.path.join(WORKSPACE, "assets", "branding", "crazzydocs")
PREVIEWS_DIR = os.path.join(WORKSPACE, "docs", "previews")
SFX_PATH = os.path.join(WORKSPACE, "assets", "audio", "sfx", "chime_pop.wav")

os.makedirs(BRANDING_DIR, exist_ok=True)
os.makedirs(PREVIEWS_DIR, exist_ok=True)


def prepare_dark_studio_plate(width: int = 1080, height: int = 1920) -> Image.Image:
    """
    Creates an ultra-clean, elegant dark studio canvas with subtle radial depth.
    Does NOT use YouTube banner artwork.
    """
    # Base deep slate
    bg = Image.new("RGB", (width, height), (10, 14, 23))
    draw = ImageDraw.Draw(bg)

    # Subtle radial luminance centered behind the icon (center_x = 540, center_y = 960)
    cx, cy = width // 2, height // 2
    max_radius = int(math.hypot(cx, cy))
    
    # Render radial gradient in steps for high performance
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d_overlay = ImageDraw.Draw(overlay)

    # 12-layer soft concentric rings creating a gentle ambient spotlight
    for r in range(500, 40, -40):
        intensity = int(28 * (1.0 - (r / 500.0) ** 1.5))
        d_overlay.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(26, 42, 68, intensity)
        )

    # Soft blur to create perfectly smooth light falloff
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=30))
    bg.paste(overlay, (0, 0), overlay)

    return bg


AUTHORITATIVE_ICON_PATH = os.path.join(SRC_DIR, "Youtube-Icon.png")
AUTHORITATIVE_ICON_SHA256 = "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060"


def prepare_circular_icon(size: int = 420) -> Image.Image:
    """
    Prepares the pristine circular CrazzyDocs icon with an elegant gold & cyan accent ring.
    Strictly uses the authoritative channel logo from Youtube Channel Src/Youtube-Icon.png.
    Preserves exact channel logo artwork with zero geometric distortion.
    """
    if not os.path.exists(AUTHORITATIVE_ICON_PATH):
        raise FileNotFoundError(f"Authoritative CrazzyDocs channel icon missing at: {AUTHORITATIVE_ICON_PATH}")

    # Compute SHA-256 to ensure zero tampering or synthetic substitution
    import hashlib
    h = hashlib.sha256()
    with open(AUTHORITATIVE_ICON_PATH, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    actual_hash = h.hexdigest()

    if actual_hash != AUTHORITATIVE_ICON_SHA256:
        raise ValueError(
            f"Branding validation failed: {AUTHORITATIVE_ICON_PATH} SHA-256 ({actual_hash}) "
            f"does not match authoritative hash ({AUTHORITATIVE_ICON_SHA256})"
        )

    orig = Image.open(AUTHORITATIVE_ICON_PATH).convert("RGBA")
    icon = orig.resize((size, size), Image.Resampling.LANCZOS)

    # Crisp circular mask with 1-pixel feathering to eliminate aliasing
    mask = Image.new("L", (size, size), 0)
    draw_m = ImageDraw.Draw(mask)
    draw_m.ellipse([2, 2, size - 2, size - 2], fill=255)
    icon.putalpha(mask)

    # Add outer glowing accent ring (Gold & Teal brand colors)
    ring_padding = 24
    framed_size = size + ring_padding * 2
    framed = Image.new("RGBA", (framed_size, framed_size), (0, 0, 0, 0))
    fd = ImageDraw.Draw(framed)

    # Outer subtle gold ring
    fd.ellipse(
        [ring_padding - 6, ring_padding - 6, framed_size - (ring_padding - 6), framed_size - (ring_padding - 6)],
        outline=(254, 224, 4, 190),
        width=4
    )
    # Inner cyan/teal ring
    fd.ellipse(
        [ring_padding - 2, ring_padding - 2, framed_size - (ring_padding - 2), framed_size - (ring_padding - 2)],
        outline=(47, 111, 115, 230),
        width=3
    )

    offset = ring_padding
    framed.paste(icon, (offset, offset), icon)
    return framed


def render_icon_only_frame(
    bg_plate: Image.Image,
    logo_img: Image.Image,
    frame_idx: int,
    total_frames: int = 60,
    width: int = 1080,
    height: int = 1920
) -> Image.Image:
    """
    Renders a single frame of the Icon-Only CrazzyDocs outro.
    NO text, NO banners, NO buttons, NO subtitles.
    """
    frame = bg_plate.copy()

    # 1. Spring Entrance Animation (Frames 0 to 18: 0.0s to 0.6s)
    # Overshoot spring curve: scales from 0.60 to 1.05 and settles at 1.00
    if frame_idx <= 18:
        p = frame_idx / 18.0
        # Damped harmonic spring curve: 1.0 - e^(-3p) * cos(4.5p)
        spring = 1.0 - math.exp(-3.5 * p) * math.cos(4.2 * p)
        scale_factor = 0.55 + 0.45 * spring
    else:
        # Subtle gentle float / breathing (Frames 19 to 59)
        t_float = (frame_idx - 18) * 0.12
        scale_factor = 1.0 + 0.018 * math.sin(t_float)

    lw, lh = logo_img.size
    cur_lw = max(10, int(lw * scale_factor))
    cur_lh = max(10, int(lh * scale_factor))
    scaled_logo = logo_img.resize((cur_lw, cur_lh), Image.Resampling.BILINEAR)

    # Position: Exactly centered on 1080x1920
    cx = width // 2
    cy = height // 2
    frame.paste(scaled_logo, (cx - cur_lw // 2, cy - cur_lh // 2), scaled_logo)

    # 2. Fade In (first 6 frames = 0.2s) and Fade to Black (last 6 frames = 0.2s)
    fade_in_frames = 6
    fade_out_frames = 6

    if frame_idx < fade_in_frames:
        fade = 1.0 - (frame_idx / float(fade_in_frames))
        dimmer = Image.new("RGBA", (width, height), (0, 0, 0, int(fade * 255)))
        frame.paste(dimmer, (0, 0), dimmer)
    elif frame_idx >= (total_frames - fade_out_frames):
        fade = (frame_idx - (total_frames - fade_out_frames)) / float(fade_out_frames)
        dimmer = Image.new("RGBA", (width, height), (0, 0, 0, int(fade * 255)))
        frame.paste(dimmer, (0, 0), dimmer)

    return frame


def generate_crazzydocs_outro(
    output_mp4_path: str = None,
    duration: float = 2.0,
    fps: int = 30
) -> str:
    """
    Renders the official CrazzyDocs Icon-Only 1080x1920 MP4 outro video.
    Guarantees:
    - Duration: Exactly 2.0 seconds (60 frames at 30 FPS).
    - Audio: 48kHz stereo AAC with chime pop synchronized to icon entrance.
    - Zero text, zero banners, zero subtitles.
    """
    if output_mp4_path is None:
        output_mp4_path = os.path.join(BRANDING_DIR, "crazzydocs_outro_1080x1920.mp4")

    total_frames = int(round(duration * fps)) # Exactly 60 frames for 2.0s @ 30 FPS
    temp_frames_dir = os.path.join(BRANDING_DIR, "_tmp_outro_frames")
    os.makedirs(temp_frames_dir, exist_ok=True)

    print(f"[CRAZZYDOCS OUTRO] Generating {total_frames} frames (1080x1920 @ {fps} FPS, Icon-Only)...")
    bg_plate = prepare_dark_studio_plate(1080, 1920)
    logo_img = prepare_circular_icon(size=420)

    # Save preview image of peak outro frame (frame 30 = 1.0s mark)
    preview_frame = render_icon_only_frame(bg_plate, logo_img, 30, total_frames)
    preview_path = os.path.join(PREVIEWS_DIR, "crazzydocs_outro_preview.png")
    preview_frame.save(preview_path, "PNG")
    print(f"  -> Saved clean preview: {preview_path}")

    # Render all 60 frames
    for f in range(total_frames):
        img = render_icon_only_frame(bg_plate, logo_img, f, total_frames)
        frame_file = os.path.join(temp_frames_dir, f"frame_{f:04d}.png")
        img.save(frame_file, "PNG")

    # Render Video via FFmpeg
    raw_video = os.path.join(BRANDING_DIR, "_tmp_raw_outro.mp4")
    cmd_video = [
        FFMPEG_EXE, "-y",
        "-r", str(fps),
        "-i", os.path.join(temp_frames_dir, "frame_%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-preset", "fast",
        "-video_track_timescale", "30000",
        raw_video
    ]
    subprocess.run(cmd_video, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # Combine with Audio Chime (duration clamped strictly to 2.0s)
    if os.path.exists(SFX_PATH):
        cmd_mux = [
            FFMPEG_EXE, "-y",
            "-i", raw_video,
            "-i", SFX_PATH,
            "-filter_complex", f"[1:a]afade=t=in:ss=0:d=0.1,afade=t=out:st={duration-0.4:.2f}:d=0.4,apad[a]",
            "-map", "0:v",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            "-t", f"{duration:.3f}",
            output_mp4_path
        ]
        subprocess.run(cmd_mux, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        # Synthesize clean 2.0s silent stereo audio
        cmd_mux = [
            FFMPEG_EXE, "-y",
            "-i", raw_video,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "320k",
            "-t", f"{duration:.3f}",
            output_mp4_path
        ]
        subprocess.run(cmd_mux, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # Cleanup temp frames
    shutil.rmtree(temp_frames_dir, ignore_errors=True)
    if os.path.exists(raw_video):
        os.remove(raw_video)

    print(f"[CRAZZYDOCS OUTRO] Icon-Only Outro successfully rendered to: {output_mp4_path}")
    return output_mp4_path


if __name__ == "__main__":
    generate_crazzydocs_outro()
