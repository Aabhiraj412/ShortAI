"""
Isolated Validation Topic History & Semantic Anti-Repetition Store.
Stage 3.21K Standard.

Maintains persistent validation/test topic history completely separate from the
genuine production `content_ledger` SQLite table and `topic_history.json`.
Tracks topic selection, immediate rejection, research/script/storyboard fingerprints,
and thumbnail headlines across consecutive validation runs to prevent repetition
without polluting production publishing history.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.core.paths import PROJECT_ROOT_STR

logger = logging.getLogger(__name__)

VALIDATION_HISTORY_PATH = os.path.join(
    PROJECT_ROOT_STR, "backend", "data", "validation_topic_history.json"
)
TEST_VALIDATION_HISTORY_PATH = os.path.join(
    PROJECT_ROOT_STR, "backend", "data", "test_validation_topic_history.json"
)

STOPWORDS: Set[str] = {
    "why", "what", "how", "the", "a", "an", "of", "in", "on", "at", "for", "to",
    "and", "or", "is", "are", "was", "were", "who", "with", "without", "that",
    "this", "from", "into", "over", "under", "about", "after", "before", "during",
    "between", "story", "true", "history", "poses", "known", "behind", "inside",
}


def normalize_text(text: str) -> str:
    """Normalizes titles/phrases for deterministic comparisons."""
    if not text:
        return ""
    clean = re.sub(r"[,.'\"!?:;()\-–—/]", " ", text.lower())
    clean = re.sub(r"[^\w\s]", " ", clean)
    return " ".join(clean.split())


def stem_token(token: str) -> str:
    """Lightweight deterministic morphological stemmer for topic/entity matching."""
    tok = (token or "").lower().strip()
    if len(tok) <= 3:
        return tok
    if tok.endswith("field") and len(tok) > 6:
        tok = tok[:-5]
    elif tok.endswith("fields") and len(tok) > 7:
        tok = tok[:-6]
    if tok.endswith("ies") and len(tok) > 5:
        tok = tok[:-3] + "y"
    elif tok.endswith("es") and len(tok) > 4 and not tok.endswith(("ses", "xes", "zes")):
        if tok.endswith(("ches", "shes", "sses")):
            tok = tok[:-2]
        elif tok.endswith("ines") or tok.endswith("ones") or tok.endswith("ates"):
            tok = tok[:-1]
        else:
            tok = tok[:-1] if tok[-2] == "e" else tok[:-2]
    elif tok.endswith("s") and len(tok) > 4 and not tok.endswith("ss"):
        tok = tok[:-1]
    if tok.endswith("ing") and len(tok) > 6:
        tok = tok[:-3]
    elif tok.endswith("ed") and len(tok) > 5:
        tok = tok[:-2]
    return tok


def semantic_token_set(text: str) -> Set[str]:
    """Returns stemmed non-stopword tokens plus merged adjacent bigrams."""
    norm = normalize_text(text)
    words = [w for w in norm.split() if w and w not in STOPWORDS]
    stems = [stem_token(w) for w in words]
    tokens = {s for s in stems if len(s) >= 2}
    # Add collapsed bigrams so "land mine" == "landmine"
    for i in range(len(stems) - 1):
        merged = stems[i] + stems[i + 1]
        if len(merged) >= 5:
            tokens.add(merged)
    return tokens


def compute_topic_fingerprint(
    topic_title: str,
    key_entities: Optional[List[str]] = None,
    main_subject: str = "",
) -> str:
    """Computes a canonical order-invariant SHA-256 fingerprint for a topic subject."""
    title_stems = sorted(semantic_token_set(topic_title))
    subj_stems = sorted(semantic_token_set(main_subject)) if main_subject else []
    ent_stems = sorted({
        stem_token(tok)
        for ent in (key_entities or [])
        for tok in normalize_text(ent).split()
        if tok and tok not in STOPWORDS
    })
    payload = {
        "title_stems": title_stems,
        "subject_stems": subj_stems,
        "entity_stems": ent_stems,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def compute_research_fingerprint(
    summary: str = "",
    extended_extract: str = "",
    factual_bullets: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
) -> str:
    """Computes a deterministic SHA-256 fingerprint of a topic research dossier."""
    blob = " ".join([
        normalize_text(summary),
        normalize_text(extended_extract[:1200]),
        " ".join(normalize_text(b) for b in (factual_bullets or [])[:8]),
        " ".join(sorted((sources or [])[:5])),
    ]).strip()
    if not blob:
        return ""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_semantic_similarity(
    cand_title: str,
    cand_entities: List[str],
    cand_hook: str,
    rec_title: str,
    rec_entities: List[str],
    rec_hook: str,
) -> Tuple[float, str]:
    """
    Multi-signal semantic similarity resistant to headline word swaps,
    plurals/inflections, compound spacing, and paraphrase subsets.
    Returns (score_0_to_1, reason_detail).
    """
    c_norm = normalize_text(cand_title)
    r_norm = normalize_text(rec_title)
    if not c_norm or not r_norm:
        return 0.0, ""

    if c_norm == r_norm:
        return 1.0, f"Exact normalized title match with '{rec_title}'"

    c_collapsed = c_norm.replace(" ", "")
    r_collapsed = r_norm.replace(" ", "")
    if c_collapsed == r_collapsed:
        return 0.99, f"Collapsed normalized title match with '{rec_title}'"

    c_words = {stem_token(w) for w in c_norm.split() if w and w not in STOPWORDS}
    r_words = {stem_token(w) for w in r_norm.split() if w and w not in STOPWORDS}
    c_sem = semantic_token_set(cand_title)
    r_sem = semantic_token_set(rec_title)

    # Pure word-order permutation check (e.g. headline swaps)
    if c_words and r_words and c_words == r_words:
        return 0.98, f"Headline permutation / token-set match with '{rec_title}'"

    shared_words = c_words & r_words
    shared_sem = c_sem & r_sem
    union_words = c_words | r_words
    union_sem = c_sem | r_sem

    word_jaccard = (len(shared_words) / len(union_words)) if union_words else 0.0
    sem_jaccard = (len(shared_sem) / len(union_sem)) if union_sem else 0.0
    min_words = min(len(c_words), len(r_words))
    containment = (len(shared_words) / min_words) if min_words else 0.0

    # SequenceMatcher handles slight spelling/inflection diffs
    seq_ratio = SequenceMatcher(None, c_norm, r_norm).ratio()

    # Stemmed entity collision check
    c_ent_norm = {normalize_text(e) for e in cand_entities if normalize_text(e)}
    r_ent_norm = {normalize_text(e) for e in rec_entities if normalize_text(e)}
    c_ent_stems = {stem_token(t) for e in c_ent_norm for t in e.split() if t not in STOPWORDS}
    r_ent_stems = {stem_token(t) for e in r_ent_norm for t in e.split() if t not in STOPWORDS}

    # Exact entity phrase Jaccard + stemmed token overlap
    ent_phrase_jaccard = (
        len(c_ent_norm & r_ent_norm) / len(c_ent_norm | r_ent_norm)
        if (c_ent_norm | r_ent_norm)
        else 0.0
    )
    ent_stem_overlap = c_ent_stems & r_ent_stems
    ent_stem_jaccard = (
        len(ent_stem_overlap) / len(c_ent_stems | r_ent_stems)
        if (c_ent_stems | r_ent_stems)
        else 0.0
    )
    entity_score = max(ent_phrase_jaccard, ent_stem_jaccard)

    # Cross-check: title stems appearing in counterparty's entities (catches renamed headlines)
    title_in_rec_ent = len(c_words & r_ent_stems) / len(c_words) if c_words and r_ent_stems else 0.0

    # Hook overlap
    c_hook_toks = {stem_token(w) for w in normalize_text(cand_hook).split() if w and w not in STOPWORDS}
    r_hook_toks = {stem_token(w) for w in normalize_text(rec_hook).split() if w and w not in STOPWORDS}
    hook_jaccard = (
        len(c_hook_toks & r_hook_toks) / len(c_hook_toks | r_hook_toks)
        if (c_hook_toks | r_hook_toks)
        else 0.0
    )

    if len(shared_words) >= 2 and containment >= 0.65:
        score = max(0.82, round(containment, 2))
        return score, f"Headline subset/paraphrase match ({sorted(shared_words)}) with '{rec_title}'"

    if len(shared_words) >= 2 and title_in_rec_ent >= 0.65:
        score = max(0.75, round(title_in_rec_ent, 2))
        return score, f"Candidate keywords match existing topic entities for '{rec_title}'"

    if seq_ratio >= 0.78:
        return round(seq_ratio, 2), f"High string similarity ({seq_ratio:.2f}) with '{rec_title}'"

    best_score = max(word_jaccard, sem_jaccard, entity_score, hook_jaccard)
    if sem_jaccard >= 0.35 or word_jaccard >= 0.35:
        return round(max(word_jaccard, sem_jaccard), 2), (
            f"Semantic title overlap ({max(word_jaccard, sem_jaccard):.2f}) with '{rec_title}'"
        )
    if entity_score >= 0.30 and (len(ent_stem_overlap) >= 2 or ent_phrase_jaccard >= 0.30):
        return round(entity_score, 2), (
            f"Key entity overlap ({sorted(ent_stem_overlap)[:4]}) with '{rec_title}'"
        )
    if hook_jaccard >= 0.45:
        return round(hook_jaccard, 2), f"Opening hook similarity ({hook_jaccard:.2f}) with '{rec_title}'"

    return round(best_score, 2), ""


def _parse_iso_utc(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        clean = dt_str.strip()
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class ValidationTopicRecord:
    topic_id: str
    run_id: str
    normalized_title: str
    topic_title: str
    topic_fingerprint: str
    research_fingerprint: str = ""
    script_fingerprint: str = ""
    storyboard_fingerprint: str = ""
    thumbnail_headline: str = ""
    discovery_source: str = ""
    execution_context: str = "VALIDATION"
    outcome: str = "selected"  # selected, in_progress, completed, failed, rejected
    rejection_reason: Optional[str] = None
    key_entities: List[str] = field(default_factory=list)
    opening_hook: str = ""
    selected_at: str = ""
    expires_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _resolve_default_history_path() -> str:
    env_override = os.environ.get("CRAZZYDOCS_VALIDATION_HISTORY_PATH")
    if env_override:
        return os.path.abspath(env_override)
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CRAZZYDOCS_TEST_MODE") == "1":
        return TEST_VALIDATION_HISTORY_PATH
    return VALIDATION_HISTORY_PATH


class ValidationTopicHistory:
    """Thread-safe, JSON-backed isolated validation topic ledger."""

    def __init__(self, history_path: Optional[str] = None, default_cooldown_days: int = 30):
        self.history_path = os.path.abspath(history_path or _resolve_default_history_path())
        self.default_cooldown_days = default_cooldown_days
        self._lock = threading.RLock()
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        if not os.path.exists(self.history_path):
            self._write_raw({"schema_version": "3.21K", "records": []})

    def _read_raw(self) -> Dict[str, Any]:
        self._ensure_file()
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return {"schema_version": "3.21K", "records": data}
            if isinstance(data, dict):
                data.setdefault("records", [])
                return data
        except Exception as e:
            logger.warning("ValidationTopicHistory read fallback (%s): %s", self.history_path, e)
        return {"schema_version": "3.21K", "records": []}

    def _write_raw(self, payload: Dict[str, Any]):
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        tmp = f"{self.history_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.history_path)

    def load_records(self) -> List[ValidationTopicRecord]:
        with self._lock:
            raw = self._read_raw()
            out: List[ValidationTopicRecord] = []
            for item in raw.get("records", []):
                if not isinstance(item, dict):
                    continue
                try:
                    out.append(ValidationTopicRecord(
                        topic_id=str(item.get("topic_id", "")),
                        run_id=str(item.get("run_id", "")),
                        normalized_title=str(item.get("normalized_title", normalize_text(item.get("topic_title", "")))),
                        topic_title=str(item.get("topic_title", "")),
                        topic_fingerprint=str(item.get("topic_fingerprint", "")),
                        research_fingerprint=str(item.get("research_fingerprint", "")),
                        script_fingerprint=str(item.get("script_fingerprint", "")),
                        storyboard_fingerprint=str(item.get("storyboard_fingerprint", "")),
                        thumbnail_headline=str(item.get("thumbnail_headline", "")),
                        discovery_source=str(item.get("discovery_source", "")),
                        execution_context=str(item.get("execution_context", "VALIDATION")),
                        outcome=str(item.get("outcome", "selected")),
                        rejection_reason=item.get("rejection_reason"),
                        key_entities=list(item.get("key_entities") or []),
                        opening_hook=str(item.get("opening_hook", "")),
                        selected_at=str(item.get("selected_at", "")),
                        expires_at=str(item.get("expires_at", "")),
                        metadata=dict(item.get("metadata") or {}),
                    ))
                except Exception:
                    continue
            return out

    def record_selection(
        self,
        *,
        run_id: str,
        topic_id: str,
        topic_title: str,
        execution_context: str = "VALIDATION",
        outcome: str = "selected",
        rejection_reason: Optional[str] = None,
        key_entities: Optional[List[str]] = None,
        opening_hook: str = "",
        research_fingerprint: str = "",
        script_fingerprint: str = "",
        storyboard_fingerprint: str = "",
        thumbnail_headline: str = "",
        discovery_source: str = "",
        cooldown_days: Optional[int] = None,
        selected_at: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ValidationTopicRecord:
        """Persists a validation topic entry immediately so consecutive runs exclude it."""
        entities = list(key_entities or [])
        now_dt = _parse_iso_utc(selected_at) or datetime.now(timezone.utc)
        cd_days = int(cooldown_days if cooldown_days is not None else self.default_cooldown_days)
        expires_dt = now_dt + timedelta(days=cd_days)

        rec = ValidationTopicRecord(
            topic_id=topic_id,
            run_id=run_id,
            normalized_title=normalize_text(topic_title),
            topic_title=topic_title,
            topic_fingerprint=compute_topic_fingerprint(topic_title, entities),
            research_fingerprint=research_fingerprint or "",
            script_fingerprint=script_fingerprint or "",
            storyboard_fingerprint=storyboard_fingerprint or "",
            thumbnail_headline=thumbnail_headline or "",
            discovery_source=discovery_source or "",
            execution_context=str(execution_context or "VALIDATION").upper(),
            outcome=outcome,
            rejection_reason=rejection_reason,
            key_entities=entities,
            opening_hook=opening_hook or "",
            selected_at=now_dt.isoformat(),
            expires_at=expires_dt.isoformat(),
            metadata=dict(metadata or {}),
        )

        with self._lock:
            raw = self._read_raw()
            records = raw.get("records", [])
            # Upsert by (run_id, normalized_title) so updates overwrite in-place cleanly
            records = [
                r for r in records
                if not (
                    isinstance(r, dict)
                    and r.get("run_id") == run_id
                    and r.get("normalized_title") == rec.normalized_title
                )
            ]
            records.append(rec.to_dict())
            raw["schema_version"] = "3.21K"
            raw["last_updated"] = datetime.now(timezone.utc).isoformat()
            raw["records"] = records
            self._write_raw(raw)
        return rec

    def update_outcome(
        self,
        run_id: str,
        outcome: str,
        *,
        topic_title: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        research_fingerprint: Optional[str] = None,
        script_fingerprint: Optional[str] = None,
        storyboard_fingerprint: Optional[str] = None,
        thumbnail_headline: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ValidationTopicRecord]:
        """Updates an existing validation record with completion/rejection fingerprints."""
        norm_t = normalize_text(topic_title) if topic_title else None
        with self._lock:
            raw = self._read_raw()
            records = raw.get("records", [])
            updated_dict: Optional[Dict[str, Any]] = None
            for idx in range(len(records) - 1, -1, -1):
                item = records[idx]
                if not isinstance(item, dict):
                    continue
                if item.get("run_id") != run_id:
                    continue
                if norm_t and item.get("normalized_title") != norm_t:
                    continue
                item["outcome"] = outcome
                if rejection_reason is not None:
                    item["rejection_reason"] = rejection_reason
                if research_fingerprint:
                    item["research_fingerprint"] = research_fingerprint
                if script_fingerprint:
                    item["script_fingerprint"] = script_fingerprint
                if storyboard_fingerprint:
                    item["storyboard_fingerprint"] = storyboard_fingerprint
                if thumbnail_headline:
                    item["thumbnail_headline"] = thumbnail_headline
                if metadata:
                    merged_meta = dict(item.get("metadata") or {})
                    merged_meta.update(metadata)
                    item["metadata"] = merged_meta
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated_dict = item
                break

            if updated_dict is not None:
                raw["last_updated"] = datetime.now(timezone.utc).isoformat()
                self._write_raw(raw)
                return ValidationTopicRecord(**{
                    k: updated_dict.get(k, ValidationTopicRecord.__dataclass_fields__[k].default)
                    if k not in ("key_entities", "metadata")
                    else updated_dict.get(k) or ([] if k == "key_entities" else {})
                    for k in ValidationTopicRecord.__dataclass_fields__
                })
        return None

    def check_collision(
        self,
        candidate_title: str,
        candidate_hook: str = "",
        candidate_entities: Optional[List[str]] = None,
        *,
        topic_fingerprint: str = "",
        research_fingerprint: str = "",
        script_fingerprint: str = "",
        storyboard_fingerprint: str = "",
        cooldown_days: Optional[int] = None,
        rejected_cooldown_hours: int = 24,
        similarity_threshold: float = 0.42,
    ) -> Tuple[bool, float, str]:
        """Checks whether a candidate collides with any active validation record."""
        records = self.load_records()
        if not records:
            return False, 0.0, "No validation history collision."

        cd_days = int(cooldown_days if cooldown_days is not None else self.default_cooldown_days)
        entities = list(candidate_entities or [])
        cand_fp = topic_fingerprint or compute_topic_fingerprint(candidate_title, entities)
        now_utc = datetime.now(timezone.utc)

        for rec in records:
            sel_dt = _parse_iso_utc(rec.selected_at)
            exp_dt = _parse_iso_utc(rec.expires_at)

            # Determine if record is within its active cooldown window
            in_cooldown = False
            age_desc = "recent"
            if rec.outcome in ("rejected", "failed"):
                if sel_dt:
                    age_hours = (now_utc - sel_dt).total_seconds() / 3600.0
                    in_cooldown = age_hours <= rejected_cooldown_hours
                    age_desc = f"{age_hours:.1f}h ago ({rec.outcome})"
                else:
                    in_cooldown = True
            else:
                if sel_dt:
                    age_days = (now_utc - sel_dt).total_seconds() / 86400.0
                    in_cooldown = 0 <= age_days < cd_days
                    if exp_dt and now_utc < exp_dt:
                        in_cooldown = True
                    age_desc = f"{int(max(0, age_days))}d ago ({rec.outcome})"
                else:
                    in_cooldown = True

            if not in_cooldown:
                continue

            if cand_fp and rec.topic_fingerprint and cand_fp == rec.topic_fingerprint:
                return True, 1.0, (
                    f"Validation history topic fingerprint match with '{rec.topic_title}' "
                    f"(run={rec.run_id}, {age_desc})"
                )

            if research_fingerprint and rec.research_fingerprint and research_fingerprint == rec.research_fingerprint:
                return True, 1.0, (
                    f"Validation history research fingerprint match with '{rec.topic_title}' (run={rec.run_id})"
                )

            if script_fingerprint and rec.script_fingerprint and script_fingerprint == rec.script_fingerprint:
                return True, 1.0, (
                    f"Validation history script fingerprint match with '{rec.topic_title}' (run={rec.run_id})"
                )

            if storyboard_fingerprint and rec.storyboard_fingerprint and storyboard_fingerprint == rec.storyboard_fingerprint:
                return True, 1.0, (
                    f"Validation history storyboard fingerprint match with '{rec.topic_title}' (run={rec.run_id})"
                )

            sim_score, sim_reason = compute_semantic_similarity(
                candidate_title,
                entities,
                candidate_hook,
                rec.topic_title,
                rec.key_entities,
                rec.opening_hook,
            )
            if sim_reason and sim_score >= min(similarity_threshold, 0.30 if sim_score >= 0.30 else similarity_threshold):
                # Enforce stricter threshold during active cooldown
                if sim_score >= 0.30:
                    return True, sim_score, f"Validation cooldown ({age_desc}): {sim_reason}"

        return False, 0.0, "Passed validation history checks."

    def get_recent_thumbnail_headlines(self, cooldown_days: Optional[int] = None) -> List[str]:
        """Returns distinct normalized non-empty thumbnail headlines within cooldown."""
        cd_days = int(cooldown_days if cooldown_days is not None else self.default_cooldown_days)
        now_utc = datetime.now(timezone.utc)
        headlines: List[str] = []
        seen: Set[str] = set()
        for rec in self.load_records():
            if not rec.thumbnail_headline:
                continue
            sel_dt = _parse_iso_utc(rec.selected_at)
            if sel_dt and (now_utc - sel_dt).total_seconds() / 86400.0 > cd_days:
                continue
            h_norm = normalize_text(rec.thumbnail_headline)
            if h_norm and h_norm not in seen:
                seen.add(h_norm)
                headlines.append(rec.thumbnail_headline)
        return headlines

    def seed_from_validation_workspaces(self, productions_dir: Optional[str] = None) -> int:
        """
        Safely imports completed historical validation runs from canonical media/productions/val_*
        manifests on disk if they are not yet tracked in validation_topic_history.json.
        Never touches production content_ledger.
        """
        base = productions_dir or os.path.join(PROJECT_ROOT_STR, "media", "productions")
        if not os.path.isdir(base):
            return 0

        existing = {(r.run_id, r.normalized_title) for r in self.load_records()}
        added = 0
        for name in sorted(os.listdir(base)):
            if not name.startswith("val_"):
                continue
            p_dir = os.path.join(base, name)
            manifest_p = os.path.join(p_dir, "production_manifest.json")
            if not os.path.isfile(manifest_p):
                continue
            try:
                with open(manifest_p, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                topic_title = manifest.get("topic_title") or ""
                topic_id = manifest.get("topic_id") or f"topic_{name}"
                if not topic_title:
                    continue
                if (name, normalize_text(topic_title)) in existing:
                    continue

                # Read optional companion files for rich fingerprints
                res_fp = ""
                source_name = ""
                res_p = os.path.join(p_dir, "research", f"research_{name}.json")
                if os.path.isfile(res_p):
                    with open(res_p, "r", encoding="utf-8") as rf:
                        rdata = json.load(rf)
                    res_fp = compute_research_fingerprint(
                        summary=rdata.get("summary", ""),
                        extended_extract=rdata.get("extended_extract", ""),
                        factual_bullets=rdata.get("factual_facts", rdata.get("factual_bullets", [])),
                        sources=rdata.get("sources", []),
                    )
                    source_name = rdata.get("discovery_source", "wikipedia_onthisday")
                    entities = list(rdata.get("key_entities") or [])
                else:
                    entities = []

                headline = ""
                thumb_p = os.path.join(p_dir, "reports", f"thumb_report_{name}.json")
                if os.path.isfile(thumb_p):
                    with open(thumb_p, "r", encoding="utf-8") as tf:
                        tdata = json.load(tf)
                    headline = (
                        tdata.get("winner_headline")
                        or tdata.get("winning_headline")
                        or tdata.get("headline")
                        or ""
                    )

                self.record_selection(
                    run_id=name,
                    topic_id=topic_id,
                    topic_title=topic_title,
                    execution_context="VALIDATION",
                    outcome="completed",
                    key_entities=entities,
                    research_fingerprint=res_fp,
                    script_fingerprint=manifest.get("script_fingerprint", ""),
                    storyboard_fingerprint=manifest.get("storyboard_fingerprint", ""),
                    thumbnail_headline=headline,
                    discovery_source=source_name,
                    selected_at=manifest.get("last_updated"),
                    metadata={"seeded_from_manifest": manifest_p},
                )
                added += 1
            except Exception as e:
                logger.warning("Failed seeding validation manifest %s: %s", manifest_p, e)
        return added
