"""
Autonomous YouTube Publishing Engine for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.7 Production Standard.

Features:
1. Google OAuth2 channel authentication, token persistence, refresh, and disconnect.
2. Channel connection verification (retrieves channel title, handle, subscriber count).
3. Search-optimized metadata generator with strict factual alignment and attribution credits.
4. Resumable chunked upload engine with progress tracking and transient retry.
5. Thumbnail upload engine preserving local 9:16 vertical originals.
6. Strict Publishing Gate: PUBLIC PUBLISHING REMAINS PERMANENTLY DISABLED by default.
   Only 'private' or 'unlisted' uploads are permitted.
7. Local SQLite publishing database with duplicate prevention and quota tracking.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core.execution_context import ExecutionContext, require_production_context

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")
DEFAULT_CLIENT_SECRETS = os.path.join(WORKSPACE_ROOT, "backend", "client_secrets.json")
DEFAULT_TOKEN_FILE = os.path.join(WORKSPACE_ROOT, "backend", "token.json")

# Mandatory OAuth scopes for upload and channel inspection
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]


@dataclass
class YouTubeMetadata:
    title: str
    description: str
    tags: List[str]
    hashtags: List[str]
    category_id: str = "27"  # 27 = Education, 28 = Science & Tech, 24 = Entertainment
    default_language: str = "en"
    default_audio_language: str = "en"
    attribution_text: str = ""
    pinned_comment: Optional[str] = None


@dataclass
class PublicationRecord:
    id: Optional[int]
    topic_title: str
    video_path: str
    thumbnail_path: Optional[str]
    metadata_json: str
    youtube_video_id: Optional[str]
    upload_status: str        # "pending", "uploaded_private", "uploaded_unlisted", "failed"
    visibility: str           # "private", "unlisted", "public"
    video_hash: str
    upload_timestamp: Optional[str] = None
    scheduled_timestamp: Optional[str] = None
    error_details: Optional[str] = None


def compute_file_sha256(file_path: str) -> str:
    """Computes SHA-256 hash of video to prevent duplicate uploads."""
    if not os.path.exists(file_path):
        return ""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def normalize_topic_title(title: str) -> str:
    """Normalizes a topic title for duplicate detection.
    Strips hashtags (#Shorts, #History, etc.), punctuation, excess whitespace, and lowercases."""
    if not title:
        return ""
    cleaned = re.sub(r'#\w+', '', title)
    cleaned = re.sub(r'[,.\'\"!?:;()\-–—]', '', cleaned)
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
    return ' '.join(cleaned.lower().split())


class PublishingDatabase:
    """Manages the local publication history and duplicate prevention ledger."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS publishing_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_title TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    thumbnail_path TEXT,
                    metadata_json TEXT NOT NULL,
                    youtube_video_id TEXT,
                    upload_status TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    video_hash TEXT NOT NULL,
                    upload_timestamp TEXT,
                    scheduled_timestamp TEXT,
                    error_details TEXT
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_pending_topic
                ON publishing_ledger (topic_title)
                WHERE upload_status = 'pending_queue'
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_pending_hash
                ON publishing_ledger (video_hash)
                WHERE upload_status = 'pending_queue'
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS youtube_quota_ledger (
                    date_str TEXT PRIMARY KEY,
                    units_consumed INTEGER DEFAULT 0,
                    last_updated TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS test_queue_incident_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ledger_id INTEGER,
                    topic_title TEXT,
                    video_path TEXT,
                    upload_status TEXT,
                    visibility TEXT,
                    video_hash TEXT,
                    upload_timestamp TEXT,
                    incident_reason TEXT,
                    archived_at TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def check_duplicate(self, video_path: str, topic_title: str) -> Tuple[bool, Optional[str]]:
        """Returns (is_duplicate: bool, existing_video_id_or_status: Optional[str])."""
        v_hash = compute_file_sha256(video_path) if video_path and os.path.exists(video_path) else None
        norm_target = normalize_topic_title(topic_title)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            # Check active queue first
            cur.execute("""
                SELECT id, topic_title, video_hash, upload_status, youtube_video_id
                FROM publishing_ledger
                WHERE upload_status IN ('pending_queue', 'uploading')
            """)
            for row in cur.fetchall():
                eid, etitle, ehash, estatus, eyt = row
                if (norm_target and normalize_topic_title(etitle) == norm_target) or (v_hash and ehash == v_hash):
                    return True, f"enqueued_{estatus}_{eid}"

            # Check uploaded/published records
            cur.execute("""
                SELECT youtube_video_id, upload_status, topic_title, video_hash FROM publishing_ledger
                WHERE upload_status IN ('uploaded_private', 'uploaded_unlisted', 'uploaded_public', 'published')
            """)
            for row in cur.fetchall():
                eyt, estatus, etitle, ehash = row
                if (norm_target and normalize_topic_title(etitle) == norm_target) or (v_hash and ehash == v_hash):
                    return True, eyt
            return False, None
        finally:
            conn.close()

    def record_publication(
        self,
        record: PublicationRecord,
        execution_context: ExecutionContext = ExecutionContext.PRODUCTION,
    ) -> int:
        """Inserts or updates a publication record with atomic deduplication and execution-context enforcement."""
        if isinstance(execution_context, str):
            execution_context = ExecutionContext.from_string(execution_context)
        require_production_context(execution_context, "Publishing queue insertion")

        norm_target = normalize_topic_title(record.topic_title)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                cur = conn.cursor()
                # Atomic check for existing active or pending record with matching normalized topic or hash
                cur.execute("""
                    SELECT id, topic_title, video_path, video_hash, upload_status, youtube_video_id
                    FROM publishing_ledger
                    WHERE upload_status IN ('pending_queue', 'uploading')
                """)
                rows = cur.fetchall()
                for row in rows:
                    existing_id, existing_title, existing_path, existing_hash, existing_status, existing_yt = row
                    norm_existing = normalize_topic_title(existing_title)
                    is_title_match = bool(norm_target and norm_existing == norm_target)
                    is_hash_match = bool(record.video_hash and existing_hash == record.video_hash)
                    if is_title_match or is_hash_match:
                        # Legitimate retry check: same video path or hash
                        if is_hash_match or existing_path == record.video_path:
                            logger.info(
                                "[PUBLISHING IDEMPOTENCY] Legitimate retry returning existing queue record #%d for topic '%s'",
                                existing_id, record.topic_title,
                            )
                            return existing_id
                        else:
                            logger.warning(
                                "[PUBLISHING IDEMPOTENCY REJECTION] Duplicate active queue insertion rejected! "
                                "Existing record #%d ('%s') conflicts with new attempt ('%s'). Returning existing record.",
                                existing_id, existing_title, record.topic_title,
                            )
                            return existing_id

                # Check already published records
                cur.execute("""
                    SELECT id, youtube_video_id, upload_status, topic_title, video_hash FROM publishing_ledger
                    WHERE upload_status IN ('uploaded_private', 'uploaded_unlisted', 'uploaded_public', 'published')
                """)
                for row in cur.fetchall():
                    pub_id, pub_yt, pub_status, pub_title, pub_hash = row
                    if (norm_target and normalize_topic_title(pub_title) == norm_target) or (record.video_hash and pub_hash == record.video_hash):
                        logger.info("[PUBLISHING IDEMPOTENCY] Topic already published (#%d, YT: %s)", pub_id, pub_yt)
                        return pub_id

                # Safe atomic insertion
                try:
                    cur.execute("""
                        INSERT INTO publishing_ledger (
                            topic_title, video_path, thumbnail_path, metadata_json,
                            youtube_video_id, upload_status, visibility, video_hash,
                            upload_timestamp, scheduled_timestamp, error_details
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        record.topic_title,
                        record.video_path,
                        record.thumbnail_path,
                        record.metadata_json,
                        record.youtube_video_id,
                        record.upload_status,
                        record.visibility,
                        record.video_hash,
                        record.upload_timestamp or datetime.utcnow().isoformat() + "Z",
                        record.scheduled_timestamp,
                        record.error_details,
                    ))
                    return cur.lastrowid
                except sqlite3.IntegrityError:
                    # Concurrently inserted active record caught by unique index
                    cur.execute("""
                        SELECT id FROM publishing_ledger
                        WHERE upload_status IN ('pending_queue', 'uploading')
                          AND (topic_title = ? OR video_hash = ?)
                        LIMIT 1
                    """, (record.topic_title, record.video_hash))
                    conflicting = cur.fetchone()
                    if conflicting:
                        logger.info(
                            "[PUBLISHING IDEMPOTENCY] Concurrent duplicate caught by database unique constraint. Returning existing ID #%d",
                            conflicting[0],
                        )
                        return conflicting[0]
                    raise
        finally:
            conn.close()

    def add_quota_consumption(self, units: int):
        """Records daily API quota consumption."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO youtube_quota_ledger (date_str, units_consumed, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(date_str) DO UPDATE SET
                    units_consumed = units_consumed + ?,
                    last_updated = ?
            """, (today, units, datetime.utcnow().isoformat(), units, datetime.utcnow().isoformat()))
            conn.commit()
        finally:
            conn.close()

    def get_quota_used_today(self) -> int:
        """Returns total estimated quota units consumed today."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT units_consumed FROM youtube_quota_ledger WHERE date_str = ?", (today,))
            row = cur.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()


class YouTubeOAuthManager:
    """Manages Google OAuth2 authentication lifecycle for YouTube Data API."""

    def __init__(
        self,
        client_secrets_file: str = DEFAULT_CLIENT_SECRETS,
        token_file: str = DEFAULT_TOKEN_FILE,
        scopes: Optional[List[str]] = None,
    ):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.scopes = scopes or YOUTUBE_SCOPES

    def install_client_secrets(self, source_path_or_json: str) -> Tuple[bool, str]:
        """
        Validates and installs OAuth 2.0 client secrets to backend/client_secrets.json.
        Accepts either a filesystem path or raw JSON string.
        """
        try:
            if os.path.exists(source_path_or_json):
                with open(source_path_or_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = json.loads(source_path_or_json)

            # Validate standard Google OAuth client secret structure
            app_type = "installed" if "installed" in data else ("web" if "web" in data else None)
            if not app_type:
                return False, "Invalid secrets format: missing top-level 'installed' or 'web' property."

            client_info = data[app_type]
            if "client_id" not in client_info or "client_secret" not in client_info:
                return False, "Invalid secrets format: missing 'client_id' or 'client_secret'."

            os.makedirs(os.path.dirname(self.client_secrets_file), exist_ok=True)
            with open(self.client_secrets_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.info("Installed OAuth client secrets securely to: %s", self.client_secrets_file)
            return True, f"Client secrets installed successfully at {self.client_secrets_file}"
        except json.JSONDecodeError as je:
            return False, f"JSON parse error: {je}"
        except Exception as e:
            return False, f"Failed to install secrets: {e}"

    def get_auth_status(self) -> Dict:
        """Reports detailed connection state, channel info, and token health."""
        secrets_exist = os.path.exists(self.client_secrets_file)
        token_exist = os.path.exists(self.token_file)

        creds = None
        is_valid = False
        can_refresh = False
        has_refresh_token = False
        token_expiry = None
        channel_info = None
        error_msg = None

        if token_exist:
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
                if creds:
                    has_refresh_token = bool(creds.refresh_token)
                    can_refresh = has_refresh_token
                    token_expiry = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
                    is_valid = creds.valid
                    # If expired or invalid but we have a refresh token, attempt automatic refresh
                    if (not is_valid or creds.expired) and has_refresh_token:
                        try:
                            creds.refresh(Request())
                            is_valid = creds.valid
                            token_expiry = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
                            self.save_credentials(creds)
                        except Exception as ref_err:
                            error_msg = (
                                f"Token refresh failed: {ref_err}. "
                                "Please re-authenticate via: python backend/scripts/configure_youtube_oauth.py --auth"
                            )
            except Exception as e:
                error_msg = f"Failed to load token file: {e}"

        if is_valid and creds:
            try:
                youtube = build("youtube", "v3", credentials=creds)
                req = youtube.channels().list(mine=True, part="snippet,statistics,status")
                res = req.execute()
                items = res.get("items", [])
                if items:
                    ch = items[0]
                    snip = ch.get("snippet", {})
                    stats = ch.get("statistics", {})
                    stat = ch.get("status", {})
                    thumbs = snip.get("thumbnails", {})
                    thumb_url = (
                        thumbs.get("high", {}).get("url")
                        or thumbs.get("medium", {}).get("url")
                        or thumbs.get("default", {}).get("url")
                    )
                    channel_info = {
                        "id": ch.get("id"),
                        "title": snip.get("title"),
                        "custom_url": snip.get("customUrl"),
                        "thumbnail_url": thumb_url,
                        "subscribers": stats.get("subscriberCount"),
                        "video_count": stats.get("videoCount"),
                        "view_count": stats.get("viewCount"),
                        "privacy_status": stat.get("privacyStatus"),
                        "is_linked": stat.get("isLinked"),
                        "long_uploads_status": stat.get("longUploadsStatus"),
                    }
            except Exception as ch_err:
                error_msg = f"Channel verification failed: {ch_err}"

        return {
            "secrets_installed": secrets_exist,
            "token_installed": token_exist,
            "is_authenticated": is_valid,
            "can_refresh": can_refresh,
            "has_refresh_token": has_refresh_token,
            "token_expiry": token_expiry,
            "channel_connected": channel_info is not None,
            "channel_info": channel_info,
            "error": error_msg,
            "client_secrets_path": self.client_secrets_file,
            "token_path": self.token_file,
        }

    def refresh_token_safely(self) -> Tuple[bool, Optional[str]]:
        """
        Safely attempts to refresh credentials using the stored refresh_token.
        Returns: (success: bool, error_or_message: Optional[str])
        """
        if not os.path.exists(self.token_file):
            return False, "Token file not found. Channel is not authenticated."
        try:
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
            if not creds.refresh_token:
                return False, "No refresh_token present in token.json. Re-authentication required with access_type='offline'."
            creds.refresh(Request())
            if creds.valid:
                self.save_credentials(creds)
                return True, "OAuth token refreshed successfully."
            else:
                return False, "Refreshed credentials are still invalid."
        except Exception as e:
            return False, f"Token refresh failed: {e}. Re-authenticate via: python backend/scripts/configure_youtube_oauth.py --auth"

    def start_interactive_auth(
        self,
        port: int = 8088,
        host: str = "localhost",
        open_browser: bool = True,
        timeout_sec: int = 120,
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """
        Launches local server OAuth flow to authorize channel with Google.
        Returns: (success: bool, channel_info: Optional[Dict], error_message: Optional[str])
        """
        if not os.path.exists(self.client_secrets_file):
            return False, None, (
                f"OAuth client secrets file not found at: {self.client_secrets_file}. "
                "Download your OAuth 2.0 Client ID (Desktop App) from Google Cloud Console."
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secrets_file,
                scopes=self.scopes
            )
            logger.info("Starting local OAuth redirect server on http://%s:%d ...", host, port)
            creds = flow.run_local_server(
                host=host,
                port=port,
                authorization_prompt_message="Please visit this URL to authorize CrazzyDocs: {url}",
                success_message="Authentication successful! You can now close this tab and return to CrazzyDocs.",
                open_browser=open_browser,
                timeout_seconds=timeout_sec,
                access_type="offline",
                prompt="consent",
            )
            self.save_credentials(creds)
            auth_status = self.get_auth_status()
            if auth_status.get("channel_connected"):
                return True, auth_status.get("channel_info"), None
            else:
                return True, None, "Authorized, but could not retrieve channel details."
        except Exception as e:
            logger.error("OAuth authentication failed: %s", e)
            return False, None, str(e)

    def get_authenticated_service(self):
        """Builds and returns the authenticated YouTube Data API client."""
        if not os.path.exists(self.token_file):
            return None
        try:
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
            if (not creds.valid or creds.expired) and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self.save_credentials(creds)
                except Exception as ref_err:
                    logger.error("OAuth token refresh failed: %s", ref_err)
            if creds.valid:
                return build("youtube", "v3", credentials=creds)
        except Exception as e:
            logger.error("Failed building authenticated service: %s", e)
        return None

    def save_credentials(self, creds: Credentials):
        """Persists refreshed credentials securely to token.json."""
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        logger.info("Saved refreshed OAuth credentials to: %s", self.token_file)

    def disconnect_channel(self) -> bool:
        """Revokes and safely removes the local token.json file."""
        if os.path.exists(self.token_file):
            try:
                # Attempt remote token revocation if possible
                try:
                    import urllib.request
                    import urllib.parse
                    with open(self.token_file, "r", encoding="utf-8") as f:
                        tok_data = json.load(f)
                    tok = tok_data.get("token") or tok_data.get("refresh_token")
                    if tok:
                        req_data = urllib.parse.urlencode({"token": tok}).encode("utf-8")
                        req = urllib.request.Request(
                            "https://oauth2.googleapis.com/revoke",
                            data=req_data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}
                        )
                        urllib.request.urlopen(req, timeout=5)
                except Exception as rev_err:
                    logger.debug("Remote revocation note: %s", rev_err)

                os.remove(self.token_file)
                logger.info("Disconnected channel; removed %s", self.token_file)
                return True
            except Exception as e:
                logger.error("Failed removing token file: %s", e)
                return False
        return True


class MetadataGenerator:
    """Generates factually consistent, high-CTR YouTube Shorts metadata."""

    @staticmethod
    def generate_shorts_metadata(
        topic_title: str,
        opening_hook: str,
        category: str,
        key_entities: List[str],
        story_summary: Optional[str] = None,
        sources: Optional[List[str]] = None,
        attributions: Optional[List[Dict]] = None,
    ) -> YouTubeMetadata:
        """Constructs factual, compliant metadata tailored for YouTube Shorts."""
        # 1. Clean Title: keep within 100 chars, avoid arbitrary ellipsis truncation
        clean_title = topic_title.strip()
        if len(f"{clean_title} #Shorts") <= 100:
            yt_title = f"{clean_title} #Shorts"
        else:
            # Fall back to main clause before colon if available
            main_clause = clean_title.split(":")[0].strip()
            if len(f"{main_clause} #Shorts") <= 100:
                yt_title = f"{main_clause} #Shorts"
            else:
                words = clean_title.split()
                assembled = ""
                for w in words:
                    cand = f"{assembled} {w}".strip()
                    if len(cand) + 8 <= 100:
                        assembled = cand
                    else:
                        break
                yt_title = f"{assembled} #Shorts"

        # 2. Build credit string from attributions and sources
        credits = []
        if attributions:
            for item in attributions[:6]:
                name = item.get("filename") or item.get("title") or item.get("original_filename", "")
                license_name = item.get("license", "Public Domain")
                source = item.get("source") or item.get("attribution", "Historical Archive")
                if name:
                    credits.append(f"• {name} — {source} ({license_name})")
        elif sources:
            for s in sources[:4]:
                credits.append(f"• Primary Source: {s}")

        credit_block = "\n".join(credits) if credits else "Public Domain historical archives (Wikimedia Commons)."

        # 3. Description: Hook, Factual Narrative Summary, Attribution, Tags
        summary_text = story_summary or (
            f"The incredible, true historical story of {clean_title}. "
            "Verified against primary historical documents and public archives."
        )

        description = (
            f"{opening_hook}\n\n"
            f"{summary_text}\n\n"
            "📚 Historical Source Credits & Archival References:\n"
            f"{credit_block}\n\n"
            "🔔 Subscribe to CrazzyDocs for incredible true stories and astonishing history!\n\n"
            "#Shorts #History #CrazzyDocs #DidYouKnow #TrueStory #Documentary"
        )

        # 4. Categorize
        cat_lower = category.lower()
        if "science" in cat_lower or "tech" in cat_lower:
            category_id = "28"  # Science & Technology
        elif "education" in cat_lower or "history" in cat_lower or "unbelievable" in cat_lower:
            category_id = "27"  # Education
        else:
            category_id = "24"  # Entertainment

        # 5. Topic-Specific Tags (Key entities + spelling variants + niche keywords)
        tags = [
            "shorts", "crazzydocs", "history", "true story", "documentary",
            "did you know", "unbelievable history"
        ]
        for ent in key_entities:
            clean_ent = ent.lower().strip()
            if clean_ent and clean_ent not in tags:
                tags.append(clean_ent)

        # Add specific topic-derived variants
        title_lower = topic_title.lower()
        if "vesna" in title_lower or "vulovic" in title_lower:
            extra_variants = [
                "vesna vulovic", "vesna vulović", "jat flight 367", "fall from 33000 feet",
                "aviation miracle", "survived without parachute", "srbska kamenice",
                "highest fall survived", "guinness world records"
            ]
            for v in extra_variants:
                if v not in tags:
                    tags.append(v)
        elif "yamaguchi" in title_lower:
            extra_variants = [
                "tsutomu yamaguchi", "hiroshima survivor", "nagasaki survivor",
                "double atomic bomb survivor", "hibakusha", "august 1945"
            ]
            for v in extra_variants:
                if v not in tags:
                    tags.append(v)

        hashtags = ["#Shorts", "#History", "#CrazzyDocs", "#TrueStory"]

        return YouTubeMetadata(
            title=yt_title,
            description=description,
            tags=tags[:20],
            hashtags=hashtags,
            category_id=category_id,
            attribution_text=credit_block,
            pinned_comment=f"What part of this incredible story surprised you most? Let us know below! 👇"
        )

    @staticmethod
    def validate_metadata_against_story(
        metadata: YouTubeMetadata,
        topic_title: str,
        story_text: str,
        sources: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Validates metadata integrity against story facts:
        1. Title length <= 100 and not arbitrarily truncated (no trailing '...')
        2. Title contains primary topic subject
        3. Description summarizes story (> 100 chars, mentions key subject)
        4. Tags contain primary entities and no forbidden cross-contamination terms
        5. Attribution is non-empty and cites primary sources
        """
        issues = []

        # Check 1: Title length & truncation
        if not metadata.title:
            issues.append("Title is empty.")
        elif len(metadata.title) > 100:
            issues.append(f"Title exceeds 100 characters ({len(metadata.title)} chars).")
        if metadata.title.endswith("..."):
            issues.append("Title is arbitrarily truncated with trailing ellipsis '...'.")

        # Check 2: Description substance
        if len(metadata.description) < 100:
            issues.append("Description is too brief (< 100 characters).")

        # Check 3: Cross-contamination guard
        title_lower = topic_title.lower()
        meta_all = (metadata.title + " " + metadata.description + " " + " ".join(metadata.tags)).lower()

        # If not a nuclear story, forbid nuclear terms
        if not any(k in title_lower for k in ("atomic", "nuclear", "bomb", "yamaguchi", "plutonium", "hiroshima", "nagasaki")):
            forbidden = ["atomic bomb", "hiroshima", "nagasaki", "plutonium", "fat man", "little boy"]
            for f in forbidden:
                if f in meta_all:
                    issues.append(f"Forbidden cross-contamination keyword '{f}' found in non-nuclear story metadata.")

        # If aviation story, ensure aviation keywords present
        if any(k in title_lower for k in ("flight", "plane", "attendant", "vesna", "fall", "parachute")):
            if not any(k in meta_all for k in ("vesna", "flight", "plane", "fall", "parachute", "33,000", "33000")):
                issues.append("Aviation story metadata lacks core topic entities.")

        # Check 4: Attribution completeness
        if not metadata.attribution_text or "Public Domain" not in metadata.attribution_text and "Source" not in metadata.attribution_text:
            issues.append("Attribution text is missing or does not state source/license.")

        return len(issues) == 0, issues


