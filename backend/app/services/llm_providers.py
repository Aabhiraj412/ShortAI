"""
Free LLM provider registry, quota tracking and quota-aware selection (2026-11-28).

The script writer (``content.grounded_script_writer``) was built on Gemini only; the free tier's per-model daily
request caps (20 RPD on the Flash models) were exceeded on 2026-11-27, and every later production fell back to the
extractive script. This module extends that writer's model chain with verified FREE alternatives instead of adding a
parallel generation system:

* ``PROVIDERS``: each provider, its credential environment variable (never a key in code), API kind, timeout and the
  allow-list of models that are free on that provider's free plan with their documented limits. A model that is not
  in the allow-list is refused (``NotFreeError``): there is no paid fallback, silent or otherwise.
* ``QuotaTracker``: per provider:model RPM / TPM / RPD counts, the provider's own rate-limit headers, quota exhaustion
  and cooldowns, persisted under ``logs/llm_providers`` (runtime state, not configuration) so every process sees it.
  An HTTP 429 cools the model down until the provider's reset time (``retry-after`` / ``x-ratelimit-reset-*`` /
  Gemini's per-day quota -> next Pacific midnight) instead of retrying; an overload backs off with a bounded,
  growing cooldown.
* Selection: ``candidates()`` = the configured primary (Gemini unless ``CRAZZYDOCS_LLM_PRIMARY`` explicitly says
  otherwise) followed by the explicitly configured alternates (``CRAZZYDOCS_LLM_ALTERNATES``, e.g.
  ``groq:openai/gpt-oss-120b``). Nothing is enabled automatically: an alternate is used only when it is configured,
  its key is set and it is in the free allow-list. Models in cooldown are skipped without a request, so the chain is
  walked once and the writer falls back to the extractive script; nothing waits indefinitely.
* Free-plan guard (Groq): Groq's Developer (paid) plan raises the limits. When a response's
  ``x-ratelimit-limit-requests`` exceeds the documented free RPD, the provider is marked ``NOT_FREE_PLAN`` and is not
  used again until an operator clears it, so a key from a paid organisation can never be used by accident.

Limits verified from the providers' own documentation on 2026-11-28 (see docs/AI_PROVIDERS.md).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VERIFIED_ON = "2026-11-28"
OVERLOAD_BACKOFF_SEC = (60, 180, 600)          # bounded, growing cooldown after consecutive overloads / timeouts
DEFAULT_429_COOLDOWN_SEC = 900
MIN_COOLDOWN_SEC = 30

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini (free tier)", "kind": "gemini", "env": "GEMINI_API_KEY", "timeout_sec": 90,
        # The existing key and models; the free tier's limits are per model (20 RPD observed on the Flash models).
        "models": {"gemini-3.8-flash": {"rpd": 20}, "gemini-3.6-flash": {"rpd": 20}, "gemini-3.7-flash": {"rpd": 20},
                   "gemini-3.5-flash": {"rpd": 20}, "gemma-4-31b-it": {"rpd": 14400}, "gemma-4-26b-a4b-it": {"rpd": 14400}},
        "allow_unlisted": True,      # existing behaviour: GEMINI_MODEL / GEMINI_FALLBACK_MODELS may name other models
        "reset": "pacific_midnight",
        "source": "existing integration; RPD as shown in Google AI Studio usage",
    },
    "groq": {
        "label": "Groq (free plan)", "kind": "openai_compat", "env": "GROQ_API_KEY", "timeout_sec": 60,
        "base_url": "https://api.groq.com/openai/v1",
        # https://console.groq.com/docs/rate-limits (free plan table) and /docs/models (both "Production").
        "models": {
            "openai/gpt-oss-120b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000, "json": "json_object",
                                    "json_schema_strict": True, "max_output_tokens": 5000},
            "openai/gpt-oss-20b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000, "json": "json_object",
                                   "json_schema_strict": True, "max_output_tokens": 5000},
        },
        "allow_unlisted": False,
        "reset": "headers",
        "free_plan_guard": True,
        "source": "https://console.groq.com/docs/rate-limits",
    },
}

# Evaluated 2026-11-28 but NOT integrated (reasons in docs/AI_PROVIDERS.md): Cloudflare Workers AI (10,000 free
# Neurons/day, blocked not billed on Workers Free; needs an account id + token and an integration test), OpenRouter
# (":free" models, 50 requests/day without purchased credits), Mistral (free mode has no API access), SambaNova (free
# tier could not be verified), xAI (no zero-cost API allowance stated).


class InvalidOutput(ValueError):
    """The provider rejected its own answer as invalid JSON (e.g. Groq json_validate_failed): a bad draft, retried
    with feedback like malformed JSON from any model, not an API outage."""

    status_code = code = 400
    transient = False


class NotFreeError(RuntimeError):
    """The model is not on the provider's verified free allow-list (or the key is on a paid plan)."""


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None, retry_after: Optional[float] = None,
                 transient: bool = False):
        super().__init__(message)
        self.status_code = status
        self.code = status
        self.retry_after = retry_after
        self.transient = transient


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_pacific_midnight(now: datetime) -> datetime:
    from backend.app.localization.translator import _next_pacific_midnight as npm
    return npm(now)


