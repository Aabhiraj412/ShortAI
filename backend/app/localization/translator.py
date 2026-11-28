"""Stage 4.02: English Short -> natural Indian Hinglish, scene by scene (language policy 2026-11-27).

The audience is Indian YouTube viewers: the narration is how an Indian creator actually talks (Hindi and English mixed
conversationally, English technical terms kept in English, no formal or textbook Hindi). Every text visible in the
video (headlines, labels, category, subtitles) is English or Roman-script Hinglish; Devanagari is never shown.

Each scene carries the same Hinglish sentence twice:
* ``narration``      Roman-script Hinglish: the subtitles (and the only narration text anyone sees);
* ``narration_tts``  the same words for the Hindi neural voice: Hindi words in Devanagari, English words in Latin.
                     A hi-IN voice mispronounces Hindi written in Roman script ("mein" -> "mine") but pronounces
                     this code-mixed form naturally. It is never shown.

Provider: the Gemini free tier already configured for English scripts (zero cost). The Hindi model chain EXCLUDES the
models English production uses, because free-tier limits are per model. Order: ``CRAZZYDOCS_HINDI_MODELS`` or
``DEFAULT_HINDI_MODELS``.

* A transient provider error (429 / 5xx / timeout) on every model -> ``ProviderUnavailable`` (an infrastructure
  wait; the localization retries later and keeps any completed work).
* A malformed or rule-breaking answer -> one retry with the problems as feedback, then ``TranslationRejected``.

Quota resilience: a model that answers 429 is put in a cooldown taken from the provider's own answer: until the
daily reset (midnight Pacific) for a per-day free-tier quota, else its ``retryDelay`` (default 15 min). A model that
is overloaded (5xx / timeout) cools down for ``OVERLOAD_COOLDOWN_SEC``. Models in cooldown are not called at all, so an
exhausted quota costs no further requests; ``ProviderUnavailable.retry_at`` says when the earliest model is usable
again, and the localization waits until then without using up its attempts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_HINDI_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemma-4-31b-it", "gemma-4-26b-a4b-it")
HINDI_WORDS_PER_SEC = 2.0          # measured 2026-11-27: edge-tts Swara speaks ~2.07 words/s at +8 %, 2.26 at +18 %
MAX_HINDI_WORDS_PER_SEC = 2.2      # per-scene ceiling given to the model (just under the +18 % pace)
TRANSIENT = {408, 429, 500, 502, 503, 504}
QUOTA_DEFAULT_COOLDOWN_SEC = 900
OVERLOAD_COOLDOWN_SEC = 600
MIN_COOLDOWN_SEC = 30

_cooldown: Dict[str, Tuple[float, str]] = {}      # model -> (epoch seconds usable again, why); this process only
_cooldown_lock = threading.Lock()


class ProviderUnavailable(RuntimeError):
    """Every model answered with a transient error or is cooling down: an infrastructure wait (retry at retry_at)."""

    def __init__(self, message: str, retry_at: Optional[datetime] = None, quota: bool = False):
        super().__init__(message)
        self.retry_at = retry_at
        self.quota = quota


def _next_pacific_midnight(now: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        pt = now.astimezone(ZoneInfo("America/Los_Angeles"))
        nxt = (pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)
    except Exception:                                   # no tz database: PST midnight (08:00 UTC)
        nxt = now.replace(hour=8, minute=0, second=0, microsecond=0)
        return nxt if nxt > now else nxt + timedelta(days=1)


def cooldown_for(exc: BaseException, status: Optional[int], now: Optional[datetime] = None) -> Tuple[float, str]:
    """(seconds, why) to leave a model alone after a provider error, from the provider's own answer."""
    now = now or datetime.now(timezone.utc)
    text = str(exc) + " " + str(getattr(exc, "details", "") or "")
    if status == 429 or "RESOURCE_EXHAUSTED" in text:
        if re.search(r"PerDay", text):
            until = _next_pacific_midnight(now) + timedelta(minutes=5)
            return max(MIN_COOLDOWN_SEC, (until - now).total_seconds()), "daily free-tier quota exhausted"
        m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
        if m:
            return max(MIN_COOLDOWN_SEC, float(m.group(1))), "rate limit (provider retryDelay)"
        return QUOTA_DEFAULT_COOLDOWN_SEC, "rate limit"
    return OVERLOAD_COOLDOWN_SEC, f"unavailable (HTTP {status or type(exc).__name__})"


def _cooling(model: str, now_epoch: float) -> Optional[Tuple[float, str]]:
    with _cooldown_lock:
        cd = _cooldown.get(model)
        if cd and cd[0] > now_epoch:
            return cd
        _cooldown.pop(model, None)
        return None


def _cool(model: str, seconds: float, why: str, now_epoch: float) -> None:
    with _cooldown_lock:
        _cooldown[model] = (now_epoch + seconds, why)


