"""Stage 3.32H: dashboard health monitoring with liveness/readiness separation and hysteresis.

Root cause of the false "unhealthy" detections: the monitor probed ``/api/dashboard/health`` - a heavyweight aggregate
(Windows scheduled-task queries, service and database checks, disk, archive) that routinely takes about 2 s - with a
2 s timeout, and treated any timeout as "down". This module separates:

* **liveness**  ``/api/health/live``  - answers from the event loop without touching the database;
* **readiness** ``/api/health/ready`` - a bounded database check (``SELECT 1``).

States (persisted to ``logs/health_monitor_state.json``; every probe is appended to ``health_monitor_events.jsonl``):

* ``HEALTHY``     - live and ready, within the latency budget;
* ``DEGRADED``    - live but slow, not ready, or still recovering (hysteresis);
* ``UNAVAILABLE`` - liveness failed fewer than ``failure_threshold`` consecutive times;
* ``UNHEALTHY``   - liveness failed ``failure_threshold`` or more consecutive times.

Restart policy: a restart is only ever *recommended* for UNHEALTHY **and** no dashboard process alive. A slow or
degraded dashboard is never restarted, and a live-but-unresponsive process is reported, not killed.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HEALTHY, DEGRADED, UNAVAILABLE, UNHEALTHY = "HEALTHY", "DEGRADED", "UNAVAILABLE", "UNHEALTHY"
STATES = (HEALTHY, DEGRADED, UNAVAILABLE, UNHEALTHY)
ACTION_NONE, ACTION_RESTART, ACTION_SUPPRESSED = "none", "restart", "restart_suppressed"

BASE_URL = "http://127.0.0.1:8000"
LIVENESS_PATH = "/api/health/live"
READINESS_PATH = "/api/health/ready"
LEGACY_HEALTH_PATH = "/api/dashboard/health"

EVENTS_FILE = "health_monitor_events.jsonl"
STATE_FILE = "health_monitor_state.json"
MAX_EVENTS_BYTES = 5 * 1024 * 1024
CONFIG_FILE_NAME = "health_monitor_config.json"

# (default, minimum, maximum) - configuration values are clamped so a bad file can never disable detection.
_BOUNDS: Dict[str, tuple] = {
    "interval_sec": (30.0, 10.0, 600.0),
    "liveness_timeout_sec": (5.0, 1.0, 30.0),
    "readiness_timeout_sec": (10.0, 2.0, 60.0),
    "slow_latency_ms": (2500.0, 200.0, 30000.0),
    "failure_threshold": (3, 2, 20),
    "recovery_threshold": (2, 1, 20),
    "max_restarts_per_hour": (3, 0, 6),
    "base_backoff_sec": (5.0, 1.0, 120.0),
}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class MonitorConfig:
    interval_sec: float = 30.0
    liveness_timeout_sec: float = 5.0
    readiness_timeout_sec: float = 10.0
    slow_latency_ms: float = 2500.0
    failure_threshold: int = 3
    recovery_threshold: int = 2
    max_restarts_per_hour: int = 3
    base_backoff_sec: float = 5.0
    base_url: str = BASE_URL
    source: str = "defaults"


def load_config(path: Optional[Path] = None) -> MonitorConfig:
    """Reads ``backend/data/health_monitor_config.json`` if present; unknown keys are ignored, values clamped."""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "data" / CONFIG_FILE_NAME
    values: Dict[str, Any] = {}
    source = "defaults"
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                values, source = raw, str(path.name)
    except Exception:
        source = "defaults (configuration file unreadable)"
    out: Dict[str, Any] = {}
    for key, (default, lo, hi) in _BOUNDS.items():
        v = values.get(key, default)
        try:
            v = type(default)(v)
        except (TypeError, ValueError):
            v = default
        out[key] = min(max(v, lo), hi)
    return MonitorConfig(**out, source=source)


@dataclass
class ProbeResult:
    ok: bool
    latency_ms: float
    reason: str                       # ok | timeout | connection_refused | http_<code> | not_ready | bad_payload | error:<T>
    status_code: Optional[int] = None
    detail: str = ""


def http_probe(url: str, timeout: float, *, expect: Optional[Callable[[Dict[str, Any]], bool]] = None,
               opener: Callable = urllib.request.urlopen) -> ProbeResult:
    """One GET with a hard timeout; measures latency and names the failure reason. Never raises."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-HealthMonitor/2.0"})
        with opener(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            body = resp.read(65536)
        ms = (time.perf_counter() - start) * 1000.0
        if code != 200:
            return ProbeResult(False, ms, f"http_{code}", code)
        if expect is not None:
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                return ProbeResult(False, ms, "bad_payload", code)
            if not expect(payload):
                return ProbeResult(False, ms, "not_ready", code, str(payload.get("reason", ""))[:200])
        return ProbeResult(True, ms, "ok", code)
    except urllib.error.HTTPError as exc:
        return ProbeResult(False, (time.perf_counter() - start) * 1000.0, f"http_{exc.code}", exc.code)
    except Exception as exc:  # timeouts surface as URLError(timeout) or socket.timeout depending on the phase
        ms = (time.perf_counter() - start) * 1000.0
        text = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}".lower()
        if "timed out" in text or "timeout" in text:
            reason = "timeout"
        elif "refused" in text or "10061" in text:
            reason = "connection_refused"
        else:
            reason = f"error:{type(exc).__name__}"
        return ProbeResult(False, ms, reason, None, str(exc)[:200])


