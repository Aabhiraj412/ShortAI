"""Community content intelligence: where a post's facts come from, which topic and format to use, and the text.

Sources (all read-only):
* completed productions (``persistent_jobs``) with a research dossier
  (``media/productions/<job>/research/research_<job>.json``): grounded facts, category, hook, composite score;
* ``publishing_ledger``: the YouTube video id and visibility for each production (only PUBLIC videos are referenced
  or linked; a private video is never promoted);
* ``topic_candidate_ledger`` (Stage 3.31): ELIGIBLE upcoming topics, using the candidate summary as the fact corpus;
* ``content_ledger`` and ``creative_identity_registry``: the channel's own categories (channel relevance);
* earlier Community drafts: topic, format, opening, question and call-to-action rotation.

Text is produced by deterministic templates (zero API calls). An optional Gemini writer (existing SDK and key,
off by default, bounded per day) can phrase the same facts; its output passes the same validation and falls back to
the template text on any problem. Nothing here invents a fact: every number must come from the source corpus.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

FORMATS = ("fact", "educational_question", "multiple_choice", "behind_the_scenes", "video_followup", "trivia",
           "curiosity", "engagement_prompt")
FORMAT_LABELS = {
    "fact": "Interesting fact", "educational_question": "Educational question",
    "multiple_choice": "Multiple-choice question", "behind_the_scenes": "Behind the scenes",
    "video_followup": "Video follow-up", "trivia": "History / technology trivia",
    "curiosity": "Short curiosity post", "engagement_prompt": "Audience engagement prompt",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_\-]{11}$")
_URL = re.compile(r"https?://|www\.", re.I)
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_INT = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?![\w.,]\d)")
TRIVIA_HINTS = ("history", "technolog", "engineer", "invent", "space", "astro", "science", "artificial", "computer",
                "ancient")
MAX_DOSSIER_BYTES = 2 * 1024 * 1024
SOURCE_DAYS = 30
CANDIDATE_DAYS = 14
TOPIC_REUSE_DAYS = 60
ROTATION_LOOKBACK = 14

# Static, verifiable statements about how CrazzyDocs works (they describe the pipeline itself, not a topic).
PROCESS_FACTS = (
    "Every image in our shorts comes from openly licensed collections such as Openverse, Wikimedia Commons, NASA "
    "and the Internet Archive, and each one is credited in the video description.",
    "Each short starts from a research file of sourced facts, and the script is checked so that every number in it "
    "appears in that research.",
    "Our shorts are composed and rendered on a single laptop, from research to final video.",
)


@dataclass
class Source:
    kind: str                       # production | candidate
    ref: str                        # job id or candidate id
    title: str
    category: str
    facts: List[str]
    corpus: str
    hook: str = ""
    score: float = 0.0
    created_at: Optional[datetime] = None
    video_id: Optional[str] = None
    video_public: bool = False
    licensed_images: int = 0
    repositories: List[str] = field(default_factory=list)

    @property
    def topic_key(self) -> str:
        return topic_key(self.title)

    def summary(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "title": self.title, "category": self.category,
                "facts_available": len(self.facts), "video_id": self.video_id if self.video_public else None,
                "video_public": self.video_public, "score": round(self.score, 3)}


def topic_key(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return " ".join(words)[:120]


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(re.findall(r"[a-z0-9]+", (text or "").lower())).encode()).hexdigest()


def trigrams(text: str) -> set:
    w = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {" ".join(w[i:i + 3]) for i in range(len(w) - 2)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def opening_of(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())[:5])


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_fact(text: Any) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”')
    if not (25 <= len(t) <= 260) or _URL.search(t) or t.lower().startswith(("source", "sources")):
        return None
    if t.endswith(("...", "…")) or t.count('"') % 2:      # truncated in the dossier: never post half a fact
        return None
    if not t.endswith((".", "!", "?")):
        t += "."
    return t[0].upper() + t[1:]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "")) if s.strip()]


# -- sources ----------------------------------------------------------------------------------------------------
class SourceReader:
    """Read-only access to production data. ``db_path`` is opened with ``mode=ro``; files are bounded."""

    def __init__(self, db_path: str, media_dir: Path):
        self.db_path = str(db_path)
        self.media_dir = Path(media_dir)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    @staticmethod
    def _has_table(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def channel_categories(self) -> List[str]:
        cats: set = set()
        try:
            conn = self._conn()
        except sqlite3.Error:
            return []
        try:
            for table in ("content_ledger", "creative_identity_registry"):
                if self._has_table(conn, table):
                    cats |= {str(r[0]).strip() for r in conn.execute(
                        f"SELECT DISTINCT category FROM {table} WHERE category IS NOT NULL LIMIT 200") if str(r[0]).strip()}
        finally:
            conn.close()
        return sorted(cats)

    def _videos(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not self._has_table(conn, "publishing_ledger"):
            return out
        for r in conn.execute("SELECT video_path, youtube_video_id, upload_status, visibility FROM publishing_ledger "
                              "ORDER BY id DESC LIMIT 500"):
            m = re.search(r"productions[\\/]+([A-Za-z0-9_\-]{1,80})[\\/]", str(r["video_path"] or ""))
            vid = str(r["youtube_video_id"] or "")
            if m and m.group(1) not in out and YOUTUBE_ID.match(vid):
                out[m.group(1)] = {"video_id": vid, "public": str(r["visibility"] or "").lower() == "public"
                                   and str(r["upload_status"] or "").startswith("uploaded")}
        return out

    def _dossier(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not SAFE_ID.match(job_id):
            return None
        path = self.media_dir / "productions" / job_id / "research" / f"research_{job_id}.json"
        try:
            if not path.is_file() or path.stat().st_size > MAX_DOSSIER_BYTES:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _manifest(self, job_id: str) -> Dict[str, Any]:
        path = self.media_dir / "productions" / job_id / "metadata" / f"attribution_manifest_{job_id}.json"
        try:
            if path.is_file() and path.stat().st_size <= MAX_DOSSIER_BYTES:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            pass
        return {}

    def sources(self, now: datetime) -> List[Source]:
        try:
            conn = self._conn()
        except sqlite3.Error:
            return []
        out: List[Source] = []
        try:
            videos = self._videos(conn)
            if self._has_table(conn, "persistent_jobs"):
                rows = conn.execute("SELECT id, topic_title, completed_at FROM persistent_jobs WHERE status='COMPLETED' "
                                    "AND completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 60").fetchall()
                for r in rows:
                    created = _parse_dt(r["completed_at"])
                    if created and now - created > timedelta(days=SOURCE_DAYS):
                        continue
                    job = str(r["id"])
                    d = self._dossier(job)
                    if not d:
                        continue
                    facts = [f for f in (_clean_fact(b) for b in (d.get("factual_bullets") or [])[:20]) if f]
                    if not facts:
                        continue
                    title = str(d.get("title") or r["topic_title"] or "").strip()[:200]
                    manifest = self._manifest(job)
                    items = [i for i in (manifest.get("items") or []) if isinstance(i, dict) and i.get("verified")]
                    v = videos.get(job, {})
                    corpus = "\n".join(str(p) for p in [title, d.get("summary"), d.get("extended_extract"), *facts] if p)
                    out.append(Source(
                        kind="production", ref=job, title=title, category=str(d.get("category") or "").strip(),
                        facts=facts, corpus=corpus, hook=_clean_fact(d.get("winning_hook")) or facts[0],
                        score=float(d.get("composite_score") or 0.0), created_at=created,
                        video_id=v.get("video_id"), video_public=bool(v.get("public")),
                        licensed_images=len([i for i in items if i.get("asset_type") == "image"]),
                        repositories=sorted({str(i.get("source_repository")) for i in items if i.get("source_repository")})))
            if self._has_table(conn, "topic_candidate_ledger"):
                for r in conn.execute("SELECT candidate_id, title, summary, category, score, last_discovered_at "
                                      "FROM topic_candidate_ledger WHERE selection_status='ELIGIBLE' "
                                      "ORDER BY last_discovered_at DESC LIMIT 40"):
                    created = _parse_dt(r["last_discovered_at"])
                    if created and now - created > timedelta(days=CANDIDATE_DAYS):
                        continue
                    facts = [f for f in (_clean_fact(s) for s in _sentences(str(r["summary"] or ""))[:6]) if f]
                    if not facts or not str(r["category"] or "").strip():
                        continue
                    title = str(r["title"] or "").strip()[:200]
                    out.append(Source(kind="candidate", ref=str(r["candidate_id"])[:80], title=title,
                                      category=str(r["category"]).strip(), facts=facts,
                                      corpus="\n".join([title, str(r["summary"] or "")]), hook=facts[0],
                                      score=float(r["score"] or 0.0), created_at=created))
        finally:
            conn.close()
        return out


# -- selection ----------------------------------------------------------------------------------------------------
def _numeric_facts(src: Source) -> List[str]:
    return [f for f in src.facts if re.search(r"\d", f)]


def feasible(fmt: str, src: Source) -> bool:
    if fmt == "video_followup":
        return src.kind == "production" and src.video_public and bool(src.video_id)
    if fmt == "multiple_choice":
        return any(_mcq_parts(f) for f in _numeric_facts(src))
    if fmt == "trivia":
        return any(h in src.category.lower() for h in TRIVIA_HINTS) and bool(_numeric_facts(src))
    if fmt == "behind_the_scenes":
        return True
    return bool(src.facts)


def score_source(src: Source, now: datetime, recent: Sequence[Dict[str, Any]]) -> float:
    age = (now - src.created_at).total_seconds() / 86400 if src.created_at else SOURCE_DAYS
    s = 0.4 * min(max(src.score, 0.0), 10.0) / 10.0 + 0.3 * max(0.0, 1 - age / SOURCE_DAYS)
    s += 0.2 if src.kind == "production" else 0.05
    s += 0.1 if src.video_public else 0.0
    cats = [str(d.get("category") or "").lower() for d in recent[:3]]
    if cats and src.category.lower() == cats[0]:
        s -= 0.25
    elif src.category.lower() in cats:
        s -= 0.1
    return round(s, 4)


def select(sources: Sequence[Source], recent: Sequence[Dict[str, Any]], formats: Sequence[str], now: datetime, *,
           channel_categories: Sequence[str], excluded_categories: Sequence[str] = (),
           exclude_refs: Sequence[str] = ()) -> Tuple[Optional[Source], Optional[str], Dict[str, Any]]:
    """Returns (source, format, explanation). Topics used in the last 60 days, off-channel categories and excluded
    categories are never chosen. Formats rotate in the configured order, starting after the last used one, and the
    format used on each of the last two drafts is skipped while another feasible format exists."""
    used_topics = {d.get("topic_key") for d in recent
                   if (d.get("created_at") and now - d["created_at"] <= timedelta(days=TOPIC_REUSE_DAYS))}
    chan = {c.lower() for c in channel_categories}
    excl = {c.lower() for c in excluded_categories}
    rejected: Dict[str, int] = {"topic_reused": 0, "off_channel": 0, "excluded_category": 0, "excluded_ref": 0}
    eligible: List[Source] = []
    for s in sources:
        if s.topic_key in used_topics:
            rejected["topic_reused"] += 1
        elif s.ref in exclude_refs:
            rejected["excluded_ref"] += 1
        elif s.category.lower() in excl:
            rejected["excluded_category"] += 1
        elif chan and s.category.lower() not in chan:
            rejected["off_channel"] += 1
        else:
            eligible.append(s)
    ranked = sorted(eligible, key=lambda s: score_source(s, now, recent), reverse=True)
    order = [f for f in formats if f in FORMATS] or list(FORMATS)
    last = [d.get("format") for d in recent[:2] if d.get("format")]
    start = (order.index(last[0]) + 1) % len(order) if last and last[0] in order else 0
    rotation = order[start:] + order[:start]
    preferred = [f for f in rotation if f not in last] + [f for f in rotation if f in last]
    for fmt in preferred:
        for s in ranked:
            if feasible(fmt, s):
                return s, fmt, {"format_rotation": rotation, "skipped_recent_formats": last, "candidates": len(sources),
                                "eligible": len(eligible), "rejected": rejected,
                                "score": score_source(s, now, recent)}
    return None, None, {"format_rotation": rotation, "candidates": len(sources), "eligible": len(eligible),
                        "rejected": rejected, "reason": "no eligible topic has the facts any permitted format needs"}


# -- templates -------------------------------------------------------------------------------------------------------
OPENINGS = {
    "fact": ["From our research desk:", "Here is a detail worth knowing.", "Today's science note:",
             "Something we learned while researching {short}:"],
    "educational_question": ["A question from today's research:", "Here is one to think about:",
                             "Quick question for curious minds:"],
    "multiple_choice": ["Quiz time.", "Test your knowledge.", "Can you fill the gap?"],
    "behind_the_scenes": ["Behind the scenes at CrazzyDocs:", "How we make our shorts:", "A look behind our shorts:"],
    "video_followup": ["In a recent short we looked at {short}.", "Following up on our short about {short}."],
    "trivia": ["{category} trivia:", "A piece of {category_lower} trivia:", "Trivia from our research:"],
    "curiosity": ["Curious fact:", "Small detail, big question:", "Worth a second look:"],
    "engagement_prompt": ["Over to you.", "We want to hear from you.", "Your turn."],
}
QUESTIONS = {
    "fact": ["Had you heard about this before?", "Did this surprise you?", "What would you want to know next about this?"],
    "educational_question": ["What else would you like to learn about it?", "How much of this did you already know?",
                             "What question would you ask next?"],
    "multiple_choice": ["Reply with A, B or C. We will share the answer in the comments.",
                        "Which one is right: A, B or C? The answer follows in the comments.",
                        "Make your guess, A, B or C, before you look it up."],
    "behind_the_scenes": ["What topic should we research next?", "Which part of making a short would you like to see?"],
    "video_followup": ["What should we cover next?", "What question did the short leave you with?"],
    "trivia": ["Which {category_lower} topic should we explore next?", "Did you know this one already?"],
    "curiosity": ["What is your take?", "Would you have guessed it?"],
    "engagement_prompt": ["If you could ask the people behind {short} one question, what would it be?",
                          "What would you most like to learn about {short}?",
                          "Which part of {short} should we dig into next?"],
}
CTAS = {
    "fact": ["", "Tell us in the comments.", "Share your thoughts below."],
    "educational_question": ["Tell us in the comments.", "", "We read every reply."],
    "multiple_choice": [""],
    "behind_the_scenes": ["Tell us in the comments.", ""],
    "video_followup": ["", "Tell us in the comments."],
    "trivia": ["", "Share your thoughts below."],
    "curiosity": [""],
    "engagement_prompt": ["Tell us in the comments.", "We read every reply."],
}


def _short(title: str, n: int = 60) -> str:
    t = re.sub(r"\s+", " ", title or "").strip().rstrip(".")
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") or t[:n]


def _pick(pool: Sequence[str], recent_keys: set, prefix: str, seed: str) -> Tuple[int, str]:
    start = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
    for i in range(len(pool)):
        idx = (start + i) % len(pool)
        if f"{prefix}.{idx}" not in recent_keys:
            return idx, f"{prefix}.{idx}"
    return start, f"{prefix}.{start}"


def _mcq_parts(fact: str) -> Optional[Dict[str, Any]]:
    for m in _INT.finditer(fact):
        raw = m.group(1)
        n = int(raw.replace(",", ""))
        if n < 2:
            continue
        if _YEAR.fullmatch(raw):
            opts = sorted({n - 20, n, n + 15})
            if max(opts) > datetime.now(timezone.utc).year:
                opts = sorted({n - 30, n - 15, n})
        else:
            half = n // 2 if n // 2 != n else n + 1
            opts = sorted({half, n, n * 2})
        if len(opts) != 3:
            continue
        fmt = (lambda v: f"{v:,}") if "," in raw else str
        labels = ["A", "B", "C"]
        return {"blanked": fact[:m.start()] + "___" + fact[m.end():], "options": [fmt(o) for o in opts],
                "answer": labels[opts.index(n)], "answer_value": raw, "distractors": [str(o) for o in opts if o != n]}
    return None


def compose(src: Source, fmt: str, recent: Sequence[Dict[str, Any]], seed: str) -> Dict[str, Any]:
    """Deterministic template text for ``fmt``. Opening, question and call to action avoid the variants used in the
    last 14 drafts. Returns text plus the metadata validation needs (allowed numbers, rotation keys, answer)."""
    keys = set()
    for d in recent[:ROTATION_LOOKBACK]:
        keys |= {d.get("opening_key"), d.get("question_key"), d.get("cta_key")}
    ctx = {"short": _short(src.title), "category": src.category or "Science",
           "category_lower": (src.category or "science").lower()}
    o_idx, o_key = _pick(OPENINGS[fmt], keys, f"{fmt}.open", seed + "o")
    q_idx, q_key = _pick(QUESTIONS[fmt], keys, f"{fmt}.q", seed + "q")
    c_idx, c_key = _pick(CTAS[fmt], keys, f"{fmt}.cta", seed + "c")
    opening = OPENINGS[fmt][o_idx].format(**ctx)
    question = QUESTIONS[fmt][q_idx].format(**ctx)
    cta = CTAS[fmt][c_idx]
    numeric = _numeric_facts(src)
    fact_i = int(hashlib.sha256((seed + "f").encode()).hexdigest(), 16)
    fact = src.facts[fact_i % len(src.facts)]
    meta: Dict[str, Any] = {"allowed_numbers": [], "extra_corpus": ""}
    if fmt == "fact":
        body = fact
    elif fmt == "educational_question":
        body = f"What do you know about {ctx['short']}? Here is one detail from our research: {fact}"
    elif fmt == "multiple_choice":
        parts = next(p for p in (_mcq_parts(f) for f in numeric) if p)
        body = f"Fill in the blank: \"{parts['blanked']}\"\nA) {parts['options'][0]}   B) {parts['options'][1]}   C) {parts['options'][2]}"
        meta.update({"answer": parts["answer"], "answer_value": parts["answer_value"],
                     "allowed_numbers": parts["distractors"]})
    elif fmt == "behind_the_scenes":
        pf = PROCESS_FACTS[fact_i % len(PROCESS_FACTS)]
        if src.video_public and src.licensed_images:
            extra = (f"Our short on {ctx['short']} used {src.licensed_images} licensed "
                     f"image{'s' if src.licensed_images != 1 else ''}"
                     + (f" from {', '.join(src.repositories[:3])}" if src.repositories else "") + ".")
            meta["extra_corpus"] = extra
            body = f"{pf} {extra}"
        else:
            body = pf
        meta["extra_corpus"] = (meta["extra_corpus"] + "\n" + pf).strip()
    elif fmt == "video_followup":
        body = f"One more detail from our research: {fact}\nWatch it here: https://youtube.com/shorts/{src.video_id}"
    elif fmt == "trivia":
        body = (numeric or src.facts)[fact_i % len(numeric or src.facts)]
    elif fmt == "curiosity":
        body = src.hook if len(src.hook) <= 200 else fact
    else:   # engagement_prompt
        body = ""
    text = "\n\n".join(p for p in [opening, body, (question + (" " + cta if cta else "")).strip()] if p)
    return {"text": text, "format": fmt, "opening_key": o_key, "question_key": q_key, "cta_key": c_key if cta else None,
            "fact_used": body if fmt not in ("engagement_prompt", "multiple_choice") else None, **meta}


# -- optional Gemini wording ---------------------------------------------------------------------------------------
GEMINI_TIMEOUT_MS = 30_000


def gemini_generate(prompt: str) -> str:
    """One call through the existing Gemini SDK and key (``GEMINI_API_KEY`` / ``GEMINI_MODEL``, as the grounded script
    writer uses). Raises when Gemini is not configured. 30 s timeout, no retries."""
    import os
    try:
        from backend.app.core.config import settings
        key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("GEMINI_MODEL") or settings.DEFAULT_GEMINI_MODEL
    except Exception:
        key, model = os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_MODEL", "")
    if not key or not model:
        raise RuntimeError("Gemini is not configured")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    resp = client.models.generate_content(model=model, contents=prompt)
    return str(getattr(resp, "text", "") or "")


def gemini_prompt(src: Source, fmt: str, template_text: str) -> str:
    facts = "\n".join(f"- {f}" for f in src.facts[:6])
    return ("Rewrite this YouTube Community post for an educational science and history channel. Keep it under 450 "
            "characters, friendly and clear, with one question for the audience. Use ONLY the facts listed; do not add "
            "numbers, names, dates or claims that are not in them. No hashtags, no emojis, no links, no 'subscribe' "
            "or 'like'. Return only the post text.\n\n"
            f"Format: {FORMAT_LABELS.get(fmt, fmt)}\nTopic: {src.title}\nFacts:\n{facts}\n\nDraft:\n{template_text}")


def write_text(src: Source, fmt: str, recent: Sequence[Dict[str, Any]], seed: str, *, mode: str = "template",
               generate: Optional[Callable[[str], str]] = None,
               check: Optional[Callable[[str, Dict[str, Any]], bool]] = None) -> Dict[str, Any]:
    """Template text, optionally reworded by Gemini. Gemini output is kept only if ``check`` accepts it; otherwise
    (or on any error) the template text is used and the reason is recorded."""
    out = compose(src, fmt, recent, seed)
    out["text_mode"] = "template"
    if mode != "gemini" or fmt in ("multiple_choice", "video_followup") or generate is None:
        if mode == "gemini":
            out["gemini"] = {"used": False, "reason": "format keeps template text" if generate else "not configured"}
        return out
    try:
        candidate = re.sub(r"\s+\n", "\n", (generate(gemini_prompt(src, fmt, out["text"])) or "").strip())[:600]
    except Exception as exc:                       # noqa: BLE001 - optional path; never fails the draft
        out["gemini"] = {"used": False, "called": True, "reason": f"{type(exc).__name__}"}
        return out
    if candidate and (check is None or check(candidate, out)):
        out.update({"text": candidate, "text_mode": "gemini", "gemini": {"used": True, "called": True}})
    else:
        out["gemini"] = {"used": False, "called": True, "reason": "output failed validation; template text kept"}
    return out
