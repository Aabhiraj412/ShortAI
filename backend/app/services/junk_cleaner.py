"""
Junk Cleaner / Storage Lifecycle Manager: auditable, policy-driven cleanup of generated production assets.

Stage 3.30 origin: no automatic cleanup had ever run; deliverables were permanent and intermediates could only be
QUARANTINED (moved, reversible). Storage lifecycle upgrade (2026-11-28): the retention period of every category is
configurable (``backend/data/storage_lifecycle.json``, defaults in ``RETENTION_DEFAULTS``; ``null`` = protected),
final deliverables can expire once their YouTube publication is confirmed, and test renders follow their outcome.

Safety (unchanged or stricter):
* DRY-RUN is the default; the daily 03:30 IST job is a dry run. A live run never starts automatically: it needs an
  explicit confirmation AND the id of a successful dry run from the last hour (``authorization``).
* Live mode only QUARANTINES (atomic rename inside the media root, manifest line per file, ``restore(run_id)``).
* Unknown files, manifests / provenance / reports, user media, quarantine and unpublished deliverables are never
  eligible. Active jobs, recoverable failures, open localizations and queued coordinator jobs protect their files.
* Final deliverables (MP4, thumbnail, audio) need: a YouTube video id, upload confirmed, the publication confirmed
  public by the reconciliation job (``uploaded_public``), the thumbnail remotely verified (and its Shorts portrait
  backfill done) and, in a live run, a fresh YouTube check (processing SUCCEEDED, public, owned by the verified
  English channel) immediately before the move.
* Symlinks / junctions are never followed or moved; every path must resolve inside the media root; a file whose
  identity (size, mtime, file id) changed since the scan is skipped.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat as _stat
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"RUNNING", "RETRYING", "QUEUED", "PAUSED", "INTERRUPTED", "RETRY_SCHEDULED", "PENDING",
                   "HANDOFF_PENDING", "STARTING"}
TERMINAL_STATUSES = {"COMPLETED", "RECOVERY_WINDOW_EXPIRED", "MISSED", "ABORTED_PRE_RESET", "CANCELLED"}
FAILED_RECOVERABLE_HOURS = 24
FAILED_LIKE = {"FAILED", "BLOCKED_QC"}
UPLOADED_STATES = {"uploaded", "uploaded_private", "uploaded_public", "publication_scheduled", "published", "scheduled"}
PUBLISHED_VERIFIED = "uploaded_public"          # set only after the reconciliation job read the video back as public
QUARANTINE_SUBDIR = ("quarantine", "junk_cleaner")
LOCALIZATION_DONE = {"QC_PASSED", "QC_REJECTED"}
TEST_ACTIVE_HOURS = 6
AUTHORIZATION_MAX_AGE = timedelta(hours=1)
FORECAST_DAYS = 30

CATEGORIES: Dict[str, Dict[str, Any]] = {
    "final_mp4": {"label": "Final rendered MP4 (published and verified)", "temporary": False},
    "final_thumbnail": {"label": "Final thumbnail (uploaded and verified)", "temporary": False},
    "final_audio": {"label": "Final audio (video verified)", "temporary": False},
    "intermediate_clips": {"label": "Intermediate scene clips / segments", "temporary": True},
    "temp_audio": {"label": "Temporary TTS / narration segments", "temporary": True},
    "cache": {"label": "Cache entries", "temporary": True},
    "thumbnail_variants": {"label": "Temporary thumbnail variants", "temporary": True},
    "scene_graphics": {"label": "Scene assets and generated graphics", "temporary": True},
    "test_renders": {"label": "Test / validation renders", "temporary": True},
    "render_temp": {"label": "Rendering temporary files", "temporary": True},
    "browser_artifacts": {"label": "Browser / rendering temporary artifacts", "temporary": True},
    "downloaded_media": {"label": "Downloaded source media", "temporary": False},
    "source_media": {"label": "User-provided media / branding", "temporary": False},
    "manifests_metadata": {"label": "Manifests, provenance and reports", "temporary": False},
    "quarantine": {"label": "Quarantined files (recovery area)", "temporary": False},
    "unknown": {"label": "Unknown / unclassified", "temporary": False},
}
# Configurable retention (days; None = protected). Defaults from the storage lifecycle policy (2026-11-28).
RETENTION_DEFAULTS: Dict[str, Optional[int]] = {
    "final_mp4": 30, "final_thumbnail": 14, "final_audio": 7, "intermediate_clips": 7, "temp_audio": 7, "cache": 3,
    "thumbnail_variants": 7, "scene_graphics": 7, "test_renders_success": 3, "test_renders_failed": 7,
    "render_temp": 7, "browser_artifacts": 7, "downloaded_media": None,
}
RETENTION_LABELS = {k: (CATEGORIES.get(k) or {}).get("label") for k in RETENTION_DEFAULTS}
RETENTION_LABELS.update(test_renders_success="Successful test / validation renders",
                        test_renders_failed="Failed test renders")
FIXED = {"source_media": "permanent (user media / branding)", "manifests_metadata": "permanent (manifests, provenance, reports)",
         "quarantine": "protected (recovery area)", "unknown": "never deleted automatically",
         "final_mp4_unverified": "protected (unpublished / unverified deliverable)",
         "test_renders_active": "protected (active test)", "test_renders_unresolved": "protected (unresolved investigation)"}
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


# ---- storage lifecycle configuration ------------------------------------------------------------------------------
def lifecycle_path(root: Optional[Path] = None) -> Path:
    return (root or _project_root()) / "backend" / "data" / "storage_lifecycle.json"


def validate_lifecycle(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg, dict) or set(cfg) - {"retention_days", "protected_tests"}:
        raise ValueError("storage lifecycle accepts retention_days and protected_tests only")
    days = dict(RETENTION_DEFAULTS)
    for k, v in (cfg.get("retention_days") or {}).items():
        if k not in RETENTION_DEFAULTS:
            raise ValueError(f"{k} has a fixed policy and cannot be configured" if k in FIXED or k in CATEGORIES
                             else f"unknown retention category {k}")
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 3650):
            raise ValueError(f"{k}: retention must be 1 to 3650 days, or null for protected")
        days[k] = v
    tests = cfg.get("protected_tests") or []
    if not isinstance(tests, list) or len(tests) > 200 or any(
            not isinstance(t, str) or not t or any(c in t for c in "/\\:") or t in (".", "..") for t in tests):
        raise ValueError("protected_tests must be a list of test folder names")
    return {"retention_days": days, "protected_tests": sorted(set(tests))}


def load_lifecycle(root: Optional[Path] = None) -> Dict[str, Any]:
    try:
        raw = json.loads(lifecycle_path(root).read_text(encoding="utf-8"))
        return validate_lifecycle({k: raw.get(k) for k in ("retention_days", "protected_tests") if k in raw})
    except (OSError, ValueError):
        return validate_lifecycle({})


def save_lifecycle(cfg: Dict[str, Any], root: Optional[Path] = None, *, actor: str = "dashboard") -> Dict[str, Any]:
    """Validated, merged per category, written atomically, every change appended to the lifecycle history."""
    if not isinstance(cfg, dict) or set(cfg) - {"retention_days", "protected_tests"}:
        raise ValueError("storage lifecycle accepts retention_days and protected_tests only")
    cur = load_lifecycle(root)
    merged = {"retention_days": dict(cur["retention_days"], **(cfg.get("retention_days") or {})),
              "protected_tests": cfg.get("protected_tests", cur["protected_tests"])}
    new = validate_lifecycle(merged)
    path = lifecycle_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(new, updated_at_utc=_utc_now(), updated_by=actor), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    hist = (root or _project_root()) / "logs" / "junk_cleaner" / "lifecycle_changes.jsonl"
    try:
        hist.parent.mkdir(parents=True, exist_ok=True)
        with open(hist, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at_utc": _utc_now(), "by": actor, "from": cur, "to": new}) + "\n")
    except OSError:
        pass
    return new


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- classification -------------------------------------------------------------------------------------------------
def classify(rel_parts: List[str], name: str, job_id: Optional[str]) -> str:
    """Category of a file from its location under media/ (rel_parts excludes the file name)."""
    if not rel_parts:
        return "unknown"
    top = rel_parts[0]
    ext = os.path.splitext(name)[1].lower()
    if top == "quarantine":
        return "quarantine"
    if top == "test_renders":
        return "manifests_metadata" if ext in PROTECTED_SUFFIXES else "test_renders"
    if top == "source":
        return "source_media"
    if top == "asset_cache":
        return "cache"
    if top == "temp_render":
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


def _is_link_or_reparse(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if _stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity(path: Path) -> Optional[Tuple[int, int, int]]:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0))


RemoteVerifier = Callable[[List[str]], Dict[str, Dict[str, Any]]]


class JunkCleaner:
    def __init__(self, *, root: Optional[Path] = None, db_path: Optional[str] = None,
                 now: Optional[datetime] = None, policy: Optional[Dict[str, Any]] = None,
                 lifecycle: Optional[Dict[str, Any]] = None, remote_verifier: Optional[RemoteVerifier] = None):
        self.root = Path(root) if root else _project_root()
        self.media = self.root / "media"
        if db_path is None:
            from backend.app.core.paths import DATABASE_PATH
            db_path = str(DATABASE_PATH)
        self.db_path = db_path
        self.now = now or datetime.now(timezone.utc)
        self.policy = policy if policy is not None else load_policy(self.root)
        self.lifecycle = validate_lifecycle(lifecycle) if lifecycle is not None else load_lifecycle(self.root)
        self.retention = self.lifecycle["retention_days"]
        self.runs_dir = self.root / "logs" / "junk_cleaner"
        self._remote_verifier = remote_verifier
        self._tests: Dict[str, Dict[str, Any]] = {}
        self._remote: Dict[str, Dict[str, Any]] = {}
        self._fresh: Optional[Tuple[float, Dict[str, Any]]] = None

    # ---- state from the database (read-only) -------------------------------------------------------------
    def _state(self) -> Dict[str, Any]:
        jobs: Dict[str, Dict[str, Any]] = {}
        pubs: List[Dict[str, Any]] = []
        thumbs: Dict[str, Dict[str, Any]] = {}
        locs: Dict[str, str] = {}
        queued: List[str] = []
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
        except sqlite3.Error:
            return {"jobs": jobs, "pubs": pubs, "thumbs": thumbs, "locs": locs, "queued": queued, "db_ok": False}
        try:
            try:
                for jid, status, updated in conn.execute("SELECT id, status, updated_at FROM persistent_jobs"):
                    jobs[jid] = {"status": (status or "").upper(), "updated_at": updated}
            except sqlite3.Error:
                return {"jobs": jobs, "pubs": pubs, "thumbs": thumbs, "locs": locs, "queued": queued, "db_ok": False}
            try:
                for lid, vpath, tpath, vid, st in conn.execute(
                        "SELECT id, video_path, thumbnail_path, youtube_video_id, upload_status FROM publishing_ledger"):
                    pubs.append({"id": lid, "video_path": vpath or "", "thumbnail_path": tpath or "",
                                 "video_id": vid, "upload_status": (st or "").lower()})
            except sqlite3.Error:
                pass
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(thumbnail_state)")}
                sel = "ledger_id, verify_status" + (", artifact_path" if "artifact_path" in cols else ", NULL")
                for lid, vstatus, art in conn.execute(f"SELECT {sel} FROM thumbnail_state"):
                    thumbs[str(lid)] = {"verify_status": vstatus, "artifact_path": art or ""}
            except sqlite3.Error:
                pass
            try:   # open Hindi localizations read the English production folder (plan, scripts, assets)
                for src, status in conn.execute("SELECT source_job_id, status FROM content_localizations"):
                    if (status or "") not in LOCALIZATION_DONE:
                        locs[src] = status
            except sqlite3.Error:
                pass
            try:
                queued = [r[0] for r in conn.execute("SELECT id FROM production_queue_jobs WHERE state IN "
                                                      "('QUEUED','RUNNING','WAITING','CLAIMED')")]
            except sqlite3.Error:
                queued = []
        finally:
            conn.close()
        return {"jobs": jobs, "pubs": pubs, "thumbs": thumbs, "locs": locs, "queued": queued, "db_ok": True}

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
        if job_id in st.get("locs", {}):
            return f"Hindi localization still uses this production ({st['locs'][job_id]})"
        if any(job_id in q for q in st.get("queued", [])):
            return "a queued coordinator job references this production"
        return None

    def _publication(self, job_id: str, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for p in st["pubs"]:
            if job_id and (job_id in p["video_path"] or job_id in p["thumbnail_path"]):
                return p
        return None

    # ---- test renders ----------------------------------------------------------------------------------------
    def _test_state(self, folder: str) -> Dict[str, Any]:
        """Outcome of one test folder (top level under media/test_renders): active / unresolved / baseline /
        success / failed / unknown. Read once per folder per run."""
        if folder in self._tests:
            return self._tests[folder]
        base = self.media / "test_renders" / folder
        out: Dict[str, Any] = {"state": "unknown", "why": "no TEST_STATUS.json and no result report"}
        newest = None
        for dp, _d, fs in os.walk(base):
            for f in fs:
                try:
                    m = os.lstat(os.path.join(dp, f)).st_mtime
                except OSError:
                    continue
                newest = m if newest is None or m > newest else newest
        marker = {}
        try:
            marker = json.loads((base / "TEST_STATUS.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        status = str(marker.get("status") or "").lower()
        if folder in self.lifecycle["protected_tests"] or status == "unresolved" or (base / "UNRESOLVED").exists():
            out = {"state": "unresolved", "why": "test evidence for an unresolved investigation"}
        elif status == "active" or (newest and self.now.timestamp() - newest < TEST_ACTIVE_HOURS * 3600):
            out = {"state": "active", "why": f"active test (changed in the last {TEST_ACTIVE_HOURS} h)"}
        elif folder.startswith("baseline"):
            out = {"state": "baseline", "why": "QC baseline / reference evidence"}
        elif status in ("passed", "success", "succeeded"):
            out = {"state": "success", "why": "TEST_STATUS.json: passed"}
        elif status in ("failed", "failure"):
            out = {"state": "failed", "why": "TEST_STATUS.json: failed"}
        else:
            verdicts = []
            for p in sorted(base.glob("*.json"))[:20]:
                try:
                    if p.stat().st_size > 1_000_000:
                        continue
                    verdicts += _verdicts(json.loads(p.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            if verdicts:
                out = ({"state": "failed", "why": "a result report records a failure"} if False in verdicts else
                       {"state": "success", "why": "result reports record success"})
        out["newest_utc"] = datetime.fromtimestamp(newest, timezone.utc) if newest else None
        self._tests[folder] = out
        return out

    # ---- decision -----------------------------------------------------------------------------------------------
    def decide(self, cat: str, rel: List[str], job_id: Optional[str], mtime: datetime,
               st: Dict[str, Any]) -> Tuple[str, str, Optional[datetime]]:
        """(status, reason, eligible_on). status: eligible | eligible_pending_remote | waiting_age | protected.
        waiting_age means only the retention age is missing (forecast); everything else protects the file."""
        if cat in FIXED:
            return "protected", FIXED[cat], None
        if cat == "test_renders":
            t = self._test_state(rel[1] if len(rel) > 1 else "")
            if t["state"] in ("active", "unresolved", "baseline", "unknown"):
                return "protected", t["why"], None
            key = "test_renders_success" if t["state"] == "success" else "test_renders_failed"
            age_from = t["newest_utc"] or mtime
            return self._age(key, max(age_from, mtime))
        key = cat
        if self.retention.get(key) is None:
            return "protected", f"{CATEGORIES[cat]['label']}: protected by the retention policy", None
        protection = self._job_protection(job_id, st)
        if protection:
            return "protected", protection, None
        remote_needed = False
        if job_id:
            job = st["jobs"].get(job_id, {})
            pub = self._publication(job_id, st)
            if cat in ("final_mp4", "final_thumbnail", "final_audio"):
                reason = self._deliverable_reason(cat, job, pub, st)
                if reason:
                    return "protected", reason, None
                remote_needed = True
            elif job.get("status") == "COMPLETED":
                if cat in ("intermediate_clips", "browser_artifacts", "scene_graphics", "temp_audio"):
                    if not any((self.media / "productions" / job_id / "video").glob("*.mp4")):
                        return "protected", "final MP4 missing: intermediates kept", None
                if cat == "thumbnail_variants":
                    if not pub or not pub.get("video_id"):
                        return "protected", "thumbnail not uploaded yet", None
                    if (st["thumbs"].get(str(pub["id"])) or {}).get("verify_status") != "verified":
                        return "protected", "thumbnail upload not remotely verified", None
                if pub and pub["upload_status"] not in UPLOADED_STATES:
                    return "protected", "publishing still pending", None
        status, reason, on = self._age(key, mtime)
        if status == "eligible" and remote_needed:
            v = self._remote.get(self._publication(job_id, st)["video_id"]) if self._remote else None
            if v is None:
                return "eligible_pending_remote", "eligible on stored evidence; YouTube re-verified before any move", None
            if not v.get("ok"):
                return "protected", f"YouTube verification failed: {v.get('reason')}", None
        return status, reason, on

    def _age(self, key: str, mtime: datetime) -> Tuple[str, str, Optional[datetime]]:
        days = self.retention.get(key)
        if days is None:
            return "protected", f"{key}: protected by the retention policy", None
        on = mtime + timedelta(days=int(days))
        if self.now < on:
            return "waiting_age", f"younger than {key} retention ({days} days)", on
        return "eligible", f"older than {key} retention ({days} days)", None

    def _deliverable_reason(self, cat: str, job: Dict[str, Any], pub: Optional[Dict[str, Any]],
                            st: Dict[str, Any]) -> Optional[str]:
        if job.get("status") != "COMPLETED":
            return f"final deliverable of a job that is not COMPLETED ({job.get('status') or 'unknown'})"
        if not pub:
            return "final deliverable without a publication record (unpublished)"
        if not pub.get("video_id"):
            return "final deliverable not uploaded (no YouTube video id)"
        if pub["upload_status"] != PUBLISHED_VERIFIED:
            return f"publication not confirmed public ({pub['upload_status'] or 'unknown'}): protected"
        if cat == "final_thumbnail":
            ts = st["thumbs"].get(str(pub["id"])) or {}
            if ts.get("verify_status") != "verified":
                return "thumbnail not remotely verified"
            if not str(ts.get("artifact_path") or "").endswith("_shorts_1280x720.jpg"):
                return "Shorts portrait-thumbnail backfill still needs this thumbnail"
        return None

    def _verify_remote(self, st: Dict[str, Any]) -> None:
        """Live runs only: one fresh YouTube check of every candidate deliverable's video (batched, ~1 unit / 50)."""
        ids = sorted({p["video_id"] for p in st["pubs"] if p.get("video_id") and p["upload_status"] == PUBLISHED_VERIFIED})
        if not ids:
            return
        verifier = self._remote_verifier or default_remote_verifier
        try:
            self._remote = verifier(ids) or {}
        except Exception as exc:  # noqa: BLE001 - no verification -> nothing final is moved
            self._remote = {i: {"ok": False, "reason": f"verification unavailable ({type(exc).__name__})"} for i in ids}
        for i in ids:
            self._remote.setdefault(i, {"ok": False, "reason": "video not returned by YouTube"})

    # ---- run -------------------------------------------------------------------------------------------
    def run(self, *, trigger: str, live: bool = False, confirm: bool = False,
            authorization: Optional[str] = None) -> Dict[str, Any]:
        from backend.app.core.dashboard_startup import ServerLock
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
        mode = "live" if live else "dry_run"
        record: Dict[str, Any] = {"run_id": run_id, "trigger": trigger, "mode": mode,
                                  "started_at_utc": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "policy_version": self.policy.get("policy_version"),
                                  "retention_days": dict(self.retention),
                                  "protected_tests": list(self.lifecycle["protected_tests"]),
                                  "scanned": 0, "scanned_bytes": 0, "eligible": 0, "eligible_bytes": 0,
                                  "pending_remote": 0, "pending_remote_bytes": 0, "protected_bytes": 0,
                                  "waiting_bytes": 0, "temporary_bytes": 0,
                                  "deleted": 0, "bytes_reclaimed": 0, "skipped": 0, "skip_reasons": {},
                                  "errors": [], "categories": {}, "forecast": {}, "eligible_files": []}
        if live:
            refusal = self._live_refusal(confirm, authorization)
            if refusal:
                record.update(outcome="refused", errors=[refusal])
                return self._persist(record)
        lock = ServerLock(self.root / "logs" / "junk_cleaner.lock")
        if not lock.acquire():
            record.update(outcome="skipped_concurrent_run")
            return self._persist(record)
        try:
            record["disk_total"], record["disk_used_before"], record["disk_free_before"] = shutil.disk_usage(str(self.root))
            st = self._state()
            if not st["db_ok"]:
                record.update(outcome="failed", errors=["database unavailable: nothing evaluated"])
                return self._persist(record)
            active = sorted(j for j, v in st["jobs"].items() if v["status"] in ACTIVE_STATUSES)
            if live and active:
                record.update(outcome="refused", errors=[f"production job(s) active: {', '.join(active[:5])}; live "
                                                         f"cleanup runs only when no job is active"])
                return self._persist(record)
            if live:
                self._verify_remote(st)
            media_root = self.media.resolve()
            for dirpath, dirs, files in os.walk(self.media, followlinks=False):
                dirs[:] = [d for d in dirs if not _is_link_or_reparse(Path(dirpath) / d)]   # never enter links
                rel = Path(dirpath).relative_to(self.media).parts
                job_id = rel[1] if len(rel) > 1 and rel[0] == "productions" else None
                for name in files:
                    self._evaluate(Path(dirpath) / name, list(rel), name, job_id, st, record, live, run_id, media_root)
            record["disk_free_after"] = shutil.disk_usage(str(self.root)).free
            record["outcome"] = "completed_with_errors" if record["errors"] else "completed"
        finally:
            lock.release()
        return self._persist(record)

    def _live_refusal(self, confirm: bool, authorization: Optional[str]) -> Optional[str]:
        if not confirm:
            return "live cleanup needs an explicit confirmation for this run"
        if not authorization:
            return "live cleanup needs the id of a successful dry run from the last hour (authorization)"
        rec = self._read_run(authorization)
        if not rec or rec.get("mode") != "dry_run" or rec.get("outcome") != "completed":
            return "authorization is not a successful dry run"
        try:
            fin = datetime.strptime(rec["finished_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            return "authorization dry run has no finish time"
        if datetime.now(timezone.utc) - fin > AUTHORIZATION_MAX_AGE:
            return "authorization dry run is older than one hour: run a new dry run"
        if rec.get("retention_days") != self.retention:
            return "the retention policy changed after the authorizing dry run: run a new dry run"
        return None

    def _read_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        if not run_id or any(c in run_id for c in ("/", "\\", ".", ":")):
            return None
        try:
            return json.loads((self.runs_dir / f"run_{run_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _evaluate(self, path: Path, rel: List[str], name: str, job_id: Optional[str], st: Dict[str, Any],
                  record: Dict[str, Any], live: bool, run_id: str, media_root: Path) -> None:
        if _is_link_or_reparse(path):
            record["skip_reasons"]["symbolic link / junction: never followed or moved"] = \
                record["skip_reasons"].get("symbolic link / junction: never followed or moved", 0) + 1
            record["skipped"] += 1
            return
        ident = _identity(path)
        if ident is None:
            record["errors"].append("stat failed")
            return
        size, mtime = ident[0], datetime.fromtimestamp(ident[1] / 1e9, tz=timezone.utc)
        cat = classify(rel, name, job_id)
        c = record["categories"].setdefault(cat, {"label": CATEGORIES[cat]["label"], "scanned": 0, "scanned_bytes": 0,
                                                   "eligible": 0, "eligible_bytes": 0, "pending_remote": 0,
                                                   "pending_remote_bytes": 0, "waiting": 0, "waiting_bytes": 0,
                                                   "protected": 0, "protected_bytes": 0, "deleted": 0,
                                                   "bytes_reclaimed": 0, "next_eligible_utc": None, "skip_reasons": {}})
        record["scanned"] += 1
        record["scanned_bytes"] += size
        c["scanned"] += 1
        c["scanned_bytes"] += size
        if CATEGORIES[cat]["temporary"]:
            record["temporary_bytes"] += size
        status, reason, on = self.decide(cat, rel, job_id, mtime, st)
        if status != "eligible":
            record["skipped"] += 1
            record["skip_reasons"][reason] = record["skip_reasons"].get(reason, 0) + 1
            c["skip_reasons"][reason] = c["skip_reasons"].get(reason, 0) + 1
        if status == "protected":
            record["protected_bytes"] += size
            c["protected"] += 1
            c["protected_bytes"] += size
            return
        if status == "waiting_age":
            record["waiting_bytes"] += size
            c["waiting"] += 1
            c["waiting_bytes"] += size
            ts = on.strftime("%Y-%m-%dT%H:%M:%SZ")
            if not c["next_eligible_utc"] or ts < c["next_eligible_utc"]:
                c["next_eligible_utc"] = ts
            day = (on + timedelta(hours=5, minutes=30)).date().isoformat()      # forecast by IST day
            if (on - self.now).days < FORECAST_DAYS:
                record["forecast"][day] = record["forecast"].get(day, 0) + size
            return
        if status == "eligible_pending_remote":
            record["pending_remote"] += 1
            record["pending_remote_bytes"] += size
            c["pending_remote"] += 1
            c["pending_remote_bytes"] += size
            return
        record["eligible"] += 1
        record["eligible_bytes"] += size
        c["eligible"] += 1
        c["eligible_bytes"] += size
        if len(record["eligible_files"]) < 500:
            record["eligible_files"].append({"path": str(path.relative_to(self.root)), "bytes": size, "category": cat,
                                             "reason": reason})
        if live:
            try:
                self._move_verified(path, ident, rel, name, job_id, run_id, media_root)
                record["deleted"] += 1
                record["bytes_reclaimed"] += size
                c["deleted"] += 1
                c["bytes_reclaimed"] += size
            except (OSError, ValueError) as exc:
                record["errors"].append(f"not moved {path.name}: {str(exc)[:160]}")

    def _move_verified(self, path: Path, ident: Tuple[int, int, int], rel: List[str], name: str,
                       job_id: Optional[str], run_id: str, media_root: Path) -> None:
        """Immediately before the move: same identity, still inside the media root, not a link, and the job's state
        and the decision read again from the database. Then one atomic rename into the run's quarantine."""
        if _identity(path) != ident:
            raise ValueError("file changed since the scan")
        if _is_link_or_reparse(path) or not path.resolve().is_relative_to(media_root):
            raise ValueError("path is a link or outside the media root")
        if self._any_active():
            raise ValueError("a production job became active (or the database is unavailable)")
        if self._fresh is None or time.monotonic() - self._fresh[0] > 15:      # job / publication state <= 15 s old
            self._fresh = (time.monotonic(), self._state())
        fresh = self._fresh[1]
        if not fresh["db_ok"]:
            raise ValueError("database unavailable")
        status, reason, _ = self.decide(classify(rel, name, job_id), rel, job_id,
                                        datetime.fromtimestamp(ident[1] / 1e9, tz=timezone.utc), fresh)
        if status != "eligible":
            raise ValueError(f"no longer eligible: {reason}")
        self._quarantine(path, run_id, ident[0], datetime.fromtimestamp(ident[1] / 1e9, tz=timezone.utc))

    def _any_active(self) -> bool:
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30) as c:
                q = ",".join("?" * len(ACTIVE_STATUSES))
                return bool(c.execute(f"SELECT COUNT(*) FROM persistent_jobs WHERE UPPER(status) IN ({q})",
                                      sorted(ACTIVE_STATUSES)).fetchone()[0])
        except sqlite3.Error:
            return True

    def quarantine_dir(self, run_id: str) -> Path:
        return self.media.joinpath(*QUARANTINE_SUBDIR) / run_id

    def _quarantine(self, path: Path, run_id: str, size: Optional[int] = None, mtime: Optional[datetime] = None) -> None:
        """Reversible ATOMIC move (rename on the same volume; never a copy+delete). Every moved file gets a manifest
        line, flushed to disk, so ``restore`` can always put the whole run back."""
        qdir = self.quarantine_dir(run_id)
        rel = path.relative_to(self.media)
        dest = qdir / "files" / rel
        if dest.exists():
            raise FileExistsError(str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(path), str(dest))
        entry = {"original": str(rel).replace("\\", "/"), "quarantined": str(dest.relative_to(qdir)).replace("\\", "/"),
                 "bytes": size, "mtime_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ") if mtime else None,
                 "moved_at_utc": _utc_now()}
        with open(qdir / "manifest.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

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
    """Dashboard view. Healthy only with a recent successful run, never merely because it is enabled. The storage
    breakdown and forecast come from the last completed run (no scan here)."""
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
    done = next((r for r in reversed(runs) if r.get("outcome") == "completed"), None)
    lifecycle = load_lifecycle(root)
    storage = None
    if done:
        storage = {
            "run_id": done.get("run_id"), "at_utc": done.get("finished_at_utc"),
            "scanned_bytes": done.get("scanned_bytes"), "scanned_files": done.get("scanned"),
            "occupied_bytes": done.get("scanned_bytes"),
            "disk": {"total": done.get("disk_total"), "used": done.get("disk_used_before"),
                     "free": done.get("disk_free_after", done.get("disk_free_before"))},
            "protected_bytes": done.get("protected_bytes"), "temporary_bytes": done.get("temporary_bytes"),
            "waiting_bytes": done.get("waiting_bytes"),
            "eligible_bytes": done.get("eligible_bytes"),
            "pending_remote_bytes": done.get("pending_remote_bytes"),
            "estimated_reclaimable_bytes": int(done.get("eligible_bytes") or 0) + int(done.get("pending_remote_bytes") or 0),
            "forecast": done.get("forecast") or {},
            "categories": done.get("categories") or {},
        }
    return {
        "enabled": True,
        "mode": "dry_run",
        "policy": {k: policy.get(k) for k in ("policy_version", "default_mode", "automatic_deletion")},
        "retention": {"days": lifecycle["retention_days"], "defaults": RETENTION_DEFAULTS, "labels": RETENTION_LABELS,
                      "fixed": FIXED, "protected_tests": lifecycle["protected_tests"]},
        "schedule": "daily dry-run at 03:30 IST (scheduler daemon); live cleanup never automatic and needs an "
                    "explicit confirmation plus a dry run from the last hour",
        "health": health,
        "last_run": last,
        "storage": storage,
        "runs_recorded": len(runs),
        "cumulative_bytes_reclaimed": sum(int(r.get("bytes_reclaimed") or 0) for r in live_runs),
        "cumulative_bytes_freed": 0,
        "cumulative_note": ("moved to quarantine by live runs (reversible; disk space is freed only if the quarantine "
                            "is purged, which never happens automatically)") if live_runs
                           else "no live run has ever been recorded",
    }


def _verdicts(doc: Any, depth: int = 0) -> List[bool]:
    """Boolean pass/fail verdicts in a test result report (keys passed / qc_passed / success / ok), shallow."""
    out: List[bool] = []
    if depth > 3:
        return out
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in ("passed", "qc_passed", "success", "ok") and isinstance(v, bool):
                out.append(v)
            elif isinstance(v, (dict, list)):
                out += _verdicts(v, depth + 1)
    elif isinstance(doc, list):
        for v in doc[:200]:
            out += _verdicts(v, depth + 1)
    return out


def default_remote_verifier(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fresh YouTube check (English channel's own authorization, quota-recorded): processing SUCCEEDED, upload
    processed, public, and the video belongs to the verified English channel."""
    from backend.app.channels.registry import get_registry
    from backend.app.services.youtube_publishing_engine import YouTubePublishingEngine
    expected = (get_registry().get("en") or {}).get("youtube_channel_id")
    eng = YouTubePublishingEngine()
    svc = eng.oauth.get_authenticated_service()
    if not svc or not expected:
        raise RuntimeError("English channel authorization or verified identity unavailable")
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        res = eng.quota.execute(svc.videos().list(part="status,processingDetails,snippet", id=",".join(chunk)),
                                "videos.list", purpose="storage_lifecycle_verification")
        for it in res.get("items") or []:
            stt, pd, sn = it.get("status") or {}, it.get("processingDetails") or {}, it.get("snippet") or {}
            problems = []
            if (pd.get("processingStatus") or "").lower() != "succeeded":
                problems.append(f"processing {pd.get('processingStatus') or 'unknown'}")
            if (stt.get("uploadStatus") or "").lower() != "processed":
                problems.append(f"upload {stt.get('uploadStatus') or 'unknown'}")
            if stt.get("privacyStatus") != "public":
                problems.append(f"privacy {stt.get('privacyStatus') or 'unknown'}")
            if sn.get("channelId") != expected:
                problems.append("not on the verified English channel")
            out[it.get("id")] = {"ok": not problems, "reason": "; ".join(problems) or "verified"}
    return out