def probe_liveness(cfg: MonitorConfig, opener: Callable = urllib.request.urlopen) -> ProbeResult:
    res = http_probe(cfg.base_url + LIVENESS_PATH, cfg.liveness_timeout_sec,
                     expect=lambda p: p.get("status") == "alive", opener=opener)
    if res.reason == "http_404":       # a dashboard still running pre-3.32H code: fall back to the legacy endpoint
        legacy = http_probe(cfg.base_url + LEGACY_HEALTH_PATH, cfg.readiness_timeout_sec, opener=opener)
        legacy.detail = "legacy health endpoint (dashboard predates /api/health/live)"
        return legacy
    return res


def probe_readiness(cfg: MonitorConfig, opener: Callable = urllib.request.urlopen) -> ProbeResult:
    res = http_probe(cfg.base_url + READINESS_PATH, cfg.readiness_timeout_sec,
                     expect=lambda p: p.get("status") == "ready", opener=opener)
    if res.reason == "http_404":
        return ProbeResult(True, res.latency_ms, "ok", 404, "readiness endpoint not deployed; liveness only")
    return res


@dataclass
class Decision:
    at_utc: str
    state: str
    previous_state: str
    action: str
    reason: str
    process_alive: bool
    live_ok: bool
    live_latency_ms: float
    live_reason: str
    ready_ok: Optional[bool]
    ready_latency_ms: Optional[float]
    ready_reason: Optional[str]
    consecutive_failures: int
    consecutive_successes: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["live_latency_ms"] = round(self.live_latency_ms, 1)
        if self.ready_latency_ms is not None:
            d["ready_latency_ms"] = round(self.ready_latency_ms, 1)
        return d


@dataclass
class HealthEvaluator:
    """Pure state machine: consecutive-failure threshold to go UNHEALTHY, recovery hysteresis to return HEALTHY."""
    cfg: MonitorConfig = field(default_factory=MonitorConfig)
    state: str = HEALTHY
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    def observe(self, live: ProbeResult, ready: Optional[ProbeResult], process_alive: bool) -> Decision:
        prev = self.state
        if live.ok:
            self.consecutive_failures = 0
            self.consecutive_successes += 1
            slow = live.latency_ms > self.cfg.slow_latency_ms or (
                ready is not None and ready.latency_ms > self.cfg.slow_latency_ms)
            if ready is not None and not ready.ok:
                state, reason = DEGRADED, f"live but not ready ({ready.reason})"
            elif slow:
                state, reason = DEGRADED, "live but slow (latency above the budget)"
            else:
                state, reason = HEALTHY, "live and ready"
            if state == HEALTHY and prev in (UNHEALTHY, UNAVAILABLE, DEGRADED) and \
                    self.consecutive_successes < self.cfg.recovery_threshold and prev != HEALTHY:
                state = DEGRADED
                reason = f"recovering ({self.consecutive_successes}/{self.cfg.recovery_threshold} healthy probes)"
            action = ACTION_NONE
        else:
            self.consecutive_successes = 0
            self.consecutive_failures += 1
            if self.consecutive_failures < self.cfg.failure_threshold:
                state = UNAVAILABLE
                reason = (f"liveness failed ({live.reason}); {self.consecutive_failures}/"
                          f"{self.cfg.failure_threshold} before unhealthy")
                action = ACTION_NONE
            else:
                state = UNHEALTHY
                if process_alive:
                    reason = (f"liveness failed {self.consecutive_failures} times ({live.reason}) but the dashboard "
                              "process is alive: not restarting (busy, starting or hung; needs operator attention)")
                    action = ACTION_NONE
                else:
                    reason = f"liveness failed {self.consecutive_failures} times ({live.reason}) and no dashboard process is running"
                    action = ACTION_RESTART
        self.state = state
        return Decision(_utc(), state, prev, action, reason, process_alive, live.ok, live.latency_ms, live.reason,
                        None if ready is None else ready.ok, None if ready is None else ready.latency_ms,
                        None if ready is None else ready.reason, self.consecutive_failures, self.consecutive_successes)


