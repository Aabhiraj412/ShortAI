"""Render-stage failure type shared by the orchestrator and the scheduler retry policy."""
from __future__ import annotations

from typing import Any, Dict, Optional

# Lemo-Opuscar's page.mjs prints this when window.READY never became true.
READINESS_MARKERS = ("window.ready never became true", "still waiting for window.ready")


def is_readiness_failure(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in READINESS_MARKERS)


class PublishingStepError(RuntimeError):
    """An upload/scheduling/thumbnail step after QC must be retried (never the render)."""


class RenderFailure(RuntimeError):
    """
    The video render failed after its inputs (script, narration, media, storyboard)
    were produced. Those inputs are preserved and reused on any later attempt.

    deterministic=True means every in-stage attempt failed the same way (for example
    the page never signalled readiness): rerunning cannot help, so the scheduler must
    not spend whole-pipeline retries on it.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        readiness: bool,
        deterministic: bool,
        preserved: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.readiness = readiness
        self.deterministic = deterministic
        self.preserved = dict(preserved or {})
