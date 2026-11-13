"""
Creative engine status for the dashboard: engine flag and version, script and asset sources, API
configuration (present / absent only, never the value), and the visual quality reports produced
by the Pop Info engine for productions and controlled test renders.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.paths import MEDIA_DIR

REPORT_ROOTS = ("productions", "test_renders")


def _report_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def _reports() -> List[Path]:
    found: List[Path] = []
    for root in REPORT_ROOTS:
        base = Path(MEDIA_DIR) / root
        if base.is_dir():
            found += list(base.glob("**/pop_info_film/quality_report.json"))
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _summary(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"id": _report_id(path), "error": "unreadable report"}
    film = path.parent
    script_source = None
    for cand in film.parent.glob("scripts/script_*.json"):
        try:
            script_source = json.loads(cand.read_text(encoding="utf-8")).get("script_source")
        except (OSError, ValueError):
            pass
    issues = data.get("issues", [])
    return {
        "id": _report_id(path), "name": film.parent.name, "kind": "test" if "test_renders" in str(path) else "production",
        "video": Path(data.get("video", "")).name, "overall": data.get("overall_score"), "passed": data.get("passed"),
        "dimensions": {k: v.get("score") for k, v in (data.get("dimensions") or {}).items()},
        "issue_counts": {sev: sum(1 for i in issues if i.get("severity") == sev) for sev in ("critical", "major", "minor")},
        "script_source": script_source, "updated": path.stat().st_mtime,
    }


def report_detail(report_id: str) -> Optional[Dict[str, Any]]:
    for p in _reports():
        if _report_id(p) == report_id:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {"summary": _summary(p), "report": data}
    return None


def status() -> Dict[str, Any]:
    from backend.app.rendering.pop_info.pipeline import active_visual_engine
    from backend.app.rendering.pop_info.scene_planner import ENGINE_VERSION
    try:
        from backend.app.core.config import settings
        key_present = bool(settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY"))
        model = os.environ.get("GEMINI_MODEL") or settings.DEFAULT_GEMINI_MODEL
    except Exception:
        key_present, model = bool(os.environ.get("GEMINI_API_KEY")), os.environ.get("GEMINI_MODEL", "")
    reports = [_summary(p) for p in _reports()[:12]]
    last = reports[0] if reports else None
    failed_checks = [r for r in reports if r.get("passed") is False][:5]
    engine = active_visual_engine()
    return {
        "engine_version": ENGINE_VERSION,
        "active_engine": engine,
        "feature_flag": {"name": "CRAZZYDOCS_VISUAL_ENGINE", "value": engine,
                         "production_uses": "Pop Info engine" if engine != "legacy" else "legacy film builder (rollback default)"},
        "active_visual_style": "pop_info",
        "script_generation": {"writer": "grounded_script_writer", "gemini_key_configured": key_present, "model": model,
                              "source_when_unconfigured": "extractive_fallback (labelled)",
                              "live_validation": "BLOCKED: GEMINI_API_KEY not configured" if not key_present else "configured; see milestone report"},
        "asset_generation": {"illustrations": "procedural vector (drawn in code)", "photos": "licensed open-media acquisition (verified, relevance >= 0.5)",
                             "sound": "synthesized in code", "fonts": "Fredoka + Lilita One (SIL OFL)", "ai_images": "not used"},
        "estimated_cost": {"per_video_usd": 0.0 if not key_present else None,
                           "note": "no paid API is called without a Gemini key" if not key_present else "Gemini usage is billed by Google; measure in the AI Studio console"},
        "last_quality": last, "recent_failed_checks": failed_checks, "reports": reports,
    }
