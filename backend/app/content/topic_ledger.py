"""
Persistent Topic & Content Ledger for CrazzyDocs YouTube Shorts Studio.

Tracks all generated topics, hooks, scripts, key entities, and fingerprints
to enforce strict content diversity and prevent repetitive stories across runs.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

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


def compute_script_fingerprint(script_text: str) -> str:
    """Computes a deterministic SHA-256 hash of normalized script text."""
    clean = re.sub(r"\[.*?\]", "", script_text).lower()
    clean = re.sub(r"[^\w\s]", "", clean)
    normalized = " ".join(clean.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
        similarity_threshold: float = 0.55,
    ) -> Tuple[bool, float, str]:
        """
        Evaluates a candidate topic against historical records across multiple dimensions:
        1. Exact script fingerprint match (100% duplicate).
        2. Title semantic similarity.
        3. Hook semantic similarity.
        4. Key entity set overlap (Jaccard index).
        
        Returns: (is_duplicate: bool, highest_score: float, reason: str)
        """
        records = self.load_all_records()
        if not records:
            return False, 0.0, "No previous records; clean topic."

        candidate_fp = compute_script_fingerprint(candidate_script) if candidate_script else None
        cand_ent_set = {e.lower().strip() for e in candidate_entities if e.strip()}
        cand_title_words = set(re.findall(r"\w+", candidate_title.lower()))
        cand_hook_words = set(re.findall(r"\w+", candidate_hook.lower()))

        for rec in records:
            # 1. Exact script fingerprint match
            if candidate_fp and rec.script_fingerprint == candidate_fp:
                return True, 1.0, f"Exact script fingerprint match with existing topic '{rec.topic_title}'"

            # 2. Entity Overlap (Jaccard similarity)
            rec_ent_set = {e.lower().strip() for e in rec.key_entities}
            if cand_ent_set and rec_ent_set:
                intersect = cand_ent_set & rec_ent_set
                union = cand_ent_set | rec_ent_set
                entity_jaccard = len(intersect) / len(union) if union else 0.0
                if entity_jaccard >= 0.50:
                    return True, round(entity_jaccard, 2), f"High key-entity collision ({intersect}) with topic '{rec.topic_title}'"

            # 3. Title word-level overlap
            rec_title_words = set(re.findall(r"\w+", rec.topic_title.lower()))
            if cand_title_words and rec_title_words:
                stopwords = {"why", "what", "how", "the", "a", "an", "of", "in", "for", "to", "and", "is"}
                w1 = cand_title_words - stopwords
                w2 = rec_title_words - stopwords
                title_sim = len(w1 & w2) / len(w1 | w2) if (w1 | w2) else 0.0
                if title_sim >= similarity_threshold:
                    return True, round(title_sim, 2), f"Title similarity ({title_sim:.2f}) exceeds threshold against '{rec.topic_title}'"

            # 4. Hook overlap
            rec_hook_words = set(re.findall(r"\w+", rec.opening_hook.lower()))
            if cand_hook_words and rec_hook_words:
                stopwords = {"the", "a", "an", "of", "in", "for", "to", "and", "is", "was"}
                h1 = cand_hook_words - stopwords
                h2 = rec_hook_words - stopwords
                hook_sim = len(h1 & h2) / len(h1 | h2) if (h1 | h2) else 0.0
                if hook_sim >= similarity_threshold:
                    return True, round(hook_sim, 2), f"Opening hook similarity ({hook_sim:.2f}) exceeds threshold against '{rec.topic_title}'"

        return False, 0.0, "Topic passed all diversity checks."

    def record_content(self, record: ContentRecord, allow_override: bool = False) -> bool:
        """Saves a verified content record to both SQLite and the JSON mirror."""
        if not allow_override:
            is_dup, score, reason = self.check_content_collision(
                record.topic_title,
                record.opening_hook,
                record.key_entities,
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
