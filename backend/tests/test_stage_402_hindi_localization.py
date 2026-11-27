"""Stage 4.02: Hindi localization pipeline.

Fake Gemini clients, fake voices (FFmpeg test tones), temporary databases and media folders. FFmpeg/libass are real
(the Roman-script subtitle render check). No network, no real token, no production data.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.localization import qc as hqc
from backend.app.localization import render as hrender
from backend.app.localization import script_qc, translator, voice
from backend.app.localization.pipeline import (COORDINATOR_STEPS, KIND, HindiLocalizer, InfrastructureWait, enqueue,
                                               overview)

FIX = Path(__file__).parent / "fixtures" / "hindi_fixture_cycle_9_20261127_0800.json"
JOB = "cycle_9_20261127_0800"


def _ffmpeg():
    from backend.app.core.paths import get_ffmpeg_path, get_ffprobe_path
    return get_ffmpeg_path(), get_ffprobe_path()


def english_plan():
    scenes, t = [], 0.0
    data = [("HOOK", 6.1, "You might assume public monuments in English cities have always celebrated diverse communities.", "Hibiscus Rising", "Rising"),
            ("HOOK", 3.0, "In Leeds, that was not the case until recently.", "case until", "until"),
            ("CONTEXT", 8.6, "On 24 November 2023, artist Yinka Shonibare revealed the outdoor artwork Hibiscus Rising during LEEDS 2023.", "Hibiscus Rising during LEEDS 2023", "2023"),
            ("CONTEXT", 5.5, "This piece honors the life of David Oluwale, a British-Nigerian man.", "David Oluwale", "Oluwale"),
            ("ESCALATION", 7.0, "Oluwale died in 1969 in an event involving two members of Leeds City Police. To remember him,", "Leeds City Police", "Police"),
            ("ESCALATION", 3.3, "LEEDS 2023 partnered with Leeds City Council", "Leeds City Council", "Council"),
            ("ESCALATION", 4.5, "and the David Oluwale Memorial Association to commission it.", "David Oluwale Memorial", "Memorial"),
            ("REVELATION", 5.5, "According to academic Dr. Emily Zobel Marshall, this monument holds historic weight.", "Emily Zobel Marshall", "Marshall"),
            ("REVELATION", 5.3, "It is the very first public artwork in Leeds to reflect its cultural diversity.", "cultural diversity", "diversity"),
            ("PAYOFF", 7.9, "Hibiscus Rising transforms a painful history into a lasting symbol of inclusion.", "Hibiscus Rising", "Rising")]
    for i, (stage, d, nar, head, hl) in enumerate(data, start=1):
        scenes.append({"scene_id": f"scene_{i:02d}", "stage": stage, "start_sec": round(t, 3), "end_sec": round(t + d, 3),
                       "narration": nar, "headline": head, "highlight": hl, "labels": [], "numbers": [],
                       "template": "title", "sfx": [{"t": round(t, 3), "kind": "whoosh", "gain": 0.5},
                                                    {"t": round(t + 0.35, 3), "kind": "pop", "gain": 0.4}]})
        t += d
    return {"engine": "pop_info", "job_id": JOB, "topic": "Hibiscus Rising", "category": "Space and Astronomy",
            "duration_sec": round(t, 2), "design": {"width": 1080, "height": 1920}, "scenes": scenes}


def fixture_hindi():
    d = json.loads(FIX.read_text(encoding="utf-8"))
    d.pop("_note", None)
    return d


def request():
    return translator.build_request(topic="Hibiscus Rising", category="Space and Astronomy",
                                    script={"full_script": "x"}, plan=english_plan())


# -- 1. translation and adaptation -------------------------------------------------------------------------------

def test_hindi_models_never_use_the_english_chain(monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_HINDI_MODELS", raising=False)
    models = translator.hindi_models(["gemini-3.8-flash", "gemini-3.6-flash"])
    assert models and not set(models) & {"gemini-3.8-flash", "gemini-3.6-flash"}
    monkeypatch.setenv("CRAZZYDOCS_HINDI_MODELS", "gemini-3.8-flash,gemma-4-31b-it")
    assert translator.hindi_models(["gemini-3.8-flash"]) == ["gemma-4-31b-it"]


class FakeClient:
    def __init__(self, plan):
        self.plan = {m: list(v) for m, v in plan.items()}
        self.prompts = []
        self.models = self

    def generate_content(self, *, model, contents, config=None):
        self.prompts.append((model, contents))
        item = self.plan[model].pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def test_translation_success_and_prompt_rules():
    c = FakeClient({"m1": [fixture_hindi()]})
    hindi, attempts = translator.translate(request(), client=c, models=["m1"], validate=script_qc.validate)
    assert hindi["_model"] == "m1" and attempts[-1]["ok"]
    prompt = c.prompts[0][1]
    assert "HINGLISH" in prompt and "Do not add facts" in prompt and "Do NOT translate sentence by sentence" in prompt
    assert "never Devanagari" in prompt and "Keep familiar English words, technical terms" in prompt
    assert "Aapke brain mein ek fascinating process" in prompt                 # the policy's own example


def test_rule_breaking_answer_is_retried_with_feedback_then_rejected():
    bad = fixture_hindi()
    for k in ("narration", "narration_tts"):
        bad["scenes"][4][k] = bad["scenes"][4][k].replace("1969", "1970")
    c = FakeClient({"m1": [bad, bad], "m2": [fixture_hindi()]})
    with pytest.raises(translator.TranslationRejected) as e:
        translator.translate(request(), client=c, models=["m1", "m2"], validate=script_qc.validate)
    assert any("1969" in p for p in e.value.problems) and any("1970" in p for p in e.value.problems)
    assert "previous answer had these problems" in c.prompts[1][1]
    assert [m for m, _ in c.prompts] == ["m1", "m1"]                     # a rule problem does not move on


def test_feedback_retry_can_fix_the_answer():
    bad = fixture_hindi()
    bad["scenes"][0]["highlight"] = "missing"
    c = FakeClient({"m1": [bad, fixture_hindi()]})
    hindi, attempts = translator.translate(request(), client=c, models=["m1"], validate=script_qc.validate)
    assert [a["ok"] for a in attempts] == [False, True]


def test_every_model_unavailable_is_an_infrastructure_condition():
    c = FakeClient({"m1": [ApiError(429)], "m2": [ApiError(503)], "m3": [ApiError(404)]})
    with pytest.raises(translator.ProviderUnavailable):
        translator.translate(request(), client=c, models=["m1", "m2", "m3"], validate=script_qc.validate)


def test_malformed_json_is_retried():
    c = FakeClient({"m1": ["not json at all", fixture_hindi()]})
    hindi, attempts = translator.translate(request(), client=c, models=["m1"], validate=script_qc.validate)
    assert attempts[0]["ok"] is False and attempts[1]["ok"] is True


# -- 2. Hinglish script quality ----------------------------------------------------------------------------------

def _both(h, i, fn):
    for k in ("narration", "narration_tts"):
        h["scenes"][i][k] = fn(h["scenes"][i][k])


def test_fixture_passes_and_each_rule_catches_its_problem():
    req, good = request(), fixture_hindi()
    assert script_qc.validate(req, good) == []
    cases = {
        "missing": lambda h: h["scenes"].pop(),
        "plain_english": lambda h: [h["scenes"][i].update(narration=s["narration"]) for i, s in enumerate(req["scenes"])],
        "devanagari_subtitle": lambda h: h["scenes"][1].update(narration="लीड्स में तो पहले ऐसा नहीं था।"),
        "devanagari_headline": lambda h: h["scenes"][1].update(headline="ऐसा नहीं था", highlight="नहीं"),
        "devanagari_label": lambda h: h.update(category="कला और इतिहास"),
        "formal": lambda h: _both(h, 1, lambda t: t + " mahatvapurn"),
        "tts_roman": lambda h: [sc.update(narration_tts=sc["narration"]) for sc in h["scenes"]],
        "tts_mismatch": lambda h: h["scenes"][2].update(narration_tts=h["scenes"][2]["narration_tts"].replace("24 ", "25 ")),
        "highlight": lambda h: h["scenes"][1].update(highlight="gayab"),
        "number_missing": lambda h: _both(h, 2, lambda t: t.replace("24 ", "")),
        "number_invented": lambda h: _both(h, 8, lambda t: t + " 500 saal se."),
        "name": lambda h: h.update(terms=[t for t in h["terms"] if "Shonibare" not in t["en"]]) or
                          _both(h, 2, lambda t: t.replace("Yinka Shonibare", "ek artist")),
        "too_long": lambda h: _both(h, 0, lambda t: t + " " + "bahut " * 80),
        "spoken_form": lambda h: h["terms"][0].update(spoken="Leeds"),
    }
    expect = {"missing": "scenes must be", "plain_english": "reads as English",
              "devanagari_subtitle": "no Devanagari", "devanagari_headline": "no Devanagari",
              "devanagari_label": "no Devanagari", "formal": "too formal", "tts_roman": "Hindi voice mispronounces",
              "tts_mismatch": "same numbers", "highlight": "must appear inside", "number_missing": "numbers missing",
              "number_invented": "not in the English", "name": "not preserved", "too_long": "outside",
              "spoken_form": "spoken form"}
    for name, mutate in cases.items():
        h = copy.deepcopy(good)
        mutate(h)
        problems = script_qc.validate(req, h)
        assert any(expect[name] in p for p in problems), (name, problems)


def test_english_vocabulary_and_technical_terms_are_never_rejected():
    """Hinglish keeps English terms: a scene that is mostly English words (with everyday Hindi glue) is natural."""
    req, h = request(), fixture_hindi()
    h["scenes"][3].update(narration="Ye artwork David Oluwale ki life aur legacy ko honour karta hai, ek British-Nigerian man.",
                          narration_tts="ये artwork David Oluwale की life और legacy को honour करता है, एक British-Nigerian man.")
    h["scenes"][2]["headline"] = "THE BIG REVEAL"
    h["scenes"][2]["highlight"] = "REVEAL"
    assert script_qc.validate(req, h) == []


def test_roman_hinglish_headlines_and_labels_are_accepted():
    req, h = request(), fixture_hindi()
    h["scenes"][0].update(headline="Aapko pata tha?", highlight="pata")
    h["scenes"][9].update(headline="Leeds ka Secret", highlight="Secret")
    assert script_qc.validate(req, h) == []


def test_length_rule_accepts_what_the_fastest_natural_voice_rate_fits():
    """4.02 acceptance finding: a 128-word adaptation speaks in ~56.6 s at +18 % and must not be rejected; 134 words
    cannot fit even at +18 % and still are."""
    req = request()

    def words(n):
        h = fixture_hindi()
        base = sum(len(sc["narration_tts"].split()) for sc in h["scenes"])
        _both(h, 8, lambda t: t + " " + " ".join(["hai"] * (n - base)) if "Ye" in t or "ये" in t else t)
        h["scenes"][8]["narration_tts"] = h["scenes"][8]["narration_tts"].replace(" hai", " है")
        return h
    assert not any("estimated spoken length" in p for p in script_qc.validate(req, words(128)))
    assert any("estimated spoken length" in p for p in script_qc.validate(req, words(134)))


def test_roman_numerals_may_become_digits_but_lone_i_never_counts():
    assert script_qc.roman_numbers("receptors of class II and phase IV; I think") == {"2", "4"}
    assert "II" not in script_qc.proper_names("VIP binds class II receptors in Leeds.")      # 4.02 acceptance finding
    req = request()
    req["scenes"][0]["narration"] += " It binds class II receptors."
    h = fixture_hindi()
    h["scenes"][0]["narration"] += " Ye class 2 receptors se judta hai."
    h["scenes"][0]["narration_tts"] += " ये class 2 receptors से जुड़ता है।"
    assert not any("not in the English" in p for p in script_qc.validate(req, h))


def test_length_feedback_names_the_scenes_over_their_ceiling():
    req = request()
    assert all(s["max_words"] >= s["target_words"] for s in req["scenes"])
    h = fixture_hindi()
    h["scenes"][8]["narration"] += " " + " ".join(["hai"] * 40)
    h["scenes"][8]["narration_tts"] += " " + " ".join(["है"] * 40)
    msg = [p for p in script_qc.validate(req, h) if "estimated spoken length" in p][0]
    assert "shorten: scene_09 has" in msg                                  # the worst scene first
    assert "NEVER exceed max_words" in translator.build_prompt(req)


def test_devanagari_digits_count_as_the_same_number():
    assert script_qc.numbers_in("साल १९६९ में") == {"1969"}
    assert script_qc.numbers_in("1,880 लोग") == {"1880"}


# -- 3. voice ----------------------------------------------------------------------------------------------------

def _tone_edge(ffmpeg, seconds_per_word=0.42, fail_voices=()):
    calls = []

    def edge(text, v, rate, out_mp3):
        calls.append((v, rate, text))
        if v in fail_voices:
            raise ConnectionError("offline")
        speed = 1.0 + int(rate.strip("%+")) / 100.0
        dur = max(0.5, len(text.split()) * seconds_per_word / speed)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur:.2f}",
                        "-af", "volume=0.5", str(out_mp3)], check=True, capture_output=True)
    return edge, calls


def test_voice_generates_per_scene_with_pronunciation(tmp_path):
    ffmpeg, _ = _ffmpeg()
    edge, calls = _tone_edge(ffmpeg)
    h = fixture_hindi()
    h["terms"].append({"en": "Hibiscus Rising", "spoken": "हिबिस्कस राइज़िंग"})
    res = voice.synthesize(h["scenes"], h["terms"], tmp_path, ffmpeg=ffmpeg, edge=edge, sapi_list=lambda: [])
    assert res["provider"] == "edge-tts" and res["voice"] == "hi-IN-SwaraNeural" and len(res["timings"]) == 10
    texts = [c[2] for c in calls]
    assert texts[0].startswith("आपको लगता होगा English cities")       # the voice form (code-mixed), never the Roman one
    assert any("हिबिस्कस राइज़िंग" in t for t in texts) and not any("Hibiscus Rising" in t for t in texts)
    assert any("यिंका शोनीबारे" in t for t in texts)                      # name pronunciation applied
    assert Path(res["voice_path"]).is_file() and res["total_sec"] <= voice.MAX_TOTAL_SEC
    assert res["timings"][1]["start"] == pytest.approx(res["timings"][0]["duration"] + voice.SCENE_GAP_SEC, abs=0.01)


def test_voice_falls_back_to_the_second_voice(tmp_path):
    ffmpeg, _ = _ffmpeg()
    edge, calls = _tone_edge(ffmpeg, fail_voices=("hi-IN-SwaraNeural",))
    res = voice.synthesize(fixture_hindi()["scenes"], [], tmp_path, ffmpeg=ffmpeg, edge=edge, sapi_list=lambda: [])
    assert res["voice"] == "hi-IN-MadhurNeural" and any("SwaraNeural" in e for e in res["errors"])


def test_no_voice_available_is_an_infrastructure_wait(tmp_path):
    ffmpeg, _ = _ffmpeg()
    edge, _calls = _tone_edge(ffmpeg, fail_voices=voice.DEFAULT_VOICES)
    with pytest.raises(voice.VoiceUnavailable):
        voice.synthesize(fixture_hindi()["scenes"], [], tmp_path, ffmpeg=ffmpeg, edge=edge, sapi_list=lambda: [])


def test_voice_uses_a_faster_rate_when_too_long(tmp_path):
    ffmpeg, _ = _ffmpeg()
    edge, calls = _tone_edge(ffmpeg, seconds_per_word=0.55)
    res = voice.synthesize(fixture_hindi()["scenes"], [], tmp_path, ffmpeg=ffmpeg, edge=edge, sapi_list=lambda: [])
    assert res["rate"] != voice.RATES[0] and any("too long" in e for e in res["errors"])


def test_voice_never_returns_an_over_length_voice(tmp_path):
    ffmpeg, _ = _ffmpeg()
    edge, calls = _tone_edge(ffmpeg, seconds_per_word=1.2)          # too long even at +25 %
    with pytest.raises(voice.ScriptTooLong) as e:
        voice.synthesize(fixture_hindi()["scenes"], [], tmp_path, ffmpeg=ffmpeg, edge=edge, sapi_list=lambda: [])
    assert e.value.seconds > voice.MAX_TOTAL_SEC and e.value.rate == voice.RATES[-1]
    assert not (tmp_path / "voice_hi.json").exists()


def test_silent_clip_is_rejected(tmp_path):
    ffmpeg, _ = _ffmpeg()

    def silent(text, v, rate, out_mp3):
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2",
                        str(out_mp3)], check=True, capture_output=True)
    with pytest.raises(voice.VoiceUnavailable):
        voice.synthesize(fixture_hindi()["scenes"][:2], [], tmp_path, ffmpeg=ffmpeg, edge=silent, sapi_list=lambda: [])


# -- 4. visual and subtitle adaptation ----------------------------------------------------------------------------

def _timings(plan, factor=1.05):
    t, out = 0.0, []
    for s in plan["scenes"]:
        d = (s["end_sec"] - s["start_sec"]) * factor
        out.append({"scene_id": s["scene_id"], "start": round(t, 3), "duration": round(d, 3)})
        t += d + 0.12
    return out


def test_plan_shows_only_english_or_roman_hinglish_and_retimes():
    en = english_plan()
    hi = hrender.build_hindi_plan(en, fixture_hindi(), _timings(en))
    assert hi["language"] == "hi-Latn" and hi["category"] == "Art and History"
    assert hi["topic"] == "Hibiscus Rising: Leeds ka naya monument"
    for name, text in hqc.visible_texts(hi):
        assert not script_qc.DEVANAGARI.search(str(text or "")), (name, text)
    for s in hi["scenes"]:
        assert not script_qc.DEVANAGARI.search(s["narration"])           # subtitles: Roman Hinglish
        assert script_qc.DEVANAGARI.search(s["narration_tts"])           # voice form keeps Hindi words in Devanagari
        assert s["highlight"] in s["headline"] and "stage_label" not in s
        assert all(s["start_sec"] <= c["t"] <= s["end_sec"] for c in s["sfx"])   # SFX moved with the scene
    assert hi["scenes"][1]["start_sec"] == pytest.approx(_timings(en)[1]["start"])
    assert en["scenes"][0]["headline"] == "Hibiscus Rising"                     # English plan untouched


def test_package_uses_the_unchanged_english_engine_and_english_stage_labels(tmp_path):
    from backend.app.rendering.pop_info.film_builder import ENGINE_JS
    english_js = Path(ENGINE_JS).read_text(encoding="utf-8")
    src = tmp_path / "en_film"
    (src / "assets").mkdir(parents=True)
    (src / "assets" / "photo_x.jpg").write_bytes(b"x")
    en = english_plan()
    film = hrender.package_film(src, tmp_path / "hi_film", hrender.build_hindi_plan(en, fixture_hindi(), _timings(en)))
    js = (film / "engine.js").read_text(encoding="utf-8")
    assert js == english_js                         # same typography, fonts and English stage pills ("the setup", ...)
    assert "ESCALATION: 'the twist'" in js and "Nirmala" not in js
    assert Path(ENGINE_JS).read_text(encoding="utf-8") == english_js           # the English engine is unchanged
    assert (film / "assets" / "photo_x.jpg").is_file()                         # licensed assets reused
    page = (film / "index.html").read_text(encoding="utf-8")
    assert 'lang="hi-Latn"' in page and "Leeds ka naya monument" in page
    plan_text = (film / "plan.json").read_text(encoding="utf-8")
    shown = json.loads(plan_text)
    assert not any(script_qc.DEVANAGARI.search(str(t or "")) for _n, t in hqc.visible_texts(shown))


def test_roman_hinglish_subtitles_render_without_clipping(tmp_path):
    ffmpeg, _ = _ffmpeg()
    en = english_plan()
    plan = hrender.build_hindi_plan(en, fixture_hindi(), _timings(en))
    ass = hrender.write_ass(plan, tmp_path / "subs.ass")
    text = Path(ass["path"]).read_text(encoding="utf-8-sig")
    assert ass["events"] >= 10 and ass["longest_line_chars"] <= 36
    assert "Leeds mein to pehle aisa nahi tha." in text.replace(chr(92) + "N", " ") and not script_qc.DEVANAGARI.search(text)
    assert f"Style: HI,{hrender.SUBTITLE_FONT}," in text
    check = hrender.render_check_ass(ffmpeg, Path(ass["path"]), plan["scenes"][2]["start_sec"] + 0.5, tmp_path / "f.png")
    assert check["ok"] is True and check["inked_pixels"] > 1500 and check["clipped"] is False


def test_subtitle_wrap_keeps_words_whole():
    t = "Ye Leeds ka pehla public artwork hai jo cultural diversity dikhata hai."
    lines = hrender.wrap_text(t, 24)
    assert all(len(l) <= 24 for l in lines) and " ".join(lines).split() == t.split()


# -- 5/6. QC --------------------------------------------------------------------------------------------------------

def _probe(v_dur=52.0, a_dur=52.0, codec="h264", size=(1080, 1920), fps="30/1", fmt_dur=52.0):
    return lambda ffprobe, mp4: {"format": {"duration": str(fmt_dur)}, "streams": [
        {"codec_type": "video", "codec_name": codec, "width": size[0], "height": size[1], "r_frame_rate": fps, "duration": str(v_dur)},
        {"codec_type": "audio", "codec_name": "aac", "duration": str(a_dur), "sample_rate": "48000", "channels": 1}]}


def _qc(plan, **kw):
    base = dict(mp4="x.mp4", plan=plan, ffmpeg="ffmpeg", ffprobe="ffprobe", probe_fn=_probe(), decode_fn=lambda f, m: "",
                volume_fn=lambda f, m: -20.0, subtitle_check={"ok": True}, voice_total_sec=float(plan["duration_sec"]))
    base.update(kw)
    return hqc.evaluate(**base)


def test_qc_pass_and_each_failure_is_classified():
    en = english_plan()
    plan = hrender.build_hindi_plan(en, fixture_hindi(), _timings(en, 0.9))
    plan["duration_sec"] = 52.0
    assert _qc(plan)["passed"] is True
    assert _qc(plan, probe_fn=_probe(a_dur=51.0))["classification"] == "TECHNICAL_FAILURE"        # A/V drift
    assert _qc(plan, probe_fn=_probe(fmt_dur=61.0))["classification"] == "CREATIVE_REJECTION"     # too long
    assert _qc(plan, probe_fn=_probe(size=(1920, 1080)))["classification"] == "TECHNICAL_FAILURE"
    assert _qc(plan, volume_fn=lambda f, m: -70.0)["classification"] == "TECHNICAL_FAILURE"       # voice missing
    assert _qc(plan, decode_fn=lambda f, m: "corrupt frame")["classification"] == "TECHNICAL_FAILURE"
    assert _qc(plan, subtitle_check={"ok": False})["classification"] == "TECHNICAL_FAILURE"
    assert _qc(plan, typography_issues=[{"t": 3.0, "message": "text outside safe area"}])["classification"] == "CREATIVE_REJECTION"
    eng = copy.deepcopy(plan)
    eng["scenes"][0]["headline"] = "THE BIG DISCOVERY"                         # English on screen is the default
    assert _qc(eng)["passed"] is True
    for where in ("headline", "highlight", "label", "category", "stage", "number", "subtitles"):
        bad = copy.deepcopy(plan)
        kw = {}
        if where == "headline":
            bad["scenes"][0]["headline"] = "हिबिस्कस राइज़िंग"
        elif where == "highlight":
            bad["scenes"][0]["highlight"] = "राइज़िंग"
        elif where == "label":
            bad["scenes"][0]["labels"] = ["कला"]
        elif where == "category":
            bad["category"] = "कला और इतिहास"
        elif where == "stage":
            bad["scenes"][1]["stage_label"] = "शुरुआत"
        elif where == "number":
            bad["scenes"][0]["numbers"] = [{"text": "28", "unit": "अमीनो"}]
        else:
            kw["subtitles_text"] = "Dialogue: 0,0:00:01.00,0:00:02.00,HI,,0,0,0,,लीड्स में"
        res = _qc(bad, **kw)
        assert res["classification"] == "CREATIVE_REJECTION" and any("Devanagari in video-visible text" in c
                                                                      for c in res["creative"]), where
    assert _qc(plan, script_problems=["numbers missing in the Hinglish: 1969"])["classification"] == "CREATIVE_REJECTION"


# -- 7. pipeline: mapping, duplicates, retries, channel isolation, publishing safeguards --------------------------------

@pytest.fixture
def world(tmp_path):
    from backend.app.channels.registry import ChannelRegistry
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE persistent_jobs (id TEXT, status TEXT, current_stage TEXT, topic_title TEXT,
                         youtube_video_id TEXT, created_at TEXT);
                       CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, upload_status TEXT, upload_timestamp TEXT);""")
    c.execute("INSERT INTO persistent_jobs VALUES (?,?,?,?,?,datetime('now'))", (JOB, "COMPLETED", "COMPLETED", "Hibiscus Rising", "vid1"))
    c.commit()
    c.close()

    def no_oauth(*a, **k):
        raise AssertionError("localization must never build an OAuth manager or read a token")
    reg = ChannelRegistry(str(db), backend_dir=tmp_path / "backend", oauth_factory=no_oauth)
    (tmp_path / "backend").mkdir()
    reg.ensure()
    media = tmp_path / "media"
    src = media / "productions" / JOB
    (src / "scripts").mkdir(parents=True)
    (src / "pop_info_film").mkdir()
    (src / "scripts" / f"script_{JOB}.json").write_text(json.dumps({"full_script": "x"}), encoding="utf-8")
    (src / "pop_info_film" / "plan.json").write_text(json.dumps(english_plan()), encoding="utf-8")
    calls = {"translate": 0, "voice": 0, "render": 0, "qc": 0}

    def tr(loc, wd, arts):
        calls["translate"] += 1
        p = wd / "script_hi.json"
        p.write_text(json.dumps({"request": request(), "hindi": fixture_hindi()}, ensure_ascii=False), encoding="utf-8")
        return {"script_hi": str(p)}

    def vo(loc, wd, arts):
        calls["voice"] += 1
        return {"voice_meta": "v.json"}

    def rd(loc, wd, arts):
        calls["render"] += 1
        mp4 = wd / f"hindi_{JOB}.mp4"
        mp4.write_bytes(b"mp4")
        return {"final_mp4": str(mp4), "plan_hi": "p.json", "film_dir": str(wd)}

    qc_result = {"passed": True, "classification": "PASSED", "creative": [], "technical": []}

    def q(loc, wd, arts):
        calls["qc"] += 1
        return dict(qc_result)

    loc = HindiLocalizer(reg, media_dir=media, translate_fn=tr, voice_fn=vo, render_fn=rd, qc_fn=q,
                         ffmpeg="ffmpeg", ffprobe="ffprobe", workspace_root=tmp_path)
    return SimpleNamespace(reg=reg, loc=loc, db=db, media=media, calls=calls, qc_result=qc_result)


