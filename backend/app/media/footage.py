"""Stage 3.35: real-world video footage for Pop Info films (search, licence, download, validation, extraction).

Why: an audit of the 30 most recent finals found 0 % footage, 10.6 % photographs and 89 % text-led or vector
scenes; Pixabay (configured, 70+ successful requests) contributed nothing to any final video, and moving media was
rejected as "not a still image". This module adds licensed stock and archival footage to suitable scenes.

Providers (free; access controls respected; every request goes through media/net.py, so it is counted in the
existing provider health telemetry under its own name):

* ``Pixabay Videos``  - pixabay.com/api/videos/ (existing PIXABAY_API_KEY). Pixabay Content License: free commercial
                        use, attribution not required (credited anyway). Files are downloaded, never hot-linked.
* ``Wikimedia Commons Video`` - Commons file search (WebM/Ogg video), using the 720p transcoded derivative.
                        Only CC0 / public domain / CC BY / CC BY-SA, with author and licence recorded.
* ``NASA Video``      - images-api.nasa.gov media_type=video, only for space subjects; captions with a
                        third-party copyright notice are rejected.
* ``Pexels Videos``   - api.pexels.com/videos (PEXELS_API_KEY). Optional: KEY_MISSING without a key, never required.

Processing (FFmpeg, one clip at a time, bounded): download (size cap, temp file, retried transient locks) ->
ffprobe (video stream, duration, resolution) -> a decode test -> centre crop to 9:16 -> 1080x1920 30 fps JPEG frame
sequence for the scene's duration, audio dropped. The Pop Info page draws one frame per output frame (deterministic
under the frame-capture renderer; one decoded frame in memory at a time).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.core.fs_utils import remove_with_retry, replace_with_retry
from backend.app.rendering.ffmpeg_geometry import stderr_tail

logger = logging.getLogger(__name__)

MIN_SHORT_SIDE = 640            # below this a 1080x1920 crop is too soft
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MIN_DURATION_SEC = 3.0
FPS = 30
FRAME_SIZE = (1080, 1920)
EXTRACT_TIMEOUT_SEC = 120
ALLOWED_LICENCE = re.compile(r"^(cc0|cc[- ]?zero|public domain|pdm|pd|cc[- ]by(?:[- ]sa)?(?:[- ]?\d\.\d)?|"
                             r"pixabay|pexels|nasa public domain)", re.I)
_TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = frozenset("the and for with from that this into over under about after before their there which while "
                  "where when what were was are has have had its it's they them than then also very more most "
                  "video footage clip stock free hd 4k 1080p background".split())


def tokens(text: str) -> set:
    return {w for w in _TOKEN.findall((text or "").lower()) if w not in _STOP}


@dataclass
class FootageCandidate:
    provider: str
    asset_id: str
    title: str
    tags: List[str]
    description: str
    page_url: str
    download_url: str
    width: int
    height: int
    duration: float
    license: str
    license_url: str = ""
    creator: str = ""
    attribution_required: bool = True
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -- providers ---------------------------------------------------------------------------------------------------
def _get_json(url: str, provider: str, headers: Optional[Dict[str, str]] = None, timeout: float = 12.0):
    from backend.app.media.net import fetch_json
    return fetch_json(url, provider=provider, timeout=timeout, retries=1, headers=headers)


class PixabayVideoProvider:
    name = "Pixabay Videos"

    def __init__(self, api_key: Optional[str] = None, get_json: Callable = _get_json):
        self.api_key = api_key if api_key is not None else os.getenv("PIXABAY_API_KEY", "")
        self.get_json = get_json

    def configured(self) -> bool:
        return bool((self.api_key or "").strip())

    def search(self, query: str, n: int = 12) -> List[FootageCandidate]:
        if not self.configured():
            return []
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
            {"key": self.api_key, "q": query[:100], "safesearch": "true", "per_page": max(3, min(50, n)),
             "video_type": "film"})
        data = self.get_json(url, self.name) or {}
        out = []
        for h in data.get("hits") or []:
            vids = h.get("videos") or {}
            # smallest rendition that still gives a sharp 9:16 crop (short side >= 720 preferred)
            opts = sorted((v for v in vids.values() if isinstance(v, dict) and v.get("url")),
                          key=lambda v: (min(v.get("width") or 0, v.get("height") or 0) < 720, v.get("size") or 0))
            if not opts:
                continue
            v = opts[0]
            slug = urllib.parse.urlparse(h.get("pageURL") or "").path.rstrip("/").split("/")[-1]
            tags = [t.strip() for t in str(h.get("tags") or "").split(",") if t.strip()]
            title = re.sub(r"[-_\d]+", " ", slug).strip()
            if title.lower() in ("", "id"):        # newer page URLs are just "id-<number>": describe it by its tags
                title = ", ".join(tags[:3])
            out.append(FootageCandidate(self.name, f"pixabay_video_{h.get('id')}", title, tags, "",
                                        h.get("pageURL") or "", v["url"], int(v.get("width") or 0), int(v.get("height") or 0),
                                        float(h.get("duration") or 0), "Pixabay Content License",
                                        "https://pixabay.com/service/license-summary/", str(h.get("user") or ""),
                                        attribution_required=False, size_bytes=int(v.get("size") or 0)))
        return out


class PexelsVideoProvider:
    name = "Pexels Videos"

    def __init__(self, api_key: Optional[str] = None, get_json: Callable = _get_json):
        self.api_key = api_key if api_key is not None else os.getenv("PEXELS_API_KEY", "")
        self.get_json = get_json

    def configured(self) -> bool:
        return bool((self.api_key or "").strip())

    def search(self, query: str, n: int = 12) -> List[FootageCandidate]:
        if not self.configured():
            return []
        url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode({"query": query[:100], "per_page": min(40, n)})
        data = self.get_json(url, self.name, headers={"Authorization": self.api_key}) or {}
        out = []
        for v in data.get("videos") or []:
            files = sorted((f for f in v.get("video_files") or [] if f.get("link") and f.get("file_type") == "video/mp4"),
                           key=lambda f: (min(f.get("width") or 0, f.get("height") or 0) < 720, (f.get("width") or 0) * (f.get("height") or 0)))
            if not files:
                continue
            f = files[0]
            slug = urllib.parse.urlparse(v.get("url") or "").path.rstrip("/").split("/")[-1]
            out.append(FootageCandidate(self.name, f"pexels_video_{v.get('id')}", re.sub(r"[-_\d]+", " ", slug).strip(), [], "",
                                        v.get("url") or "", f["link"], int(f.get("width") or 0), int(f.get("height") or 0),
                                        float(v.get("duration") or 0), "Pexels License", "https://www.pexels.com/license/",
                                        str((v.get("user") or {}).get("name") or ""), attribution_required=False))
        return out


class WikimediaVideoProvider:
    name = "Wikimedia Commons Video"

    def __init__(self, get_json: Callable = _get_json):
        self.get_json = get_json

    def search(self, query: str, n: int = 10) -> List[FootageCandidate]:
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": min(20, n),
            "gsrsearch": f"{query[:100]} filetype:video", "prop": "videoinfo",
            "viprop": "url|size|mime|extmetadata|derivatives", "viurlwidth": 1280})
        data = self.get_json(url, self.name) or {}
        out = []
        for p in ((data.get("query") or {}).get("pages") or {}).values():
            vi = (p.get("videoinfo") or [{}])[0]
            meta = vi.get("extmetadata") or {}
            lic = str((meta.get("LicenseShortName") or {}).get("value") or "")
            ders = [d for d in vi.get("derivatives") or [] if str(d.get("type", "")).startswith("video/webm")
                    and (d.get("height") or 0) and d.get("src")]
            ders.sort(key=lambda d: (abs((d.get("height") or 0) - 720), d.get("height") or 0))
            src = ders[0] if ders else None
            if not src:
                continue
            desc = re.sub(r"<[^>]+>", " ", str((meta.get("ImageDescription") or {}).get("value") or ""))[:400]
            artist = re.sub(r"<[^>]+>", "", str((meta.get("Artist") or {}).get("value") or ""))[:80]
            title = re.sub(r"^File:|\.\w+$", "", p.get("title") or "")
            out.append(FootageCandidate(self.name, f"commons_{p.get('pageid')}", title, [], desc,
                                        vi.get("descriptionurl") or "", src["src"], int(src.get("width") or 0),
                                        int(src.get("height") or 0), float(vi.get("duration") or 0), lic,
                                        str((meta.get("LicenseUrl") or {}).get("value") or ""), artist,
                                        attribution_required=not re.search(r"cc0|public domain|pdm", lic, re.I)))
        return out


class NASAVideoProvider:
    name = "NASA Video"

    def __init__(self, get_json: Callable = _get_json):
        self.get_json = get_json

    def search(self, query: str, n: int = 8) -> List[FootageCandidate]:
        url = "https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"q": query[:100], "media_type": "video"})
        data = self.get_json(url, self.name) or {}
        out = []
        for item in ((data.get("collection") or {}).get("items") or [])[:n]:
            meta = (item.get("data") or [{}])[0]
            text = f"{meta.get('title', '')} {meta.get('description', '')}"
            if re.search(r"(©|\(c\)|copyright|all rights reserved)", text, re.I):
                continue
            try:
                assets = self.get_json(item.get("href"), self.name) or []
            except Exception:
                continue
            mp4 = [a for a in assets if isinstance(a, str) and a.lower().endswith(".mp4")]
            pick = next((a for a in mp4 if "~mobile" in a), None) or next((a for a in mp4 if "~small" in a), None) or \
                (mp4[0] if mp4 else None)
            if not pick:
                continue
            out.append(FootageCandidate(self.name, f"nasa_{meta.get('nasa_id')}", str(meta.get("title") or ""),
                                        [str(k) for k in meta.get("keywords") or []][:15], str(meta.get("description") or "")[:400],
                                        f"https://images.nasa.gov/details/{urllib.parse.quote(str(meta.get('nasa_id')))}",
                                        pick.replace("http://", "https://"), 0, 0, 0.0, "NASA public domain",
                                        "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                                        str(meta.get("center") or "NASA"), attribution_required=False))
        return out


_SPACE = re.compile(r"\b(space|nasa|orbit|planet|mars|moon|lunar|rocket|satellite|galaxy|star|astronaut|telescope|"
                    r"comet|asteroid|solar|nebula|cosmic|jupiter|saturn|venus)\b", re.I)


def default_providers(subject_text: str) -> List[Any]:
    prov: List[Any] = [PixabayVideoProvider(), WikimediaVideoProvider()]
    if _SPACE.search(subject_text or ""):
        prov.insert(1, NASAVideoProvider())
    pexels = PexelsVideoProvider()
    if pexels.configured():
        prov.append(pexels)
    return [p for p in prov if getattr(p, "configured", lambda: True)()]


def provider_status() -> List[Dict[str, Any]]:
    """Configuration state of each footage provider (never key values)."""
    return [{"provider": "Pixabay Videos", "key": "PIXABAY_API_KEY", "configured": PixabayVideoProvider().configured()},
            {"provider": "Pexels Videos", "key": "PEXELS_API_KEY", "configured": PexelsVideoProvider().configured()},
            {"provider": "Wikimedia Commons Video", "key": None, "configured": True},
            {"provider": "NASA Video", "key": None, "configured": True, "note": "space subjects only"}]


# -- licence, relevance ----------------------------------------------------------------------------------------
def licence_ok(c: FootageCandidate) -> Tuple[bool, str]:
    lic = (c.license or "").strip()
    if not lic or not ALLOWED_LICENCE.search(lic):
        return False, f"licence not allowed ({lic or 'missing'})"
    if re.search(r"\bnc\b|non-?commercial|\bnd\b|no-?deriv", lic, re.I):
        return False, f"licence restricts commercial or derivative use ({lic})"
    if c.attribution_required and not c.creator and not c.page_url:
        return False, "attribution required but no author or source page"
    return True, ""


_QUAL_SKIP = frozenset("a an these those each every single entire whole many some such other same new old big small "
                       "giant huge tiny one two three our your his her".split())


def _norm(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


# Words that describe ideas rather than anything a camera can film. Footage queries never use them.
ABSTRACT = frozenset(_norm(w) for w in (
    "analysis global reveal reveals actually accelerate relationship surrounding mechanism scientist research study "
    "understand picture clarified fully dynamic begin process result effect impact way thing part help often primary "
    "component constant extreme entire instantly directly produce produced cause causing create keep make making show "
    "finding new data number percent problem question answer reason because might could would should assume really "
    "important amplify loss rather simply every never always another even just like much many without within between "
    "through across first second then next finally truly suddenly only still these those there here where "
    "officially scientific named called known transmitting engineer january february march april june july "
    "august september october november december monday sunday today year years".split()))


def ntokens(text: str) -> set:
    return {_norm(w) for w in tokens(text)}


def footage_terms(title: str, script_text: str) -> Tuple[str, Counter]:
    """(subject, recurring word counts). The subject is the title's filmable words that recur in the narration
    (at most two, in title order): "Global analysis reveals how lakes amplify glacier ice loss" -> "lake glacier"."""
    counts: Counter = Counter(_norm(w) for w in _TOKEN.findall((script_text or "").lower())
                              if w not in _STOP and len(w) >= 4 and _norm(w) not in ABSTRACT)
    title_words = [t for t in dict.fromkeys(_norm(w) for w in _TOKEN.findall((title or "").lower()))
                   if t not in ABSTRACT and t not in _STOP]
    recurring = [t for t in title_words if counts[t] >= 2]
    top = set(sorted(recurring, key=lambda t: -counts[t])[:2])
    subject = " ".join(t for t in title_words if t in top)
    return subject, counts


def subject_qualifiers(context: str, heads: set) -> Dict[str, set]:
    """Words the script itself puts directly before a subject word ("gas turbine", "steam turbines" -> turbine:
    {gas, steam}). A clip of a differently qualified thing (a *wind* turbine) is not this subject."""
    words = [w for w in _TOKEN.findall((context or "").lower())]
    seen: Counter = Counter()
    for prev, w in zip(words, words[1:]):
        h = _norm(w)
        if h in heads and prev not in _STOP and prev not in _QUAL_SKIP and _norm(prev) != h:
            seen[(h, _norm(prev))] += 1
    out: Dict[str, set] = {}
    for (h, q), n in seen.items():
        if n >= 2:          # a consistent compound ("gas turbine" twice), not a one-off verb ("touching glaciers")
            out.setdefault(h, set()).add(q)
    return out


def relevance(c: FootageCandidate, anchor: str, narration: str, context: str = "") -> Dict[str, Any]:
    """Footage is b-roll: it must show the subject (anchor) or the scene's own concrete words.

    Partial subject matches are not enough on their own: "wind turbine" shares one word with "turbine blade" but is
    a different thing. A clip is relevant when it covers every subject word, or half of them plus at least one of
    the scene's own words, or at least three of the scene's words."""
    blob = ntokens(f"{c.title} {' '.join(c.tags)} {c.description}")
    a = ntokens(anchor) - ABSTRACT
    narr = ntokens(narration) - a - ABSTRACT
    anchor_cov = len(a & blob) / max(1, len(a))
    narr_hits = sorted(narr & blob)
    ok = anchor_cov >= 1.0 or (anchor_cov >= 0.5 and bool(narr_hits)) or len(narr_hits) >= 3
    conflict = None
    if ok and context:
        nblob = blob
        heads = a & nblob
        for h, quals in subject_qualifiers(f"{anchor} {context}", heads).items():
            if not (quals & nblob):
                conflict = f"script means {'/'.join(sorted(quals))} {h}"
                ok = False
                break
    score = round(0.6 * anchor_cov + 0.4 * min(1.0, len(narr_hits) / 3), 3)
    out = {"score": score, "anchor_coverage": round(anchor_cov, 2), "scene_words": narr_hits[:6], "relevant": ok}
    if conflict:
        out["qualifier_conflict"] = conflict
    return out


def quality(c: FootageCandidate, scene_dur: float) -> float:
    short = min(c.width or 0, c.height or 0) or 720
    res = min(1.0, short / 1080)
    portrait = 1.0 if (c.height or 0) > (c.width or 0) else 0.7
    dur_fit = 1.0 if (c.duration or scene_dur) >= scene_dur else max(0.0, (c.duration or 0) / max(scene_dur, 0.1))
    return round(0.4 * res + 0.2 * portrait + 0.4 * dur_fit, 3)


# -- download, validation, extraction ----------------------------------------------------------------------------
def _ffprobe(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    return str(p.with_name("ffprobe.exe" if p.name.lower().endswith(".exe") else "ffprobe"))


def probe(path: Path, ffmpeg: str) -> Dict[str, Any]:
    r = subprocess.run([_ffprobe(ffmpeg), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError(f"not a readable video ({stderr_tail(r.stderr, 200)})")
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [None])[0]
    if not s:
        raise ValueError("no video stream")
    return {"codec": s.get("codec_name"), "width": int(s.get("width") or 0), "height": int(s.get("height") or 0),
            "duration": float((d.get("format") or {}).get("duration") or 0), "fps": s.get("avg_frame_rate")}


def download(c: FootageCandidate, cache_dir: Path, *, fetcher: Optional[Callable[..., bytes]] = None) -> Tuple[Path, bool]:
    """Downloads into a content-addressed cache (re-used across productions). Returns (path, cache_hit)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(c.download_url.encode("utf-8")).hexdigest()[:24]
    ext = ".webm" if ".webm" in c.download_url.lower() else ".mp4"
    final = cache_dir / f"{key}{ext}"
    if final.is_file() and final.stat().st_size > 0:
        return final, True
    if fetcher is None:
        from backend.app.media.net import fetch
        fetcher = fetch
    body = fetcher(c.download_url, provider=c.provider, timeout=60.0, retries=1, max_bytes=MAX_DOWNLOAD_BYTES)
    tmp = cache_dir / f"temp_{key}_{int(time.time() * 1000)}{ext}"
    try:
        with open(tmp, "wb") as fh:
            fh.write(body)
        replace_with_retry(str(tmp), str(final))
    finally:
        remove_with_retry(str(tmp))
    return final, False


