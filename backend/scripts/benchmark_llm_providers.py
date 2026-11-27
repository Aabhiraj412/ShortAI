"""
Benchmark free LLM providers for CrazzyDocs script writing (2026-11-28). Development tool; never run by production.

Reads research dossiers of existing productions (read-only), asks each target to write the 5-stage script with the
production prompt (``grounded_script_writer.build_prompt``) and records, per target and topic:
JSON compliance, the production validators' verdict (stages, length, invented numbers, copied runs), the structured
script-quality scores (hook, storytelling, narration...), latency, tokens and the provider's rate-limit headers.
Optionally (--hindi) it translates one accepted script with the Hindi localization prompt.

Results go to ``logs/llm_benchmark/<timestamp>.json`` only: separate from production configuration, which this
script never changes. Quota use is recorded in the shared quota tracker like any other request.

Usage:
  python backend/scripts/benchmark_llm_providers.py --targets gemini:gemma-4-31b-it,groq:openai/gpt-oss-120b --topics 3
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def load_topics(n: int, productions: Path):
    out = []
    for d in sorted(productions.glob("cycle_*"), reverse=True):
        f = next(iter((d / "research").glob("research_*.json")), None) if (d / "research").is_dir() else None
        if not f:
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(t.title == r.get("title") for t in out):
            continue
        out.append(SimpleNamespace(**{k: r.get(k) for k in ("title", "category", "summary", "extended_extract",
                                                            "factual_bullets", "winning_hook")}, job_id=d.name))
        if len(out) >= n:
            break
    return out


def run_target(provider: str, model: str, topic, gemini_client):
    from backend.app.content import grounded_script_writer as gsw
    from backend.app.content.script_quality import evaluate_script
    hook_type = gsw.choose_hook_type(topic.job_id)
    prompt = gsw.build_prompt(topic, hook_type, topic.winning_hook or "")
    row = {"provider": provider, "model": model, "topic": topic.title, "job_id": topic.job_id}
    try:
        raw, info = gsw._call_with_backoff(gemini_client, model, prompt, provider=provider, sleep=lambda s: None)
    except Exception as exc:
        row.update(ok=False, error=gsw.describe_error(exc), calls=list(getattr(exc, "calls", [])))
        return row
    call = info["calls"][-1]
    row.update(latency_ms=call.get("latency_ms"), tokens=call.get("total_tokens"), finish=call.get("finish_reason"))
    try:
        data = gsw._parse(raw)
        row["json_ok"] = True
    except Exception:
        row.update(json_ok=False, ok=False)
        return row
    sections = data.get("sections") or []
    problems = gsw.validate_sections(sections, gsw.fact_corpus(topic))
    row.update(ok=True, accepted=not problems, problems=problems)
    if not problems:
        script = gsw._assemble(sections, ["question", "mechanism", "evidence", "revelation"], topic.category or "",
                               script_source=f"llm:{provider}:{model}")
        q = evaluate_script(script, topic)
        row["quality"] = {"overall": q.get("overall"),
                          **{k: (v or {}).get("score") for k, v in (q.get("dimensions") or {}).items()}}
        row["script"] = script["full_script"]
    return row


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True, help="comma separated provider:model")
    ap.add_argument("--topics", type=int, default=3)
    ap.add_argument("--productions", default=str(ROOT / "media" / "productions"))
    ap.add_argument("--env-file", default=None, help="read credentials from this .env file (read only)")
    args = ap.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    from backend.app.content import grounded_script_writer as gsw
    from backend.app.services import llm_providers as lp
    targets = [lp.parse_target(t) for t in args.targets.split(",")]
    if any(t is None for t in targets):
        sys.exit("unknown provider in --targets")
    for p, m in targets:
        lp.check_free(p, m)                         # refuses anything not on a verified free list
    client = None
    if any(p == "gemini" for p, _ in targets):
        client, _ = gsw.default_client()
        if client is None:
            sys.exit("GEMINI_API_KEY not available (use --env-file)")
    topics = load_topics(args.topics, Path(args.productions))
    rows = []
    for topic in topics:
        for p, m in targets:
            ok, why = lp.tracker().available(p, m)
            if not ok:
                rows.append({"provider": p, "model": m, "topic": topic.title, "skipped": why})
                continue
            rows.append(run_target(p, m, topic, client))
            time.sleep(2)
    summary = {}
    for p, m in targets:
        rs = [r for r in rows if r["provider"] == p and r["model"] == m and "skipped" not in r]
        acc = [r for r in rs if r.get("accepted")]
        summary[f"{p}:{m}"] = {
            "runs": len(rs), "json_ok": sum(1 for r in rs if r.get("json_ok")), "accepted": len(acc),
            "median_latency_ms": statistics.median([r["latency_ms"] for r in rs if r.get("latency_ms")]) if any(r.get("latency_ms") for r in rs) else None,
            "mean_quality": round(statistics.mean([r["quality"]["overall"] for r in acc if (r.get("quality") or {}).get("overall") is not None]), 1)
            if any((r.get("quality") or {}).get("overall") is not None for r in acc) else None,
            "skipped": sum(1 for r in rows if r.get("provider") == p and r.get("model") == m and "skipped" in r)}
    out = {"at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "targets": args.targets,
           "topics": [t.title for t in topics], "summary": summary, "rows": rows,
           "quota": lp.tracker().view()}
    dest = ROOT / "logs" / "llm_benchmark"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"benchmark_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(path)


if __name__ == "__main__":
    main()
