"""Community post images: local, zero cost, licence-verified.

Priority:
1. Reuse a licensed image already acquired for the source production. An asset qualifies only when its file's
   SHA-256 matches a ``verified`` entry of that production's attribution manifest whose licence is in
   ``ACCEPTED_LICENCES`` and permits commercial use and modification. It is cover-cropped to 1080 x 1080 (1:1,
   the Community post format) with no text added.
2. Otherwise a typographic title card rendered locally with Pillow: the topic title and category from the research
   dossier on a plain gradient (no third-party imagery, so no licence is needed).

No network access, no paid API, no GPU model and no use of the video renderer. Files are written with exclusive
create into ``<community data>/images/<draft id>/`` and renamed into place; nothing is ever overwritten.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SIZE = 1080
JPEG_QUALITY = 88
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MIN_SOURCE_SIDE = 400
MAX_SOURCE_PIXELS = 60_000_000
MAX_CANDIDATE_FILES = 40
ATTRIBUTION_REQUIRED = ("CC BY",)

# Licences accepted for reuse in a Community image (commercial use and modification allowed). Share-alike, NC and
# ND licences are excluded.
ACCEPTED_LICENCES = {
    "CC0": "CC0", "PDM": "Public Domain", "PUBLIC DOMAIN": "Public Domain", "PUBLIC DOMAIN MARK": "Public Domain",
    "NO KNOWN COPYRIGHT RESTRICTIONS": "Public Domain", "NASA": "Public Domain",
    "CC BY": "CC BY", "CC BY 2.0": "CC BY", "CC BY 3.0": "CC BY", "CC BY 4.0": "CC BY", "BY": "CC BY",
}
FONT_CANDIDATES = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_REGULAR = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


class ImageError(Exception):
    """The image could not be produced; the draft is marked IMAGE_FAILED (never a silent unrelated image)."""


def normalise_licence(value: Any) -> Optional[str]:
    v = re.sub(r"[\s_\-]+", " ", str(value or "")).strip().upper()
    v = v.replace("CREATIVE COMMONS ", "CC ").replace("ATTRIBUTION", "BY")
    if v in ACCEPTED_LICENCES:
        return ACCEPTED_LICENCES[v]
    if re.fullmatch(r"CC BY \d\.\d( [A-Z]+)?", v) and not re.search(r"\b(SA|NC|ND)\b", v):
        return "CC BY"
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def dhash(img) -> str:
    """64-bit difference hash (hex) for near-duplicate detection."""
    from PIL import Image
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.tobytes())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def hamming(a: Optional[str], b: Optional[str]) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _short(value: Any, n: int) -> str:
    """Manifest text for a credit line: HTML entities decoded, whitespace collapsed, cut on a word boundary."""
    import html
    t = re.sub(r"\s+", " ", html.unescape(html.unescape(str(value or "")))).strip()
    t = re.sub(r"[\x00-\x1f\x7f]", "", t)
    if len(t) <= n:
        return t
    return (t[:n].rsplit(" ", 1)[0].rstrip(",;:-.") or t[:n]) + "…"


# -- candidates --------------------------------------------------------------------------------------------------
def licensed_candidates(media_dir: Path, job_id: str) -> List[Dict[str, Any]]:
    """Licensed images acquired for a production, matched to their attribution manifest entry by SHA-256."""
    if not SAFE_ID.match(job_id or ""):
        return []
    base = Path(media_dir) / "productions" / job_id
    manifest_path = base / "metadata" / f"attribution_manifest_{job_id}.json"
    try:
        if not manifest_path.is_file() or manifest_path.stat().st_size > 2 * 1024 * 1024:
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries: Dict[str, Dict[str, Any]] = {}
    for item in (manifest.get("items") or []) if isinstance(manifest, dict) else []:
        if not isinstance(item, dict) or item.get("asset_type") != "image" or item.get("verified") is not True:
            continue
        lic = normalise_licence(item.get("license"))
        restrictions = str(item.get("usage_restrictions") or "").lower()
        if not lic or item.get("modification_permitted") is False or "non-commercial" in restrictions \
                or "noncommercial" in restrictions:
            continue
        sha = str(item.get("sha256") or "").lower()
        if re.fullmatch(r"[0-9a-f]{64}", sha):
            entries[sha] = {**item, "licence": lic}
    if not entries:
        return []
    folder = base / "assets" / "acquired"
    out: List[Dict[str, Any]] = []
    if not folder.is_dir():
        return out
    files = sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                    and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")), key=lambda p: p.name)
    for p in files[:MAX_CANDIDATE_FILES]:
        try:
            if p.stat().st_size > MAX_SOURCE_BYTES or not inside(p, folder):
                continue
            sha = sha256_file(p)
        except OSError:
            continue
        item = entries.get(sha)
        if not item:
            continue
        creator = _short(item.get("creator"), 60)
        title = _short(item.get("title"), 80)
        out.append({
            "choice": "asset:" + sha[:16], "path": p, "sha256": sha, "licence": item["licence"],
            "licence_url": str(item.get("license_url") or "")[:300], "creator": creator, "title": title,
            "source_url": str(item.get("source_url") or "")[:500],
            "repository": str(item.get("source_repository") or "")[:80],
            "asset_id": str(item.get("asset_id") or "")[:120], "original_filename": p.name,
            "attribution_required": item["licence"] in ATTRIBUTION_REQUIRED,
            "attribution": (f"Image: {title or 'untitled'} by {creator or 'unknown creator'} ({item['licence']}"
                            + (f", via {_short(item.get('source_repository'), 30)}" if item.get("source_repository") else "")
                            + ")" if item["licence"] in ATTRIBUTION_REQUIRED else ""),
        })
    return out


# -- rendering ---------------------------------------------------------------------------------------------------
def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for path in (FONT_CANDIDATES if bold else FONT_REGULAR):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _save_new(img, out_dir: Path, stem: str) -> Dict[str, Any]:
    """Exclusive-create a temp file in ``out_dir`` and rename it to ``<stem>.jpg``; never overwrites."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".{stem}.part"
    final = out_dir / f"{stem}.jpg"
    try:
        with open(tmp, "xb") as fh:
            img.save(fh, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        os.rename(tmp, final)                    # fails rather than replacing an existing file
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return {"path": final, "filename": final.name, "sha256": sha256_file(final), "bytes": final.stat().st_size,
            "width": img.width, "height": img.height, "mime": "image/jpeg", "dhash": dhash(img)}


def render_asset_card(src: Path, out_dir: Path, stem: str) -> Dict[str, Any]:
    from PIL import Image, ImageOps
    # Pillow's global MAX_IMAGE_PIXELS is deliberately left alone (the production renderer shares the process);
    # the pixel limit is checked per image before any pixel data is decoded.
    try:
        with Image.open(src) as im:
            if im.width * im.height > MAX_SOURCE_PIXELS:
                raise ImageError(f"source image too large ({im.width}x{im.height})")
            im.verify()
        with Image.open(src) as im:
            if min(im.size) < MIN_SOURCE_SIDE:
                raise ImageError(f"source image too small ({im.width}x{im.height}); at least {MIN_SOURCE_SIDE} px needed")
            im = ImageOps.exif_transpose(im).convert("RGB")
            card = ImageOps.fit(im, (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.45))
    except ImageError:
        raise
    except Exception as exc:
        raise ImageError(f"source image unreadable: {type(exc).__name__}") from exc
    return _save_new(card, out_dir, stem)


def _palette(seed: str):
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    top = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h, 0.22, 0.55))
    bottom = tuple(int(c * 255) for c in colorsys.hls_to_rgb((h + 0.08) % 1, 0.10, 0.50))
    accent = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h, 0.65, 0.75))
    return top, bottom, accent


