"""Stage 3.32B action handlers: the only code an approved autonomous action can run.

Each handler has ``run`` (do the work), ``verify`` (confirm the post-condition) and ``compensate`` (undo its own
artifact when verification fails). All handlers are local and bounded:

* files are written only inside ``<data>/autonomy/`` (reports, community_drafts, operator_inbox.jsonl);
* production data is only read, through Stage 3.32A Operational Intelligence (read-only connection);
* no network call, no Gemini call, no YouTube call, no scheduler / worker / configuration / OAuth access.

A handler can only be registered for a permitted action type; ``ControlPlane`` refuses anything else at start.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from backend.app.autonomy import registry


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> Tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _remove_own_file(ctx, result: Dict[str, Any], subdir: str) -> Dict[str, Any]:
    path = Path(str((result or {}).get("path", "")))
    base = ctx.autonomy_dir / subdir
    if not result or not path.name or not _inside(base, path):
        return {"removed": False, "reason": "no file of this execution to remove"}
    try:
        path.unlink()
        return {"removed": True}
    except FileNotFoundError:
        return {"removed": False, "reason": "already absent"}


class OperationalReportHandler:
    subdir = "reports"

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        svc = ctx.intelligence()
        summary = svc.daily_summary(payload.get("date"))
        body: Dict[str, Any] = {"kind": "operational_report", "request_id": ctx.request_id,
                                "generated_at_utc": ctx.now.isoformat(), "daily_summary": summary}
        if payload.get("days"):
            body["window"] = {"days": payload["days"],
                              "production_health": svc.production_health(days=payload["days"]),
                              "failure_intelligence": svc.failure_intelligence(days=payload["days"])}
        path = ctx.autonomy_dir / self.subdir / f"ops_report_{summary.get('date', 'unknown')}_{ctx.request_id}.json"
        sha, size = _atomic_write_json(path, body)
        return {"path": str(path), "sha256": sha, "bytes": size, "date": summary.get("date"), "health": summary.get("health")}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        path = Path(result["path"])
        if not _inside(ctx.autonomy_dir / self.subdir, path) or not path.is_file():
            return False, {"check": "report file exists", "ok": False}
        raw = path.read_bytes()
        ok = hashlib.sha256(raw).hexdigest() == result["sha256"] and json.loads(raw).get("request_id") == ctx.request_id
        return ok, {"check": "report file exists, parses and matches its SHA-256", "ok": ok}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return _remove_own_file(ctx, result, self.subdir)


class RecommendationHandler:
    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"title": payload["title"], "text": payload["text"], "evidence_ref": payload.get("evidence_ref"),
                "note": "Recommendation for operator review only; nothing was executed."}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        ok = result.get("title") == payload["title"] and result.get("text") == payload["text"]
        return ok, {"check": "recommendation recorded as approved", "ok": ok}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return {"removed": False, "reason": "nothing to undo"}


class ReadOnlyDiagnosticHandler:
    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        name, days = payload["diagnostic"], payload.get("days") or 1
        if name not in registry.DIAGNOSTICS:
            raise ValueError("diagnostic is not allow-listed")
        if name == "ops_overview":
            ph = ctx.intelligence().production_health(days=days)
            data = {"status": ph.get("status"), "metrics": ph.get("metrics")}
        elif name == "ops_daily_summary":
            ds = ctx.intelligence().daily_summary(None)
            data = {"date": ds.get("date"), "health": ds.get("health"), "anomalies": ds.get("anomalies", [])[:20]}
        elif name == "audit_chain_verify":
            data = ctx.control_plane.verify_audit_chain()
        else:
            data = ctx.control_plane.kill_switches()
        return {"diagnostic": name, "status": "ok", "data": data}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        ok = result.get("diagnostic") == payload["diagnostic"] and result.get("status") == "ok"
        return ok, {"check": "diagnostic returned a result", "ok": ok}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return {"removed": False, "reason": "read-only; nothing to undo"}


class OperatorNotifyHandler:
    filename = "operator_inbox.jsonl"

    def _append(self, ctx, record: Dict[str, Any]) -> None:
        ctx.autonomy_dir.mkdir(parents=True, exist_ok=True)
        with open(ctx.autonomy_dir / self.filename, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = {"request_id": ctx.request_id, "ts_utc": ctx.now.isoformat(), "subject": payload["subject"],
                  "message": payload["message"], "severity": payload.get("severity") or "info"}
        self._append(ctx, record)
        return {"delivered_to": "dashboard_inbox", "subject": payload["subject"], "severity": record["severity"]}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        path = ctx.autonomy_dir / self.filename
        ok = False
        if path.is_file():
            with open(path, "rb") as fh:
                fh.seek(max(0, path.stat().st_size - 65536))
                for line in fh.read().decode("utf-8", "replace").splitlines():
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ok = ok or (rec.get("request_id") == ctx.request_id and rec.get("subject") == payload["subject"])
        return ok, {"check": "inbox contains the notification", "ok": ok}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        self._append(ctx, {"request_id": ctx.request_id, "ts_utc": ctx.now.isoformat(), "retracted": True})
        return {"retracted": True}


class CommunityDraftHandler:
    subdir = "community_drafts"

    def run(self, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
        draft = {"kind": "youtube_community_post_draft", "status": "DRAFT", "publishable": False,
                 "request_id": ctx.request_id, "created_at_utc": ctx.now.isoformat(), "text": payload["text"],
                 "topic": payload.get("topic"), "source_job_id": payload.get("source_job_id"),
                 "note": "Local draft only. Stage 3.32B never publishes Community posts."}
        path = ctx.autonomy_dir / self.subdir / f"draft_{ctx.request_id}.json"
        sha, size = _atomic_write_json(path, draft)
        return {"path": str(path), "sha256": sha, "bytes": size, "status": "DRAFT", "publishable": False}

    def verify(self, ctx, payload, result) -> Tuple[bool, Dict[str, Any]]:
        path = Path(result["path"])
        if not _inside(ctx.autonomy_dir / self.subdir, path) or not path.is_file():
            return False, {"check": "draft file exists", "ok": False}
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = data.get("status") == "DRAFT" and data.get("publishable") is False and data.get("text") == payload["text"]
        return ok, {"check": "draft is DRAFT, not publishable, with the approved text", "ok": ok}

    def compensate(self, ctx, payload, result) -> Dict[str, Any]:
        return _remove_own_file(ctx, result, self.subdir)


HANDLERS: Dict[str, Any] = {
    "report.operational.generate": OperationalReportHandler(),
    "recommendation.record": RecommendationHandler(),
    "diagnostic.readonly.run": ReadOnlyDiagnosticHandler(),
    "operator.notify": OperatorNotifyHandler(),
    "community.draft.prepare": CommunityDraftHandler(),
}

# Import-time guard: a handler can never exist for an unknown or prohibited action type.
for _action_type in HANDLERS:
    registry.require_permitted(_action_type)
assert set(HANDLERS) == {s.action_type for s in registry.PERMITTED}