def test_mapping_is_unique_and_keeps_the_english_identity(world):
    a = world.loc.plan(JOB)
    b = world.loc.plan(JOB)
    assert a["duplicate"] is False and b["duplicate"] is True
    row = a["localization"]
    assert row["source_channel_key"] == "en" and row["target_channel_key"] == "hi" and row["source_youtube_video_id"] == "vid1"


def test_full_run_passes_qc_and_never_publishes(world):
    lid = world.loc.plan(JOB)["localization"]["id"]
    for step in ("translate", "voice", "render", "qc"):
        world.loc.run_step(lid, step)
    row = world.loc.get(lid)
    assert row["status"] == "QC_PASSED" and row["artifacts"]["final_mp4"].endswith(f"hindi_{JOB}.mp4")
    elig = world.reg.publishing_eligibility("hi", row)
    assert elig["eligible"] is False and elig["automatic_publishing"] is False
    n = sqlite3.connect(world.db).execute("SELECT COUNT(*) FROM publishing_ledger").fetchone()[0]
    assert n == 0                                                           # nothing entered the publishing queue
    assert Path(row["artifacts"]["script_hi"]).parent != world.media / "productions" / JOB   # stored separately


def test_passed_localization_can_be_rerendered_only_explicitly(world):
    lid = world.loc.plan(JOB)["localization"]["id"]
    for step in ("translate", "voice", "render", "qc"):
        world.loc.run_step(lid, step)
    with pytest.raises(ValueError, match="redo a step explicitly"):
        world.loc.reopen(lid, actor="op")
    row = world.loc.reopen(lid, actor="op", from_step="render")
    assert row["status"] == "PLANNED" and set(row["artifacts"]) == {"script_hi", "voice_meta"}
    wd = world.loc.work_dir(row)
    assert any((p / f"hindi_{JOB}.mp4").is_file() for p in wd.iterdir() if p.name.startswith("attempt_"))
    for step in ("render", "qc"):
        world.loc.run_step(lid, step)
    assert world.loc.get(lid)["status"] == "QC_PASSED" and world.calls["translate"] == 1


