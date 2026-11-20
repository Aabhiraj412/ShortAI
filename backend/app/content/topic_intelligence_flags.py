"""Stage 3.31 topic-intelligence feature flags.

Every flag defaults to OFF, so deploying this code changes nothing: production keeps the
approved selection path until someone adds a ``topic_intelligence`` block to
``backend/data/creative_policy_config.json`` (the existing creative-policy config file).

Modes
-----
* ``off``    - the feature does not run (default for every flag).
* ``shadow`` - the feature runs and is recorded in the candidate ledger, but never changes which
               topic is selected. Only flags where that is meaningful accept it.
* ``on``     - the feature takes part in selection.

Example config block (not present in the live file)::

    "topic_intelligence": {
        "candidate_ledger": "on",
        "category_classifier_v2": "shadow",
        "production_query_rotation": "off",
        "gnews_candidates": "off",
        "wikipedia_in_the_news": "off",
        "exhaustion_recovery": "off",
        "exhaustion_limits": {"max_attempts": 4, "max_requests": 60, "max_elapsed_seconds": 180,
                              "max_candidates": 24, "min_request_interval_seconds": 0.2}
    }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

OFF, SHADOW, ON = "off", "shadow", "on"
_FLAG_MODES = {
    "candidate_ledger": (OFF, ON),
    "category_classifier_v2": (OFF, SHADOW, ON),
    "production_query_rotation": (OFF, ON),
    "gnews_candidates": (OFF, ON),
    "wikipedia_in_the_news": (OFF, ON),
    "exhaustion_recovery": (OFF, ON),
    # Stage 3.31K (demand-led discovery)
    "demand_signals": (OFF, SHADOW, ON),       # trending/public-interest signals as a candidate source
    "scoring_v2": (OFF, SHADOW, ON),           # multi-signal topic score (demand, velocity, novelty, ...)
    "expanded_categories": (OFF, ON),          # cinema & entertainment, media & internet culture, sports & events
}


@dataclass(frozen=True)
class ExhaustionLimits:
    """Hard bounds on one exhaustion-recovery pass (3.31D)."""
    max_attempts: int = 4                    # expansion steps tried
    max_requests: int = 60                   # HTTP requests across all steps
    max_elapsed_seconds: float = 180.0       # wall clock, further capped by the recovery deadline
    max_candidates: int = 24                 # candidates enriched and scored
    min_request_interval_seconds: float = 0.2  # politeness gap between requests to one provider


@dataclass(frozen=True)
class TopicIntelligenceFlags:
    candidate_ledger: str = OFF
    category_classifier_v2: str = OFF
    production_query_rotation: str = OFF
    gnews_candidates: str = OFF
    wikipedia_in_the_news: str = OFF
    exhaustion_recovery: str = OFF
    demand_signals: str = OFF
    scoring_v2: str = OFF
    expanded_categories: str = OFF
    exhaustion_limits: ExhaustionLimits = field(default_factory=ExhaustionLimits)

    def is_on(self, name: str) -> bool:
        return getattr(self, name) == ON

    def is_active(self, name: str) -> bool:
        """On or shadow: the feature runs (shadow runs are recorded but do not affect selection)."""
        return getattr(self, name) in (ON, SHADOW)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Optional[Dict[str, Any]]) -> "TopicIntelligenceFlags":
        raw = raw if isinstance(raw, dict) else {}
        values: Dict[str, Any] = {}
        for name, allowed in _FLAG_MODES.items():
            val = raw.get(name, OFF)
            if isinstance(val, bool):
                val = ON if val else OFF
            val = str(val).strip().lower()
            if val not in allowed:
                if val:
                    logger.warning("topic_intelligence.%s=%r is not one of %s; using 'off'", name, val, allowed)
                val = OFF
            values[name] = val
        lim_raw = raw.get("exhaustion_limits") if isinstance(raw.get("exhaustion_limits"), dict) else {}
        base = ExhaustionLimits()
        lim: Dict[str, Any] = {}
        for k, default in asdict(base).items():
            try:
                v = type(default)(lim_raw.get(k, default))
                lim[k] = v if v > 0 else default
            except (TypeError, ValueError):
                lim[k] = default
        return cls(exhaustion_limits=ExhaustionLimits(**lim), **values)


def load_flags(config_path: Optional[str] = None) -> TopicIntelligenceFlags:
    """Reads the ``topic_intelligence`` block of the creative-policy config. Missing file, missing
    block or unreadable JSON all mean every flag is off."""
    if config_path is None:
        from backend.app.content.creative_production_policy import DEFAULT_POLICY_CONFIG_PATH
        config_path = DEFAULT_POLICY_CONFIG_PATH
    try:
        if not config_path or not os.path.isfile(config_path):
            return TopicIntelligenceFlags()
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return TopicIntelligenceFlags.from_mapping((cfg or {}).get("topic_intelligence"))
    except Exception as exc:  # a broken config must never change selection
        logger.warning("topic_intelligence flags unreadable (%s); all features off", type(exc).__name__)
        return TopicIntelligenceFlags()
