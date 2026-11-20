"""Creative records (one per production job) and controlled creative experiments.

``creative_records`` is the stable association Stage 3.35 needs: ``job_id`` (primary key) -> ``youtube_video_id``
(resolved from the publishing ledger, whose video path contains the job id) -> the creative decisions and their
evaluation. Audience metrics are NOT stored here; Stage 3.35 joins Stage 3.30 ``video_performance`` on
``youtube_video_id`` / ``job_id``.

``creative_experiments``: an operator-defined experiment restricts one existing controlled-variation point to
chosen arms. Supported point: ``hook_type`` (the script writer already assigns hook types by a hash of the job
id, i.e. randomly per job; an active experiment limits that assignment to its arms, still hash-assigned). At most
one active experiment per dimension. No experiment is active by default; activating one is an explicit operator
action and changes only which hook style the script writer is asked for.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS creative_records (
    job_id TEXT PRIMARY KEY,
    evaluated_at_utc TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    topic_title TEXT,
    category TEXT,
    renderer TEXT,
    visual_engine TEXT,
    hook_type TEXT,
    hook_text TEXT,
    script_fingerprint TEXT,
    video_path TEXT,
    youtube_video_id TEXT,
    video_linked_at_utc TEXT,
    experiment_id TEXT,
    experiment_arm TEXT,
    overall_score REAL,
    plan_score REAL,
    render_score REAL,
    decisions_json TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_creative_records_video ON creative_records (youtube_video_id);
CREATE TABLE IF NOT EXISTS creative_experiments (
    id TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,
    arms_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED')),
    hypothesis TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_creative_experiment_active ON creative_experiments (dimension) WHERE status = 'ACTIVE';
"""
EXPERIMENT_DIMENSIONS = {"hook_type": ("surprising_fact", "direct_question", "contradiction", "stakes", "scale")}
STATUSES = ("DRAFT", "ACTIVE", "PAUSED", "COMPLETED")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CreativeStoreError(ValueError):
    pass


