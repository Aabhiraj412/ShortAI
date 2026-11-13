"""
Grounded script writer: model drafts are accepted only when verifiable; every failure path
falls back to the extractive composer without failing the production. No network: the
Gemini client is a fake.
"""
import json
from types import SimpleNamespace

import pytest

from backend.app.content import grounded_script_writer as gsw
from backend.app.core.execution_context import ExecutionContext

EXTRACT = (
    "The Breakthrough Prize in Fundamental Physics is awarded each year to researchers. "
    "Each prize carries an award of 3 million US dollars, roughly three times a Nobel Prize. "
    "It was founded in 2012 by Yuri Milner. Laureates have included string theorists and "
    "cosmologists who mapped the early universe."
)

TOPIC = SimpleNamespace(
    id="t1", title="Breakthrough Prize in Fundamental Physics", category="Science",
    summary="An annual physics award founded in 2012.", extended_extract=EXTRACT,
    factual_bullets=["The prize is worth 3 million US dollars."], key_entities=["Yuri Milner"],
    sources=["https://en.wikipedia.org/wiki/Breakthrough_Prize"],
)

GOOD = [
    {"stage": "HOOK", "text": "One physics prize pays three times more than a Nobel. So why have most people never heard of it?"},
    {"stage": "CONTEXT", "text": "It is called the Breakthrough Prize. A tech investor named Yuri Milner started it in 2012, "
                                 "and every year it hands 3 million dollars to the people asking the deepest questions about reality."},
    {"stage": "ESCALATION", "text": "That money does not go to gadgets or quick wins. It goes to theorists, "
                                    "the ones working on strings, black holes and the very first moments after the big bang."},
    {"stage": "REVELATION", "text": "The idea is simple. Make thinking about the universe as celebrated as winning a final, "
                                    "and maybe more young people will choose it."},
    {"stage": "PAYOFF", "text": "So the biggest reward in physics is not about a discovery you can hold. It is about the questions."},
]


def _words(sections):
    return sum(len(s["text"].split()) for s in sections)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []
        self.models = self

    def generate_content(self, *, model, contents, config=None):
        self.prompts.append(contents)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item if isinstance(item, str) else json.dumps(item))


def _write(client, job_id="cycle_1_20261114_0000"):
    return gsw.write_grounded_script(TOPIC, "A hook.", job_id=job_id, execution_context=ExecutionContext.TEST,
                                     client=client, model="gemini-test")


def test_fixture_script_is_in_range():
    assert gsw.MIN_WORDS <= _words(GOOD) <= gsw.MAX_WORDS
    assert gsw.validate_sections(GOOD, gsw.fact_corpus(TOPIC)) == []


def test_accepted_draft_keeps_the_pipeline_schema():
    out = _write(FakeClient({"angle": "Why nobody knows the richest physics prize", "sections": GOOD}))
    assert out["script_source"] == "llm:gemini-test"
    assert [s["id"] for s in out["sections"]] == list(gsw.SECTION_IDS.values())
    assert [s["emotion"] for s in out["sections"]] == ["hook", "curiosity", "tension", "revelation", "closing"]
    assert out["full_script"].startswith("One physics prize")
    assert out["word_count"] == len(out["full_script"].split())
    assert out["angle"] and out["hook_type"] in {h for h, _ in gsw.HOOK_TYPES}


def test_invented_number_is_rejected_then_fixed_with_feedback():
    bad = [dict(s) for s in GOOD]
    bad[1]["text"] = bad[1]["text"].replace("3 million", "7 million")
    client = FakeClient({"sections": bad}, {"sections": GOOD})
    out = _write(client)
    assert out["script_source"] == "llm:gemini-test"
    assert "numbers not found in the research: 7" in client.prompts[1]
    assert out["writer_attempts"][0]["accepted"] is False


