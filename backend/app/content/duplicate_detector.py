"""
Duplicate Detection Subsystem for Content Intelligence.

Detects duplicate or highly similar video topics against historical production records
using normalized token Jaccard similarity, character-level SequenceMatcher ratio,
and key conceptual overlap checks.
"""

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple

from backend.app.database.db import SessionLocal, JobModel, TopicModel

# Common English stop words to strip before semantic comparison
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "don", "should", "now", "are", "you", "your", "does", "do", "did"
}


def normalize_title(title: str) -> str:
    """Normalizes a title string: lowercase, strips punctuation, standardizes whitespace."""
    if not title:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return " ".join(cleaned.split())


def extract_keywords(title: str) -> Set[str]:
    """Extracts non-stop-word lexical tokens from a title."""
    norm = normalize_title(title)
    tokens = norm.split()
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 2}


class DuplicateDetector:
    """Evaluates topic uniqueness and prevents repetition of recent concepts."""

    def __init__(self, similarity_threshold: float = 0.60):
        self.threshold = similarity_threshold

    def compute_similarity(self, title1: str, title2: str) -> float:
        """
        Calculates a hybrid similarity score combining:
        1. Token prefix / stem matching (concept overlap)
        2. Subset containment ratio
        3. Character-level SequenceMatcher ratio
        """
        t1_clean = normalize_title(title1)
        t2_clean = normalize_title(title2)
        if not t1_clean or not t2_clean:
            return 0.0

        if t1_clean == t2_clean:
            return 1.0

        # Character sequence ratio
        seq_ratio = SequenceMatcher(None, t1_clean, t2_clean).ratio()

        # Keyword sets
        kw1 = extract_keywords(title1)
        kw2 = extract_keywords(title2)

        if not kw1 or not kw2:
            return round(seq_ratio, 3)

        # Count fuzzy/stemmed matches between keywords
        matched_1 = 0
        for w1 in kw1:
            for w2 in kw2:
                if w1 == w2 or (len(w1) >= 4 and len(w2) >= 4 and (w1.startswith(w2) or w2.startswith(w1))):
                    matched_1 += 1
                    break

        matched_2 = 0
        for w2 in kw2:
            for w1 in kw1:
                if w1 == w2 or (len(w1) >= 4 and len(w2) >= 4 and (w1.startswith(w2) or w2.startswith(w1))):
                    matched_2 += 1
                    break

        common_count = (matched_1 + matched_2) / 2.0
        union_count = len(kw1) + len(kw2) - common_count
        jaccard = common_count / union_count if union_count > 0 else 0.0
        min_len = min(len(kw1), len(kw2))
        subset_overlap = common_count / min_len if min_len > 0 else 0.0

        # Hybrid metric: balances token overlap and concept containment
        hybrid = max(
            (0.50 * jaccard) + (0.35 * subset_overlap) + (0.15 * seq_ratio),
            subset_overlap * 0.85
        )
        return round(min(1.0, hybrid), 3)

    def is_duplicate(
        self,
        candidate_title: str,
        existing_titles: List[str],
        custom_threshold: Optional[float] = None
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Checks candidate against a list of existing titles.
        Returns: (is_duplicate: bool, max_similarity: float, matching_title: Optional[str])
        """
        thresh = custom_threshold if custom_threshold is not None else self.threshold
        max_sim = 0.0
        best_match = None

        for existing in existing_titles:
            sim = self.compute_similarity(candidate_title, existing)
            if sim > max_sim:
                max_sim = sim
                best_match = existing

            if max_sim >= thresh:
                return True, max_sim, best_match

        return False, max_sim, best_match

    def fetch_historical_titles(self, limit: int = 150) -> List[str]:
        """Fetches previously used topic titles from the SQLite database."""
        titles = set()
        try:
            with SessionLocal() as session:
                # 1. From persistent jobs
                jobs = session.query(JobModel).filter(JobModel.topic_title.isnot(None)).all()
                for j in jobs:
                    if j.topic_title:
                        titles.add(j.topic_title)

                # 2. From topics table
                top_records = session.query(TopicModel).order_by(TopicModel.created_at.desc()).limit(limit).all()
                for t in top_records:
                    if t.title:
                        titles.add(t.title)
        except Exception as e:
            # Fallback if DB query fails
            print(f"[DuplicateDetector] Warning reading DB history: {e}")

        return list(titles)
