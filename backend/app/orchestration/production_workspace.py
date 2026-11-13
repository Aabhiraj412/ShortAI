"""
Production Asset Isolation Workspace Manager for CrazzyDocs.
Phase 5, Stage 3.13B Production Standard.

Enforces strict isolation for each production cycle:
media/productions/{production_id}/
    ├── research/
    ├── scripts/
    ├── narration/
    ├── visual_timeline/
    ├── assets/
    ├── thumbnails/
    ├── video/
    ├── metadata/
    └── reports/

Each production maintains an immutable production_manifest.json linking all generated
artifacts to the unique production_id and topic_id. Cross-topic asset reuse is prohibited.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

from backend.app.core.paths import (
    MEDIA_THUMBNAIL_DIR,
    MEDIA_VIDEO_DIR,
    PROJECT_ROOT_STR as WORKSPACE_ROOT,
)
PRODUCTIONS_BASE_DIR = os.path.join(WORKSPACE_ROOT, "media", "productions")


def compute_file_sha256(filepath: str) -> str:
    if not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _inspect_media_dimensions(filepath: str) -> Dict[str, Any]:
    """Extracts width, height, and format for images or videos without raising."""
    ext = os.path.splitext(filepath)[1].lstrip(".").lower() or "unknown"
    info: Dict[str, Any] = {"width": 0, "height": 0, "format": ext}
    try:
        if ext in ("png", "jpg", "jpeg", "webp", "bmp", "gif"):
            from PIL import Image
            with Image.open(filepath) as img:
                info["width"], info["height"] = img.size
                info["format"] = (img.format or ext).lower()
        elif ext in ("mp4", "mov", "mkv", "webm"):
            from backend.app.services.animation_service import probe_video
            probed = probe_video(filepath)
            info["width"] = int(probed.get("width") or 0)
            info["height"] = int(probed.get("height") or 0)
            info["format"] = ext
    except Exception:
        pass
    return info


@dataclass
class ProductionArtifact:
    stage: str
    artifact_type: str  # research, script, storyboard, audio, narration, acquired_asset, generated_asset, animation, asset, video, thumbnail, metadata, report, audit
    file_path: str
    relative_path: str
    file_size_bytes: int
    sha256: str
    created_at: str
    asset_id: Optional[str] = None
    source_url: Optional[str] = None
    creator: Optional[str] = None
    license: Optional[str] = None
    license_url: Optional[str] = None
    generation_provider: Optional[str] = None
    prompt_metadata: Optional[Dict[str, Any]] = None
    width: int = 0
    height: int = 0
    format: str = ""


class ProductionWorkspace:
    """Manages an isolated, self-contained directory tree for a single Short production."""

    def __init__(
        self,
        production_id: str,
        topic_id: str,
        topic_title: str,
        category: str = "",
        base_dir: Optional[str] = None,
    ):
        self.production_id = production_id
        self.topic_id = topic_id
        self.topic_title = topic_title
        self.category = category
        self.workspace_root = WORKSPACE_ROOT

        if base_dir:
            self.prod_dir = os.path.abspath(base_dir)
        else:
            self.prod_dir = os.path.join(PRODUCTIONS_BASE_DIR, self.production_id)

        # Standard isolated subdirectories (Stage 3.21J canonical structure + legacy aliases)
        self.research_dir = os.path.join(self.prod_dir, "research")
        self.scripts_dir = os.path.join(self.prod_dir, "scripts")
        self.storyboards_dir = os.path.join(self.prod_dir, "storyboards")
        self.audio_dir = os.path.join(self.prod_dir, "audio")
        self.narration_dir = os.path.join(self.prod_dir, "narration")
        self.visual_timeline_dir = os.path.join(self.prod_dir, "visual_timeline")
        self.assets_dir = os.path.join(self.prod_dir, "assets")
        self.assets_acquired_dir = os.path.join(self.assets_dir, "acquired")
        self.assets_generated_dir = os.path.join(self.assets_dir, "generated")
        self.assets_animations_dir = os.path.join(self.assets_dir, "animations")
        self.thumbnails_dir = os.path.join(self.prod_dir, "thumbnails")
        self.video_dir = os.path.join(self.prod_dir, "video")
        self.metadata_dir = os.path.join(self.prod_dir, "metadata")
        self.reports_dir = os.path.join(self.prod_dir, "reports")
        self.audit_dir = os.path.join(self.prod_dir, "audit")

        self.manifest_path = os.path.join(self.prod_dir, "production_manifest.json")
        self.artifacts: List[ProductionArtifact] = []
        self.script_fingerprint: str = ""
        self.storyboard_fingerprint: str = ""

    def get_canonical_video_path(self, filename: Optional[str] = None) -> str:
        """Returns the authoritative final video destination for this production."""
        fname = filename or f"showcase_{self.production_id}.mp4"
        return os.path.join(self.video_dir, fname)

    def get_canonical_thumbnail_path(self, filename: Optional[str] = None) -> str:
        """Returns the authoritative final thumbnail destination for this production."""
        fname = filename or f"thumb_{self.production_id}.png"
        return os.path.join(self.thumbnails_dir, fname)

    def deliver_approved_sample(self, video_source: str, thumbnail_source: str) -> Dict[str, Any]:
        """Atomically stage and create-only link an approved sample in the canonical output folders."""
        assets = (
            ("video", os.path.abspath(video_source), os.path.join(str(MEDIA_VIDEO_DIR), "crazzydocs_black_hole_final.mp4")),
            ("thumbnail", os.path.abspath(thumbnail_source), os.path.join(str(MEDIA_THUMBNAIL_DIR), "crazzydocs_black_hole_thumbnail.jpg")),
        )
        pending: List[Dict[str, Any]] = []
        results: Dict[str, Dict[str, Any]] = {}
        for asset_type, source, destination in assets:
            if not os.path.isfile(source) or os.path.getsize(source) == 0:
                raise FileNotFoundError(f"Approved {asset_type} source is missing or empty: {source}")
            if os.path.commonpath((os.path.abspath(self.prod_dir), source)) != os.path.abspath(self.prod_dir):
                raise ValueError(f"Approved {asset_type} source must remain inside its production workspace: {source}")
            source_hash = compute_file_sha256(source)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            if os.path.exists(destination):
                if compute_file_sha256(destination) != source_hash:
                    raise FileExistsError(f"Refusing to overwrite a different canonical {asset_type}: {destination}")
                results[asset_type] = {
                    "workspace_path": source,
                    "canonical_path": destination,
                    "sha256": source_hash,
                    "file_size_bytes": os.path.getsize(destination),
                    "delivery_status": "already_present",
                }
                continue
            pending.append({
                "type": asset_type,
                "source": source,
                "destination": destination,
                "sha256": source_hash,
                "file_size_bytes": os.path.getsize(source),
            })

        staged: List[Dict[str, Any]] = []
        linked: List[Dict[str, Any]] = []
        try:
            for asset in pending:
                descriptor, temporary_path = tempfile.mkstemp(
                    prefix=f".{os.path.basename(asset['destination'])}.",
                    suffix=".tmp",
                    dir=os.path.dirname(asset["destination"]),
                )
                with os.fdopen(descriptor, "wb") as target, open(asset["source"], "rb") as source_file:
                    shutil.copyfileobj(source_file, target)
                    target.flush()
                    os.fsync(target.fileno())
                staged.append({**asset, "temporary_path": temporary_path})

            for asset in staged:
                try:
                    os.link(asset["temporary_path"], asset["destination"])
                except FileExistsError:
                    if compute_file_sha256(asset["destination"]) != asset["sha256"]:
                        raise FileExistsError(
                            f"Refusing to overwrite a different canonical {asset['type']}: {asset['destination']}"
                        )
                    delivery_status = "already_present"
                else:
                    linked.append(asset)
                    delivery_status = "created"
                results[asset["type"]] = {
                    "workspace_path": asset["source"],
                    "canonical_path": asset["destination"],
                    "sha256": asset["sha256"],
                    "file_size_bytes": asset["file_size_bytes"],
                    "delivery_status": delivery_status,
                }
        except OSError:
            for asset in reversed(linked):
                if compute_file_sha256(asset["destination"]) == asset["sha256"]:
                    os.unlink(asset["destination"])
            raise
        finally:
            for asset in staged:
                if os.path.exists(asset["temporary_path"]):
                    os.unlink(asset["temporary_path"])

        return results

    def has_verified_deliverable(self) -> bool:
        """Idempotency check: verifies if production already has valid non-empty deliverables."""
        v_path = self.get_canonical_video_path()
        t_path = self.get_canonical_thumbnail_path()
        return (
            os.path.isfile(v_path)
            and os.path.isfile(t_path)
            and os.path.getsize(v_path) > 1024
            and os.path.getsize(t_path) > 1024
        )

    def prepare_staging_paths(self) -> Dict[str, str]:
        """
        Safeguard: Generates distinguishable temporary staging paths for rendering and thumbnailing.
        Temporary renders will not pollute root directories or overwrite deliverables prematurely.
        """
        staging_dir = os.path.join(self.prod_dir, "staging")
        os.makedirs(staging_dir, exist_ok=True)
        return {
            "staging_video": os.path.join(staging_dir, f"staging_{self.production_id}.mp4"),
            "staging_thumbnail": os.path.join(staging_dir, f"staging_{self.production_id}.png"),
        }

    def promote_staging_to_canonical(
        self,
        staging_video: str,
        staging_thumbnail: str,
        preserve_backup: bool = True,
    ) -> Dict[str, str]:
        """
        Safeguard: Atomically promotes staging deliverables to canonical outputs.
        Preserves existing deliverables as .bak until new deliverables are validated.
        """
        canonical_vid = self.get_canonical_video_path()
        canonical_thumb = self.get_canonical_thumbnail_path()

        vid_bak = canonical_vid + ".bak"
        thumb_bak = canonical_thumb + ".bak"

        try:
            if preserve_backup and os.path.isfile(canonical_vid):
                shutil.copy2(canonical_vid, vid_bak)
            if preserve_backup and os.path.isfile(canonical_thumb):
                shutil.copy2(canonical_thumb, thumb_bak)

            if os.path.isfile(staging_video):
                shutil.move(staging_video, canonical_vid)
            if os.path.isfile(staging_thumbnail):
                shutil.move(staging_thumbnail, canonical_thumb)

            # Register canonical artifacts
            self.register_artifact("STAGE_09_RENDERING", "video", canonical_vid, copy_into_workspace=False)
            self.register_artifact("STAGE_10_THUMBNAIL", "thumbnail", canonical_thumb, copy_into_workspace=False)

            # Cleanup backups after successful promotion
            if os.path.isfile(vid_bak):
                os.remove(vid_bak)
            if os.path.isfile(thumb_bak):
                os.remove(thumb_bak)

            return {
                "canonical_video": canonical_vid,
                "canonical_thumbnail": canonical_thumb,
                "status": "PROMOTED_SUCCESS",
            }
        except Exception as e:
            logger.error("Promotion failed, restoring previous backups: %s", e)
            if os.path.isfile(vid_bak):
                os.replace(vid_bak, canonical_vid)
            if os.path.isfile(thumb_bak):
                os.replace(thumb_bak, canonical_thumb)
            raise

    def initialize_workspace(self) -> Dict[str, str]:
        """Creates all production-specific directories and initializes the manifest."""
        dirs = [
            self.research_dir,
            self.scripts_dir,
            self.storyboards_dir,
            self.audio_dir,
            self.narration_dir,
            self.visual_timeline_dir,
            self.assets_dir,
            self.assets_acquired_dir,
            self.assets_generated_dir,
            self.assets_animations_dir,
            self.thumbnails_dir,
            self.video_dir,
            self.metadata_dir,
            self.reports_dir,
            self.audit_dir,
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)

        self._save_manifest()
        return {
            "production_id": self.production_id,
            "path": self.prod_dir,
            "status": "INITIALIZED",
        }

    def register_artifact(
        self,
        stage: str,
        artifact_type: str,
        source_path: str,
        target_filename: Optional[str] = None,
        copy_into_workspace: bool = True,
        *,
        asset_id: Optional[str] = None,
        source_url: Optional[str] = None,
        creator: Optional[str] = None,
        license_name: Optional[str] = None,
        license: Optional[str] = None,
        license_url: Optional[str] = None,
        generation_provider: Optional[str] = None,
        prompt_metadata: Optional[Dict[str, Any]] = None,
        width: int = 0,
        height: int = 0,
        fmt: str = "",
        format: str = "",
    ) -> ProductionArtifact:
        """
        Registers an artifact into the production workspace.
        Optionally copies it into the proper production subdirectory to guarantee isolation.
        """
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Source artifact not found: {source_path}")

        type_to_dir = {
            "research": self.research_dir,
            "script": self.scripts_dir,
            "storyboard": self.storyboards_dir,
            "audio": self.audio_dir,
            "narration": self.audio_dir,
            "visual_timeline": self.visual_timeline_dir,
            "acquired_asset": self.assets_acquired_dir,
            "generated_asset": self.assets_generated_dir,
            "animation": self.assets_animations_dir,
            "asset": self.assets_dir,
            "thumbnail": self.thumbnails_dir,
            "video": self.video_dir,
            "metadata": self.metadata_dir,
            "report": self.reports_dir,
            "audit": self.audit_dir,
        }

        dest_dir = type_to_dir.get(artifact_type, self.assets_dir)
        os.makedirs(dest_dir, exist_ok=True)
        fname = target_filename or os.path.basename(source_path)
        dest_path = os.path.join(dest_dir, fname)

        if copy_into_workspace and os.path.abspath(source_path) != os.path.abspath(dest_path):
            shutil.copy2(source_path, dest_path)
            final_path = dest_path
        else:
            final_path = source_path

        size_b = os.path.getsize(final_path)
        sha256 = compute_file_sha256(final_path)
        dim_info = _inspect_media_dimensions(final_path)
        resolved_w = width or dim_info.get("width", 0)
        resolved_h = height or dim_info.get("height", 0)
        resolved_fmt = fmt or format or dim_info.get("format", "")
        resolved_lic = license_name or license

        try:
            rel_p = os.path.relpath(final_path, self.workspace_root).replace("\\", "/")
        except ValueError:
            rel_p = os.path.abspath(final_path).replace("\\", "/")

        artifact = ProductionArtifact(
            stage=stage,
            artifact_type=artifact_type,
            file_path=final_path,
            relative_path=rel_p,
            file_size_bytes=size_b,
            sha256=sha256,
            created_at=datetime.now(timezone.utc).isoformat(),
            asset_id=asset_id,
            source_url=source_url,
            creator=creator,
            license=resolved_lic,
            license_url=license_url,
            generation_provider=generation_provider,
            prompt_metadata=prompt_metadata,
            width=resolved_w,
            height=resolved_h,
            format=resolved_fmt,
        )
        # Replace any duplicate entry for exact same path + artifact_type to keep manifest crisp
        self.artifacts = [
            a for a in self.artifacts
            if not (os.path.abspath(a.file_path) == os.path.abspath(final_path) and a.artifact_type == artifact_type)
        ]
        self.artifacts.append(artifact)
        self._save_manifest()
        return artifact

    def _save_manifest(self):
        """Atomically persists production manifest linking all assets to topic and production ID."""
        try:
            ws_rel = os.path.relpath(self.prod_dir, self.workspace_root).replace("\\", "/")
        except ValueError:
            ws_rel = os.path.abspath(self.prod_dir).replace("\\", "/")

        acquired = [a for a in self.artifacts if a.artifact_type == "acquired_asset"]
        generated = [a for a in self.artifacts if a.artifact_type == "generated_asset"]
        animations = [a for a in self.artifacts if a.artifact_type == "animation"]
        unique_asset_hashes = len({
            a.sha256 for a in (acquired + generated + animations) if a.sha256
        })

        def _deliverable_entry(art_type: str, fallback_path: str) -> Dict[str, Any]:
            matched = [a for a in self.artifacts if a.artifact_type == art_type]
            target_path = matched[-1].file_path if matched else fallback_path
            exists = os.path.isfile(target_path)
            size_b = os.path.getsize(target_path) if exists else 0
            sha = (matched[-1].sha256 if matched and matched[-1].sha256 else (compute_file_sha256(target_path) if exists else ""))
            try:
                rel_p = os.path.relpath(target_path, self.workspace_root).replace("\\", "/")
            except ValueError:
                rel_p = os.path.abspath(target_path).replace("\\", "/")
            return {
                "path": os.path.abspath(target_path) if target_path else "",
                "relative_path": rel_p,
                "exists": exists,
                "size_bytes": size_b,
                "sha256": sha,
            }

        canon_video = _deliverable_entry("video", self.get_canonical_video_path())
        canon_thumb = _deliverable_entry("thumbnail", self.get_canonical_thumbnail_path())
        deliverables = {
            "final_video": canon_video,
            "thumbnail": canon_thumb,
            "voiceover": _deliverable_entry("narration", os.path.join(self.audio_dir, f"voiceover_{self.production_id}.mp3")),
            "storyboard": _deliverable_entry("storyboard", os.path.join(self.storyboards_dir, f"storyboard_{self.production_id}.json")),
            "qc_report": _deliverable_entry("report", os.path.join(self.reports_dir, f"qc_report_{self.production_id}.json")),
            "integrity_report": _deliverable_entry("report", os.path.join(self.audit_dir, f"integrity_report_{self.production_id}.json")),
        }

        manifest_data = {
            "manifest_version": "3.21J",
            "schema_revision": "3.21K",
            "production_id": self.production_id,
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "category": self.category,
            "script_fingerprint": self.script_fingerprint,
            "storyboard_fingerprint": self.storyboard_fingerprint,
            "workspace_dir": ws_rel,
            "workspace_abs_dir": os.path.abspath(self.prod_dir),
            "canonical_video_path": canon_video["path"],
            "canonical_thumbnail_path": canon_thumb["path"],
            "deliverables": deliverables,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "asset_summary": {
                "acquired_images": len(acquired),
                "generated_images": len(generated),
                "animations": len(animations),
                "unique_visual_hashes": unique_asset_hashes,
            },
            "artifacts_count": len(self.artifacts),
            "artifacts": [asdict(a) for a in self.artifacts],
        }
        # Caller-supplied fields (save_manifest) survive every regeneration of the manifest.
        manifest_data.update(getattr(self, "_manifest_overrides", {}))
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        tmp_manifest = f"{self.manifest_path}.{os.getpid()}.tmp"
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)
        os.replace(tmp_manifest, self.manifest_path)

    def save_manifest(self, data: Dict[str, Any]) -> None:
        """
        Merges caller-supplied top-level fields (e.g. renderer, pinned_commit) into the
        production manifest and persists it atomically. The manifest is regenerated from the
        registered artifacts on every register_artifact(), so the supplied fields are kept as
        overrides and re-applied on each regeneration instead of being silently dropped.
        Fields the caller merely echoed back from load_manifest() are not frozen.
        """
        generated = self.load_manifest()
        overrides = getattr(self, "_manifest_overrides", None)
        if overrides is None:
            overrides = self._manifest_overrides = {}
        for key, value in (data or {}).items():
            if key in ("artifacts", "artifacts_count"):
                continue   # always derived from registered artifacts
            if key not in generated or generated[key] != value or key in overrides:
                overrides[key] = value
        self._save_manifest()

    def load_manifest(self) -> Dict[str, Any]:
        """Loads the current production manifest."""
        if not os.path.isfile(self.manifest_path):
            return {}
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
