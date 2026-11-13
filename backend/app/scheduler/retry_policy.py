"""
Bounded retry policy for production cycle jobs.

Rules
- Every failed attempt increments JobModel.retry_count exactly once (record_failure).
- PERMANENT errors (code/config defects such as NameError) are never retried.
- TRANSIENT errors are retried with exponential backoff until max_retries is reached.
- A failure after a valid video was rendered is never auto-retried: re-running the
  pipeline would regenerate the video. The artifacts are preserved and the job is
  terminal with error_classification POST_RENDER_FAILURE.
- Terminal state is status FAILED. Pending retries use status RETRY_SCHEDULED with
  cycle_state.next_retry_at_utc; nothing dispatches them before that time.

All state lives in existing columns (status, retry_count, max_retries, error_details)
and in checkpoint_data_json["cycle_state"], so no schema migration is needed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

PERMANENT = "PERMANENT"
TRANSIENT = "TRANSIENT"
POST_RENDER_FAILURE = "POST_RENDER_FAILURE"
QUEUE_HANDOFF_FAILURE = "QUEUE_HANDOFF_FAILURE"
# Render failed after its inputs were prepared. Deterministic (every in-stage render
# attempt failed the same way, e.g. the page never signalled readiness): terminal, inputs
# preserved, no whole-pipeline retry. Otherwise retried; the retry reuses the cached
# script/narration/media/storyboard, so only the render runs again.
RENDER_FAILURE = "RENDER_FAILURE"

STATUS_RETRY_SCHEDULED = "RETRY_SCHEDULED"
# Video rendered and QC-passed; only the Publishing Queue insert is retried.
STATUS_HANDOFF_PENDING = "HANDOFF_PENDING"
STATUS_FAILED = "FAILED"
PENDING_STATUSES = frozenset({STATUS_RETRY_SCHEDULED, STATUS_HANDOFF_PENDING})
TERMINAL_STATUSES = frozenset({"FAILED", "BLOCKED_QC", "MISSED", "RECOVERY_WINDOW_EXPIRED", "COMPLETED", "PUBLISHED"})

DEFAULT_MAX_RETRIES = 3
# Publishing (queue insert / upload / schedule / thumbnail) has its own counter and limit,
# separate from production attempts (cycle_state.publish_attempts).
PUBLISH_MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 120
BACKOFF_CAP_SECONDS = 1800

# Programming / configuration defects: retrying runs the same broken code again.
PERMANENT_ERROR_TYPES = frozenset({
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
    "AttributeError",
    "TypeError",
    "SyntaxError",
    "IndentationError",
    "UnboundLocalError",
    "NotImplementedError",
    "AssertionError",
    "KeyError",
    "PermissionError",
    "RecursionError",
})
PERMANENT_MESSAGE_MARKERS = (
    "security gate enforced",
    "integrity gate",
    "invalid_grant",
    "quota exceeded for quota metric",
)

# Stages at or after which a rendered video exists (orchestrator checkpoint names).
POST_RENDER_STAGES = frozenset({"RENDERING", "THUMBNAIL", "QUALITY_CHECK", "SEO", "COMPLETED", "PUBLISHING_QUEUE_HANDOFF"})
MIN_VALID_VIDEO_BYTES = 1024


def classify_error(exc: Optional[BaseException] = None, *, error_type: str = "", message: str = "") -> tuple[str, str]:
    """Returns (PERMANENT|TRANSIENT, error_type)."""
    etype = type(exc).__name__ if exc is not None else (error_type or "")
    msg = (str(exc) if exc is not None else message or "").lower()
    if etype in PERMANENT_ERROR_TYPES:
        return PERMANENT, etype
    if any(marker in msg for marker in PERMANENT_MESSAGE_MARKERS):
        return PERMANENT, etype or "Error"
    return TRANSIENT, etype or "Error"


def backoff_seconds(attempt: int) -> int:
    """Backoff before retry number `attempt` (1-based): 2m, 4m, 8m, ... capped at 30m."""
    attempt = max(1, int(attempt))
    return int(min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))))


def _load_checkpoint(job) -> dict[str, Any]:
    try:
        return json.loads(job.checkpoint_data_json or "{}")
    except (TypeError, ValueError):
        return {}


def rendered_video_path(job) -> Optional[str]:
    """Path of a valid rendered video if the job already passed the render stage."""
    cp = _load_checkpoint(job)
    path = cp.get("video_path")
    stage = str(job.current_stage or "").upper()
    if stage not in POST_RENDER_STAGES or not path:
        return None
    try:
        if os.path.isfile(path) and os.path.getsize(path) > MIN_VALID_VIDEO_BYTES:
            return path
    except OSError:
        return None
    return None


def record_failure(job, exc: Optional[BaseException] = None, *, now_utc: Optional[datetime] = None,
                   error_type: str = "", message: str = "") -> dict[str, Any]:
    """
    Applies the retry policy to a failed attempt and mutates `job` in place
    (caller commits). Returns the decision.
    """
    now_utc = now_utc or datetime.utcnow()
    classification, etype = classify_error(exc, error_type=error_type, message=message)
    msg = str(exc) if exc is not None else (message or "")
    max_retries = int(job.max_retries if job.max_retries is not None else DEFAULT_MAX_RETRIES)
    preserved_video = rendered_video_path(job)
    cp = _load_checkpoint(job)
    is_handoff = bool(
        preserved_video
        and str(job.current_stage or "").upper() == "PUBLISHING_QUEUE_HANDOFF"
        and cp.get("queue_handoff")
    )
    if is_handoff:
        # Publishing failures never consume production attempts.
        pub_cs = cp.setdefault("cycle_state", {})
        attempts = int(pub_cs.get("publish_attempts", 0)) + 1
        pub_cs["publish_attempts"] = attempts
        max_retries = PUBLISH_MAX_ATTEMPTS
    else:
        job.retry_count = int(job.retry_count or 0) + 1
        attempts = job.retry_count
    if is_handoff:
        classification = QUEUE_HANDOFF_FAILURE
    elif preserved_video:
        classification = POST_RENDER_FAILURE

    render_failure = exc is not None and type(exc).__name__ == "RenderFailure"
    render_terminal = False
    if render_failure and not is_handoff and not preserved_video:
        classification = RENDER_FAILURE
        render_terminal = bool(getattr(exc, "deterministic", False))

    cs = cp.setdefault("cycle_state", {})
    retry_allowed = (
        (classification in (TRANSIENT, QUEUE_HANDOFF_FAILURE) or (classification == RENDER_FAILURE and not render_terminal))
        and attempts < max_retries
    )
    if preserved_video:
        cs["preserved_artifacts"] = {"video_path": preserved_video}
    elif render_failure and getattr(exc, "preserved", None):
        cs["preserved_artifacts"] = dict(exc.preserved)

    if retry_allowed:
        delay = backoff_seconds(attempts)
        next_at = now_utc + timedelta(seconds=delay)
        job.status = STATUS_HANDOFF_PENDING if is_handoff else STATUS_RETRY_SCHEDULED
        cs["next_retry_at_utc"] = next_at.replace(microsecond=0).isoformat() + "Z"
        cs["retry_backoff_seconds"] = delay
        what = "queue handoff" if is_handoff else "attempt"
        summary = f"{what} {attempts}/{max_retries} failed; retry in {delay // 60} min"
    else:
        delay = None
        job.status = STATUS_FAILED
        cs.pop("next_retry_at_utc", None)
        if classification == POST_RENDER_FAILURE:
            summary = (
                f"failed after rendering; not retried to avoid regenerating the video. "
                f"Preserved: {preserved_video}"
            )
        elif classification == QUEUE_HANDOFF_FAILURE:
            summary = (
                f"queue handoff failed {attempts}/{max_retries} times; video preserved at "
                f"{preserved_video} for a manual handoff"
            )
        elif classification == PERMANENT:
            summary = "permanent error; not retried"
        elif classification == RENDER_FAILURE and render_terminal:
            summary = (
                f"render failed the same way on every in-stage attempt; not retried. "
                f"Render inputs preserved: {', '.join(sorted(cs.get('preserved_artifacts', {})))}"
            )
        else:
            summary = f"retries exhausted ({attempts}/{max_retries})"

    cs["error_classification"] = classification
    cs["last_error_type"] = etype
    cs["last_error_message"] = msg[:2000]
    cs["last_failure_at_utc"] = now_utc.replace(microsecond=0).isoformat() + "Z"
    cs["retry_terminal"] = not retry_allowed
    job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)
    job.error_details = f"[{classification}] {etype}: {msg}"[:4000] + f" ({summary})"
    return {
        "status": job.status,
        "classification": classification,
        "error_type": etype,
        "attempts": attempts,
        "max_retries": max_retries,
        "retry_in_seconds": delay,
        "terminal": not retry_allowed,
        "summary": summary,
    }


def next_retry_at(job) -> Optional[datetime]:
    raw = (_load_checkpoint(job).get("cycle_state") or {}).get("next_retry_at_utc")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).rstrip("Z"))
    except ValueError:
        return None


def is_retry_due(job, now_utc: Optional[datetime] = None) -> bool:
    """True only for RETRY_SCHEDULED/HANDOFF_PENDING jobs whose backoff has elapsed and attempts remain."""
    if str(job.status or "") not in PENDING_STATUSES:
        return False
    if str(job.status) == STATUS_HANDOFF_PENDING:
        cs = _load_checkpoint(job).get("cycle_state") or {}
        if int(cs.get("publish_attempts", 0)) >= PUBLISH_MAX_ATTEMPTS:
            return False
    else:
        max_retries = int(job.max_retries if job.max_retries is not None else DEFAULT_MAX_RETRIES)
        if int(job.retry_count or 0) >= max_retries:
            return False
    due = next_retry_at(job)
    return due is None or due <= (now_utc or datetime.utcnow())


def clear_failure_state(job) -> None:
    """Called on success so a COMPLETED job never carries a stale error."""
    job.error_details = None
    cp = _load_checkpoint(job)
    cs = cp.get("cycle_state")
    if isinstance(cs, dict):
        for key in ("next_retry_at_utc", "retry_backoff_seconds", "error_classification",
                    "last_error_type", "last_error_message", "retry_terminal"):
            cs.pop(key, None)
        job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)
