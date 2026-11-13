"""Hook evaluator: strong openings must score clearly above weak ones, for stated reasons."""
import pytest

from backend.app.content.hook_evaluator import evaluate_hook

STRONG = [
    ("Breakthrough Prize in Fundamental Physics",
     "There is a physics award that pays more than double the cash of a Nobel Prize. It gives winners three million dollars."),
    ("Space junk", "Most people picture a garbage truck in orbit. But catching space junk is not the hard part, paying for it is."),
    ("Drone bridge inspection", "Why would a city trust a drone to find bridge cracks no engineer could see?"),
]
WEAK = [
    ("Breakthrough Prize in Fundamental Physics", "The Breakthrough Prize in Fundamental Physics is awarded each year to researchers."),
    ("Science fiction", "Science fiction is a genre of speculative fiction which typically deals with imaginative concepts."),
    ("Space junk", "Did you know that there are many interesting things about space junk?"),
    ("Rhine", "Today we are going to talk about the Rhine and some various important aspects of its history."),
]


@pytest.mark.parametrize("title,hook", STRONG)
def test_strong_hooks_score_well(title, hook):
    r = evaluate_hook(hook, topic_title=title)
    assert r["score"] >= 5.5, r
    assert not r["penalties"]


@pytest.mark.parametrize("title,hook", WEAK)
def test_weak_hooks_score_low_with_a_penalty(title, hook):
    r = evaluate_hook(hook, topic_title=title)
    assert r["score"] <= 2.5, r
    assert r["penalties"]


def test_scores_are_spread_not_saturated():
    strong = [evaluate_hook(h, topic_title=t)["score"] for t, h in STRONG]
    weak = [evaluate_hook(h, topic_title=t)["score"] for t, h in WEAK]
    assert min(strong) - max(weak) >= 3
    assert max(strong) < 10   # no hook is perfect by construction


def test_expletive_there_is_not_a_definition():
    r = evaluate_hook("There is a prize that pays more than a Nobel.", topic_title="Breakthrough Prize")
    assert not any("encyclopedia" in p["reason"] for p in r["penalties"])


def test_every_characteristic_has_evidence():
    r = evaluate_hook(STRONG[1][1], topic_title=STRONG[1][0])
    assert set(r["characteristics"]) == {"curiosity", "specificity", "surprise", "stakes", "contradiction",
                                         "clarity", "relevance", "continuation"}
    assert all(c["evidence"] for c in r["characteristics"].values())