def test_retry_reuses_completed_steps(world):
    lid = world.loc.plan(JOB)["localization"]["id"]
    world.loc.run_step(lid, "translate")
    original = world.loc.voice_fn

    def flaky(loc, wd, arts):
        raise InfrastructureWait("voice provider offline")
    world.loc.voice_fn = flaky
    with pytest.raises(InfrastructureWait):
        world.loc.run_step(lid, "voice")
    row = world.loc.get(lid)
    assert row["status"] == "WAITING" and row["attempts"] == 1 and row["artifacts"]["script_hi"]
    world.loc.voice_fn = original
    world.loc.reopen(lid, actor="op")
    for step in ("voice", "render", "qc"):
        world.loc.run_step(lid, step)
    assert world.calls["translate"] == 1 and world.loc.get(lid)["status"] == "QC_PASSED"


def test_creative_rejection_is_final_until_retranslated_and_archives_files(world):
    world.qc_result.update(passed=False, classification="CREATIVE_REJECTION", creative=["duration 61s"])
    lid = world.loc.plan(JOB)["localization"]["id"]
    for step in ("translate", "voice", "render", "qc"):
        world.loc.run_step(lid, step)
    assert world.loc.get(lid)["status"] == "QC_REJECTED"
    assert world.loc.run_step(lid, "render")["skipped"] is True            # terminal: nothing runs again
    with pytest.raises(ValueError, match="retry from 'translate'"):
        world.loc.reopen(lid, actor="op")
    wd = world.loc.work_dir(world.loc.get(lid))
    world.loc.reopen(lid, actor="op", from_step="translate")
    archived = [p for p in wd.iterdir() if p.name.startswith("attempt_")]
    assert archived and (archived[0] / "script_hi.json").is_file()        # archived, never deleted
    assert world.loc.get(lid)["status"] == "PLANNED" and world.loc.get(lid)["artifacts"] == {}