def parse_duration(text: Optional[str]) -> Optional[float]:
    """Groq style durations: '2m59.56s', '7.66s', '1h2m', '120ms', or plain seconds."""
    if text is None:
        return None
    s = str(text).strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", s):
        found = True
        total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total if found else None


# ---- configuration ------------------------------------------------------------------------------------------------
def key_set(provider: str) -> bool:
    env = (PROVIDERS.get(provider) or {}).get("env")
    if not env:
        return False
    if os.environ.get(env):
        return True
    try:
        from backend.app.core.config import settings
        return bool(getattr(settings, env, "") or "")
    except Exception:
        return False


def _key(provider: str) -> str:
    env = PROVIDERS[provider]["env"]
    val = os.environ.get(env, "")
    if not val:
        try:
            from backend.app.core.config import settings
            val = getattr(settings, env, "") or ""
        except Exception:
            val = ""
    return val


def check_free(provider: str, model: str) -> Dict[str, Any]:
    """The model's free-tier limits, or raises NotFreeError. The single no-paid-model gate."""
    spec = PROVIDERS.get(provider)
    if not spec:
        raise NotFreeError(f"unknown provider {provider}")
    limits = spec["models"].get(model)
    if limits is None and not spec.get("allow_unlisted"):
        raise NotFreeError(f"{provider}:{model} is not on the verified free-model list")
    return limits or {}


def parse_target(text: str) -> Optional[Tuple[str, str]]:
    text = (text or "").strip()
    if ":" not in text:
        return None
    provider, model = text.split(":", 1)
    provider, model = provider.strip().lower(), model.strip()
    return (provider, model) if provider in PROVIDERS and model else None


def primary_provider() -> str:
    p = (os.environ.get("CRAZZYDOCS_LLM_PRIMARY") or "gemini").strip().lower()
    return p if p in PROVIDERS else "gemini"


def configured_alternates() -> List[Tuple[str, str]]:
    raw = os.environ.get("CRAZZYDOCS_LLM_ALTERNATES") or ""
    out: List[Tuple[str, str]] = []
    for part in raw.split(","):
        t = parse_target(part)
        if t and t not in out:
            out.append(t)
    return out


def candidates(gemini_models: List[str]) -> List[Dict[str, Any]]:
    """Ordered (provider, model) targets with why each is or is not usable. Gemini first unless explicitly
    configured otherwise; alternates only when configured, keyed and free."""
    gem = [("gemini", m) for m in gemini_models]
    alts = configured_alternates()
    order = gem + alts
    if primary_provider() != "gemini":
        first = [t for t in alts if t[0] == primary_provider()]
        order = first + gem + [t for t in alts if t not in first]
    out = []
    for provider, model in order:
        why = None
        try:
            check_free(provider, model)
        except NotFreeError as exc:
            why = str(exc)
        if why is None and provider != "gemini" and not key_set(provider):
            why = f"{PROVIDERS[provider]['env']} not set"
        if why is None and tracker().plan_blocked(provider):
            why = "key is not on the provider's free plan (paid limits seen); not used"
        out.append({"provider": provider, "model": model, "usable": why is None, "why": why})
    return out


