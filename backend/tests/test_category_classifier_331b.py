"""Stage 3.31B: category classification repair (token-aware v2 classifier)."""

import pytest

from backend.app.content import candidate_ledger as cl
from backend.app.content.category_classifier import (
    AMBIGUOUS, CLASSIFIED, OUT_OF_SCOPE, UNKNOWN, CategoryClassifierV2, definition_type, _count, _normalize,
)
from topic_fakes_331 import FakeWeb, make_engine

C = CategoryClassifierV2()

CASES = [
    # (id, title, text, expected status, expected category key or out-of-scope domain)
    ("science_with_era",
     "Physicists probe the inflationary era of the early universe",
     "Physicists used particle experiments and quantum measurements to probe the inflationary era. "
     "Several laboratory results constrain the radiation produced.", CLASSIFIED, "science"),
    ("award_is_not_war",
     "Chemist receives award for molecule discovery",
     "The chemistry award recognised a laboratory experiment on a new molecule. The prize was awarded towards "
     "the end of the year.", CLASSIFIED, "science"),
    ("supernova",
     "This supernova is 50 million light-years away, but that's close enough",
     "Astronomers observed a supernova in a nearby galaxy. Several telescopes tracked the explosion over the era of "
     "observation.", CLASSIFIED, "space_and_astronomy"),
    ("dwarf_star_duo",
     "Astronomers track a flaring dwarf-star duo with exceptional precision",
     "Astronomers used a space telescope to follow two dwarf stars. The flares were recorded over several orbits.",
     CLASSIFIED, "space_and_astronomy"),
    ("science_fiction_genre",
     "Science fiction",
     "Science fiction is a genre of speculative fiction which deals with imaginative concepts such as advanced "
     "science and technology, space exploration and time travel.", OUT_OF_SCOPE, "entertainment"),
    ("historical_war",
     "World War I",
     "World War I was a global conflict between two coalitions. It lasted from 1914 to 1918 and was among the "
     "deadliest wars in history.", CLASSIFIED, "history"),
    ("battle",
     "Battle of Hastings",
     "The Battle of Hastings was fought on 14 October 1066 between the Norman-French army and an English army.",
     CLASSIFIED, "history"),
    ("political_theorist",
     "Mark Satin",
     "Mark Satin is an American political theorist, author and newsletter publisher. He received an award "
     "for his work during the era.", OUT_OF_SCOPE, "politics"),
    ("dissident",
     "Wei Jingsheng",
     "Wei Jingsheng is a Chinese human rights activist and dissident known for his essay on democracy.",
     OUT_OF_SCOPE, "politics"),
    ("geopolitical_attack",
     "November 2015 Paris attacks",
     "On 13 November 2015, a series of coordinated Islamist terrorist attacks took place in Paris.",
     OUT_OF_SCOPE, "politics"),
    ("music",
     "Can't Let Go (Mariah Carey song)",
     "\"Can't Let Go\" is a song recorded by American singer Mariah Carey for her first studio album.",
     OUT_OF_SCOPE, "music"),
    ("sport",
     "Lionel Messi",
     "Lionel Messi is an Argentine professional footballer who plays as a forward and captains his national team.",
     OUT_OF_SCOPE, "sport"),
    ("volcano",
     "Aucanquilcha",
     "Aucanquilcha is a massive stratovolcano in northern Chile. It formed during the Pleistocene era and "
     "has several summits.", CLASSIFIED, "geography"),
    ("strait",
     "Nares Strait",
     "Nares Strait is a waterway between Ellesmere Island and Greenland that connects Baffin Bay with the Arctic Ocean.",
     CLASSIFIED, "geography"),
    ("natural_history",
     "How woodcocks evolved to be the weird cousins of their family",
     "Woodcocks are birds whose evolution produced unusual traits. Researchers studied several species and their habitat.",
     CLASSIFIED, "nature_and_wildlife"),
    ("ambiguous_or_empty",
     "Thing",
     "Something happened somewhere to someone a while ago.", UNKNOWN, None),
    ("painting_no_evidence",
     "Regulus (Turner)",
     "Regulus is an oil painting by the English artist J. M. W. Turner. It received an award in that era.",
     UNKNOWN, None),
]


@pytest.mark.parametrize("cid,title,text,status,expected", CASES, ids=[c[0] for c in CASES])
def test_regression_cases(cid, title, text, status, expected):
    r = C.classify(title, text)
    assert r.status == status, (r.status, r.scores, r.evidence)
    if status == OUT_OF_SCOPE:
        assert r.out_of_scope_domain == expected and r.category_key is None
    elif expected is None:
        assert r.category_key is None
    else:
        assert r.category_key == expected, (r.scores, r.evidence)
        assert r.display_name and r.category_code


