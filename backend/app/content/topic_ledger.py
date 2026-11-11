"""
Persistent Topic & Content Ledger for CrazzyDocs YouTube Shorts Studio.

Tracks all generated topics, hooks, scripts, key entities, and fingerprints
to enforce strict content diversity and prevent repetitive stories across runs.
Includes title normalization, configurable cooldown, and multi-dimensional collision checks.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DB_PATH = r"D:\Agents\CrazzyDocs\backend\studio.db"
HISTORY_JSON_PATH = r"D:\Agents\CrazzyDocs\backend\app\content\topic_history.json"


@dataclass
class ContentRecord:
    topic_id: str
    topic_title: str
    opening_hook: str
    main_subject: str
    key_entities: List[str]
    script_fingerprint: str
    category: str
    visual_style: str
    created_at: str
    publication_status: str = "approved"

    def to_dict(self) -> Dict:
        return asdict(self)


def normalize_topic_title(title: str) -> str:
    """Normalizes a topic title for resilient deduplication and collision detection."""
    if not title:
        return ""
    # Strip punctuation without introducing extra spaces so numbers stay intact (e.g. 33,000 -> 33000)
    clean = re.sub(r"[,.'\"!?:;()\-–—]", "", title.lower())
    clean = re.sub(r"[^\w\s]", " ", clean)
    return " ".join(clean.split())


def compute_script_fingerprint(script_text: str) -> str:
    """Computes a deterministic SHA-256 hash of normalized script text."""
    if not script_text:
        return ""
    clean = re.sub(r"\[.*?\]", "", script_text).lower()
    clean = re.sub(r"[^\w\s]", "", clean)
    normalized = " ".join(clean.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_storyboard_fingerprint(beats: List[Any]) -> str:
    """Computes a deterministic SHA-256 hash of storyboard beat configurations."""
    if not beats:
        return ""
    beat_summaries = []
    for beat in beats:
        label = getattr(beat, "label", getattr(beat, "stage", getattr(beat, "id", "")))
        style = getattr(beat, "style", getattr(beat, "visual_style", getattr(beat, "motion_type", "")))
        text = getattr(
            beat,
            "primary_text",
            getattr(beat, "editorial_text", getattr(beat, "on_screen_text", getattr(beat, "spoken_text", "")))
        )
        sub = getattr(beat, "sub_label", getattr(beat, "top_tag", ""))
        img = getattr(beat, "image_filename", "")
        beat_summaries.append(f"{label}|{style}|{text}|{sub}|{img}")
    raw = "||".join(beat_summaries)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    """Safely parses ISO datetime string."""
    if not dt_str:
        return None
    try:
        clean_str = dt_str.rstrip("Z")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


class TopicLedger:
    """Manages persistent history and multi-dimensional duplicate detection."""

    def __init__(self, db_path: str = DB_PATH, json_path: str = HISTORY_JSON_PATH):
        self.db_path = db_path
        self.json_path = json_path
        self._init_storage()

    def _init_storage(self):
        """Initializes SQLite table and JSON mirror."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS content_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id TEXT UNIQUE NOT NULL,
                    topic_title TEXT NOT NULL,
                    opening_hook TEXT NOT NULL,
                    main_subject TEXT NOT NULL,
                    key_entities TEXT NOT NULL,
                    script_fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    visual_style TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    publication_status TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

        # Seed initial history file if missing
        if not os.path.exists(self.json_path):
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

    def load_all_records(self) -> List[ContentRecord]:
        """Loads all historical content records from SQLite."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT topic_id, topic_title, opening_hook, main_subject,
                       key_entities, script_fingerprint, category, visual_style,
                       created_at, publication_status
                FROM content_ledger
                ORDER BY id ASC
            """)
            rows = cur.fetchall()
            records = []
            for r in rows:
                entities = json.loads(r[4]) if r[4] else []
                records.append(ContentRecord(
                    topic_id=r[0],
                    topic_title=r[1],
                    opening_hook=r[2],
                    main_subject=r[3],
                    key_entities=entities,
                    script_fingerprint=r[5],
                    category=r[6],
                    visual_style=r[7],
                    created_at=r[8],
                    publication_status=r[9],
                ))
            return records
        finally:
            conn.close()

    def check_content_collision(
        self,
        candidate_title: str,
        candidate_hook: str,
        candidate_entities: List[str],
        candidate_script: Optional[str] = None,
        similarity_threshold: float = 0.45,
        cooldown_days: int = 30,
    ) -> Tuple[bool, float, str]:
        """
        Evaluates a candidate topic against historical records across multiple dimensions:
        1. Exact script fingerprint match.
        2. Production cooldown window (cooldown_days).
        3. Key entity set overlap (Jaccard index).
        4. Title normalized word-level semantic similarity.
        5. Hook semantic similarity.
        
        Returns: (is_duplicate: bool, highest_score: float, reason: str)
        """
        records = self.load_all_records()
        if not records:
            return False, 0.0, "No previous records; clean topic."

        candidate_fp = compute_script_fingerprint(candidate_script) if candidate_script else None
        cand_ent_set = {e.lower().strip() for e in candidate_entities if e.strip()}
        cand_norm_title = normalize_topic_title(candidate_title)
        cand_title_words = set(cand_norm_title.split())
        cand_hook_words = set(normalize_topic_title(candidate_hook).split())

        stopwords = {
            "why", "what", "how", "the", "a", "an", "of", "in", "for", "to",
            "and", "is", "was", "who", "with", "without", "that", "this", "from"
        }
        w1_title = cand_title_words - stopwords
        h1_hook = cand_hook_words - stopwords

        now = datetime.utcnow()

        for rec in records:
            rec_norm_title = normalize_topic_title(rec.topic_title)
            rec_title_words = set(rec_norm_title.split())
            rec_ent_set = {e.lower().strip() for e in rec.key_entities if e.strip()}

            # 1. Exact script fingerprint match
            if candidate_fp and rec.script_fingerprint and rec.script_fingerprint == candidate_fp:
                return True, 1.0, f"Exact script fingerprint match with existing topic '{rec.topic_title}'"

            # 2. Check Cooldown Window
            rec_dt = parse_iso_datetime(rec.created_at)
            is_under_cooldown = False
            age_days = -1
            if rec_dt:
                age_days = (now - rec_dt).days
                if 0 <= age_days < cooldown_days:
                    is_under_cooldown = True

            # Calculate entity overlap
            entity_jaccard = 0.0
            intersect_ent = set()
            if cand_ent_set and rec_ent_set:
                intersect_ent = cand_ent_set & rec_ent_set
                union_ent = cand_ent_set | rec_ent_set
                entity_jaccard = len(intersect_ent) / len(union_ent) if union_ent else 0.0

            # Calculate title overlap
            w2_title = rec_title_words - stopwords
            title_sim = len(w1_title & w2_title) / len(w1_title | w2_title) if (w1_title | w2_title) else 0.0

            # If under cooldown and substantial overlap, reject immediately
            if is_under_cooldown:
                if cand_norm_title == rec_norm_title or title_sim >= 0.35 or entity_jaccard >= 0.30:
                    return (
                        True,
                        1.0,
                        f"Topic '{rec.topic_title}' was produced {age_days} days ago (within {cooldown_days}-day cooldown period)"
                    )

            # 3. Entity Overlap (Jaccard similarity >= 0.40)
            if entity_jaccard >= 0.40:
                return (
                    True,
                    round(entity_jaccard, 2),
                    f"High key-entity collision ({intersect_ent}) with topic '{rec.topic_title}'"
                )

            # 4. Title word-level overlap
            if title_sim >= similarity_threshold:
                return (
                    True,
                    round(title_sim, 2),
                    f"Title similarity ({title_sim:.2f}) exceeds threshold against '{rec.topic_title}'"
                )

            # 5. Hook overlap
            rec_hook_words = set(normalize_topic_title(rec.opening_hook).split())
            h2_hook = rec_hook_words - stopwords
            hook_sim = len(h1_hook & h2_hook) / len(h1_hook | h2_hook) if (h1_hook | h2_hook) else 0.0
            if hook_sim >= similarity_threshold:
                return (
                    True,
                    round(hook_sim, 2),
                    f"Opening hook similarity ({hook_sim:.2f}) exceeds threshold against '{rec.topic_title}'"
                )

        return False, 0.0, "Topic passed all diversity checks."

    def record_content(
        self,
        record: ContentRecord,
        allow_override: bool = False,
        execution_context: str = "production"
    ) -> bool:
        """Saves a verified content record to both SQLite and the JSON mirror.
        Strictly prevents tests from polluting the production database (DB_PATH).
        """
        is_ctx_test = "test" in str(execution_context).lower()
        is_env_test = os.environ.get("CRAZZYDOCS_TEST_MODE") == "1"
        if not allow_override and (is_ctx_test or is_env_test):
            logger.info("Test execution context; skipping write to persistent content_ledger.")
            return True

        if not allow_override:
            is_dup, score, reason = self.check_content_collision(
                record.topic_title,
                record.opening_hook,
                record.key_entities,
                record.script_fingerprint,
            )
            if is_dup:
                logger.warning("Rejecting duplicate content (%s, score=%.2f): %s", record.topic_title, score, reason)
                return False

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO content_ledger (
                    topic_id, topic_title, opening_hook, main_subject,
                    key_entities, script_fingerprint, category, visual_style,
                    created_at, publication_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.topic_id,
                record.topic_title,
                record.opening_hook,
                record.main_subject,
                json.dumps(record.key_entities),
                record.script_fingerprint,
                record.category,
                record.visual_style,
                record.created_at,
                record.publication_status,
            ))
            conn.commit()
        finally:
            conn.close()

        # Update JSON mirror
        records = self.load_all_records()
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in records], f, indent=2)

        logger.info("Recorded content in ledger: %s (%s)", record.topic_title, record.topic_id)
        return True
