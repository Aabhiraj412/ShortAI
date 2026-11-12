"""
YouTube Data API v3 integration: OAuth2 authentication, video upload,
custom thumbnail upload, and scheduled publishing for 10:00 AM IST.
Maintains backwards compatibility and delegates to YouTubePublishingEngine.
"""

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeOAuthManager,
    MetadataGenerator,
    YouTubeMetadata,
    youtube_publishing_engine,
    PublicationRecord,
    compute_file_sha256,
    ui_queue_status,
)

IST_TZ = ZoneInfo("Asia/Kolkata")


def parse_ist_to_utc_iso(ist_dt_str: str) -> str:
    """Parses an Asia/Kolkata datetime string (e.g. '2026-11-13T18:30') and returns UTC ISO-8601 'Z'."""
    raw = (ist_dt_str or "").strip()
    if not raw:
        raise ValueError("Publication date and time in IST is required.")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST_TZ)
    utc_dt = parsed.astimezone(timezone.utc)
    if utc_dt <= datetime.now(timezone.utc):
        raise ValueError("Scheduled publication time must be in the future (Asia/Kolkata).")
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class YouTubePublisherService:
    def __init__(self):
        self.engine = youtube_publishing_engine
        self.oauth = self.engine.oauth

    def get_auth_status(self) -> Dict:
        """Returns detailed OAuth credentials and channel connection status."""
        return self.oauth.get_auth_status()

    def get_youtube_client(self):
        """Constructs authenticated YouTube Data API client."""
        return self.oauth.get_authenticated_service()

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list,
        category_id: str = "28",
        privacy_status: str = "private",
        publish_at_iso: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Uploads video to YouTube with resumable upload and safety gates.
        Returns: (success: bool, video_id: str, error_message: str)
        """
        metadata = YouTubeMetadata(
            title=title,
            description=description,
            tags=tags,
            hashtags=["#Shorts"],
            category_id=category_id,
        )
        res = self.engine.upload_short(
            video_path=video_path,
            topic_title=title,
            metadata=metadata,
            visibility=privacy_status,
        )
        return res.get("success", False), res.get("video_id"), res.get("error")

    def queue_approved_production(
        self,
        publication_id: int,
        video_path: str,
        thumbnail_path: Optional[str],
        metadata: YouTubeMetadata,
    ) -> Dict:
        if not os.path.isfile(video_path) or not video_path.lower().endswith(".mp4"):
            raise ValueError("A completed local MP4 is required.")
        if thumbnail_path and not os.path.isfile(thumbnail_path):
            raise ValueError("The production thumbnail does not exist.")
        metadata.source_publication_id = publication_id
        record = PublicationRecord(
            id=None,
            topic_title=metadata.title,
            video_path=os.path.abspath(video_path),
            thumbnail_path=os.path.abspath(thumbnail_path) if thumbnail_path else None,
            metadata_json=json.dumps(asdict(metadata)),
            youtube_video_id=None,
            upload_status="pending_queue",
            visibility="private",
            video_hash=compute_file_sha256(video_path),
        )
        ledger_id = self.engine.db.record_publication(record)
        queued = self.engine.db.get_publication(ledger_id)
        if not queued:
            raise RuntimeError("The publishing ledger did not persist the queued production.")
        if queued["upload_status"] not in ("pending_queue", "approved", "uploading"):
            raise ValueError("A duplicate or previously completed production cannot be queued again.")
        return self._safe_publication(queued)

    def approve_and_upload(
        self,
        ledger_id: int,
        confirmed: bool,
        publish_at_iso: Optional[str] = None,
    ) -> Dict:
        if confirmed is not True:
            raise PermissionError("Explicit operator confirmation is required.")
        queued = self.engine.db.get_publication(ledger_id)
        if not queued:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        from backend.app.services.control_center_service import is_automatic_publishing_authorized
        auto_authorized, _ = is_automatic_publishing_authorized()
        target_publish_at = publish_at_iso or queued.get("scheduled_timestamp")
        target_visibility = str(queued.get("visibility") or "private").lower()
        if target_publish_at:
            target_visibility = "private"
        if target_visibility != "private" and not auto_authorized:
            raise PermissionError("Stage 3.22 only permits private uploads.")
        if queued["upload_status"] not in (
            "pending_queue", "upload_interrupted", "failed", "awaiting_auth"
        ):
            raise ValueError("This publishing record is not eligible for manual approval.")

        approved = self.engine.db.approve_upload(ledger_id)
        meta_dict = json.loads(approved["metadata_json"])
        meta_dict.pop("_schedule_context", None)
        metadata = YouTubeMetadata(**meta_dict)
        result = self.engine.upload_short(
            video_path=approved["video_path"],
            topic_title=approved["topic_title"],
            metadata=metadata,
            thumbnail_path=approved["thumbnail_path"],
            visibility=target_visibility if (auto_authorized and not target_publish_at) else "private",
            ledger_id=ledger_id,
            publish_at_iso=target_publish_at,
            allow_public_bypass=bool(auto_authorized and target_visibility != "private" and not target_publish_at),
        )
        self._update_application_publication(metadata.source_publication_id, result)
        return result

    def update_queue_item(
        self,
        ledger_id: int,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        hashtags: Optional[List[str]] = None,
        thumbnail_path: Optional[str] = None,
    ) -> Dict:
        """Saves user edits to title, description, tags, hashtags, or thumbnail without uploading."""
        if title is not None and not title.strip():
            raise ValueError("Video title cannot be empty.")
        if thumbnail_path is not None and thumbnail_path != "" and not os.path.isfile(thumbnail_path):
            raise ValueError(f"Thumbnail file not found: {thumbnail_path}")
        updated = self.engine.db.update_queue_metadata_and_schedule(
            ledger_id,
            title=title,
            description=description,
            tags=tags,
            hashtags=hashtags,
            thumbnail_path=thumbnail_path or None,
        )
        if updated.get("youtube_video_id"):
            self.engine.update_or_reschedule_remote_video(
                ledger_id,
                publish_at_iso=updated.get("scheduled_timestamp"),
                thumbnail_path=updated.get("thumbnail_path"),
            )
        return self.get_upload(ledger_id) or {}

    def edit_and_schedule(
        self,
        ledger_id: int,
        *,
        scheduled_ist: str,
        confirm: bool = True,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        hashtags: Optional[List[str]] = None,
        thumbnail_path: Optional[str] = None,
    ) -> Dict:
        """Validates user-selected IST time -> UTC, updates metadata, uploads private+publishAt, and verifies."""
        if confirm is not True:
            raise PermissionError("Explicit user confirmation is required to schedule publication.")
        if title is not None and not title.strip():
            raise ValueError("Video title cannot be empty.")
        if thumbnail_path and not os.path.isfile(thumbnail_path):
            raise ValueError(f"Thumbnail file not found: {thumbnail_path}")
        publish_at_utc = parse_ist_to_utc_iso(scheduled_ist)
        queued = self.engine.db.get_publication(ledger_id)
        if not queued:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")

        updated = self.engine.db.update_queue_metadata_and_schedule(
            ledger_id,
            title=title,
            description=description,
            tags=tags,
            hashtags=hashtags,
            thumbnail_path=thumbnail_path,
            scheduled_timestamp=publish_at_utc,
            error_details=None,
        )
        if updated.get("youtube_video_id"):
            res = self.engine.update_or_reschedule_remote_video(
                ledger_id,
                publish_at_iso=publish_at_utc,
                thumbnail_path=updated.get("thumbnail_path"),
            )
            return {"result": res, "item": self.get_upload(ledger_id)}

        if updated["upload_status"] != "approved":
            self.engine.db.approve_upload(ledger_id)
        meta_dict = json.loads(updated["metadata_json"])
        meta_dict.pop("_schedule_context", None)
        metadata = YouTubeMetadata(**meta_dict)
        res = self.engine.upload_short(
            video_path=updated["video_path"],
            topic_title=updated["topic_title"],
            metadata=metadata,
            thumbnail_path=updated.get("thumbnail_path"),
            visibility="private",
            ledger_id=ledger_id,
            publish_at_iso=publish_at_utc,
            allow_public_bypass=False,
        )
        self._update_application_publication(metadata.source_publication_id, res)
        return {"result": res, "item": self.get_upload(ledger_id)}

    def publish_now(self, ledger_id: int, confirm: bool = True) -> Dict:
        """Immediately publishes a queued or scheduled video publicly upon explicit user confirmation."""
        if confirm is not True:
            raise PermissionError("Explicit user confirmation is required to publish immediately.")
        queued = self.engine.db.get_publication(ledger_id)
        if not queued:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        if queued.get("youtube_video_id"):
            res = self.engine.update_or_reschedule_remote_video(ledger_id, make_public_now=True)
            return {"result": res, "item": self.get_upload(ledger_id)}

        if queued["upload_status"] != "approved":
            self.engine.db.approve_upload(ledger_id)
        meta_dict = json.loads(queued["metadata_json"])
        meta_dict.pop("_schedule_context", None)
        metadata = YouTubeMetadata(**meta_dict)
        res = self.engine.upload_short(
            video_path=queued["video_path"],
            topic_title=queued["topic_title"],
            metadata=metadata,
            thumbnail_path=queued.get("thumbnail_path"),
            visibility="public",
            ledger_id=ledger_id,
            publish_at_iso=None,
            allow_public_bypass=True,
        )
        self._update_application_publication(metadata.source_publication_id, res)
        return {"result": res, "item": self.get_upload(ledger_id)}

    def cancel_schedule(self, ledger_id: int, confirm: bool = True) -> Dict:
        """Cancels scheduled release on YouTube (if uploaded) and returns item to Ready to Schedule."""
        if confirm is not True:
            raise PermissionError("Explicit user confirmation is required to cancel schedule.")
        queued = self.engine.db.get_publication(ledger_id)
        if not queued:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        if queued.get("youtube_video_id"):
            res = self.engine.update_or_reschedule_remote_video(ledger_id, cancel_schedule=True)
            return {"result": res, "item": self.get_upload(ledger_id)}
        self.engine.db.update_queue_metadata_and_schedule(
            ledger_id,
            scheduled_timestamp=None,
            upload_status="pending_queue",
            visibility="private",
            error_details=None,
        )
        return {"result": {"success": True, "status": "pending_queue"}, "item": self.get_upload(ledger_id)}

    def get_upload(self, ledger_id: int) -> Optional[Dict]:
        record = self.engine.db.get_publication(ledger_id)
        if not record:
            return None
        state = self.engine.db.get_upload_state(ledger_id)
        record["upload_state"] = {
            "progress_pct": state.get("progress_pct", 0.0),
            "retry_count": state.get("retry_count", 0),
            "verification_status": state.get("verification_status", "pending"),
            "approved_at": state.get("approved_at"),
            "updated_at": state.get("updated_at"),
            "last_error": state.get("last_error"),
        }
        return self._safe_publication(record)

    def list_uploads(self, statuses: Optional[List[str]] = None, limit: int = 100) -> List[Dict]:
        return [self.get_upload(item["id"]) for item in self.engine.db.list_publications(statuses, limit)]

    @staticmethod
    def _safe_publication(record: Dict) -> Dict:
        from backend.app.services.control_center_service import get_publishing_policy
        from backend.app.services.youtube_publishing_engine import (
            build_publishing_timeline,
            normalize_lifecycle_state,
        )

        metadata = json.loads(record.get("metadata_json") or "{}")
        up_state = record.get("upload_state") or {}
        merged_for_tl = dict(record)
        merged_for_tl["verification_status"] = up_state.get("verification_status")
        lifecycle_state = normalize_lifecycle_state(
            record.get("upload_status", ""),
            record.get("visibility", "private"),
            record.get("scheduled_timestamp"),
            up_state.get("verification_status"),
            record.get("error_details"),
        )
        policy = get_publishing_policy()
        requires_manual = (
            policy != "automatic"
            and lifecycle_state == "PENDING_QUEUE"
        )
        sched_ctx = metadata.get("_schedule_context") or {}
        sched_utc = record.get("scheduled_timestamp")
        sched_ist = None
        sched_ist_input = None
        if sched_utc and "T" in str(sched_utc):
            try:
                dt_utc = datetime.fromisoformat(str(sched_utc).replace("Z", "+00:00"))
                dt_ist = dt_utc.astimezone(IST_TZ)
                sched_ist = dt_ist.strftime("%Y-%m-%d %H:%M IST")
                sched_ist_input = dt_ist.strftime("%Y-%m-%dT%H:%M")
            except Exception:
                sched_ist = str(sched_utc)
        vid = record.get("youtube_video_id")
        return {
            "id": record["id"],
            "title": metadata.get("title") or record.get("topic_title"),
            "description": metadata.get("description") or "",
            "tags": metadata.get("tags") or [],
            "hashtags": metadata.get("hashtags") or ["#Shorts"],
            "topic_title": record.get("topic_title"),
            "video_path": record.get("video_path"),
            "thumbnail_path": record.get("thumbnail_path"),
            "status": record.get("upload_status"),
            "ui_status": ui_queue_status(record.get("upload_status", ""), record.get("error_details")),
            "lifecycle_state": lifecycle_state,
            "youtube_scheduled_state": lifecycle_state,
            "requires_manual_approval": requires_manual,
            "visibility": record.get("visibility"),
            "youtube_privacy_status": record.get("visibility"),
            "youtube_video_id": vid,
            "youtube_url": f"https://youtu.be/{vid}" if vid else None,
            "scheduled_timestamp": sched_utc,
            "scheduled_utc_publish_at": sched_utc,
            "scheduled_ist_display": sched_ist or "Not Scheduled",
            "scheduled_ist_input": sched_ist_input or "",
            "production_cycle_id": sched_ctx.get("production_cycle_id"),
            "upload_timestamp": record.get("upload_timestamp"),
            "error": record.get("error_details") or None,
            "upload_state": up_state,
            "timeline": build_publishing_timeline(merged_for_tl),
        }

    @staticmethod
    def _update_application_publication(publication_id: Optional[int], result: Dict) -> None:
        if publication_id is None:
            return
        from backend.app.database.db import PublicationModel, SessionLocal

        with SessionLocal() as db:
            publication = db.query(PublicationModel).filter(PublicationModel.id == publication_id).first()
            if publication:
                publication.youtube_video_id = result.get("video_id")
                if result.get("success"):
                    publication.status = "uploaded_private"
                    publication.published_at = datetime.utcnow()
                else:
                    publication.status = result.get("status", "upload_failed")
                db.commit()

    def upload_thumbnail(self, video_id: str, thumbnail_path: str) -> Tuple[bool, Optional[str]]:
        """Uploads custom thumbnail to YouTube video."""
        service = self.oauth.get_authenticated_service()
        if not service:
            return False, "Not authenticated with YouTube."
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(thumbnail_path, mimetype="image/png")
            service.thumbnails().set(videoId=video_id, media_body=media).execute()
            return True, None
        except Exception as e:
            return False, str(e)
