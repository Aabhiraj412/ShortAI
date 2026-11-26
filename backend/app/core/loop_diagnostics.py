"""Remediation WS1: lightweight event-loop health diagnostics for the dashboard process.

Two always-on, low-cost instruments (no third-party tools, no extra process):

* **Loop lag**: an asyncio task asks to wake every ``LAG_INTERVAL`` s and records how late it actually ran. A busy
  or blocked event loop shows up directly as lag; this is what makes liveness probes time out.
* **Loop-thread stack sampler**: a daemon thread reads ``sys._current_frames()`` for the event-loop thread every
  ``SAMPLE_INTERVAL`` s and counts the innermost application frames. When the loop is busy, the top entries name
  the code that keeps it busy; an idle loop samples as the selector/proactor wait.

Both keep bounded memory (a fixed-size window and a capped counter) and cost well under 1 % of a core.
"""
from __future__ import annotations

import asyncio
import collections
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

LAG_INTERVAL = 1.0
SAMPLE_INTERVAL = 0.5
WINDOW = 600                 # samples kept (10 min of lag at 1 s, 5 min of stacks at 0.5 s)
MAX_SIGNATURES = 200
IDLE_MARKERS = ("GetQueuedCompletionStatus", "select", "_poll", "base_events.py:_run_once")

_lock = threading.Lock()
_lags: Deque[Tuple[float, float]] = collections.deque(maxlen=WINDOW)   # (epoch, lag seconds)
_stacks: "collections.Counter[str]" = collections.Counter()
_stack_window: Deque[str] = collections.deque(maxlen=WINDOW)
_state: Dict[str, Any] = {"started": False, "loop_thread_id": None, "loop_class": None, "started_at": None}


def _signature(frame) -> str:
    """Innermost 3 frames, application frames preferred, as 'file:function' entries."""
    parts: List[str] = []
    f = frame
    app: List[str] = []
    while f is not None and len(parts) < 40:
        co = f.f_code
        name = co.co_filename.replace("\\", "/")
        short = name.rsplit("/", 1)[-1]
        entry = f"{short}:{co.co_name}:{f.f_lineno}"
        parts.append(entry)
        if "/backend/app/" in name and "loop_diagnostics" not in name:
            app.append(entry)
        f = f.f_back
    chosen = app[:3] if app else parts[:3]
    return " < ".join(chosen)


def _sampler(thread_id: int) -> None:
    while True:
        time.sleep(SAMPLE_INTERVAL)
        frame = sys._current_frames().get(thread_id)
        if frame is None:
            return
        sig = _signature(frame)
        with _lock:
            if len(_stack_window) == _stack_window.maxlen:
                old = _stack_window[0]
                _stacks[old] -= 1
                if _stacks[old] <= 0:
                    del _stacks[old]
            _stack_window.append(sig)
            if sig in _stacks or len(_stacks) < MAX_SIGNATURES:
                _stacks[sig] += 1


async def _lag_task() -> None:
    loop = asyncio.get_running_loop()
    while True:
        t = loop.time()
        await asyncio.sleep(LAG_INTERVAL)
        lag = max(0.0, loop.time() - t - LAG_INTERVAL)
        with _lock:
            _lags.append((time.time(), lag))


def start() -> None:
    """Call once from inside the running event loop (application startup)."""
    if _state["started"]:
        return
    loop = asyncio.get_running_loop()
    _state.update(started=True, loop_thread_id=threading.get_ident(), loop_class=type(loop).__name__,
                  started_at=time.time())
    loop.create_task(_lag_task(), name="loop-lag-monitor")
    threading.Thread(target=_sampler, args=(threading.get_ident(),), name="loop-stack-sampler", daemon=True).start()


def snapshot(window_seconds: float = 300.0) -> Dict[str, Any]:
    now = time.time()
    with _lock:
        lags = sorted(l for (t, l) in _lags if now - t <= window_seconds)
        stacks = _stacks.most_common(12)
        total = len(_stack_window)
    out: Dict[str, Any] = {"started": _state["started"], "loop_class": _state["loop_class"],
                           "uptime_seconds": round(now - _state["started_at"], 1) if _state["started_at"] else None}
    if lags:
        out["lag_seconds"] = {"samples": len(lags), "p50": round(lags[len(lags) // 2], 3),
                              "p95": round(lags[min(len(lags) - 1, int(0.95 * len(lags)))], 3),
                              "max": round(lags[-1], 3)}
    busy = sum(c for s, c in stacks if not any(m in s for m in IDLE_MARKERS))
    out["loop_thread_samples"] = total
    out["loop_thread_busy_share_top12"] = round(busy / total, 3) if total else None
    out["loop_thread_top_stacks"] = [{"stack": s, "share": round(c / total, 3)} for s, c in stacks] if total else []
    return out


def reset_for_tests() -> None:
    with _lock:
        _lags.clear()
        _stacks.clear()
        _stack_window.clear()
    _state.update(started=False, loop_thread_id=None, loop_class=None, started_at=None)