class CreativeStore:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        self.db_path = str(db_path)
        self._ready = False
        self._lock = threading.Lock()

    def _rw(self) -> sqlite3.Connection:
        if not Path(self.db_path).is_file():
            raise CreativeStoreError("database not available")
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        if not self._ready:
            with self._lock:
                conn.executescript(SCHEMA)
                self._ready = True
        return conn

    def _ro(self) -> Optional[sqlite3.Connection]:
        """Read connection; None when the tables do not exist yet (reads never create tables)."""
        if not Path(self.db_path).is_file():
            return None
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='creative_records'").fetchone():
            conn.close()
            return None
        return conn

    # -- records -----------------------------------------------------------------------------------------------
    def save_evaluation(self, ev: Dict[str, Any], *, topic_title: str = "", category: str = "",
                        experiment: Optional[Dict[str, str]] = None) -> None:
        d = ev.get("decisions") or {}
        fp = d.get("script_fingerprint")
        fp = fp.get("sha256") if isinstance(fp, dict) else fp
        conn = self._rw()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO creative_records (job_id, evaluated_at_utc, evaluator_version, topic_title, category, renderer, "
                    "visual_engine, hook_type, hook_text, script_fingerprint, video_path, experiment_id, experiment_arm, "
                    "overall_score, plan_score, render_score, decisions_json, evaluation_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                    "evaluated_at_utc=excluded.evaluated_at_utc, evaluator_version=excluded.evaluator_version, "
                    "overall_score=excluded.overall_score, plan_score=excluded.plan_score, render_score=excluded.render_score, "
                    "decisions_json=excluded.decisions_json, evaluation_json=excluded.evaluation_json",
                    (ev["job_id"], _now(), ev.get("version", "?"), topic_title[:300], category[:100], d.get("renderer"),
                     d.get("visual_engine"), d.get("hook_type"), (d.get("hook_text") or "")[:500], fp,
                     (ev.get("artifacts") or {}).get("video"), (experiment or {}).get("id"), (experiment or {}).get("arm"),
                     ev.get("overall"), ev.get("plan_score"), ev.get("render_score"),
                     json.dumps(d, default=str)[:20000], json.dumps(ev, default=str)[:60000]))
        finally:
            conn.close()

    def evaluated_job_ids(self) -> set:
        conn = self._ro()
        if conn is None:
            return set()
        try:
            return {r[0] for r in conn.execute("SELECT job_id FROM creative_records")}
        finally:
            conn.close()

    def link_video_ids(self) -> int:
        """Fills youtube_video_id from the publishing ledger (video path contains the job id). Idempotent."""
        conn = self._ro()
        if conn is None:
            return 0
        try:
            todo = [r[0] for r in conn.execute("SELECT job_id FROM creative_records WHERE youtube_video_id IS NULL")]
            ledger = conn.execute("SELECT youtube_video_id, video_path FROM publishing_ledger WHERE youtube_video_id "
                                  "IS NOT NULL AND youtube_video_id != ''").fetchall() if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='publishing_ledger'").fetchone() else []
        finally:
            conn.close()
        links = {}
        for job in todo:
            needle = job.lower()
            for vid, path in ledger:
                parts = str(path or "").replace("\\", "/").lower().split("/")
                if needle in parts or any(p.endswith(f"_{needle}.mp4") for p in parts):
                    links[job] = vid
                    break
        if not links:
            return 0
        rw = self._rw()
        try:
            with rw:
                for job, vid in links.items():
                    rw.execute("UPDATE creative_records SET youtube_video_id=?, video_linked_at_utc=? WHERE job_id=? AND "
                               "youtube_video_id IS NULL", (vid, _now(), job))
        finally:
            rw.close()
        return len(links)

    def list_records(self, limit: int = 30) -> List[Dict[str, Any]]:
        conn = self._ro()
        if conn is None:
            return []
        try:
            rows = conn.execute("SELECT job_id, evaluated_at_utc, topic_title, category, renderer, visual_engine, hook_type, "
                                "youtube_video_id, experiment_id, experiment_arm, overall_score, plan_score, render_score, "
                                "evaluation_json FROM creative_records ORDER BY job_id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            ev = json.loads(r["evaluation_json"] or "{}")
            d = {k: r[k] for k in r.keys() if k != "evaluation_json"}
            d["weakest"] = ev.get("weakest")
            d["dimensions"] = {k: v.get("score") for k, v in (ev.get("dimensions") or {}).items()}
            out.append(d)
        return out

    def get_record(self, job_id: str) -> Optional[Dict[str, Any]]:
        conn = self._ro()
        if conn is None:
            return None
        try:
            r = conn.execute("SELECT * FROM creative_records WHERE job_id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if r is None:
            return None
        d = dict(r)
        d["decisions"] = json.loads(d.pop("decisions_json") or "{}")
        d["evaluation"] = json.loads(d.pop("evaluation_json") or "{}")
        return d

    def summary(self) -> Dict[str, Any]:
        recs = self.list_records(500)
        if not recs:
            return {"records": 0}
        dims: Dict[str, List[float]] = {}
        for r in recs:
            for k, v in (r.get("dimensions") or {}).items():
                if v is not None:
                    dims.setdefault(k, []).append(v)
        by_hook: Dict[str, List[float]] = {}
        for r in recs:
            if r.get("hook_type") and r.get("overall_score") is not None:
                by_hook.setdefault(r["hook_type"], []).append(r["overall_score"])
        return {"records": len(recs), "linked_to_youtube": sum(1 for r in recs if r.get("youtube_video_id")),
                "mean_overall": round(sum(r["overall_score"] for r in recs if r.get("overall_score") is not None)
                                      / max(1, sum(1 for r in recs if r.get("overall_score") is not None)), 1),
                "dimension_means": {k: round(sum(v) / len(v), 1) for k, v in sorted(dims.items())},
                "by_hook_type": {k: {"n": len(v), "mean_craft_score": round(sum(v) / len(v), 1)} for k, v in by_hook.items()},
                "note": "Craft scores from artifacts. Audience performance per creative choice needs Stage 3.35 analytics."}

    # -- experiments -------------------------------------------------------------------------------------------
    def create_experiment(self, dimension: str, arms: Sequence[str], hypothesis: str, operator: str) -> Dict[str, Any]:
        allowed = EXPERIMENT_DIMENSIONS.get(dimension)
        if not allowed:
            raise CreativeStoreError(f"unsupported dimension; one of {', '.join(EXPERIMENT_DIMENSIONS)}")
        arms = sorted(set(arms))
        if len(arms) < 2 or any(a not in allowed for a in arms):
            raise CreativeStoreError(f"choose at least two arms from {', '.join(allowed)}")
        hypothesis = (hypothesis or "").strip()[:500]
        if not hypothesis:
            raise CreativeStoreError("a hypothesis is required")
        eid = "exp_" + hashlib.sha256(f"{dimension}|{','.join(arms)}|{_now()}".encode()).hexdigest()[:10]
        conn = self._rw()
        try:
            with conn:
                conn.execute("INSERT INTO creative_experiments VALUES (?,?,?,?,?,?,?,?,?)",
                             (eid, dimension, json.dumps(arms), "DRAFT", hypothesis, operator, _now(), operator, _now()))
        finally:
            conn.close()
        return self.get_experiment(eid)

    def set_experiment_status(self, eid: str, status: str, operator: str) -> Dict[str, Any]:
        if status not in STATUSES:
            raise CreativeStoreError(f"status must be one of {', '.join(STATUSES)}")
        conn = self._rw()
        try:
            with conn:
                n = conn.execute("UPDATE creative_experiments SET status=?, updated_by=?, updated_at_utc=? WHERE id=?",
                                 (status, operator, _now(), eid)).rowcount
        except sqlite3.IntegrityError as exc:
            raise CreativeStoreError("another experiment on this dimension is already active") from exc
        finally:
            conn.close()
        if not n:
            raise CreativeStoreError("experiment not found")
        return self.get_experiment(eid)

    def get_experiment(self, eid: str) -> Optional[Dict[str, Any]]:
        for e in self.list_experiments():
            if e["id"] == eid:
                return e
        return None

    def list_experiments(self) -> List[Dict[str, Any]]:
        conn = self._ro()
        if conn is None:
            return []
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='creative_experiments'").fetchone():
                return []
            rows = conn.execute("SELECT * FROM creative_experiments ORDER BY created_at_utc DESC").fetchall()
            counts = {(r[0], r[1]): r[2] for r in conn.execute(
                "SELECT experiment_id, experiment_arm, COUNT(*) FROM creative_records WHERE experiment_id IS NOT NULL "
                "GROUP BY experiment_id, experiment_arm")}
        finally:
            conn.close()
        out = []
        for r in rows:
            e = dict(r)
            e["arms"] = json.loads(e.pop("arms_json"))
            e["productions_per_arm"] = {a: counts.get((e["id"], a), 0) for a in e["arms"]}
            out.append(e)
        return out

    def active_experiment(self, dimension: str) -> Optional[Dict[str, Any]]:
        try:
            return next((e for e in self.list_experiments() if e["dimension"] == dimension and e["status"] == "ACTIVE"), None)
        except Exception:
            return None                       # fail safe: no experiment, normal behaviour


def experiment_arm(job_id: str, arms: Sequence[str]) -> str:
    """Deterministic per-job assignment (same hash as the script writer's normal hook rotation)."""
    arms = list(arms)
    return arms[int(hashlib.sha256((job_id or "").encode("utf-8")).hexdigest(), 16) % len(arms)]
