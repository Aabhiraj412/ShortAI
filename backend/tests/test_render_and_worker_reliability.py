"""
Regressions for the Lemo-Opuscar readiness failure (cycle_1/cycle_2 of 2026-11-13) and
for worker liveness:

- the generated film page signals window.READY (it never did), shows video beats with
  <video> (they were broken <img> elements), and renders in a real headless browser;
- render failures retry inside Stage 9 with the same inputs and are classified so a
  deterministic readiness failure does not consume whole-pipeline retries;
- script/narration/media/storyboard are reused on a retry, not regenerated;
- a long stage keeps the worker heartbeat fresh, and a live worker is never replaced.
"""
import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core import paths
from backend.app.database.db import Base, JobModel
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.app.orchestration.stage_artifact_cache import StageArtifactCache, fingerprint
from backend.app.rendering.lemo_opuscar_film_builder import LemoOpuscarFilmBuilder, _render_film_html
from backend.app.rendering.render_errors import RenderFailure, is_readiness_failure
from backend.app.scheduler import reconciliation_service as recon_mod
from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler import worker_registry as wr
from backend.app.services import control_center_service as control
from backend.app.services.media_lifecycle_manager import MediaLifecycleManager

IST = ZoneInfo("Asia/Kolkata")
READINESS_ERROR = (
    "Lemo-Opuscar command failed (1): still waiting for window.READY after 20 s.\n"
    "window.READY never became true (waited 180 s, limit 180 s): page.waitForFunction: Timeout 180000ms exceeded."
)


# =============================================================================
# 1. Film page contract
# =============================================================================

def _package(tmp_path, with_video=True):
    src = tmp_path / "src"
    src.mkdir()
    from PIL import Image
    img = src / "photo.png"
    Image.new("RGB", (64, 64), (200, 40, 40)).save(img)
    beats = [{"beat_number": 1, "image_path": str(img), "spoken_text": "Hook </script><script>alert(1)</script>"}]
    if with_video:
        clip = src / "anim.mp4"
        ffmpeg = paths.get_ffmpeg_path()
        if not (ffmpeg and os.path.isfile(ffmpeg)):
            pytest.skip("ffmpeg not available")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=1",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)], check=True)
        beats.append({"beat_number": 2, "image_path": str(clip), "spoken_text": "Second"})
    prod = tmp_path / "prod"
    manifest = LemoOpuscarFilmBuilder.build_film_package(
        prod_dir=prod, job_id="cycle_x_20261113_0000", topic="Test topic",
        script_data={"title": "Test topic"}, storyboard=beats, duration_sec=16.0, category="Science",
    )
    return prod / "lemo_film", manifest


def test_page_signals_ready_and_uses_video_elements(tmp_path):
    film, manifest = _package(tmp_path, with_video=True)
    html = (film / "index.html").read_text(encoding="utf-8")
    assert "window.READY = true" in html, "the page must signal readiness (root cause of the 180 s timeout)"
    assert html.index("window.READY = false") < html.index("window.READY = true")
    assert 'document.createElement("video")' in html and "VIDEO_EXT" in html
    assert "document.fonts.ready" in html and "ASSET_STATUS" in html
    # A caption cannot terminate the script element.
    assert "</script><script>alert(1)" not in html and "<\\/script>" in html
    assert manifest["unique_asset_count"] == 2


def test_old_template_failure_mode_is_detected():
    assert is_readiness_failure(READINESS_ERROR)
    assert not is_readiness_failure("FFmpeg audio mux failed")


