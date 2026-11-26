"""Stage 3.37 benchmark: coordinator overhead and the Shorts baseline that sizes the long-form gap.

Read-only against production (``?mode=ro``); all writes go to a throwaway database in a temp folder. It does NOT run
a render, and it never runs two renders at once: raising heavy concurrency above 1 needs a separate, operator-
scheduled rendering benchmark (docs/STAGE_3_37_MULTI_PIPELINE_PRODUCTION.md section 6).

Usage: python backend/scripts/benchmark_coordinator_337.py [--prod-db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Point every app module at a throwaway database: production is only ever opened read-only below.
_TMP = tempfile.mkdtemp(prefix="bench337-")
os.environ["CRAZZYDOCS_DATABASE_PATH"] = str(Path(_TMP) / "app.db")

from backend.app.scheduler import resource_monitor  # noqa: E402
from backend.app.scheduler.production_coordinator import HEAVY, LONG_FORM, ProductionCoordinator  # noqa: E402


def _ms(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return {"n": n, "median_ms": round(statistics.median(out), 2), "max_ms": round(max(out), 2)}


def shorts_baseline(db: str) -> dict:
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT actual_start_at, updated_at FROM persistent_jobs WHERE status='COMPLETED' "
                            "AND actual_start_at IS NOT NULL ORDER BY created_at DESC LIMIT 100").fetchall()
    finally:
        conn.close()
    mins = []
    for a, b in rows:
        try:
            mins.append((datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds() / 60)
        except ValueError:
            pass
    mins = sorted(m for m in mins if 0 < m < 120)
    if not mins:
        return {"jobs": 0}
    p95 = mins[min(len(mins) - 1, int(0.95 * len(mins)))]
    return {"jobs": len(mins), "median_min": round(statistics.median(mins), 1), "p95_min": round(p95, 1),
            "max_min": round(mins[-1], 1), "hourly_gap_after_p95_min": round(60 - p95, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod-db", default=str(ROOT / "backend" / "studio.db"))
    args = ap.parse_args()
    res: dict = {"at": datetime.now().isoformat(timespec="seconds")}
    res["probe_cold"] = _ms(lambda: resource_monitor.sample(use_cache=False), 3)
    res["probe_cached"] = _ms(lambda: resource_monitor.sample(), 50)
    res["sample"] = {k: v for k, v in resource_monitor.sample().items() if k not in ("gpu_engines", "ffmpeg_pids")}
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "bench.db"
        sqlite3.connect(str(db)).close()
        c = ProductionCoordinator(str(db), resource_probe=lambda: res["sample"],
                                  shorts_view=lambda now: {"known": True, "active_jobs": [], "next_start_utc": None})
        c.ensure()

        def lease():
            with c.shorts_render_slot("bench"):
                pass

        res["shorts_lease_overhead"] = _ms(lease, 100)
        res["slot_acquire_release"] = _ms(lambda: c.release_slot(c.acquire_slot(LONG_FORM, "b")), 100)
        for i in range(50):
            c.enqueue(kind="bench", title=f"j{i}", requested_by="bench",
                      steps=[{"name": "render", "weight": HEAVY, "estimate_minutes": 20}])
        res["plan_50_jobs"] = _ms(lambda: c.plan(), 20)
        res["status_50_jobs"] = _ms(lambda: c.status(), 10)
    try:
        res["shorts_baseline"] = shorts_baseline(args.prod_db)
    except Exception as exc:  # noqa: BLE001
        res["shorts_baseline"] = {"error": type(exc).__name__}
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
