"""Stage 3.35: audience learning - production decisions joined to real YouTube performance.

Learning record (one per published video, table ``learning_records``): the video's decisions (topic, category,
hook type and text, duration, publish hour IST, craft scores from the 3.32 creative record, scene composition from
the rendered plan, topic scores/demand from the 3.31 ledger) and its performance:

* preferred window: the fixed **first 7 days** (comparable across videos);
* otherwise the latest lifetime-to-date snapshot, flagged ``window: lifetime`` and excluded from claims.

Analysis rules (no overfitting):
* a learning claim needs at least ``MIN_VIDEOS_FOR_CLAIMS`` (30) videos with first-7-days metrics;
* a group comparison needs at least ``MIN_GROUP`` (10) videos per group and a relative difference >= 15 %;
* medians (robust to outliers) and winsorised means; outliers above Q3 + 3 IQR are flagged, not removed;
* correlations are Spearman rank correlations with a permutation p-value, labelled "correlation, not causation";
* every recommendation carries evidence, sample size, confidence, expected benefit and limitations, is a
  PROPOSAL (never applied), and is reproducible from its input hash. Runs are logged in ``learning_runs``.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MIN_VIDEOS_FOR_CLAIMS = 30
MIN_GROUP = 10
MIN_RELATIVE_EFFECT = 0.15
VERSION = "3.35-learning-1"
IST = timezone(timedelta(hours=5, minutes=30))
CRAFT_DIMS = ("overall", "hook", "storytelling", "pacing", "ending_payoff", "transitions", "visual_dynamics",
              "scene_diversity", "text_readability")
METRICS = ("views", "averageViewPercentage", "engaged_rate", "likes_per_100_views")

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_records (
    video_id TEXT PRIMARY KEY,
    job_id TEXT,
    built_at_utc TEXT NOT NULL,
    window_kind TEXT NOT NULL,
    decisions_json TEXT NOT NULL,
    performance_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_runs (
    run_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    videos INTEGER NOT NULL,
    videos_first_7_days INTEGER NOT NULL,
    sufficiency TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def confidence_label(n: int) -> str:
    return "insufficient" if n < 5 else "low" if n < MIN_GROUP else "medium" if n < MIN_VIDEOS_FOR_CLAIMS else "high"


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else None


# -- records ------------------------------------------------------------------------------------------------------
def build_records(db_path: str, productions_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "video_performance" not in names:
            return []
        rows = conn.execute("SELECT * FROM video_performance ORDER BY collected_at_utc").fetchall()
        creative = {}
        if "creative_records" in names:
            for r in conn.execute("SELECT job_id, youtube_video_id, hook_type, overall_score, evaluation_json, category "
                                  "FROM creative_records"):
                ev = json.loads(r["evaluation_json"] or "{}")
                dims = {k: (v or {}).get("score") for k, v in (ev.get("dimensions") or {}).items()}
                creative[r["job_id"]] = {"hook_type": r["hook_type"], "category": r["category"],
                                         "craft": {"overall": r["overall_score"], **dims}}
        ledger = {}
        if "topic_candidate_ledger" in names:
            for r in conn.execute("SELECT selected_job_id, category, score, score_breakdown_json, provider FROM "
                                  "topic_candidate_ledger WHERE selected_job_id IS NOT NULL"):
                bd = json.loads(r["score_breakdown_json"] or "{}")
                ledger[r["selected_job_id"]] = {"category_v1": r["category"], "score_v1": r["score"],
                                                "score_v2": (bd.get("_v2") or {}).get("score"), "provider": r["provider"]}
    finally:
        conn.close()
    latest: Dict[str, Any] = {}
    d7: Dict[str, Any] = {}
    for r in rows:
        if (r["window_kind"] if "window_kind" in r.keys() else None) == "first_7_days":
            d7[r["video_id"]] = r
        else:
            latest[r["video_id"]] = r
    out = []
    for vid in sorted(set(latest) | set(d7)):
        r = d7.get(vid) or latest[vid]
        metrics = json.loads(r["metrics_json"] or "{}")
        status = json.loads(r["metric_status_json"] or "{}")
        views = _num(metrics.get("views"))
        perf = {"views": views, "averageViewPercentage": _num(metrics.get("averageViewPercentage")),
                "averageViewDuration": _num(metrics.get("averageViewDuration")),
                "engaged_rate": (round(_num(metrics.get("engagedViews")) / views, 3)
                                 if views and _num(metrics.get("engagedViews")) is not None else None),
                "likes_per_100_views": (round(100 * _num(metrics.get("likes")) / views, 2)
                                        if views and _num(metrics.get("likes")) is not None else None),
                "retention": metrics.get("retention"), "traffic_sources": metrics.get("traffic_sources"),
                "status": status, "collected_at_utc": r["collected_at_utc"]}
        job = r["job_id"]
        pub = None
        try:
            pub = datetime.fromisoformat(str(r["published_at_utc"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass
        comp = None
        if productions_dir and job:
            plan = Path(productions_dir) / job / "pop_info_film" / "plan.json"
            if plan.is_file():
                try:
                    from backend.app.rendering.pop_info.asset_manifest import composition
                    comp = composition(json.loads(plan.read_text(encoding="utf-8")))["share_pct"]
                except Exception:
                    comp = None
        cr = creative.get(job) or {}
        decisions = {"job_id": job, "topic": r["topic"], "category": r["topic_category"] or cr.get("category"),
                     "hook_type": cr.get("hook_type"), "hook_text": (r["hook"] or "")[:200],
                     "duration_sec": _num(r["duration_sec"]), "published_at_utc": r["published_at_utc"],
                     "publish_hour_ist": pub.astimezone(IST).hour if pub else None,
                     "craft": cr.get("craft") or {}, "composition_pct": comp, "topic_scores": ledger.get(job) or {},
                     "renderer": r["renderer"], "visual_engine": r["visual_engine"]}
        out.append({"video_id": vid, "job_id": job, "window_kind": "first_7_days" if vid in d7 else "lifetime",
                    "decisions": decisions, "performance": perf})
    return out


def persist_records(db_path: str, records: Sequence[Dict[str, Any]]) -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.executescript(SCHEMA)
        with conn:
            conn.executemany("INSERT INTO learning_records VALUES (?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
                             "job_id=excluded.job_id, built_at_utc=excluded.built_at_utc, window_kind=excluded.window_kind, "
                             "decisions_json=excluded.decisions_json, performance_json=excluded.performance_json",
                             [(r["video_id"], r["job_id"], _now(), r["window_kind"], json.dumps(r["decisions"], default=str),
                               json.dumps(r["performance"], default=str)) for r in records])
    finally:
        conn.close()
    return len(records)


# -- statistics ---------------------------------------------------------------------------------------------------
def _winsor_mean(vals: List[float], p: float = 0.9) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    cap = s[min(len(s) - 1, int(p * (len(s) - 1)))]
    return round(statistics.mean(min(v, cap) for v in s), 3)


def describe(vals: Iterable[Optional[float]]) -> Dict[str, Any]:
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0}
    s = sorted(v)
    q1, q3 = s[len(s) // 4], s[(3 * len(s)) // 4]
    out = {"n": len(v), "median": round(statistics.median(v), 3), "winsorised_mean": _winsor_mean(v),
           "q1": q1, "q3": q3, "confidence": confidence_label(len(v))}
    out["outliers"] = sum(1 for x in v if x > q3 + 3 * (q3 - q1)) if len(v) >= 4 else 0
    return out


def _ranks(x: List[float]) -> List[float]:
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: List[float], y: List[float], permutations: int = 400, seed: int = 7) -> Dict[str, Any]:
    n = len(x)
    if n < 5:
        return {"n": n, "rho": None, "p": None, "confidence": "insufficient"}

    def rho(a, b):
        ra, rb = _ranks(a), _ranks(b)
        ma, mb = statistics.mean(ra), statistics.mean(rb)
        num = sum((p - ma) * (q - mb) for p, q in zip(ra, rb))
        den = math.sqrt(sum((p - ma) ** 2 for p in ra) * sum((q - mb) ** 2 for q in rb))
        return num / den if den else 0.0
    r = rho(x, y)
    rnd = random.Random(seed)
    yy = list(y)
    hits = 0
    for _ in range(permutations):
        rnd.shuffle(yy)
        if abs(rho(x, yy)) >= abs(r) - 1e-12:
            hits += 1
    return {"n": n, "rho": round(r, 3), "p": round((hits + 1) / (permutations + 1), 3), "confidence": confidence_label(n),
            "note": "correlation, not causation"}


def _bucket_duration(d: Optional[float]) -> Optional[str]:
    if d is None:
        return None
    return "<45s" if d < 45 else "45-52s" if d < 52 else "52-58s"


def _bucket_hour(h: Optional[int]) -> Optional[str]:
    if h is None:
        return None
    return ("00-06", "06-12", "12-18", "18-24")[h // 6]


def analyse(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    d7 = [r for r in records if r["window_kind"] == "first_7_days"]
    with_data = [r for r in records if r["performance"].get("views") is not None]
    sufficient = len(d7) >= MIN_VIDEOS_FOR_CLAIMS
    basis = d7 if sufficient else (d7 or with_data)
    groupings = {
        "category": lambda r: r["decisions"].get("category"),
        "hook_type": lambda r: r["decisions"].get("hook_type"),
        "duration": lambda r: _bucket_duration(r["decisions"].get("duration_sec")),
        "publish_hour_ist": lambda r: _bucket_hour(r["decisions"].get("publish_hour_ist")),
        "has_footage": lambda r: (None if r["decisions"].get("composition_pct") is None else
                                  ("with footage" if (r["decisions"]["composition_pct"].get("video_footage") or 0) > 0
                                   else "no footage")),
        "has_photos": lambda r: (None if r["decisions"].get("composition_pct") is None else
                                 ("with photos" if (r["decisions"]["composition_pct"].get("photograph") or 0) > 0
                                  else "no photos")),
    }
    groups: Dict[str, Dict[str, Any]] = {}
    for gname, key in groupings.items():
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in basis:
            k = key(r)
            if k:
                buckets[str(k)].append(r)
        groups[gname] = {k: {m: describe(x["performance"].get(m) for x in v) for m in METRICS} | {"n": len(v)}
                         for k, v in sorted(buckets.items())}
    correlations: Dict[str, Dict[str, Any]] = {}
    for dim in CRAFT_DIMS:
        for metric in ("averageViewPercentage", "views"):
            pairs = [(r["decisions"]["craft"].get(dim), r["performance"].get(metric)) for r in basis
                     if isinstance(r["decisions"].get("craft", {}).get(dim), (int, float))
                     and r["performance"].get(metric) is not None]
            if len(pairs) >= 5:
                correlations[f"{dim}~{metric}"] = spearman([p[0] for p in pairs], [p[1] for p in pairs])
    return {"version": VERSION, "videos": len(records), "videos_with_data": len(with_data),
            "videos_first_7_days": len(d7), "basis": "first_7_days" if (sufficient or d7) else "lifetime_to_date",
            "sufficiency": "sufficient" if sufficient else "preliminary",
            "needed_for_claims": max(0, MIN_VIDEOS_FOR_CLAIMS - len(d7)),
            "overall": {m: describe(r["performance"].get(m) for r in basis) for m in METRICS},
            "groups": groups, "correlations": correlations,
            "notes": ["Medians and winsorised means; outliers flagged, not removed.",
                      "Correlations are not causation; hook style is randomised per job, the other decisions are not.",
                      f"Learning claims need >= {MIN_VIDEOS_FOR_CLAIMS} videos with first-7-days metrics."]}


def recommendations(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Proposals only. Without sufficient data the output is a list of what each recommendation still needs."""
    recs: List[Dict[str, Any]] = []
    if analysis["sufficiency"] != "sufficient":
        for area, affects in (("category", "topic selection"), ("hook_type", "creative policy"), ("duration", "creative policy"),
                              ("publish_hour_ist", "publication timing"), ("has_footage", "visual policy")):
            sizes = {k: v.get("n", 0) for k, v in analysis["groups"].get(area, {}).items()}
            recs.append({"id": f"pending_{area}", "status": "insufficient_data", "area": area, "affects": affects,
                         "sample_sizes": sizes, "confidence": "insufficient",
                         "needs": f"{analysis['needed_for_claims']} more videos with first-7-days metrics and "
                                  f">= {MIN_GROUP} videos per compared group",
                         "requires_authorization": True, "applied": False})
        return recs
    for area, affects in (("category", "topic selection"), ("hook_type", "creative policy"), ("duration", "creative policy"),
                          ("publish_hour_ist", "publication timing"), ("has_footage", "visual policy")):
        g = {k: v for k, v in analysis["groups"].get(area, {}).items() if v.get("n", 0) >= MIN_GROUP}
        for metric in ("averageViewPercentage", "views"):
            ranked = sorted(((k, v[metric].get("median")) for k, v in g.items() if v[metric].get("median") is not None),
                            key=lambda x: -x[1])
            if len(ranked) < 2:
                continue
            (best, b), (worst, w) = ranked[0], ranked[-1]
            if w <= 0 or (b - w) / w < MIN_RELATIVE_EFFECT:
                continue
            n_b, n_w = g[best]["n"], g[worst]["n"]
            rid = hashlib.sha256(f"{area}|{metric}|{best}|{worst}|{n_b}|{n_w}".encode()).hexdigest()[:12]
            recs.append({"id": f"rec_{rid}", "status": "proposal", "area": area, "affects": affects, "metric": metric,
                         "recommendation": f"Prefer {area} '{best}' over '{worst}'",
                         "evidence": {"best": {best: b, "n": n_b}, "worst": {worst: w, "n": n_w}},
                         "sample_size": n_b + n_w, "confidence": confidence_label(min(n_b, n_w)),
                         "expected_benefit": f"median {metric} {round(100 * (b - w) / w)} % higher in observed data",
                         "limitations": "observational; confounded by topic and timing unless randomised (hook style is)",
                         "requires_authorization": True, "applied": False})
    return recs


