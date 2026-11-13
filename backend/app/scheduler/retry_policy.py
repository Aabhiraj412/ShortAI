"""
Retry policy for production cycle jobs.

Policy `unlimited_until_recovery_deadline` (default since 2026-11-14, see recovery_settings):
- There is no fixed attempt count. A failed attempt schedules another one, with exponential
  backoff (1, 2, 4, 8, then 15 min; bounded jitter), for as long as the cycle's recovery window
  leaves time for a real attempt (min_attempt_seconds). The recovery deadline is the only stop.
- Transient failures (API, network, provider, quota, timeouts, renderer hiccups) simply back off.
- Deterministic failures (QC blocks, invalid research/script, configuration and code defects,
  missing credentials) never loop: the retry waits for a cooldown AND a meaningful change, i.e. a
  new deployed code revision or an operator acknowledgement. The same inputs are never re-run
  against the same code that already proved it cannot succeed.
- A failure after a valid video was rendered is never re-run (the video would be regenerated):
  POST_RENDER_FAILURE stays terminal with the artifacts preserved.
- Publishing (queue handoff) failures use their own counter and never restart production.
- Every attempt is appended to cycle_state.attempt_history in checkpoint_data_json, so retry
  state and history survive process restarts.

Policy `bounded` keeps the previous behaviour (max_retries attempts) as a rollback.

Terminal statuses: FAILED (non-retryable), RECOVERY_WINDOW_EXPIRED (no time left).
Pending statuses: RETRY_SCHEDULED (production), HANDOFF_PENDING (publishing only).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.app.scheduler.recovery_settings import POLICY_UNLIMITED, RecoverySettings, load_settings

PERMANENT = "PERMANENT"
TRANSIENT = "TRANSIENT"
DETERMINISTIC = "DETERMINISTIC"          # same inputs + same code => same failure (QC, validation)
POST_RENDER_FAILURE = "POST_RENDER_FAILURE"
QUEUE_HANDOFF_FAILURE = "QUEUE_HANDOFF_FAILURE"
# Render failed after its inputs were prepared. Deterministic render failures need a change;
# others are retried and reuse the cached script/narration/media/storyboard.
RENDER_FAILURE = "RENDER_FAILURE"

STATUS_RETRY_SCHEDULED = "RETRY_SCHEDULED"
STATUS_HANDOFF_PENDING = "HANDOFF_PENDING"
STATUS_FAILED = "FAILED"
STATUS_RECOVERY_EXPIRED = "RECOVERY_WINDOW_EXPIRED"
PENDING_STATUSES = frozenset({STATUS_RETRY_SCHEDULED, STATUS_HANDOFF_PENDING})
TERMINAL_STATUSES = frozenset({"FAILED", "BLOCKED_QC", "MISSED", "RECOVERY_WINDOW_EXPIRED", "COMPLETED", "PUBLISHED"})

DEFAULT_MAX_RETRIES = 3                   # `bounded` policy only
PUBLISH_MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 120                # `bounded` policy only
BACKOFF_CAP_SECONDS = 1800

PERMANENT_ERROR_TYPES = frozenset({
    "NameError", "ImportError", "ModuleNotFoundError", "AttributeError", "TypeError", "SyntaxError",
    "IndentationError", "UnboundLocalError", "NotImplementedError", "AssertionError", "KeyError",
    "PermissionError", "RecursionError",
})
PERMANENT_MESSAGE_MARKERS = (
    "security gate enforced",
    "integrity gate",
    "invalid_grant",
    "quota exceeded for quota metric",
    "missing credentials",
    "client_secrets",
)
DETERMINISTIC_MESSAGE_MARKERS = (
    "blocked_qc",
    "check_a_topic_narration",
    "invalid research",
    "script validation",
)

POST_RENDER_STAGES = frozenset({"RENDERING", "THUMBNAIL", "QUALITY_CHECK", "SEO", "COMPLETED", "PUBLISHING_QUEUE_HANDOFF"})
MIN_VALID_VIDEO_BYTES = 1024
# code/config defects and deterministic failures: retry only after a meaningful change
CHANGE_REQUIRED = frozenset({PERMANENT, DETERMINISTIC})
# When this process loaded its code; failures recorded earlier ran on older code.
PROCESS_STARTED_UTC = datetime.utcnow()


def classify_error(exc: Optional[BaseException] = None, *, error_type: str = "", message: str = "") -> tuple[str, str]:
    """Returns (PERMANENT|DETERMINISTIC|TRANSIENT, error_type)."""
    etype = type(exc).__name__ if exc is not None else (error_type or "")
    msg = (str(exc) if exc is not None else message or "").lower()
    if etype in PERMANENT_ERROR_TYPES:
        return PERMANENT, etype
    if any(marker in msg for marker in PERMANENT_MESSAGE_MARKERS):
        return PERMANENT, etype or "Error"
    if etype in ("QCBlocked",) or any(marker in msg for marker in DETERMINISTIC_MESSAGE_MARKERS):
        return DETERMINISTIC, etype or "Error"
    return TRANSIENT, etype or "Error"


def current_code_revision() -> Optional[str]:
    try:
        from backend.app.core.code_revision import LOADED_CODE_REVISION
        return LOADED_CODE_REVISION
    except Exception:
        return None


def _jitter(job_id: str, attempt: int, fraction: float) -> float:
    """Deterministic jitter in [-fraction, +fraction] so a restart recomputes the same delay."""
    h = int(hashlib.sha256(f"{job_id}|{attempt}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (2 * h - 1) * fraction


def backoff_seconds(attempt: int, *, job_id: str = "", settings: Optional[RecoverySettings] = None) -> int:
    """Delay before automatic retry number `attempt` (1-based)."""
    s = settings or load_settings()
    attempt = max(1, int(attempt))
    if s.retry_policy != POLICY_UNLIMITED:
        return int(min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))))
    sched = s.backoff_schedule_seconds
    base = sched[min(attempt, len(sched)) - 1]
    return max(1, int(round(base * (1 + _jitter(job_id, attempt, s.jitter_fraction)))))


def _load_checkpoint(job) -> dict[str, Any]:
    try:
        return json.loads(job.checkpoint_data_json or "{}")
    except (TypeError, ValueError):
        return {}


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def recovery_deadline_utc(job) -> Optional[datetime]:
    """The cycle's recovery expiration (cycle_state.recovery_expiration_at), as naive UTC."""
    raw = (_load_checkpoint(job).get("cycle_state") or {}).get("recovery_expiration_at")
    if not raw:
        return None
    try:
        return _utc_naive(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def rendered_video_path(job) -> Optional[str]:
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


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def record_failure(job, exc: Optional[BaseException] = None, *, now_utc: Optional[datetime] = None,
                   error_type: str = "", message: str = "", code_revision: Optional[str] = None,
                   settings: Optional[RecoverySettings] = None) -> dict[str, Any]:
    """Applies the retry policy to a failed attempt and mutates `job` in place (caller commits)."""
    s = settings or load_settings()
    unlimited = s.retry_policy == POLICY_UNLIMITED
    now_utc = now_utc or datetime.utcnow()
    code_revision = code_revision or current_code_revision()
    classification, etype = classify_error(exc, error_type=error_type, message=message)
    msg = str(exc) if exc is not None else (message or "")
    max_retries = int(job.max_retries if job.max_retries is not None else DEFAULT_MAX_RETRIES)
    preserved_video = rendered_video_path(job)
    cp = _load_checkpoint(job)
    cs = cp.setdefault("cycle_state", {})
    is_handoff = bool(preserved_video and str(job.current_stage or "").upper() == "PUBLISHING_QUEUE_HANDOFF"
                      and cp.get("queue_handoff"))
    if is_handoff:
        attempts = int(cs.get("publish_attempts", 0)) + 1    # publishing never consumes production attempts
        cs["publish_attempts"] = attempts
        max_retries = int(s.publish_max_attempts)
        classification = QUEUE_HANDOFF_FAILURE
    else:
        job.retry_count = int(job.retry_count or 0) + 1
        attempts = job.retry_count
        if preserved_video:
            classification = POST_RENDER_FAILURE

    render_failure = exc is not None and type(exc).__name__ == "RenderFailure"
    render_terminal = False
    if render_failure and not is_handoff and not preserved_video:
        classification = RENDER_FAILURE
        render_terminal = bool(getattr(exc, "deterministic", False))
    if preserved_video:
        cs["preserved_artifacts"] = {"video_path": preserved_video}
    elif render_failure and getattr(exc, "preserved", None):
        cs["preserved_artifacts"] = dict(exc.preserved)

    requires_change = classification in CHANGE_REQUIRED or (classification == RENDER_FAILURE and render_terminal)
    deadline = recovery_deadline_utc(job)
    delay: Optional[int] = None
    status = STATUS_FAILED
    if is_handoff:
        retry_allowed = attempts < max_retries
        if retry_allowed:
            delay = backoff_seconds(attempts, job_id=job.id or "", settings=s)
            status = STATUS_HANDOFF_PENDING
        summary = (f"queue handoff {attempts}/{max_retries} failed; retry in {max(1, (delay or 0) // 60)} min"
                   if retry_allowed else
                   f"queue handoff failed {attempts}/{max_retries} times; video preserved at {preserved_video} for a manual handoff")
    elif classification == POST_RENDER_FAILURE:
        retry_allowed = False
        summary = f"failed after rendering; not retried to avoid regenerating the video. Preserved: {preserved_video}"
    elif unlimited and deadline is not None:
        delay = backoff_seconds(attempts, job_id=job.id or "", settings=s)
        if requires_change:
            delay = max(delay, int(s.deterministic_cooldown_seconds))
        next_at = now_utc + timedelta(seconds=delay)
        if next_at + timedelta(seconds=int(s.min_attempt_seconds)) > deadline:
            retry_allowed = False
            status = STATUS_RECOVERY_EXPIRED
            summary = (f"attempt {attempts} failed; no time for another attempt before the recovery deadline "
                       f"{_iso(deadline)} (needs {int(s.min_attempt_seconds) // 60} min)")
            delay = None
        else:
            retry_allowed = True
            status = STATUS_RETRY_SCHEDULED
            summary = (f"attempt {attempts} failed; retry in {max(1, delay // 60)} min "
                       f"(no attempt limit; recovery deadline {_iso(deadline)})")
            if requires_change:
                summary += "; waits for a code change or operator acknowledgement (same inputs would fail again)"
    else:
        # bounded rollback policy, or a job without a recorded recovery deadline
        retry_allowed = (classification in (TRANSIENT,) or (classification == RENDER_FAILURE and not render_terminal)) \
            and attempts < max_retries
        if retry_allowed:
            delay = backoff_seconds(attempts, job_id=job.id or "", settings=s)
            status = STATUS_RETRY_SCHEDULED
            summary = f"attempt {attempts}/{max_retries} failed; retry in {max(1, delay // 60)} min"
        elif classification == PERMANENT:
            summary = "permanent error; not retried"
        elif classification == RENDER_FAILURE and render_terminal:
            summary = ("render failed the same way on every in-stage attempt; not retried. Render inputs preserved: "
                       + ", ".join(sorted(cs.get("preserved_artifacts", {}))))
        else:
            summary = f"retries exhausted ({attempts}/{max_retries})"

    job.status = status
    if retry_allowed and delay is not None:
        cs["next_retry_at_utc"] = _iso(now_utc + timedelta(seconds=delay))
        cs["retry_backoff_seconds"] = delay
    else:
        cs.pop("next_retry_at_utc", None)
    cs["requires_change"] = bool(retry_allowed and requires_change)
    cs["failure_code_revision"] = code_revision
    cs.pop("change_acknowledged", None)
    cs["retry_policy"] = s.retry_policy
    cs["error_classification"] = classification
    cs["last_error_type"] = etype
    cs["last_error_message"] = msg[:2000]
    cs["last_failure_at_utc"] = _iso(now_utc)
    cs["retry_terminal"] = not retry_allowed
    hist = cs.setdefault("attempt_history", [])
    hist.append({"attempt": attempts, "kind": "publish" if is_handoff else "production",
                 "failed_at_utc": _iso(now_utc), "classification": classification, "error_type": etype,
                 "message": msg[:300], "code_revision": code_revision, "outcome": status,
                 "next_retry_at_utc": cs.get("next_retry_at_utc")})
    cs["attempt_history"] = hist[-int(s.history_limit):]
    job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)
    job.error_details = f"[{classification}] {etype}: {msg}"[:4000] + f" ({summary})"
    return {"status": job.status, "classification": classification, "error_type": etype, "attempts": attempts,
            "max_retries": None if (unlimited and not is_handoff and deadline is not None) else max_retries,
            "retry_policy": s.retry_policy, "retry_in_seconds": delay, "terminal": not retry_allowed,
            "requires_change": cs["requires_change"], "recovery_deadline_utc": _iso(deadline) if deadline else None,
            "summary": summary}


