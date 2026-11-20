"""Signal collection: one bounded, cached Stage 3.32A read per window. No new analytics queries.

The sections are the Operational Intelligence outputs (already aggregated); the bundle gives row-level access to
the same rows those sections were computed from (jobs, discovery runs, productions) for rules that need per-job
detail. Nothing here writes anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

SECTION_NAMES = ("production_health", "failure_intelligence", "renderer_reliability", "topic_intelligence",
                 "api_intelligence", "recovery_analytics")
COMPLETENESS = {"actual": 1.0, "partial": 0.8, "unavailable": 0.0}


@dataclass
class Signals:
    window_days: int
    now: datetime
    sections: Dict[str, Dict[str, Any]]
    bundle: Dict[str, Any]
    failures: List[Dict[str, Any]]
    recovery_minutes_by_cycle: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    # Stage 3.32D outcome data (read-only): {"learning": [...], "deterministic": [...]}; None when unavailable.
    self_healing: Optional[Dict[str, Any]] = None

    def metrics(self, name: str) -> Dict[str, Any]:
        return (self.sections.get(name) or {}).get("metrics") or {}

    def status(self, name: str) -> str:
        return (self.sections.get(name) or {}).get("status") or "unavailable"

    def available(self, name: str) -> bool:
        return self.status(name) in ("actual", "partial") and bool(self.metrics(name))

    def completeness(self, name: str) -> float:
        return COMPLETENESS.get(self.status(name), 0.0)

    def availability(self) -> Dict[str, str]:
        return {n: self.status(n) for n in SECTION_NAMES}


def _default_recovery_minutes() -> Dict[str, int]:
    """Recovery window per cycle from the ACTIVE production schedule (read-only)."""
    from backend.app.services import schedule_service
    spec = schedule_service.active_spec() or {}
    out = {}
    for c in spec.get("cycles") or []:
        try:
            out[str(c.get("cycle_id"))] = int(c.get("recovery_duration_minutes") or 240)
        except (TypeError, ValueError):
            continue
    return out


def collect(intelligence: Any, days: int = 7, now: Optional[datetime] = None,
            schedule_provider: Optional[Callable[[], Dict[str, int]]] = None,
            learning_provider: Optional[Callable[[], Dict[str, Any]]] = None) -> Signals:
    now = now or datetime.now(timezone.utc)
    errors: Dict[str, str] = {}
    sections: Dict[str, Dict[str, Any]] = {}
    for name in SECTION_NAMES:
        try:
            sections[name] = getattr(intelligence, name)(days=days)
        except Exception as exc:                                   # one section failing never hides the others
            sections[name] = {"status": "unavailable", "metrics": {}, "unavailable": {"all": type(exc).__name__}}
            errors[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        bundle = intelligence.bundle(days)
        failures = intelligence._failures(bundle) if bundle.get("db_available") else []
    except Exception as exc:
        bundle, failures = {"jobs": [], "runs": [], "productions": [], "registry": []}, []
        errors["bundle"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        recovery = (schedule_provider or _default_recovery_minutes)()
    except Exception as exc:
        recovery = {}
        errors["schedule"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    learning = None
    if learning_provider is not None:
        try:
            learning = learning_provider()
        except Exception as exc:
            errors["self_healing"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return Signals(window_days=days, now=now, sections=sections, bundle=bundle, failures=failures,
                   recovery_minutes_by_cycle=recovery, errors=errors, self_healing=learning)
