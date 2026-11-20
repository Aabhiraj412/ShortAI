"""Stage 3.34: controlled production validation - regression and failure-injection tests.

Isolated: temporary directories and databases, fake HTTP, fake processes. Nothing here touches the production
database, media, scheduler or renderer.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import io
import json
import os
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core import fs_utils
from backend.app.core.process_utils import pid_running
from backend.app.scheduler import worker_registry as wr
from backend.app.scheduler.retry_policy import DETERMINISTIC, PERMANENT, TRANSIENT, classify_error


# == 1. process liveness (dead worker / killed monitor were treated as alive) ==================================
def _exited_child_still_referenced():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(20)                       # exited; this test still holds the Popen handle
    return child


def test_pid_running_requires_a_live_process():
    child = _exited_child_still_referenced()
    assert pid_running(os.getpid()) is True
    assert pid_running(child.pid) is False
    assert pid_running(0) is False and pid_running(None) is False and pid_running("x") is False


def test_deployment_liveness_and_health_use_exit_code_check():
    from backend.app.services import deployment_diagnostics as dd
    child = _exited_child_still_referenced()
    assert dd.is_pid_alive(child.pid) is False and dd.is_pid_alive(os.getpid()) is True


def _job(worker_id, hb_age_sec):
    return SimpleNamespace(id="cycle_9_20990101_0800", status="RUNNING",
                           heartbeat_at=datetime.utcnow() - timedelta(seconds=hb_age_sec),
                           checkpoint_data_json=json.dumps({"cycle_state": {"worker_id": worker_id}}))


def test_dead_worker_with_stale_heartbeat_is_stalled_and_recoverable():
    child = _exited_child_still_referenced()
    reg = wr.WorkerRegistry()
    assert wr.is_stalled(_job(f"worker-{child.pid}-t1", wr.STALE_AFTER_SEC + 60), reg=reg) is True


def test_live_worker_is_never_stalled_even_with_an_old_heartbeat():
    reg = wr.WorkerRegistry()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        assert wr.is_stalled(_job(f"worker-{other.pid}-t1", 10_000), reg=reg) is False   # long stage, live process
    finally:
        other.kill()
        other.wait(10)


def test_fresh_heartbeat_is_never_stalled_even_if_worker_unknown():
    assert wr.is_stalled(_job("unparseable", 5), reg=wr.WorkerRegistry()) is False


def test_heartbeat_pump_refreshes_during_a_long_stage(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.database.db import Base, JobModel
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    old = datetime.utcnow() - timedelta(minutes=10)
    with S() as s:
        s.add(JobModel(id="job1", date_str="2099-01-01", topic_title="t", status="RUNNING", current_stage="RENDERING",
                       heartbeat_at=old, checkpoint_data_json=json.dumps({"cycle_state": {"worker_id": "w1"}})))
        s.commit()
    pump = wr.HeartbeatPump("job1", "w1", S, interval_sec=60)
    assert pump.beat_once() is True
    with S() as s:
        assert s.get(JobModel, "job1").heartbeat_at > old
        s.get(JobModel, "job1").status = "FAILED"
        s.commit()
    assert pump.beat_once() is False                       # never revives a job that is no longer RUNNING


# == 2. file locks (WinError 32) =============================================================================
def _lock_error():
    e = PermissionError(13, "The process cannot access the file because it is being used by another process")
    e.winerror = 32
    return e


def test_replace_retries_a_transient_sharing_violation(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("new")
    dst.write_text("old")
    real, calls, sleeps = os.replace, [], []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise _lock_error()
        real(a, b)
    monkeypatch.setattr(fs_utils.os, "replace", flaky)
    fs_utils.replace_with_retry(str(src), str(dst), sleep=sleeps.append)
    assert dst.read_text() == "new" and len(calls) == 3 and sleeps == [0.15, 0.3]


def test_replace_does_not_retry_other_errors(monkeypatch):
    calls = []

    def bad(a, b):
        calls.append(1)
        raise FileNotFoundError("gone")
    monkeypatch.setattr(fs_utils.os, "replace", bad)
    with pytest.raises(FileNotFoundError):
        fs_utils.replace_with_retry("a", "b", sleep=lambda s: None)
    assert len(calls) == 1


def test_remove_never_raises_for_a_persistent_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(fs_utils.os, "remove", lambda p: (_ for _ in ()).throw(_lock_error()))
    assert fs_utils.remove_with_retry(str(tmp_path / "x"), sleep=lambda s: None) is False
    assert fs_utils.remove_with_retry(None) is True


def test_real_open_handle_blocks_delete_on_windows(tmp_path):
    """Documents the original defect: deleting a file while our own write handle is open fails on Windows."""
    if os.name != "nt":
        pytest.skip("Windows-only sharing semantics")
    p = tmp_path / "t.tmp"
    with open(p, "wb") as f:
        f.write(b"x")
        with pytest.raises(PermissionError) as exc:
            os.remove(p)
        assert getattr(exc.value, "winerror", None) == 32


# == 3. downloader: oversize, retries, cleanup ===============================================================
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class FakeResp:
    def __init__(self, data, ctype="image/png", fail_after=None):
        self.data, self.pos, self.fail_after = data, 0, fail_after
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        if self.fail_after is not None and self.pos >= self.fail_after:
            raise http.client.IncompleteRead(b"")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


def _downloader(tmp_path, monkeypatch, responses):
    from backend.app.media import downloader as dl
    it = iter(responses)

    def fake_urlopen(req, timeout=None):
        r = next(it)
        if isinstance(r, BaseException):
            raise r
        return r
    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    from backend.app.media.models import LicenseType, MediaAsset
    asset = MediaAsset(asset_id="a1", title="Nares Strait pan crop", creator="c", source_repository="Openverse",
                       source_url="https://example.org/page", download_url="https://example.org/x.png",
                       license_type=list(LicenseType)[0], attribution_text="a")
    return dl.AssetDownloader(storage_dir=str(tmp_path)), asset


def test_oversize_download_is_rejected_without_a_lock_error(tmp_path, monkeypatch):
    from backend.app.media import downloader as dl
    monkeypatch.setattr(dl, "MAX_FILE_SIZE_BYTES", 100_000)
    d, a = _downloader(tmp_path, monkeypatch, [FakeResp(b"\x89PNG" + b"0" * 300_000)])
    ok, path, sha, err = d.download_asset(a)
    assert not ok and "exceeded maximum" in err
    assert not list(tmp_path.glob("temp_*.tmp"))            # removed after the handle closed


def test_retry_after_midstream_drop_starts_clean(tmp_path, monkeypatch):
    from backend.app.media import downloader as dl
    data = _png_bytes()
    monkeypatch.setattr(dl, "MAX_FILE_SIZE_BYTES", int(len(data) * 1.5))   # double counting would trip this
    d, a = _downloader(tmp_path, monkeypatch, [FakeResp(data, fail_after=len(data) // 2 + 1), FakeResp(data)])
    ok, path, sha, err = d.download_asset(a)
    assert ok, err
    assert sha == hashlib.sha256(data).hexdigest()          # hash of the real file, not of partial + full bytes
    assert Path(path).read_bytes() == data and not list(tmp_path.glob("temp_*.tmp"))


@pytest.mark.parametrize("exc", [ConnectionResetError("reset"), http.client.IncompleteRead(b""),
                                 urllib.error.URLError("dns"), TimeoutError("slow")])
def test_network_errors_retry_then_fail_cleanly(tmp_path, monkeypatch, exc):
    d, a = _downloader(tmp_path, monkeypatch, [exc, exc, exc])
    ok, path, sha, err = d.download_asset(a)
    assert not ok and "after 3 attempts" in err and not list(tmp_path.glob("temp_*.tmp"))


def test_corrupt_or_wrong_type_media_is_rejected_and_cleaned(tmp_path, monkeypatch):
    d, a = _downloader(tmp_path, monkeypatch, [FakeResp(b"<html>not an image</html>" * 10)])
    assert d.download_asset(a)[0] is False and not list(tmp_path.glob("temp_*.tmp"))
    d2, a2 = _downloader(tmp_path, monkeypatch, [FakeResp(b"x" * 10, ctype="text/html")])
    assert "Content-Type" in d2.download_asset(a2)[3]


def test_existing_identical_asset_is_reused_not_overwritten(tmp_path, monkeypatch):
    data = _png_bytes()
    d, a = _downloader(tmp_path, monkeypatch, [FakeResp(data), FakeResp(data)])
    ok1, p1, _, _ = d.download_asset(a)
    mtime = os.path.getmtime(p1)
    ok2, p2, _, _ = d.download_asset(a)
    assert ok1 and ok2 and p1 == p2 and os.path.getmtime(p2) == mtime and not list(tmp_path.glob("temp_*.tmp"))


# == 4. voiceover: timeout, retries, cleanup =================================================================
def test_voiceover_timeout_retries_then_fails_transiently_without_partial_files(tmp_path, monkeypatch):
    from backend.app.services import expressive_tts_engine as tts
    calls = []

    class SlowCommunicate:
        def __init__(self, **kw):
            pass

        async def save(self, path):
            calls.append(path)
            Path(path).write_bytes(b"partial")
            await asyncio.sleep(5)
    monkeypatch.setattr(tts.edge_tts, "Communicate", SlowCommunicate)
    eng = tts.ExpressiveTTSEngine(request_timeout_sec=0.05, max_retries=2) if "max_retries" in tts.ExpressiveTTSEngine.__init__.__code__.co_varnames else tts.ExpressiveTTSEngine(request_timeout_sec=0.05)
    seg = tts.NarrationSegment(id="s1", text="Hello there, this is a test.", emotion="neutral")
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(eng.synthesize_segment(seg, str(tmp_path)))
    assert len(calls) == eng.max_retries + 1
    assert classify_error(exc.value)[0] == TRANSIENT
    assert not (tmp_path / "s1_raw.mp3").exists()


# == 5. classification of the failure scenarios ==============================================================
@pytest.mark.parametrize("exc,expected", [
    (RuntimeError("Live topic discovery failed or yielded no eligible verified topics; stopping safely"), TRANSIENT),
    (RuntimeError("TTS synthesis failed for s1: TimeoutError"), TRANSIENT),
    (RuntimeError("FFmpeg Priority B adaptation failed: [Parsed_pad_2] Padded dimensions cannot be smaller than input dimensions."), DETERMINISTIC),
    (ValueError("QC Failure: Duration 29.10s is outside the accepted window"), DETERMINISTIC),
    (ValueError("BLOCKED_QC: ['renderer compliance']"), DETERMINISTIC),
    (RuntimeError("Quota exceeded for quota metric 'Queries' and limit"), PERMANENT),
    (KeyError("missing field"), PERMANENT),
    (ConnectionResetError("upload connection reset"), TRANSIENT),
])
def test_failure_classification(exc, expected):
    assert classify_error(exc)[0] == expected


def test_readiness_failure_is_detected_and_deterministic():
    from backend.app.rendering.render_errors import RenderFailure, is_readiness_failure
    msg = "Lemo-Opuscar command failed (1): window.READY never became true (waited 180 s, limit 180 s)"
    assert is_readiness_failure(msg) and not is_readiness_failure("ffmpeg exited 1")
    f = RenderFailure(msg, attempts=2, readiness=True, deterministic=True)
    assert f.deterministic and f.readiness


def test_pop_info_page_bounds_every_readiness_wait():
    """Root cause of the Nov 13 readiness timeouts was a page that never set READY. The Pop Info page must bound
    image loads, tolerate image errors and font failures, and always set READY."""
    js = (Path(__file__).resolve().parents[1] / "app" / "rendering" / "pop_info" / "engine.js").read_text(encoding="utf-8")
    ready_block = js[js.index("await Promise.all(SCENES.filter"):js.index("window.READY = true") + 20]
    assert "setTimeout(() => done(false), 20000)" in ready_block and "im.onerror" in ready_block
    assert "document.fonts.ready" in ready_block and "catch" in ready_block


# == 6. video validation: invalid/partial MP4 and ffprobe failure ============================================
def _ffmpeg():
    from test_render_geometry_332h import FFMPEG
    return FFMPEG


def test_partial_mp4_and_probe_failure_are_validation_failures(tmp_path):
    ff = _ffmpeg()
    if not ff:
        pytest.skip("FFmpeg not available")
    from backend.app.creative_intel.evaluation import probe_streams
    good = tmp_path / "good.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30:d=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(good)], check=True, timeout=60)
    partial = tmp_path / "partial.mp4"
    partial.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 3])   # moov atom missing (renderer killed)
    probe = str(Path(ff).with_name("ffprobe.exe" if ff.endswith(".exe") else "ffprobe"))
    assert probe_streams(good, probe)["streams"][0]["width"] == 540
    with pytest.raises(RuntimeError):
        probe_streams(partial, probe)
    from backend.app.rendering.duration_standards import duration_violation
    assert duration_violation(29.9) and duration_violation(58.1) and not duration_violation(30.0) and not duration_violation(58.0)


# == 7. scheduler: duplicate claims, idle restart, recovery windows ==========================================
def test_worker_registry_refuses_a_second_worker_for_the_same_job():
    import threading
    reg = wr.WorkerRegistry()
    claimed, release = threading.Event(), threading.Event()

    def first_worker():
        assert reg.register("job1", "worker-1-t1")
        claimed.set()
        release.wait(10)
    t = threading.Thread(target=first_worker)
    t.start()
    claimed.wait(10)
    try:
        assert reg.register("job1", "worker-1-t2") is False   # a live worker holds the job: duplicate refused
        assert reg.is_active("job1")
    finally:
        release.set()
        t.join(10)
    assert reg.register("job1", "worker-1-t2") is True        # the first worker has finished


def test_restart_is_refused_while_a_job_runs_and_allowed_when_idle(monkeypatch):
    from backend.app.services import deployment_diagnostics as dd
    monkeypatch.setattr(dd, "active_jobs", lambda: [{"job_id": "cycle_4_20990101_0300", "status": "RUNNING", "stage": "RENDERING"}])
    with pytest.raises(ValueError, match="in progress"):
        dd.request_daemon_restart(True, "test")
    monkeypatch.setattr(dd, "pending_restart_request", lambda: {"requested_at_utc": "x"})
    assert dd.should_exit_for_restart()[0] is False            # a pending request still waits for the job
    monkeypatch.setattr(dd, "active_jobs", lambda: [])
    assert dd.should_exit_for_restart() == (True, "no active production job")
    monkeypatch.setattr(dd, "active_jobs", lambda: [{"job_id": "test_x", "status": "RUNNING", "stage": "x"}])
    assert dd.safe_restart_state()["safe"] is True             # synthetic test jobs never block


def test_single_render_slot_is_configured_for_production():
    src = (Path(__file__).resolve().parents[1] / "app" / "rendering" / "lemo_opuscar_adapter.py").read_text(encoding="utf-8")
    assert 'env["RENDER_SLOTS"] = "1"' in src and '"--workers",\n                "1",' in src


# == 8. observability: the live stage replaces the coarse checkpoint stage =====================================
def test_live_stage_from_progress_store(tmp_path):
    from datetime import timezone
    from backend.app.services import deployment_diagnostics as dd
    now = datetime(2099, 1, 1, 3, 5, tzinfo=timezone.utc)
    store = tmp_path / "progress.json"
    store.write_text(json.dumps({"productions": {"job1": {"current_stage": "rendering", "current_operation": "Lemo-Opuscar frames",
                                                          "current_stage_progress": 40.0, "overall_progress": 71.0,
                                                          "last_updated": "2099-01-01T03:04:30Z"}}}), encoding="utf-8")
    live = dd._live_stage("job1", str(store), now)
    assert live["stage"] == "rendering" and live["overall_progress"] == 71.0 and live["live_stage_age_sec"] == 30.0
    assert dd._live_stage("job1", str(store), now + timedelta(hours=1)) == {"live_stage_stale": True}   # stale: keep checkpoint
    assert dd._live_stage("missing", str(store), now) == {}
    assert dd._live_stage("job1", str(tmp_path / "nope.json"), now) == {}


# == 9. delivery colour format (Phase C) ======================================================================
def test_final_muxes_use_the_delivery_colour_format():
    root = Path(__file__).resolve().parents[1] / "app"
    pipe = (root / "rendering" / "pop_info" / "pipeline.py").read_text(encoding="utf-8")
    orch = (root / "orchestration" / "crazzydocs_master_orchestrator.py").read_text(encoding="utf-8")
    assert '"-vf", DELIVERY_COLOR_VF, "-c:v", "libx264"' in pipe
    assert '"-vf", DELIVERY_COLOR_VF' in orch


def test_full_range_capture_becomes_limited_range_bt709(tmp_path):
    ff = _ffmpeg()
    if not ff:
        pytest.skip("FFmpeg not available")
    from backend.app.rendering.ffmpeg_geometry import DELIVERY_COLOR_VF
    probe = str(Path(ff).with_name("ffprobe.exe" if ff.endswith(".exe") else "ffprobe"))
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    # like the browser capture: MJPEG frames -> full-range yuvj420p, white areas at luma 255
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=540x960:r=30:d=1",
                    "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30:d=1", "-filter_complex", "[0][1]blend=all_mode=lighten,format=yuvj420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True, timeout=60)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-vf", DELIVERY_COLOR_VF, "-c:v", "libx264",
                    "-preset", "ultrafast", "-r", "30", str(out)], check=True, timeout=60)

    def info(p):
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=pix_fmt,color_range,color_space,color_primaries,color_transfer,nb_frames", "-of", "json", str(p)],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)["streams"][0]
    a, b = info(src), info(out)
    assert a["color_range"] == "pc"
    assert (b["pix_fmt"], b["color_range"], b["color_space"], b["color_primaries"], b["color_transfer"]) == \
        ("yuv420p", "tv", "bt709", "bt709", "bt709")
    assert a["nb_frames"] == b["nb_frames"]
    ymax = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(out), "-vf", "signalstats,metadata=mode=print:file=-",
                           "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=60).stdout
    import re
    assert max(int(v) for v in re.findall(r"YMAX=(\d+)", ymax)) <= 240     # within the limited-range ceiling
