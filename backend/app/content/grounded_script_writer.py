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
import time
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
TRANSIENT_RETRIES = 2                    # per attempt, for 429 / 5xx / timeouts only
BACKOFF_SEC = (4.0, 12.0)
_TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}
MODEL_BUDGET_SEC = 180.0                 # stop retrying one model after this much wall time
# Used only when the configured model fails with transient errors (overload, timeouts). The model
# that actually wrote the script is recorded in script_source, so a fallback model is never hidden.
DEFAULT_FALLBACK_MODELS = ("gemini-3.6-flash",)


def model_chain(primary: Optional[str]) -> List[str]:
    raw = os.environ.get("GEMINI_FALLBACK_MODELS")
    extra = [m.strip() for m in raw.split(",")] if raw is not None else list(DEFAULT_FALLBACK_MODELS)
    chain: List[str] = []
    for m in [primary or ""] + extra:
        if m and m not in chain:
            chain.append(m)
    return chain

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
    from backend.app.content.number_words import unsupported_claims
    spelled = [q for q in unsupported_claims(_NUMBER.sub(" ", full), corpus)]
    if spelled:   # spelled-out quantities ("nine million dollars") must be in the research too
        problems.append(f"spelled-out numbers not found in the research: {', '.join(spelled)}")
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


def _usage(resp: Any) -> Dict[str, Any]:
    """Token counts exactly as the API reports them; absent fields stay None (never estimated)."""
    um = getattr(resp, "usage_metadata", None)
    get = (lambda k: getattr(um, k, None)) if um is not None else (lambda k: None)
    cands = getattr(resp, "candidates", None) or []
    finish = getattr(cands[0], "finish_reason", None) if cands else None
    return {"input_tokens": get("prompt_token_count"), "output_tokens": get("candidates_token_count"),
            "thinking_tokens": get("thoughts_token_count"), "total_tokens": get("total_token_count"),
            "model_version": getattr(resp, "model_version", None),
            "finish_reason": str(getattr(finish, "name", finish)) if finish is not None else None}


def describe_error(exc: BaseException) -> Dict[str, Any]:
    """Error type and HTTP status only. The message is never recorded: it can echo request details."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    timeout = "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
    return {"error_type": type(exc).__name__, "http_status": code,
            "status": str(getattr(exc, "status", "") or "")[:40] or None,
            "transient": bool(timeout or code in _TRANSIENT_CODES)}


def _generate(client: Any, model: str, prompt: str, telemetry: Optional[Dict[str, Any]] = None) -> str:
    try:
        from google.genai import types
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.85)
    except Exception:
        config = None
    kwargs = {"model": model, "contents": prompt}
    if config is not None:
        kwargs["config"] = config
    t0 = time.perf_counter()
    resp = client.models.generate_content(**kwargs)
    if telemetry is not None:
        telemetry.update(latency_ms=round((time.perf_counter() - t0) * 1000), response_status="ok", **_usage(resp))
    return resp.text


def _call_with_backoff(client: Any, model: str, prompt: str, sleep=None) -> Tuple[str, Dict[str, Any]]:
    """One logical attempt: retries only transient API failures, with exponential backoff."""
    calls: List[Dict[str, Any]] = []
    started = time.perf_counter()
    for n in range(TRANSIENT_RETRIES + 1):
        tel: Dict[str, Any] = {"call": n + 1}
        t0 = time.perf_counter()
        try:
            text = _generate(client, model, prompt, tel)
            calls.append(tel)
            return text, {"calls": calls}
        except Exception as exc:
            tel.update(latency_ms=round((time.perf_counter() - t0) * 1000), response_status="error", **describe_error(exc))
            calls.append(tel)
            if not tel["transient"] or n == TRANSIENT_RETRIES or time.perf_counter() - started > MODEL_BUDGET_SEC:
                exc.calls = calls   # type: ignore[attr-defined]
                raise
            (sleep or time.sleep)(BACKOFF_SEC[min(n, len(BACKOFF_SEC) - 1)])
    raise RuntimeError("unreachable")


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
        for current in model_chain(model):
            feedback: List[str] = []
            api_unavailable = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                calls: List[Dict[str, Any]] = []
                try:
                    raw, info = _call_with_backoff(client, current, build_prompt(topic, hook_type, hook_text, feedback))
                    calls = info["calls"]
                    data = _parse(raw)
                    sections = data.get("sections") or []
                    feedback = validate_sections(sections, corpus)
                except Exception as exc:   # API, quota, timeout or malformed JSON: never fail the production
                    calls = calls or list(getattr(exc, "calls", []))
                    feedback = [f"{type(exc).__name__}"]
                    attempts.append({"attempt": attempt, "model": current, "accepted": False,
                                     "problems": feedback, "calls": calls})
                    if not isinstance(exc, (ValueError, KeyError, TypeError)):
                        last = calls[-1] if calls else {}
                        # overloaded/timed-out after bounded retries, or model not served: next model.
                        # Any other API error (auth, network down, bad request) ends the attempt.
                        api_unavailable = bool(last.get("transient") or last.get("http_status") == 404)
                        break
                    continue
                attempts.append({"attempt": attempt, "model": current, "accepted": not feedback,
                                 "problems": feedback, "calls": calls, "draft": data if feedback else None})
                if not feedback:
                    policy_arc, category = _arc_and_category(topic, execution_context)
                    return _with_quality(_assemble(sections, policy_arc, category, script_source=f"llm:{current}",
                                                   model=current, configured_model=model,
                                                   hook_type=hook_type[0], angle=str(data.get("angle", ""))[:300],
                                                   writer_attempts=attempts), topic)
            if not api_unavailable:
                break   # drafts failed validation, or a non-transient API error: another model is not the fix
        logger.warning("Script writer: no accepted model draft, using extractive fallback: %s",
                       [{k: a.get(k) for k in ("attempt", "model", "problems")} for a in attempts])

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