def next_retry_at(job) -> Optional[datetime]:
    raw = (_load_checkpoint(job).get("cycle_state") or {}).get("next_retry_at_utc")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).rstrip("Z"))
    except ValueError:
        return None


def change_satisfied(cs: dict, code_revision: Optional[str]) -> bool:
    """A deterministic failure may be retried once the code changed or an operator acknowledged a fix."""
    if not cs.get("requires_change"):
        return True
    if cs.get("change_acknowledged"):
        return True
    failed_rev = cs.get("failure_code_revision")
    return bool(code_revision and failed_rev and code_revision != failed_rev)


def is_retry_due(job, now_utc: Optional[datetime] = None, *, code_revision: Optional[str] = None,
                 settings: Optional[RecoverySettings] = None) -> bool:
    """True when a RETRY_SCHEDULED/HANDOFF_PENDING job may be dispatched now."""
    if str(job.status or "") not in PENDING_STATUSES:
        return False
    s = settings or load_settings()
    now_utc = now_utc or datetime.utcnow()
    cs = _load_checkpoint(job).get("cycle_state") or {}
    if str(job.status) == STATUS_HANDOFF_PENDING:
        if int(cs.get("publish_attempts", 0)) >= int(s.publish_max_attempts):
            return False
    elif s.retry_policy == POLICY_UNLIMITED and recovery_deadline_utc(job) is not None:
        deadline = recovery_deadline_utc(job)
        if now_utc + timedelta(seconds=int(s.min_attempt_seconds)) > deadline:
            return False   # not enough time left; the scheduler expires the cycle at the deadline
        if not change_satisfied(cs, code_revision or current_code_revision()):
            return False
    else:
        max_retries = int(job.max_retries if job.max_retries is not None else DEFAULT_MAX_RETRIES)
        if int(job.retry_count or 0) >= max_retries:
            return False
    due = next_retry_at(job)
    return due is None or due <= now_utc


