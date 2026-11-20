"""Explainable confidence and priority scoring. Every score is returned with its components.

Confidence: how sure we are about the *recommendation* (not just the observation).
    confidence = strength_base × evidence_factor × (0.7 + 0.3 × consistency) × completeness
      strength_base   CONFIRMED 0.95 · STRONG 0.75 · WEAK 0.45
      evidence_factor 0.5 + 0.5 × min(1, sample_size / (2 × min_sample))   (more evidence than the minimum raises it)
      consistency     0..1 how uniform the evidence is (e.g. share of the dominant pattern)
      completeness    0..1 availability of the data sources the rule read (actual 1.0, partial 0.8)
    label: HIGH ≥ 0.70 · MEDIUM ≥ 0.45 · LOW below.

Priority (0-100): severity 35 · production impact 20 · frequency 15 · recency 15 · confidence 10 · affected jobs 5.
    severity        critical 1.0 · high 0.75 · medium 0.5 · low 0.25
    frequency       min(1, log2(1 + occurrences) / log2(1 + 20))
    recency         1.0 within 6 h, falling linearly to 0.2 at the end of the analysis window (0.5 when unknown)
    affected jobs   min(1, jobs / 10)
    label: CRITICAL ≥ 80 · HIGH ≥ 60 · MEDIUM ≥ 40 · LOW below.

The evidence type shown to the operator comes from the hypothesis strength:
    CONFIRMED -> "Confirmed observation" · STRONG -> "Strongly supported hypothesis" · WEAK -> "Weak hypothesis".
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STRENGTH_BASE = {"CONFIRMED": 0.95, "STRONG": 0.75, "WEAK": 0.45}
STRENGTH_LABEL = {"CONFIRMED": "Confirmed observation", "STRONG": "Strongly supported hypothesis", "WEAK": "Weak hypothesis"}
SEVERITY = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
WEIGHTS = {"severity": 35, "impact": 20, "frequency": 15, "recency": 15, "confidence": 10, "affected_jobs": 5}


def confidence(finding: Any) -> Dict[str, Any]:
    base = STRENGTH_BASE[finding.hypothesis_strength]
    need = max(1, int(finding.min_sample))
    evidence = 0.5 + 0.5 * min(1.0, finding.sample_size / (2.0 * need))
    consistency = 0.7 + 0.3 * max(0.0, min(1.0, finding.consistency))
    completeness = max(0.0, min(1.0, finding.completeness))
    score = round(max(0.0, min(1.0, base * evidence * consistency * completeness)), 3)
    label = "HIGH" if score >= 0.70 else "MEDIUM" if score >= 0.45 else "LOW"
    return {"score": score, "label": label, "evidence_type": STRENGTH_LABEL[finding.hypothesis_strength],
            "components": {"strength_base": base, "evidence_factor": round(evidence, 3),
                           "consistency_factor": round(consistency, 3), "completeness": completeness}}


def _recency(last_seen: Optional[str], now: datetime, window_days: int) -> float:
    if not last_seen:
        return 0.5
    from backend.app.services.operational_intelligence import _parse_ts
    ts = _parse_ts(last_seen)
    if ts is None:
        return 0.5
    age_h = max(0.0, (now - ts).total_seconds() / 3600.0)
    if age_h <= 6:
        return 1.0
    span = max(6.0, window_days * 24.0)
    return round(max(0.2, 1.0 - 0.8 * (age_h - 6) / (span - 6 if span > 6 else 1)), 3)


def priority(finding: Any, conf: Dict[str, Any], now: Optional[datetime] = None, window_days: int = 7) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    parts = {
        "severity": SEVERITY.get(finding.severity, 0.5),
        "impact": max(0.0, min(1.0, finding.impact)),
        "frequency": round(min(1.0, math.log2(1 + max(0, finding.occurrences)) / math.log2(21)), 3),
        "recency": _recency(finding.last_seen, now, window_days),
        "confidence": conf["score"],
        "affected_jobs": round(min(1.0, len(finding.affected_jobs) / 10.0), 3),
    }
    score = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    label = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 40 else "LOW"
    return {"score": score, "label": label, "components": parts, "weights": WEIGHTS}