def _length_world(world, monkeypatch, too_long_times):
    """The default voice step with a fake synthesizer (too long N times) and a fake re-translation."""
    from backend.app.localization import pipeline as pl
    calls = {"synth": 0, "translate": []}

    def synth(scenes, terms, out_dir, ffmpeg):
        calls["synth"] += 1
        if calls["synth"] <= too_long_times:
            raise voice.ScriptTooLong(61.0, "hi-IN-SwaraNeural", "+25%")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "voice_hi.json").write_text("{}", encoding="utf-8")

    def retranslate(self, loc, out, *, scale=1.0, feedback=(), length_corrections=0):
        calls["translate"].append({"scale": scale, "feedback": list(feedback), "n": length_corrections})
        out.write_text(json.dumps({"request": request(), "hindi": fixture_hindi(), "scale": scale,
                                   "length_corrections": length_corrections}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(voice, "synthesize", synth)
    monkeypatch.setattr(pl.HindiLocalizer, "_translate_to", retranslate)
    world.loc.voice_fn = world.loc._default_voice
    return calls


def test_measured_over_length_voice_is_shortened_automatically(world, monkeypatch):
    calls = _length_world(world, monkeypatch, too_long_times=1)
    lid = world.loc.plan(JOB)["localization"]["id"]
    world.loc.run_step(lid, "translate")
    assert world.loc.run_step(lid, "voice")["status"] == "IN_PROGRESS"
    t = calls["translate"][0]
    assert t["n"] == 1 and t["scale"] < 1 and "MEASURED" in t["feedback"][0] and "61.0 s" in t["feedback"][0]
    wd = world.loc.work_dir(world.loc.get(lid))
    arch = [p for p in wd.iterdir() if p.name.startswith("length_attempt_1_")]
    assert arch and (arch[0] / "script_hi.json").is_file()            # the long attempt is archived, not deleted
    assert world.calls["render"] == 0


def test_length_corrections_are_bounded_then_creative_rejection(world, monkeypatch):
    calls = _length_world(world, monkeypatch, too_long_times=99)
    lid = world.loc.plan(JOB)["localization"]["id"]
    world.loc.run_step(lid, "translate")
    res = world.loc.run_step(lid, "voice")
    row = world.loc.get(lid)
    assert row["status"] == "QC_REJECTED" and "automatic shortening" in (row["error"] or "")
    from backend.app.localization.pipeline import MAX_LENGTH_CORRECTIONS
    assert len(calls["translate"]) == MAX_LENGTH_CORRECTIONS and world.calls["render"] == 0


def test_source_must_have_its_english_artifacts(world, tmp_path):
    c = sqlite3.connect(world.db)
    c.execute("INSERT INTO persistent_jobs VALUES ('cycle_1_20261127_0000','COMPLETED','COMPLETED','t',NULL,datetime('now'))")
    c.commit()
    c.close()
    row = world.loc.plan("cycle_1_20261127_0000")["localization"]
    assert row["status"] == "FAILED" and row["error_kind"] == "SOURCE_MISSING"


def test_localization_package_has_no_publishing_or_credential_path():
    pkg = Path(__file__).resolve().parents[1] / "app" / "localization"
    text = "\n".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py"))
    for forbidden in ("youtube_publishing_engine", "videos().insert", "publishing_ledger", "token.json", "oauth_manager",
                      "YouTubeOAuthManager", "upload"):
        assert forbidden not in text, forbidden


# -- 8. coordinator: localization queue and the shared render slot -----------------------------------------------------

def test_localization_runs_through_the_coordinator_in_shorts_gaps(world, tmp_path):
    import asyncio
    from datetime import timedelta
    from backend.app.scheduler import production_coordinator as pc
    from test_production_coordinator_337 import T0, World, make
    ran = []

    def ex(ctx):
        ran.append((ctx.step["name"], ctx.slot_token is not None))
        return {}
    coord, w, clock = make(tmp_path, executors={KIND: ex}, db_name="coord.db")
    res = enqueue(JOB, operator="op", coordinator=coord, localizer=world.loc)
    assert res["queued"] is True
    job = coord.get_job(res["coordinator_job"])
    assert job["queue"] == pc.LOCALIZATION and job["checkpoint"]["localization_id"] == res["localization"]["id"]
    assert [s["name"] for s in job["steps"]] == [s["name"] for s in COORDINATOR_STEPS]
    again = enqueue(JOB, operator="op", coordinator=coord, localizer=world.loc)
    assert again["queued"] is False                                         # no duplicate coordinator job

    def drain():
        for _ in range(6):
            asyncio.run(coord.tick())
            import time as _t
            for _i in range(200):
                with coord._lock:
                    if not any(t.is_alive() for t in coord._running.values()):
                        break
                _t.sleep(0.01)
    w.shorts["next_start_utc"] = pc._ts(T0 + timedelta(minutes=10))          # render (12 min) does not fit
    drain()
    assert ("render", True) not in ran and [r[0] for r in ran] == ["translate", "voice"]
    d = [x for x in coord.plan()["decisions"] if x["job_id"] == job["id"]][0]
    assert "next Shorts cycle" in d["reason"]
    w.shorts["next_start_utc"] = pc._ts(T0 + timedelta(minutes=45))          # a gap: render runs holding the slot
    drain()
    assert ("render", True) in ran and coord.get_job(job["id"])["state"] == pc.COMPLETED


def test_localization_queue_respects_its_own_switch_and_pause(tmp_path):
    from backend.app.scheduler import production_coordinator as pc
    from test_production_coordinator_337 import make
    coord, w, clock = make(tmp_path, executors={KIND: lambda ctx: {}}, db_name="c2.db")
    queues = coord.status()["queues"]
    assert queues[pc.LOCALIZATION]["executors"] == [KIND] and queues[pc.LONG_FORM]["executors"] == []
    coord.enqueue(kind=KIND, title="t", steps=COORDINATOR_STEPS, requested_by="op", queue=pc.LOCALIZATION)
    assert coord.config()["long_form_enabled"] is False and coord.config()["localization_enabled"] is True
    coord.set_queue_paused(pc.LOCALIZATION, True, "op", "busy")
    assert "paused" in coord.plan()["decisions"][0]["reason"]
    coord.set_queue_paused(pc.LOCALIZATION, False, "op", "ok")
    coord.set_config({"localization_enabled": False}, "op", "off")
    assert "localization_enabled" in coord.plan()["decisions"][0]["reason"]


def test_production_executor_registered_lazily():
    from backend.app.scheduler import production_coordinator as pc
    c = pc.ProductionCoordinator.__new__(pc.ProductionCoordinator)
    saved = dict(pc._EXECUTORS)
    try:
        c.executors = pc._EXECUTORS
        c._register_builtin_executors()
        assert pc._EXECUTORS.get(KIND) is not None
    finally:
        pc._EXECUTORS.clear()
        pc._EXECUTORS.update(saved)


# -- 9. API, dashboard, attention ------------------------------------------------------------------------------------

def test_api_routes(monkeypatch, world):
    from fastapi.testclient import TestClient
    from backend.app import main
    import backend.app.localization.pipeline as pl
    monkeypatch.setattr(pl, "HindiLocalizer", lambda *a, **k: world.loc)
    monkeypatch.setattr(pl, "overview", lambda: {"localizations": []})
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert local.get("/api/localizations").json() == {"localizations": []}
    assert local.post("/api/localizations", json={"operator": "op", "source_job_id": JOB, "confirm": True}).status_code == 403
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/localizations").status_code in (400, 403)
    lid = world.loc.plan(JOB)["localization"]["id"]
    assert local.get(f"/api/localizations/{lid}/video").status_code == 404     # no video yet
    html = (Path(main.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="locCard"' in html and "function refreshLocalizations()" in html


def test_attention_lists_genuine_hindi_blockers_once(tmp_path):
    from backend.app.services import operator_attention as oa
    from test_autonomy_control_plane_332b import make_cp
    cp = make_cp(tmp_path, startup=False, policy=True)
    locs = {"localizations": [
        {"id": 1, "source_job_id": "a", "status": "QC_PASSED", "publishing": {"reasons": ["channel not verified"]}},
        {"id": 2, "source_job_id": "b", "status": "QC_PASSED", "publishing": {"reasons": ["channel not verified"]}},
        {"id": 3, "source_job_id": "c", "status": "QC_REJECTED", "qc": {"creative": ["duration 61s"]}},
        {"id": 4, "source_job_id": "d", "status": "WAITING", "error": "provider unavailable"},
        {"id": 5, "source_job_id": "e", "status": "FAILED", "error": "render error"}]}
    a = oa.attention(cp=cp, providers=lambda: {}, creative=lambda: {}, topic_supply=lambda: {}, localizations=lambda: locs)
    titles = [n["title"] for n in a["needs_you"]]
    assert sum("passed QC" in t for t in titles) == 1 and any("2 Hindi video" in t for t in titles)
    assert any("rejected c" in t for t in titles) and any("failed: e" in t for t in titles)
    assert not any(": d" in t or " d" == t[-2:] for t in titles)            # WAITING resolves itself


# -- 10. English backward compatibility ---------------------------------------------------------------------------

def test_english_production_paths_do_not_depend_on_localization():
    import inspect
    from backend.app.scheduler import scheduler
    from backend.app.orchestration import crazzydocs_master_orchestrator as mo
    from backend.app.rendering.pop_info import film_builder, pipeline as pop
    for mod in (scheduler, mo, film_builder, pop):
        assert "backend.app.localization" not in inspect.getsource(mod)


# -- remediation closure: bounded autonomous Hinglish localization ---------------------------------------------------

def test_auto_localization_enqueues_the_newest_completed_short_once(world, tmp_path):
    from backend.app.localization.pipeline import AUTO_ACTOR, auto_enqueue_next
    from backend.app.scheduler import production_coordinator as pc
    from test_production_coordinator_337 import make
    c = sqlite3.connect(world.db)
    c.execute("INSERT INTO persistent_jobs VALUES ('test_x','COMPLETED','COMPLETED','t',NULL,datetime('now'))")
    c.commit()
    c.close()
    coord, w, clock = make(tmp_path, executors={KIND: lambda ctx: {}}, db_name="auto.db")
    res = auto_enqueue_next(coordinator=coord, localizer=world.loc)
    assert res["queued"] is True and res["source_job_id"] == JOB          # test_ jobs and jobs without files skipped
    job = coord.get_job(res["coordinator_job"])
    assert job["queue"] == pc.LOCALIZATION and job["requested_by"] == AUTO_ACTOR
    again = auto_enqueue_next(coordinator=coord, localizer=world.loc)
    assert again["queued"] is False and "already queued" in again["reason"]   # one at a time
    coord.cancel_job(job["id"], "op", "test") if hasattr(coord, "cancel_job") else None


def test_auto_localization_respects_switch_cap_and_existing_mappings(world, tmp_path):
    from backend.app.localization.pipeline import auto_enqueue_next
    from test_production_coordinator_337 import make
    coord, w, clock = make(tmp_path, executors={KIND: lambda ctx: {}}, db_name="auto2.db")
    coord.set_config({"localization_auto_daily_max": 0}, "op", "off")
    assert "off" in auto_enqueue_next(coordinator=coord, localizer=world.loc)["reason"]
    coord.set_config({"localization_auto_daily_max": 6}, "op", "on")
    world.loc.plan(JOB)                                                      # already localized (any status)
    assert "no completed English Short" in auto_enqueue_next(coordinator=coord, localizer=world.loc)["reason"]


def test_coordinator_tick_only_auto_localizes_with_the_production_executors(tmp_path):
    from backend.app.scheduler import production_coordinator as pc
    from test_production_coordinator_337 import make
    coord, w, clock = make(tmp_path, executors={KIND: lambda ctx: {}}, db_name="auto3.db")
    called = []
    import backend.app.localization.pipeline as pl
    orig = pl.auto_enqueue_next
    pl.auto_enqueue_next = lambda **k: called.append(1) or {"queued": False}
    try:
        coord._auto_localize()                                               # injected executors (tests): never
        assert called == []
    finally:
        pl.auto_enqueue_next = orig
    assert pc.DEFAULT_CONFIG["localization_auto_daily_max"] == 6
