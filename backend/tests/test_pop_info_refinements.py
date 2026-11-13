"""Stage 3.27.1 refinements: reveal length, phrase endings, photo treatments, measured loudness."""
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from backend.app.rendering.pop_info.scene_planner import (
    PHOTO_TREATMENTS, REVEAL_TARGET_SEC, key_phrase, plan_scenes,
)

STAGES = ["HOOK", "CONTEXT", "ESCALATION", "REVELATION", "PAYOFF"]
TEXTS = [
    "There is a physics award that pays more than a Nobel Prize. It gives winners three million dollars.",
    "Launched in 2012, it honors work in fundamental physics, and Yuri Milner co-founded the foundation behind it.",
    "It is the most lucrative physics prize on Earth, and a special prize can be given at any time.",
    "Even finalists were rewarded well, with automatic nominations for five years.",
    "Solving the mysteries of physics can now pay off bigger than history's most famous award. "
    "The money changes who chases the hardest questions. And it keeps them chasing for years.",
]


def _plan(scene_photos=None, total=50.0):
    secs = [{"id": f"s{i}", "stage": st, "text": t} for i, (st, t) in enumerate(zip(STAGES, TEXTS))]
    words = [len(t.split()) for t in TEXTS]
    cur, tim = 0.0, []
    for i, w in enumerate(words):
        d = (total - 2) * w / sum(words)
        tim.append({"id": f"s{i}", "start_sec": cur, "end_sec": cur + d}); cur += d
    return plan_scenes(job_id="job_r", topic_title="Breakthrough Prize", category="Science", sections=secs,
                       timings=tim, total_duration=total, scene_photos=scene_photos)


def test_reveal_is_about_six_seconds_and_no_narration_is_dropped():
    plan = _plan()
    last = plan["scenes"][-1]
    assert last["end_sec"] - last["start_sec"] <= REVEAL_TARGET_SEC * 1.15 + 0.01
    spoken = " ".join(s["narration"] for s in plan["scenes"])
    assert re.sub(r"\s+", " ", spoken) == re.sub(r"\s+", " ", " ".join(TEXTS))
    assert all(s["end_sec"] - s["start_sec"] > 0.5 for s in plan["scenes"])


@pytest.mark.parametrize("text", [
    "And that is how Breakthrough Prize in Fundamental Physics reshaped our understanding.",
    "His latest paper highlights why clearing space junk is so complicated.",
    "Worry actually pushes certain youth to search for solutions quickly.",
])
def test_emphasis_never_ends_on_a_weak_verb_or_dangling_adverb(text):
    headline, _ = key_phrase(text)
    last = headline.split()[-1].lower().strip(".,")
    assert last not in {"reshaped", "highlights", "quickly", "actually"}, headline
    assert headline and headline in text


def test_assigned_photos_get_varied_treatments_and_moves(tmp_path):
    img = tmp_path / "p.jpg"; img.write_bytes(b"x")
    photo = lambda n: {"path": str(img), "license": "CC BY", "title": f"Photo {n}", "credit": "c", "focus": {"x": 0.5, "y": 0.4}}
    plan = _plan(scene_photos={1: photo(1), 3: photo(2), 5: photo(3)})
    photos = [s for s in plan["scenes"] if s.get("photo")]
    assert [s["template"] for s in photos] == ["photo"] * len(photos) and len(photos) >= 2
    for a, b in zip(photos, photos[1:]):
        assert a["photo"]["treatment"] != b["photo"]["treatment"] and a["photo"]["motion"] != b["photo"]["motion"]
    assert all(p["photo"]["treatment"] in PHOTO_TREATMENTS for p in photos)
    assert plan["scenes"][0]["template"] == "title" and plan["scenes"][-1]["template"] == "reveal"


def _ffmpeg():
    from backend.app.core.paths import get_ffmpeg_path
    p = get_ffmpeg_path()
    if not p or not Path(p).exists():
        pytest.skip("ffmpeg not available")
    return p


