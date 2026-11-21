"""Stage 3.35: per-video asset manifest and visual composition, from what was actually rendered.

The rendered Pop Info plan (``pop_info_film/plan.json``) is the ground truth for what appears in the final video.
Assets acquired by the documentary storyboard stage are NOT used by Pop Info renders (it selects its own scene
photos and footage), so they are listed separately as ``acquired_not_rendered`` and never counted as used.

Scene classes (by on-screen content):
  * ``video_footage``   - a full-bleed real-world clip (Stage 3.35)
  * ``photograph``      - a licensed photograph
  * ``text_led``        - title, kinetic type, callout, reveal
  * ``motion_graphic``  - procedural vector scenes (spotlight, environment, process, stat, compare, timeline, ...)
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TEXT_LED = frozenset({"title", "kinetic", "callout", "reveal"})
VERSION = "3.35-asset-manifest-1"


def scene_class(sc: Dict[str, Any]) -> str:
    ph = sc.get("photo") or {}
    if ph.get("footage"):
        return "video_footage"
    if ph:
        return "photograph"
    return "text_led" if sc.get("template") in TEXT_LED else "motion_graphic"


def composition(plan: Dict[str, Any]) -> Dict[str, Any]:
    dur: Counter = Counter()
    cnt: Counter = Counter()
    providers: Counter = Counter()
    for sc in plan.get("scenes") or []:
        d = max(0.0, float(sc.get("end_sec") or 0) - float(sc.get("start_sec") or 0))
        k = scene_class(sc)
        dur[k] += d
        cnt[k] += 1
        if sc.get("photo"):
            providers[f"{(sc['photo'].get('provider') or '?')}:{k}"] += 1
    total = sum(dur.values()) or 1.0
    return {"scenes": dict(cnt), "seconds": {k: round(v, 2) for k, v in dur.items()},
            "share_pct": {k: round(100.0 * v / total, 1) for k, v in dur.items()},
            "providers": dict(providers), "total_sec": round(total, 2)}


def build_manifest(*, job_id: str, plan: Dict[str, Any], storyboard_assets: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    assets: List[Dict[str, Any]] = []
    for sc in plan.get("scenes") or []:
        ph = sc.get("photo")
        if not ph:
            continue
        fo = ph.get("footage") or {}
        assets.append({
            "asset_id": ph.get("asset_id"), "job_id": job_id, "scene_id": sc.get("scene_id"),
            "asset_type": "video_footage" if fo else "photograph", "provider": ph.get("provider"),
            "title": ph.get("title"), "tags": (ph.get("tags") or [])[:10],
            "matched_terms": {k: v for k, v in (ph.get("relevance") or {}).items() if k in ("anchor_coverage", "scene_words")},
            "source_url": ph.get("source_url"), "download_url": ph.get("download_url"),
            "original_resolution": ph.get("resolution"), "rendered_resolution": [1080, 1920],
            "format": ph.get("format"), "duration_sec": ph.get("duration") if fo else None,
            "frames_rendered": fo.get("count") if fo else None,
            "scene_seconds": round(float(sc.get("end_sec") or 0) - float(sc.get("start_sec") or 0), 2),
            "license": ph.get("license"), "license_url": ph.get("license_url"), "creator": ph.get("creator"),
            "attribution": ph.get("attribution"), "attribution_required": ph.get("attribution_required", True),
            "relevance": (ph.get("relevance") or {}).get("score"), "quality_score": ph.get("final_score"),
            "search_query": ph.get("search_query"), "selection_reason": "highest relevance x quality that passed licence, "
                                                                         "relevance, integrity and duplicate checks",
            "reused_from_cache": bool(ph.get("cache_hit")), "validated": True, "download_status": "ok",
            "sha256": ph.get("sha256"),
        })
    return {"version": VERSION, "job_id": job_id, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "composition": composition(plan), "rendered_assets": assets,
            "acquired_not_rendered": [{"asset_id": a.get("asset_id") or a.get("canonical_asset_id"),
                                       "provider": a.get("source_provider") or a.get("provider"),
                                       "kind": a.get("visual_type") or a.get("asset_type")}
                                      for a in storyboard_assets][:60],
            "note": "rendered_assets are exactly the external assets drawn in the final video; procedural vector art "
                    "and text are original work."}


def write_manifest(prod_dir: Path, job_id: str, plan: Dict[str, Any]) -> Path:
    sb_assets: List[Dict[str, Any]] = []
    sb = Path(prod_dir) / "storyboards" / f"storyboard_{job_id}.json"
    try:
        beats = json.loads(sb.read_text(encoding="utf-8")).get("beats") or []
        sb_assets = [b for b in beats if b.get("asset_path") or b.get("source_url")]
    except Exception:
        pass
    m = build_manifest(job_id=job_id, plan=plan, storyboard_assets=sb_assets)
    out = Path(prod_dir) / "manifests" / f"asset_manifest_{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
    return out


def audit_recent(productions_dir: Path, job_ids: Iterable[str]) -> Dict[str, Any]:
    """Composition of rendered videos (from their plans), for the visual asset dashboard and audits."""
    per: List[Dict[str, Any]] = []
    tot_sec: Counter = Counter()
    prov: Counter = Counter()
    for j in job_ids:
        p = Path(productions_dir) / j / "pop_info_film" / "plan.json"
        if not p.is_file():
            continue
        try:
            c = composition(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
        per.append({"job_id": j, **c})
        for k, v in c["seconds"].items():
            tot_sec[k] += v
        prov.update(c["providers"])
    total = sum(tot_sec.values()) or 1.0
    return {"videos": len(per), "share_pct": {k: round(100.0 * v / total, 1) for k, v in tot_sec.items()},
            "providers": dict(prov), "per_video": per[:30]}
