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
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
PRODUCTIONS_BASE_DIR = os.path.join(WORKSPACE_ROOT, "media", "productions")


def compute_file_sha256(filepath: str) -> str:
    if not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ProductionArtifact:
    stage: str
    artifact_type: str  # research, script, audio, video, thumbnail, metadata, report
    file_path: str
    relative_path: str
    file_size_bytes: int
    sha256: str
    created_at: str


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

        # Standard isolated subdirectories
        self.research_dir = os.path.join(self.prod_dir, "research")
        self.scripts_dir = os.path.join(self.prod_dir, "scripts")
        self.narration_dir = os.path.join(self.prod_dir, "narration")
        self.visual_timeline_dir = os.path.join(self.prod_dir, "visual_timeline")
        self.assets_dir = os.path.join(self.prod_dir, "assets")
        self.thumbnails_dir = os.path.join(self.prod_dir, "thumbnails")
        self.video_dir = os.path.join(self.prod_dir, "video")
        self.metadata_dir = os.path.join(self.prod_dir, "metadata")
        self.reports_dir = os.path.join(self.prod_dir, "reports")

        self.manifest_path = os.path.join(self.prod_dir, "production_manifest.json")
        self.artifacts: List[ProductionArtifact] = []

    def get_canonical_video_path(self, filename: Optional[str] = None) -> str:
        """Returns the authoritative final video destination for this production."""
        fname = filename or f"showcase_{self.production_id}.mp4"
        return os.path.join(self.video_dir, fname)

    def get_canonical_thumbnail_path(self, filename: Optional[str] = None) -> str:
        """Returns the authoritative final thumbnail destination for this production."""
        fname = filename or f"thumb_{self.production_id}.png"
        return os.path.join(self.thumbnails_dir, fname)

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
            self.narration_dir,
            self.visual_timeline_dir,
            self.assets_dir,
            self.thumbnails_dir,
            self.video_dir,
            self.metadata_dir,
            self.reports_dir,
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
            "audio": self.narration_dir,
            "narration": self.narration_dir,
            "visual_timeline": self.visual_timeline_dir,
            "asset": self.assets_dir,
            "thumbnail": self.thumbnails_dir,
            "video": self.video_dir,
            "metadata": self.metadata_dir,
            "report": self.reports_dir,
        }

        dest_dir = type_to_dir.get(artifact_type, self.assets_dir)
        fname = target_filename or os.path.basename(source_path)
        dest_path = os.path.join(dest_dir, fname)

        if copy_into_workspace and os.path.abspath(source_path) != os.path.abspath(dest_path):
            shutil.copy2(source_path, dest_path)
            final_path = dest_path
        else:
            final_path = source_path

        size_b = os.path.getsize(final_path)
        sha256 = compute_file_sha256(final_path)
        rel_p = os.path.relpath(final_path, self.workspace_root).replace("\\", "/")

        artifact = ProductionArtifact(
            stage=stage,
            artifact_type=artifact_type,
            file_path=final_path,
            relative_path=rel_p,
            file_size_bytes=size_b,
            sha256=sha256,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.artifacts.append(artifact)
        self._save_manifest()
        return artifact

    def _save_manifest(self):
        """Persists production manifest linking all assets to topic and production ID."""
        manifest_data = {
            "production_id": self.production_id,
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "category": self.category,
            "workspace_dir": os.path.relpath(self.prod_dir, self.workspace_root).replace("\\", "/"),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "artifacts_count": len(self.artifacts),
            "artifacts": [asdict(a) for a in self.artifacts],
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

    def load_manifest(self) -> Dict[str, Any]:
        """Loads the current production manifest."""
        if not os.path.isfile(self.manifest_path):
            return {}
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