# ---- quota tracking -----------------------------------------------------------------------------------------------
class QuotaTracker:
    """Per provider:model usage, rate-limit headers, quota exhaustion and cooldowns (persisted JSON, best effort)."""

    def __init__(self, path: Optional[Path] = None, clock=time.time):
        if path is None:
            from backend.app.core.paths import PROJECT_ROOT
            path = Path(PROJECT_ROOT) / "logs" / "llm_providers" / "quota_state.json"
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"models": {}, "providers": {}}

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logger.debug("LLM quota state not persisted")

    def _entry(self, data: Dict[str, Any], provider: str, model: str) -> Dict[str, Any]:
        e = data.setdefault("models", {}).setdefault(f"{provider}:{model}", {})
        day = self._day(provider)
        if e.get("day") != day:
            e.update(day=day, requests_today=0, tokens_today=0, quota_failures_today=0)
        e.setdefault("recent", [])
        now = self.clock()
        e["recent"] = [r for r in e["recent"] if now - r[0] < 60]
        return e

    def _day(self, provider: str) -> str:
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        if PROVIDERS.get(provider, {}).get("reset") == "pacific_midnight":
            return (_next_pacific_midnight(now) - timedelta(days=1)).strftime("%Y-%m-%d")
        return now.strftime("%Y-%m-%d")

    # -- checks
    def available(self, provider: str, model: str, est_tokens: int = 0, *,
                  ignore_overload: bool = False) -> Tuple[bool, Optional[str]]:
        """ignore_overload: the writer's single deferred round may retry an overloaded model (never a quota one)."""
        limits = (PROVIDERS.get(provider, {}).get("models") or {}).get(model) or {}
        now = self.clock()
        with self._lock:
            data = self._load()
            if self.plan_blocked(provider, data):
                return False, "provider key is on a paid plan; not used"
            e = self._entry(data, provider, model)
        cd = e.get("cooldown_until") or 0
        if cd > now and not (ignore_overload and e.get("cooldown_kind") == "overload"):
            return False, f"cooling down until {_utc(cd)} ({e.get('cooldown_why')})"
        if limits.get("rpd") and e["requests_today"] >= limits["rpd"]:
            return False, f"daily request limit reached ({e['requests_today']}/{limits['rpd']})"
        if limits.get("tpd") and e["tokens_today"] + est_tokens > limits["tpd"]:
            return False, "daily token limit reached"
        if limits.get("rpm") and len(e["recent"]) >= limits["rpm"]:
            return False, "per-minute request limit reached"
        if limits.get("tpm") and sum(r[1] for r in e["recent"]) + est_tokens > limits["tpm"]:
            return False, "per-minute token limit would be exceeded"
        hdr = e.get("headers") or {}
        if hdr.get("remaining_requests") == 0 and (hdr.get("requests_reset_at") or 0) > now:
            return False, f"provider reports 0 requests remaining until {_utc(hdr['requests_reset_at'])}"
        return True, None

    def plan_blocked(self, provider: str, data: Optional[Dict[str, Any]] = None) -> bool:
        data = data if data is not None else self._load()
        return bool(((data.get("providers") or {}).get(provider) or {}).get("not_free_plan"))

    # -- records
    def record_success(self, provider: str, model: str, tokens: Optional[int], headers: Optional[Dict[str, str]] = None,
                       latency_ms: Optional[int] = None) -> None:
        now = self.clock()
        with self._lock:
            data = self._load()
            e = self._entry(data, provider, model)
            e["requests_today"] += 1
            e["tokens_today"] += int(tokens or 0)
            e["recent"].append([now, int(tokens or 0)])
            e.update(last_success_utc=_utc(now), last_latency_ms=latency_ms, overloads=0, cooldown_until=None,
                     cooldown_why=None, cooldown_kind=None)
            if headers:
                self._apply_headers(data, e, provider, model, headers, now)
            self._save(data)

    def record_failure(self, provider: str, model: str, *, status: Optional[int], kind: str,
                       retry_after: Optional[float] = None, why: Optional[str] = None,
                       headers: Optional[Dict[str, str]] = None) -> float:
        """kind: quota (429) | overload (5xx / timeout) | error. Returns the cooldown applied (seconds)."""
        now = self.clock()
        with self._lock:
            data = self._load()
            e = self._entry(data, provider, model)
            if headers:
                self._apply_headers(data, e, provider, model, headers, now)
            cool = 0.0
            if kind == "quota":
                e["quota_failures_today"] = e.get("quota_failures_today", 0) + 1
                hdr = e.get("headers") or {}
                cool = retry_after if retry_after is not None else None
                if cool is None and hdr.get("remaining_requests") == 0 and hdr.get("requests_reset_at"):
                    cool = hdr["requests_reset_at"] - now
                cool = max(MIN_COOLDOWN_SEC, cool if cool is not None else DEFAULT_429_COOLDOWN_SEC)
            elif kind == "overload":
                n = int(e.get("overloads") or 0)
                cool = float(OVERLOAD_BACKOFF_SEC[min(n, len(OVERLOAD_BACKOFF_SEC) - 1)])
                e["overloads"] = n + 1
            if cool:
                e.update(cooldown_until=now + cool, cooldown_why=(why or kind)[:160], cooldown_kind=kind)
            e.update(last_failure_utc=_utc(now), last_failure={"status": status, "kind": kind,
                                                                "why": (why or "")[:160]})
            self._save(data)
            return cool

    def _apply_headers(self, data, e, provider, model, headers, now) -> None:
        h = {k.lower(): v for k, v in headers.items()}

        def num(k):
            try:
                return int(float(h[k]))
            except (KeyError, TypeError, ValueError):
                return None
        rec = {"limit_requests": num("x-ratelimit-limit-requests"), "remaining_requests": num("x-ratelimit-remaining-requests"),
               "limit_tokens": num("x-ratelimit-limit-tokens"), "remaining_tokens": num("x-ratelimit-remaining-tokens")}
        rr, rt = parse_duration(h.get("x-ratelimit-reset-requests")), parse_duration(h.get("x-ratelimit-reset-tokens"))
        rec["requests_reset_at"] = now + rr if rr is not None else None
        rec["tokens_reset_at"] = now + rt if rt is not None else None
        if any(v is not None for v in rec.values()):
            e["headers"] = dict(rec, seen_utc=_utc(now))
        spec = PROVIDERS.get(provider) or {}
        free_rpd = ((spec.get("models") or {}).get(model) or {}).get("rpd")
        if spec.get("free_plan_guard") and free_rpd and rec["limit_requests"] and rec["limit_requests"] > free_rpd:
            data.setdefault("providers", {}).setdefault(provider, {}).update(
                not_free_plan=True, detected_utc=_utc(now),
                why=f"x-ratelimit-limit-requests {rec['limit_requests']} > free plan {free_rpd} for {model}")
            logger.warning("LLM provider %s: key is not on the free plan; it will not be used", provider)

    def clear_plan_block(self, provider: str) -> None:
        with self._lock:
            data = self._load()
            (data.get("providers") or {}).pop(provider, None)
            self._save(data)

    def view(self) -> Dict[str, Any]:
        now = self.clock()
        with self._lock:
            data = self._load()
        out = {}
        for k, e in (data.get("models") or {}).items():
            provider, model = k.split(":", 1)
            limits = (PROVIDERS.get(provider, {}).get("models") or {}).get(model) or {}
            fresh = e.get("day") == self._day(provider)
            cd = e.get("cooldown_until") or 0
            out[k] = {"provider": provider, "model": model, "limits": limits,
                      "requests_today": e.get("requests_today", 0) if fresh else 0,
                      "tokens_today": e.get("tokens_today", 0) if fresh else 0,
                      "quota_failures_today": e.get("quota_failures_today", 0) if fresh else 0,
                      "requests_last_minute": len([r for r in e.get("recent", []) if now - r[0] < 60]),
                      "cooldown_until_utc": _utc(cd) if cd > now else None,
                      "cooldown_why": e.get("cooldown_why") if cd > now else None,
                      "last_success_utc": e.get("last_success_utc"), "last_failure_utc": e.get("last_failure_utc"),
                      "last_failure": e.get("last_failure"), "headers": e.get("headers")}
        return {"models": out, "providers": data.get("providers") or {}}


