"""Free LLM provider selection, quota tracking and the no-paid-model guarantee (2026-11-28). Fakes only; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import backend.app.content.grounded_script_writer as gsw
from backend.app.core.execution_context import ExecutionContext
from backend.app.services import llm_providers as lp

sys.path.insert(0, str(Path(__file__).parent))
from test_gemini_writer_resilience import ScriptedClient, ServerError  # noqa: E402
from test_grounded_script_writer import GOOD, TOPIC  # noqa: E402


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(tmp_path):
    c = Clock()
    lp.set_tracker(lp.QuotaTracker(tmp_path / "q.json", clock=c))
    return c


def _write(client, monkeypatch, model="primary"):
    monkeypatch.setattr(gsw.time, "sleep", lambda s: None)
    monkeypatch.setattr(gsw, "_deferred_sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup")
    return gsw.write_grounded_script(TOPIC, "", job_id="j1", execution_context=ExecutionContext.VALIDATION,
                                     client=client, model=model)


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.sent = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "json": json, "auth": headers.get("Authorization"), "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _ok(text, headers=None):
    return FakeResp(200, {"model": "openai/gpt-oss-120b", "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 900, "completion_tokens": 400, "total_tokens": 1300}}, headers)


# -- selection -------------------------------------------------------------------------------------------------------
def test_gemini_is_primary_and_alternates_need_explicit_config_and_key(monkeypatch, clock):
    assert [c["provider"] for c in lp.candidates(["a", "b"])] == ["gemini", "gemini"]
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(lp, "key_set", lambda p: p == "gemini")
    c = lp.candidates(["a"])
    assert c[0]["provider"] == "gemini" and c[1] == {"provider": "groq", "model": "openai/gpt-oss-120b",
                                                      "usable": False, "why": "GROQ_API_KEY not set"}
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    assert lp.candidates(["a"])[1]["usable"] is True


def test_primary_override_requires_explicit_configuration(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    assert lp.candidates(["g"])[0]["provider"] == "gemini"
    monkeypatch.setenv("CRAZZYDOCS_LLM_PRIMARY", "groq")
    assert [c["provider"] for c in lp.candidates(["g"])] == ["groq", "gemini"]


def test_no_paid_model_is_ever_selected(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:llama-3.3-70b-versatile,groq:some-paid-model,xai:grok-4.7")
    c = lp.candidates([])
    assert [x["usable"] for x in c] == [False, False] and all("free-model list" in x["why"] for x in c)
    with pytest.raises(lp.NotFreeError):
        lp.generate_openai_compat("groq", "some-paid-model", "x", session=FakeSession([]))


def test_paid_plan_limits_in_headers_block_the_provider(monkeypatch, clock):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    lp.tracker().record_success("groq", "openai/gpt-oss-120b", 100, {"x-ratelimit-limit-requests": "500000"})
    assert lp.candidates([])[0]["usable"] is False
    assert "free plan" in lp.candidates([])[0]["why"]


# -- quota tracking ------------------------------------------------------------------------------------------------
def test_429_cools_down_until_retry_after_then_resets(clock):
    t = lp.tracker()
    t.record_failure("groq", "openai/gpt-oss-120b", status=429, kind="quota", retry_after=120)
    ok, why = t.available("groq", "openai/gpt-oss-120b")
    assert not ok and "cooling down" in why
    clock.t += 121
    assert t.available("groq", "openai/gpt-oss-120b") == (True, None)


def test_429_uses_reset_header_when_no_retry_after(clock):
    t = lp.tracker()
    t.record_failure("groq", "openai/gpt-oss-120b", status=429, kind="quota",
                     headers={"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m59.56s"})
    clock.t += 170
    assert t.available("groq", "openai/gpt-oss-120b")[0] is False
    clock.t += 15
    assert t.available("groq", "openai/gpt-oss-120b")[0] is True


def test_quota_failures_are_not_counted_as_requests_and_limits_apply(clock):
    t = lp.tracker()
    t.record_failure("groq", "openai/gpt-oss-20b", status=429, kind="quota", retry_after=30)
    v = t.view()["models"]["groq:openai/gpt-oss-20b"]
    assert v["requests_today"] == 0 and v["quota_failures_today"] == 1
    clock.t += 31
    for _ in range(30):
        t.record_success("groq", "openai/gpt-oss-20b", 10)
    assert t.available("groq", "openai/gpt-oss-20b") == (False, "per-minute request limit reached")
    clock.t += 61
    assert t.available("groq", "openai/gpt-oss-20b")[0] is True
    assert t.available("groq", "openai/gpt-oss-20b", est_tokens=9000) == (False, "per-minute token limit would be exceeded")


def test_overload_backoff_is_bounded_and_growing(clock):
    t = lp.tracker()
    got = [t.record_failure("groq", "openai/gpt-oss-120b", status=503, kind="overload") for _ in range(5)]
    assert got == [60, 180, 600, 600, 600]


def test_duration_parsing():
    assert lp.parse_duration("2m59.56s") == pytest.approx(179.56)
    assert lp.parse_duration("7.66s") == pytest.approx(7.66)
    assert lp.parse_duration("12") == 12.0 and lp.parse_duration("120ms") == pytest.approx(0.12)
    assert lp.parse_duration(None) is None


# -- OpenAI-compatible call ------------------------------------------------------------------------------------------
def test_groq_call_uses_json_mode_key_header_only_and_returns_usage(monkeypatch, clock):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    s = FakeSession([_ok('{"sections": []}', {"x-ratelimit-remaining-requests": "999"})])
    text, tel, headers = lp.generate_openai_compat("groq", "openai/gpt-oss-120b", "write JSON", session=s)
    sent = s.sent[0]
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["json"]["response_format"] == {"type": "json_object"} and sent["json"]["model"] == "openai/gpt-oss-120b"
    assert "test-not-a-key" not in json.dumps(sent["json"]) and sent["auth"] == "Bearer test-not-a-key"
    assert sent["timeout"] == 60 and tel["total_tokens"] == 1300 and headers == {"x-ratelimit-remaining-requests": "999"}


def test_groq_429_carries_retry_after(monkeypatch, clock):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    s = FakeSession([FakeResp(429, {}, {"retry-after": "42"})])
    with pytest.raises(lp.ProviderError) as e:
        lp.generate_openai_compat("groq", "openai/gpt-oss-120b", "x", session=s)
    assert e.value.status_code == 429 and e.value.retry_after == 42 and e.value.transient


# -- the writer --------------------------------------------------------------------------------------------------
def test_gemini_429_is_not_retried_and_the_model_is_skipped_next_time(monkeypatch, clock):
    c = ScriptedClient({"primary": [ServerError(429, "RESOURCE_EXHAUSTED")], "backup": [{"sections": GOOD}, {"sections": GOOD}]})
    out = _write(c, monkeypatch)
    assert out["script_source"] == "llm:backup" and c.calls == ["primary", "backup"]
    out2 = _write(c, monkeypatch)                 # the next production: primary is cooling down, no request
    assert out2["script_source"] == "llm:backup" and c.calls == ["primary", "backup", "backup"]
    assert any(a.get("skipped", "").startswith("cooling down") for a in out2["writer_attempts"])


def test_groq_alternate_writes_when_gemini_is_exhausted(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    seen = []

    def fake(provider, model, prompt, **kw):
        seen.append((provider, model))
        return json.dumps({"sections": GOOD}), {"latency_ms": 900, "total_tokens": 1300, "response_status": "ok"}, {}
    monkeypatch.setattr(lp, "generate_openai_compat", fake)
    quota = ServerError(429, "RESOURCE_EXHAUSTED")
    c = ScriptedClient({"primary": [quota], "backup": [quota]})
    out = _write(c, monkeypatch)
    assert out["script_source"] == "llm:groq:openai/gpt-oss-120b" and out["provider"] == "groq"
    assert seen == [("groq", "openai/gpt-oss-120b")] and len(out["sections"]) == 5     # same output contract
    assert lp.tracker().view()["models"]["groq:openai/gpt-oss-120b"]["requests_today"] == 1


def test_extractive_fallback_when_every_provider_is_unavailable(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    for m in ("primary", "backup"):
        lp.tracker().record_failure("gemini", m, status=429, kind="quota", retry_after=3600)
    lp.tracker().record_failure("groq", "openai/gpt-oss-120b", status=429, kind="quota", retry_after=3600)
    c = ScriptedClient({"primary": [], "backup": []})
    out = _write(c, monkeypatch)
    assert out["script_source"] == "extractive_fallback" and c.calls == []      # no request, no waiting
    assert sum(1 for a in out["writer_attempts"] if a.get("skipped")) == 3


def test_malformed_alternate_json_falls_back_without_crashing(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-120b")
    monkeypatch.setattr(lp, "generate_openai_compat", lambda *a, **k: ("not json", {"total_tokens": 5}, {}))
    out = _write(None, monkeypatch, model=None)
    assert out["script_source"] == "extractive_fallback"
    assert [a["model"] for a in out["writer_attempts"] if "attempt" in a] == ["groq:openai/gpt-oss-120b"] * 2


def test_status_view_never_contains_key_values(monkeypatch, clock):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value_xyz")
    v = lp.status_view()
    assert "gsk_secret_value_xyz" not in json.dumps(v) and v["providers"]["groq"]["key_set"] is True
