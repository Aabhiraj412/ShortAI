"""
Intelligent Media Asset Lifecycle Manager for CrazzyDocs YouTube Shorts Studio.
Phase 5, Stage 3.9B Production Standard.

Classifies all project files into Categories A through I, verifies database
and manifest dependencies, enforces strict safety boundaries, provides dry-run
by default, and manages reversible quarantine and restoration workflows.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
from backend.app.core.paths import DATABASE_PATH as _DATABASE_PATH
DEFAULT_DB_PATH = str(_DATABASE_PATH)
QUARANTINE_DIR = os.path.join(WORKSPACE, "media", "quarantine")
QUARANTINE_MANIFEST_PATH = os.path.join(QUARANTINE_DIR, "manifest.json")
REPORT_PATH = os.path.join(WORKSPACE, "logs", "storage_audit_report.json")


class AssetCategory(str, Enum):
    """File taxonomy categories A through J (Stage 3.13B Clean-Slate Standard)."""
    CATEGORY_A_PERMANENT_SOURCE = "CATEGORY_A_PERMANENT_SOURCE"
    CATEGORY_B_IMMUTABLE_BRANDING = "CATEGORY_B_IMMUTABLE_BRANDING"
    CATEGORY_C_APPROVED_REUSABLE_ASSETS = "CATEGORY_C_APPROVED_REUSABLE_ASSETS"
    CATEGORY_D_APPROVED_DELIVERABLES = "CATEGORY_D_APPROVED_DELIVERABLES"
    CATEGORY_E_HISTORICAL_SHOWCASES = "CATEGORY_E_HISTORICAL_SHOWCASES"
    CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS = "CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS"
    CATEGORY_G_DUPLICATES = "CATEGORY_G_DUPLICATES"
    CATEGORY_H_TEMPORARY_FILES = "CATEGORY_H_TEMPORARY_FILES"
    CATEGORY_I_OBSOLETE_CACHE = "CATEGORY_I_OBSOLETE_CACHE"
    CATEGORY_J_UNKNOWN = "CATEGORY_J_UNKNOWN"

    # Backward compatibility aliases
    CATEGORY_B_REUSABLE_ASSETS = "CATEGORY_C_APPROVED_REUSABLE_ASSETS"
    CATEGORY_C_APPROVED_DELIVERABLES = "CATEGORY_D_APPROVED_DELIVERABLES"
    CATEGORY_D_UNPUBLISHED_DELIVERABLES = "CATEGORY_D_APPROVED_DELIVERABLES"
    CATEGORY_E_TEMPORARY_FILES = "CATEGORY_H_TEMPORARY_FILES"
    CATEGORY_F_CACHE = "CATEGORY_I_OBSOLETE_CACHE"
    CATEGORY_G_FAILED_ARTIFACTS = "CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS"
    CATEGORY_H_DUPLICATES = "CATEGORY_G_DUPLICATES"
    CATEGORY_I_ORPHANED = "CATEGORY_E_HISTORICAL_SHOWCASES"


PROTECTED_CATEGORIES = {
    AssetCategory.CATEGORY_A_PERMANENT_SOURCE,
    AssetCategory.CATEGORY_B_IMMUTABLE_BRANDING,
    AssetCategory.CATEGORY_C_APPROVED_REUSABLE_ASSETS,
    AssetCategory.CATEGORY_D_APPROVED_DELIVERABLES,
    AssetCategory.CATEGORY_J_UNKNOWN,
}


@dataclass
class ClassifiedFile:
    """Detailed metadata and classification for a managed project file."""
    relative_path: str
    absolute_path: str
    category: AssetCategory
    size_bytes: int
    modified_at: str
    sha256: str = ""
    reason: str = ""
    is_protected: bool = True
    is_quarantinable: bool = False
    referenced_by: List[str] = field(default_factory=list)


@dataclass
class QuarantineRecord:
    """Manifest record for a reversibly quarantined file."""
    quarantine_id: str
    original_relative_path: str
    quarantine_relative_path: str
    sha256: str
    size_bytes: int
    category: str
    reason: str
    quarantined_at: str
    restored: bool = False
    restored_at: Optional[str] = None


def compute_file_sha256(filepath: str) -> str:
    """Computes SHA-256 hash of a file in 64KB blocks."""
    if not os.path.isfile(filepath):
        return ""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def normalize_rel_path(path_str: str, base_dir: str = WORKSPACE) -> str:
    """Normalizes any path to a forward-slash relative path from workspace root."""
    try:
        norm = os.path.normpath(path_str)
        if os.path.isabs(norm):
            rel = os.path.relpath(norm, base_dir)
        else:
            rel = norm
        return rel.replace("\\", "/").lstrip("./")
    except Exception:
        return path_str.replace("\\", "/").lstrip("./")


class MediaLifecycleManager:
    """
    Intelligent media asset lifecycle manager providing dependency-aware classification,
    quarantine, restoration, and storage auditing.
    """

    def __init__(
        self,
        workspace_root: str = WORKSPACE,
        db_path: Optional[str] = None,
        quarantine_dir: Optional[str] = None,
    ):
        self.workspace = os.path.abspath(workspace_root)
        self.db_path = db_path or DEFAULT_DB_PATH
        self.quarantine_dir = quarantine_dir or QUARANTINE_DIR
        self.manifest_path = os.path.join(self.quarantine_dir, "manifest.json")

    # -------------------------------------------------------------------------
    # 1. Dependency Collection
    # -------------------------------------------------------------------------

    def collect_dependencies(self) -> Dict[str, Set[str]]:
        """
        Gathers all file references from:
        - SQLite tables: persistent_jobs, publishing_ledger, media_assets, content_ledger, video_jobs
        - JSON manifests: attribution_manifest.json, audio telemetry, editorial subtitles
        Returns dict with keys:
          'approved_paths': set of relative paths for approved deliverables
          'unpublished_paths': set of relative paths for unpublished deliverables in queue
          'failed_paths': set of relative paths from failed/cancelled jobs
          'referenced_paths': set of all actively referenced relative paths
          'hashes': set of registered asset hashes
        """
        approved_paths: Set[str] = set()
        unpublished_paths: Set[str] = set()
        failed_paths: Set[str] = set()
        referenced_paths: Set[str] = set()
        asset_hashes: Set[str] = set()

        # A. Query SQLite Database
        if os.path.isfile(self.db_path):
            try:
                con = sqlite3.connect(self.db_path)
                cur = con.cursor()

                # 1. persistent_jobs
                try:
                    cur.execute("SELECT id, status, checkpoint_data_json FROM persistent_jobs")
                    for job_id, status, cp_json in cur.fetchall():
                        paths_in_job = []
                        if cp_json:
                            try:
                                data = json.loads(cp_json)
                                for k in ("video_path", "thumbnail_path", "audio_path", "ass_path"):
                                    val = data.get(k)
                                    if val and isinstance(val, str):
                                        paths_in_job.append(normalize_rel_path(val, self.workspace))
                            except Exception:
                                pass
                        
                        for p in paths_in_job:
                            referenced_paths.add(p)
                            if status in ("COMPLETED", "PUBLISHED"):
                                approved_paths.add(p)
                            elif status in ("FAILED", "CANCELLED"):
                                failed_paths.add(p)
                            elif status in ("PENDING", "RUNNING", "READY_FOR_PUBLICATION", "RETRYING"):
                                unpublished_paths.add(p)
                except Exception as e:
                    logger.debug("Error querying persistent_jobs: %s", e)

                # 2. publishing_ledger
                try:
                    cur.execute("SELECT video_path, thumbnail_path, upload_status, video_hash, youtube_video_id FROM publishing_ledger")
                    for v_path, t_path, pub_status, f_hash, yt_id in cur.fetchall():
                        is_approved_or_uploaded = (
                            pub_status in ("PUBLISHED", "APPROVED", "COMPLETED", "uploaded_private", "uploaded_unlisted")
                            or bool(yt_id)
                        )
                        if v_path:
                            rel_v = normalize_rel_path(v_path, self.workspace)
                            referenced_paths.add(rel_v)
                            if is_approved_or_uploaded:
                                approved_paths.add(rel_v)
                            else:
                                unpublished_paths.add(rel_v)
                        if t_path:
                            rel_t = normalize_rel_path(t_path, self.workspace)
                            referenced_paths.add(rel_t)
                            if is_approved_or_uploaded:
                                approved_paths.add(rel_t)
                            else:
                                unpublished_paths.add(rel_t)
                        if f_hash:
                            asset_hashes.add(f_hash.lower())
                except Exception as e:
                    logger.debug("Error querying publishing_ledger: %s", e)

                # 3. media_assets
                try:
                    cur.execute("SELECT local_path, portrait_path, asset_hash FROM media_assets")
                    for loc, port, h in cur.fetchall():
                        if loc:
                            referenced_paths.add(normalize_rel_path(loc, self.workspace))
                        if port:
                            referenced_paths.add(normalize_rel_path(port, self.workspace))
                        if h:
                            asset_hashes.add(h.lower())
                except Exception as e:
                    logger.debug("Error querying media_assets: %s", e)

                # 4. video_jobs
                try:
                    cur.execute("SELECT video_path, thumbnail_path, status FROM video_jobs")
                    for v, t, s in cur.fetchall():
                        if v:
                            rel_v = normalize_rel_path(v, self.workspace)
                            referenced_paths.add(rel_v)
                            if s in ("completed", "approved"):
                                approved_paths.add(rel_v)
                            elif s in ("failed", "cancelled"):
                                failed_paths.add(rel_v)
                            else:
                                unpublished_paths.add(rel_v)
                        if t:
                            rel_t = normalize_rel_path(t, self.workspace)
                            referenced_paths.add(rel_t)
                except Exception as e:
                    logger.debug("Error querying video_jobs: %s", e)

                con.close()
            except Exception as e:
                logger.warning("Could not read studio.db dependencies: %s", e)

        # B. Scan Manifests & Telemetry
        manifest_patterns = [
            os.path.join(self.workspace, "media", "images"),
            os.path.join(self.workspace, "media", "audio"),
            os.path.join(self.workspace, "media", "subtitles"),
        ]
        for base_p in manifest_patterns:
            if not os.path.exists(base_p):
                continue
            for root, _, files in os.walk(base_p):
                for f in files:
                    if f.endswith("attribution_manifest.json"):
                        man_path = os.path.join(root, f)
                        try:
                            with open(man_path, "r", encoding="utf-8") as jf:
                                mdata = json.load(jf)
                                items = mdata if isinstance(mdata, list) else mdata.get("assets", mdata.get("items", []))
                                for it in items:
                                    if isinstance(it, dict):
                                        for k in ("local_path", "portrait_path", "file_path", "path"):
                                            p = it.get(k)
                                            if p:
                                                referenced_paths.add(normalize_rel_path(p, self.workspace))
                        except Exception:
                            pass
                    elif f.endswith("telemetry.json"):
                        tel_path = os.path.join(root, f)
                        referenced_paths.add(normalize_rel_path(tel_path, self.workspace))

        # Include known approved production showcase files unconditionally
        known_approved_showcases = [
            "media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4",
            "media/thumbnails/showcase_stage_3_4_tsutomu_yamaguchi.png",
            "media/thumbnails/showcase_stage_3_5_yamaguchi_refined.png",
            "media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png",
        ]
        for sh in known_approved_showcases:
            approved_paths.add(sh)
            referenced_paths.add(sh)

        return {
            "approved_paths": approved_paths,
            "unpublished_paths": unpublished_paths,
            "failed_paths": failed_paths,
            "referenced_paths": referenced_paths,
            "asset_hashes": asset_hashes,
        }

    # -------------------------------------------------------------------------
    # 2. File Classification Engine
    # -------------------------------------------------------------------------

    def classify_file(
        self,
        file_path: str,
        deps: Dict[str, Set[str]],
        seen_hashes: Optional[Dict[str, str]] = None,
    ) -> ClassifiedFile:
        """
        Classifies a single file into one of Categories A through I.
        """
        abs_path = os.path.abspath(file_path)
        rel_path = normalize_rel_path(abs_path, self.workspace)
        size_bytes = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).isoformat()
        except Exception:
            mtime = datetime.now(timezone.utc).isoformat()

        # 1. Category A: Permanent Source & Core Code/Config
        core_prefixes = (
            "backend/", "frontend/", "tests/", "docs/", "scripts/", "tools/",
            "python-env/", ".git/", ".gemini/", "Youtube Channel Src/",
        )
        core_files = {
            "readme.md", "gemini.md", ".gitignore", "run_in_env.cmd", "run_in_env.ps1",
            "run_pipeline.bat", "start_studio.bat", "start_studio.ps1", "env.ps1",
        }
        if any(rel_path.startswith(cp) for cp in core_prefixes) or os.path.basename(rel_path).lower() in core_files:
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_A_PERMANENT_SOURCE,
                size_bytes=size_bytes,
                modified_at=mtime,
                reason="Core source code, reference template, or immutable channel asset",
                is_protected=True,
                is_quarantinable=False,
                referenced_by=["core_repository"],
            )

        # 2. Category B: Reusable Assets
        if rel_path.startswith("assets/audio/music/") or rel_path.startswith("assets/brand/") or rel_path.startswith("assets/fonts/"):
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_B_REUSABLE_ASSETS,
                size_bytes=size_bytes,
                modified_at=mtime,
                reason="Reusable channel audio, font, or branding asset",
                is_protected=True,
                is_quarantinable=False,
                referenced_by=["brand_library"],
            )

        # 3. Category D: Approved Deliverables & Protected Test Fixtures
        filename = os.path.basename(rel_path)
        known_approved_deliverables = {
            "showcase_vesna_vulovic_verified.mp4",
            "thumb_vesna_vulovic_verified.png",
            "showcase_cycle_4_20261110_1800.mp4",
            "thumb_cycle_4_20261110_1800.png",
            "stage_3_13_vesna_voice.mp3",
            "showcase_stage_3_4_tsutomu_yamaguchi.mp4",
            "stage_3_4_yamaguchi_voice.mp3",
        }
        h = ""
        if seen_hashes is not None and size_bytes > 0:
            h = compute_file_sha256(abs_path)

        if rel_path in deps["approved_paths"] or filename in known_approved_deliverables:
            if h and seen_hashes is not None and h not in seen_hashes:
                seen_hashes[h] = rel_path
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_D_APPROVED_DELIVERABLES,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Approved production deliverable or protected test fixture",
                is_protected=True,
                is_quarantinable=False,
                referenced_by=["publishing_ledger", "approved_registry", "test_fixtures"],
            )

        # 4. Check Duplicates (Category G)
        if seen_hashes is not None and size_bytes > 0:
            if h in seen_hashes and seen_hashes[h] != rel_path:
                return ClassifiedFile(
                    relative_path=rel_path,
                    absolute_path=abs_path,
                    category=AssetCategory.CATEGORY_G_DUPLICATES,
                    size_bytes=size_bytes,
                    modified_at=mtime,
                    sha256=h,
                    reason=f"Duplicate sha256 checksum of {seen_hashes[h]}",
                    is_protected=False,
                    is_quarantinable=True,
                    referenced_by=[f"duplicate_of:{seen_hashes[h]}"],
                )
            elif h:
                seen_hashes[h] = rel_path

        # 5. Category D: Unpublished Deliverables in Queue
        if rel_path in deps["unpublished_paths"]:
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_D_APPROVED_DELIVERABLES,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Pending publication or actively queued in JobModel",
                is_protected=True,
                is_quarantinable=False,
                referenced_by=["persistent_jobs_queue"],
            )

        # 6. Category H: Temporary Files
        temp_indicators = [
            "_pipe.mp4", ".tmp", ".temp", ".lock", "concat_", "_test_",
            "media/audio/scratch/", "media/video/scratch/",
        ]
        if any(ind in rel_path.lower() for ind in temp_indicators):
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_H_TEMPORARY_FILES,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Intermediate scratch render file or pipeline lock",
                is_protected=False,
                is_quarantinable=True,
                referenced_by=[],
            )

        # 7. Category I: Obsolete Cache
        if rel_path.startswith("cache/") or rel_path.startswith(".pytest_cache/") or "__pycache__" in rel_path or rel_path.startswith("media/cache/"):
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_I_OBSOLETE_CACHE,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Reproducible intermediate render or execution cache",
                is_protected=False,
                is_quarantinable=True,
                referenced_by=[],
            )

        # 8. Category F: Failed Artifacts
        if rel_path in deps["failed_paths"] or "_failed_" in rel_path or "_partial_" in rel_path:
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Artifact associated with a failed or aborted job",
                is_protected=False,
                is_quarantinable=True,
                referenced_by=["failed_job_reference"],
            )

        # 9. Active Reference Check
        if rel_path in deps["referenced_paths"]:
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_C_APPROVED_REUSABLE_ASSETS,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Actively referenced by SQLite database or manifest",
                is_protected=True,
                is_quarantinable=False,
                referenced_by=["db_or_manifest"],
            )

        # 10. Category E: Historical Showcases & Orphaned Media Outputs
        historical_names = {
            "showcase_stage_3_4_tsutomu_yamaguchi.mp4",
            "showcase_stage_3_4_tsutomu_yamaguchi.png",
            "showcase_stage_3_5_yamaguchi_refined.png",
            "showcase_stage_3_9a_yamaguchi_poster.png",
            "stage_3_4_yamaguchi_voice.mp3",
            "showcase_test_auto_job_001.mp4",
            "showcase_test_interrupted_tts_job.mp4",
        }
        ext = os.path.splitext(filename)[1].lower()
        known_media_exts = {".mp4", ".mp3", ".wav", ".png", ".jpg", ".jpeg", ".ass"}
        if (
            filename in historical_names
            or "yamaguchi" in rel_path.lower()
            or "molasses" in rel_path.lower()
            or rel_path.startswith("media/images/yamaguchi/")
            or rel_path.startswith("media/images/molasses_flood/")
            or (rel_path.startswith("media/video/") and ext in known_media_exts and "vesna" not in rel_path.lower() and "cycle_4" not in rel_path.lower())
        ):
            return ClassifiedFile(
                relative_path=rel_path,
                absolute_path=abs_path,
                category=AssetCategory.CATEGORY_E_HISTORICAL_SHOWCASES,
                size_bytes=size_bytes,
                modified_at=mtime,
                sha256=h,
                reason="Historical prototype or unreferenced media output",
                is_protected=False,
                is_quarantinable=True,
                referenced_by=[],
            )

        # Default fallback: Category J (Unknown - Protected by default)
        return ClassifiedFile(
            relative_path=rel_path,
            absolute_path=abs_path,
            category=AssetCategory.CATEGORY_J_UNKNOWN,
            size_bytes=size_bytes,
            modified_at=mtime,
            sha256=h,
            reason="Unclassified workspace file treated as protected pending manual audit",
            is_protected=True,
            is_quarantinable=False,
            referenced_by=[],
        )

    # -------------------------------------------------------------------------
    # 3. Scanning & Classification
    # -------------------------------------------------------------------------

    def scan_workspace(
        self,
        subdirs: Optional[List[str]] = None,
        compute_hashes_for_duplicates: bool = True,
    ) -> List[ClassifiedFile]:
        """
        Scans specified directories (defaults to media, cache, logs, storage)
        and classifies all files.
        """
        target_subdirs = subdirs or ["media", "cache", "logs", "storage", "assets"]
        deps = self.collect_dependencies()
        seen_hashes: Dict[str, str] = {} if compute_hashes_for_duplicates else None

        classified_list: List[ClassifiedFile] = []

        for sub in target_subdirs:
            full_sub = os.path.join(self.workspace, sub)
            if not os.path.exists(full_sub):
                continue

            for root, dirs, files in os.walk(full_sub):
                # Don't recurse into active quarantine directory during standard scan
                if os.path.abspath(root).startswith(os.path.abspath(self.quarantine_dir)):
                    continue

                for f in sorted(files, key=lambda x: (1 if "copy" in x.lower() else 0, x)):
                    file_p = os.path.join(root, f)
                    cf = self.classify_file(file_p, deps, seen_hashes)
                    classified_list.append(cf)

        return classified_list

    # -------------------------------------------------------------------------
    # 4. Storage Audit Report Generation
    # -------------------------------------------------------------------------

    def generate_storage_report(
        self,
        output_path: Optional[str] = REPORT_PATH,
        classified_files: Optional[List[ClassifiedFile]] = None,
    ) -> Dict:
        """
        Compiles storage breakdown across categories, calculates recoverable space,
        and saves a JSON report.
        """
        files = classified_files if classified_files is not None else self.scan_workspace()

        total_files = len(files)
        total_bytes = sum(f.size_bytes for f in files)

        category_stats: Dict[str, Dict] = {}
        for cat in AssetCategory:
            category_stats[cat.value] = {
                "count": 0,
                "size_bytes": 0,
                "size_mb": 0.0,
                "pct_of_total_bytes": 0.0,
            }

        quarantinable_bytes = 0
        quarantinable_count = 0

        for f in files:
            cat_key = f.category.value
            category_stats[cat_key]["count"] += 1
            category_stats[cat_key]["size_bytes"] += f.size_bytes
            if f.is_quarantinable:
                quarantinable_bytes += f.size_bytes
                quarantinable_count += 1

        for cat_key, stats in category_stats.items():
            stats["size_mb"] = round(stats["size_bytes"] / (1024 * 1024), 2)
            stats["pct_of_total_bytes"] = round(
                (stats["size_bytes"] / total_bytes * 100) if total_bytes > 0 else 0.0, 2
            )

        top_largest = sorted(files, key=lambda x: x.size_bytes, reverse=True)[:10]
        top_largest_meta = [
            {
                "path": f.relative_path,
                "size_mb": round(f.size_bytes / (1024 * 1024), 2),
                "category": f.category.value,
            }
            for f in top_largest
        ]

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": self.workspace,
            "total_files_scanned": total_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "total_size_bytes": total_bytes,
            "quarantinable_files_count": quarantinable_count,
            "quarantinable_size_mb": round(quarantinable_bytes / (1024 * 1024), 2),
            "quarantinable_size_bytes": quarantinable_bytes,
            "categories": category_stats,
            "top_largest_files": top_largest_meta,
        }

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as jf:
                json.dump(report, jf, indent=2)

        return report

    # -------------------------------------------------------------------------
    # 5. Quarantine & Restoration Workflows
    # -------------------------------------------------------------------------

    def _load_quarantine_manifest(self) -> Dict[str, Dict]:
        """Loads or initializes the quarantine manifest with crash recovery for .tmp files."""
        # 1. Recover interrupted atomic save if .tmp exists
        tmp_path = self.manifest_path + ".tmp"
        if os.path.isfile(tmp_path):
            if not os.path.isfile(self.manifest_path) or os.path.getsize(self.manifest_path) == 0:
                try:
                    shutil.move(tmp_path, self.manifest_path)
                    logger.info("Recovered quarantine manifest from interrupted .tmp file.")
                except Exception as e:
                    logger.warning("Could not recover manifest from .tmp: %s", e)
            else:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        # 2. Try primary manifest_path
        if os.path.isfile(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not load quarantine manifest: %s", e)

        # 3. Check legacy manifest path
        legacy_path = os.path.join(self.quarantine_dir, "quarantine_manifest.json")
        if os.path.isfile(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        return {}

    def _save_quarantine_manifest(self, manifest: Dict[str, Dict]):
        """Persists the quarantine manifest atomically."""
        os.makedirs(self.quarantine_dir, exist_ok=True)
        tmp_path = self.manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        shutil.move(tmp_path, self.manifest_path)

    def quarantine_files(
        self,
        files_to_quarantine: List[ClassifiedFile],
        dry_run: bool = True,
    ) -> Dict:
        """
        Moves eligible files to media/quarantine/archived_files/ and logs to manifest.
        Strict safety: Never touches protected categories (A, B, C, D, J)
        and verifies path is within D:\Agents\CrazzyDocs\.
        Default: dry_run=True.
        """
        results = {
            "dry_run": dry_run,
            "eligible_count": len(files_to_quarantine),
            "quarantined_count": 0,
            "bytes_freed": 0,
            "skipped_protected_count": 0,
            "quarantined_ids": [],
            "skipped_files": [],
        }

        manifest = self._load_quarantine_manifest() if not dry_run else {}

        for cf in files_to_quarantine:
            # Boundary & safety checks
            if cf.category in PROTECTED_CATEGORIES or cf.is_protected:
                results["skipped_protected_count"] += 1
                results["skipped_files"].append({
                    "path": cf.relative_path,
                    "reason": f"Protected category {cf.category.value}",
                })
                continue

            abs_p = os.path.abspath(cf.absolute_path)
            if not abs_p.startswith(self.workspace):
                results["skipped_protected_count"] += 1
                results["skipped_files"].append({
                    "path": cf.relative_path,
                    "reason": "Path outside workspace boundary",
                })
                continue

            if not os.path.isfile(abs_p):
                continue

            results["quarantined_count"] += 1
            results["bytes_freed"] += cf.size_bytes

            if not dry_run:
                # Execute reversible move into archived_files directory
                q_id = f"q_{int(datetime.now(timezone.utc).timestamp())}_{hashlib.md5(cf.relative_path.encode()).hexdigest()[:8]}"
                ext = os.path.splitext(abs_p)[1]
                q_filename = f"{q_id}{ext}"
                archived_dir = os.path.join(self.quarantine_dir, "archived_files")
                os.makedirs(archived_dir, exist_ok=True)
                target_q_abs = os.path.join(archived_dir, q_filename)
                target_q_rel = normalize_rel_path(target_q_abs, self.workspace)

                # Compute hash before move if not already present
                h = cf.sha256 or compute_file_sha256(abs_p)

                shutil.move(abs_p, target_q_abs)

                manifest[q_id] = asdict(
                    QuarantineRecord(
                        quarantine_id=q_id,
                        original_relative_path=cf.relative_path,
                        quarantine_relative_path=target_q_rel,
                        sha256=h,
                        size_bytes=cf.size_bytes,
                        category=cf.category.value,
                        reason=cf.reason,
                        quarantined_at=datetime.now(timezone.utc).isoformat(),
                        restored=False,
                    )
                )
                results["quarantined_ids"].append(q_id)

        if not dry_run and results["quarantined_count"] > 0:
            self._save_quarantine_manifest(manifest)

        return results

    def restore_file(self, quarantine_id: str) -> bool:
        """
        Restores a single file from quarantine back to its original path,
        verifying checksum before marking as restored.
        """
        manifest = self._load_quarantine_manifest()
        if quarantine_id not in manifest:
            logger.error("Quarantine ID %s not found in manifest.", quarantine_id)
            return False

        record = manifest[quarantine_id]
        if record.get("restored"):
            logger.info("Quarantine ID %s is already marked restored.", quarantine_id)
            return True

        q_abs = os.path.join(self.workspace, record["quarantine_relative_path"].replace("/", os.sep))
        orig_abs = os.path.join(self.workspace, record["original_relative_path"].replace("/", os.sep))

        if not os.path.isfile(q_abs):
            logger.error("Quarantined physical file missing: %s", q_abs)
            return False

        # Verify hash
        cur_hash = compute_file_sha256(q_abs)
        if record.get("sha256") and cur_hash != record["sha256"]:
            logger.warning("Checksum mismatch during restore: %s vs %s", cur_hash, record["sha256"])

        os.makedirs(os.path.dirname(orig_abs), exist_ok=True)
        shutil.move(q_abs, orig_abs)

        record["restored"] = True
        record["restored_at"] = datetime.now(timezone.utc).isoformat()
        manifest[quarantine_id] = record
        self._save_quarantine_manifest(manifest)
        return True

    def restore_all(self) -> Dict:
        """Restores all currently unrestored files in quarantine manifest."""
        manifest = self._load_quarantine_manifest()
        restored = 0
        failed = 0
        for q_id, rec in list(manifest.items()):
            if not rec.get("restored"):
                ok = self.restore_file(q_id)
                if ok:
                    restored += 1
                else:
                    failed += 1
        return {"restored_count": restored, "failed_count": failed}

    # -------------------------------------------------------------------------
    # 6. Operational Cleanup Routines
    # -------------------------------------------------------------------------

    def daily_report(self, output_path: Optional[str] = REPORT_PATH) -> Dict:
        """Generates dry-run storage audit report without modifying any files."""
        return self.generate_storage_report(output_path=output_path)

    def weekly_temp_quarantine(
        self,
        retention_days: int = 7,
        dry_run: bool = True,
    ) -> Dict:
        """
        Quarantines temporary scratch files (Category E) and failed artifacts (Category G)
        older than retention_days.
        Default: dry_run=True.
        """
        files = self.scan_workspace()
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        candidates = []
        for f in files:
            if f.category in (AssetCategory.CATEGORY_E_TEMPORARY_FILES, AssetCategory.CATEGORY_G_FAILED_ARTIFACTS):
                try:
                    f_mtime = datetime.fromisoformat(f.modified_at)
                    if f_mtime < cutoff:
                        candidates.append(f)
                except Exception:
                    candidates.append(f)

        return self.quarantine_files(candidates, dry_run=dry_run)

    def monthly_audit(self, dry_run: bool = True) -> Dict:
        """
        Runs comprehensive scan, identifies orphaned media and duplicate files,
        and optionally quarantines them if dry_run=False.
        """
        files = self.scan_workspace(compute_hashes_for_duplicates=True)
        report = self.generate_storage_report(classified_files=files)

        candidates = [
            f for f in files
            if f.category in (
                AssetCategory.CATEGORY_E_TEMPORARY_FILES,
                AssetCategory.CATEGORY_F_CACHE,
                AssetCategory.CATEGORY_G_FAILED_ARTIFACTS,
                AssetCategory.CATEGORY_H_DUPLICATES,
                AssetCategory.CATEGORY_I_ORPHANED,
            )
        ]

        quarantine_res = self.quarantine_files(candidates, dry_run=dry_run)
        return {
            "audit_report": report,
            "quarantine_action": quarantine_res,
        }


media_lifecycle_manager = MediaLifecycleManager()