def _lemo_runtime():
    tools = Path(paths.TOOLS_DIR)
    lib = tools / "dependencies" / "lemo-opuscar"
    node = tools / "nodejs" / "node.exe"
    ffbin = tools / "ffmpeg" / "bin"
    edge = Path(os.environ.get("PLAYWRIGHT_CHROME", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    if not all(p.exists() for p in (lib / "core" / "render" / "still.mjs", node, ffbin / "ffmpeg.exe", edge)):
        pytest.skip("Lemo-Opuscar runtime (library, node, ffmpeg, Edge) not available")
    return lib, node, ffbin, edge


def test_built_page_renders_in_real_headless_browser(tmp_path):
    """End-to-end on the pinned renderer: READY arrives quickly and a video beat renders."""
    from backend.app.rendering.lemo_opuscar_adapter import LemoOpuscarAdapter
    lib, node, ffbin, edge = _lemo_runtime()
    film, _ = _package(tmp_path, with_video=True)
    adapter = LemoOpuscarAdapter(lib, node, ffbin, edge, timeout_sec=170)
    t0 = time.monotonic()
    out = adapter.render_still(tmp_path, film, tmp_path / "frame.jpg", time_sec=9.0, size=(270, 480))
    elapsed = time.monotonic() - t0
    assert out.is_file() and out.stat().st_size > 1000
    assert elapsed < 60, f"render took {elapsed:.0f}s; the old page always waited the full 180 s"


# =============================================================================
# 2. Bounded render retries and classification
# =============================================================================

@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(type(master_orchestrator), "RENDER_RETRY_DELAY_SEC", 0.0)
    monkeypatch.setattr(type(master_orchestrator), "RENDER_ATTEMPTS", 2)


def test_repeated_readiness_failure_is_deterministic_render_failure(fast_retries):
    calls = []

    def render():
        calls.append(1)
        raise RuntimeError(READINESS_ERROR)

    with pytest.raises(RenderFailure) as ei:
        master_orchestrator._render_with_bounded_retries(
            "cycle_9_20261113_0000", render, preserved={"lemo_film_dir": "x"}, persist_to_db=False)
    assert len(calls) == 2
    assert ei.value.readiness and ei.value.deterministic and ei.value.preserved == {"lemo_film_dir": "x"}


def test_transient_render_error_recovers_inside_the_stage(fast_retries):
    calls = []

    def render():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("browser crashed")
        return "ok"

    assert master_orchestrator._render_with_bounded_retries("j", render, preserved={}, persist_to_db=False) == "ok"
    assert len(calls) == 2


def test_non_readiness_render_failure_is_retryable(fast_retries):
    with pytest.raises(RenderFailure) as ei:
        master_orchestrator._render_with_bounded_retries(
            "j", lambda: (_ for _ in ()).throw(RuntimeError("WebGL context lost")), preserved={}, persist_to_db=False)
    assert not ei.value.deterministic


def _job(**kw):
    d = dict(id="cycle_1_20261113_0000", date_str="2026-11-13", status="RUNNING", retry_count=0,
             max_retries=3, checkpoint_data_json="{}", current_stage="RENDER_FAILED")
    d.update(kw)
    return JobModel(**d)


def test_policy_does_not_spend_pipeline_retries_on_deterministic_render_failure(tmp_path):
    job = _job()
    exc = RenderFailure("render failed", attempts=2, readiness=True, deterministic=True,
                        preserved={"lemo_film_dir": str(tmp_path / "lemo_film"), "voiceover_path": "v.mp3"})
    d = rp.record_failure(job, exc)
    assert (job.status, d["classification"], d["terminal"]) == ("FAILED", rp.RENDER_FAILURE, True)
    cs = json.loads(job.checkpoint_data_json)["cycle_state"]
    assert cs["preserved_artifacts"]["lemo_film_dir"].endswith("lemo_film")
    assert "not retried" in job.error_details


def test_policy_retries_non_deterministic_render_failure():
    job = _job()
    d = rp.record_failure(job, RenderFailure("x", attempts=2, readiness=False, deterministic=False))
    assert (job.status, d["classification"], d["terminal"]) == ("RETRY_SCHEDULED", rp.RENDER_FAILURE, False)


# =============================================================================
# 3. Render recovery reuses prepared inputs
# =============================================================================

def test_stage_cache_reuses_until_inputs_or_files_change(tmp_path):
    cache = StageArtifactCache(tmp_path / "cache")
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x" * 100)
    calls = []

    def tts():
        calls.append(1)
        return (str(voice), [{"section": "hook", "start": 0.0}], 69.4)

    fp = fingerprint("script text", ["hook"])
    v1, reused1 = cache.get_or_compute("narration", fp, tts, files_of=lambda v: [v[0]])
    v2, reused2 = cache.get_or_compute("narration", fp, tts, files_of=lambda v: [v[0]])
    assert (reused1, reused2, len(calls)) == (False, True, 1) and v1 == v2
    # Different inputs -> recompute.
    cache.get_or_compute("narration", fingerprint("other script"), tts, files_of=lambda v: [v[0]])
    assert len(calls) == 2
    # Referenced file changed or missing -> recompute.
    voice.write_bytes(b"y" * 7)
    cache.get_or_compute("narration", fingerprint("other script"), tts, files_of=lambda v: [v[0]])
    assert len(calls) == 3
    voice.unlink()

    def regenerate():
        voice.write_bytes(b"z" * 50)
        return tts()

    cache.get_or_compute("narration", fingerprint("other script"), regenerate, files_of=lambda v: [v[0]])
    assert len(calls) == 4 and voice.exists()


def test_orchestrator_routes_every_pre_render_stage_through_the_cache():
    import ast
    import inspect
    from backend.app.orchestration import crazzydocs_master_orchestrator as orch
    tree = ast.parse(inspect.getsource(orch))
    stages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get_or_compute" and node.args:
            if isinstance(node.args[0], ast.Constant):
                stages.add(node.args[0].value)
    assert {"script", "narration", "media", "storyboard"} <= stages


def test_preserved_render_inputs_are_protected_from_retention(tmp_path):
    ws = tmp_path / "ws"
    film = ws / "media" / "productions" / "cycle_2_20261113_0600" / "lemo_film"
    (film / "assets").mkdir(parents=True)
    page = film / "index.html"
    page.write_text("<html>")
    clip = film / "assets" / "beat_02_anim_concat_x.mp4"   # 'concat_' would otherwise look temporary
    clip.write_bytes(b"\0" * 2048)
    db = tmp_path / "jobs.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add(JobModel(id="cycle_2_20261113_0600", date_str="2026-11-13", status="FAILED",
                       checkpoint_data_json=json.dumps({"cycle_state": {"preserved_artifacts": {"lemo_film_dir": str(film)}}})))
        s.commit()
    engine.dispose()
    mgr = MediaLifecycleManager(workspace_root=str(ws), db_path=str(db), quarantine_dir=str(ws / "media" / "quarantine"))
    deps = mgr.collect_dependencies()
    for f in (page, clip):
        cf = mgr.classify_file(str(f), deps, {})
        assert cf.is_protected and not cf.is_quarantinable, (f, cf.category)