def cooldowns() -> Dict[str, Dict[str, Any]]:
    now = time.time()
    with _cooldown_lock:
        return {m: {"until_utc": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "why": w}
                for m, (t, w) in _cooldown.items() if t > now}


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
        "You adapt an English YouTube Short for Indian viewers in natural, conversational HINGLISH: the way an Indian "
        "YouTube creator actually talks, mixing Hindi and English. Keep familiar English words, technical terms, "
        "names and phrases in English; use simple everyday Hindi where it sounds natural. Avoid formal, Sanskritized "
        "or textbook Hindi (never words like mahatvapurn, nirantar, sanchalit, atyant). Do NOT translate sentence by "
        "sentence; sound like a person explaining, not a translation engine.",
        "Example. Avoid: 'Manav mastishk mein ek mahatvapurn prakriya nirantar sanchalit hoti rehti hai.' "
        "Prefer: 'Aapke brain mein ek fascinating process continuously chalta rehta hai.' Another good example: "
        "'Scientists ne ek surprising discovery ki, jo humare brain ko samajhne ka perspective change kar sakti hai.'",
        "Keep the meaning, every fact, every number and every name exactly. Do not add facts, numbers, dates or claims "
        "that are not in the English. Do not drop any fact. Keep the hook, curiosity gaps, transitions, humour and the "
        "payoff; adapt expressions naturally for Indian viewers without changing facts.",
        "narration: the scene's Hinglish narration in ROMAN script only (no Devanagari). These are the subtitles. "
        "Write numbers as digits exactly as in the English (e.g. 1969, 28).",
        "narration_tts: the SAME words as narration, for a Hindi text-to-speech voice: write the Hindi words in "
        "Devanagari and keep the English words, names and terms in Latin letters, numbers as digits. Same sentence, "
        "same meaning, same order; only the script of the Hindi words changes. Example: narration 'Aapke brain mein "
        "ek fascinating process chalta rehta hai.' -> narration_tts 'आपके brain में एक fascinating process चलता रहता है।'",
        "Aim for about target_words words per scene and NEVER exceed max_words for a scene: the voice must fit each "
        "scene's seconds. Keep Shorts pacing: when a scene is dense, say it more simply and briefly; keep every fact.",
        "On-screen text is English or Roman-script Hinglish ONLY (never Devanagari). English is the default for short "
        "labels. headline: at most 5 words, the same idea as the English headline (keep the English headline when it "
        "already works; Roman Hinglish such as 'Brain ka Secret' or 'Aapko pata tha?' when it is better). highlight: "
        "copy EXACTLY one word or phrase that appears inside headline (or \"\" if the English highlight is empty). "
        "labels: 1-2 words each, same count as the English, English or Roman Hinglish. units: English.",
        "title: the Short's title in English or Roman Hinglish. category: the English category, unchanged.",
        "terms: names or technical terms a Hindi voice might mispronounce, as {\"en\": the form used in the "
        "narration, \"spoken\": a Devanagari spelling the Hindi voice pronounces correctly}. Use [] if none.",
        "Return ONLY JSON: {\"title\": str, \"category\": str, \"scenes\": [{\"scene_id\": str, \"narration\": str, "
        "\"narration_tts\": str, \"headline\": str, \"highlight\": str, \"labels\": [str], \"units\": [str]}], "
        "\"terms\": [{\"en\": str, \"spoken\": str}]}. One entry per scene, same scene_id order.",
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
    model ever answered, ProviderUnavailable.

    2026-11-28 fix (production localization #5): a provider error on the FEEDBACK attempt is not a second failed
    draft. The model is cooled down, the next model continues with the first draft's problems as feedback, and when
    none can answer it is ProviderUnavailable (a wait), never a creative rejection after only one draft."""
    from google.genai import types
    attempts: List[Dict[str, Any]] = []
    rule_problems: List[str] = []                        # set only when a model answered BOTH attempts and broke the rules
    carried: List[str] = []                              # a one-draft model's problems, fed to the next model
    waits: List[Tuple[float, bool]] = []                 # (epoch usable again, quota?) per unavailable model
    for model in models:
        cd = _cooling(model, time.time())
        if cd:                                           # exhausted or overloaded: no request at all
            attempts.append({"model": model, "attempt": 0, "ok": False, "skipped": "cooldown", "why": cd[1],
                             "until_utc": datetime.fromtimestamp(cd[0], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            waits.append((cd[0], "quota" in cd[1] or "rate limit" in cd[1]))
            continue
        feedback: List[str] = list(carried) + list(initial_feedback)
        for attempt in (1, 2):
            t0 = time.perf_counter()
            cfg = (types.GenerateContentConfig(temperature=0.6, response_mime_type="application/json")
                   if not model.startswith("gemma") else types.GenerateContentConfig(temperature=0.6))
            try:
                resp = client.models.generate_content(model=model, contents=build_prompt(req, feedback), config=cfg)
            except Exception as exc:             # provider-level: cool this model down, try the next one
                status = _status_of(exc)
                secs, why = cooldown_for(exc, status)
                _cool(model, secs, why, time.time())
                waits.append((time.time() + secs, status == 429 or "quota" in why))
                attempts.append({"model": model, "attempt": attempt, "ok": False, "http_status": status,
                                 "error_type": type(exc).__name__, "cooldown_sec": round(secs), "why": why,
                                 "latency_ms": round((time.perf_counter() - t0) * 1000)})
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
            if attempt == 2:
                rule_problems = problems                 # two answered drafts, both broke the rules
            else:
                carried = list(problems)
            feedback = list(problems) + list(initial_feedback)
        if rule_problems:
            break                                # the model answered but broke the rules: another model is not the fix
    if rule_problems:
        raise TranslationRejected("the Hindi adaptation broke the localization rules after a feedback retry", rule_problems)
    retry_at = datetime.fromtimestamp(min(w[0] for w in waits), timezone.utc) if waits else None
    quota = any(q for _, q in waits)
    raise ProviderUnavailable("every Hindi model was unavailable (" + ("free-tier quota / " if quota else "")
                              + "rate limit / overload); retry "
                              + (f"after {retry_at.strftime('%Y-%m-%d %H:%M UTC')}" if retry_at else "later"),
                              retry_at=retry_at, quota=quota)
