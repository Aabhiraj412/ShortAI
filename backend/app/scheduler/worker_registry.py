"""
In-process registry of production worker threads, a periodic worker heartbeat, and
worker liveness checks.

- A job can have at most one live worker in this process (register() is atomic).
- While a worker runs, a heartbeat thread refreshes JobModel.heartbeat_at every
  HEARTBEAT_INTERVAL_SEC, including during long stages (render, TTS, downloads), but
  only while the job is still RUNNING and still claimed by this worker.
- A RUNNING job is considered stalled only when its claimed worker is provably gone
  (thread finished in this process, or the owning process is dead) AND the heartbeat
  is older than STALE_AFTER_SEC. A live worker is never replaced, however old the
  heartbeat looks.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = float(os.environ.get("CRAZZYDOCS_WORKER_HEARTBEAT_SEC", "30"))
STALE_AFTER_SEC = float(os.environ.get("CRAZZYDOCS_WORKER_STALE_SEC", "180"))
_WORKER_ID_RE = re.compile(r"^worker-(\d+)-t(\d+)$")


@dataclass
class _Worker:
    worker_id: str
    thread: threading.Thread
    started_mono: float = field(default_factory=time.monotonic)


class WorkerRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Dict[str, _Worker] = {}

    def register(self, job_id: str, worker_id: str) -> bool:
        """Claims job_id for the calling thread. False if another live worker holds it."""
        with self._lock:
            current = self._active.get(job_id)
            if current is not None and current.thread.is_alive() and current.thread is not threading.current_thread():
                return False
            self._active[job_id] = _Worker(worker_id, threading.current_thread())
            return True

    def unregister(self, job_id: str, worker_id: str) -> None:
        with self._lock:
            current = self._active.get(job_id)
            if current is not None and current.worker_id == worker_id:
                del self._active[job_id]

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            w = self._active.get(job_id)
            return bool(w and w.thread.is_alive())

    def active_worker_id(self, job_id: str) -> Optional[str]:
        with self._lock:
            w = self._active.get(job_id)
            return w.worker_id if w and w.thread.is_alive() else None


registry = WorkerRegistry()


def _pid_alive(pid: int) -> bool:
    from backend.app.services.deployment_diagnostics import is_pid_alive  # OpenProcess on Windows; never os.kill
    return bool(is_pid_alive(pid))


def worker_alive(job_id: str, worker_id: Optional[str], *, reg: WorkerRegistry = registry,
                 pid_alive: Callable[[int], bool] = _pid_alive) -> bool:
    """True if the worker that claimed job_id may still be executing it."""
    if reg.is_active(job_id):
        return True
    m = _WORKER_ID_RE.match(worker_id or "")
    if not m:
        return False  # unclaimed or unparseable: no evidence of a live worker
    pid = int(m.group(1))
    if pid == os.getpid():
        return False  # our own process, and the registry says the thread is gone
    return pid_alive(pid)  # another process: alive means it may still own the job


def claimed_worker_id(job) -> Optional[str]:
    try:
        return (json.loads(job.checkpoint_data_json or "{}").get("cycle_state") or {}).get("worker_id")
    except (TypeError, ValueError):
        return None


def is_stalled(job, *, now_utc: Optional[datetime] = None, reg: WorkerRegistry = registry,
               pid_alive: Callable[[int], bool] = _pid_alive) -> bool:
    """A RUNNING job is stalled only if its worker is gone and the heartbeat is stale."""
    if str(job.status or "") != "RUNNING":
        return False
    if worker_alive(job.id, claimed_worker_id(job), reg=reg, pid_alive=pid_alive):
        return False
    now_utc = now_utc or datetime.utcnow()
    hb = job.heartbeat_at
    return hb is None or (now_utc - hb) > timedelta(seconds=STALE_AFTER_SEC)


class HeartbeatPump:
    """Background thread that refreshes heartbeat_at while the worker runs."""

    def __init__(self, job_id: str, worker_id: str, session_factory, interval_sec: float = HEARTBEAT_INTERVAL_SEC):
        self.job_id = job_id
        self.worker_id = worker_id
        self.session_factory = session_factory
        self.interval = interval_sec
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)
        self.beats = 0

    def start(self) -> "HeartbeatPump":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def beat_once(self) -> bool:
        from backend.app.database.db import JobModel
        with self.session_factory() as db:
            job = db.query(JobModel).filter(JobModel.id == self.job_id).first()
            if not job or job.status != "RUNNING" or claimed_worker_id(job) != self.worker_id:
                return False  # job finished, failed, or was re-claimed: stop vouching for it
            job.heartbeat_at = datetime.utcnow()
            db.commit()
            self.beats += 1
            return True

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if not self.beat_once():
                    return
            except Exception:
                logger.warning("[WORKER] Heartbeat update failed for %s; will retry.", self.job_id, exc_info=True)