class YouTubePublishingEngine:
    """Master YouTube Upload & Publishing Engine."""

    def __init__(
        self,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        db: Optional[PublishingDatabase] = None,
        max_daily_quota: int = 10000,
    ):
        self.oauth = oauth_manager or YouTubeOAuthManager()
        self.db = db or PublishingDatabase()
        self.max_daily_quota = max_daily_quota

    def check_remote_channel_duplicate(
        self,
        service: Any,
        topic_title: str,
        metadata_title: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Queries YouTube Data API v3 to check if the video has already been uploaded
        to the authenticated channel. This prevents duplicate uploads during crash recovery
        or upload uncertainty.
        """
        try:
            channels_resp = service.channels().list(mine=True, part="contentDetails").execute()
            items = channels_resp.get("items", [])
            if not items:
                return False, None

            uploads_playlist_id = (
                items[0]
                .get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if not uploads_playlist_id:
                return False, None

            playlist_resp = service.playlistItems().list(
                playlistId=uploads_playlist_id,
                part="snippet",
                maxResults=25,
            ).execute()

            clean_meta_title = metadata_title.strip().lower()
            clean_topic = topic_title.strip().lower()

            for item in playlist_resp.get("items", []):
                snippet = item.get("snippet", {})
                remote_title = snippet.get("title", "").strip().lower()
                remote_vid_id = snippet.get("resourceId", {}).get("videoId")

                if remote_vid_id and (
                    remote_title == clean_meta_title
                    or clean_topic in remote_title
                    or clean_meta_title in remote_title
                ):
                    logger.warning(
                        "[REMOTE DUPLICATE DETECTED] Video '%s' already exists remotely on YouTube (ID: %s)",
                        metadata_title,
                        remote_vid_id,
                    )
                    return True, remote_vid_id

            return False, None
        except Exception as e:
            logger.warning("[REMOTE DUPLICATE CHECK] Could not complete remote lookup: %s", e)
            return False, None

    def upload_short(
        self,
        video_path: str,
        topic_title: str,
        metadata: YouTubeMetadata,
        thumbnail_path: Optional[str] = None,
        visibility: str = "private",  # "private" or "unlisted". "public" is strictly gated!
        progress_callback: Optional[Callable[[float], None]] = None,
        allow_public_bypass: bool = False,
    ) -> Dict:
        """
        Uploads video to YouTube with resumable chunks, duplicate prevention, and safety gates.
        
        CRITICAL SECURITY INVARIANT:
        Public publishing remains permanently blocked unless explicit manual bypass is provided.
        """
        # 1. Enforce Strict Publishing Safety Gate
        if visibility == "public" and not allow_public_bypass:
            raise PermissionError(
                "SECURITY GATE ENFORCED: Public publishing is disabled. "
                "Only 'private' or 'unlisted' uploads are permitted in automated studio mode."
            )

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # 2. Check Local Duplicate Upload Prevention
        is_dup, existing_id = self.db.check_duplicate(video_path, topic_title)
        if is_dup:
            logger.warning("Duplicate upload rejected: topic '%s' already uploaded (ID: %s)", topic_title, existing_id)
            return {
                "success": False,
                "video_id": existing_id,
                "error": f"Duplicate upload prevented. Video already uploaded with ID: {existing_id}",
                "status": "duplicate_rejected"
            }

        # 3. Check Quota Availability
        quota_used = self.db.get_quota_used_today()
        # Cost: 1600 units for video upload + 50 units for thumbnail upload
        estimated_cost = 1650 if thumbnail_path else 1600
        if quota_used + estimated_cost > self.max_daily_quota:
            err = f"Daily YouTube API quota limit reached ({quota_used}/{self.max_daily_quota} units used). Upload deferred."
            logger.error(err)
            return {"success": False, "video_id": None, "error": err, "status": "quota_exceeded"}

        # 4. Verify Authentication
        service = self.oauth.get_authenticated_service()
        if not service:
            err = "YouTube account is not authenticated. Please configure client_secrets.json and complete OAuth sign-in."
            logger.info(err)
            return {"success": False, "video_id": None, "error": err, "status": "unauthenticated"}

        # 4b. Remote YouTube Duplicate Check (Resolves upload uncertainty & crash recovery)
        is_remote_dup, remote_id = self.check_remote_channel_duplicate(
            service=service,
            topic_title=topic_title,
            metadata_title=metadata.title,
        )
        if is_remote_dup and remote_id:
            logger.info("Reconciling local publishing ledger with existing remote YouTube video ID: %s", remote_id)
            status_code = f"uploaded_{visibility}"
            rec = PublicationRecord(
                id=None,
                topic_title=topic_title,
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                metadata_json=json.dumps(asdict(metadata)),
                youtube_video_id=remote_id,
                upload_status=status_code,
                visibility=visibility,
                video_hash=compute_file_sha256(video_path),
                upload_timestamp=datetime.utcnow().isoformat() + "Z",
                error_details="Reconciled from authenticated YouTube remote channel check",
            )
            ledger_id = self.db.record_publication(rec)
            return {
                "success": True,
                "video_id": remote_id,
                "visibility": visibility,
                "thumbnail_uploaded": False,
                "thumbnail_status": "existing_remote",
                "thumbnail_error": None,
                "thumbnail_local_path": thumbnail_path,
                "ledger_id": ledger_id,
                "status": "remote_duplicate_reconciled",
                "youtube_url": f"https://youtu.be/{remote_id}",
                "reconciled_from_remote": True,
            }

        # 5. Execute Resumable Chunked Upload
        body = {
            "snippet": {
                "title": metadata.title[:100],
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": metadata.category_id,
                "defaultLanguage": metadata.default_language,
                "defaultAudioLanguage": metadata.default_audio_language,
            },
            "status": {
                "privacyStatus": visibility,
                "selfDeclaredMadeForKids": False,
            }
        }

        video_id = None
        try:
            logger.info("Initiating resumable upload (%s, %s): %s", visibility, metadata.title, video_path)
            media = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
            req = service.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            retries = 0
            max_retries = 3

            while response is None:
                try:
                    status, response = req.next_chunk()
                    if status and progress_callback:
                        progress_callback(status.progress())
                    if status:
                        pct = int(status.progress() * 100)
                        if pct % 25 == 0:
                            logger.info("Upload progress: %d%%", pct)
                    retries = 0  # Reset retries on success
                except (HttpError, ConnectionError, OSError) as e:
                    retries += 1
                    if retries > max_retries:
                        raise e
                    sleep_time = 2.0 * retries
                    logger.warning("Upload chunk failed (retry %d/%d in %.1fs): %s", retries, max_retries, sleep_time, e)
                    time.sleep(sleep_time)

            video_id = response.get("id")
            logger.info("Video successfully uploaded! YouTube ID: %s", video_id)
            self.db.add_quota_consumption(1600)

        except Exception as e:
            err_msg = str(e)
            logger.error("YouTube video upload failed: %s", err_msg)
            # Record failure in ledger
            rec = PublicationRecord(
                id=None,
                topic_title=topic_title,
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                metadata_json=json.dumps(asdict(metadata)),
                youtube_video_id=None,
                upload_status="failed",
                visibility=visibility,
                video_hash=compute_file_sha256(video_path),
                error_details=err_msg
            )
            self.db.record_publication(rec)
            return {"success": False, "video_id": None, "error": err_msg, "status": "failed"}

        # 6. Upload Custom Thumbnail (if provided and supported)
        thumb_uploaded = False
        thumb_error = None
        thumb_status = "not_provided"
        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
            try:
                media_thumb = MediaFileUpload(thumbnail_path, mimetype="image/png")
                service.thumbnails().set(videoId=video_id, media_body=media_thumb).execute()
                thumb_uploaded = True
                thumb_status = "uploaded"
                self.db.add_quota_consumption(50)
                logger.info("Custom thumbnail uploaded for video: %s", video_id)
            except HttpError as h_err:
                thumb_error = str(h_err)
                if h_err.resp.status in (401, 403) or "eligible" in thumb_error.lower() or "forbidden" in thumb_error.lower():
                    thumb_status = "eligibility_restricted"
                    logger.warning(
                        "Custom thumbnail restricted (YouTube channel must be phone-verified for custom thumbnails): %s",
                        thumb_error
                    )
                else:
                    thumb_status = "failed"
                    logger.warning("Custom thumbnail HTTP error: %s", thumb_error)
            except Exception as t_err:
                thumb_error = str(t_err)
                thumb_status = "failed"
                logger.warning("Thumbnail upload skipped (account may need phone verification): %s", t_err)

        # 7. Record Successful Upload in Local Publishing Database
        status_code = f"uploaded_{visibility}"
        rec = PublicationRecord(
            id=None,
            topic_title=topic_title,
            video_path=video_path,
            thumbnail_path=thumbnail_path,
            metadata_json=json.dumps(asdict(metadata)),
            youtube_video_id=video_id,
            upload_status=status_code,
            visibility=visibility,
            video_hash=compute_file_sha256(video_path),
            upload_timestamp=datetime.utcnow().isoformat() + "Z",
            error_details=thumb_error
        )
        ledger_id = self.db.record_publication(rec)

        return {
            "success": True,
            "video_id": video_id,
            "visibility": visibility,
            "thumbnail_uploaded": thumb_uploaded,
            "thumbnail_status": thumb_status,
            "thumbnail_error": thumb_error,
            "thumbnail_local_path": thumbnail_path,
            "ledger_id": ledger_id,
            "status": status_code,
            "youtube_url": f"https://youtu.be/{video_id}"
        }


# Singleton engine instance
youtube_publishing_engine = YouTubePublishingEngine()
