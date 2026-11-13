"""
Persistent creative identity for every production attempt.

Root cause this closes (2026-11-13 audit): the TopicLedger only learned about a topic
after a production *completed*. Every failed cycle left its topic unrecorded, the live
ranker is deterministic, so the next cycle picked the same topic and composed the same
script byte for byte ("Science fiction" was selected by six productions in a row).

The registry records the identity at topic selection, before anything can fail, and keeps
growing as the production advances (script, storyboard, assets, final video). It lives in
the studio database, so it survives deleted files, restarts and new cycles, and it never
depends on an MP4 filename.

Similarity levels kept apart on purpose:
  subject    same normalized topic           -> blocked for TOPIC_COOLDOWN_DAYS
  script     identical script fingerprint    -> blocked for SCRIPT_COOLDOWN_DAYS
  storyboard / assets / final video          -> recorded for reports; a reused stock
                                                asset alone is not a duplicate video.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.app.content.topic_ledger import DB_PATH, normalize_topic_title

logger = logging.getLogger(__name__)

TOPIC_COOLDOWN_DAYS = 30
SCRIPT_COOLDOWN_DAYS = 365

_COLUMNS = (
    "job_id", "normalized_topic", "topic_title", "category", "topic_angle",
    "hook_fingerprint", "script_fingerprint", "storyboard_fingerprint",
    "asset_fingerprints_json", "visual_sequence_fingerprint", "renderer_config_json",
    "video_sha256", "status", "created_at", "updated_at",
    # Stage 3.27 creative identity (added by migration; old rows keep the defaults)
    "hook_type", "narrative_structure", "script_text", "hook_text", "ending_text",
    "scene_sequence_json", "visual_concepts_json", "visual_concepts_fingerprint", "typography",
    "animation_json", "transition_sequence_json", "audio_treatment", "thumbnail_concept",
    "visual_engine", "script_source",
)
_ADDED_COLUMNS = {
    "hook_type": "''", "narrative_structure": "''", "script_text": "''", "hook_text": "''", "ending_text": "''",
    "scene_sequence_json": "'[]'", "visual_concepts_json": "'[]'", "visual_concepts_fingerprint": "''",
    "typography": "''", "animation_json": "'[]'", "transition_sequence_json": "'[]'", "audio_treatment": "''",
    "thumbnail_concept": "''", "visual_engine": "''", "script_source": "''",
}
HOOK_NEAR_DUP = 0.6          # word-set Jaccard of two hooks
ENDING_NEAR_DUP = 0.7
SCRIPT_NEAR_DUP = 0.5        # 3-word shingle Jaccard of two scripts
VISUAL_NEAR_DUP = 0.85       # Jaccard of template:subject concept sets
_CTA_RE = re.compile(r"[^.!?]*\b(subscribe|follow|for more)\b[^.!?]*[.!?]?", re.I)


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower())) - _STOP


def _shingles(text: str, n: int = 3) -> set:
    w = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _ending_core(text: str, topic: str) -> set:
    """An ending without its call to action and without the topic's own words."""
    return _words(_CTA_RE.sub(" ", text or "")) - _words(topic)


_STOP = set("a an the and or but of in on at to for from by with is are was were be it its this that as so how why what".split())
_UPDATABLE = frozenset(_COLUMNS) - {"job_id", "created_at"}


