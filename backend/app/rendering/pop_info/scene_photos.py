"""
Scene-level photo selection for the Pop Info engine.

Photos are chosen per scene from the scene brief, not per topic: a scene about Yuri Milner looks
for Yuri Milner, a scene about orbital debris looks for orbital debris. A scene only receives a
photograph when that is the right treatment (a real person, place, object or event named in the
narration); statistics, mechanisms and abstract ideas stay with vector graphics.

Pipeline per scene:
  queries (scene entities + topic anchor) -> parallel provider search (healthy providers only)
  -> licence filter (LicenseVerifier APPROVED only; unknown licence = rejected)
  -> text relevance from the provider's description/categories/tags (never the filename alone)
  -> download through the checksum cache with integrity checks (magic bytes, size limit, decode,
     dimensions, contrast, sharpness) -> duplicate check (dHash) -> focus point for cropping
  -> provenance record (provider, source, creator, licence, attribution, checksum, query, scene).

A failed provider never stops the run: the others are still searched, and a scene with no
suitable photograph keeps its vector treatment, with the reason recorded.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
MIN_SHORT_SIDE = 640
MAX_LONG_SIDE = 2048
MIN_TEXT_SCORE = 0.5
DOWNLOADS_PER_SCENE = 3
_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"RIFF", "image/webp"))
_STOP = set("""a an the and or but if of in on at to for from by with as is are was were be been it its this that these
those there their they them he she his her we our you your not no so than then into over under about after before also
just very more most much many such can could would should will may might which who what when where why how all any each
every some one two do does did has have had while still even only like""".split())
_CALENDAR = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
# words that are capitalised but rarely a photographable subject in context ("the biggest prize on Earth")
_WEAK_ENTITIES = {"earth", "world", "god", "internet", "science", "history", "nature", "western", "eastern",
                  "northern", "southern", "english", "european", "american", "british"}
_GENERIC = {"people", "thing", "things", "time", "year", "years", "world", "way", "part", "kind", "lot", "fact", "work",
            "news", "study", "paper", "report", "research", "researchers", "scientists", "really", "actually", "simply"}
_JUNK = re.compile(r"\b(logo|icon|coat of arms|flag of|signature|seal of|stamp|screenshot|diagram|chart|graph|"
                   r"map of|locator|template|banner|poster|scan of|page \d|text|document|svg|clip ?art|"
                   r"cartoon|meme|mascot)\b", re.I)
_SPACE = re.compile(r"\b(space|orbit|orbital|satellite|nasa|rocket|planet|astronaut|galaxy|telescope|moon|mars|"
                    r"earth|debris|spacecraft|launch|solar|cosmic|asteroid|comet)\b", re.I)


def _stem(w: str) -> str:
    w = w.lower().strip("'")
    for suf in ("ing", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", text or "") if w.lower() not in _STOP and len(w) > 2}


def entities(text: str) -> List[str]:
    """Proper-noun phrases from the narration ('Yuri Milner', 'Jet Propulsion Laboratory', 'Bristol')."""
    out: List[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        words = re.findall(r"[A-Za-z][\w'\-]*|[.,;:!?]", sent)
        run: List[str] = []
        for i, w in enumerate(words + ["."]):
            cap = (w[:1].isupper() and w.lower() not in _STOP and w.lower() not in _CALENDAR
                   and w not in ("Dr", "Mr", "Ms", "Mrs"))
            joiner = run and w.lower() in ("of", "de", "von", "van") and i + 1 < len(words) and words[i + 1][:1].isupper()
            if cap or joiner:
                run.append(w)
                continue
            if run:
                phrase = " ".join(run).strip()
                first_word_only = len(run) == 1 and (i - 1) == 0
                if not first_word_only and phrase.lower() not in _STOP:
                    out.append(phrase)
                run = []
    seen, uniq = set(), []
    for e in out:
        if e.lower() not in seen:
            seen.add(e.lower()); uniq.append(e)
    return uniq


def topic_anchor(title: str, script_text: str, key_entities: Sequence[str] = ()) -> str:
    """Short searchable name of the subject ('Breakthrough Prize', 'space junk', 'climate anxiety')."""
    low_script = (script_text or "").lower()
    names = [re.sub(r"^(the|a|an)\s+", "", e, flags=re.I).strip() for e in key_entities]
    short = [e for e in names if 1 < len(e.split()) <= 5 and e.lower() in title.lower() and e.lower() != title.lower()]
    if short:   # the name the narration actually uses most, then the shorter one
        return max(short, key=lambda e: (low_script.count(e.lower()), -len(e)))
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", title) if w.lower() not in _STOP]
    if len(words) <= 4:
        return " ".join(words)
    low = (script_text or "").lower()
    pairs = [f"{a} {b}" for a, b in zip(words, words[1:])]
    best = max(pairs, key=lambda p: (low.count(p.lower()), -pairs.index(p)))
    return best if low.count(best.lower()) else " ".join(words[:3])


@dataclass
class PhotoNeed:
    scene_index: int
    scene_id: str
    narration: str
    queries: List[str]
    entities: List[str]
    reason: str


def photo_needs(plan: Dict[str, Any], anchor: str, max_photos: Optional[int] = None) -> List[PhotoNeed]:
    """Scenes where a real photograph is the right treatment, at most ~40% of scenes and never adjacent."""
    scenes = plan.get("scenes", [])
    budget = max_photos if max_photos is not None else max(2, min(5, round(len(scenes) * 0.4)))
    scored: List[Tuple[float, int, PhotoNeed]] = []
    for i, sc in enumerate(scenes):
        if i == 0 or i == len(scenes) - 1:
            continue   # the title and the payoff reveal keep the channel's illustrated identity
        narr = sc.get("narration", "")
        ents = [e for e in entities(narr) if e.lower() != anchor.lower() and e.lower() not in _WEAK_ENTITIES]
        concrete = len(_tokens(narr) & _tokens(anchor))
        data_scene = sc.get("template") in ("stat", "compare", "timeline")
        # a full name (person, place, organisation) is what a photograph shows best
        ent_weight = sum(2.5 if len(e.split()) > 1 else 1.2 for e in ents[:2])
        score = ent_weight + 1.0 * min(1, concrete) - (1.0 if data_scene else 0)
        if score < 1.0:
            continue
        queries: List[str] = []
        for e in ents[:2]:
            queries.append(e if (len(e.split()) > 1 or not anchor) else f"{e} {anchor}")
        if anchor:
            queries.append(anchor)   # entity queries first; the subject itself as the broad fallback
        reason = (f"names {ents[:2]}" if ents else f"concrete subject '{anchor}'") + (" (data scene: photo + numbers)" if data_scene else "")
        scored.append((score, i, PhotoNeed(i, sc.get("scene_id", f"scene_{i + 1:02d}"), narr, list(dict.fromkeys(queries))[:3], ents, reason)))
    chosen: List[PhotoNeed] = []
    for score, i, need in sorted(scored, key=lambda x: (-x[0], x[1])):
        if len(chosen) >= budget:
            break
        if any(abs(i - c.scene_index) <= 1 for c in chosen):
            continue
        chosen.append(need)
    return sorted(chosen, key=lambda n: n.scene_index)


# ---------------------------------------------------------------- relevance
def text_relevance(cand: Any, query: str, need: PhotoNeed, anchor: str) -> Dict[str, Any]:
    desc = f"{getattr(cand, 'description', '') or ''} {' '.join(getattr(cand, 'tags', []) or [])}"
    title = getattr(cand, "title", "") or ""
    q = _tokens(query)
    d_t, t_t = _tokens(desc), _tokens(title)
    cov_desc = len(q & d_t) / max(1, len(q))
    cov_title = len(q & t_t) / max(1, len(q))
    provider = getattr(cand, "source_repository", "")
    curated_title = provider == "NASA Image Library"
    coverage = max(cov_desc, (0.7 * cov_title + 0.3 * cov_desc) if curated_title else 0.5 * cov_desc + 0.25 * cov_title)
    blob = f"{desc} {title}".lower()
    # A scene entity in the caption is strong evidence. The topic anchor alone is not: a photo must
    # also share this scene's own words, or a "fundamental physics" lab photo could illustrate a
    # sentence about prize money.
    ent_hit = any(e.lower() in blob for e in need.entities)
    anchor_t = _tokens(anchor)
    narr = {w for w in _tokens(need.narration) if w not in _GENERIC} - anchor_t
    narr_cov = len(narr & (d_t | t_t)) / max(1, min(6, len(narr)))
    anchor_hit = bool(anchor) and anchor.lower() in blob
    if need.entities:
        scene_specific = ent_hit or narr_cov >= 0.34
    else:   # the scene is about the topic itself: the topic name plus some of the scene's own words
        scene_specific = anchor_hit and narr_cov >= 0.15
    # a caption naming a person the narration never mentions shows someone else: never use it
    narr_low = need.narration.lower()
    other_people = [nm for nm in re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+)\b", title)
                    if nm.lower() not in narr_low and not any(nm.lower() in e.lower() or e.lower() in nm.lower() for e in need.entities)
                    and not re.search(r"(laborator|universit|institut|centre|center|prize|award|station|agency|"
                                      r"museum|college|society|council|regiment|hussars|cavalry|yeomanry|"
                                      r"royal|national|space|flight|research|propulsion)", nm, re.I)]
    if other_people:
        scene_specific = False
    junk = bool(_JUNK.search(f"{title} {getattr(cand, 'description', '')}"))
    evidence = (cov_desc >= 0.5 or (curated_title and cov_desc >= 0.34 and cov_title >= 0.5)) and scene_specific
    score = 0.45 * coverage + 0.25 * (1.0 if ent_hit else 0.0) + 0.30 * min(1.0, narr_cov) - (0.4 if junk else 0.0)
    return {"score": round(score, 3), "coverage_desc": round(cov_desc, 2), "coverage_title": round(cov_title, 2),
            "scene_entity": ent_hit, "narration_overlap": round(narr_cov, 2), "scene_specific": scene_specific, "junk": junk,
            "unrelated_people": other_people,
            "evidence_beyond_filename": evidence}


# ---------------------------------------------------------------- image checks
def _sniff(data: bytes) -> Optional[str]:
    for magic, mime in _MAGIC:
        if data.startswith(magic) and (mime != "image/webp" or data[8:12] == b"WEBP"):
            return mime
    return None


def image_metrics(img) -> Dict[str, Any]:
    g = np.asarray(img.convert("L").resize((384, int(384 * img.height / img.width)) if img.width else (384, 384)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    gx, gy = np.abs(np.diff(g, axis=1))[:-1, :], np.abs(np.diff(g, axis=0))[:, :-1]
    mag = gx + gy
    total = float(mag.sum()) or 1.0
    ys, xs = np.mgrid[0:mag.shape[0], 0:mag.shape[1]]
    fx = float((mag * xs).sum() / total / mag.shape[1])
    fy = float((mag * ys).sum() / total / mag.shape[0])
    rgb = np.asarray(img.convert("RGB").resize((96, 96)), dtype=np.float32)
    sat = float((rgb.max(axis=2) - rgb.min(axis=2)).mean())
    small = np.asarray(img.convert("L").resize((9, 8)), dtype=np.float32)
    dh = int("".join("1" if b else "0" for b in (small[:, 1:] > small[:, :-1]).flatten()), 2)
    return {"contrast": round(float(g.std()), 1), "sharpness": round(float(lap.var()), 1), "saturation": round(sat, 1),
            "focus": {"x": round(min(0.7, max(0.3, fx)), 3), "y": round(min(0.68, max(0.32, fy)), 3)}, "dhash": dh}


def _ham(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class AssetCache:
    """Content-addressed image cache. A hit re-verifies the checksum; licensing is re-checked by the caller."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.files = self.root / "images"
        self.index_path = self.root / "index.json"
        self.files.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self.index: Dict[str, Any] = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.index = {}

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

    def get(self, url: str) -> Optional[Dict[str, Any]]:
        rec = self.index.get(self.key(url))
        if not rec:
            return None
        p = self.files / rec["file"]
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != rec["derived_sha256"]:
            return None   # missing or altered on disk: download again
        return {**rec, "path": str(p)}

    def put(self, url: str, rec: Dict[str, Any]) -> None:
        with self._lock:
            self.index[self.key(url)] = {k: v for k, v in rec.items() if k != "path"}
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.index, indent=1), encoding="utf-8")
            tmp.replace(self.index_path)

    def mark_used(self, url: str, job_id: str) -> None:
        with self._lock:
            rec = self.index.get(self.key(url))
            if rec is not None:
                rec["used_by"] = sorted(set(rec.get("used_by", [])) | {job_id})[-50:]
                self.index_path.write_text(json.dumps(self.index, indent=1), encoding="utf-8")


