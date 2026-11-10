"""
Robust Media Asset Downloader.

Features:
- Streaming downloads with progress and safe timeouts
- Magic bytes / file signature validation (JPEG, PNG, WebP)
- Corrupt image detection via Pillow verification
- Maximum file size guard (25 MB)
- SHA-256 hash deduplication
- Safe filesystem sanitization (anti-path traversal)
- Decompression bomb protection
"""

import hashlib
import io
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

from PIL import Image

from backend.app.media.models import MediaAsset

logger = logging.getLogger(__name__)

# Prevent decompression bomb vulnerabilities
Image.MAX_IMAGE_PIXELS = 80_000_000

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
DOWNLOAD_TIMEOUT_SEC = 10.0
MAX_RETRIES = 2

# Supported magic bytes signatures
FILE_SIGNATURES = {
    "jpeg": b"\xFF\xD8\xFF",
    "png": b"\x89PNG\r\n\x1a\n",
    "webp_riff": b"RIFF",
    "webp_tag": b"WEBP"
}


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Produces a clean, safe filename free of path traversal sequences."""
    clean = re.sub(r"[^\w\-.]", "_", name)
    clean = re.sub(r"__+", "_", clean).strip("_.")
    return clean[:max_length] if clean else "asset"


def detect_magic_type(header: bytes) -> Optional[str]:
    """Inspects the leading bytes of a buffer to verify genuine image type."""
    if len(header) < 12:
        return None
    if header.startswith(FILE_SIGNATURES["jpeg"]):
        return "image/jpeg"
    if header.startswith(FILE_SIGNATURES["png"]):
        return "image/png"
    if header.startswith(FILE_SIGNATURES["webp_riff"]) and header[8:12] == FILE_SIGNATURES["webp_tag"]:
        return "image/webp"
    return None


class AssetDownloader:
    """Downloads and verifies images securely into the local media library."""

    def __init__(self, storage_dir: str = r"D:\Agents\CrazzyDocs\assets\media_library\images"):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def download_asset(
        self,
        asset: MediaAsset,
        custom_filename: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Downloads the media asset with signature validation and integrity checks.
        Returns:
            (success: bool, local_path: Optional[str], sha256_hash: Optional[str], error_message: Optional[str])
        """
        url = asset.download_url
        if not url:
            return False, None, None, "Missing download URL."

        safe_slug = sanitize_filename(custom_filename or f"{asset.source_repository}_{asset.title}")
        temp_path = os.path.join(self.storage_dir, f"temp_{safe_slug}_{int(time.time() * 1000)}.tmp")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIYouTubeShortsStudio/1.0"
            }
        )

        hasher = hashlib.sha256()
        total_downloaded = 0
        header_bytes = b""

        for attempt in range(MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp:
                    # Validate Content-Type header if present
                    ctype = resp.headers.get("Content-Type", "").lower()
                    if ctype and not any(t in ctype for t in ("image", "octet-stream")):
                        return False, None, None, f"Rejected: HTTP Content-Type is '{ctype}', not an image."

                    with open(temp_path, "wb") as f:
                        while True:
                            chunk = resp.read(65536)  # 64 KB chunks
                            if not chunk:
                                break
                            if not header_bytes:
                                header_bytes = chunk[:16]
                            total_downloaded += len(chunk)
                            if total_downloaded > MAX_FILE_SIZE_BYTES:
                                if os.path.exists(temp_path):
                                    os.remove(temp_path)
                                return False, None, None, f"Rejected: File size exceeded maximum limit ({total_downloaded / (1024*1024):.1f} MB > 25 MB)."
                            hasher.update(chunk)
                            f.write(chunk)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                if attempt == MAX_RETRIES:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return False, None, None, f"Download failed after {MAX_RETRIES+1} attempts: {e}"
                time.sleep(0.5 * (2 ** attempt))

        # 1. Magic bytes verification
        detected_mime = detect_magic_type(header_bytes)
        if not detected_mime:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False, None, None, "Rejected: File header failed magic bytes signature validation (corrupt or unsupported format)."

        # 2. Corrupt image and decompression bomb check via Pillow
        try:
            with Image.open(temp_path) as img:
                img.verify()  # Verifies file integrity without full decode
                width, height = img.size
                img_format = img.format.lower() if img.format else "jpeg"
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False, None, None, f"Rejected: Image decode/integrity check failed: {e}"

        # 3. Finalize path with extension and SHA-256 hash
        file_hash = hasher.hexdigest()
        ext = "jpg" if img_format == "jpeg" else img_format
        final_filename = f"{safe_slug}_{file_hash[:10]}.{ext}"
        final_path = os.path.join(self.storage_dir, final_filename)

        if os.path.exists(final_path):
            # Duplicate file already exists on disk
            if os.path.exists(temp_path):
                os.remove(temp_path)
        else:
            os.replace(temp_path, final_path)

        # Update asset metadata
        asset.local_path = final_path
        asset.asset_hash = file_hash
        asset.width = width
        asset.height = height
        asset.file_size_bytes = total_downloaded
        asset.mime_type = detected_mime

        logger.info("Successfully downloaded and validated asset: %s (%dx%d, %.1f KB)", final_path, width, height, total_downloaded / 1024)
        return True, final_path, file_hash, None