# =============================================================================
# 4. Worker heartbeat, liveness and duplicate dispatch
# =============================================================================

@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _claimed(worker_id, **kw):
    return _job(status="RUNNING", checkpoint_data_json=json.dumps({"cycle_state": {"worker_id": worker_id}}), **kw)


def test_live_worker_is_never_considered_stalled_even_with_an_old_heartbeat():
    reg = wr.WorkerRegistry()
    done = threading.Event()
    t = threading.Thread(target=lambda: (reg.register("j1", "worker-1-t1"), done.wait(5)))
    t.start()
    time.sleep(0.05)
    job = _claimed("worker-1-t1", id="j1", heartbeat_at=datetime.utcnow() - timedelta(hours=2))
    assert wr.is_stalled(job, reg=reg, pid_alive=lambda _p: False) is False
    done.set()
    t.join()
    # Worker thread finished and its process (pid 1 here) is not alive: now it is stalled.
    assert wr.is_stalled(job, reg=reg, pid_alive=lambda _p: False) is True


def test_liveness_rules_for_own_and_foreign_processes():
    reg = wr.WorkerRegistry()
    old = datetime.utcnow() - timedelta(minutes=10)
    me = f"worker-{os.getpid()}-t123"
    # Own process, thread not registered: gone -> stalled once heartbeat is stale.
    assert wr.is_stalled(_claimed(me, heartbeat_at=old), reg=reg) is True
    assert wr.is_stalled(_claimed(me, heartbeat_at=datetime.utcnow()), reg=reg) is False
    # Another live process may still own it -> never stalled.
    assert wr.is_stalled(_claimed("worker-999999-t1", heartbeat_at=old), reg=reg, pid_alive=lambda _p: True) is False
    assert wr.is_stalled(_claimed("worker-999999-t1", heartbeat_at=old), reg=reg, pid_alive=lambda _p: False) is True


