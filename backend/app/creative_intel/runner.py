"""Runs creative evaluation for finished productions from the existing scheduler daemon (job
``creative_evaluation_tick``). Read-only towards production: it reads a finished job's workspace and the
database, writes only ``creative_records``, and does nothing while any production job is active (so its short
FFmpeg analysis never competes with a render)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.app.creative_intel.evaluation import evaluate_production
from backend.app.creative_intel.store import CreativeStore

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("QUEUED", "RUNNING", "RETRYING", "RETRY_SCHEDULED", "HANDOFF_PENDING")
DONE_STATUSES = ("COMPLETED", "PUBLISHED")
LOOKBACK_DAYS = 14
PER_TICK = 3


def _db() -> str:
    from backend.app.core.paths import DATABASE_PATH
    return str(DATABASE_PATH)


def _productions_dir() -> Path:
    from backend.app.core.paths import MEDIA_PRODUCTIONS_DIR
    return Path(MEDIA_PRODUCTIONS_DIR)


def pending_jobs(db_path: str, evaluated: set, *, now: Optional[datetime] = None, limit: int = PER_TICK,
                 productions: Optional[Path] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    productions = productions or _productions_dir()
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
    try:
        active = conn.execute("SELECT COUNT(*) FROM persistent_jobs WHERE status IN (%s)" % ",".join("?" * len(ACTIVE_STATUSES)),
                              ACTIVE_STATUSES).fetchone()[0]
        since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        rows = conn.execute("SELECT id, topic_title FROM persistent_jobs WHERE status IN (%s) AND created_at >= ? "
                            "ORDER BY created_at DESC" % ",".join("?" * len(DONE_STATUSES)),
                            DONE_STATUSES + (since,)).fetchall()
    finally:
        conn.close()
    todo = [{"job_id": j, "topic_title": t or ""} for j, t in rows
            if j not in evaluated and (productions / j / "video").is_dir()]
    return {"active_jobs": active, "todo": todo[:limit], "backlog": len(todo)}


def evaluate_pending(*, store: Optional[CreativeStore] = None, db_path: Optional[str] = None,
                     productions: Optional[Path] = None, limit: int = PER_TICK,
                     evaluate: Callable[..., Dict[str, Any]] = evaluate_production,
                     ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None) -> Dict[str, Any]:
    db_path = db_path or _db()
    store = store or CreativeStore(db_path)
    productions = productions or _productions_dir()
    p = pending_jobs(db_path, store.evaluated_job_ids(), limit=limit, productions=productions)
    out: Dict[str, Any] = {"active_jobs": p["active_jobs"], "backlog": p["backlog"], "evaluated": [], "errors": {}}
    if p["active_jobs"]:
        out["skipped"] = "a production job is active"
        return out
    if ffmpeg is None or ffprobe is None:
        from backend.app.core.paths import get_ffmpeg_path, get_ffprobe_path
        ffmpeg, ffprobe = ffmpeg or get_ffmpeg_path(), ffprobe or get_ffprobe_path()
    for job in p["todo"]:
        jid = job["job_id"]
        try:
            ev = evaluate(productions / jid, jid, ffmpeg=ffmpeg, ffprobe=ffprobe)
            script_path = (ev.get("artifacts") or {}).get("script")
            script = json.loads(Path(script_path).read_text(encoding="utf-8")) if script_path else {}
            store.save_evaluation(ev, topic_title=job["topic_title"], category=str(script.get("category") or ""),
                                  experiment=script.get("creative_experiment"))
            out["evaluated"].append({"job_id": jid, "overall": ev.get("overall")})
        except Exception as exc:
            out["errors"][jid] = f"{type(exc).__name__}: {str(exc)[:160]}"
    try:
        out["videos_linked"] = store.link_video_ids()
    except Exception as exc:
        out["errors"]["link"] = type(exc).__name__
    return out
