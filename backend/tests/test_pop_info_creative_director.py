"""
Creative Engine 3.28: illustrated environments, video-level creative direction, story-driven scene
casting and diversity constraints, kinetic typography. Pure planning tests (no rendering, no DB).
"""
import re
from pathlib import Path

from backend.app.rendering.pop_info import creative_director as cd
from backend.app.rendering.pop_info import scene_planner as sp
from backend.app.rendering.pop_info.film_builder import ENGINE_JS, ENVIRONMENTS_JS

from test_pop_info_engine import SECTIONS, TIMINGS, _plan

ENV_JS = ENVIRONMENTS_JS.read_text(encoding="utf-8")


def _plan_for(title, category, texts, job="job_cd"):
    stages = ["HOOK", "CONTEXT", "ESCALATION", "REVELATION", "PAYOFF"]
    sections = [{"id": f"s{i}", "stage": stages[min(i, 4)], "text": t} for i, t in enumerate(texts)]
    timings = [{"id": f"s{i}", "start_sec": i * 9.0, "end_sec": (i + 1) * 9.0} for i in range(len(texts))]
    return sp.plan_scenes(job_id=job, topic_title=title, category=category, sections=sections, timings=timings,
                          total_duration=len(texts) * 9.0 + 1.6)


# ------------------------------------------------------------------ environments library
def test_all_thirteen_environments_exist_in_the_renderer():
    assert len(cd.ENVIRONMENTS) == 13
    for env in cd.ENVIRONMENTS:
        assert re.search(rf"\b{env}\(c, t\)", ENV_JS), env
        assert env in cd.ENV_KEYWORDS


def test_environments_are_deterministic_offline_and_character_free():
    assert "Math.random" not in ENV_JS
    assert not re.search(r"https?://", ENV_JS)
    code = re.sub(r"//[^\n]*", "", ENV_JS)          # the header comment states the rule; the code must obey it
    for banned in ("mascot", "character", "person(", "face(", "drawShopper"):
        assert banned not in code


def test_engine_has_environment_and_kinetic_templates_and_paints_places():
    js = ENGINE_JS.read_text(encoding="utf-8")
    for t in ("environment", "kinetic"):
        assert t in sp.TEMPLATES and re.search(rf"\b{t}\(c, sc, u, d\)", js)
    assert "window.POP_ENV.paint(c, sc.environment, t)" in js
    assert "sc.backdrop === 'environment'" in js


# ------------------------------------------------------------------ video-level direction
def test_direction_reads_the_place_from_title_category_and_script():
    d = cd.video_direction(job_id="j", topic_title="Regulus (Turner)", category="Art",
                           texts=["Regulus is an oil painting by the English artist J. M. W. Turner.",
                                  "It was exhibited in Rome in 1828 and reworked decades later."])
    assert d["primary_environment"] == "historical"
    d2 = cd.video_direction(job_id="j", topic_title="SoHo Weekly News", category="Media",
                            texts=["The weekly newspaper covered music, art and the city.",
                                   "Its editors launched careers of famous journalists."])
    assert d2["primary_environment"] == "newsroom"
    d3 = cd.video_direction(job_id="j", topic_title="Kessler syndrome", category="Space",
                            texts=["Debris in orbit around the planet could trigger a cascade of collisions."])
    assert d3["primary_environment"] == "space" and d3["palette_mood"] == "night"


def test_direction_is_deterministic_and_recorded_in_the_plan():
    a, b = _plan("job_a"), _plan("job_a")
    assert a["direction"] == b["direction"]
    assert a["direction"]["primary_environment"] in cd.ENVIRONMENTS
    assert a["engine_revision"] == "3.28"


# ------------------------------------------------------------------ scene casting
def test_content_layouts_are_never_recast():
    for template in ("stat", "compare", "timeline", "process", "photo", "title", "reveal"):
        assert cd.cast_scene(template, stage="CONTEXT", text="In the city lab nobody ever saw this!", headline="city lab",
                             previous=None, recent_modes=[], direction={}) == template


def test_a_setting_becomes_a_place_and_an_emphatic_line_becomes_type():
    place = cd.cast_scene("spotlight", stage="CONTEXT", text="Inside the factory the machines never stopped.",
                          headline="machines never stopped", previous=None, recent_modes=[], direction={})
    assert place == "environment"
    typo = cd.cast_scene("callout", stage="REVELATION", text="But nobody had ever tested the idea.",
                         headline="nobody tested the idea", previous=None, recent_modes=[], direction={})
    assert typo == "kinetic"


def test_weak_lines_never_become_kinetic_type():
    for weak in ("winners have", "people never", "it is"):
        assert cd.cast_scene("callout", stage="REVELATION", text="But only some winners have.", headline=weak,
                             previous=None, recent_modes=[], direction={}) == "callout"


def test_casting_never_repeats_the_previous_layout_or_a_mode_three_times():
    assert cd.cast_scene("spotlight", stage="CONTEXT", text="Inside the factory.", headline="the factory floor",
                         previous="environment", recent_modes=[], direction={}) != "environment"
    assert cd.cast_scene("spotlight", stage="CONTEXT", text="Inside the factory.", headline="factory floor",
                         previous="photo", recent_modes=["environment", "environment"], direction={}) != "environment"


# ------------------------------------------------------------------ whole-plan diversity
TEXTS = [
    "Inside a quiet laboratory, a chemist noticed something strange about a common molecule.",
    "The experiment took place in a basement lab in 1952, with equipment worth 40 thousand dollars.",
    "The molecule changed colour when heated, which nobody had predicted.",
    "But the real surprise came later, inside the hospital where doctors tried it on patients.",
    "Today the same compound sits in medicine cabinets around the world.",
    "Not a single textbook mentions the chemist by name.",
]


