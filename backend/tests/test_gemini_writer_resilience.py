"""Writer resilience: backoff only on transient errors, a recorded model fallback, honest telemetry."""
import sys
from pathlib import Path
from types import SimpleNamespace

import backend.app.content.grounded_script_writer as gsw
from backend.app.core.execution_context import ExecutionContext

sys.path.insert(0, str(Path(__file__).parent))
from test_grounded_script_writer import GOOD, TOPIC  # noqa: E402


class ServerError(Exception):
    def __init__(self, code, status, secret="sk-should-never-be-recorded"):
        super().__init__(f"{code} {status} request with key {secret}")
        self.code, self.status = code, status


class ScriptedClient:
    """Per-model scripted responses; records which model each call used."""
    def __init__(self, plan):
        self.plan = {m: list(v) for m, v in plan.items()}
        self.calls = []
        self.models = self

    def generate_content(self, *, model, contents, config=None):
        self.calls.append(model)
        item = self.plan[model].pop(0)
        if isinstance(item, Exception):
            raise item
        import json
        return SimpleNamespace(text=json.dumps(item), model_version=model, candidates=[],
                               usage_metadata=SimpleNamespace(prompt_token_count=900, candidates_token_count=280,
                                                              thoughts_token_count=None, total_token_count=1180))


def _write(client, monkeypatch, model="primary"):
    monkeypatch.setattr(gsw.time, "sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup")
    return gsw.write_grounded_script(TOPIC, "", job_id="j1", execution_context=ExecutionContext.VALIDATION,
                                     client=client, model=model)


def test_overloaded_primary_falls_back_to_recorded_model(monkeypatch):
    c = ScriptedClient({"primary": [ServerError(503, "UNAVAILABLE")] * 3, "backup": [{"sections": GOOD}]})
    out = _write(c, monkeypatch)
    assert c.calls == ["primary"] * 3 + ["backup"]
    assert out["script_source"] == "llm:backup" and out["configured_model"] == "primary"
    calls = [x for a in out["writer_attempts"] for x in a["calls"]]
    assert [x["http_status"] for x in calls[:3]] == [503] * 3 and all(x["transient"] for x in calls[:3])
    assert calls[-1]["input_tokens"] == 900 and calls[-1]["output_tokens"] == 280
    assert calls[-1]["thinking_tokens"] is None          # not reported -> not invented
    assert "sk-should-never-be-recorded" not in repr(out)


def test_non_transient_error_does_not_retry_or_switch_model(monkeypatch):
    c = ScriptedClient({"primary": [ServerError(401, "UNAUTHENTICATED")], "backup": [{"sections": GOOD}]})
    out = _write(c, monkeypatch)
    assert c.calls == ["primary"] and out["script_source"] == "extractive_fallback"


def test_model_chain_is_configurable_and_deduplicated(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "a, primary ,b")
    assert gsw.model_chain("primary") == ["primary", "a", "b"]
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    assert gsw.model_chain("primary") == ["primary"]
