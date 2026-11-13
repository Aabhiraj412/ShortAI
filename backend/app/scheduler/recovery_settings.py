"""
Recovery / retry policy settings (configurable without a code change).

Default policy is `unlimited_until_recovery_deadline` (user decision, 2026-11-14): a production
cycle is retried automatically, with backoff, until its recovery deadline. There is no fixed
attempt count. `bounded` keeps the previous fixed-count behaviour as a rollback.

Sources, later wins: built-in defaults -> config/recovery_policy.json -> CRAZZYDOCS_RETRY_POLICY env.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

POLICY_UNLIMITED = "unlimited_until_recovery_deadline"
POLICY_BOUNDED = "bounded"
POLICIES = (POLICY_UNLIMITED, POLICY_BOUNDED)


@dataclass
class RecoverySettings:
    retry_policy: str = POLICY_UNLIMITED
    # delay before automatic retry n (1-based); the last value repeats for every later attempt
    backoff_schedule_seconds: List[int] = field(default_factory=lambda: [60, 120, 240, 480, 900])
    jitter_fraction: float = 0.10          # +/-10%, deterministic per (job, attempt)
    min_attempt_seconds: int = 20 * 60     # never start an attempt with less recovery time left than this
    deterministic_cooldown_seconds: int = 15 * 60   # QC/config/code failures: wait at least this long...
    # ...and only retry after a meaningful change (new code revision or operator acknowledgement)
    bounded_max_retries: int = 3           # used only by the `bounded` rollback policy
    publish_max_attempts: int = 5          # publishing has its own counter; never restarts production
    operator_retry_allowance: int = 3      # manual retries per job; never limits automatic retries
    history_limit: int = 100

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _config_path() -> Path:
    from backend.app.core.paths import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "config" / "recovery_policy.json"


def load_settings() -> RecoverySettings:
    s = RecoverySettings()
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        for k, v in (data or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
    except (OSError, ValueError):
        pass
    env = os.environ.get("CRAZZYDOCS_RETRY_POLICY", "").strip()
    if env in POLICIES:
        s.retry_policy = env
    if s.retry_policy not in POLICIES:
        s.retry_policy = POLICY_UNLIMITED
    s.backoff_schedule_seconds = [max(1, int(x)) for x in (s.backoff_schedule_seconds or [60])]
    s.jitter_fraction = min(0.5, max(0.0, float(s.jitter_fraction)))
    return s


def save_settings(values: Dict[str, Any]) -> RecoverySettings:
    """Validated save (dashboard / operator). Unknown keys are rejected."""
    s = load_settings()
    unknown = set(values) - set(asdict(s))
    if unknown:
        raise ValueError(f"Unknown recovery settings: {sorted(unknown)}")
    if "retry_policy" in values and values["retry_policy"] not in POLICIES:
        raise ValueError(f"retry_policy must be one of {POLICIES}")
    if "backoff_schedule_seconds" in values:
        sched = [int(x) for x in values["backoff_schedule_seconds"]]
        if not sched or any(x < 10 or x > 3600 for x in sched):
            raise ValueError("backoff_schedule_seconds must be 1+ values between 10 and 3600")
    if "min_attempt_seconds" in values and not 300 <= int(values["min_attempt_seconds"]) <= 7200:
        raise ValueError("min_attempt_seconds must be between 300 and 7200")
    for k, v in values.items():
        setattr(s, k, v)
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(p)
    return load_settings()