class HealthLedger:
    """Persists the current state and an append-only probe log (bounded: one rotated generation)."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = Path(logs_dir)
        self.events_path = self.logs_dir / EVENTS_FILE
        self.state_path = self.logs_dir / STATE_FILE
        self._lock = threading.Lock()

    def _append(self, rec: Dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                if self.events_path.exists() and self.events_path.stat().st_size > MAX_EVENTS_BYTES:
                    os.replace(self.events_path, self.events_path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def record_decision(self, d: Decision, cfg: MonitorConfig, restarts: List[Dict[str, Any]]) -> None:
        rec = {"type": "probe", **d.to_dict()}
        self._append(rec)
        state = {"updated_at_utc": d.at_utc, "pid": os.getpid(), "last": d.to_dict(),
                 "config": {k: getattr(cfg, k) for k in _BOUNDS} | {"source": cfg.source},
                 "recent_restarts": restarts[-10:]}
        tmp = self.state_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def record_restart(self, outcome: str, reason: str, backoff_sec: float) -> Dict[str, Any]:
        rec = {"type": "restart", "at_utc": _utc(), "outcome": outcome, "reason": reason, "backoff_sec": backoff_sec}
        self._append(rec)
        return rec


def read_state(logs_dir: Path) -> Dict[str, Any]:
    p = Path(logs_dir) / STATE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_events(logs_dir: Path, since_utc: Optional[str] = None, until_utc: Optional[str] = None,
                max_lines: int = 200000) -> List[Dict[str, Any]]:
    """Reads probe/restart events (current + rotated file), optionally within [since, until)."""
    out: List[Dict[str, Any]] = []
    base = Path(logs_dir) / EVENTS_FILE
    for p in (base.with_suffix(".jsonl.1"), base):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ts = rec.get("at_utc", "")
                    if (since_utc and ts < since_utc) or (until_utc and ts >= until_utc):
                        continue
                    out.append(rec)
                    if len(out) >= max_lines:
                        return out
        except OSError:
            continue
    return out


def summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Latency percentiles, state counts, restarts and failure reasons from persisted probe events."""
    probes = [e for e in events if e.get("type") == "probe"]
    restarts = [e for e in events if e.get("type") == "restart"]
    if not probes:
        return {"available": False, "reason": "no health-monitor probe events recorded in this period",
                "restarts": len(restarts)}
    lat = sorted(float(e.get("live_latency_ms") or 0.0) for e in probes if e.get("live_ok"))
    states: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    for e in probes:
        states[e.get("state", "?")] = states.get(e.get("state", "?"), 0) + 1
        if not e.get("live_ok"):
            reasons[e.get("live_reason", "?")] = reasons.get(e.get("live_reason", "?"), 0) + 1
        elif e.get("ready_ok") is False:
            key = f"not_ready:{e.get('ready_reason')}"
            reasons[key] = reasons.get(key, 0) + 1

    def pct(p: float) -> Optional[float]:
        if not lat:
            return None
        return round(lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))], 1)

    return {"available": True, "probes": len(probes), "states": states, "failure_reasons": reasons,
            "liveness_latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": round(lat[-1], 1) if lat else None},
            "availability_pct": round(100.0 * sum(1 for e in probes if e.get("live_ok")) / len(probes), 2),
            "restarts": len(restarts),
            "restart_outcomes": {o: sum(1 for r in restarts if r.get("outcome") == o)
                                 for o in sorted({r.get("outcome") for r in restarts})},
            "first_at_utc": probes[0].get("at_utc"), "last_at_utc": probes[-1].get("at_utc")}
