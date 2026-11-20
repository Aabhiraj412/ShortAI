"""Stage 3.32 (roadmap: creative intelligence): evaluation, records keyed for 3.35, experiments, runner.

Temporary databases and synthetic media only.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.app.content import grounded_script_writer as gsw
from backend.app.creative_intel import evaluation as ev
from backend.app.creative_intel import runner
from backend.app.creative_intel.store import CreativeStore, CreativeStoreError, experiment_arm
from test_render_geometry_332h import FFMPEG, needs_ffmpeg

FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe")) if FFMPEG else None
JOB = "cycle_1_20261121_0000"

SCRIPT = {"hook_type": "contradiction", "angle": "a", "script_source": "llm:test", "category": "Geography",
          "sections": [{"stage": "HOOK", "text": "Most people assume volcanoes are slow."}],
          "quality": {"overall": 80, "criteria": {k: {"score": s, "reason": f"{k} reason"} for k, s in (
              ("hook", 6.4), ("curiosity", 4.0), ("narrative_progression", 10), ("originality", 9),
              ("spoken_naturalness", 8), ("pacing", 9), ("payoff", 7))}}}
PLAN = {"scenes": [
    {"template": "title", "transition_in": "cut", "animation_direction": "rise", "composition": {"a": 1}, "start_sec": 0, "end_sec": 3},
    {"template": "photo", "transition_in": "slide", "animation_direction": "ken_burns", "composition": {"a": 2}, "start_sec": 3, "end_sec": 7},
    {"template": "kinetic", "transition_in": "wipe", "animation_direction": "pop", "composition": {"a": 3}, "start_sec": 7, "end_sec": 10},
    {"template": "kinetic", "transition_in": "cut", "animation_direction": "static", "composition": {"a": 3}, "start_sec": 10, "end_sec": 12}]}
STORYBOARD = {"beats": [{"relevance_score": 0.9, "visual_type": "map"}, {"relevance_score": 0.7, "visual_type": "photo"}]}
ASS = """[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Most people assume
Dialogue: 0,0:00:02.00,0:00:02.50,Default,,0,0,0,,{\\b1}this caption is far too fast to read{\\b0}
"""


def workspace(tmp_path, *, with_video=False) -> Path:
    p = tmp_path / "productions" / JOB
    for sub in ("scripts", "storyboards", "pop_info_film", "manifests", "video"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    (p / "scripts" / f"script_{JOB}.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
    (p / "scripts" / f"subtitles_{JOB}.ass").write_text(ASS, encoding="utf-8")
    (p / "storyboards" / f"storyboard_{JOB}.json").write_text(json.dumps(STORYBOARD), encoding="utf-8")
    (p / "pop_info_film" / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    (p / "manifests" / f"render_manifest_{JOB}.json").write_text(json.dumps(
        {"renderer": "lemo-opuscar", "visual_engine": "pop_info_v1", "script_fingerprint": {"sha256": "abc"}}), encoding="utf-8")
    if with_video:
        make_video(p / "video" / f"showcase_{JOB}.mp4")
    return p


def make_video(path: Path, seconds: int = 6) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s=540x960:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-filter_complex", "[0:v]hue=h=t*60[v];[1:a]volume=0.25[a]", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True, timeout=120)


# -- evaluation ------------------------------------------------------------------------------------------------
def test_script_dims_reuse_existing_quality_and_report_missing():
    d = ev.evaluate_script_dims(SCRIPT)
    assert d["hook"]["score"] == 64.0 and "contradiction" in d["hook"]["evidence"]
    assert d["storytelling"]["score"] == pytest.approx((40 + 90 + 80) / 3, abs=0.1)
    missing = ev.evaluate_script_dims({})
    assert all(v["score"] is None and not v["measured"] for v in missing.values())


def test_scene_dims_prefer_rendered_plan_over_storyboard():
    d = ev.evaluate_scene_dims(PLAN, STORYBOARD)
    assert "3 scene types in 4 scenes" in d["scene_diversity"]["evidence"]
    assert d["visual_relevance"]["score"] == 80.0                    # plan has none: storyboard relevance used
    assert "1/3 designed transitions" not in d["transitions"]["evidence"]
    assert d["transitions"]["evidence"].startswith("2/3 designed transitions")
    assert d["animation"]["evidence"].startswith("3/4 animated scenes")
    only_sb = ev.evaluate_scene_dims(None, STORYBOARD)
    assert only_sb["scene_diversity"]["source"] == "storyboard"
    assert all(v["score"] is None for v in ev.evaluate_scene_dims(None, None).values())


def test_subtitle_readability(tmp_path):
    p = tmp_path / "s.ass"
    p.write_text(ASS, encoding="utf-8")
    d = ev.evaluate_subtitles(p)
    assert "2 captions" in d["evidence"] and "50% above" in d["evidence"] and d["score"] < 80
    assert ev.evaluate_subtitles(None)["score"] is None


@needs_ffmpeg
def test_render_analysis_on_synthetic_video(tmp_path):
    v = tmp_path / "v.mp4"
    make_video(v)
    dims, stats = ev.evaluate_render_dims(v, FFMPEG, FFPROBE)
    assert stats["width"] == 540 and stats["has_audio"] and stats["duration_sec"] == pytest.approx(6.0, abs=0.1)
    assert stats["integrated_lufs"] is not None and stats["luma_mean"] is not None
    assert all(dims[k]["score"] is not None for k in ("composition", "narration_audio", "av_sync", "visual_dynamics"))
    assert dims["av_sync"]["score"] >= 90


@needs_ffmpeg
def test_render_analysis_failure_is_reported_not_raised(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    dims, stats = ev.evaluate_render_dims(bad, FFMPEG, FFPROBE)
    assert all(d["score"] is None and "analysis failed" in d["evidence"] for d in dims.values()) and stats["error"]
    assert ev.evaluate_render_dims(None, FFMPEG, FFPROBE)[0]["av_sync"]["score"] is None


@needs_ffmpeg
def test_evaluate_production_end_to_end(tmp_path):
    p = workspace(tmp_path, with_video=True)
    r = ev.evaluate_production(p, JOB, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    assert set(r["dimensions"]) == set(ev.DIMENSIONS)
    assert r["overall"] is not None and r["plan_score"] is not None and r["render_score"] is not None
    assert r["decisions"]["hook_type"] == "contradiction" and r["decisions"]["renderer"] == "lemo-opuscar"
    assert r["decisions"]["scene_templates"] == ["title", "photo", "kinetic", "kinetic"]
    assert len(r["weakest"]) == 3 and "not an audience prediction" in r["note"]


# -- store, 3.35 association, experiments ----------------------------------------------------------------------
def store(tmp_path) -> CreativeStore:
    db = tmp_path / "studio.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, youtube_video_id TEXT, video_path TEXT)")
    conn.execute("INSERT INTO publishing_ledger (youtube_video_id, video_path) VALUES (?, ?)",
                 ("VID123", rf"media\productions\{JOB}\video\showcase_{JOB}.mp4"))
    conn.execute("INSERT INTO publishing_ledger (youtube_video_id, video_path) VALUES (?, ?)",
                 ("OTHER", r"media\productions\cycle_1_20261121_00001\video\x.mp4"))
    conn.commit()
    conn.close()
    return CreativeStore(str(db))


def test_reads_create_no_tables(tmp_path):
    s = store(tmp_path)
    assert s.list_records() == [] and s.list_experiments() == [] and s.summary() == {"records": 0}
    assert s.evaluated_job_ids() == set() and s.link_video_ids() == 0
    names = {r[0] for r in sqlite3.connect(s.db_path).execute("SELECT name FROM sqlite_master")}
    assert "creative_records" not in names


def test_save_and_link_to_youtube_video(tmp_path):
    s = store(tmp_path)
    p = workspace(tmp_path)
    r = ev.evaluate_production(p, JOB, analyse_render=False)
    s.save_evaluation(r, topic_title="Volcano", category="Geography", experiment={"id": "exp_1", "arm": "contradiction"})
    assert s.evaluated_job_ids() == {JOB}
    assert s.link_video_ids() == 1 and s.link_video_ids() == 0                       # idempotent, exact job match
    rec = s.get_record(JOB)
    assert rec["youtube_video_id"] == "VID123" and rec["script_fingerprint"] == "abc"
    assert rec["experiment_arm"] == "contradiction" and rec["evaluation"]["version"] == ev.VERSION
    s.save_evaluation(r, topic_title="Volcano")                                         # re-evaluation updates
    assert len(s.list_records()) == 1 and s.summary()["linked_to_youtube"] == 1


def test_experiments_validation_and_single_active(tmp_path):
    s = store(tmp_path)
    with pytest.raises(CreativeStoreError):
        s.create_experiment("music_style", ["a", "b"], "h", "abhi")
    with pytest.raises(CreativeStoreError):
        s.create_experiment("hook_type", ["contradiction"], "h", "abhi")
    with pytest.raises(CreativeStoreError):
        s.create_experiment("hook_type", ["contradiction", "made_up"], "h", "abhi")
    with pytest.raises(CreativeStoreError):
        s.create_experiment("hook_type", ["contradiction", "scale"], "", "abhi")
    e1 = s.create_experiment("hook_type", ["contradiction", "direct_question"], "questions beat contradictions", "abhi")
    assert e1["status"] == "DRAFT" and s.active_experiment("hook_type") is None
    s.set_experiment_status(e1["id"], "ACTIVE", "abhi")
    e2 = s.create_experiment("hook_type", ["scale", "stakes"], "numbers beat stakes", "abhi")
    with pytest.raises(CreativeStoreError):
        s.set_experiment_status(e2["id"], "ACTIVE", "abhi")                             # one active per dimension
    assert s.active_experiment("hook_type")["id"] == e1["id"]
    with pytest.raises(CreativeStoreError):
        s.set_experiment_status("exp_missing", "PAUSED", "abhi")


def test_hook_type_experiment_controls_assignment(tmp_path, monkeypatch):
    s = store(tmp_path)
    monkeypatch.setattr("backend.app.creative_intel.store.CreativeStore", lambda *a, **k: s)
    jobs = [f"cycle_{i}_20261121_0000" for i in range(40)]
    assert all(gsw.choose_hook_type_for_job(j) == (gsw.choose_hook_type(j), None) for j in jobs)   # default unchanged
    e = s.create_experiment("hook_type", ["contradiction", "direct_question"], "h", "abhi")
    s.set_experiment_status(e["id"], "ACTIVE", "abhi")
    got = [gsw.choose_hook_type_for_job(j) for j in jobs]
    assert {h[0] for h, _ in got} == {"contradiction", "direct_question"}               # both arms used
    assert all(x["id"] == e["id"] and x["arm"] == h[0] for h, x in got)
    assert all(experiment_arm(j, ["contradiction", "direct_question"]) == h[0] for j, (h, _) in zip(jobs, got))
    s.set_experiment_status(e["id"], "PAUSED", "abhi")
    assert gsw.choose_hook_type_for_job(jobs[0])[1] is None


def test_experiment_lookup_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr("backend.app.creative_intel.store.CreativeStore", boom)
    assert gsw.choose_hook_type_for_job("j1") == (gsw.choose_hook_type("j1"), None)


# -- runner ----------------------------------------------------------------------------------------------------
def runner_db(tmp_path, statuses):
    s = store(tmp_path)
    conn = sqlite3.connect(s.db_path)
    conn.execute("CREATE TABLE persistent_jobs (id TEXT, topic_title TEXT, status TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO persistent_jobs VALUES (?,?,?,?)", statuses)
    conn.commit()
    conn.close()
    return s


def test_runner_skips_while_a_job_is_active(tmp_path):
    workspace(tmp_path)
    s = runner_db(tmp_path, [(JOB, "Volcano", "COMPLETED", "2026-11-21 00:00:00"), ("cycle_2_x", "t", "RUNNING", "2026-11-21 01:00:00")])
    calls = []
    out = runner.evaluate_pending(store=s, db_path=s.db_path, productions=tmp_path / "productions",
                                  evaluate=lambda *a, **k: calls.append(a) or {}, ffmpeg="f", ffprobe="p")
    assert out["skipped"] and calls == []


def test_runner_evaluates_pending_and_links(tmp_path):
    workspace(tmp_path)
    s = runner_db(tmp_path, [(JOB, "Volcano", "COMPLETED", "2099-11-21 00:00:00"),
                             ("cycle_9_old", "t", "FAILED", "2099-11-21 00:00:00"),
                             ("cycle_8_noworkspace", "t", "COMPLETED", "2099-11-21 00:00:00")])
    out = runner.evaluate_pending(store=s, db_path=s.db_path, productions=tmp_path / "productions",
                                  evaluate=lambda p, j, **k: ev.evaluate_production(p, j, analyse_render=False),
                                  ffmpeg="f", ffprobe="p")
    assert [e["job_id"] for e in out["evaluated"]] == [JOB] and out["videos_linked"] == 1 and out["errors"] == {}
    again = runner.evaluate_pending(store=s, db_path=s.db_path, productions=tmp_path / "productions",
                                    evaluate=lambda *a, **k: pytest.fail("already evaluated"), ffmpeg="f", ffprobe="p")
    assert again["evaluated"] == [] and again["backlog"] == 0


def test_runner_captures_evaluation_errors(tmp_path):
    workspace(tmp_path)
    s = runner_db(tmp_path, [(JOB, "Volcano", "COMPLETED", "2099-11-21 00:00:00")])

    def boom(*a, **k):
        raise RuntimeError("corrupt artifacts")
    out = runner.evaluate_pending(store=s, db_path=s.db_path, productions=tmp_path / "productions", evaluate=boom,
                                  ffmpeg="f", ffprobe="p")
    assert "corrupt artifacts" in out["errors"][JOB] and s.evaluated_job_ids() == set()


def test_scheduler_registers_creative_tick_after_render_window():
    src = (Path(__file__).resolve().parents[1] / "app" / "scheduler" / "scheduler.py").read_text(encoding="utf-8")
    i = src.index('id="creative_evaluation_tick"')
    chunk = src[i - 300:i + 200]
    assert 'minute="33"' in chunk and "max_instances=1" in chunk


def test_intelligence_and_experiment_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    s = store(tmp_path)
    monkeypatch.setattr(main, "_creative_store", lambda: s)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    remote = TestClient(main.app)
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    assert remote.get("/api/intelligence/topic-creative-rendering").status_code == 403
    view = local.get("/api/intelligence/topic-creative-rendering").json()
    assert set(view) == {"topic", "creative", "rendering"} and "demand_signals" in view["topic"]["flags"]
    body = {"operator": "abhi", "dimension": "hook_type", "arms": ["contradiction", "scale"], "hypothesis": "numbers win"}
    assert local.post("/api/creative/experiments", json=body).status_code == 403                    # no CSRF
    assert local.post("/api/creative/experiments", json=body | {"dimension": "music"}, headers=h).status_code == 422
    assert local.post("/api/creative/experiments", json=body | {"arms": ["contradiction", "x"]}, headers=h).status_code == 400
    assert local.post("/api/creative/experiments", json=body | {"operator": "autonomy-engine"}, headers=h).status_code in (400, 403)
    e = local.post("/api/creative/experiments", json=body, headers=h).json()
    assert e["status"] == "DRAFT"
    url = f"/api/creative/experiments/{e['id']}/status"
    assert local.post(url, json={"operator": "abhi", "status": "DRAFT"}, headers=h).status_code == 422
    assert local.post(url, json={"operator": "abhi", "status": "ACTIVE"}, headers=h).json()["status"] == "ACTIVE"
    assert local.post("/api/creative/experiments/exp_zzzz/status", json={"operator": "abhi", "status": "PAUSED"},
                      headers=h).status_code == 404
