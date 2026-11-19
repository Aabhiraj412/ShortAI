"""
Production vs test rendering parity (Stage 3.29).

Finding (2026-11-16): the approved test videos were rendered by `render_pop_info(..., select_photos=True,
key_entities=...)` (Pop Info engine 3.28, scene-level licensed photos, illustrated environments, kinetic
type, -14 LUFS, visual quality gate). Production never reached that code: the orchestrator calls
`active_visual_engine()`, which returns "legacy" because CRAZZYDOCS_VISUAL_ENGINE is set neither in the
daemon environment nor in .env.local, so every production video was built by the legacy
`LemoOpuscarFilmBuilder` (dark slates, clipped text, repeated diagrams). Even with the flag set, the
production branch passed topic-level photos and no `select_photos`/`key_entities`, so it would still
have differed from the approved configuration.

This module is the single definition of the approved configuration. Both the test-render script and
the production branch take their render options from `APPROVED_RENDER_OPTIONS`, and every render
records a parity report in its manifest; a mismatch is surfaced (log warning, manifest, dashboard),
never silently accepted. It does not switch the production engine: that stays an operator decision.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# Options passed to render_pop_info by BOTH the approved test renders and the production branch.
APPROVED_RENDER_OPTIONS: Dict[str, Any] = {"select_photos": True, "run_quality_gate": True}


def approved_config() -> Dict[str, Any]:
    from backend.app.rendering.duration_standards import ACCEPTED_DURATION_RANGE_SEC
    from backend.app.rendering.pop_info.scene_planner import ENGINE_REVISION, ENGINE_VERSION
    return {
        "renderer": "lemo-opuscar",
        "visual_engine": ENGINE_VERSION,
        "engine_revision": ENGINE_REVISION,
        "scene_photo_selection": True,
        "illustrated_environments": True,
        "visual_quality_gate": True,
        "resolution": "1080x1920",
        "fps": 30,
        "target_loudness_lufs": -14.0,
        "duration_window_sec": list(ACCEPTED_DURATION_RANGE_SEC),
    }


def production_config() -> Dict[str, Any]:
    """What a production render would use right now, read from the same sources the orchestrator reads."""
    from backend.app.rendering.duration_standards import ACCEPTED_DURATION_RANGE_SEC
    from backend.app.rendering.pop_info.pipeline import FEATURE_FLAG_ENV, active_visual_engine
    from backend.app.rendering.pop_info.scene_planner import ENGINE_REVISION
    engine = active_visual_engine()
    pop = engine != "legacy"
    flag_source = "environment" if os.environ.get(FEATURE_FLAG_ENV) else (".env.local or default")
    return {
        "renderer": "lemo-opuscar",
        "visual_engine": engine,
        "engine_revision": ENGINE_REVISION if pop else "legacy_film_builder",
        "scene_photo_selection": pop and bool(APPROVED_RENDER_OPTIONS["select_photos"]),
        "illustrated_environments": pop,
        "visual_quality_gate": pop,
        "resolution": "1080x1920",
        "fps": 30,
        "target_loudness_lufs": -14.0 if pop else None,
        "duration_window_sec": list(ACCEPTED_DURATION_RANGE_SEC),
        "feature_flag": {"name": FEATURE_FLAG_ENV, "value": engine, "source": flag_source},
    }


def parity_report(production: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    approved = approved_config()
    prod = production or production_config()
    diffs = [{"key": k, "approved": v, "production": prod.get(k)} for k, v in approved.items() if prod.get(k) != v]
    return {"matches": not diffs, "differences": diffs, "approved": approved, "production": prod,
            "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def log_parity(job_id: str) -> Dict[str, Any]:
    rep = parity_report()
    if rep["matches"]:
        logger.info("[PARITY] %s: production render configuration matches the approved test configuration.", job_id)
    else:
        logger.warning("[PARITY] %s: production render configuration DIFFERS from the approved test configuration: %s",
                       job_id, "; ".join(f"{d['key']}: approved={d['approved']!r} production={d['production']!r}"
                                         for d in rep["differences"]))
    return rep


def script_fingerprint(script_data: Dict[str, Any]) -> Dict[str, Any]:
    text = (script_data or {}).get("full_script") or " ".join(
        s.get("text", "") for s in (script_data or {}).get("sections") or [])
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "words": len(text.split()),
            "source": (script_data or {}).get("script_source"), "model": (script_data or {}).get("model")}


REQUIRED_MANIFEST_KEYS = ("job_id", "renderer", "visual_engine", "engine_revision", "configuration", "parity",
                          "script_fingerprint", "asset_provenance", "scene_types", "quality", "duration")


def write_render_manifest(path: Path, *, job_id: str, execution_context: str, visual_engine: str,
                          script_data: Dict[str, Any], film: Dict[str, Any], quality: Optional[Dict[str, Any]],
                          measured_duration_sec: Optional[float], video_path: str,
                          thumbnail: Optional[Dict[str, Any]] = None, parity: Optional[Dict[str, Any]] = None,
                          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Writes the per-video render manifest (every generated video has one, both engines)."""
    from backend.app.rendering.duration_standards import ACCEPTED_DURATION_RANGE_SEC, duration_accepted
    parity = parity or parity_report()
    plan = (film or {}).get("plan") or {}
    if not plan and (film or {}).get("plan_path"):   # the Pop Info result keeps the plan on disk only
        try:
            plan = json.loads(Path(film["plan_path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            plan = {}
    scenes = plan.get("scenes") or []
    if scenes:
        scene_types = [s.get("template") for s in scenes]
    else:   # legacy film builder: beats with a visual type
        scene_types = [b.get("visual_type") or b.get("type") or
                       ("legacy:animation" if "anim" in str(b.get("asset") or "") else "legacy:still")
                       for b in (film or {}).get("beats") or []]
    manifest = {
        "manifest_version": "render_manifest_v1",
        "job_id": job_id,
        "execution_context": execution_context,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "renderer": (film or {}).get("renderer") or "lemo-opuscar",
        "visual_engine": visual_engine,
        "engine_revision": plan.get("engine_revision") or ("legacy_film_builder" if visual_engine == "legacy" else None),
        "configuration": parity["production"],
        "parity": {"matches": parity["matches"], "differences": parity["differences"]},
        "script_fingerprint": script_fingerprint(script_data),
        "asset_provenance": (film or {}).get("asset_provenance") or [],
        "scene_types": scene_types,
        "visual_modes": (plan.get("fingerprints") or {}).get("visual_modes"),
        "creative_direction": {k: v for k, v in (plan.get("direction") or {}).items() if k != "principles"},
        "quality": quality or {},
        "duration": {"measured_sec": measured_duration_sec, "accepted_window_sec": list(ACCEPTED_DURATION_RANGE_SEC),
                     "accepted": duration_accepted(measured_duration_sec) if measured_duration_sec else None},
        "video_path": video_path,
        "thumbnail": thumbnail or {},
        **(extra or {}),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest
