"""
YouTube Data API v3 integration: OAuth2 authentication, video upload,
custom thumbnail upload, and scheduled publishing for 10:00 AM IST.
Maintains backwards compatibility and delegates to YouTubePublishingEngine.
"""

from typing import Dict, Optional, Tuple
from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeOAuthManager,
    MetadataGenerator,
    YouTubeMetadata,
    youtube_publishing_engine,
)


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
