"""
Grounded narration writer.

Until 2026-11-13 every narration was `compose_structured_script`: sentences lifted from the
Wikipedia lead in order, so a video about "Science fiction" opened with
"Science fiction (often shortened to sci-fi or abbreviated SF) is the genre of ...".

This module asks a language model (Gemini, the provider the project is already built on) to
*write* a spoken Shorts script from the research dossier, then refuses anything it cannot
verify:

  * exactly the five narrative stages the storyboard and TTS pipeline expect;
  * a spoken length that fits the 58 s ceiling with the 2 s outro;
  * every number in the script appears in the research (no invented statistics);
  * no 9-word run copied from the source (it must be written, not pasted);
  * no parentheticals, URLs, stage directions or calls to subscribe.

Anything that fails twice, a missing API key, or any API error falls back to the extractive
composer, cleaned of parentheticals, and the script records which path produced it. A writer
problem never fails a production.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

STAGES = ("HOOK", "CONTEXT", "ESCALATION", "REVELATION", "PAYOFF")
EMOTIONS = {"HOOK": "hook", "CONTEXT": "curiosity", "ESCALATION": "tension",
            "REVELATION": "revelation", "PAYOFF": "closing"}
SECTION_IDS = {"HOOK": "seg_01_hook", "CONTEXT": "seg_02_context", "ESCALATION": "seg_03_escalation",
               "REVELATION": "seg_04_revelation", "PAYOFF": "seg_05_payoff"}
MIN_WORDS, MAX_WORDS = 95, 126          # ~2.3 words/s: 41-55 s of speech + 2 s outro <= 58 s
COPY_NGRAM = 9
MAX_ATTEMPTS = 2

# Controlled variation (audit Phase 2.5): the hook structure rotates by job, so two
# productions never share a hook shape by accident. Quality comes from the validators.
HOOK_TYPES = (
    ("surprising_fact", "Open on the single most surprising verified fact, stated plainly."),
    ("direct_question", "Open with a question the viewer cannot answer yet but wants to."),
    ("contradiction", "Open on what most people assume, then signal that it is wrong or incomplete."),
    ("stakes", "Open on what was at stake: who or what could have been lost or changed."),
    ("scale", "Open on a size, speed, distance, cost or count from the research that makes the subject vivid."),
)

_BANNED = re.compile(r"\(|\)|https?://|www\.|\[|\]|\bsubscribe\b|\blike and\b|\bin this video\b", re.I)
_NUMBER = re.compile(r"\d[\d,.]*")


def choose_hook_type(job_id: str) -> Tuple[str, str]:
    idx = int(hashlib.sha256((job_id or "").encode("utf-8")).hexdigest(), 16) % len(HOOK_TYPES)
    return HOOK_TYPES[idx]


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _norm_number(tok: str) -> str:
    return tok.replace(",", "").rstrip(".")


def fact_corpus(topic: Any) -> str:
    parts = [getattr(topic, "title", ""), getattr(topic, "summary", ""),
             getattr(topic, "extended_extract", "")]
    parts.extend(getattr(topic, "factual_bullets", None) or [])
    return "\n".join(str(p) for p in parts if p)


def validate_sections(sections: Sequence[Dict[str, Any]], corpus: str) -> List[str]:
    """Returns the list of problems; empty means the script is acceptable."""
    problems: List[str] = []
    stages = [str(s.get("stage", "")).upper() for s in sections]
    if tuple(stages) != STAGES:
        return [f"sections must be exactly {', '.join(STAGES)} in that order (got {stages})"]
    texts = [str(s.get("text", "")).strip() for s in sections]
    if any(not t for t in texts):
        problems.append("every section needs spoken text")
    full = " ".join(texts)
    n = len(full.split())
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"total length must be {MIN_WORDS}-{MAX_WORDS} words (got {n})")
    if _BANNED.search(full):
        problems.append("no parentheses, brackets, URLs, or calls to like/subscribe")
    known = {_norm_number(t) for t in _NUMBER.findall(corpus)}
    invented = sorted({_norm_number(t) for t in _NUMBER.findall(full)} - known - {""})
    if invented:
        problems.append(f"numbers not found in the research: {', '.join(invented)}")
    src = _words(corpus)
    src_grams = {" ".join(src[i:i + COPY_NGRAM]) for i in range(len(src) - COPY_NGRAM + 1)}
    out = _words(full)
    copied = [" ".join(out[i:i + COPY_NGRAM]) for i in range(len(out) - COPY_NGRAM + 1)
              if " ".join(out[i:i + COPY_NGRAM]) in src_grams]
    if copied:
        problems.append(f"copied verbatim from the source, rewrite in your own words: \"{copied[0]}\"")
    return problems


def build_prompt(topic: Any, hook_type: Tuple[str, str], suggested_hook: str,
                 feedback: Sequence[str] = ()) -> str:
    research = fact_corpus(topic)[:6000]
    lines = [
        "You write narration for a 50-second educational YouTube Short in the style of a sharp,",
        "friendly explainer channel. Write for the ear: short sentences, concrete images, one idea",
        "per sentence, no jargon left unexplained.",
        "",
        f"Topic: {getattr(topic, 'title', '')}",
        f"Category: {getattr(topic, 'category', '')}",
        f"Hook style: {hook_type[0]}. {hook_type[1]}",
        f"A candidate hook you may improve or replace: {suggested_hook}" if suggested_hook else "",
        "",
        "Rules:",
        f"- Total {MIN_WORDS + 10}-{MAX_WORDS - 4} words across five sections: HOOK (1-2 sentences),",
        "  CONTEXT (what it is, why it matters), ESCALATION (the surprising mechanism or turn),",
        "  REVELATION (the key insight), PAYOFF (one memorable closing line that answers the hook).",
        "- Use ONLY facts from the research below. Every number you use must appear in it.",
        "  If a detail is not in the research, leave it out.",
        f"- Do not copy any run of {COPY_NGRAM} or more words from the research. Rewrite.",
        "- No parentheses, no URLs, no stage directions, no 'subscribe' or 'in this video'.",
        "- Do not open with a dictionary definition of the topic.",
        "",
        "Return JSON only:",
        '{"angle": "one sentence: the specific angle this script takes",',
        ' "sections": [{"stage": "HOOK", "text": "..."}, {"stage": "CONTEXT", "text": "..."},',
        '              {"stage": "ESCALATION", "text": "..."}, {"stage": "REVELATION", "text": "..."},',
        '              {"stage": "PAYOFF", "text": "..."}]}',
        "",
        "Research:",
        research,
    ]
    if feedback:
        lines += ["", "Your previous draft was rejected. Fix exactly these problems:"]
        lines += [f"- {p}" for p in feedback]
    return "\n".join(line for line in lines if line is not None)


def _parse(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def default_client() -> Tuple[Any, Optional[str]]:
    """A Gemini client when GEMINI_API_KEY is configured, else (None, None)."""
    try:
        from backend.app.core.config import settings
        key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("GEMINI_MODEL") or settings.DEFAULT_GEMINI_MODEL
    except Exception:
        key, model = os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_MODEL", "")
    if not key or not model:
        return None, None
    from google import genai
    from google.genai import types
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=90_000)), model


def _generate(client: Any, model: str, prompt: str) -> str:
    try:
        from google.genai import types
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.85)
    except Exception:
        config = None
    kwargs = {"model": model, "contents": prompt}
    if config is not None:
        kwargs["config"] = config
    return client.models.generate_content(**kwargs).text


def _assemble(sections: Sequence[Dict[str, Any]], arc: Sequence[str], category: str,
              **meta: Any) -> Dict[str, Any]:
    # Same arc tags compose_structured_script produces: arc[0], CONTEXT, arc[1], arc[2], arc[3].
    tags = [arc[0] if len(arc) > 0 else "HOOK", "CONTEXT", arc[1] if len(arc) > 1 else "ESCALATION",
            arc[2] if len(arc) > 2 else "REVELATION", arc[3] if len(arc) > 3 else "PAYOFF"]
    out = []
    for i, stage in enumerate(STAGES):
        text = re.sub(r"\s+", " ", str(sections[i]["text"])).strip()
        out.append({"id": SECTION_IDS[stage], "stage": stage,
                    "arc_phase": str(tags[i]).upper()[:16],
                    "emotion": EMOTIONS[stage], "text": text})
    full = " ".join(s["text"] for s in out)
    return {"full_script": full, "sections": out, "word_count": len(full.split()),
            "category": category, "narrative_arc": list(arc), **meta}


def _strip_parentheticals(script: Dict[str, Any]) -> Dict[str, Any]:
    for sec in script.get("sections", []):
        text = re.sub(r"\s*\([^)]*\)", "", sec.get("text", ""))
        sec["text"] = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", text)).strip()
    script["full_script"] = " ".join(s["text"] for s in script.get("sections", []))
    script["word_count"] = len(script["full_script"].split())
    return script


def write_grounded_script(topic: Any, hook_text: str, *, job_id: str, execution_context: Any,
                          client: Any = None, model: Optional[str] = None) -> Dict[str, Any]:
    from backend.app.rendering.documentary_storyboard_builder import compose_structured_script

    hook_type = choose_hook_type(job_id)
    if client is None:
        try:
            client, model = default_client()
        except Exception as exc:
            logger.warning("Script writer: Gemini client unavailable (%s)", type(exc).__name__)
            client = None
    attempts: List[Dict[str, Any]] = []
    if client is not None and model:
        corpus = fact_corpus(topic)
        feedback: List[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                data = _parse(_generate(client, model, build_prompt(topic, hook_type, hook_text, feedback)))
                sections = data.get("sections") or []
                feedback = validate_sections(sections, corpus)
            except Exception as exc:   # API, quota, timeout or malformed JSON: never fail the production
                feedback = [f"{type(exc).__name__}"]
                attempts.append({"attempt": attempt, "accepted": False, "problems": feedback})
                if not isinstance(exc, (ValueError, KeyError, TypeError)):
                    break   # transport/API errors: a second call will not help this cycle
                continue
            attempts.append({"attempt": attempt, "accepted": not feedback, "problems": feedback})
            if not feedback:
                policy_arc, category = _arc_and_category(topic, execution_context)
                return _with_quality(_assemble(sections, policy_arc, category, script_source=f"llm:{model}",
                                               hook_type=hook_type[0], angle=str(data.get("angle", ""))[:300],
                                               writer_attempts=attempts), topic)
        logger.warning("Script writer: model draft rejected, using extractive fallback: %s", attempts)

    script = _strip_parentheticals(compose_structured_script(topic, hook_text, execution_context=execution_context))
    script.update(script_source="extractive_fallback", hook_type="", angle="",
                  writer_attempts=attempts or [{"skipped": "no GEMINI_API_KEY configured"}])
    return _with_quality(script, topic)


def _with_quality(script: Dict[str, Any], topic: Any) -> Dict[str, Any]:
    """Attaches the structured script-quality evaluation; an evaluator problem never blocks a script."""
    try:
        from backend.app.content.script_quality import evaluate_script
        script["quality"] = evaluate_script(script, topic)
    except Exception as exc:
        script["quality"] = {"overall": None, "error": type(exc).__name__}
    return script


def _arc_and_category(topic: Any, execution_context: Any) -> Tuple[List[str], str]:
    from backend.app.content.creative_production_policy import get_creative_production_policy
    spec = get_creative_production_policy(execution_context).get_category(getattr(topic, "category", "science"))
    return list(spec.narrative_arc or ["question", "mechanism", "evidence", "revelation"]), spec.display_name
