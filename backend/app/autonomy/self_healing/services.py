"""Adapters to existing services and the local system. The ONLY self-healing module that touches files outside
the autonomy folder, runs a process or uses the network - and only in these bounded, fixed ways:

* read-only file access inside the allowed media roots (``media/`` and ``assets/``), with size limits;
* ``ffprobe`` with a fixed argument vector (no shell, no user-built arguments, 20 s timeout) on a validated path;
* guarded HTTPS (``guarded_open``): one uncached request to a provider's public search endpoint, or one streamed
  download into the self-healing staging folder. Every hop, including redirects, must be an allow-listed host
  that resolves only to public addresses; size, time and content are capped and validated.

There is no generic command execution: never a shell, no ``eval``/``exec``, no argument built from free text.
Tests replace the whole bundle with fakes through ``set_services``.
"""
from __future__ import annotations

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
                "detail": ("held by another process (sharing violation)" if win in (32, 33) else
                           "open refused: held by another process or not permitted (Windows does not say which)"),
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


# -- guarded HTTP (Stage 3.32D audit) ---------------------------------------------------------------------------
# One request at a time, HTTPS only, every hop (including redirects) on an allow-listed host that resolves only to
# public addresses, bounded size and time, no cache, no retries, no credentials. Used by both network actions.
MAX_REDIRECTS = 3
PROVIDER_TIMEOUT = 15.0
PROVIDER_MAX_BYTES = 2 * 1024 * 1024
DOWNLOAD_TIMEOUT = 15.0
DOWNLOAD_DEADLINE = 60.0
DOWNLOAD_MAX_BYTES = 25 * 1024 * 1024
USER_AGENT = "CrazzyDocsStudio/1.0 (self-healing diagnostics)"


class GuardError(Exception):
    """A request was refused or aborted by a guard (destination, size, time or content)."""


def _resolves_public(host: str, resolver: Callable[..., Any]) -> bool:
    import ipaddress
    try:
        infos = resolver(host, 443)
    except OSError:
        return False                                          # cannot resolve: fail closed
    addrs = {i[4][0] for i in infos}
    try:
        return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)
    except ValueError:
        return False


def destination_allowed(url: str, hosts: Sequence[str], resolver: Optional[Callable[..., Any]] = None) -> bool:
    import socket
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme != "https" or p.username or p.password or port not in (None, 443) or not host:
        return False
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return False
    return _resolves_public(host, resolver or socket.getaddrinfo)


def _opener(hosts: Sequence[str], resolver: Optional[Callable[..., Any]]):
    import urllib.request
    from backend.app.media.net import tls_context

    class GuardedRedirect(urllib.request.HTTPRedirectHandler):
        max_redirections = MAX_REDIRECTS

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not destination_allowed(newurl, hosts, resolver):
                raise GuardError(f"redirect to a destination that is not allow-listed refused (HTTP {code})")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=tls_context()), GuardedRedirect())


def guarded_open(url: str, hosts: Sequence[str], timeout: float, *, opener: Any = None,
                 resolver: Optional[Callable[..., Any]] = None):
    import urllib.request
    if not destination_allowed(url, hosts, resolver):
        raise GuardError("destination is not an allow-listed public HTTPS host")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    return (opener or _opener(hosts, resolver)).open(req, timeout=timeout)


def _count_openverse(d):
    return len(d["results"]) if isinstance(d.get("results"), list) else None


def _count_wikimedia(d):
    s = (d.get("query") or {}).get("search")
    return len(s) if isinstance(s, list) else None


def _count_nasa(d):
    s = (d.get("collection") or {}).get("items")
    return len(s) if isinstance(s, list) else None


def _count_ia(d):
    s = (d.get("response") or {}).get("docs")
    return len(s) if isinstance(s, list) else None


# The same public search endpoints the production provider clients use (constants read from those modules).
PROVIDER_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "Openverse": {"module": "openverse_provider", "const": "OPENVERSE_API_URL", "count": _count_openverse,
                  "params": lambda q: {"q": q, "page_size": 1, "license": "cc0,pdm,by", "mature": "false"}},
    "Wikimedia Commons": {"module": "wikimedia_provider", "const": "WIKIMEDIA_API_URL", "count": _count_wikimedia,
                          "params": lambda q: {"action": "query", "list": "search", "srsearch": q, "srnamespace": "6",
                                               "srlimit": 1, "format": "json"}},
    "NASA Image Library": {"module": "nasa_provider", "const": "NASA_API_URL", "count": _count_nasa,
                           "params": lambda q: {"q": q, "media_type": "image", "page_size": 1}},
    "Internet Archive": {"module": "internet_archive_provider", "const": "IA_SEARCH_URL", "count": _count_ia,
                         "params": lambda q: {"q": q, "fl[]": "identifier", "rows": 1, "page": 1, "output": "json"}},
}


