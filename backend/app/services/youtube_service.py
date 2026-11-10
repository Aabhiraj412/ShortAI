"""
YouTube Data API v3 integration: OAuth2 authentication, video upload,
custom thumbnail upload, and scheduled publishing for 10:00 AM IST.
"""
import os
import time
from typing import Dict, Optional, Tuple
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from app.core.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

class YouTubePublisherService:
    def __init__(self):
        self.secrets_file = settings.YOUTUBE_CLIENT_SECRETS_FILE
        self.token_file = settings.YOUTUBE_TOKEN_FILE

    def get_auth_status(self) -> Dict:
        """Returns whether OAuth credentials and client secrets are installed."""
        secrets_exist = os.path.exists(self.secrets_file)
        token_exist = os.path.exists(self.token_file)
        is_authenticated = False
        
        if token_exist:
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
                is_authenticated = creds.valid or (creds.expired and creds.refresh_token is not None)
            except Exception:
                is_authenticated = False
                
        return {
            "secrets_configured": secrets_exist,
            "token_configured": token_exist,
            "is_authenticated": is_authenticated,
            "secrets_path": self.secrets_file,
            "token_path": self.token_file
        }

    def get_youtube_client(self):
        """Constructs authenticated YouTube Data API client."""
        if not os.path.exists(self.token_file):
            return None
        creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        return build("youtube", "v3", credentials=creds)

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
        Uploads video to YouTube with resumable upload.
        Returns: (success: bool, video_id: str, error_message: str)
        """
        youtube = self.get_youtube_client()
        if not youtube:
            return False, None, "YouTube OAuth token is not configured yet. Upload skipped for manual approval."
            
        status_body = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
        if publish_at_iso and privacy_status == "private":
            status_body["publishAt"] = publish_at_iso
            
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags,
                "categoryId": category_id
            },
            "status": status_body
        }
        
        try:
            media = MediaFileUpload(video_path, chunksize=1024*1024*4, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"Uploaded {int(status.progress() * 100)}% to YouTube...", flush=True)
                    
            video_id = response.get("id")
            print(f"Video uploaded successfully! YouTube ID: {video_id}", flush=True)
            return True, video_id, None
        except Exception as e:
            return False, None, str(e)

    def upload_thumbnail(self, video_id: str, thumbnail_path: str) -> Tuple[bool, Optional[str]]:
        """Uploads custom thumbnail to YouTube video."""
        youtube = self.get_youtube_client()
        if not youtube:
            return False, "Not authenticated with YouTube."
            
        try:
            media = MediaFileUpload(thumbnail_path, mimetype="image/png")
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=media
            ).execute()
            print(f"Thumbnail uploaded successfully for video {video_id}!", flush=True)
            return True, None
        except Exception as e:
            print(f"Thumbnail upload failed (may require verified YouTube account): {e}", flush=True)
            return False, str(e)