def download_checked(cand: Any, cache: AssetCache) -> Tuple[Optional[Dict[str, Any]], str]:
    """(record, reason). Never returns an image that failed an integrity check."""
    from PIL import Image
    from backend.app.media.net import fetch

    url = getattr(cand, "download_url", "")
    if getattr(cand, "source_repository", "") == "NASA Image Library" and "~orig." in url:
        url = url.replace("~orig.", "~large.")   # ~1600 px rendition; originals can exceed 50 MB
    hit = cache.get(url)
    if hit:
        return {**hit, "cache_hit": True}, "cache hit (checksum verified)"
    try:
        data = fetch(url, provider=getattr(cand, "source_repository", "download"), retries=1, max_bytes=MAX_DOWNLOAD_BYTES,
                     headers={"Accept": "image/jpeg,image/png,image/webp"})
    except Exception as exc:
        return None, f"download failed: {exc}"
    mime = _sniff(data)
    if not mime:
        return None, "not a JPEG/PNG/WebP file (magic bytes)"
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        return None, f"image does not decode: {type(exc).__name__}"
    w, h = img.size
    if min(w, h) < MIN_SHORT_SIDE:
        return None, f"too small: {w}x{h} (short side < {MIN_SHORT_SIDE})"
    if max(w, h) / max(1, min(w, h)) > 3.2:
        return None, f"extreme aspect ratio {w}x{h}"
    m = image_metrics(img)
    if m["contrast"] < 18:
        return None, f"flat/washed-out image (contrast {m['contrast']})"
    if m["sharpness"] < 25:
        return None, f"blurry image (sharpness {m['sharpness']})"
    img = img.convert("RGB")
    if max(w, h) > MAX_LONG_SIDE:
        k = MAX_LONG_SIDE / max(w, h)
        img = img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    derived = buf.getvalue()
    original_sha, derived_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(derived).hexdigest()
    name = f"{derived_sha[:24]}.jpg"
    (cache.files / name).write_bytes(derived)
    rec = {"file": name, "path": str(cache.files / name), "download_url": url, "original_sha256": original_sha,
           "derived_sha256": derived_sha, "original_format": mime, "original_size": [w, h], "size": list(img.size),
           "bytes": len(data), "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "metrics": m,
           "cache_hit": False}
    cache.put(url, rec)
    return rec, "downloaded and verified"


