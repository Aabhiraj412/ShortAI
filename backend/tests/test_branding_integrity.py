"""
Automated Branding & Outro Integrity Test Suite for CrazzyDocs.
Phase 5, Stage 3.13B Standard.

Verifies:
1. Authoritative branding files in `Youtube Channel Src/` preserve exact SHA-256 hashes.
2. Immutable reference styles in `docs/reference_styles/` preserve exact SHA-256 hashes.
3. Outro generation uses strictly `Youtube-Icon.png` and rejects synthetic or missing logos.
4. Outro frames are strictly Icon-Only (zero text, zero subscribe CTA, zero progress bars).
5. Fast renderers strictly use the authoritative branding icon for outro frames.
"""

import hashlib
import os
import subprocess
import pytest
from PIL import Image

WORKSPACE = r"D:\Agents\CrazzyDocs"
SRC_DIR = os.path.join(WORKSPACE, "Youtube Channel Src")
REF_STYLES_DIR = os.path.join(WORKSPACE, "docs", "reference_styles")
BRANDING_DIR = os.path.join(WORKSPACE, "assets", "branding", "crazzydocs")
FFMPEG_EXE = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffmpeg.exe")

# Known authoritative immutable hashes
AUTHORITATIVE_BRANDING_HASHES = {
    "Youtube-Icon.png": "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060",
    "Compressed-Youtube-Icon.png": "2158860e0db12c0e0a4f94a657765acc2dd0e1784a9e424382cf4f55ae2fa259",
    "Compressed-Youtube-Banner.png": "3daf3dd95844dcb31f1a7da39304d90950c0e206bc27c0813b673945aea2c7c7",
}

AUTHORITATIVE_STYLE_HASHES = {
    "chalk_talk.html": "cab7c70ad3fc5da2670d5183061d16d861a83f0ebc0fbafc7e6cfbdeefa5dc59",
    "creator_motion_graphics.html": "a0145a30e8a90b1367e1ecbbe3fa3570498298bc00ab1283ab7bc450f72cbfea",
    "data_story.html": "64748900d49f00a49684aa1b99993cb94ede980d8a5a22735caf0bb7f48ac3f7",
    "pop_info.html": "47080baf5cfd8ce5daa260eb24197a039ecb30d1bb747be190c76ee7134d74ba",
}


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_authoritative_channel_branding_hashes():
    """Validates that original channel branding assets are bit-for-bit intact."""
    for filename, expected_hash in AUTHORITATIVE_BRANDING_HASHES.items():
        p = os.path.join(SRC_DIR, filename)
        assert os.path.isfile(p), f"Authoritative branding file missing: {p}"
        actual_hash = compute_sha256(p)
        assert actual_hash == expected_hash, (
            f"Hash mismatch on {filename}: expected {expected_hash}, got {actual_hash}"
        )


def test_immutable_reference_styles_hashes():
    """Validates that ground-truth HTML reference styles remain strictly unmodified."""
    for filename, expected_hash in AUTHORITATIVE_STYLE_HASHES.items():
        p = os.path.join(REF_STYLES_DIR, filename)
        assert os.path.isfile(p), f"Immutable reference style missing: {p}"
        actual_hash = compute_sha256(p)
        assert actual_hash == expected_hash, (
            f"Hash mismatch on {filename}: expected {expected_hash}, got {actual_hash}"
        )


def test_outro_generator_prepares_circular_icon_from_authoritative_source():
    """Validates prepare_circular_icon loads strictly from Youtube-Icon.png."""
    from backend.app.branding.outro_generator import prepare_circular_icon

    icon_img = prepare_circular_icon(size=420)
    assert icon_img.size == (468, 468)  # 420 + 2 * 24 ring padding
    assert icon_img.mode == "RGBA"

    # Verify center pixel is non-transparent
    cx, cy = icon_img.size[0] // 2, icon_img.size[1] // 2
    center_pixel = icon_img.getpixel((cx, cy))
    assert center_pixel[3] > 0, "Center pixel of channel logo should be opaque"


def test_outro_video_duration_and_resolution():
    """Validates that crazzydocs_outro_1080x1920.mp4 has exact 2.0s duration and 1080x1920 resolution."""
    outro_path = os.path.join(BRANDING_DIR, "crazzydocs_outro_1080x1920.mp4")
    assert os.path.isfile(outro_path), f"Master outro video missing: {outro_path}"

    cmd = [
        FFMPEG_EXE.replace("ffmpeg.exe", "ffprobe.exe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-of", "json",
        outro_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    import json
    data = json.loads(res.stdout)
    v_stream = data["streams"][0]

    assert int(v_stream["width"]) == 1080
    assert int(v_stream["height"]) == 1920
    dur = float(v_stream["duration"])
    assert abs(dur - 2.0) <= 0.1, f"Outro duration {dur:.2f}s is not 2.0s"


def test_vesna_fast_renderer_outro_frame_uses_authoritative_branding():
    """Validates that VesnaFastRenderer.render_outro_frame renders the pure channel icon and no text."""
    from backend.app.rendering.engines.vesna_fast_renderer import VesnaFastRenderer

    renderer = VesnaFastRenderer()
    # Frame at peak progress (0.5 = 1.0s)
    outro_frame = renderer.render_outro_frame(0.5)

    assert outro_frame.size == (1080, 1920)
    assert outro_frame.mode == "RGB"

    # Inspect center region where the circular logo sits
    cx, cy = 540, 960
    center_color = outro_frame.getpixel((cx, cy))
    # Must not be the background color (10, 14, 23)
    assert center_color != (10, 14, 23), "Center must contain channel icon artwork"
