"""Remediation WS7: the measurable feedback loop between completed videos and future production.

Input: the per-video craft evaluations already stored by the Stage 3.32 evaluator (``creative_records``: 14
dimensions, script source, hook type) and the production job outcomes (``persistent_jobs``). Read-only.

Output (``feedback_report``):

* **quality by script source**: overall and per-dimension means with sample sizes. The production evidence that
  started this: model-written scripts 89.7 vs extractive fallback 76.3 overall (n = 100 / 71);
* **recurring weaknesses**: how often each dimension is among a video's three weakest;
* **trend**: the last window of videos against the previous one. A change is reported only when both windows hold
  at least ``MIN_N`` videos AND the difference is at least ``MIN_DELTA`` points; otherwise "insufficient evidence"
  or "no measurable change". Collecting metrics is never reported as improvement;
* **failures, separated**: creative (QC-rejected content) versus infrastructure (provider, network, recovery
  window); QC rejections are never treated as retryable infrastructure failures;
* **proposals**: deterministic rules with their evidence. Nothing is applied automatically;
* **interventions**: improvements that WERE applied (code changes), each measured on the videos produced after it
  started (``creative_interventions``, recorded once by the hourly creative tick), against the same number before.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

MIN_N = 20
MIN_DELTA = 3.0
WINDOW = 24

# Applied improvements and the metric each must move. Recorded with its start time on first sight.
INTERVENTIONS = (
    {"id": "ws7_deferred_script_retry",
     "description": "One deferred Gemini round (150 s) when every model failed with transient 429/5xx errors, "
                    "before falling back to the extractive script.",
     "metric": "model_share", "expected": "increase"},
)

SCHEMA = """CREATE TABLE IF NOT EXISTS creative_interventions (
    id TEXT PRIMARY KEY, description TEXT NOT NULL, metric TEXT NOT NULL, expected TEXT NOT NULL,
    applied_from_utc TEXT NOT NULL)"""

INFRA_MARKERS = ("timeout", "network", "connection", "recovery window", "quota", "provider", "winerror", "ffmpeg",
                 "http", "rate limit", "interrupted", "internet")


def _db(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from backend.app.core.paths import DATABASE_PATH
    return str(DATABASE_PATH)


def _ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _source(decisions: Dict[str, Any]) -> str:
    s = str((decisions or {}).get("script_source") or "unknown")
    return "model" if s.startswith("llm:") else s


def _load(db_path: str) -> List[Dict[str, Any]]:
    conn = _ro(db_path)
    try:
        rows = conn.execute("SELECT job_id, evaluated_at_utc, overall_score, decisions_json, evaluation_json "
                            "FROM creative_records ORDER BY evaluated_at_utc").fetchall()
    finally:
        conn.close()
    out = []
    for job_id, at, overall, dj, ej in rows:
        try:
            d, e = json.loads(dj or "{}") or {}, json.loads(ej or "{}") or {}
        except ValueError:
            continue
        dims = {k: float(v["score"]) for k, v in (e.get("dimensions") or {}).items()
                if isinstance(v, dict) and v.get("score") is not None and v.get("measured", True)}
        weakest = [w if isinstance(w, str) else (w or {}).get("dimension") for w in (e.get("weakest") or [])[:3]]
        out.append({"job_id": job_id, "at": at, "overall": overall, "source": _source(d), "dims": dims,
                    "weakest": [w for w in weakest if w], "hook_type": d.get("hook_type")})
    return out


def _mean(vals: Sequence[float]) -> Optional[float]:
    return round(statistics.mean(vals), 1) if vals else None


def _group(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    dims: Dict[str, List[float]] = {}
    for r in records:
        for k, v in r["dims"].items():
            dims.setdefault(k, []).append(v)
    overall = [r["overall"] for r in records if r["overall"] is not None]
    return {"n": len(records), "overall": _mean(overall), "dimensions": {k: _mean(v) for k, v in sorted(dims.items())},
            "model_share": round(sum(1 for r in records if r["source"] == "model") / len(records), 2) if records else None}


def _verdict(a: Dict[str, Any], b: Dict[str, Any], key: str = "overall", scale: float = 1.0) -> Dict[str, Any]:
    """b against a. Reports a change only with enough videos on both sides and a meaningful difference."""
    va, vb = a.get(key), b.get(key)
    if a["n"] < MIN_N or b["n"] < MIN_N or va is None or vb is None:
        return {"verdict": "insufficient evidence", "detail": f"needs {MIN_N} videos per window (have {a['n']} / {b['n']})"}
    delta = round((vb - va) * scale, 1)
    if abs(delta) < MIN_DELTA:
        return {"verdict": "no measurable change", "delta": delta}
    return {"verdict": "improving" if delta > 0 else "declining", "delta": delta}


def _failures(db_path: str, days: int = 7) -> Dict[str, Any]:
    conn = _ro(db_path)
    try:
        rows = conn.execute("SELECT id, status, error_details FROM persistent_jobs WHERE status IN "
                            "('FAILED','BLOCKED_QC','RECOVERY_WINDOW_EXPIRED') AND created_at >= datetime('now', ?) "
                            "AND id NOT LIKE 'test%' AND id NOT LIKE 'mock%'", (f"-{int(days)} day",)).fetchall()
    finally:
        conn.close()
    creative, infra, unknown = Counter(), Counter(), 0
    for _id, status, err in rows:
        text = str(err or "")
        codes = re.findall(r"\[(CHECK_[A-Z0-9_]+)\]", text)
        if status == "BLOCKED_QC" or codes:
            creative.update(codes or ["QC_BLOCKED"])
        elif status == "RECOVERY_WINDOW_EXPIRED" or any(m in text.lower() for m in INFRA_MARKERS):
            infra.update([status if status == "RECOVERY_WINDOW_EXPIRED" else (text.split(":")[0][:40] or status)])
        else:
            unknown += 1
    return {"days": days, "creative_quality": dict(creative.most_common(10)),
            "infrastructure": dict(infra.most_common(10)), "unclassified": unknown,
            "rule": "QC-rejected content is a creative failure and is never retried as an infrastructure failure"}


def record_interventions(db_path: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """Called by the hourly creative tick: stores each known intervention's start time once (idempotent)."""
    path = _db(db_path)
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute(SCHEMA)
        n = 0
        for iv in INTERVENTIONS:
            n += conn.execute("INSERT OR IGNORE INTO creative_interventions VALUES (?,?,?,?,?)",
                              (iv["id"], iv["description"], iv["metric"], iv["expected"], ts)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def _interventions(db_path: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        conn = _ro(db_path)
        try:
            rows = conn.execute("SELECT id, description, metric, expected, applied_from_utc FROM creative_interventions").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = []
    for iid, desc, metric, expected, applied in rows:
        after = [r for r in records if str(r["at"]) >= applied]
        before = [r for r in records if str(r["at"]) < applied][-max(len(after), MIN_N):]
        a, b = _group(before), _group(after)
        v = _verdict(a, b, metric, scale=100.0 if metric == "model_share" else 1.0)
        out.append({"id": iid, "description": desc, "metric": metric, "expected": expected,
                    "applied_from_utc": applied, "before": {"n": a["n"], metric: a.get(metric)},
                    "after": {"n": b["n"], metric: b.get(metric)}, **v})
    return out


def _proposals(by_source: Dict[str, Any], recent: Dict[str, Any], weak_share: Dict[str, float]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    m, f = by_source.get("model"), by_source.get("extractive_fallback")
    if m and f and m["n"] >= MIN_N and f["n"] >= MIN_N and m["overall"] is not None and f["overall"] is not None:
        gap = round(m["overall"] - f["overall"], 1)
        if gap >= MIN_DELTA and (recent.get("model_share") or 0) < 0.5:
            out.append({"id": "script_model_availability", "status": "being measured (intervention applied)",
                        "evidence": f"model scripts {m['overall']} vs fallback {f['overall']} overall "
                                    f"(n={m['n']}/{f['n']}); model share in the last {recent['n']} videos "
                                    f"{recent['model_share']}",
                        "action": "keep the deferred retry; measure model share on the next videos",
                        "requires_operator": False})
    for dim, share in weak_share.items():
        if share >= 0.5:
            out.append({"id": f"weak_{dim}", "status": "proposal",
                        "evidence": f"'{dim}' is among the three weakest dimensions in {round(share * 100)}% of recent videos",
                        "action": ("run a controlled hook_type experiment (existing Stage 3.32 experiments)"
                                   if dim == "hook" else f"review the {dim} rules in the script/storyboard prompts"),
                        "requires_operator": True,
                        "reason": "changes creative policy; evidence-based but not yet validated by an experiment"})
    return out


def feedback_report(db_path: Optional[str] = None, *, window: int = WINDOW) -> Dict[str, Any]:
    path = _db(db_path)
    try:
        records = _load(path)
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"creative records unavailable ({type(exc).__name__})"}
    if not records:
        return {"available": False, "reason": "no evaluated videos yet"}
    by_source: Dict[str, Any] = {}
    for src in sorted({r["source"] for r in records}):
        by_source[src] = _group([r for r in records if r["source"] == src])
    last, prev = records[-window:], records[-2 * window:-window]
    recent, previous = _group(last), _group(prev)
    weak = Counter(w for r in records[-2 * window:] for w in r["weakest"])
    n_recent = len(records[-2 * window:]) or 1
    weak_share = {k: round(v / n_recent, 2) for k, v in weak.most_common(6)}
    trend = {"overall": _verdict(previous, recent),
             "model_share": _verdict(previous, recent, "model_share", scale=100.0),
             "dimensions": {d: _verdict({"n": previous["n"], d: previous["dimensions"].get(d)},
                                        {"n": recent["n"], d: recent["dimensions"].get(d)}, d)
                            for d in sorted(recent["dimensions"])}}
    effect = {}
    m, f = by_source.get("model"), by_source.get("extractive_fallback")
    if m and f:
        effect = {d: round(m["dimensions"][d] - f["dimensions"][d], 1) for d in m["dimensions"]
                  if d in f["dimensions"] and m["dimensions"][d] is not None and f["dimensions"][d] is not None}
    try:
        failures = _failures(path)
    except sqlite3.Error:
        failures = {"available": False}
    return {"available": True, "videos": len(records), "window": window, "min_n": MIN_N, "min_delta": MIN_DELTA,
            "by_script_source": by_source, "script_source_effect": effect,
            "recent": recent, "previous": previous, "trend": trend, "recurring_weaknesses": weak_share,
            "failures": failures, "proposals": _proposals(by_source, recent, weak_share),
            "interventions": _interventions(path, records),
            "applied_automatically": [iv["id"] for iv in INTERVENTIONS],
            "policy": "Nothing here changes production policy. Proposals that change creative policy need the operator; "
                      "an improvement is reported only when it is measured on enough videos."}