# ---------------------------------------------------------------- orchestration
def default_providers(anchor: str):
    from backend.app.media.license_verifier import LicenseVerifier
    from backend.app.media.providers.internet_archive_provider import InternetArchiveProvider
    from backend.app.media.providers.nasa_provider import NASAProvider
    from backend.app.media.providers.openverse_provider import OpenverseProvider
    from backend.app.media.providers.stock_provider import PexelsProvider, PixabayProvider
    from backend.app.media.providers.wikimedia_provider import WikimediaProvider
    v = LicenseVerifier()
    providers = [WikimediaProvider(license_verifier=v), OpenverseProvider(license_verifier=v),
                 InternetArchiveProvider(license_verifier=v)]
    if _SPACE.search(anchor or ""):
        providers.insert(1, NASAProvider(license_verifier=v))
    for extra in (PexelsProvider(license_verifier=v), PixabayProvider(license_verifier=v)):
        if extra.is_configured():
            providers.append(extra)
    return providers


def _licence_ok(cand: Any) -> Tuple[bool, str]:
    from backend.app.media.models import LicenseStatus
    if getattr(cand, "verification_status", None) != LicenseStatus.APPROVED:
        return False, f"licence not approved: {getattr(cand, 'rejection_reason', None) or getattr(cand, 'license_type', '')}"
    if getattr(cand, "asset_type", "image") not in ("image",):
        return False, "not a still image"
    mime = (getattr(cand, "mime_type", "") or "").lower()
    if mime and mime not in ("image/jpeg", "image/png", "image/webp"):
        return False, f"unsupported type {mime}"
    if re.search(r"(©|\(c\)|copyright|all rights reserved)", f"{getattr(cand, 'description', '')}", re.I) and \
            getattr(cand, "source_repository", "") == "NASA Image Library":
        return False, "third-party copyright notice in a NASA caption"
    return True, ""


