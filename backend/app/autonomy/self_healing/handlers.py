"""Stage 3.32D action handlers. They run ONLY inside ``ControlPlane.execute`` (approved, signed, gated, timed,
retry-bounded, audited) and follow the 3.32B handler contract: ``run`` / ``verify`` / ``compensate``.

Every run:
* loads its incident (unknown incident -> permanent failure, nothing done);
* acquires the incident's resource locks all-or-nothing (busy -> permanent failure, nothing done) and releases them;
* touches nothing in production: reads are bounded and inside the media roots; writes go only to
  ``<autonomy>/self_healing/{reports,staging}``.

``verify`` returns ``(ok, details)`` where details carry ``verdict``: VERIFIED, FAILED, or UNVERIFIED (the outcome
could not be independently confirmed). UNVERIFIED is never treated as success.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import PermanentActionError, redact
from backend.app.autonomy.self_healing import planner as P
from backend.app.autonomy.self_healing.services import get_services, inside, sha256_file
from backend.app.autonomy.self_healing.store import ResourceBusy, SelfHealingStore

WIN_PATH_RX = re.compile(r"[A-Za-z]:\\[^'\"\n<>|*?]{3,400}")
FILTER_RX = re.compile(r"\b(scale|pad)=(\d{1,5}):(\d{1,5})")
MEDIA_EXT = {".mp4", ".mov", ".webm", ".mkv", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".mp3", ".m4a"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
FILE_DIAGNOSTICS = ("collect_log_context", "inspect_media_metadata", "inspect_file_locks", "validate_ffmpeg_filter")


def _store(ctx) -> SelfHealingStore:
    return SelfHealingStore(ctx.control_plane.db_path)


def _sh_dir(ctx, sub: str) -> Path:
    return Path(ctx.autonomy_dir) / "self_healing" / sub


def _incident(ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
    inc = _store(ctx).get_incident(payload["incident_id"])
    if inc is None:
        raise PermanentActionError("unknown incident; nothing was done")
    return inc


def _require_job_idle(ctx, inc: Dict[str, Any]) -> None:
    """Live precondition for recovery actions (re-read now, not the plan snapshot): no worker is executing the job
    and the existing scheduler is not recovering it."""
    if not inc.get("job_id"):
        return
    from backend.app.autonomy.self_healing.evidence import SCHEDULER_RECOVERY_STATUSES, EvidenceSource
    job = EvidenceSource(ctx.intelligence, ctx.control_plane.db_path).job(inc["job_id"])
    if not job.get("found"):
        raise PermanentActionError("job state could not be read; recovery refused (fail closed)")
    if EvidenceSource.worker_active(job, ctx.now).get("active"):
        raise PermanentActionError(f"a worker is executing the job (status {job.get('status')}); recovery refused")
    if job.get("status") in SCHEDULER_RECOVERY_STATUSES:
        raise PermanentActionError(f"the existing scheduler is recovering the job (status {job.get('status')})")


class _Locked:
    def __init__(self, ctx, incident: Dict[str, Any], action_type: str, payload: Dict[str, Any]):
        spec = registry.REGISTRY[action_type]
        self.store, self.owner = _store(ctx), ctx.request_id
        self.keys = P.resource_keys(incident, action_type, payload)
        self.ttl = spec.timeout_seconds * (spec.retry_limit + 1) + 60
        self.now = ctx.now

    def __enter__(self):
        try:
            self.store.acquire_locks(self.keys, self.owner, self.ttl, self.now)
        except ResourceBusy as exc:
            raise PermanentActionError(str(exc)) from exc
        return self

    def __exit__(self, *exc):
        try:
            self.store.release_locks(self.owner)
        except Exception:
            pass
        return False


def _windows_paths(text: str) -> List[Path]:
    out: List[Path] = []
    for m in WIN_PATH_RX.finditer((text or "").replace("\\\\", "\\")):
        p = Path(m.group(0).rstrip(" .,;:)'\""))
        if p not in out:
            out.append(p)
    return out[:10]


def _evidence_text(incident: Dict[str, Any]) -> str:
    diag = incident.get("diagnosis") or {}
    ev = diag.get("evidence") or {}
    parts = [incident.get("message") or "", ev.get("error_message") or ""]
    parts += [ln.get("text") or "" for ln in ev.get("log_context") or []]
    return "\n".join(parts)


def _rel(path: Path, roots: List[Path]) -> str:
    for r in roots:
        try:
            return str(path.resolve().relative_to(r.resolve()))
        except (ValueError, OSError):
            continue
    return path.name


class SelfHealingDiagnosticHandler:
    action_type = "selfheal.diagnostic.run"

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = payload["diagnostic"]
        if name not in registry.SELF_HEALING_DIAGNOSTICS:
            raise PermanentActionError("diagnostic is not allow-listed")
        inc = _incident(ctx, payload)
        if name in FILE_DIAGNOSTICS:
            # These open job files. Opening a file on Windows can make the job's own rename / delete fail with
            # WinError 32, so they only run while no worker or scheduler recovery is using the job (re-read live).
            _require_job_idle(ctx, inc)
        svc = get_services()
        roots = svc.roots()
        with _Locked(ctx, inc, self.action_type, payload):
            data = getattr(self, "_" + name)(ctx, inc, svc, roots)
        return {"diagnostic": name, "incident_id": inc["id"], "status": "ok", "data": redact(data)}

    # -- diagnostics ------------------------------------------------------------------------------------------
    def _collect_log_context(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        ev = ((inc.get("diagnosis") or {}).get("evidence") or {})
        path = ev.get("event_log_path")
        if not path:
            return {"available": False, "note": "no production event log was recorded for this job"}
        tail = svc.read_tail(Path(path), roots)
        if not tail.get("available"):
            return {"available": False, "note": f"event log not readable: {tail.get('reason')}"}
        events, errors = [], []
        for line in tail["lines"]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            item = {"at": rec.get("timestamp"), "type": rec.get("event_type"), "stage": rec.get("stage"),
                    "message": str(rec.get("message") or "")[:240]}
            events.append(item)
            if "FAIL" in str(rec.get("event_type") or "").upper() or "ERROR" in str(rec.get("event_type") or "").upper():
                errors.append(item)
        return {"available": True, "log": _rel(Path(path), roots), "lines_read": len(tail["lines"]),
                "events": events[-20:], "error_events": errors[-10:]}

    def _inspect_media_metadata(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        files: List[Path] = [p for p in _windows_paths(_evidence_text(inc)) if p.suffix.lower() in MEDIA_EXT]
        job = inc.get("job_id")
        if job and roots:
            base = roots[0] / "productions" / job / "assets"
            if inside(base, roots) and base.is_dir():
                found = []
                for p in base.rglob("*"):
                    if p.is_file() and p.suffix.lower() in MEDIA_EXT:
                        found.append(p)
                    if len(found) >= 200:
                        break
                found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                files += [p for p in found if p not in files]
        results = []
        for p in files[:svc.extra.get("max_probe_files", 5)]:
            info = svc.image_info(p, roots) if p.suffix.lower() in IMAGE_EXT else svc.ffprobe(p, roots)
            results.append({"file": _rel(p, roots), **info})
        return {"files_considered": len(files), "inspected": results,
                "invalid": [r for r in results if not r.get("ok")],
                "odd_dimensions": [r["file"] for r in results if r.get("odd_dimensions")],
                "note": None if files else "no media files recorded for this incident"}

    def _check_disk_space(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        usage = svc.disk_usage(roots + [Path(ctx.autonomy_dir)])
        low = [u for u in usage if isinstance(u.get("free_gb"), (int, float)) and u["free_gb"] < 5]
        return {"volumes": usage, "low_space": low, "threshold_gb": 5}

    def _inspect_file_locks(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        paths = _windows_paths(_evidence_text(inc))
        results = [{"file": _rel(p, roots), **svc.probe_lock(p, roots)} for p in paths]
        return {"paths_in_evidence": len(paths), "checked": results,
                "currently_locked": [r["file"] for r in results if r.get("locked")],
                "note": None if paths else "no file path was recorded in the error"}

    def _validate_ffmpeg_filter(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        text = _evidence_text(inc)
        log = self._collect_log_context(ctx, inc, svc, roots)
        if log.get("available"):
            text += "\n" + "\n".join(e.get("message") or "" for e in log.get("events", []))
        scales = [(int(a), int(b)) for k, a, b in FILTER_RX.findall(text) if k == "scale"]
        pads = [(int(a), int(b)) for k, a, b in FILTER_RX.findall(text) if k == "pad"]
        problems = []
        for i, pad in enumerate(pads):
            sc = scales[i] if i < len(scales) else None
            if sc and (pad[0] < sc[0] or pad[1] < sc[1]):
                problems.append(f"pad {pad[0]}x{pad[1]} is smaller than scale {sc[0]}x{sc[1]}")
            if pad[0] % 2 or pad[1] % 2:
                problems.append(f"pad {pad[0]}x{pad[1]} has an odd dimension (yuv420p needs even sizes)")
        for sc in scales:
            if sc[0] <= 0 or sc[1] <= 0:
                problems.append(f"scale {sc[0]}x{sc[1]} is not positive")
        mechanism = "padded dimensions cannot be smaller than input" in text.lower()
        return {"filters_found": {"scale": scales[:10], "pad": pads[:10]}, "problems": problems,
                "error_states_pad_smaller_than_input": mechanism,
                "parameters_recorded": bool(scales or pads),
                "note": None if (scales or pads) else
                "the filter parameters were not recorded in the error or log, so they cannot be re-validated; the error "
                "text itself states the pad target was smaller than the scaled input"}

    def _generate_diagnostic_report(self, ctx, inc, svc, roots) -> Dict[str, Any]:
        body = {"kind": "self_healing_diagnostic_report", "request_id": ctx.request_id, "incident_id": inc["id"],
                "generated_at_utc": ctx.now.isoformat(), "signature": inc["signature"], "template": inc["template"],
                "failure_class": inc["failure_class"], "certainty": inc["certainty"],
                "classification": inc.get("classification"), "diagnosis": inc.get("diagnosis"),
                "plan": {k: (inc.get("plan") or {}).get(k) for k in ("summary", "checks", "operator_only")},
                "note": "Read-only diagnostic report. Root-cause statements follow the stated evidence status."}
        path = _sh_dir(ctx, "reports") / f"report_{inc['id']}_{ctx.request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(redact(body), indent=2, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
        return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    # -- contract -------------------------------------------------------------------------------------------
    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        ok = (isinstance(result, dict) and result.get("diagnostic") == payload["diagnostic"]
              and result.get("incident_id") == payload["incident_id"] and result.get("status") == "ok")
        detail = {"check": "diagnostic completed and returned its result", "ok": ok}
        if ok and payload["diagnostic"] == "generate_diagnostic_report":
            data = result.get("data") or {}
            path = Path(str(data.get("path", "")))
            good = (inside(path, [_sh_dir(ctx, "reports")]) and path.is_file()
                    and sha256_file(path) == data.get("sha256"))
            ok = bool(good)
            detail = {"check": "report file exists inside the reports folder and matches its SHA-256", "ok": ok}
        detail["verdict"] = "VERIFIED" if ok else "FAILED"
        return ok, detail

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        data = (result or {}).get("data") or {}
        path = Path(str(data.get("path", "")))
        if payload.get("diagnostic") == "generate_diagnostic_report" and path.name and inside(path, [_sh_dir(ctx, "reports")]):
            try:
                path.unlink()
                return {"removed": True}
            except FileNotFoundError:
                return {"removed": False, "reason": "already absent"}
        return {"removed": False, "reason": "read-only; nothing to undo"}


class ProviderReadOnlyRetryHandler:
    action_type = "selfheal.provider.retry_readonly"

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload["provider"] not in registry.SELF_HEALING_PROVIDERS:
            raise PermanentActionError("provider is not allow-listed")
        inc = _incident(ctx, payload)
        expected = P.clean_query(((inc.get("diagnosis") or {}).get("evidence") or {}).get("topic_title"))
        if not expected or payload["query"] != expected:
            raise PermanentActionError("the query must be the topic recorded in the incident evidence")
        _require_job_idle(ctx, inc)
        with _Locked(ctx, inc, self.action_type, payload):
            res = get_services().provider_search(payload["provider"], payload["query"], 3)
        return {"provider": payload["provider"], "incident_id": inc["id"], **res}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        ok = bool(isinstance(result, dict) and result.get("ok") is True and isinstance(result.get("count"), int)
                  and result.get("provider") == payload["provider"])
        return ok, {"check": "provider answered and the response parsed into media records", "ok": ok,
                    "verdict": "VERIFIED" if ok else "FAILED",
                    "records": (result or {}).get("count"), "error": (result or {}).get("error")}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return {"removed": False, "reason": "read-only search; nothing to undo"}


class StagedAssetReacquireHandler:
    action_type = "selfheal.asset.reacquire_staged"

    def _dir(self, ctx) -> Path:
        return _sh_dir(ctx, "staging") / ctx.request_id

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = payload["asset_url"]
        if not P.allowed_url(url):
            raise PermanentActionError("URL host is not allow-listed")
        inc = _incident(ctx, payload)
        if url not in P.urls_in_evidence(_evidence_text(inc) + "\n" + json.dumps(inc.get("diagnosis") or {})):
            raise PermanentActionError("the URL is not recorded in the incident evidence")
        _require_job_idle(ctx, inc)
        with _Locked(ctx, inc, self.action_type, payload):
            res = get_services().download_to_staging(url, self._dir(ctx))
        return {"incident_id": inc["id"], "staging_dir": str(self._dir(ctx)), **res}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        if not isinstance(result, dict) or not result.get("ok") or not result.get("path"):
            return False, {"check": "staged file downloaded", "ok": False, "verdict": "FAILED",
                           "error": (result or {}).get("error")}
        path = Path(result["path"])
        if not inside(path, [self._dir(ctx)]) or not path.is_file():
            return False, {"check": "staged file exists inside the staging folder", "ok": False, "verdict": "FAILED"}
        if sha256_file(path) != result.get("sha256"):
            return False, {"check": "SHA-256 matches on re-read", "ok": False, "verdict": "FAILED"}
        svc = get_services()
        info = svc.image_info(path, [self._dir(ctx)]) if path.suffix.lower() in IMAGE_EXT else svc.ffprobe(path, [self._dir(ctx)])
        if not info.get("ok"):
            reason = str(info.get("reason") or "")
            verdict = "UNVERIFIED" if "not available" in reason or "timed out" in reason else "FAILED"
            return False, {"check": "decoded metadata", "ok": False, "verdict": verdict, "reason": reason}
        good = bool(info.get("width") and info.get("height"))
        return good, {"check": "file exists, SHA-256 matches, decodes with positive dimensions", "ok": good,
                      "verdict": "VERIFIED" if good else "FAILED", "width": info.get("width"), "height": info.get("height")}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        d = self._dir(ctx)
        removed = 0
        if d.is_dir() and inside(d, [_sh_dir(ctx, "staging")]):
            for p in d.iterdir():
                if p.is_file():
                    p.unlink()
                    removed += 1
            try:
                d.rmdir()
            except OSError:
                pass
        return {"removed_files": removed}


SELF_HEALING_HANDLERS: Dict[str, Any] = {
    SelfHealingDiagnosticHandler.action_type: SelfHealingDiagnosticHandler(),
    ProviderReadOnlyRetryHandler.action_type: ProviderReadOnlyRetryHandler(),
    StagedAssetReacquireHandler.action_type: StagedAssetReacquireHandler(),
}