def test_registry_refuses_a_second_live_worker_for_the_same_job():
    reg = wr.WorkerRegistry()
    hold = threading.Event()
    results = []
    t1 = threading.Thread(target=lambda: (results.append(reg.register("job", "worker-1-t1")), hold.wait(5)))
    t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=lambda: results.append(reg.register("job", "worker-1-t2")))
    t2.start()
    t2.join()
    assert results == [True, False]
    hold.set()
    t1.join()
    assert reg.register("job", "worker-1-t3") is True  # first worker finished


def test_heartbeat_pump_keeps_long_stage_fresh_and_stops_when_job_leaves_running(jobs_db):
    wid = f"worker-{os.getpid()}-t1"
    with jobs_db() as s:
        s.add(_claimed(wid, id="jh", heartbeat_at=datetime.utcnow() - timedelta(hours=1)))
        s.commit()
    pump = wr.HeartbeatPump("jh", wid, jobs_db, interval_sec=0.05).start()
    time.sleep(0.4)  # a "long render" with no stage checkpoint
    with jobs_db() as s:
        hb = s.query(JobModel).filter_by(id="jh").one().heartbeat_at
    assert datetime.utcnow() - hb < timedelta(seconds=2) and pump.beats >= 2
    with jobs_db() as s:
        s.query(JobModel).filter_by(id="jh").one().status = "FAILED"
        s.commit()
    time.sleep(0.2)
    beats = pump.beats
    time.sleep(0.2)
    pump.stop()
    assert pump.beats == beats  # stopped vouching for a job that is no longer running


@pytest.fixture
def sched_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sched.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (sched_mod, recon_mod, control):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(sched_mod, "reload_profile_from_db_if_changed", lambda: False)
    monkeypatch.setattr(sched_mod, "production_capacity", lambda **_: {"available": True})
    monkeypatch.setattr(control, "evaluate_resource_safety", lambda *a, **k: {"safe": True})
    monkeypatch.setattr(sched_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(recon_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(sched_mod.email_monitoring_service, "send_cycle_summary_report", lambda **_: {"status": "SENT"})
    sched_mod.set_active_profile("FOUR_CYCLES_DAILY")
    yield factory
    engine.dispose()


def test_no_duplicate_dispatch_while_original_worker_runs(sched_db):
    """A 2-hour-old heartbeat must not start a second worker if the first is alive."""
    now = datetime(2026, 11, 13, 0, 30, tzinfo=IST)
    started, release = threading.Event(), threading.Event()
    calls = []

    def long_render(job_id=None, **_):
        calls.append(job_id)
        started.set()
        release.wait(10)
        return {"status": "COMPLETED", "job_id": job_id}

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=long_render):
        first = threading.Thread(target=lambda: asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now)))
        first.start()
        assert started.wait(5)
        with sched_db() as s:
            job = s.query(JobModel).one()
            job.heartbeat_at = datetime.utcnow() - timedelta(hours=2)
            s.commit()
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now + timedelta(minutes=40)))
        asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now + timedelta(minutes=41)))
        assert r["status"] == "SKIPPED" and r["reason"] == "CONCURRENT_ACTIVE_RUN"
        assert len(calls) == 1
        release.set()
        first.join(10)
    assert len(calls) == 1


def test_dead_worker_with_stale_heartbeat_is_recovered_from_checkpoint(sched_db):
    now = datetime(2026, 11, 13, 0, 30, tzinfo=IST)
    dead = f"worker-{os.getpid()}-t42"   # this process, thread not registered: provably gone
    with sched_db() as s:
        s.add(JobModel(id="cycle_1_20261113_0000", date_str="2026-11-13", status="RUNNING", retry_count=0,
                       max_retries=3, current_stage="VOICEOVER", heartbeat_at=datetime.utcnow() - timedelta(hours=1),
                       checkpoint_data_json=json.dumps({"topic_title": "T", "cycle_state": {"worker_id": dead}})))
        s.commit()
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline") as pipe:
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now))
    assert r["reason"] == "WORKER_STALLED" and r["status"] == "RETRY_SCHEDULED"
    pipe.assert_not_called()  # retried later by the recovery loop, after backoff
    with sched_db() as s:
        job = s.query(JobModel).one()
        assert job.retry_count == 1 and json.loads(job.checkpoint_data_json)["topic_title"] == "T"
