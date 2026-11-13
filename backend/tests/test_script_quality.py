"""Structured script-quality evaluation: explicit scores with reasons, honest about what is not measured."""
import sys
from pathlib import Path

from backend.app.content.script_quality import evaluate_script

sys.path.insert(0, str(Path(__file__).parent))
from test_grounded_script_writer import GOOD, TOPIC  # noqa: E402

STAGES = ["HOOK", "CONTEXT", "ESCALATION", "REVELATION", "PAYOFF"]


def _script(texts):
    secs = [{"stage": s, "text": t} for s, t in zip(STAGES, texts)]
    return {"sections": secs, "full_script": " ".join(texts)}


def test_written_script_beats_the_encyclopedia_lead():
    written = evaluate_script({"sections": GOOD, "full_script": " ".join(s["text"] for s in GOOD)}, TOPIC)
    lead = evaluate_script(_script([
        "The Breakthrough Prize in Fundamental Physics is awarded each year to researchers.",
        "Each prize carries an award of 3 million US dollars, roughly three times a Nobel Prize.",
        "It was founded in 2012 by Yuri Milner.",
        "Laureates have included string theorists and cosmologists who mapped the early universe.",
        "And that is how Breakthrough Prize reshaped our understanding. Subscribe to CrazzyDocs for more real-world stories.",
    ]), TOPIC)
    assert written["overall"] > lead["overall"]
    assert lead["criteria"]["hook"]["score"] < written["criteria"]["hook"]["score"]
    assert "definition" in lead["criteria"]["hook"]["reason"]
    assert lead["criteria"]["originality"]["score"] < 5
    assert "templated closing" in lead["criteria"]["payoff"]["reason"]


def test_every_score_has_a_reason():
    r = evaluate_script({"sections": GOOD, "full_script": " ".join(s["text"] for s in GOOD)}, TOPIC)
    for name, c in r["criteria"].items():
        assert c["reason"], name
    assert set(r["criteria"]) >= {"hook", "spoken_style", "structure", "grounding", "originality", "duration_fit", "payoff", "narratability"}


def test_grounding_and_originality_are_not_invented_without_research():
    r = evaluate_script({"sections": GOOD, "full_script": " ".join(s["text"] for s in GOOD)})
    assert r["criteria"]["grounding"]["score"] is None and r["criteria"]["originality"]["score"] is None
    assert r["overall"] is not None


def test_invented_number_lowers_grounding():
    bad = [dict(s) for s in GOOD]
    bad[1]["text"] = bad[1]["text"].replace("3 million", "9 million")
    r = evaluate_script({"sections": bad, "full_script": " ".join(s["text"] for s in bad)}, TOPIC)
    assert r["criteria"]["grounding"]["score"] < 10 and "9" in r["criteria"]["grounding"]["reason"]