def render_title_card(title: str, category: str, out_dir: Path, stem: str) -> Dict[str, Any]:
    from PIL import Image, ImageDraw
    top, bottom, accent = _palette(category or title)
    img = Image.new("RGB", (SIZE, SIZE), top)
    draw = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    margin = 96
    draw.rectangle([margin, 210, margin + 120, 222], fill=accent)
    cat = (category or "Science").upper()[:40]
    draw.text((margin, 140), cat, font=_font(38, bold=False), fill=accent)
    clean = re.sub(r"\s+", " ", title or "").strip()[:140] or "CrazzyDocs"
    for size in (84, 74, 64, 56, 48):
        font = _font(size)
        width_chars = max(10, int((SIZE - 2 * margin) / (size * 0.52)))
        lines = textwrap.wrap(clean, width=width_chars)[:6]
        if len(lines) * size * 1.2 <= 560 and all(draw.textlength(ln, font=font) <= SIZE - 2 * margin for ln in lines):
            break
    y = 280
    for ln in lines:
        draw.text((margin, y), ln, font=font, fill=(245, 245, 245))
        y += int(size * 1.2)
    draw.text((margin, SIZE - 140), "CrazzyDocs", font=_font(40), fill=(235, 235, 235))
    return _save_new(img, out_dir, stem)


def image_stats(path: Path) -> Dict[str, Any]:
    """Decoded properties used by validation. Raises ImageError when the file cannot be decoded."""
    from PIL import Image, ImageStat
    try:
        with Image.open(path) as im:
            fmt = im.format
            im.verify()
        with Image.open(path) as im:
            im.load()
            rgb = im.convert("RGB")
            stddev = sum(ImageStat.Stat(rgb.convert("L")).stddev) if rgb.width else 0.0
            return {"format": fmt, "width": im.width, "height": im.height, "stddev": round(stddev, 2),
                    "dhash": dhash(rgb)}
    except Exception as exc:
        raise ImageError(f"{type(exc).__name__}: {str(exc)[:120]}") from exc
