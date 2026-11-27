"""Stage 4.02: English Short -> natural, culturally adapted spoken Hindi, scene by scene.

Provider: the Gemini free tier already configured for English scripts (zero cost). The Hindi model chain EXCLUDES the
models English production uses, because free-tier limits are per model and English scripts must not lose quota to
Hindi. Order: ``CRAZZYDOCS_HINDI_MODELS`` (comma list) or ``DEFAULT_HINDI_MODELS``.

* A transient provider error (429 / 5xx / timeout) on every model -> ``ProviderUnavailable`` (an infrastructure
  wait; the localization retries later and keeps any completed work).
* A malformed or rule-breaking answer -> one retry with the problems as feedback, then ``TranslationRejected``
  (a creative rejection, never retried as infrastructure).

The request is per scene, so captions, headlines and timing stay aligned with the English film. The answer carries a
term list (English form, Hindi display form, spoken Devanagari form) used for pronunciation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_HINDI_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemma-4-31b-it", "gemma-4-26b-a4b-it")
HINDI_WORDS_PER_SEC = 2.0          # measured 2026-11-27: edge-tts Swara speaks ~2.07 words/s at +8 %, 2.26 at +18 %
MAX_HINDI_WORDS_PER_SEC = 2.2      # per-scene ceiling given to the model (just under the +18 % pace)
TRANSIENT = {408, 429, 500, 502, 503, 504}


class ProviderUnavailable(RuntimeError):
    """Every model answered with a transient error: an infrastructure condition (retry later)."""


class TranslationRejected(RuntimeError):
    """The answers broke the localization rules after a feedback retry: a creative rejection."""

    def __init__(self, message: str, problems: Sequence[str]):
        super().__init__(message)
        self.problems = list(problems)


def hindi_models(english_chain: Sequence[str] = ()) -> List[str]:
    raw = os.environ.get("CRAZZYDOCS_HINDI_MODELS")
    models = [m.strip() for m in raw.split(",")] if raw else list(DEFAULT_HINDI_MODELS)
    exclude = set(english_chain)
    return [m for m in models if m and m not in exclude]


def build_request(*, topic: str, category: str, script: Dict[str, Any], plan: Dict[str, Any],
                  scale: float = 1.0) -> Dict[str, Any]:
    """``scale`` < 1 shrinks the word targets after the voice measured the previous adaptation as too long."""
    scenes = []
    for sc in plan.get("scenes") or []:
        secs = round(float(sc["end_sec"]) - float(sc["start_sec"]), 2)
        scenes.append({"scene_id": sc["scene_id"], "stage": sc.get("stage"), "seconds": secs,
                       "target_words": max(3, round(secs * HINDI_WORDS_PER_SEC * scale)),
                       # hard ceiling: what the fastest natural voice rate (+18 %) fits into the scene's time
                       "max_words": max(4, int(secs * MAX_HINDI_WORDS_PER_SEC * scale)),
                       "narration": sc.get("narration") or "", "headline": sc.get("headline") or "",
                       "highlight": sc.get("highlight") or "", "labels": list(sc.get("labels") or []),
                       "number_units": [n.get("unit") for n in (sc.get("numbers") or []) if n.get("unit")]})
    return {"topic": topic, "category": category, "scenes": scenes,
            "english_script": script.get("full_script") or " ".join(s.get("narration", "") for s in scenes),
            "total_seconds": round(float(plan.get("duration_sec") or 0.0), 2)}


def build_prompt(req: Dict[str, Any], feedback: Sequence[str] = ()) -> str:
    rules = [
        "You adapt an English YouTube Short into a NATURAL, CONVERSATIONAL Hindi Short for a Hindi-speaking audience in "
        "India. Write how a good Hindi science presenter actually speaks (simple, warm, energetic; everyday Hindi with "
        "common English loanwords where Hindi speakers really use them). NOT a word-for-word translation.",
        "Keep the meaning, every fact, every number and every name exactly. Do not add facts, numbers, dates or claims "
        "that are not in the English. Do not drop any fact.",
        "Adapt the hook, transitions, punchline and ending for Hindi audiences (a curiosity question or a surprising "
        "turn works well), keeping the same story arc and tone.",
        "narration_hi: spoken Hindi in Devanagari ONLY (write names and technical terms in Devanagari as they are "
        "pronounced, e.g. 'बायोल्यूमिनेसेंस'). Aim for about target_words words per scene and NEVER exceed max_words for a "
        "scene: the voice must fit each scene's seconds. Hindi tends to run longer than English, so when a scene is "
        "dense, say it more simply and briefly; keep every fact and number, drop filler words.",
        "Write numbers as digits (e.g. 1880, 3) exactly as in the English.",
        "headline_hi: a short on-screen headline in Devanagari, at most 5 words, for the same idea as the English "
        "headline. highlight_hi: copy EXACTLY one word or phrase that appears inside headline_hi (or \"\" if the "
        "English highlight is empty). labels_hi: the labels translated, 1-2 words each, same count.",
        "terms: every proper name and technical term you kept, as {\"en\": English form, \"hi\": Devanagari display "
        "form, \"spoken\": Devanagari spelling that a Hindi voice pronounces correctly}.",
        "Return ONLY JSON: {\"title_hi\": str, \"category_hi\": str, \"scenes\": [{\"scene_id\": str, \"narration_hi\": "
        "str, \"headline_hi\": str, \"highlight_hi\": str, \"labels_hi\": [str], \"units_hi\": [str]}], \"terms\": "
        "[{\"en\": str, \"hi\": str, \"spoken\": str}]}. One entry per scene, same scene_id order.",
    ]
    body = {"topic": req["topic"], "category": req["category"], "scenes": req["scenes"]}
    fb = ("\n\nYour previous answer had these problems; fix all of them:\n- " + "\n- ".join(feedback)) if feedback else ""
    return "\n".join(rules) + "\n\nENGLISH SHORT:\n" + json.dumps(body, ensure_ascii=False, indent=1) + fb


def _parse(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def _status_of(exc: BaseException) -> Optional[int]:
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        try:
            if v is not None:
                return int(v)
        except (TypeError, ValueError):
            pass
    return None


def translate(req: Dict[str, Any], *, client: Any, models: Sequence[str], validate,
              sleep=time.sleep, initial_feedback: Sequence[str] = ()) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Returns (hindi, attempts). ``validate(req, hindi) -> problems`` is the Hindi script QC.

    Per model: up to two attempts (the second with the first one's problems as feedback). A provider error moves to
    the next model. A model that answered but broke the rules twice ends the chain with TranslationRejected; if no
    model ever answered, ProviderUnavailable."""
    from google.genai import types
    attempts: List[Dict[str, Any]] = []
    rule_problems: List[str] = []
    for model in models:
        feedback: List[str] = list(initial_feedback)
        for attempt in (1, 2):
            t0 = time.perf_counter()
            cfg = (types.GenerateContentConfig(temperature=0.6, response_mime_type="application/json")
                   if not model.startswith("gemma") else types.GenerateContentConfig(temperature=0.6))
            try:
                resp = client.models.generate_content(model=model, contents=build_prompt(req, feedback), config=cfg)
            except Exception as exc:             # provider-level: try the next model
                attempts.append({"model": model, "attempt": attempt, "ok": False, "http_status": _status_of(exc),
                                 "error_type": type(exc).__name__, "latency_ms": round((time.perf_counter() - t0) * 1000)})
                break
            try:
                hindi = _parse(getattr(resp, "text", "") or "")
                problems = validate(req, hindi)
            except (ValueError, TypeError, KeyError) as exc:
                hindi, problems = None, [f"the answer was not valid JSON in the requested shape ({type(exc).__name__})"]
            attempts.append({"model": model, "attempt": attempt, "ok": not problems, "problems": problems,
                             "latency_ms": round((time.perf_counter() - t0) * 1000)})
            if not problems:
                hindi["_model"] = model
                return hindi, attempts
            rule_problems = problems
            feedback = list(problems) + list(initial_feedback)
        if rule_problems:
            break                                # the model answered but broke the rules: another model is not the fix
    if rule_problems:
        raise TranslationRejected("the Hindi adaptation broke the localization rules after a feedback retry", rule_problems)
    raise ProviderUnavailable("every Hindi model was unavailable (rate limit / overload / not served); retry later")