def test_mix_reaches_target_loudness_without_clipping(tmp_path):
    ffmpeg = _ffmpeg()
    from backend.app.rendering.pop_info.sound_design import SR, mix_audio
    plan = _plan(total=20.0)
    plan["duration_sec"] = 20.0
    t = np.arange(int(SR * 19)) / SR
    rng = np.random.default_rng(3)   # quiet, speech-like: modulated tones + noise around -30 dBFS
    voice = (0.03 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) + 0.006 * rng.standard_normal(t.size))
    vp = tmp_path / "voice.wav"
    with wave.open(str(vp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((voice * 32767).astype("<i2").tobytes())
    out = tmp_path / "mix.wav"
    res = mix_audio(ffmpeg=ffmpeg, voice_path=str(vp), plan=plan, work_dir=tmp_path / "w", out_path=out)
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(out), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    lufs = float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))
    assert abs(lufs - -14.0) <= 0.8, (lufs, res.get("loudness_passes"))   # independent measurement of the file
    assert peak <= -1.0, peak


def test_title_card_is_the_subject_not_a_hook_phrase():
    from backend.app.rendering.pop_info.scene_planner import title_card
    assert title_card("Breakthrough Prize in Fundamental Physics", "pays more than a Nobel Prize") == \
        "Breakthrough Prize in Fundamental Physics"
    assert title_card("Solving the space junk crisis isn't just about counting trash", "our space junk crisis") == "Space junk"
    assert title_card("Regulus (Turner)", "") == "Regulus"
    plan = _plan()
    assert plan["scenes"][0]["headline"] == "Breakthrough Prize"


def test_process_layout_needs_three_real_labels():
    from backend.app.rendering.pop_info.scene_planner import node_labels
    assert len(node_labels("Meanwhile, that exact same feeling causes others to freeze right up.")) < 3
    plan = _plan()
    for s in plan["scenes"]:
        if s["template"] == "process":
            assert len(s["labels"]) == 3 and not any(l in ("1", "2", "3", "Meanwhile", "Exact", "Same") for l in s["labels"])


@pytest.mark.parametrize("text,bad_end", [
    ("In fact, intense climate fear produces two completely opposite outcomes.", "produces"),
    ("Meanwhile, that exact same feeling causes others to freeze right up.", "same"),
])
def test_emphasis_does_not_cut_a_verb_from_its_object(text, bad_end):
    headline, _ = key_phrase(text)
    assert headline.split()[-1].lower() != bad_end, headline


def test_reveal_never_splits_inside_the_final_sentence():
    secs = [{"id": f"s{i}", "stage": st, "text": t} for i, (st, t) in enumerate(zip(STAGES, TEXTS[:4] + [
        "Climate anxiety can fuel a fight for solutions, or freeze you entirely, depending on how the fear is framed."]))]
    tim, cur = [], 0.0
    for sec in secs:
        d = 9.0 if sec["stage"] == "PAYOFF" else 9.0
        tim.append({"id": sec["id"], "start_sec": cur, "end_sec": cur + d}); cur += d
    plan = plan_scenes(job_id="j", topic_title="Climate anxiety", category="Science", sections=secs, timings=tim,
                       total_duration=cur + 1.6)
    assert plan["scenes"][-1]["narration"].startswith("Climate anxiety can fuel")


def test_year_is_never_rendered_as_a_counter():
    js = (Path(__file__).resolve().parents[1] / "app/rendering/pop_info/engine.js").read_text(encoding="utf-8")
    photo = js[js.index("  photo(c, sc, u, d) {"):js.index("  reveal(c, sc, u, d) {")]
    assert "find(x => !x.is_year);" in photo and "|| (sc.numbers || [])[0]" not in photo


def test_no_clean_phrase_returns_nothing_rather_than_a_raw_word_run():
    assert key_phrase("or freeze you entirely") == ("", "")
    plan = _plan()
    for s in plan["scenes"]:
        assert not s["headline"].lower().startswith(("or ", "and ", "but ")), s["headline"]


def test_phrase_prefers_ending_where_the_clause_ends():
    headline, _ = key_phrase("Worry actually pushes certain youth to search for solutions.")
    assert headline.endswith("solutions"), headline
