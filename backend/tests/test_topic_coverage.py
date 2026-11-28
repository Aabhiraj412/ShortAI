"""Topic coverage (2026-11-28): one rule for the script writer and production QC CHECK_A.

cycle_5_20261128_0400 ("Generative pre-trained transformer") was blocked by CHECK_A although its Groq narration said
"GPTs" eight times and "the transformer": the legacy rule wanted two title words verbatim. The writer had no such
check, so it accepted the draft and spent no revision on it. Fakes only: no network, no production database."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import backend.app.content.grounded_script_writer as gsw
from backend.app.content import topic_coverage as tc
from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.services import llm_providers as lp

sys.path.insert(0, str(Path(__file__).parent))
from test_gemini_writer_resilience import ScriptedClient, ServerError  # noqa: E402
from test_grounded_script_writer import GOOD, TOPIC  # noqa: E402
from test_llm_providers import _write, clock  # noqa: E402,F401  (clock is a fixture)

# The exact narration production QC rejected (media/productions/cycle_5_20261128_0400/scripts/script_*.json).
CYCLE5_TITLE = "Generative pre-trained transformer"
CYCLE5 = ("GPTs can write new stories from just a few words. That surprise comes because they learn from massive "
          "unlabeled data. They are built on the transformer, a deep learning architecture that processes sequences. "
          "OpenAI first applied this in 2018 with GPT‑1. By late 2022 ChatGPT, powered by GPT‑3.5, hit the "
          "market. Soon after, Gemini, DeepSeek, Claude and others followed suit. Beyond text, GPTs can produce images "
          "and audio, making them multimodal. For tough tasks, reasoning GPTs spend extra compute to analyze before "
          "answering. It does so without explicit programming. So when a chatbot composes a poem, know it draws from a "
          "vast pool of unseen content it studied alone.")


def _gate(title, narration, tmp_path):
    r = ContentIntegrityGate(workspace_root=str(tmp_path)).validate_production(
        topic_title=title, category="Science", narration_text=narration, visual_beat_configs=[],
        video_path=str(tmp_path / "x.mp4"), thumbnail_path=str(tmp_path / "x.png"), thumbnail_headline="X",
        metadata_title=title, metadata_description=narration, metadata_tags=["x"], attribution_text="")
    return next(c for c in r.checks if c.check_name == "CHECK_A_TOPIC_NARRATION")


# -- cycle 5 ------------------------------------------------------------------------------------------------------
def test_cycle5_exact_failure_is_now_recognised_as_on_topic(tmp_path):
    c = tc.check(CYCLE5_TITLE, CYCLE5)
    assert c.legacy_passed is False and c.legacy_matches == ["transformer"]      # what blocked the production
    assert c.passed and c.acronym == "gpt" and c.missing == []
    a = _gate(CYCLE5_TITLE, CYCLE5, tmp_path)
    assert a.passed and "acronym GPT" in a.details


def test_cycle5_without_the_acronym_still_needs_a_second_concept(tmp_path):
    no_acr = CYCLE5.replace("GPTs", "These models").replace("GPT‑", "version ")
    c = tc.check(CYCLE5_TITLE, no_acr)
    assert not c.passed and c.covered == ["transformer"] and set(c.missing) == {"generative", "trained"}
    assert not _gate(CYCLE5_TITLE, no_acr, tmp_path).passed
    assert tc.check(CYCLE5_TITLE, no_acr + " They generate text after pre-training.").passed


# -- natural forms ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("title,narration", [
    ("Fiona hippopotamus", "Fiona the hippo was born six weeks early."),                 # clipping (cycle_10_20261124)
    ("Market economics", "Markets set prices, and the whole economy adjusts."),            # inflection (cycle_21_20261121)
    ("Geographical exploration", "Exploration redrew every map; geography itself changed."),
    ("Generative pre-trained transformer", "A transformer generates text after training on books."),
])
def test_valid_scripts_using_natural_forms_pass(title, narration, tmp_path):
    assert tc.check(title, narration).passed
    assert _gate(title, narration, tmp_path).passed


# -- genuinely missing concepts -----------------------------------------------------------------------------------
@pytest.mark.parametrize("title,narration", [
    ("Dead Sea Scrolls", "Shepherds found clay jars in a cave near Qumran, full of brittle parchment."),
    ("Stane Street", "A Roman road ran straight from London to Chichester."),
    ("Scientific method", "Experiments test ideas against evidence, again and again."),
])
def test_script_missing_the_topic_is_still_rejected(title, narration, tmp_path):
    c = tc.check(title, narration)
    assert not c.passed and c.missing
    a = _gate(title, narration, tmp_path)
    assert not a.passed and a.details.startswith("Narration lacks topic keywords")   # marker downstream code parses
    fb = c.feedback()
    assert all(m in fb for m in c.missing) and "natural form" in fb


# -- generic words ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("title,narration", [
    ("Ancient Egypt", "Egypt built its pyramids with copper tools and ramps."),           # cycle_18_20261125
    ("Retail industry", "Retailers track every shelf with a barcode."),                     # cycle_24_20261121
    ("Finding the catalyst sweet spot: turning CO2 into methane",
     "A nickel catalyst works best at one temperature, and the gas becomes methane."),
])
def test_generic_title_words_are_not_mandatory(title, narration, tmp_path):
    c = tc.check(title, narration)
    assert c.passed and not set(c.essential) & tc.GENERIC
    assert _gate(title, narration, tmp_path).passed


def test_generic_words_alone_do_not_count_as_covering_the_topic():
    c = tc.check("Finding the catalyst sweet spot: turning CO2 into methane", "Scientists keep turning ideas into fuel.")
    assert c.covered == [] and set(c.missing) == {"catalyst", "sweet", "spot", "methane"}


def test_acronym_counts_only_when_written_as_one():
    title = "Computed axial tomography"
    assert tc.check(title, "A CAT scan slices the body into images.").passed
    assert not tc.check(title, "The cat sat still while the machine hummed.").passed


@pytest.mark.parametrize("title,narration", [
    ("Regulus (Turner)", "Regulus is an oil painting by J. M. W. Turner."),
    ("Space-junk economics, explained", "Space junk is an economics problem."),
    ("Breakthrough Prize in Fundamental Physics", " ".join(s["text"] for s in GOOD)),
    ("History of the Antikythera mechanism", "Its history begins with Antikythera divers."),     # generic + one name
])
def test_never_stricter_than_the_legacy_rule(title, narration):
    c = tc.check(title, narration)
    assert c.legacy_passed and c.passed


# -- the script writer: one revision with the feedback, every provider ---------------------------------------------
OFF_TOPIC = [dict(s) for s in GOOD]
OFF_TOPIC[0]["text"] = "One award pays three times more than a Nobel. So why have most people never heard of it?"
OFF_TOPIC[1]["text"] = OFF_TOPIC[1]["text"].replace("It is called the Breakthrough Prize. ", "It has a bold name. ")


def test_validator_reports_only_the_topic_problem_for_an_off_topic_draft():
    corpus = gsw.fact_corpus(TOPIC)
    assert gsw.validate_sections(GOOD, corpus, TOPIC.title) == []
    assert gsw.validate_sections(OFF_TOPIC, corpus) == []                    # the old validator accepted it
    problems = gsw.validate_sections(OFF_TOPIC, corpus, TOPIC.title)
    assert len(problems) == 1 and problems[0].startswith("the narration does not name the topic")
    assert "Name the topic itself" in gsw.build_prompt(TOPIC, ("x", "y"), "")


def test_gemini_draft_gets_one_revision_with_the_topic_feedback(monkeypatch, clock):
    c = ScriptedClient({"primary": [{"sections": OFF_TOPIC}, {"sections": GOOD}], "backup": []})
    prompts = []
    orig = gsw.build_prompt
    monkeypatch.setattr(gsw, "build_prompt", lambda *a, **k: prompts.append(orig(*a, **k)) or prompts[-1])
    out = _write(c, monkeypatch)
    assert out["script_source"] == "llm:primary" and c.calls == ["primary", "primary"]
    tried = [a for a in out["writer_attempts"] if "attempt" in a]
    assert [a["accepted"] for a in tried] == [False, True]
    assert "does not name the topic" in tried[0]["problems"][0] and "does not name the topic" in prompts[1]


def test_groq_draft_gets_one_revision_with_the_topic_feedback(monkeypatch, clock):
    monkeypatch.setattr(lp, "key_set", lambda p: True)
    monkeypatch.setenv("CRAZZYDOCS_LLM_ALTERNATES", "groq:openai/gpt-oss-20b")
    drafts, prompts = [OFF_TOPIC, GOOD], []

    def fake(provider, model, prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"sections": drafts.pop(0)}), {"total_tokens": 900, "response_status": "ok"}, {}
    monkeypatch.setattr(lp, "generate_openai_compat", fake)
    quota = ServerError(429, "RESOURCE_EXHAUSTED")
    out = _write(ScriptedClient({"primary": [quota], "backup": [quota]}), monkeypatch)
    assert out["script_source"] == "llm:groq:openai/gpt-oss-20b" and len(prompts) == 2
    assert "does not name the topic" in prompts[1] and tc.check(TOPIC.title, out["full_script"]).passed


def test_two_off_topic_drafts_fall_back_to_an_extractive_script_that_passes_qc(monkeypatch, clock, tmp_path):
    c = ScriptedClient({"primary": [{"sections": OFF_TOPIC}, {"sections": OFF_TOPIC}], "backup": []})
    out = _write(c, monkeypatch)
    assert c.calls == ["primary", "primary"]                                    # bounded: one revision, no more
    assert out["script_source"] == "extractive_fallback"
    assert tc.check(TOPIC.title, out["full_script"]).passed and _gate(TOPIC.title, out["full_script"], tmp_path).passed