_tracker: Optional[QuotaTracker] = None


def tracker() -> QuotaTracker:
    global _tracker
    if _tracker is None:
        _tracker = QuotaTracker()
    return _tracker


def set_tracker(t: Optional[QuotaTracker]) -> None:
    """Tests inject an isolated tracker."""
    global _tracker
    _tracker = t


# ---- OpenAI-compatible call (Groq) --------------------------------------------------------------------------------
def generate_openai_compat(provider: str, model: str, prompt: str, *, temperature: float = 0.85,
                           session=None, schema: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
    """One chat completion in JSON mode. Returns (text, usage telemetry, response headers). Raises ProviderError
    (status, retry_after, transient) or NotFreeError. The API key is sent only in the Authorization header."""
    limits = check_free(provider, model)
    spec = PROVIDERS[provider]
    key = _key(provider)
    if not key:
        raise ProviderError(f"{spec['env']} not set", status=None)
    import requests
    sess = session or requests
    body: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}],
                            "temperature": temperature}
    if limits.get("max_output_tokens"):
        body["max_completion_tokens"] = limits["max_output_tokens"]
    if schema is not None and limits.get("json_schema_strict"):
        # Constrained decoding (documented for gpt-oss on Groq): the answer is always schema-valid JSON.
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "crazzydocs_output", "strict": True, "schema": schema}}
    elif limits.get("json") == "json_object":
        body["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    try:
        resp = sess.post(spec["base_url"].rstrip("/") + "/chat/completions", json=body,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         timeout=spec.get("timeout_sec", 60))
    except Exception as exc:                       # network error / timeout: never echo the request
        raise ProviderError(f"{provider} request failed ({type(exc).__name__})", transient=True) from None
    latency = round((time.perf_counter() - t0) * 1000)
    headers = {k: v for k, v in (getattr(resp, "headers", None) or {}).items() if k.lower().startswith(("x-ratelimit", "retry-after"))}
    status = getattr(resp, "status_code", None)
    if status != 200:
        ra = parse_duration(headers.get("retry-after") or headers.get("Retry-After"))
        code = None
        try:                                       # the provider's error code only (e.g. json_validate_failed)
            code = str(((resp.json() or {}).get("error") or {}).get("code") or "")[:60] or None
        except Exception:
            pass
        err = ProviderError(f"{provider} HTTP {status}" + (f" ({code})" if code else ""), status=status, retry_after=ra,
                            transient=status in (408, 429, 500, 502, 503, 504))
        err.error_code = code                      # type: ignore[attr-defined]
        if status == 400 and code == "json_validate_failed":
            raise InvalidOutput(f"{provider} answer was not valid JSON ({code})")
        err.headers = headers                      # type: ignore[attr-defined]
        err.latency_ms = latency                   # type: ignore[attr-defined]
        raise err
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content")) or ""
    usage = data.get("usage") or {}
    tel = {"latency_ms": latency, "response_status": "ok", "input_tokens": usage.get("prompt_tokens"),
           "output_tokens": usage.get("completion_tokens"), "thinking_tokens": None,
           "total_tokens": usage.get("total_tokens"), "model_version": data.get("model"),
           "finish_reason": choice.get("finish_reason"), "provider": provider}
    return text, tel, headers


def status_view() -> Dict[str, Any]:
    """Dashboard view: providers, their free limits, whether configured (never key values) and live quota state."""
    from backend.app.content.grounded_script_writer import model_chain
    try:
        from backend.app.core.config import settings
        primary_model = os.environ.get("GEMINI_MODEL") or settings.DEFAULT_GEMINI_MODEL
    except Exception:
        primary_model = os.environ.get("GEMINI_MODEL") or ""
    cands = candidates(model_chain(primary_model))
    t = tracker()
    for c in cands:
        ok, why = t.available(c["provider"], c["model"])
        c["available_now"] = bool(c["usable"] and ok)
        c["quota_note"] = why
    return {"verified_on": VERIFIED_ON, "primary_provider": primary_provider(),
            "alternates_configured": [f"{p}:{m}" for p, m in configured_alternates()],
            "providers": {k: {"label": v["label"], "env": v["env"], "key_set": key_set(k), "source": v.get("source"),
                              "free_models": v["models"]} for k, v in PROVIDERS.items()},
            "selection_order": cands, "quota": t.view(),
            "fallback": "extractive script (labelled) when no provider is usable; never a paid model"}