def validate(path: Path, ffmpeg: str, *, min_duration: float) -> Dict[str, Any]:
    info = probe(path, ffmpeg)
    if info["duration"] < min_duration:
        raise ValueError(f"too short ({info['duration']:.1f}s < {min_duration:.1f}s)")
    if min(info["width"], info["height"]) < MIN_SHORT_SIDE:
        raise ValueError(f"too small ({info['width']}x{info['height']})")
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{min(1.0, info['duration'] / 3):.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG:file=-",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError(f"cannot decode ({stderr_tail(r.stderr, 200)})")
    m = re.search(r"YAVG=(\d+(?:\.\d+)?)", r.stdout)
    if m and float(m.group(1)) < 12:
        raise ValueError("frame is black")
    return info


def extract_frames(src: Path, out_dir: Path, *, duration: float, ffmpeg: str, start: float = 0.0,
                   timeout: float = EXTRACT_TIMEOUT_SEC) -> int:
    """Centre-cropped 9:16, 1080x1920, 30 fps JPEG frames for ``duration`` seconds; no audio. Returns frame count."""
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = FRAME_SIZE
    vf = (f"crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale={w}:{h}:flags=lanczos,setsar=1,fps={FPS},"
          "format=yuvj420p")
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{duration:.3f}", "-i", str(src),
                        "-an", "-vf", vf, "-q:v", "4", str(out_dir / "f_%04d.jpg")],
                       capture_output=True, text=True, timeout=timeout)
    frames = len(list(out_dir.glob("f_*.jpg")))
    if r.returncode != 0 or frames == 0:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise ValueError(f"frame extraction failed ({stderr_tail(r.stderr, 200)})")
    return frames