def test_copied_source_text_is_rejected():
    copied = [dict(s) for s in GOOD]
    copied[3]["text"] = "Laureates have included string theorists and cosmologists who mapped the early universe."
    problems = gsw.validate_sections(copied, gsw.fact_corpus(TOPIC))
    assert any("copied verbatim" in p for p in problems)


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s[:4], "exactly"),
    (lambda s: [dict(x, text=x["text"] + " (sic)") if x["stage"] == "HOOK" else x for x in s], "parentheses"),
    (lambda s: [dict(x, text=x["text"] + " Subscribe for more.") if x["stage"] == "PAYOFF" else x for x in s], "subscribe"),
    (lambda s: [dict(x, text="Short.") for x in s], "total length"),
])
def test_rule_violations(mutate, needle):
    problems = gsw.validate_sections(mutate([dict(x) for x in GOOD]), gsw.fact_corpus(TOPIC))
    assert any(needle in p for p in problems), problems


def test_two_rejections_fall_back_to_cleaned_extractive_script():
    bad = [dict(s, text="Short.") for s in GOOD]
    out = _write(FakeClient({"sections": bad}, {"sections": bad}))
    assert out["script_source"] == "extractive_fallback"
    assert "(" not in out["full_script"]
    assert len(out["writer_attempts"]) == 2


def test_api_error_falls_back_without_a_second_call():
    client = FakeClient(ConnectionError("network down"), {"sections": GOOD})
    out = _write(client)
    assert out["script_source"] == "extractive_fallback"
    assert len(client.prompts) == 1
    assert out["writer_attempts"][0]["problems"] == ["ConnectionError"]


def test_malformed_json_is_retried():
    out = _write(FakeClient("not json at all", "```json\n" + json.dumps({"sections": GOOD}) + "\n```"))
    assert out["script_source"] == "llm:gemini-test"


def test_no_key_uses_fallback(monkeypatch):
    monkeypatch.setattr(gsw, "default_client", lambda: (None, None))
    out = gsw.write_grounded_script(TOPIC, "A hook.", job_id="j", execution_context=ExecutionContext.TEST)
    assert out["script_source"] == "extractive_fallback"
    assert out["writer_attempts"] == [{"skipped": "no GEMINI_API_KEY configured"}]


def test_fallback_strips_the_wikipedia_parenthetical(monkeypatch):
    monkeypatch.setattr(gsw, "default_client", lambda: (None, None))
    topic = SimpleNamespace(**{**vars(TOPIC), "title": "Science fiction", "extended_extract":
                            "Science fiction (often shortened to sci-fi or abbreviated SF) is a genre of "
                            "speculative fiction about imagined futures. " + EXTRACT})
    out = gsw.write_grounded_script(topic, "", job_id="j", execution_context=ExecutionContext.TEST)
    assert out["script_source"] == "extractive_fallback"
    assert "(" not in out["full_script"] and "often shortened" not in out["full_script"]
    assert gsw._strip_parentheticals({"sections": [{"text": "Sci-fi (SF) is a genre ."}]})["full_script"] == "Sci-fi is a genre."


def test_hook_type_rotates_deterministically_by_job():
    a, b = gsw.choose_hook_type("cycle_1_20261114_0000"), gsw.choose_hook_type("cycle_1_20261114_0000")
    assert a == b
    assert len({gsw.choose_hook_type(f"cycle_{i}")[0] for i in range(40)}) == len(gsw.HOOK_TYPES)


def test_prompt_carries_rules_and_research():
    prompt = gsw.build_prompt(TOPIC, gsw.HOOK_TYPES[0], "A hook.")
    assert "Every number you use must appear in it" in prompt
    assert "Yuri Milner" in prompt and "Do not open with a dictionary definition" in prompt


def test_orchestrator_uses_the_grounded_writer():
    from backend.app.orchestration import crazzydocs_master_orchestrator as om
    src = open(om.__file__, encoding="utf-8").read()
    assert "lambda: write_grounded_script(" in src
    assert "lambda: compose_structured_script(" not in src