def select_scene_photos(*, plan: Dict[str, Any], topic_title: str, key_entities: Sequence[str], job_id: str,
                        cache_root: Path, providers: Optional[Sequence[Any]] = None,
                        per_query: int = 12) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
    """({scene_index: photo}, report). The report lists every decision with its reason."""
    from backend.app.media.net import HEALTH

    script_text = " ".join(s.get("narration", "") for s in plan.get("scenes", []))
    anchor = topic_anchor(topic_title, script_text, key_entities)
    needs = photo_needs(plan, anchor)
    providers = list(providers) if providers is not None else default_providers(f"{anchor} {script_text}")
    cache = AssetCache(cache_root)
    report: Dict[str, Any] = {"anchor": anchor, "providers": [p.provider_name for p in providers], "scenes": []}
    queries = sorted({q for n in needs for q in n.queries})
    results: Dict[Tuple[str, str], List[Any]] = {}
    search_log: List[Dict[str, Any]] = []

    def run(provider, query):
        if HEALTH.tripped(provider.provider_name):
            return provider.provider_name, query, [], "skipped: provider failed 3 times in a row"
        t0 = time.perf_counter()
        try:
            found = provider._sync_fetch(query, per_query)
            return provider.provider_name, query, found, f"{len(found)} results in {time.perf_counter() - t0:.1f}s"
        except Exception as exc:   # providers normally catch their own errors; this is a last guard
            return provider.provider_name, query, [], f"error {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, query, found, note in pool.map(lambda pq: run(*pq), [(p, q) for q in queries for p in providers]):
            results[(name, query)] = found
            search_log.append({"provider": name, "query": query, "note": note})
    report["searches"] = search_log

    chosen: Dict[int, Dict[str, Any]] = {}
    hashes: List[int] = []
    used_urls: set = set()
    for need in needs:
        entry: Dict[str, Any] = {"scene_id": need.scene_id, "why_photo": need.reason, "queries": need.queries,
                                 "rejected": {}, "top_candidates": []}
        pool: List[Tuple[float, Any, Dict[str, Any], str]] = []
        found_by: Dict[str, Tuple[Any, List[str]]] = {}
        for (pname, query), found in results.items():
            if query not in need.queries:
                continue
            for cand in found:   # the same image returned by several queries is judged once, on its best query
                found_by.setdefault(cand.download_url, (cand, []))[1].append(query)
        for cand, qs in found_by.values():
            ok, why = _licence_ok(cand)
            if not ok:
                entry["rejected"][why.split(":")[0]] = entry["rejected"].get(why.split(":")[0], 0) + 1
                continue
            rel, query = max(((text_relevance(cand, q, need, anchor), q) for q in qs),
                             key=lambda rq: (rq[0]["evidence_beyond_filename"], rq[0]["score"]))
            if not rel["evidence_beyond_filename"] or rel["score"] < MIN_TEXT_SCORE:
                key = ("not about this scene" if not rel["scene_specific"] else
                       "no description/tag evidence" if not rel["evidence_beyond_filename"] else "low relevance")
                entry["rejected"][key] = entry["rejected"].get(key, 0) + 1
                continue
            pool.append((rel["score"], cand, rel, query))
        pool.sort(key=lambda x: -x[0])
        seen_ids = set()
        pool = [p for p in pool if not (p[1].download_url in seen_ids or seen_ids.add(p[1].download_url))]
        entry["top_candidates"] = [{"title": c.title[:90], "provider": c.source_repository, "score": s,
                                    "relevance": r} for s, c, r, _ in pool[:3]]
        picked = None
        for s, cand, rel, query in pool[:DOWNLOADS_PER_SCENE * 2]:
            if cand.download_url in used_urls:
                continue
            rec, why = download_checked(cand, cache)
            if rec is None:
                entry["rejected"][why.split(":")[0]] = entry["rejected"].get(why.split(":")[0], 0) + 1
                continue
            if any(_ham(rec["metrics"]["dhash"], h) <= 10 for h in hashes):
                entry["rejected"]["near-duplicate of another chosen photo"] = entry["rejected"].get("near-duplicate of another chosen photo", 0) + 1
                continue
            reuse = len(set(rec.get("used_by", [])) - {job_id})
            w, h = rec["size"]
            quality = min(1.0, min(rec["original_size"]) / 1200) * 0.6 + min(1.0, rec["metrics"]["sharpness"] / 400) * 0.4
            aspect = 1 - min(1.0, abs(np.log((w / h) / 0.75)) / 1.6)
            final = round(float(0.6 * s + 0.2 * quality + 0.1 * aspect + 0.1 * (0 if reuse else 1)), 3)
            lic_name = str(getattr(cand.license_type, "value", cand.license_type))
            ver = cand.license_version or ""
            lic = lic_name if (not ver or ver.lower() in lic_name.lower()) else f"{lic_name} {ver}"
            picked = {
                "path": rec["path"], "sha256": rec["derived_sha256"], "original_sha256": rec["original_sha256"],
                "asset_id": cand.asset_id, "provider": cand.source_repository, "source_url": cand.source_url,
                "download_url": rec["download_url"], "creator": cand.creator, "title": cand.title,
                "license": lic, "license_url": cand.license_url, "attribution": cand.attribution_text,
                "attribution_required": not any(k in lic.lower() for k in ("cc0", "public domain", "pdm")),
                "downloaded_at": rec["downloaded_at"], "format": rec["original_format"], "resolution": rec["original_size"],
                "search_query": query, "topic": topic_title, "scene_id": need.scene_id, "job_id": job_id,
                "relevance": rel, "final_score": final, "cache_hit": rec.get("cache_hit", False),
                "focus": rec["metrics"]["focus"], "metrics": {k: rec["metrics"][k] for k in ("contrast", "sharpness", "saturation")},
                "credit": f"{(cand.creator or 'Unknown')[:28]} / {cand.source_repository}, {lic}",
            }
            hashes.append(rec["metrics"]["dhash"])
            used_urls.add(cand.download_url)
            cache.mark_used(rec["download_url"], job_id)
            break
        if picked:
            chosen[need.scene_index] = picked
            entry["chosen"] = {k: picked[k] for k in ("title", "provider", "license", "final_score", "search_query", "cache_hit")}
        else:
            entry["chosen"] = None
            entry["fallback"] = "vector treatment kept: no candidate passed licence, relevance and integrity checks"
        report["scenes"].append(entry)
    report["provider_health"] = HEALTH.snapshot()
    HEALTH.persist(Path(cache_root) / "provider_health.json")
    return chosen, report
