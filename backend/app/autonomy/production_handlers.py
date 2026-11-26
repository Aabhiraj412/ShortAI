"""Stage 3.36 handlers for the two production coordination actions (run only through ControlPlane.execute).

Both refuse to run anywhere except inside the scheduler daemon's Autopilot controller while it holds production
ownership: an approved request executed from the dashboard process (or any other process) fails permanently, so
no second renderer can ever be started from outside the daemon. The work itself is the EXISTING production code
(``run_cycle_generation`` / ``dispatch_eligible_recoveries``) awaited on the daemon's own event loop.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Tuple

from backend.app.autonomy.control_plane import PermanentActionError


def _controller_or_refuse(ctx, payload: Dict[str, Any]):
    from backend.app.autonomy import production_controller as pc
    ctrl = pc.runtime()
    if ctrl is None or ctrl.loop is None or ctrl.loop.is_closed():
        raise PermanentActionError("production dispatch runs only inside the scheduler daemon's Autopilot controller")
    token = ctrl.owner_token
    if not token or not ctrl.ownership.token_valid(token):
        raise PermanentActionError("the Autopilot controller does not hold production ownership")
    return ctrl, token


def _await_on(ctrl, coro, timeout: float) -> Any:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is ctrl.loop:                     # never block the loop on itself
        coro.close()
        raise PermanentActionError("dispatch must not be awaited on the controller's own event loop thread")
    return asyncio.run_coroutine_threadsafe(coro, ctrl.loop).result(timeout=timeout)


def _verify(payload: Dict[str, Any], result: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    from backend.app.autonomy import production_controller as pc
    ctrl = pc.runtime()
    job = (ctrl._job_state if ctrl else pc._default_job_state)(payload["job_id"])
    try:
        from backend.app.scheduler import worker_registry
        worker_live = worker_registry.registry.is_active(payload["job_id"])
    except Exception:
        worker_live = False
    status = str((result or {}).get("result_status") or "").upper()
    safe = (job.get("status") in pc.SAFE_JOB_STATES) or status in pc.SAFE_RESULT_STATES
    same_job = (result or {}).get("job_id") in (None, payload["job_id"])
    ok = bool(safe and not worker_live and same_job)
    return ok, {"check": "job reached a safe state and no worker for it is still running", "ok": ok,
                "job_status": job.get("status"), "result_status": status or None, "worker_still_running": worker_live,
                "same_job": same_job}


class ScheduledCycleDispatchHandler:
    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        ctrl, token = _controller_or_refuse(ctx, payload)
        from backend.app.scheduler.schedule_profiles import IST_TZ, get_active_profile
        cycle = next((c for c in get_active_profile().cycles if c.cycle_id == payload["cycle_id"]), None)
        if cycle is None:
            raise PermanentActionError(f"{payload['cycle_id']} is not a cycle of the active schedule")
        now = datetime.now(IST_TZ)
        if cycle.format_job_id(now) != payload["job_id"]:
            raise PermanentActionError(f"{payload['job_id']} is no longer the current occurrence of {payload['cycle_id']}")
        start, _t, _d, _p = cycle.calculate_datetimes(now)
        if cycle.is_past_recovery_window(start, now):
            raise PermanentActionError(f"{payload['job_id']}: the recovery window has passed")
        res = _await_on(ctrl, ctrl._dispatch_scheduled(payload, token), timeout=3 * 3600)
        res = dict(res or {})
        return {"job_id": res.get("job_id") or payload["job_id"], "result_status": res.get("status"),
                "reason": res.get("reason") or res.get("error")}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        return _verify(payload, result)

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return {"compensated": False, "reason": "production outcomes are handled by the existing retry policy"}


class RecoveryDispatchHandler:
    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        ctrl, token = _controller_or_refuse(ctx, payload)
        res = _await_on(ctrl, ctrl._dispatch_recovery(payload, token), timeout=3 * 3600)
        res = dict(res or {})
        return {"job_id": payload["job_id"], "result_status": res.get("status"), "dispatched": res.get("dispatched")}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        return _verify(payload, result)

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return {"compensated": False, "reason": "bounded by the existing retry policy"}


PRODUCTION_HANDLERS = {
    "production.scheduled_cycle.dispatch": ScheduledCycleDispatchHandler(),
    "production.recovery.dispatch": RecoveryDispatchHandler(),
}