def provider_endpoint(provider: str) -> str:
    import importlib
    spec = PROVIDER_ENDPOINTS[provider]
    return getattr(importlib.import_module("backend.app.media.providers." + spec["module"]), spec["const"])


def provider_search(provider: str, query: str, limit: int = 1, *, opener: Any = None,
                    resolver: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Availability check: ONE uncached GET to the provider's public search endpoint (no key, no retry, 15 s, 2 MB).
    Success requires HTTP 200 and JSON with the provider's documented result structure; an error, an empty body or an
    unexpected shape is a failure. The production provider clients are not used here: they serve cached responses
    and turn errors into empty result lists, so they cannot prove that a provider is available."""
    from urllib.parse import urlencode, urlparse
    if provider not in PROVIDER_ENDPOINTS:
        return {"ok": False, "error": "provider not allow-listed"}
    base = provider_endpoint(provider)
    url = base + "?" + urlencode(PROVIDER_ENDPOINTS[provider]["params"](query), doseq=True)
    host = urlparse(base).hostname
    try:
        with guarded_open(url, [host], PROVIDER_TIMEOUT, opener=opener, resolver=resolver) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read(PROVIDER_MAX_BYTES + 1)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "endpoint_host": host}
    if status != 200:
        return {"ok": False, "error": f"HTTP {status}", "endpoint_host": host}
    if len(body) > PROVIDER_MAX_BYTES:
        return {"ok": False, "error": "response larger than 2 MB", "endpoint_host": host}
    try:
        data = json.loads(body.decode("utf-8"))
        count = PROVIDER_ENDPOINTS[provider]["count"](data) if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        data, count = None, None
    if count is None or (isinstance(data, dict) and data.get("error")):
        return {"ok": False, "error": "response did not have the provider's result structure", "endpoint_host": host}
    return {"ok": True, "count": int(count), "endpoint_host": host, "cached": False, "requests": 1}


def download_to_staging(url: str, staging_dir: Path, *, opener: Any = None,
                        resolver: Optional[Callable[..., Any]] = None,
                        clock: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    """Guarded download into the staging folder only. Streamed to a new ``download.part`` (exclusive create, so
    nothing is ever overwritten), capped at 25 MB and 60 s, validated with the production downloader's signature
    check and a Pillow decode, then renamed to ``staged_<sha16>.<ext>`` (rename fails rather than overwrite). Any
    failure deletes the partial file. It never touches a job's media folder."""
    import time as _time
    from backend.app.autonomy.self_healing.planner import ALLOWED_ASSET_HOSTS
    from backend.app.media.downloader import detect_magic_type
    clock = clock or _time.monotonic
    staging_dir.mkdir(parents=True, exist_ok=True)
    part = staging_dir / "download.part"
    h, n, head = hashlib.sha256(), 0, b""
    t0 = clock()
    try:
        with guarded_open(url, ALLOWED_ASSET_HOSTS, DOWNLOAD_TIMEOUT, opener=opener, resolver=resolver) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise GuardError(f"HTTP {status}")
            hdrs = getattr(resp, "headers", None)
            ctype = ((hdrs.get("Content-Type") if hdrs is not None else "") or "").lower()
            if ctype and not any(t in ctype for t in ("image", "video", "octet-stream")):
                raise GuardError(f"content type {ctype[:40]} is not media")
            declared = int((hdrs.get("Content-Length") if hdrs is not None else 0) or 0)
            if declared > DOWNLOAD_MAX_BYTES:
                raise GuardError("declared size exceeds 25 MB")
            with open(part, "xb") as fh:
                while True:
                    if clock() - t0 > DOWNLOAD_DEADLINE:
                        raise GuardError("download exceeded 60 s")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > DOWNLOAD_MAX_BYTES:
                        raise GuardError("download exceeded 25 MB")
                    if len(head) < 16:
                        head += chunk[:16 - len(head)]
                    h.update(chunk)
                    fh.write(chunk)
        mime = detect_magic_type(head)
        if not mime:
            raise GuardError("file signature is not a supported image or video format")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
               "video/webm": "webm", "video/mp4": "mp4"}[mime]
        if mime.startswith("image/"):
            from PIL import Image
            with Image.open(part) as img:
                img.verify()
        final = staging_dir / f"staged_{h.hexdigest()[:16]}.{ext}"
        os.rename(part, final)
        return {"ok": True, "path": str(final), "sha256": h.hexdigest(), "error": None, "mime_type": mime, "bytes": n}
    except Exception as exc:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        return {"ok": False, "path": None, "sha256": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "bytes": n}


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