class DuplicateCreativeIdentity(RuntimeError):
    """A production would repeat an earlier production's subject or script."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CreativeIdentityRegistry:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS creative_identity_registry (
                    job_id TEXT PRIMARY KEY,
                    normalized_topic TEXT NOT NULL,
                    topic_title TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    topic_angle TEXT NOT NULL DEFAULT '',
                    hook_fingerprint TEXT NOT NULL DEFAULT '',
                    script_fingerprint TEXT NOT NULL DEFAULT '',
                    storyboard_fingerprint TEXT NOT NULL DEFAULT '',
                    asset_fingerprints_json TEXT NOT NULL DEFAULT '[]',
                    visual_sequence_fingerprint TEXT NOT NULL DEFAULT '',
                    renderer_config_json TEXT NOT NULL DEFAULT '{}',
                    video_sha256 TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'SELECTED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            have = {r[1] for r in conn.execute("PRAGMA table_info(creative_identity_registry)")}
            for col, default in _ADDED_COLUMNS.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE creative_identity_registry ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cir_topic ON creative_identity_registry(normalized_topic)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cir_script ON creative_identity_registry(script_fingerprint)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ writes
    def reserve(self, job_id: str, *, topic_title: str, category: str = "",
                topic_angle: str = "", hook_fingerprint: str = "") -> None:
        """Records the selected topic for this job. Idempotent per job."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO creative_identity_registry
                   (job_id, normalized_topic, topic_title, category, topic_angle, hook_fingerprint,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'SELECTED', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                     normalized_topic=excluded.normalized_topic, topic_title=excluded.topic_title,
                     category=excluded.category, topic_angle=excluded.topic_angle,
                     hook_fingerprint=excluded.hook_fingerprint, updated_at=excluded.updated_at""",
                (job_id, normalize_topic_title(topic_title), topic_title, category or "",
                 topic_angle or "", hook_fingerprint or "", now, now),
            )

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Unknown creative identity fields: {sorted(unknown)}")
        if not fields:
            return
        values = {k: (json.dumps(v, sort_keys=True) if k.endswith("_json") and not isinstance(v, str) else v)
                  for k, v in fields.items()}
        values["updated_at"] = _now()
        assignments = ", ".join(f"{k}=?" for k in values)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE creative_identity_registry SET {assignments} WHERE job_id=?",
                (*values.values(), job_id),
            )

    def backfill_from_history(self, productions_dir: Optional[str] = None) -> int:
        """Imports productions that predate the registry. Idempotent; never overwrites a row.

        Sources in the same database: persistent_jobs (every attempt, including failed
        ones) and content_ledger (completed productions). Script fingerprints come from the
        workspace script file when it still exists; a deleted file only loses that detail.
        """
        from pathlib import Path
        from backend.app.content.topic_ledger import compute_script_fingerprint

        if productions_dir is None:
            from backend.app.core.paths import MEDIA_PRODUCTIONS_DIR
            productions_dir = str(MEDIA_PRODUCTIONS_DIR)
        added = 0
        with self._connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows: List[tuple] = []
            if "persistent_jobs" in tables:
                for r in conn.execute(
                    "SELECT id, topic_title, status, COALESCE(actual_start_at, created_at) FROM persistent_jobs "
                    "WHERE topic_title IS NOT NULL AND topic_title <> '' AND id NOT LIKE 'test%'"
                ):
                    rows.append((r[0], r[1], f"HISTORICAL_{r[2]}", r[3], "", ""))
            if "content_ledger" in tables:
                for r in conn.execute(
                    "SELECT topic_id, topic_title, publication_status, created_at, script_fingerprint, category "
                    "FROM content_ledger"
                ):
                    job = r[0][len("topic_"):] if r[0].startswith("topic_") else r[0]
                    rows.append((job, r[1], f"HISTORICAL_{(r[2] or '').upper()}", r[3], r[4] or "", r[5] or ""))
            for job_id, title, status, created, script_fp, category in rows:
                if not script_fp:
                    script_file = Path(productions_dir) / job_id / "scripts" / f"script_{job_id}.json"
                    try:
                        script_fp = compute_script_fingerprint(
                            json.loads(script_file.read_text(encoding="utf-8")).get("full_script", ""))
                    except (OSError, ValueError, AttributeError):
                        script_fp = ""
                created_iso = (_parse(str(created).replace(" ", "T")) or datetime.now(timezone.utc)).isoformat()
                cur = conn.execute(
                    """INSERT OR IGNORE INTO creative_identity_registry
                       (job_id, normalized_topic, topic_title, category, script_fingerprint, status,
                        created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, normalize_topic_title(title), title, category, script_fp, status,
                     created_iso, _now()),
                )
                added += cur.rowcount
        if added:
            logger.info("Creative identity registry: imported %d historical productions", added)
        return added

    # ------------------------------------------------------------------- reads
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_identity_registry WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def all(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM creative_identity_registry ORDER BY created_at")]

    def _recent(self, column: str, value: str, *, exclude_job: str, days: int,
                now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not value:
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM creative_identity_registry WHERE {column}=? AND job_id<>?",
                (value, exclude_job),
            ).fetchall()
        return [dict(r) for r in rows if (_parse(r["created_at"]) or cutoff) >= cutoff]

    def topic_conflict(self, topic_title: str, *, exclude_job: str,
                       days: int = TOPIC_COOLDOWN_DAYS, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Another job selected the same subject within the cooldown, whatever its outcome."""
        hits = self._recent("normalized_topic", normalize_topic_title(topic_title),
                            exclude_job=exclude_job, days=days, now=now)
        return hits[0] if hits else None

    def script_conflict(self, script_fingerprint: str, *, exclude_job: str,
                        days: int = SCRIPT_COOLDOWN_DAYS, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        hits = self._recent("script_fingerprint", script_fingerprint, exclude_job=exclude_job, days=days, now=now)
        return hits[0] if hits else None

    def near_duplicates(self, *, job_id: str, topic_title: str = "", script_text: str = "", hook_text: str = "",
                        ending_text: str = "", visual_concepts: Sequence[str] = (), script_source: str = "",
                        days: int = 180, now: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Meaningful near-duplication against earlier productions, split into blocking and advisory.

        Blocking: a near-identical script, a reused hook, or the same visual composition sequence.
        Advisory: a repeated ending when the script came from the labelled extractive fallback, whose
        closing line is a fixed template; blocking it would stop every production until a model key
        exists, so it is recorded instead. Shared channel branding is never compared.
        """
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        blocking: List[Dict[str, Any]] = []
        advisory: List[Dict[str, Any]] = []
        s_sh, h_w = _shingles(script_text), _words(hook_text)
        e_core, v_set = _ending_core(ending_text, topic_title), set(visual_concepts)
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM creative_identity_registry WHERE job_id<>?", (job_id,))]
        for r in rows:
            if (_parse(r["created_at"]) or cutoff) < cutoff:
                continue
            ref = {"job_id": r["job_id"], "topic_title": r["topic_title"]}
            if s_sh and r.get("script_text"):
                sim = jaccard(s_sh, _shingles(r["script_text"]))
                if sim >= SCRIPT_NEAR_DUP:
                    blocking.append({**ref, "kind": "script", "similarity": round(sim, 2)})
            if h_w and r.get("hook_text"):
                sim = jaccard(h_w, _words(r["hook_text"]))
                if sim >= HOOK_NEAR_DUP:
                    blocking.append({**ref, "kind": "hook", "similarity": round(sim, 2)})
            if v_set and r.get("visual_concepts_json") not in (None, "", "[]"):
                sim = jaccard(v_set, set(json.loads(r["visual_concepts_json"])))
                if sim >= VISUAL_NEAR_DUP:
                    blocking.append({**ref, "kind": "visual_sequence", "similarity": round(sim, 2)})
            if e_core and r.get("ending_text"):
                sim = jaccard(e_core, _ending_core(r["ending_text"], r["topic_title"]))
                if sim >= ENDING_NEAR_DUP:
                    (advisory if script_source == "extractive_fallback" else blocking).append(
                        {**ref, "kind": "ending", "similarity": round(sim, 2)})
        return {"blocking": blocking, "advisory": advisory}

    def filter_fresh(self, candidates: Sequence[Any], *, job_id: str,
                     now: Optional[datetime] = None) -> tuple[List[Any], List[Dict[str, str]]]:
        """Drops candidates whose subject another job already selected. Keeps the ranking order."""
        fresh, rejected = [], []
        for cand in candidates:
            title = getattr(cand, "title", "") or ""
            hit = self.topic_conflict(title, exclude_job=job_id, now=now)
            if hit:
                rejected.append({"title": title, "conflicts_with": hit["job_id"], "status": hit["status"]})
            else:
                fresh.append(cand)
        return fresh, rejected


def asset_fingerprints(beats: Iterable[Dict[str, Any]]) -> List[str]:
    """Stable per-asset identities from storyboard beats (hash, falling back to source identity)."""
    out = []
    for beat in beats or []:
        if not isinstance(beat, dict):
            continue
        for key in ("asset_hash", "source_image_sha256", "source_image_identity"):
            fp = beat.get(key)
            if fp not in (None, "", "None"):
                out.append(str(fp))
                break
    return out
