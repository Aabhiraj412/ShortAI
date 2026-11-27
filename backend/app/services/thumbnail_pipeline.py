"""
YouTube thumbnail pipeline (Stage 3.29).

Findings (2026-11-19, read-only checks of 27 published videos on i.ytimg.com):
* every `thumbnails().set` call had succeeded, and YouTube's 16:9 thumbnails (mq/hq/sd/maxresdefault)
  were our images - but pillarboxed: the only artifact uploaded was the 1080x1920 portrait, so the
  headline sat small in a narrow centre column of a 16:9 slot;
* the portrait PNGs were 1.0-3.5 MB (YouTube documents a 2 MB thumbnail limit);
* "verification" was `bool(thumb_uploaded and (thumbs or True))` - always true once the call returned;
  thumbnail state was never persisted, so the dashboard could not show it;
* the Shorts feed uses the `oar2` (original aspect ratio) image, which is a video frame chosen by
  YouTube; the Data API cannot set it (documented limitation, not something an upload can fix).

Shorts follow-up (2026-11-27, measured on a live Short with the Data API):
* ``thumbnails.set`` accepts a 9:16 image, but YouTube stores it into the 16:9 sizes (pillarboxed) and leaves the
  Shorts-feed ``oar2`` frame unchanged, so uploading the raw portrait only degrades the 16:9 surfaces;
* when the thumbnail is set AFTER processing SUCCEEDED, YouTube adds a ``shortsAspectRatio`` variant
  (``sardefault.jpg``, 405x720) that is the centre 9:16 crop of the uploaded 16:9 image. Thumbnails set right after
  ``videos.insert`` (the old flow) never got it;
* so a Short's upload artifact is a 1280x720 image whose centre 405x720 column is exactly the 1080x1920 portrait
  design (blurred fill beside it), set only after processing SUCCEEDED and verified through ``shortsAspectRatio``.
  Long-form keeps the 16:9 composition. The ``oar2`` frame itself cannot be set through the API.

This module makes the upload artifact a real 1280x720 composition (subject image filling the frame,
short headline large on a readable panel), validates it before upload, verifies the remote image
after upload by comparing it with what was sent, and persists the state per ledger row.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

logger = logging.getLogger(__name__)

W, H = 1280, 720
MAX_BYTES = 2 * 1024 * 1024
YOUTUBE_SUFFIX = "_youtube_1280x720.jpg"
FONT_CANDIDATES = ("impact.ttf", "seguibl.ttf", "arialbd.ttf")
YELLOW, WHITE = (255, 216, 107), (255, 255, 255)
SHORTS_SUFFIX = "_shorts_1280x720.jpg"
SHORT, LONG = "SHORT", "LONG"
SAR_W = 405                                       # shortsAspectRatio: the centre 405x720 of the 16:9 image


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover(img: Image.Image, w: int, h: int, focus_y: float = 0.42) -> Image.Image:
    sw, sh = img.size
    s = max(w / sw, h / sh)
    nw, nh = int(sw * s + 0.5), int(sh * s + 0.5)
    r = img.resize((nw, nh), Image.LANCZOS)
    x = (nw - w) // 2
    y = int(max(0, min(nh - h, nh * focus_y - h / 2)))
    return r.crop((x, y, x + w, y + h))


def _layout_headline(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int):
    """Largest size (150 -> 64) at which the headline fits in <= 3 lines, measured; never clipped."""
    words = (text or "").upper().split()
    for size in range(150, 62, -4):
        font = _font(size)
        lines: List[str] = []
        cur = ""
        for wd in words:
            nxt = f"{cur} {wd}".strip()
            if draw.textlength(nxt, font=font) <= max_w:
                cur = nxt
            else:
                if cur:
                    lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        line_h = int(size * 1.05)
        if (len(lines) <= 3 and all(draw.textlength(l, font=font) <= max_w for l in lines)
                and line_h * len(lines) <= max_h):
            return font, lines, line_h
    font = _font(64)
    return font, [" ".join(words)[:28]], 68


def compose_youtube_thumbnail(out_path: str, *, headline: str = "", source_image: Optional[str] = None,
                              portrait_thumbnail: Optional[str] = None, brand: str = "CRAZZYDOCS") -> str:
    """1280x720 JPEG. With a source image and headline: full-bleed subject, headline panel on the left.
    With only a finished portrait thumbnail: blurred full-bleed fill plus the portrait at full height
    (its own headline stays readable), never a crop that cuts its text."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = None
    for cand in (source_image, None if source_image else portrait_thumbnail):
        if cand and os.path.isfile(cand):
            try:
                src = Image.open(cand).convert("RGB")
                break
            except OSError:
                src = None
    if headline and src is not None:
        canvas = _cover(src, W, H).filter(ImageFilter.UnsharpMask(2, 60, 3))
        shade = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(shade)
        for x in range(W):                       # left panel for the headline, image stays clear on the right
            a = 225 if x < 560 else max(0, int(225 * (1 - (x - 560) / 300)))
            sd.line([(x, 0), (x, H)], fill=a)
        canvas = Image.composite(Image.new("RGB", (W, H), (10, 14, 22)), canvas, shade)
        d = ImageDraw.Draw(canvas)
        font, lines, lh = _layout_headline(d, headline, 690, 500)
        y = (H - lh * len(lines)) // 2 + 20
        for i, line in enumerate(lines):
            col = YELLOW if i == len(lines) - 1 and len(lines) > 1 else WHITE
            d.text((64, y), line, font=font, fill=col, stroke_width=6, stroke_fill=(0, 0, 0))
            y += lh
        bf = _font(30)
        bw = int(d.textlength(brand, font=bf)) + 36
        d.rounded_rectangle((64, 40, 64 + bw, 92), radius=26, fill=(47, 111, 115))
        d.text((82, 47), brand, font=bf, fill=WHITE)
    elif src is not None:
        canvas = _cover(src, W, H).filter(ImageFilter.GaussianBlur(28))
        canvas = Image.blend(canvas, Image.new("RGB", (W, H), (8, 10, 16)), 0.35)
        ph = H
        pw = int(src.width * ph / src.height)
        canvas.paste(src.resize((pw, ph), Image.LANCZOS), ((W - pw) // 2, 0))
    else:
        canvas = Image.new("RGB", (W, H), (36, 88, 91))
        d = ImageDraw.Draw(canvas)
        font, lines, lh = _layout_headline(d, headline or brand, 1100, 560)
        y = (H - lh * len(lines)) // 2
        for line in lines:
            tw = d.textlength(line, font=font)
            d.text(((W - tw) / 2, y), line, font=font, fill=WHITE, stroke_width=6, stroke_fill=(0, 0, 0))
            y += lh
    for q in (92, 86, 80, 72, 64):                 # stay under YouTube's 2 MB limit
        canvas.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if out.stat().st_size <= MAX_BYTES:
            break
    return str(out)


def validate_thumbnail(path: Optional[str]) -> Dict[str, Any]:
    problems: List[str] = []
    info: Dict[str, Any] = {"path": path}
    if not path or not os.path.isfile(path):
        return {"ok": False, "problems": ["thumbnail file is missing"], **info}
    size = os.path.getsize(path)
    info["bytes"] = size
    if size > MAX_BYTES:
        problems.append(f"file is {size / 1048576:.2f} MB; YouTube accepts at most 2 MB")
    try:
        with Image.open(path) as im:
            info.update(format=im.format, width=im.width, height=im.height)
            if im.format not in ("JPEG", "PNG"):
                problems.append(f"format {im.format} is not JPEG or PNG")
            if (im.width, im.height) != (W, H):
                problems.append(f"dimensions {im.width}x{im.height}; the upload artifact must be {W}x{H}")
            stdev = ImageStat.Stat(im.convert("L")).stddev[0]
            info["luma_stddev"] = round(stdev, 1)
            if stdev < 12:
                problems.append("image is nearly blank (no contrast)")
    except OSError as exc:
        problems.append(f"not a readable image: {exc}")
    return {"ok": not problems, "problems": problems, **info}


def youtube_artifact_for(thumbnail_path: Optional[str], *, headline: str = "") -> Optional[str]:
    """The 1280x720 upload artifact for a stored thumbnail: an existing valid one (made at render time),
    else composed now from the portrait (sidecar headline/source used when present)."""
    if not thumbnail_path or not os.path.isfile(thumbnail_path):
        return None
    p = Path(thumbnail_path)
    if validate_thumbnail(str(p))["ok"]:
        return str(p)
    target = p.with_name(p.stem + YOUTUBE_SUFFIX)
    if target.is_file() and validate_thumbnail(str(target))["ok"]:
        return str(target)
    side = p.with_suffix(".json")
    meta: Dict[str, Any] = {}
    if side.is_file():
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    return compose_youtube_thumbnail(str(target), headline=meta.get("headline") or headline,
                                     source_image=meta.get("source_image"), portrait_thumbnail=str(p))


def is_portrait(path: Optional[str]) -> bool:
    try:
        with Image.open(path) as im:
            return abs(im.width / im.height - 9 / 16) < 0.01
    except (OSError, TypeError, ZeroDivisionError):
        return False


def shorts_artifact_for(portrait_path: Optional[str]) -> Optional[str]:
    """1280x720 upload artifact for a Short: the whole 9:16 portrait design at full height in the centre column
    (exactly what YouTube crops for ``shortsAspectRatio``), a blurred fill of it beside."""
    if not portrait_path or not os.path.isfile(portrait_path) or not is_portrait(portrait_path):
        return None
    p = Path(portrait_path)
    target = p.with_name(p.stem + SHORTS_SUFFIX)
    if target.is_file() and validate_thumbnail(str(target))["ok"] and target.stat().st_mtime >= p.stat().st_mtime:
        return str(target)
    return compose_youtube_thumbnail(str(target), portrait_thumbnail=str(p))


def artifact_for(thumbnail_path: Optional[str], kind: str = SHORT) -> Optional[str]:
    """Shorts with a 9:16 portrait -> the Shorts-safe artifact; long-form (or no portrait) -> the 16:9 one."""
    if kind == SHORT and is_portrait(thumbnail_path):
        return shorts_artifact_for(thumbnail_path)
    return youtube_artifact_for(thumbnail_path)


def portrait_of(artifact: Optional[str]) -> Optional[str]:
    if artifact and artifact.endswith(SHORTS_SUFFIX):
        p = artifact[: -len(SHORTS_SUFFIX)] + ".png"
        return p if os.path.isfile(p) else None
    return None


def verify_shorts_variant(api_thumbnails: Dict[str, Any], portrait_path: Optional[str],
                          fetch: Optional[Callable[[str], bytes]] = None, threshold: int = 22) -> Dict[str, Any]:
    """Uses YouTube's own metadata: the ``shortsAspectRatio`` thumbnail must exist and show the portrait design."""
    sar = (api_thumbnails or {}).get("shortsAspectRatio")
    if not sar:
        return {"status": "no_shorts_variant", "reason": "YouTube lists no shortsAspectRatio thumbnail"}
    if not portrait_path:
        return {"status": "unavailable", "reason": "no local portrait to compare"}
    try:
        remote = Image.open(io.BytesIO((fetch or _fetch)(sar["url"]))).convert("RGB")
        local = Image.open(portrait_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - private videos are not served by the CDN
        return {"status": "unavailable", "reason": f"shorts thumbnail not fetchable ({type(exc).__name__})",
                "size": [sar.get("width"), sar.get("height")]}
    dist = sum(a != b for a, b in zip(_dhash(local), _dhash(remote)))
    return {"status": "verified" if dist <= threshold else "mismatch", "distance": dist,
            "size": list(remote.size)}


def write_sidecar(portrait_path: str, *, headline: str, source_image: Optional[str]) -> None:
    Path(portrait_path).with_suffix(".json").write_text(
        json.dumps({"headline": headline, "source_image": source_image}, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ remote verification
def _dhash(im: Image.Image, n: int = 12) -> List[bool]:
    g = im.convert("L").resize((n + 1, n), Image.LANCZOS)
    px = g.load()
    return [px[c, r] > px[c + 1, r] for r in range(n) for c in range(n)]


def _fetch(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-ThumbnailVerify/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def verify_remote(video_id: str, local_artifact: str, fetch: Optional[Callable[[str], bytes]] = None,
                  threshold: int = 22) -> Dict[str, Any]:
    """Compares YouTube's 16:9 thumbnail with the uploaded artifact (perceptual hash).
    verified / mismatch (YouTube still shows another image, e.g. its auto frame) / unavailable."""
    fetch = fetch or _fetch
    try:
        local = Image.open(local_artifact).convert("RGB")
    except OSError as exc:
        return {"status": "unavailable", "reason": f"local artifact unreadable: {exc}"}
    ref = _dhash(local)
    best = None
    for variant in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            remote = Image.open(io.BytesIO(fetch(f"https://i.ytimg.com/vi/{video_id}/{variant}.jpg"))).convert("RGB")
        except Exception:
            continue
        rw, rh = remote.size
        if abs(rw / rh - 4 / 3) < 0.02:            # 4:3 sizes letterbox the 16:9 image
            remote = remote.crop((0, int(rh * 0.125), rw, int(rh * 0.875)))
        dist = sum(a != b for a, b in zip(ref, _dhash(remote)))
        if best is None or dist < best[1]:
            best = (variant, dist)
        if dist <= threshold:
            return {"status": "verified", "variant": variant, "distance": dist}
    if best is None:
        return {"status": "unavailable", "reason": "no remote thumbnail could be fetched"}
    return {"status": "mismatch", "variant": best[0], "distance": best[1]}


# ------------------------------------------------------------------ persisted state
class ThumbnailStateStore:
    """Per-ledger thumbnail state in the publishing database (no credentials stored)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        with sqlite3.connect(db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS thumbnail_state (
                ledger_id INTEGER PRIMARY KEY, video_id TEXT, artifact_path TEXT, validation_json TEXT,
                upload_status TEXT, upload_attempts INTEGER DEFAULT 0, uploaded_at TEXT,
                verify_status TEXT, verify_detail_json TEXT, verify_attempts INTEGER DEFAULT 0,
                verified_at TEXT, last_error TEXT, updated_at TEXT)""")

    def get(self, ledger_id: int) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM thumbnail_state WHERE ledger_id = ?", (int(ledger_id),)).fetchone()
        return dict(row) if row else {}

    def all(self) -> Dict[int, Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as c:
            c.row_factory = sqlite3.Row
            return {int(r["ledger_id"]): dict(r) for r in c.execute("SELECT * FROM thumbnail_state")}

    def update(self, ledger_id: int, **fields: Any) -> Dict[str, Any]:
        fields["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for k in ("validation_json", "verify_detail_json"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k])
        cur = self.get(ledger_id)
        with sqlite3.connect(self.db_path) as c:
            if cur:
                sets = ", ".join(f"{k} = ?" for k in fields)
                c.execute(f"UPDATE thumbnail_state SET {sets} WHERE ledger_id = ?", (*fields.values(), int(ledger_id)))
            else:
                cols = ", ".join(["ledger_id", *fields])
                c.execute(f"INSERT INTO thumbnail_state ({cols}) VALUES ({', '.join('?' * (len(fields) + 1))})",
                          (int(ledger_id), *fields.values()))
        return self.get(ledger_id)


def ready_label(state: Dict[str, Any]) -> str:
    """Thumbnail readiness for the dashboard: a video is fully ready only with a verified thumbnail."""
    up, ver = state.get("upload_status"), state.get("verify_status")
    if not state:
        return "unknown"
    if up in ("invalid", "failed", "restricted"):
        return "NOT READY: thumbnail " + up
    if up == "uploaded" and ver == "verified":
        return "ready"
    if up == "uploaded":
        return "uploaded, verification " + (ver or "pending")
    return up or "pending"


# ------------------------------------------------------------------ channel-independent set + verify
def processing_status(service: Any, quota: Any, video_id: str, purpose: str) -> Dict[str, Any]:
    res = quota.execute(service.videos().list(part="processingDetails,status,snippet", id=video_id), "videos.list",
                        purpose=purpose, video_id=video_id)
    item = (res.get("items") or [{}])[0]
    return {"processing": ((item.get("processingDetails") or {}).get("processingStatus") or "").lower() or None,
            "privacy": (item.get("status") or {}).get("privacyStatus"),
            "thumbnails": (item.get("snippet") or {}).get("thumbnails") or {}}


def set_and_verify(service: Any, quota: Any, video_id: str, thumbnail_path: Optional[str], *, kind: str = SHORT,
                   previous: Optional[Dict[str, Any]] = None, purpose: str = "thumbnail",
                   fetch: Optional[Callable[[str], bytes]] = None) -> Dict[str, Any]:
    """One step of the thumbnail state machine (used by the Hindi publisher; the English engine has the same rules):
    awaiting_processing -> attempted -> set (YouTube accepted) -> verified (YouTube shows our image), or rejected /
    invalid / quota_deferred. A thumbnail already set with the same artifact is never uploaded again."""
    prev = dict(previous or {})
    checks = int(prev.get("checks") or 0) + 1
    artifact = artifact_for(thumbnail_path, kind)
    val = validate_thumbnail(artifact)
    if not artifact or not val["ok"]:
        return {"state": "invalid", "error": "; ".join(val["problems"] or ["no thumbnail"]), "checks": checks}
    try:
        st = processing_status(service, quota, video_id, purpose)
    except Exception as exc:  # noqa: BLE001
        return {**prev, "state": prev.get("state") or "awaiting_processing", "checks": checks,
                "error": f"processing status unavailable ({type(exc).__name__})"}
    if st["processing"] != "succeeded":
        return {"state": "awaiting_processing", "processing": st["processing"], "checks": checks, "error": None}
    already = prev.get("state") in ("set", "verified") and prev.get("artifact") == artifact
    if not already:
        gate = quota.check("general", 50)
        if not gate["allowed"]:
            return {**prev, "state": "quota_deferred", "error": gate["reason"], "checks": checks}
        try:
            from googleapiclient.http import MediaFileUpload
            resp = quota.execute(service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(
                artifact, mimetype="image/jpeg")), "thumbnails.set", purpose=purpose, video_id=video_id)
        except Exception as exc:  # noqa: BLE001 - YouTube's rejection is reported, never hidden
            status = getattr(getattr(exc, "resp", None), "status", None)
            return {"state": "rejected", "artifact": artifact, "http_status": status, "checks": checks,
                    "error": f"YouTube rejected the thumbnail (HTTP {status}): {str(exc)[:200]}"}
        variants = sorted(((resp.get("items") or [{}])[0]).keys())
        prev = {"state": "set", "artifact": artifact, "variants": variants, "set_at_utc":
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            st = processing_status(service, quota, video_id, purpose)
        except Exception:  # noqa: BLE001
            return {**prev, "checks": checks, "error": None}
    if kind == SHORT and is_portrait(thumbnail_path):
        v = verify_shorts_variant(st["thumbnails"], thumbnail_path, fetch=fetch)
    else:
        v = verify_remote(video_id, artifact, fetch=fetch)
    state = "verified" if v["status"] == "verified" else "set"
    return {**prev, "state": state, "verify": v, "privacy": st["privacy"], "checks": checks,
            "error": None if state == "verified" or v["status"] in ("unavailable",) else
            f"thumbnail set but not verified: {v['status']}"}
