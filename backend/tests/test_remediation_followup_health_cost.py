"""Remediation follow-up: root cause of the dashboard's 60-second CPU bursts (and its growth with uptime).

Live evidence (worker-thread sampler, dashboard PID 12584): 22 of 58 busy samples in
production_health_dashboard.get_disk_health, which walked the whole media tree (60k+ files, ~10k more per day) on
every one-minute /api/dashboard/health poll; get_database_health ran a full PRAGMA integrity_check each time too.
"""
from __future__ import annotations

import os
import sqlite3
import time

from backend.app.services import production_health_dashboard as phd


def _tree(tmp_path, n=30):
    media = tmp_path / "media"
    for i in range(n):
        d = media / f"d{i % 5}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i}.bin").write_bytes(b"x" * 1024)
    return str(media)


def _reset():
    with phd._media_size_lock:
        phd._media_size_state.update(mb=None, at=None, running=False, dir=None)


def test_footprint_is_cached_and_rescanned_at_most_once_per_ttl(tmp_path, monkeypatch):
    _reset()
    media = _tree(tmp_path)
    scans = []
    real = phd._scan_media_mb
    monkeypatch.setattr(phd, "_scan_media_mb", lambda d: scans.append(d) or real(d))
    mb, at = phd.media_footprint_mb(media, now=1000.0, background=False)
    assert mb == round(30 * 1024 / (1024 * 1024), 2) and len(scans) == 1
    for t in (1001.0, 1500.0, 2700.0):
        phd.media_footprint_mb(media, now=t, background=False)
    assert len(scans) == 1                                     # within the TTL: no walk at all
    phd.media_footprint_mb(media, now=at + phd.MEDIA_SIZE_TTL_SEC + 1, background=False)
    assert len(scans) == 2
    _reset()


def test_health_call_never_walks_on_the_request_thread(tmp_path, monkeypatch):
    _reset()
    media = _tree(tmp_path)
    monkeypatch.setattr(os, "walk", lambda *a, **k: (_ for _ in ()).throw(AssertionError("os.walk on request path")))
    started = []
    monkeypatch.setattr(phd, "_refresh_media_size", lambda d: started.append(d))
    t0 = time.perf_counter()
    mb, at = phd.media_footprint_mb(media)                    # first call: schedules the background scan
    assert (time.perf_counter() - t0) < 0.5 and mb is None and at is None
    _reset()


def test_integrity_check_is_cached(tmp_path, monkeypatch):
    db = tmp_path / "studio.db"
    sqlite3.connect(db).executescript("CREATE TABLE a (x INTEGER);")
    phd._integrity_state.clear()
    dash = phd.ProductionHealthDashboard.__new__(phd.ProductionHealthDashboard)
    dash.db_path = str(db)
    first = dash.get_database_health()
    assert first["status"] == "HEALTHY" and first["table_count"] == 1
    stamp = phd._integrity_state[str(db)][1]
    second = dash.get_database_health()
    assert phd._integrity_state[str(db)][1] == stamp and second["integrity"] == "ok"   # not re-run
    phd._integrity_state.clear()
