"""
Isolated, real Gemini script validation (no mocks, no production process touched).

usage: python backend/scripts/validate_gemini_scripts.py <env_file> <topics.json> <out.json> [index ...]

Loads GEMINI_API_KEY from <env_file> into THIS process only, runs the production grounded script
writer on each chosen topic, and records per topic: angle, model, response status, latency, token
counts as reported by the API (None when not reported), retries, validation failures, the
script-generation source, the script itself and its quality evaluation.

The key is never printed, logged or written: the report is checked for it before it is saved.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_key(env_file: Path) -> bool:
    from dotenv import dotenv_values
    value = (dotenv_values(env_file).get("GEMINI_API_KEY") or "").strip()
    if value:
        os.environ["GEMINI_API_KEY"] = value
    return bool(value)


def topic_ns(raw: dict, job_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=raw.get("id", job_id), title=raw["title"], category=raw.get("category", "Science"),
                           summary=raw.get("summary", ""), extended_extract=raw.get("extended_extract", ""),
                           factual_bullets=raw.get("factual_bullets", []), key_entities=raw.get("key_entities", []),
                           sources=raw.get("sources", []), coordinates=None, composite_score=9.0, research_fingerprint="")


def main() -> int:
    env_file, topics_file, out_file = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    if not load_key(env_file):
        print("BLOCKED: GEMINI_API_KEY absent or empty in the env file")
        return 2
    from backend.app.content.grounded_script_writer import write_grounded_script
    from backend.app.core.execution_context import ExecutionContext

    topics = json.loads(topics_file.read_text(encoding="utf-8"))
    picks = [int(x) for x in sys.argv[4:]] or list(range(len(topics)))
    results = []
    for idx in picks:
        raw = topics[idx]
        job_id = f"gemini_validation_{idx + 1}"
        t0 = time.perf_counter()
        script = write_grounded_script(topic_ns(raw, job_id), raw.get("hook", ""), job_id=job_id,
                                       execution_context=ExecutionContext.VALIDATION)
        attempts = script.get("writer_attempts") or []
        calls = [c for a in attempts for c in a.get("calls", [])]
        ok_calls = [c for c in calls if c.get("response_status") == "ok"]
        tokens = lambda k: (sum(c[k] for c in ok_calls if c.get(k) is not None)
                            if any(c.get(k) is not None for c in ok_calls) else None)
        results.append({
            "topic": raw["title"], "category": raw.get("category"), "job_id": job_id,
            "script_source": script.get("script_source"), "model": script.get("model"),
            "hook_type": script.get("hook_type"), "angle": script.get("angle"),
            "api_calls": len(calls), "api_calls_ok": len(ok_calls),
            "retries": max(0, len(calls) - 1),
            "draft_attempts": len(attempts),
            "validation_failures": [p for a in attempts for p in a.get("problems", [])],
            "latency_ms": [c.get("latency_ms") for c in calls],
            "input_tokens": tokens("input_tokens"), "output_tokens": tokens("output_tokens"),
            "thinking_tokens": tokens("thinking_tokens"), "total_tokens": tokens("total_tokens"),
            "model_version": next((c.get("model_version") for c in ok_calls if c.get("model_version")), None),
            "errors": [{k: c.get(k) for k in ("error_type", "http_status", "status", "transient")}
                       for c in calls if c.get("response_status") == "error"],
            "word_count": script.get("word_count"),
            "quality": script.get("quality"),
            "sections": script.get("sections"),
            "full_script": script.get("full_script"),
            "writer_attempts": attempts,
            "wall_sec": round(time.perf_counter() - t0, 1),
        })
        r = results[-1]
        print(json.dumps({k: r[k] for k in ("topic", "script_source", "api_calls", "retries", "latency_ms",
                                            "input_tokens", "output_tokens", "validation_failures", "errors")})[:900])
    blob = json.dumps({"generated_at_epoch": time.time(), "results": results}, indent=2, default=str)
    if os.environ["GEMINI_API_KEY"] in blob:
        print("ABORT: report would contain the API key; not written")
        return 3
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(blob, encoding="utf-8")
    return 0 if all(str(r["script_source"]).startswith("llm:") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
