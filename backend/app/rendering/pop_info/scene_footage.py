"""Stage 3.35: scene-level footage selection for Pop Info films.

Which scenes get footage: scenes whose job is to SHOW the subject or a setting. Never the title card, the payoff,
data scenes (stat / compare / timeline / process: a chart says more) or the single kinetic-type emphasis beat, and
never a scene that already has a scene-specific photograph. The creative target is 40-60 % footage for suitable
subjects; relevance always wins: a scene with no relevant, licensed, valid clip keeps its photo or vector treatment.

Each chosen clip becomes the scene's photo in ``full_bleed`` treatment with a ``footage`` frame sequence, so the
existing planner, page template, captions and credits apply unchanged.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.media import footage as ft

NO_FOOTAGE_TEMPLATES = frozenset({"stat", "compare", "timeline", "process", "kinetic"})
MAX_FOOTAGE_SHARE = 0.6
DOWNLOAD_TRIES_PER_SCENE = 3
PER_QUERY = 12


@dataclass
class FootageNeed:
    scene_index: int
    scene_id: str
    narration: str
    duration: float
    queries: List[str]


def footage_subject(plan: Dict[str, Any], anchor: str) -> Tuple[str, Any]:
    """The filmable subject and the script's recurring words; falls back to the photo anchor."""
    script_text = " ".join(s.get("narration", "") for s in plan.get("scenes", []))
    topic = plan.get("topic")
    title = topic.get("title") if isinstance(topic, dict) else (topic or "")
    subject, counts = ft.footage_terms(f"{title} {anchor}", script_text)
    return (subject or anchor), counts


def footage_needs(plan: Dict[str, Any], anchor: str, exclude: Sequence[int] = (), max_share: float = MAX_FOOTAGE_SHARE) -> List[FootageNeed]:
    """Queries per scene: the subject plus the scene's own filmable words that recur in the script (concrete
    nouns repeat; abstract connective words rarely do)."""
    scenes = plan.get("scenes", [])
    subject, counts = footage_subject(plan, anchor)
    subj = ft.ntokens(subject)
    budget = max(1, int(round(len(scenes) * max_share)))
    needs: List[FootageNeed] = []
    for i, sc in enumerate(scenes):
        if i == 0 or i == len(scenes) - 1 or i in set(exclude) or sc.get("template") in NO_FOOTAGE_TEMPLATES:
            continue
        narr = sc.get("narration", "") or ""
        words = [w for w in ft.ntokens(narr) if w not in subj and w not in ft.ABSTRACT and len(w) >= 4]
        recurring = [w for w in words if counts[w] >= 2]
        top = sorted(recurring or words, key=lambda w: (-counts[w], -len(w), w))[:2]
        queries = [q for q in dict.fromkeys([f"{subject} {' '.join(top[:1])}".strip(), subject, " ".join(top)]) if q.strip()]
        needs.append(FootageNeed(i, sc.get("scene_id", f"scene_{i + 1:02d}"), narr,
                                 float(sc.get("end_sec", 0)) - float(sc.get("start_sec", 0)), queries[:3]))
    return needs[:budget]


