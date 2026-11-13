"""
Packages a Pop Info film for Lemo-Opuscar: <prod_dir>/pop_info_film/{index.html, engine.js, plan.json,
fonts/, assets/}. Fonts are the SIL OFL faces bundled with Lemo-Opuscar (Fredoka, Lilita One), copied
with their licence so the page renders offline and identically every time.

Returns the same manifest keys the legacy LemoOpuscarFilmBuilder returns (demo_dir, beat_count, ...),
so the orchestrator can switch builders behind the feature flag without other changes.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.app.rendering.pop_info.scene_planner import ENGINE_VERSION, plan_scenes

ENGINE_JS = Path(__file__).with_name("engine.js")
from backend.app.core.paths import TOOLS_DIR
LEMO_ROOT = TOOLS_DIR / "dependencies" / "lemo-opuscar"
FONT_FILES = ("f1.woff2", "f2.woff2", "f3.woff2", "f4.woff2", "f5.woff2")   # Fredoka 500-700, Lilita One (latin, latin-ext)

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="fonts/fonts.css">
<style>html,body{{margin:0;background:#F4EEDF;overflow:hidden}}#stage{{display:block;width:100vw;height:100vh}}</style>
</head><body><canvas id="stage" width="{w}" height="{h}"></canvas>
<script>window.PLAN = {plan};</script>
<script src="engine.js"></script>
</body></html>
"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_fonts(dst: Path) -> List[str]:
    src = LEMO_ROOT / "core" / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    css = (src / "fonts.css").read_text(encoding="utf-8")
    # keep only the Fredoka and Lilita One faces (the others are not used and not shipped)
    blocks = [b for b in css.split("@font-face") if ("'Fredoka'" in b or "'Lilita One'" in b)]
    (dst / "fonts.css").write_text("".join("@font-face" + b for b in blocks), encoding="utf-8")
    copied = []
    for name in FONT_FILES:
        if (src / name).is_file():
            shutil.copyfile(src / name, dst / name)
            copied.append(name)
    shutil.copyfile(src / "OFL.md", dst / "OFL.md")
    return copied


def build_pop_info_film(*, prod_dir: Path, job_id: str, topic: str, category: str,
                        script_data: Dict[str, Any], section_timings: Sequence[Dict[str, Any]],
                        duration_sec: float, photos: Sequence[Dict[str, Any]] = (),
                        scene_photos: Optional[Dict[int, Dict[str, Any]]] = None,
                        size: tuple = (1080, 1920)) -> Dict[str, Any]:
    film_dir = Path(prod_dir) / "pop_info_film"
    assets_dir = film_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # photos: only licensed, relevance-checked assets offered by the caller; copied with provenance
    usable: List[Dict[str, Any]] = []
    for p in photos:
        src = Path(p.get("path", ""))
        if not src.is_file() or not p.get("license"):
            continue
        sha = p.get("sha256") or _sha256(src)
        name = f"photo_{sha[:16]}{src.suffix.lower() or '.jpg'}"
        shutil.copyfile(src, assets_dir / name)
        usable.append({**p, "path": str(src), "sha256": sha, "src": f"assets/{name}",
                       "credit": p.get("credit") or f"{p.get('creator') or 'Unknown'} / {p.get('provider') or ''}, {p['license']}"})

    # scene-specific photos (scene index -> photo chosen for that scene by scene_photos.py)
    usable_by_scene: Dict[int, Dict[str, Any]] = {}
    for idx, p in (scene_photos or {}).items():
        src = Path(p.get("path", ""))
        if not src.is_file() or not p.get("license"):
            continue
        sha = p.get("sha256") or _sha256(src)
        name = f"photo_{sha[:16]}{src.suffix.lower() or '.jpg'}"
        shutil.copyfile(src, assets_dir / name)
        usable_by_scene[int(idx)] = {**p, "sha256": sha, "src": f"assets/{name}"}
    plan = plan_scenes(job_id=job_id, topic_title=topic, category=category,
                       sections=script_data.get("sections") or [], timings=section_timings,
                       total_duration=float(duration_sec), photos=usable, scene_photos=usable_by_scene or None)
    fonts = _copy_fonts(film_dir / "fonts")
    shutil.copyfile(ENGINE_JS, film_dir / "engine.js")
    (film_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    page = PAGE.format(title=html.escape(topic), w=size[0], h=size[1],
                       plan=json.dumps(plan).replace("</", "<\\/"))
    (film_dir / "index.html").write_text(page, encoding="utf-8")

    asset_provenance = []
    for sc in plan["scenes"]:
        if sc.get("photo"):
            ph = sc["photo"]
            keep = ("asset_id", "provider", "source_url", "download_url", "creator", "title", "license", "license_url",
                    "attribution", "attribution_required", "downloaded_at", "original_sha256", "format", "resolution",
                    "search_query", "topic", "job_id", "relevance", "final_score", "cache_hit", "treatment", "motion")
            asset_provenance.append({"scene": sc["scene_id"], "kind": "downloaded_photo", "sha256": ph["sha256"],
                                     **{k: ph.get(k) for k in keep if k in ph},
                                     "attribution_required": ph.get("attribution_required", True)})
        asset_provenance.append({"scene": sc["scene_id"], "kind": "procedural_vector",
                                 "icons": [sc["main_subject"], *sc["supporting_objects"]],
                                 "license": "original work (CrazzyDocs, drawn in code)"})
    return {
        "renderer": "lemo-opuscar", "engine": ENGINE_VERSION, "job_id": job_id, "topic": topic,
        "category": category, "duration_sec": plan["duration_sec"], "beat_count": len(plan["scenes"]),
        "demo_dir": str(film_dir), "index_html": str(film_dir / "index.html"), "plan_path": str(film_dir / "plan.json"),
        "fonts": {"files": fonts, "license": "SIL Open Font License 1.1 (bundled with Lemo-Opuscar)"},
        "asset_provenance": asset_provenance, "fingerprints": plan["fingerprints"],
        "unique_asset_count": len({a.get("sha256") for a in asset_provenance if a.get("sha256")}),
        "plan": plan,
    }
