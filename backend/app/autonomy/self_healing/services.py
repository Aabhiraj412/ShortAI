"""Adapters to existing services and the local system. The ONLY self-healing module that touches files outside
the autonomy folder, runs a process or uses the network - and only in these bounded, fixed ways:

* read-only file access inside the allowed media roots (``media/`` and ``assets/``), with size limits;
* ``ffprobe`` with a fixed argument vector (no shell, no user-built arguments, 20 s timeout) on a validated path;
* the existing media-provider clients (read-only searches) and the existing ``AssetDownloader`` (downloads only into
  the self-healing staging folder).

There is no generic command execution: never a shell, no ``eval``/``exec``, no argument built from free text.
Tests replace the whole bundle with fakes through ``set_services``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

MAX_LOG_BYTES = 64 * 1024
MAX_PROBE_FILES = 5
FFPROBE_TIMEOUT = 20


def inside(path: Path, roots: Sequence[Path]) -> bool:
    try:
        rp = path.resolve()
    except (OSError, RuntimeError):
        return False
    for r in roots:
        try:
            rp.relative_to(r.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_roots() -> List[Path]:
    from backend.app.core.paths import ASSETS_DIR, MEDIA_DIR
    return [Path(MEDIA_DIR), Path(ASSETS_DIR)]


def read_tail(path: Path, roots: Sequence[Path], max_bytes: int = MAX_LOG_BYTES, max_lines: int = 60) -> Dict[str, Any]:
    if not inside(path, roots):
        return {"available": False, "reason": "path outside the allowed media roots"}
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            data = fh.read(max_bytes).decode("utf-8", "replace")
    except FileNotFoundError:
        return {"available": False, "reason": "file not found"}
    except OSError as exc:
        return {"available": False, "reason": f"{type(exc).__name__}"}
    lines = data.splitlines()[-max_lines:]
    return {"available": True, "bytes": size, "lines": lines}


def probe_lock(path: Path, roots: Sequence[Path]) -> Dict[str, Any]:
    """Opens the file read-only and closes it immediately. A sharing violation means another process holds it."""
    if not inside(path, roots):
        return {"checked": False, "reason": "path outside the allowed media roots"}
    if not path.exists():
        return {"checked": True, "exists": False, "locked": False}
    try:
        with open(path, "rb"):
            pass
        return {"checked": True, "exists": True, "locked": False}
    except PermissionError as exc:
        win = getattr(exc, "winerror", None)
        return {"checked": True, "exists": True, "locked": True,
                "detail": "held by another process (sharing violation)" if win in (32, 33) else "access denied",
                "error": f"PermissionError winerror={win}"}
    except OSError as exc:
        return {"checked": True, "exists": True, "locked": None, "error": type(exc).__name__}


def disk_usage(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        try:
            u = shutil.disk_usage(str(p if p.exists() else p.anchor or p))
            out.append({"path": str(p), "free_gb": round(u.free / 1e9, 2), "total_gb": round(u.total / 1e9, 2),
                        "used_pct": round(100.0 * u.used / u.total, 1) if u.total else None})
        except OSError as exc:
            out.append({"path": str(p), "error": type(exc).__name__})
    return out


def ffprobe(path: Path, roots: Sequence[Path]) -> Dict[str, Any]:
    """Fixed argv; never a shell. Returns the stream summary or {"ok": False, "reason": ...}."""
    if not inside(path, roots) or not path.is_file():
        return {"ok": False, "reason": "file missing or outside the allowed roots"}
    from backend.app.core.paths import get_ffprobe_path
    exe = get_ffprobe_path()
    try:
        proc = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                              capture_output=True, text=True, timeout=FFPROBE_TIMEOUT, shell=False, check=False)
    except FileNotFoundError:
        return {"ok": False, "reason": "ffprobe is not available"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"ffprobe timed out after {FFPROBE_TIMEOUT} s"}
    if proc.returncode != 0:
        return {"ok": False, "reason": (proc.stderr or "ffprobe failed").strip()[:300]}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return {"ok": False, "reason": "ffprobe output was not JSON"}
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") not in (None, "N/A") else None
    except (TypeError, ValueError):
        duration = None
    w, h = (v or {}).get("width"), (v or {}).get("height")
    return {"ok": True, "width": w, "height": h, "codec": (v or {}).get("codec_name"), "pix_fmt": (v or {}).get("pix_fmt"),
            "duration": duration, "format": fmt.get("format_name"), "bytes": path.stat().st_size,
            "odd_dimensions": bool(w and h and (int(w) % 2 or int(h) % 2))}


def image_info(path: Path, roots: Sequence[Path]) -> Dict[str, Any]:
    if not inside(path, roots) or not path.is_file():
        return {"ok": False, "reason": "file missing or outside the allowed roots"}
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            w, h = img.size
            fmt = img.format
        return {"ok": True, "width": w, "height": h, "format": fmt, "bytes": path.stat().st_size}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


PROVIDER_CLASSES = {
    "Openverse": ("backend.app.media.providers.openverse_provider", "OpenverseProvider"),
    "Wikimedia Commons": ("backend.app.media.providers.wikimedia_provider", "WikimediaProvider"),
    "NASA Image Library": ("backend.app.media.providers.nasa_provider", "NASAProvider"),
    "Internet Archive": ("backend.app.media.providers.internet_archive_provider", "InternetArchiveProvider"),
}


def provider_search(provider: str, query: str, limit: int = 3) -> Dict[str, Any]:
    """One read-only search through the existing provider client (no API keys: key-less providers only)."""
    import importlib
    if provider not in PROVIDER_CLASSES:
        return {"ok": False, "error": "provider not allow-listed"}
    mod_name, cls_name = PROVIDER_CLASSES[provider]
    client = getattr(importlib.import_module(mod_name), cls_name)(timeout_sec=12.0)
    try:
        results = asyncio.run(client.search_media(query, limit=limit))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if not isinstance(results, list):
        return {"ok": False, "error": "provider returned no list"}
    return {"ok": True, "count": len(results), "sample": [{"title": str(getattr(r, "title", ""))[:80],
                                                           "repository": getattr(r, "source_repository", None)}
                                                          for r in results[:3]]}


def download_to_staging(url: str, staging_dir: Path) -> Dict[str, Any]:
    """The existing AssetDownloader, pointed at the staging folder. It performs signature and decode validation."""
    from backend.app.media.downloader import AssetDownloader
    from backend.app.media.models import LicenseType, MediaAsset
    staging_dir.mkdir(parents=True, exist_ok=True)
    asset = MediaAsset(asset_id="selfheal_reacquire", title="reacquire", creator=None, source_repository="self-healing",
                       source_url=url, download_url=url, license_type=LicenseType.UNKNOWN,
                       attribution_text="staged copy for diagnosis; not for publication")
    ok, path, digest, err = AssetDownloader(storage_dir=str(staging_dir)).download_asset(asset, custom_filename="staged")
    return {"ok": bool(ok), "path": path, "sha256": digest, "error": err, "mime_type": asset.mime_type,
            "width": asset.width, "height": asset.height, "bytes": asset.file_size_bytes}


@dataclass
class Services:
    roots: Callable[[], List[Path]] = _default_roots
    read_tail: Callable[..., Dict[str, Any]] = read_tail
    probe_lock: Callable[..., Dict[str, Any]] = probe_lock
    disk_usage: Callable[..., List[Dict[str, Any]]] = disk_usage
    ffprobe: Callable[..., Dict[str, Any]] = ffprobe
    image_info: Callable[..., Dict[str, Any]] = image_info
    provider_search: Callable[..., Dict[str, Any]] = provider_search
    download_to_staging: Callable[..., Dict[str, Any]] = download_to_staging
    extra: Dict[str, Any] = field(default_factory=dict)


_services: Optional[Services] = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services()
    return _services


def set_services(services: Optional[Services]) -> None:
    """Test hook: replace every adapter (None restores the defaults)."""
    global _services
    _services = services