def run(db_path: Optional[str] = None, productions_dir: Optional[Path] = None, *, persist: bool = True) -> Dict[str, Any]:
    if db_path is None:
        from backend.app.core.paths import DATABASE_PATH
        db_path = str(DATABASE_PATH)
    if productions_dir is None:
        from backend.app.core.paths import MEDIA_PRODUCTIONS_DIR
        productions_dir = Path(MEDIA_PRODUCTIONS_DIR)
    records = build_records(db_path, productions_dir)
    a = analyse(records)
    recs = recommendations(a)
    input_hash = hashlib.sha256(json.dumps([(r["video_id"], r["window_kind"], r["performance"].get("views"),
                                             r["performance"].get("averageViewPercentage")) for r in records],
                                           default=str).encode()).hexdigest()[:16]
    summary = {"analysis": a, "recommendations": recs, "input_hash": input_hash}
    if persist:
        persist_records(db_path, records)
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.executescript(SCHEMA)
            with conn:
                conn.execute("INSERT OR REPLACE INTO learning_runs VALUES (?,?,?,?,?,?,?,?)",
                             (f"lrun_{input_hash}", _now(), VERSION, input_hash, a["videos"], a["videos_first_7_days"],
                              a["sufficiency"], json.dumps(summary, default=str)[:200000]))
        finally:
            conn.close()
    return summary


def latest_run(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if db_path is None:
        from backend.app.core.paths import DATABASE_PATH
        db_path = str(DATABASE_PATH)
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='learning_runs'").fetchone():
            return None
        r = conn.execute("SELECT created_at_utc, summary_json FROM learning_runs ORDER BY created_at_utc DESC LIMIT 1").fetchone()
        return {"created_at_utc": r[0], **json.loads(r[1])} if r else None
    finally:
        conn.close()
