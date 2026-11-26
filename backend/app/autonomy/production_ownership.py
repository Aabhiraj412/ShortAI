"""Stage 3.36: production ownership record shared by the scheduler and the Autopilot controller.

Exactly one execution path owns automatic production at a time:

* ``SCHEDULER`` (default, and the fail-safe): the existing APScheduler cron jobs and the recovery loop dispatch
  production exactly as before Stage 3.36.
* ``AUTOPILOT``: the Autopilot controller (running inside the same scheduler daemon process) dispatches. The cron jobs
  still fire on the unchanged 24-cycle schedule, but ``run_cycle_generation`` hands the fired occurrence to the
  controller (``record_deferred``) instead of dispatching it, and the recovery loop leaves recoveries to the
  controller.

The record is ONE row, changed only by compare-and-swap on ``version``, so two claimers can never both win. A claim is
honoured only while its owner process is alive and, for the daemon's own process, while the controller heartbeat is
fresh. Anything unreadable or inconsistent means "the scheduler owns production" (production continues on the proven
path; the in-process dispatch guard and the worker registry still prevent a second renderer).

This module has no heavy imports: the scheduler calls it on every cron firing.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

OWNER_SCHEDULER = "SCHEDULER"
OWNER_AUTOPILOT = "AUTOPILOT"
CONTROLLER_HEARTBEAT_FRESH = timedelta(minutes=10)   # same-process claim lapses if the controller stops ticking
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
MAX_DEFERRED = 48

SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_production_ownership (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner TEXT NOT NULL,
    owner_token TEXT,
    owner_pid INTEGER,
    acquired_at_utc TEXT,
    heartbeat_at_utc TEXT,
    deferred_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL
);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(ts or "", TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def default_db_path() -> str:
    from backend.app.core.paths import DATABASE_PATH
    return str(DATABASE_PATH)


def _pid_alive(pid: int) -> bool:
    from backend.app.services.deployment_diagnostics import is_pid_alive
    return bool(is_pid_alive(int(pid)))


class OwnershipStore:
    def __init__(self, db_path: Optional[str] = None, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 pid_alive: Optional[Callable[[int], bool]] = None, pid: Optional[int] = None):
        self.db_path = str(db_path or default_db_path())
        self.clock = clock
        self.pid_alive = pid_alive or _pid_alive
        self.pid = pid if pid is not None else os.getpid()

    # -- low level ------------------------------------------------------------------------------------------
    def _connect(self, write: bool) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")
        mode = "rw" if write else "ro"
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5.0,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def ensure(self) -> None:
        conn = self._connect(True)
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO autopilot_production_ownership (id, owner, version, updated_at_utc) "
                         "VALUES (1, ?, 0, ?)", (OWNER_SCHEDULER, _ts(self.clock())))
        finally:
            conn.close()

    def read(self) -> Dict[str, Any]:
        """The stored row (no liveness interpretation). Missing table/row -> scheduler owns."""
        try:
            conn = self._connect(False)
        except Exception:
            return {"owner": OWNER_SCHEDULER, "version": 0, "deferred": [], "stored": False}
        try:
            row = conn.execute("SELECT * FROM autopilot_production_ownership WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            conn.close()
        if row is None:
            return {"owner": OWNER_SCHEDULER, "version": 0, "deferred": [], "stored": False}
        d = dict(row)
        try:
            d["deferred"] = json.loads(d.pop("deferred_json") or "[]")
        except ValueError:
            d["deferred"] = []
        d["stored"] = True
        return d

    def effective(self, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Who really owns production now, with the reason. Fail safe: SCHEDULER."""
        row = row or self.read()
        if row.get("owner") != OWNER_AUTOPILOT:
            return {"owner": OWNER_SCHEDULER, "reason": "scheduler owns production", "row": row}
        pid = int(row.get("owner_pid") or 0)
        try:
            alive = pid == self.pid or self.pid_alive(pid)
        except Exception:
            alive = False
        if not alive:
            return {"owner": OWNER_SCHEDULER, "row": row, "lapsed": True,
                    "reason": f"Autopilot claim lapsed: owner process {pid} is not running"}
        if pid == self.pid:
            hb = _parse(row.get("heartbeat_at_utc"))
            if hb is None or self.clock() - hb > CONTROLLER_HEARTBEAT_FRESH:
                return {"owner": OWNER_SCHEDULER, "row": row, "lapsed": True,
                        "reason": "Autopilot claim lapsed: the controller in this process stopped ticking"}
        return {"owner": OWNER_AUTOPILOT, "reason": f"Autopilot controller (process {pid}) owns production", "row": row}

    def autopilot_owns(self) -> bool:
        try:
            return self.effective()["owner"] == OWNER_AUTOPILOT
        except Exception:
            return False

    def token_valid(self, token: Optional[str]) -> bool:
        """A dispatch presented by the controller: the token must be the live claim held by THIS process."""
        if not token:
            return False
        try:
            eff = self.effective()
        except Exception:
            return False
        row = eff["row"]
        return (eff["owner"] == OWNER_AUTOPILOT and int(row.get("owner_pid") or 0) == self.pid
                and secrets.compare_digest(str(row.get("owner_token") or ""), str(token)))

    # -- compare-and-swap changes ---------------------------------------------------------------------------
    def _cas(self, expected_version: int, **values: Any) -> bool:
        cols = ", ".join(f"{k}=?" for k in values)
        conn = self._connect(True)
        try:
            cur = conn.execute(f"UPDATE autopilot_production_ownership SET {cols}, version=version+1, updated_at_utc=? "
                               f"WHERE id=1 AND version=?", (*values.values(), _ts(self.clock()), expected_version))
            return cur.rowcount == 1
        finally:
            conn.close()

    def acquire(self) -> Optional[str]:
        """Claim production for the controller in this process. Returns the new token, or None when another live
        owner holds it or the row changed underneath (lost race)."""
        self.ensure()
        row = self.read()
        eff = self.effective(row)
        if eff["owner"] == OWNER_AUTOPILOT and int(row.get("owner_pid") or 0) != self.pid:
            return None
        token = secrets.token_hex(16)
        now = _ts(self.clock())
        ok = self._cas(int(row.get("version") or 0), owner=OWNER_AUTOPILOT, owner_token=token, owner_pid=self.pid,
                       acquired_at_utc=now, heartbeat_at_utc=now)
        return token if ok else None

    def heartbeat(self, token: str) -> bool:
        row = self.read()
        if row.get("owner") != OWNER_AUTOPILOT or row.get("owner_token") != token:
            return False
        return self._cas(int(row["version"]), heartbeat_at_utc=_ts(self.clock()))

    def release(self, token: Optional[str] = None, *, force_lapsed: bool = False) -> Dict[str, Any]:
        """Hand production back to the scheduler. With a token: only that claim. With force_lapsed: only a claim
        whose owner is provably gone (never a live owner's claim). Returns the deferred occurrences to replay."""
        self.ensure()
        row = self.read()
        if row.get("owner") != OWNER_AUTOPILOT:
            return {"released": False, "reason": "scheduler already owns production", "deferred": row.get("deferred", [])}
        if token is not None and row.get("owner_token") != token:
            return {"released": False, "reason": "claim token does not match", "deferred": []}
        if token is None:
            if not force_lapsed or not self.effective(row).get("lapsed"):
                return {"released": False, "reason": "claim is held by a live owner", "deferred": []}
        ok = self._cas(int(row["version"]), owner=OWNER_SCHEDULER, owner_token=None, owner_pid=None,
                       heartbeat_at_utc=None, deferred_json="[]")
        return {"released": ok, "reason": "released" if ok else "lost race", "deferred": row.get("deferred", []) if ok else []}

    # -- deferred occurrences (cron firings handed to the controller) ----------------------------------------
    def record_deferred(self, entry: Dict[str, Any]) -> bool:
        for _ in range(3):
            row = self.read()
            if row.get("owner") != OWNER_AUTOPILOT:
                return False
            items: List[Dict[str, Any]] = [d for d in row.get("deferred", []) if d.get("job_id") != entry.get("job_id")]
            items.append(entry)
            if self._cas(int(row["version"]), deferred_json=json.dumps(items[-MAX_DEFERRED:], sort_keys=True)):
                return True
        return False

    def remove_deferred(self, job_id: str) -> None:
        for _ in range(3):
            row = self.read()
            items = [d for d in row.get("deferred", []) if d.get("job_id") != job_id]
            if len(items) == len(row.get("deferred", [])):
                return
            if self._cas(int(row["version"]), deferred_json=json.dumps(items, sort_keys=True)):
                return


def get_store() -> OwnershipStore:
    return OwnershipStore()