def test_every_film_shows_its_place_and_mixes_visual_modes():
    plan = _plan_for("The forgotten molecule", "Science", TEXTS)
    t = [s["template"] for s in plan["scenes"]]
    modes = [s["visual_mode"] for s in plan["scenes"]]
    assert t[0] == "title" and t[-1] == "reveal"
    assert "environment" in t
    assert {"diagram", "environment", "illustration"} <= set(modes)
    assert all(a != b for a, b in zip(t, t[1:]))
    for i in range(2, len(modes)):
        generic_left = any(plan["scenes"][j]["template"] in cd.GENERIC for j in (i - 1, i))
        assert not (modes[i] == modes[i - 1] == modes[i - 2] and generic_left), (i, modes)


def test_environment_scenes_are_story_driven_and_recorded():
    plan = _plan_for("The forgotten molecule", "Science", TEXTS)
    envs = {s["environment"] for s in plan["scenes"]}
    assert envs <= set(cd.ENVIRONMENTS)
    assert plan["direction"]["primary_environment"] == "lab"
    hosp = [s for s in plan["scenes"] if "hospital" in s["narration"].lower()]
    assert hosp and all(s["environment"] == "hospital" for s in hosp)   # the scene's own place wins
    for s in plan["scenes"]:
        if s["template"] == "environment" or s["backdrop"]:
            assert f"procedural:environment:{s['environment']}" in s["asset_requirements"]
    fp = plan["fingerprints"]
    assert fp["visual_modes"] == [s["visual_mode"] for s in plan["scenes"]]
    assert set(fp["environments"]) <= set(cd.ENVIRONMENTS)


def test_payoff_scenes_stay_clean_and_setup_diagrams_sit_in_the_place():
    plan = _plan()
    for s in plan["scenes"]:
        if s["stage"] in ("REVELATION", "PAYOFF") and s["template"] != "title":
            assert s["backdrop"] == ""
        if s["template"] in ("stat", "timeline") and s["stage"] in ("HOOK", "CONTEXT", "ESCALATION"):
            assert s["backdrop"] == "environment"


def test_kinetic_scenes_get_word_taps():
    plan = _plan_for("The forgotten molecule", "Science", TEXTS)
    for s in plan["scenes"]:
        if s["template"] == "kinetic":
            assert sum(1 for c in s["sfx"] if c["kind"] == "tap") >= 2
            assert cd._strong_line(s["headline"])


# ------------------------------------------------------------------ photo selection (3.28)
def test_typography_beat_keeps_its_type_unless_a_strong_name_is_spoken():
    from backend.app.rendering.pop_info import scene_photos as photos
    scenes = [{"scene_id": f"scene_{i:02d}", "template": t, "narration": n} for i, (t, n) in enumerate([
        ("title", "Intro."),
        ("callout", "Turner exhibited Regulus in Rome."),
        ("spotlight", "Nothing happened."),
        ("kinetic", "Turner exhibited Regulus in Rome."),
        ("reveal", "End."),
    ])]
    needs = photos.photo_needs({"scenes": scenes}, "Regulus")
    idx = [n.scene_index for n in needs]
    assert 1 in idx and 3 not in idx          # same narration: the kinetic beat ranks below the callout
    scenes[3]["narration"] = "Joseph Mallord William Turner met Charles Eastlake in Rome."
    assert 3 in [n.scene_index for n in photos.photo_needs({"scenes": scenes}, "Regulus")]


# ------------------------------------------------------------------ spelled numbers and per-film budgets
def test_spelled_numbers_become_data_scenes():
    nums = sp.extract_numbers("a prize worth three million dollars can be given; five thousand years of history")
    assert [(n.text, n.unit) for n in nums] == [("3", "MILLION DOLLARS"), ("5,000", "YEARS")]
    assert sp.extract_numbers("One physics prize pays more than double the cash") == []
    plan = _plan_for("Breakthrough Prize", "Science", [
        "There is a physics award that pays more than a Nobel Prize.",
        "Launched in July 2012, it honors transformative work in fundamental physics.",
        "A special prize worth three million dollars can be given at any time.",
        "Finalists receive automatic nominations for five years.",
        "Solving the mysteries of physics can now pay off bigger than history's most famous award."])
    assert "stat" in [s["template"] for s in plan["scenes"]]


def test_typography_and_places_stay_within_the_film_budget():
    texts = ["But nobody saw it coming in the city.", "Not one expert believed the lab results.",
             "Yet inside the factory, nothing changed.", "Only the newspaper noticed the truth.",
             "Still, the hospital never told patients.", "Even the school forgot the lesson.",
             "But the bank kept the money anyway.", "Nobody remembers the story today."]
    plan = _plan_for("A hidden story", "History", texts)
    t = [s["template"] for s in plan["scenes"]]
    budget = cd.caps(len(t))
    assert t.count("kinetic") <= budget["kinetic"] and t.count("environment") <= budget["environment"] + 1


def test_title_card_never_names_a_time_phrase():
    script = "Five thousand years ago the Rhine was crossed. Years ago, years ago. Double axes and coarse pottery."
    card = sp.title_card("Double axes and coarse pottery reveal connections across the Rhine 5,000 years ago", script)
    assert "years" not in card.lower() and "ago" not in card.lower()


def test_photo_anchor_is_never_a_time_phrase():
    from backend.app.rendering.pop_info import scene_photos as photos
    title = "Double axes and coarse pottery reveal connections across the Rhine 5,000 years ago"
    anchor = photos.topic_anchor(title, "Five thousand years ago. Years ago the Rhine. Double axes and coarse pottery.")
    assert "years" not in anchor.lower() and "ago" not in anchor.lower()
