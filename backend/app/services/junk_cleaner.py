"""
Junk Cleaner: auditable, policy-driven cleanup of generated production assets (Stage 3.30).

Audit result that motivated this module (see docs/STAGE_3_30_STATUS.md):
* media_lifecycle_manager only ever ran `daily_report()` (read-only, from the daily summary email). Its
  `weekly_temp_quarantine()` / `monthly_audit()` were never scheduled; no automatic cleanup has ever run.
* The existing retention policy (backend/data/media_retention_policy.json) says `default_mode: dry_run` and
  `automatic_deletion: false`; deliverables are permanent; intermediates may be QUARANTINED (moved, reversible)
  after `regeneratable_intermediates` days. This module follows that policy and invents no new retention period.

Every decision uses real state: persistent_jobs (active / recoverable), publishing_ledger (uploaded?),
thumbnail_state (thumbnail verified?). Unknown files are never touched. Live mode only quarantines, only files
the policy makes eligible, and only when the policy allows automatic deletion or an operator confirms explicitly.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"RUNNING", "RETRYING", "QUEUED", "PAUSED", "INTERRUPTED", "RETRY_SCHEDULED", "PENDING",
                   "HANDOFF_PENDING", "STARTING"}
TERMINAL_STATUSES = {"COMPLETED", "RECOVERY_WINDOW_EXPIRED", "MISSED", "ABORTED_PRE_RESET", "CANCELLED"}
# A FAILED job can be re-evaluated for retry inside its recovery window (at most 4 h for the current schedule).
# Treat it as recoverable for 24 h after its last update: conservative, never shorter than a recovery window.
FAILED_RECOVERABLE_HOURS = 24
# BLOCKED_QC is terminal for the retry policy (scheduler/retry_policy.TERMINAL_STATUSES) but was treated as
# "non-terminal" here, so a QC-blocked job's intermediates were kept forever. It now follows the FAILED rule.
FAILED_LIKE = {"FAILED", "BLOCKED_QC"}
# The publishing ledger records "uploaded_private" for a video already on YouTube (private / scheduled); it was
# missing, so those jobs counted as "publishing still pending" indefinitely.
UPLOADED_STATES = {"uploaded", "uploaded_private", "uploaded_public", "publication_scheduled", "published", "scheduled"}
QUARANTINE_SUBDIR = ("quarantine", "junk_cleaner")

# category -> (description, policy retention key or None = never eligible)
CATEGORIES: Dict[str, Dict[str, Any]] = {
    "final_mp4": {"label": "Final rendered MP4", "retention": None},
    "intermediate_clips": {"label": "Intermediate scene clips / segments", "retention": "regeneratable_intermediates"},
    "final_audio": {"label": "Final voice-over / mixed audio", "retention": None},
    "temp_audio": {"label": "Temporary TTS / narration segments", "retention": "regeneratable_intermediates"},
    "final_thumbnail": {"label": "Final thumbnail (design + YouTube artifact)", "retention": None},
    "thumbnail_variants": {"label": "Temporary thumbnail variants", "retention": "regeneratable_intermediates"},
    "downloaded_media": {"label": "Downloaded photos / illustrations / clips", "retention": None},
    "scene_graphics": {"label": "Scene assets and generated graphics", "retention": "regeneratable_intermediates"},
    "render_temp": {"label": "FFmpeg temporary files", "retention": "temporary_caches"},
    "browser_artifacts": {"label": "Browser / rendering artifacts (html, js, fonts)", "retention": "regeneratable_intermediates"},
    "manifests_metadata": {"label": "Manifests, reports, scripts, provenance", "retention": None},
    "cache": {"label": "Cache entries", "retention": "temporary_caches"},
    "test_renders": {"label": "Test / validation renders", "retention": None},
    "source_media": {"label": "Source / user-provided media (branding)", "retention": None},
    "quarantine": {"label": "Quarantined files (recovery area)", "retention": None},
    "unknown": {"label": "Unclassified", "retention": None},
}
PROTECTED_DIRS = {"manifests", "metadata", "reports", "audit", "scripts", "research", "storyboards",
                  "visual_timeline"}
PROTECTED_SUFFIXES = {".db", ".sqlite", ".json", ".jsonl", ".pkl", ".env", ".log", ".md"}


def _project_root() -> Path:
    from backend.app.core.paths import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def load_policy(root: Optional[Path] = None) -> Dict[str, Any]:
    path = (root or _project_root()) / "backend" / "data" / "media_retention_policy.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"policy_version": None, "default_mode": "dry_run", "automatic_deletion": False,
                "retention_days": {}, "missing": True}


def classify(rel_parts: List[str], name: str, job_id: Optional[str]) -> str:
    """Category of a file from its location under media/ (rel_parts excludes the file name)."""
    if not rel_parts:
        return "unknown"
    top = rel_parts[0]
    ext = os.path.splitext(name)[1].lower()
    if top == "quarantine":
        return "quarantine"
    if top in ("test_renders",):
        return "test_renders"
    if top == "source":
        return "source_media"
    if top in ("asset_cache",):
        return "cache"
    if top in ("temp_render",):
        return "render_temp"
    if top != "productions" or len(rel_parts) < 2:
        return "unknown"
    sub = rel_parts[2] if len(rel_parts) > 2 else ""
    if ext in PROTECTED_SUFFIXES or sub in PROTECTED_DIRS or sub == "":
        return "manifests_metadata"
    if sub == "video":
        return "final_mp4" if ext == ".mp4" else "unknown"
    if sub == "thumbnails":
        if len(rel_parts) > 3 and rel_parts[3] == "variants":
            return "thumbnail_variants"
        return "final_thumbnail" if ext in (".png", ".jpg", ".jpeg", ".webp") else "unknown"
    if sub == "audio":
        return "final_audio"
    if sub == "narration":
        return "temp_audio"
    if sub == "assets":
        return "downloaded_media"
    if sub in ("lemo_film", "render_work", "pop_info_film"):
        if ext == ".mp4":
            return "intermediate_clips"
        if ext in (".html", ".js", ".css", ".ttf", ".otf", ".woff", ".woff2"):
            return "browser_artifacts"
        if ext in (".mp3", ".wav", ".m4a"):
            return "temp_audio"
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            return "scene_graphics"
        return "unknown"
    return "unknown"


class JunkCleaner:
    def __init__(self, *, root: Optional[Path] = None, db_path: Optional[str] = None,
                 now: Optional[datetime] = None, policy: Optional[Dict[str, Any]] = None):
        self.root = Path(root) if root else _project_root()
        self.media = self.root / "media"
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        self.db_path = db_path
        self.now = now or datetime.now(timezone.utc)
        self.policy = policy if policy is not None else load_policy(self.root)
        self.runs_dir = self.root / "logs" / "junk_cleaner"

    # ---- state from the database (read-only) -------------------------------------------------------------
    def _state(self) -> Dict[str, Any]:
        jobs: Dict[str, Dict[str, Any]] = {}
        pubs: List[Dict[str, Any]] = []
        thumbs: Dict[str, Dict[str, Any]] = {}
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
        except sqlite3.Error:
            return {"jobs": jobs, "pubs": pubs, "thumbs": thumbs, "db_ok": False}
        try:
            try:
                for jid, status, updated in conn.execute("SELECT id, status, updated_at FROM persistent_jobs"):
                    jobs[jid] = {"status": (status or "").upper(), "updated_at": updated}
            except sqlite3.Error:
                pass
            try:
                for lid, vpath, tpath, vid, st in conn.execute(
                        "SELECT id, video_path, thumbnail_path, youtube_video_id, upload_status FROM publishing_ledger"):
                    pubs.append({"id": lid, "video_path": vpath or "", "thumbnail_path": tpath or "",
                                 "video_id": vid, "upload_status": (st or "").lower()})
            except sqlite3.Error:
                pass
            try:
                for lid, vstatus in conn.execute("SELECT ledger_id, verify_status FROM thumbnail_state"):
                    thumbs[str(lid)] = {"verify_status": vstatus}
            except sqlite3.Error:
                pass
        finally:
            conn.close()
        return {"jobs": jobs, "pubs": pubs, "thumbs": thumbs, "db_ok": True}

    def _job_protection(self, job_id: Optional[str], st: Dict[str, Any]) -> Optional[str]:
        """Reason a job's assets must be kept, or None."""
        if not job_id:
            return None
        job = st["jobs"].get(job_id)
        if job is None:
            return "job not in database (unknown state)"
        status = job["status"]
        if status in ACTIVE_STATUSES:
            return f"active job ({status})"
        if status in FAILED_LIKE:
            try:
                upd = datetime.fromisoformat(str(job["updated_at"]).replace("Z", "")).replace(tzinfo=timezone.utc)
            except ValueError:
                return "failed job with unreadable timestamp"
            if self.now - upd < timedelta(hours=FAILED_RECOVERABLE_HOURS):
                return "failed job may still be recoverable"
            return None
        if status not in TERMINAL_STATUSES:
            return f"non-terminal status {status}"
        return None

    def _publication(self, job_id: str, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for p in st["pubs"]:
            if job_id and (job_id in p["video_path"] or job_id in p["thumbnail_path"]):
                return p
        return None

    # ---- run -------------------------------------------------------------------------------------------
    def run(self, *, trigger: str, live: bool = False, confirm: bool = False) -> Dict[str, Any]:
        from backend.app.core.dashboard_startup import ServerLock
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
        mode = "live" if live else "dry_run"
        record: Dict[str, Any] = {"run_id": run_id, "trigger": trigger, "mode": mode,
                                  "started_at_utc": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "policy_version": self.policy.get("policy_version"),
                                  "policy_automatic_deletion": bool(self.policy.get("automatic_deletion")),
                                  "scanned": 0, "scanned_bytes": 0, "eligible": 0, "eligible_bytes": 0,
                                  "deleted": 0, "bytes_reclaimed": 0, "skipped": 0, "skip_reasons": {},
                                  "errors": [], "categories": {}, "eligible_files": []}
        if live and not (confirm or self.policy.get("automatic_deletion")):
            record.update(outcome="refused", errors=["live cleanup needs automatic_deletion in the policy or an "
                                                     "explicit operator confirmation"])
            return self._persist(record)
        lock = ServerLock(self.root / "logs" / "junk_cleaner.lock")
        if not lock.acquire():
            record.update(outcome="skipped_concurrent_run")
            return self._persist(record)
        try:
            record["disk_free_before"] = shutil.disk_usage(str(self.root)).free
            st = self._state()
            if not st["db_ok"]:
                record.update(outcome="failed", errors=["database unavailable: nothing evaluated"])
                return self._persist(record)
            active = sorted(j for j, v in st["jobs"].items() if v["status"] in ACTIVE_STATUSES)
            if live and active:
                # Shared caches and FFmpeg temp files carry no job id, so a live run while production is active could
                # remove something a running job is about to read. Never move anything while a job is active.
                record.update(outcome="refused", errors=[f"production job(s) active: {', '.join(active[:5])}; live "
                                                         f"cleanup runs only when no job is active"])
                return self._persist(record)
            days = self.policy.get("retention_days") or {}
            for dirpath, _dirs, files in os.walk(self.media):
                rel = Path(dirpath).relative_to(self.media).parts
                job_id = rel[1] if len(rel) > 1 and rel[0] == "productions" else None
                for name in files:
                    path = Path(dirpath) / name
                    try:
                        size = path.stat().st_size
                        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    except OSError as exc:
                        record["errors"].append(f"stat failed: {type(exc).__name__}")
                        continue
                    cat = classify(list(rel), name, job_id)
                    c = record["categories"].setdefault(cat, {"label": CATEGORIES[cat]["label"], "scanned": 0,
                                                               "scanned_bytes": 0, "eligible": 0, "eligible_bytes": 0,
                                                               "deleted": 0, "bytes_reclaimed": 0, "skip_reasons": {}})
                    record["scanned"] += 1
                    record["scanned_bytes"] += size
                    c["scanned"] += 1
                    c["scanned_bytes"] += size
                    reason = self._skip_reason(cat, job_id, mtime, days, st)
                    if reason:
                        record["skipped"] += 1
                        record["skip_reasons"][reason] = record["skip_reasons"].get(reason, 0) + 1
                        c["skip_reasons"][reason] = c["skip_reasons"].get(reason, 0) + 1
                        continue
                    record["eligible"] += 1
                    record["eligible_bytes"] += size
                    c["eligible"] += 1
                    c["eligible_bytes"] += size
                    if len(record["eligible_files"]) < 500:
                        record["eligible_files"].append({"path": str(path.relative_to(self.root)), "bytes": size,
                                                         "category": cat})
                    if live:
                        try:
                            self._quarantine(path, run_id, size, mtime)
                            record["deleted"] += 1
                            record["bytes_reclaimed"] += size
                            c["deleted"] += 1
                            c["bytes_reclaimed"] += size
                        except OSError as exc:
                            record["errors"].append(f"quarantine failed for {path.name}: {type(exc).__name__}")
            record["disk_free_after"] = shutil.disk_usage(str(self.root)).free
            record["outcome"] = "completed_with_errors" if record["errors"] else "completed"
        finally:
            lock.release()
        return self._persist(record)

    def _skip_reason(self, cat: str, job_id: Optional[str], mtime: datetime, days: Dict[str, Any],
                     st: Dict[str, Any]) -> Optional[str]:
        key = CATEGORIES[cat]["retention"]
        if key is None:
            return {"final_mp4": "final deliverable (policy: permanent)",
                    "final_thumbnail": "final deliverable (policy: permanent)",
                    "final_audio": "final audio (policy: permanent deliverable)",
                    "downloaded_media": "source binary with provenance (no per-asset cache policy)",
                    "manifests_metadata": "protected: manifests / provenance / reports",
                    "test_renders": "test renders: retained (not covered by policy)",
                    "source_media": "protected: source / user-provided media",
                    "quarantine": "quarantine recovery area",
                    "unknown": "unknown file: never touched automatically"}[cat]
        protection = self._job_protection(job_id, st)
        if protection:
            return protection
        retention = days.get(key)
        if retention is None:
            return f"policy has no retention period for {key}"
        if self.now - mtime < timedelta(days=int(retention)):
            return f"younger than {key} retention ({retention} days)"
        if job_id:
            job = st["jobs"].get(job_id, {})
            if job.get("status") == "COMPLETED":
                pub = self._publication(job_id, st)
                if cat in ("intermediate_clips", "browser_artifacts", "scene_graphics", "temp_audio"):
                    # the final render must exist and be integrity-verified (COMPLETED implies the gate passed)
                    if not any((self.media / "productions" / job_id / "video").glob("*.mp4")):
                        return "final MP4 missing: intermediates kept"
                if cat == "thumbnail_variants":
                    if not pub or not pub.get("video_id"):
                        return "thumbnail not uploaded yet"
                    if (st["thumbs"].get(str(pub["id"])) or {}).get("verify_status") != "verified":
                        return "thumbnail upload not remotely verified"
                if pub and pub["upload_status"] not in UPLOADED_STATES and cat != "render_temp":
                    return "publishing still pending"
        return None

    def quarantine_dir(self, run_id: str) -> Path:
        return self.media.joinpath(*QUARANTINE_SUBDIR) / run_id

    def _quarantine(self, path: Path, run_id: str, size: Optional[int] = None, mtime: Optional[datetime] = None) -> None:
        """Reversible move. Every moved file gets a manifest line (the run record lists at most 500 files), so
        ``restore`` can always put the whole run back."""
        qdir = self.quarantine_dir(run_id)
        rel = path.relative_to(self.media)
        dest = qdir / "files" / rel
        if dest.exists():
            raise FileExistsError(str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        entry = {"original": str(rel).replace("\\", "/"), "quarantined": str(dest.relative_to(qdir)).replace("\\", "/"),
                 "bytes": size, "mtime_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ") if mtime else None,
                 "moved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        with open(qdir / "manifest.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def restore(self, run_id: str) -> Dict[str, Any]:
        """Move every file of one live run back to its original location. Never overwrites: a file whose original
        path exists again is left in quarantine and reported as a conflict."""
        if not run_id or any(c in run_id for c in ("/", "\\", ".", ":")):
            raise ValueError("invalid run id")
        qdir = self.quarantine_dir(run_id)
        out: Dict[str, Any] = {"run_id": run_id, "restored": 0, "restored_bytes": 0, "conflicts": [], "missing": [],
                               "errors": []}
        try:
            lines = (qdir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            out["errors"].append("no quarantine manifest for this run")
            return out
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                out["errors"].append("unreadable manifest line")
                continue
            src, dst = qdir / e["quarantined"], self.media / e["original"]
            if not src.resolve().is_relative_to(qdir.resolve()) or not dst.resolve().is_relative_to(self.media.resolve()):
                out["errors"].append(f"path outside allowed area: {e['original']}")
                continue
            if not src.exists():
                out["missing"].append(e["original"])
                continue
            if dst.exists():
                out["conflicts"].append(e["original"])
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                out["restored"] += 1
                out["restored_bytes"] += int(e.get("bytes") or 0)
            except OSError as exc:
                out["errors"].append(f"{e['original']}: {type(exc).__name__}")
        out["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            with open(self.runs_dir / "restores.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(out) + "\n")
        except OSError:
            pass
        logger.info("[JUNK CLEANER] restore %s: %d restored, %d conflicts, %d missing", run_id, out["restored"],
                    len(out["conflicts"]), len(out["missing"]))
        return out

    # ---- persistence ------------------------------------------------------------------------------------
    def _persist(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            (self.runs_dir / f"run_{record['run_id']}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            summary = {k: v for k, v in record.items() if k != "eligible_files"}
            with open(self.runs_dir / "runs.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(summary) + "\n")
        except OSError as exc:
            logger.warning("[JUNK CLEANER] could not persist run record: %s", type(exc).__name__)
        logger.info("[JUNK CLEANER] run %s (%s, %s): scanned %d, eligible %d (%.1f MB), quarantined %d, outcome %s",
                    record["run_id"], record["mode"], record["trigger"], record["scanned"], record["eligible"],
                    record["eligible_bytes"] / 1e6, record["deleted"], record.get("outcome"))
        return record


def status(root: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Dashboard view. Healthy only with a recent successful run, never merely because it is enabled."""
    root = Path(root) if root else _project_root()
    now = now or datetime.now(timezone.utc)
    policy = load_policy(root)
    runs: List[Dict[str, Any]] = []
    try:
        for line in (root / "logs" / "junk_cleaner" / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                runs.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    last = runs[-1] if runs else None
    if not last:
        health = "NO_RUNS_RECORDED"
    else:
        try:
            age = now - datetime.strptime(last["finished_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            age = timedelta(days=999)
        if last.get("outcome") not in ("completed",):
            health = "LAST_RUN_" + str(last.get("outcome") or "unknown").upper()
        elif age > timedelta(hours=26):
            health = "STALE"
        else:
            health = "HEALTHY"
    live_runs = [r for r in runs if r.get("mode") == "live"]
    return {
        "enabled": True,
        "mode": "live" if policy.get("automatic_deletion") else "dry_run",
        "policy": {k: policy.get(k) for k in ("policy_version", "default_mode", "automatic_deletion", "retention_days")},
        "schedule": "daily dry-run at 03:30 IST (scheduler daemon); live cleanup never automatic",
        "health": health,
        "last_run": last,
        "runs_recorded": len(runs),
        "cumulative_bytes_reclaimed": sum(int(r.get("bytes_reclaimed") or 0) for r in live_runs),
        "cumulative_note": "sum of persisted live-run records only" if live_runs else "no live run has ever been recorded",
    }
