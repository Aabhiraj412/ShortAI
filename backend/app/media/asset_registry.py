"""
SQLite Media Asset Registry.

Persists, queries, and deduplicates media assets using the SQLite database.
"""

import logging
from typing import List, Optional

from backend.app.database.db import MediaAssetModel, SessionLocal
from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset

logger = logging.getLogger(__name__)


class AssetRegistry:
    """Manages CRUD and deduplication operations for media assets in SQLite."""

    def register_asset(self, asset: MediaAsset, job_id: Optional[str] = None) -> Optional[int]:
        """
        Saves or updates a MediaAsset record in the database.
        Returns the database primary key ID.
        """
        try:
            with SessionLocal() as session:
                existing = session.query(MediaAssetModel).filter_by(asset_id=asset.asset_id).first()
                if existing:
                    # Update paths and verification
                    existing.local_path = asset.local_path
                    existing.portrait_path = asset.portrait_path
                    existing.asset_hash = asset.asset_hash
                    existing.verification_status = asset.verification_status.value
                    existing.relevance_score = asset.relevance_score
                    if job_id:
                        existing.job_id = job_id
                    session.commit()
                    return existing.id

                record = MediaAssetModel(
                    asset_id=asset.asset_id,
                    title=asset.title,
                    creator=asset.creator,
                    source_repository=asset.source_repository,
                    source_url=asset.source_url,
                    download_url=asset.download_url,
                    license_id=asset.license_type.value,
                    license_url=asset.license_url,
                    attribution_text=asset.attribution_text,
                    local_path=asset.local_path,
                    portrait_path=asset.portrait_path,
                    asset_hash=asset.asset_hash,
                    mime_type=asset.mime_type,
                    file_size_bytes=asset.file_size_bytes,
                    width=asset.width,
                    height=asset.height,
                    aspect_ratio=round(asset.width / max(1, asset.height), 3),
                    relevance_score=asset.relevance_score,
                    search_query=asset.search_query,
                    verification_status=asset.verification_status.value,
                    rejection_reason=asset.rejection_reason,
                    job_id=job_id
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                return record.id
        except Exception as e:
            logger.error("Error registering media asset '%s': %s", asset.asset_id, e)
            return None

    def get_by_hash(self, sha256_hash: str) -> Optional[MediaAssetModel]:
        """Checks if an identical file hash already exists in the registry."""
        if not sha256_hash:
            return None
        try:
            with SessionLocal() as session:
                return session.query(MediaAssetModel).filter_by(asset_hash=sha256_hash).first()
        except Exception as e:
            logger.error("Error checking asset hash: %s", e)
            return None

    def get_by_asset_id(self, asset_id: str) -> Optional[MediaAssetModel]:
        """Fetches an asset record by its unique provider ID."""
        try:
            with SessionLocal() as session:
                return session.query(MediaAssetModel).filter_by(asset_id=asset_id).first()
        except Exception as e:
            logger.error("Error fetching asset ID '%s': %s", asset_id, e)
            return None

    def get_approved_assets_for_job(self, job_id: str) -> List[MediaAssetModel]:
        """Fetches all approved assets associated with a specific job."""
        try:
            with SessionLocal() as session:
                return session.query(MediaAssetModel).filter_by(
                    job_id=job_id,
                    verification_status=LicenseStatus.APPROVED.value
                ).all()
        except Exception as e:
            logger.error("Error fetching assets for job '%s': %s", job_id, e)
            return []
