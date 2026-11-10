"""
Hybrid Dual-Engine Rendering System.

Combines Headless Chromium Canvas rendering for rich animations with
fast Python Pillow rendering for photographic imagery and universal FFmpeg composition.
"""

from backend.app.rendering.engine import HybridVisualEngine
from backend.app.rendering.types import (
    EngineType,
    RenderTelemetry,
    SceneBeat,
    SegmentResult,
    StoryboardPlan,
    VisualModality,
)

__all__ = [
    "HybridVisualEngine",
    "EngineType",
    "VisualModality",
    "SceneBeat",
    "StoryboardPlan",
    "SegmentResult",
    "RenderTelemetry",
]