def rearm_if_recoverable(job, *, now_utc: Optional[datetime] = None, code_revision: Optional[str] = None,
                         process_started_utc: Optional[datetime] = None,
                         settings: Optional[RecoverySettings] = None) -> Optional[str]:
    """
    A FAILED job made terminal by an attempt-count limit or a deterministic error is re-armed
    (status RETRY_SCHEDULED) under the unlimited policy when: its recovery window still leaves time
    for an attempt; it never produced, queued or uploaded a video; and, for deterministic or
    permanent failures, the code has changed since the failure. Returns the reason, or None.
    """
    s = settings or load_settings()
    if s.retry_policy != POLICY_UNLIMITED or str(job.status or "") != STATUS_FAILED:
        return None
    if (job.id or "").startswith(("test_", "mock_")) or job.youtube_video_id:
        return None
    now_utc = now_utc or datetime.utcnow()
    cp = _load_checkpoint(job)
    cs = cp.setdefault("cycle_state", {})
    if cp.get("queue_handoff") or cp.get("queue_ledger_id") or rendered_video_path(job):
        return None
    classification = cs.get("error_classification")
    if classification in (POST_RENDER_FAILURE, QUEUE_HANDOFF_FAILURE):
        return None
    deadline = recovery_deadline_utc(job)
    if deadline is None or now_utc + timedelta(seconds=int(s.min_attempt_seconds)) > deadline:
        return None
    reclass, _ = classify_error(error_type=str(cs.get("last_error_type") or ""),
                                message=str(cs.get("last_error_message") or job.error_details or ""))
    deterministic = reclass in CHANGE_REQUIRED or classification in CHANGE_REQUIRED
    code_revision = code_revision or current_code_revision()
    if deterministic:
        failed_rev = cs.get("failure_code_revision")
        changed = (failed_rev is not None and code_revision is not None and failed_rev != code_revision)
        if failed_rev is None:
            # failure recorded before revisions were tracked: changed if this process loaded newer code
            last_fail = cs.get("last_failure_at_utc")
            try:
                lf = datetime.fromisoformat(str(last_fail).rstrip("Z")) if last_fail else None
            except ValueError:
                lf = None
            changed = bool(lf and process_started_utc and lf < process_started_utc)
        if not changed:
            return None
        reason = "deterministic failure; code changed since the failure"
    else:
        reason = "attempt limit removed by the unlimited-until-deadline policy"
    job.status = STATUS_RETRY_SCHEDULED
    job.recovery_reason = "POLICY_REARMED"
    cs["next_retry_at_utc"] = _iso(now_utc)
    cs["retry_terminal"] = False
    cs["requires_change"] = False
    cs["error_classification"] = DETERMINISTIC if deterministic else (classification or TRANSIENT)
    cs["retry_policy"] = s.retry_policy
    cs.setdefault("attempt_history", []).append({"attempt": int(job.retry_count or 0), "kind": "rearm",
                                                "at_utc": _iso(now_utc), "reason": reason,
                                                "code_revision": code_revision})
    job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)
    job.error_details = (job.error_details or "")[:3500] + f" | re-armed: {reason}"
    return reason


def acknowledge_change(job, *, now_utc: Optional[datetime] = None) -> None:
    """Operator says the cause was fixed: the next automatic retry may run now."""
    cp = _load_checkpoint(job)
    cs = cp.setdefault("cycle_state", {})
    cs["change_acknowledged"] = True
    cs["next_retry_at_utc"] = _iso(now_utc or datetime.utcnow())
    job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)


def clear_failure_state(job) -> None:
    """Called on success so a COMPLETED job never carries a stale error (history is kept)."""
    job.error_details = None
    cp = _load_checkpoint(job)
    cs = cp.get("cycle_state")
    if isinstance(cs, dict):
        for key in ("next_retry_at_utc", "retry_backoff_seconds", "error_classification", "last_error_type",
                    "last_error_message", "retry_terminal", "requires_change", "change_acknowledged"):
            cs.pop(key, None)
        job.checkpoint_data_json = json.dumps(cp, ensure_ascii=True)