def select_scene_footage(*, plan: Dict[str, Any], anchor: str, job_id: str, cache_root: Path, ffmpeg: str,
                         exclude: Sequence[int] = (), providers: Optional[Sequence[Any]] = None,
                         fetcher=None) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
    """({scene_index: footage-as-photo record}, report). Every decision is reported with its reason."""
    from backend.app.media.net import HEALTH
    t0 = time.perf_counter()
    script_text = " ".join(s.get("narration", "") for s in plan.get("scenes", []))
    anchor, _ = footage_subject(plan, anchor)
    needs = footage_needs(plan, anchor, exclude)
    providers = list(providers) if providers is not None else ft.default_providers(f"{anchor} {script_text}")
    report: Dict[str, Any] = {"anchor": anchor, "providers": [p.name for p in providers], "scenes": [], "searches": []}
    if not needs or not providers:
        report["note"] = "no suitable scenes" if not needs else "no footage provider configured"
        return {}, report
    queries = sorted({q for n in needs for q in n.queries})
    results: Dict[Tuple[str, str], List[ft.FootageCandidate]] = {}

    def run(provider, query):
        if HEALTH.tripped(provider.name):
            return provider.name, query, [], "skipped: provider failed 3 times in a row"
        s = time.perf_counter()
        try:
            found = provider.search(query, PER_QUERY)
            return provider.name, query, found, f"{len(found)} results in {time.perf_counter() - s:.1f}s"
        except Exception as exc:
            return provider.name, query, [], f"error {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, query, found, note in pool.map(lambda pq: run(*pq), [(p, q) for q in queries for p in providers]):
            results[(name, query)] = found
            report["searches"].append({"provider": name, "query": query, "note": note})

    chosen: Dict[int, Dict[str, Any]] = {}
    used: set = set()
    clip_cache = Path(cache_root) / "footage_clips"
    frames_root = Path(cache_root) / "footage_frames" / job_id
    for need in needs:
        entry: Dict[str, Any] = {"scene_id": need.scene_id, "queries": need.queries, "duration": round(need.duration, 2),
                                 "rejected": {}, "top_candidates": []}

        def reject(why: str) -> None:
            k = why.split(" (")[0]
            entry["rejected"][k] = entry["rejected"].get(k, 0) + 1
        pool_: Dict[str, Tuple[float, ft.FootageCandidate, Dict[str, Any], str]] = {}
        for (pname, query), found in results.items():
            if query not in need.queries:
                continue
            for c in found:
                if c.asset_id in used:
                    reject("already used in this film")
                    continue
                ok, why = ft.licence_ok(c)
                if not ok:
                    reject(why)
                    continue
                rel = ft.relevance(c, anchor, need.narration, context=script_text)
                if not rel["relevant"]:
                    reject("a differently qualified subject" if rel.get("qualifier_conflict") else "not about this scene")
                    continue
                if c.duration and c.duration < max(ft.MIN_DURATION_SEC, 0.7 * need.duration):
                    reject("clip shorter than the scene")
                    continue
                score = round(0.65 * rel["score"] + 0.35 * ft.quality(c, need.duration), 3)
                if c.asset_id not in pool_ or score > pool_[c.asset_id][0]:
                    pool_[c.asset_id] = (score, c, rel, query)
        ranked = sorted(pool_.values(), key=lambda x: -x[0])
        entry["top_candidates"] = [{"title": c.title[:80], "provider": c.provider, "score": s} for s, c, _, _ in ranked[:3]]
        picked = None
        for score, c, rel, query in ranked[:DOWNLOAD_TRIES_PER_SCENE]:
            try:
                path, hit = ft.download(c, clip_cache, fetcher=fetcher)
                info = ft.validate(path, ffmpeg, min_duration=min(need.duration, ft.MIN_DURATION_SEC))
                start = 1.0 if info["duration"] >= need.duration + 2.0 else 0.0      # skip a fade-in when possible
                frames_dir = frames_root / need.scene_id
                n = ft.extract_frames(path, frames_dir, duration=min(need.duration + 0.6, info["duration"] - start),
                                      ffmpeg=ffmpeg, start=start)
            except Exception as exc:
                reject(f"invalid or failed download ({type(exc).__name__})")
                errs = entry.setdefault("download_errors", [])
                if len(errs) < 5:
                    errs.append({"asset_id": c.asset_id, "provider": c.provider, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                continue
            poster = frames_dir / "f_0001.jpg"
            lic = c.license
            picked = {
                "kind": "video_footage", "path": str(poster),
                "sha256": hashlib.sha256(poster.read_bytes()).hexdigest(),
                "asset_id": c.asset_id, "provider": c.provider, "source_url": c.page_url, "download_url": c.download_url,
                "creator": c.creator, "title": c.title, "license": lic, "license_url": c.license_url,
                "attribution": f"{c.creator or 'Unknown'} / {c.provider}, {lic}", "attribution_required": c.attribution_required,
                "credit": f"{(c.creator or 'Unknown')[:28]} / {c.provider}, {lic}",
                "downloaded_at": datetime.now(timezone.utc).isoformat(), "format": path.suffix.lstrip("."),
                "resolution": [info["width"], info["height"]], "duration": round(info["duration"], 2),
                "search_query": query, "scene_id": need.scene_id, "job_id": job_id, "relevance": rel,
                "final_score": score, "cache_hit": hit, "tags": c.tags[:10],
                "footage": {"frames_dir": str(frames_dir), "count": n, "fps": ft.FPS, "clip_start": start},
            }
            used.add(c.asset_id)
            break
        if picked:
            chosen[need.scene_index] = picked
            entry["chosen"] = {k: picked[k] for k in ("title", "provider", "license", "final_score", "search_query",
                                                      "cache_hit", "duration")}
        else:
            entry["chosen"] = None
            entry["fallback"] = "photo or vector treatment kept: no relevant, licensed, valid clip"
        report["scenes"].append(entry)
    report["provider_health"] = HEALTH.snapshot()
    report["elapsed_sec"] = round(time.perf_counter() - t0, 1)
    return chosen, report