def test_word_boundaries_not_substrings():
    t = _normalize("several awards toward generally operational era")
    assert _count("war", t) == 0
    assert _count("era", t) == 1
    assert _count("light-years", _normalize("50 million light years away")) == 1
    assert _count("galaxy", _normalize("two galaxies collided")) == 1          # plural suffix


def test_v1_substring_failure_is_fixed_but_v1_untouched():
    from backend.app.content.creative_production_policy import get_creative_production_policy
    pol = get_creative_production_policy()
    text = "Mark Satin received an award for writing in that era about several topics."
    assert pol.classify_topic("Mark Satin", text).key == "history"          # v1 unchanged (substring "war"/"era")
    assert C.classify("Mark Satin", text).category_key != "history"


def test_deterministic_and_tie_break_not_history_by_default():
    title, text = "Glacier telescope", "A glacier and a telescope."
    first = C.classify(title, text)
    for _ in range(5):
        again = C.classify(title, text)
        assert (again.status, again.category_key, again.scores) == (first.status, first.category_key, first.scores)
    assert first.status == AMBIGUOUS
    # equal evidence: tie-break order is score, title hits, distinct terms, hint, then key alphabetically
    assert first.category_key == "geography"
    hinted = C.classify(title, text, hint_key="space_and_astronomy")
    assert hinted.category_key == "space_and_astronomy"


def test_hint_is_evidence_not_verdict():
    # a strong out-of-scope definition is not overridden by a discovery hint
    r = C.classify("Lionel Messi", "Lionel Messi is an Argentine professional footballer.", hint_key="science")
    assert r.status == OUT_OF_SCOPE


def test_definition_context():
    assert definition_type("X", "X is a stratovolcano in Chile.") == "geography"
    assert definition_type("X", "X is a proposed space probe concept.") == "space_and_astronomy"
    assert definition_type("X", "X is a 1991 song by a singer.") == "oos:music"
    assert definition_type("X", "No definition here.") is None


# ---- engine integration (flags) ---------------------------------------------------------------
def _engine(tmp_path, **flags):
    web = FakeWeb(search={}, kinds={})
    eng = make_engine(tmp_path, web, **flags)
    return eng, web


def test_flag_off_keeps_v1_category(tmp_path):
    eng, _ = _engine(tmp_path)
    cand = {"title": "Aucanquilcha", "extended_extract": "Aucanquilcha is a stratovolcano in Chile.",
            "category": "History", "category_code": "H"}
    eng._apply_category(cand, None)
    assert cand["category"] == "History" and "category_v2" not in cand


def test_shadow_records_without_changing_category(tmp_path):
    eng, _ = _engine(tmp_path, category_classifier_v2="shadow")
    cand = {"title": "Aucanquilcha", "extended_extract": "Aucanquilcha is a stratovolcano in Chile.",
            "category": "History", "category_code": "H"}
    eng._apply_category(cand, None)
    assert cand["category"] == "History"
    assert cand["category_v2"]["category_key"] == "geography"


def test_on_assigns_v2_category_and_hint_fallback(tmp_path):
    eng, _ = _engine(tmp_path, category_classifier_v2="on")
    cand = {"title": "Aucanquilcha", "extended_extract": "Aucanquilcha is a stratovolcano in Chile.", "category": "History"}
    eng._apply_category(cand, None)
    assert cand["category"] == "Geography" and "category_invalid" not in cand
    vague = {"title": "Kelvaro", "extended_extract": "Kelvaro happened somewhere.", "category": "History"}
    eng._apply_category(vague, "engineering")
    assert vague["category"] == "Engineering" and "category_invalid" not in vague
    vague2 = {"title": "Kelvaro", "extended_extract": "Kelvaro happened somewhere.", "category": "History"}
    eng._apply_category(vague2, None)
    assert vague2["category_invalid"] == UNKNOWN


def test_on_rejects_out_of_scope_candidates_before_ranking(tmp_path):
    q = "pivotal historical event archival record"
    web = FakeWeb(search={q: ["Songtitle", "Volcanotitle"]}, kinds={"Songtitle": "song", "Volcanotitle": "volcano"})
    eng = make_engine(tmp_path, web, category_classifier_v2="on", candidate_ledger="on")
    eng.policy.categories = {"history": eng.policy.categories["history"], **{k: v for k, v in eng.policy.categories.items() if k != "history"}}
    eng.begin_run("cycle_cat")
    ranked = eng.discover_and_rank_live_topics(preferred_category="History")
    titles = {t.title for t in ranked}
    assert "Volcanotitle" in titles and "Songtitle" not in titles
    led = cl.CandidateLedger(str(tmp_path / "studio.db"))
    row = led.get(cl.candidate_id("wikipedia_search:history", "Songtitle"))
    assert row["rejection_reason"] == cl.INVALID_CATEGORY and row["rejection_stage"] == "category_gate"
    vol = [t for t in ranked if t.title == "Volcanotitle"][0]
    assert vol.category == "Geography"
