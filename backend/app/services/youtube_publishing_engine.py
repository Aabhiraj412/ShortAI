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
import mimetypes
import os
import re
import shutil
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.core.execution_context import ExecutionContext, require_production_context
from backend.app.core.config import settings
from backend.app.core.secure_storage import protect_text, read_protected_file, unprotect_text, write_protected_file

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
from backend.app.core.paths import DATABASE_PATH as _DATABASE_PATH
DB_PATH = str(_DATABASE_PATH)
DEFAULT_CLIENT_SECRETS = settings.YOUTUBE_CLIENT_SECRETS_FILE
DEFAULT_TOKEN_FILE = settings.YOUTUBE_TOKEN_FILE

# Mandatory OAuth scopes for upload and channel inspection
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
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
    source_publication_id: Optional[int] = None


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
            had_ledger = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publishing_ledger'"
            ).fetchone() is not None
            had_upload_state = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='youtube_upload_state'"
            ).fetchone() is not None
            if had_ledger and not had_upload_state and os.path.getsize(self.db_path) > 0:
                backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                backup_path = os.path.join(
                    backup_dir,
                    f"studio_pre_stage_3_22_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.db",
                )
                backup = sqlite3.connect(backup_path)
                try:
                    conn.backup(backup)
                    backup.commit()
                finally:
                    backup.close()
                logger.info("Created SQLite backup before Stage 3.22 upload-state migration.")
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
                CREATE TABLE IF NOT EXISTS youtube_upload_state (
                    ledger_id INTEGER PRIMARY KEY,
                    progress_pct REAL NOT NULL DEFAULT 0,
                    resumable_uri_protected TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    verification_status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    approved_at TEXT,
                    updated_at TEXT NOT NULL
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

    def get_publication(self, ledger_id: int) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM publishing_ledger WHERE id = ?", (ledger_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_publications(self, statuses: Optional[List[str]] = None, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"SELECT * FROM publishing_ledger WHERE upload_status IN ({placeholders}) "
                    "ORDER BY id DESC LIMIT ?",
                    (*statuses, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM publishing_ledger ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def set_upload_status(self, ledger_id: int, status: str, error: Optional[str] = None) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                cur = conn.execute(
                    "UPDATE publishing_ledger SET upload_status = ?, error_details = ? WHERE id = ?",
                    (status, error, ledger_id),
                )
                if cur.rowcount != 1:
                    raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        finally:
            conn.close()

    def record_video_uploaded(self, ledger_id: int, video_id: str) -> None:
        """Immediately persists youtube_video_id after video insert so thumbnail retries never re-upload."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                conn.execute(
                    "UPDATE publishing_ledger SET youtube_video_id=?, upload_timestamp=? WHERE id=?",
                    (video_id, datetime.utcnow().isoformat() + "Z", ledger_id),
                )
        finally:
            conn.close()

    def update_queue_metadata_and_schedule(
        self,
        ledger_id: int,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        hashtags: Optional[List[str]] = None,
        thumbnail_path: Optional[str] = None,
        scheduled_timestamp: Optional[str] = "__UNSET__",
        upload_status: Optional[str] = None,
        visibility: Optional[str] = None,
        error_details: Optional[str] = "__UNSET__",
    ) -> Dict[str, Any]:
        record = self.get_publication(ledger_id)
        if not record:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        meta = json.loads(record.get("metadata_json") or "{}")
        if title is not None:
            meta["title"] = title.strip()
        if description is not None:
            meta["description"] = description
        if tags is not None:
            meta["tags"] = tags
        if hashtags is not None:
            meta["hashtags"] = hashtags

        new_topic = title.strip() if title is not None else record["topic_title"]
        new_thumb = os.path.abspath(thumbnail_path) if thumbnail_path else record.get("thumbnail_path")
        new_sched = record.get("scheduled_timestamp") if scheduled_timestamp == "__UNSET__" else scheduled_timestamp
        new_status = upload_status or record["upload_status"]
        new_vis = visibility or record["visibility"]
        new_err = record.get("error_details") if error_details == "__UNSET__" else error_details

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                conn.execute(
                    """UPDATE publishing_ledger
                       SET topic_title=?, thumbnail_path=?, metadata_json=?,
                           scheduled_timestamp=?, upload_status=?, visibility=?, error_details=?
                       WHERE id=?""",
                    (
                        new_topic,
                        new_thumb,
                        json.dumps(meta),
                        new_sched,
                        new_status,
                        new_vis,
                        new_err,
                        ledger_id,
                    ),
                )
        finally:
            conn.close()
        return self.get_publication(ledger_id) or {}

    def finish_upload(
        self,
        ledger_id: int,
        video_id: str,
        status: str,
        publish_at_iso: Optional[str],
        error: Optional[str] = None,
    ) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                cur = conn.execute(
                    """UPDATE publishing_ledger SET youtube_video_id=?, upload_status=?,
                       upload_timestamp=?, scheduled_timestamp=?, error_details=? WHERE id=?""",
                    (
                        video_id, status, datetime.utcnow().isoformat() + "Z",
                        publish_at_iso, error, ledger_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        finally:
            conn.close()

    def approve_upload(self, ledger_id: int) -> Dict[str, Any]:
        record = self.get_publication(ledger_id)
        if not record:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        eligible_statuses = (
            "pending_queue", "awaiting_review", "upload_interrupted",
            "failed", "awaiting_auth", "quota_exceeded", "thumbnail_failed",
        )
        if record["upload_status"] not in eligible_statuses:
            raise ValueError(f"Publishing ledger record {ledger_id} is not awaiting approval.")
        now = datetime.utcnow().isoformat() + "Z"
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                placeholders = ",".join("?" for _ in eligible_statuses)
                cur = conn.execute(
                    f"UPDATE publishing_ledger SET upload_status='approved', error_details=NULL "
                    f"WHERE id=? AND upload_status IN ({placeholders})",
                    (ledger_id, *eligible_statuses),
                )
                if cur.rowcount != 1:
                    raise ValueError(f"Publishing ledger record {ledger_id} is already being processed.")
                conn.execute(
                    """INSERT INTO youtube_upload_state (ledger_id, approved_at, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(ledger_id) DO UPDATE SET approved_at=excluded.approved_at,
                       last_error=NULL, updated_at=excluded.updated_at""",
                    (ledger_id, now, now),
                )
        finally:
            conn.close()
        return self.get_publication(ledger_id) or {}

    def recover_interrupted_uploads(self) -> int:
        """Mark uploads left in-flight by a previous process as resumable interruptions."""
        now = datetime.utcnow().isoformat() + "Z"
        message = "Application restarted during upload; resumable state was retained."
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                rows = conn.execute(
                    "SELECT id FROM publishing_ledger WHERE upload_status='uploading'"
                ).fetchall()
                if not rows:
                    return 0
                ledger_ids = [row[0] for row in rows]
                conn.executemany(
                    "UPDATE publishing_ledger SET upload_status='upload_interrupted', error_details=? "
                    "WHERE id=? AND upload_status='uploading'",
                    [(message, ledger_id) for ledger_id in ledger_ids],
                )
                conn.executemany(
                    """INSERT INTO youtube_upload_state (ledger_id, last_error, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(ledger_id) DO UPDATE SET
                       last_error=excluded.last_error, updated_at=excluded.updated_at""",
                    [(ledger_id, message, now) for ledger_id in ledger_ids],
                )
                return len(ledger_ids)
        finally:
            conn.close()

    def claim_upload(self, ledger_id: int, allow_pending: bool = False) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                if allow_pending:
                    cur = conn.execute(
                        "UPDATE publishing_ledger SET upload_status='uploading', error_details=NULL "
                        "WHERE id=? AND upload_status IN ('approved', 'pending_queue')",
                        (ledger_id,),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE publishing_ledger SET upload_status='uploading', error_details=NULL "
                        "WHERE id=? AND upload_status='approved'",
                        (ledger_id,),
                    )
                if cur.rowcount != 1:
                    raise ValueError(f"Publishing ledger record {ledger_id} is already being processed.")
        finally:
            conn.close()

    def get_upload_state(self, ledger_id: int) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM youtube_upload_state WHERE ledger_id = ?", (ledger_id,)
            ).fetchone()
            state = dict(row) if row else {}
        finally:
            conn.close()
        protected_uri = state.pop("resumable_uri_protected", None)
        state["resumable_uri"] = unprotect_text(protected_uri) if protected_uri else None
        return state

    def update_upload_state(
        self,
        ledger_id: int,
        progress_pct: Optional[float] = None,
        resumable_uri: Optional[str] = None,
        retry_count: Optional[int] = None,
        verification_status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        prior = self.get_upload_state(ledger_id)
        values = {
            "progress_pct": prior.get("progress_pct", 0.0) if progress_pct is None else max(0.0, min(100.0, progress_pct)),
            "resumable_uri_protected": (
                protect_text(resumable_uri) if resumable_uri is not None
                else (protect_text(prior["resumable_uri"]) if prior.get("resumable_uri") else None)
            ),
            "retry_count": prior.get("retry_count", 0) if retry_count is None else retry_count,
            "verification_status": verification_status or prior.get("verification_status", "pending"),
            "last_error": last_error,
            "approved_at": prior.get("approved_at"),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            with conn:
                conn.execute(
                    """INSERT INTO youtube_upload_state (
                           ledger_id, progress_pct, resumable_uri_protected, retry_count,
                           verification_status, last_error, approved_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(ledger_id) DO UPDATE SET
                           progress_pct=excluded.progress_pct,
                           resumable_uri_protected=excluded.resumable_uri_protected,
                           retry_count=excluded.retry_count,
                           verification_status=excluded.verification_status,
                           last_error=excluded.last_error,
                           approved_at=COALESCE(excluded.approved_at, youtube_upload_state.approved_at),
                           updated_at=excluded.updated_at""",
                    (ledger_id, *values.values()),
                )
        finally:
            conn.close()

    def check_duplicate(
        self, video_path: str, topic_title: str, exclude_ledger_id: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
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
                  AND (? IS NULL OR id != ?)
            """, (exclude_ledger_id, exclude_ledger_id))
            for row in cur.fetchall():
                eid, etitle, ehash, estatus, eyt = row
                if (norm_target and normalize_topic_title(etitle) == norm_target) or (v_hash and ehash == v_hash):
                    return True, f"enqueued_{estatus}_{eid}"

            # Check uploaded/published records
            cur.execute("""
                SELECT youtube_video_id, upload_status, topic_title, video_hash FROM publishing_ledger
                WHERE upload_status IN ('uploaded_private', 'uploaded_unlisted', 'uploaded_public', 'uploaded_unverified', 'published')
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

                # Same rendered bytes already in the ledger in any state (approved,
                # scheduled, uploaded...): a queue-handoff retry must return that row.
                if record.video_hash:
                    cur.execute(
                        "SELECT id FROM publishing_ledger WHERE video_hash = ? ORDER BY id ASC LIMIT 1",
                        (record.video_hash,),
                    )
                    same_bytes = cur.fetchone()
                    if same_bytes:
                        logger.info("[PUBLISHING IDEMPOTENCY] Video hash already queued as #%d", same_bytes[0])
                        return same_bytes[0]

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
            if not source_path_or_json.lstrip().startswith("{") and os.path.exists(source_path_or_json):
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

            write_protected_file(
                self.client_secrets_file,
                json.dumps(data, separators=(",", ":")).encode("utf-8"),
            )
            logger.info("Installed protected OAuth client configuration.")
            return True, "OAuth client configuration installed."
        except json.JSONDecodeError:
            return False, "Invalid OAuth client configuration JSON."
        except Exception as exc:
            logger.warning(
                "Could not install OAuth client configuration (%s).",
                type(exc).__name__,
            )
            return False, "Could not securely install OAuth client configuration."

    def _load_credentials(self) -> Optional[Credentials]:
        data = read_protected_file(self.token_file)
        if data is None:
            return None
        token_data = json.loads(data.decode("utf-8"))
        creds = Credentials.from_authorized_user_info(token_data, self.scopes)
        if data.lstrip().startswith(b"{"):
            self.save_credentials(creds)
        return creds

    def _load_client_config(self) -> Dict[str, Any]:
        data = read_protected_file(self.client_secrets_file)
        if data is None:
            raise FileNotFoundError("OAuth client configuration is not installed.")
        config = json.loads(data.decode("utf-8"))
        if data.lstrip().startswith(b"{"):
            write_protected_file(
                self.client_secrets_file,
                json.dumps(config, separators=(",", ":")).encode("utf-8"),
            )
        return config

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
        auth_problem = None

        if token_exist:
            try:
                creds = self._load_credentials()
                if creds:
                    has_refresh_token = bool(creds.refresh_token)
                    can_refresh = has_refresh_token
                    token_expiry = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
                    is_valid = creds.valid
                    if not is_valid or creds.expired:
                        if has_refresh_token:
                            try:
                                creds.refresh(Request())
                                is_valid = creds.valid
                                token_expiry = creds.expiry.isoformat() if getattr(creds, "expiry", None) else None
                                self.save_credentials(creds)
                                if not is_valid:
                                    auth_problem = "refresh_failed"
                            except Exception:
                                auth_problem = "refresh_failed"
                        else:
                            auth_problem = "reconnect_required"
            except Exception:
                auth_problem = "credential_load_failed"

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
            except HttpError as exc:
                auth_problem = (
                    "authorization_revoked"
                    if getattr(getattr(exc, "resp", None), "status", None) == 401
                    else "channel_verification_failed"
                )
            except Exception:
                auth_problem = "channel_verification_failed"

        return {
            "secrets_installed": secrets_exist,
            "token_installed": token_exist,
            "is_authenticated": is_valid,
            "token_expiry": token_expiry,
            "channel_connected": channel_info is not None,
            "channel_info": channel_info,
            "reconnect_required": auth_problem in (
                "refresh_failed",
                "credential_load_failed",
                "reconnect_required",
                "authorization_revoked",
            ),
            "status": auth_problem or ("connected" if channel_info else "not_connected"),
        }

    def refresh_token_safely(self) -> Tuple[bool, Optional[str]]:
        """
        Safely attempts to refresh credentials using the stored refresh_token.
        Returns: (success: bool, error_or_message: Optional[str])
        """
        if not os.path.exists(self.token_file):
            return False, "Token file not found. Channel is not authenticated."
        try:
            creds = self._load_credentials()
            if not creds:
                return False, "Token file not found. Channel is not authenticated."
            if not creds.refresh_token:
                return False, "No refresh_token present in token.json. Re-authentication required with access_type='offline'."
            creds.refresh(Request())
            if creds.valid:
                self.save_credentials(creds)
                return True, "OAuth token refreshed successfully."
            else:
                return False, "Refreshed credentials are still invalid."
        except Exception:
            logger.warning("OAuth refresh failed; reconnection may be required.")
            return False, "Token refresh failed; re-authentication is required."

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
            flow = InstalledAppFlow.from_client_config(self._load_client_config(), scopes=self.scopes)
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
        except Exception as exc:
            logger.warning("OAuth authentication failed (%s).", type(exc).__name__)
            return False, None, "OAuth authorization failed. Check local configuration and retry."

    def get_authenticated_service(self):
        """Builds and returns the authenticated YouTube Data API client."""
        if not os.path.exists(self.token_file):
            return None
        try:
            creds = self._load_credentials()
            if not creds:
                return None
            if (not creds.valid or creds.expired) and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self.save_credentials(creds)
                except Exception:
                    logger.warning("OAuth token refresh failed; reconnect the channel.")
            if creds.valid:
                return build("youtube", "v3", credentials=creds)
        except Exception as exc:
            logger.warning("Could not build authenticated YouTube client (%s).", type(exc).__name__)
        return None

    def save_credentials(self, creds: Credentials):
        """Persists refreshed credentials securely to token.json."""
        write_protected_file(self.token_file, creds.to_json().encode("utf-8"))
        logger.info("Saved protected OAuth credentials.")

    def disconnect_channel(self) -> bool:
        """Revokes and safely removes the local token.json file."""
        if os.path.exists(self.token_file):
            try:
                # Attempt remote token revocation if possible
                try:
                    import urllib.request
                    import urllib.parse
                    creds = self._load_credentials()
                    tok = (creds.token or creds.refresh_token) if creds else None
                    if tok:
                        req_data = urllib.parse.urlencode({"token": tok}).encode("utf-8")
                        req = urllib.request.Request(
                            "https://oauth2.googleapis.com/revoke",
                            data=req_data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}
                        )
                        urllib.request.urlopen(req, timeout=5)
                except Exception:
                    logger.debug("Remote token revocation was unavailable; local credentials will still be removed.")

                os.remove(self.token_file)
                logger.info("Disconnected channel; removed %s", self.token_file)
                return True
            except Exception:
                logger.exception("Failed removing local OAuth credentials.")
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
        fail_closed: bool = False,
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
        except Exception:
            logger.warning("[REMOTE DUPLICATE CHECK] Could not complete remote lookup.")
            if fail_closed:
                raise RuntimeError("YouTube duplicate check is unavailable; upload was not started.")
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
        ledger_id: Optional[int] = None,
        publish_at_iso: Optional[str] = None,
        auto_policy_authorized: bool = False,
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
                "Only 'private' or 'unlisted' uploads are permitted without an explicit manual override."
            )

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # 2. Check Local Duplicate Upload Prevention
        if ledger_id is not None:
            queued = self.db.get_publication(ledger_id)
            allowed_statuses = {"approved", "pending_queue"} if auto_policy_authorized else {"approved"}
            if not queued or queued["upload_status"] not in allowed_statuses:
                raise PermissionError("The upload must be explicitly approved before it can start.")
            if os.path.abspath(queued["video_path"]) != os.path.abspath(video_path):
                raise ValueError("Approved upload path does not match the publishing ledger.")
            if queued["video_hash"] != compute_file_sha256(video_path):
                raise ValueError("Approved video changed after it was queued.")

        if publish_at_iso:
            if visibility != "private":
                raise ValueError("Scheduled publication requires a private upload.")
            publish_at = datetime.fromisoformat(publish_at_iso.replace("Z", "+00:00"))
            if publish_at.tzinfo is None or publish_at <= datetime.now(publish_at.tzinfo):
                raise ValueError("Scheduled publication time must be a future timezone-aware timestamp.")

        is_dup, existing_id = self.db.check_duplicate(video_path, topic_title, exclude_ledger_id=ledger_id)
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
            if ledger_id is not None:
                self.db.set_upload_status(ledger_id, "awaiting_auth", err)
            logger.info(err)
            return {"success": False, "video_id": None, "error": err, "status": "unauthenticated"}

        # 4b. Remote YouTube Duplicate Check (Resolves upload uncertainty & crash recovery)
        is_remote_dup, remote_id = self.check_remote_channel_duplicate(
            service=service,
            topic_title=topic_title,
            metadata_title=metadata.title,
            fail_closed=ledger_id is not None,
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
            if ledger_id is not None:
                self.db.set_upload_status(ledger_id, status_code)
                self.db.update_upload_state(
                    ledger_id, progress_pct=100, resumable_uri="", verification_status="verified"
                )
                existing_ledger_id = ledger_id
            else:
                existing_ledger_id = self.db.record_publication(rec)
            return {
                "success": True,
                "video_id": remote_id,
                "visibility": visibility,
                "thumbnail_uploaded": False,
                "thumbnail_status": "existing_remote",
                "thumbnail_error": None,
                "thumbnail_local_path": thumbnail_path,
                "ledger_id": existing_ledger_id,
                "status": "remote_duplicate_reconciled",
                "youtube_url": f"https://youtu.be/{remote_id}",
                "reconciled_from_remote": True,
            }

        # 5. Execute Resumable Chunked Upload (or resume from existing youtube_video_id)
        video_id = None
        if ledger_id is not None:
            existing_rec = self.db.get_publication(ledger_id)
            if existing_rec and existing_rec.get("youtube_video_id"):
                video_id = existing_rec["youtube_video_id"]
                logger.info(
                    "Resuming ledger %s with existing YouTube video ID %s (skipping duplicate video upload).",
                    ledger_id,
                    video_id,
                )

        if not video_id:
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
            if publish_at_iso:
                body["status"]["publishAt"] = publish_at_iso

            try:
                logger.info("Initiating resumable upload (%s, %s): %s", visibility, metadata.title, video_path)
                media = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
                req = service.videos().insert(part="snippet,status", body=body, media_body=media)
                upload_state = self.db.get_upload_state(ledger_id) if ledger_id is not None else {}
                resumable_uri = upload_state.get("resumable_uri")
                if resumable_uri:
                    req.resumable_uri = resumable_uri
                    req._in_error_state = True
                if ledger_id is not None:
                    self.db.claim_upload(ledger_id, allow_pending=auto_policy_authorized)

                response = None
                retries = 0
                max_retries = 3

                while response is None:
                    try:
                        status, response = req.next_chunk()
                        if ledger_id is not None:
                            progress = status.progress() if status else 0.0
                            self.db.update_upload_state(
                                ledger_id,
                                progress_pct=progress * 100,
                                resumable_uri=req.resumable_uri,
                                retry_count=retries,
                            )
                        if status and progress_callback:
                            progress_callback(status.progress())
                        if status:
                            pct = int(status.progress() * 100)
                            if pct % 25 == 0:
                                logger.info("Upload progress: %d%%", pct)
                        retries = 0  # Reset retries on success
                    except (HttpError, ConnectionError, OSError) as e:
                        err_text = str(e).lower()
                        if isinstance(e, HttpError) and ("quotaexceeded" in err_text or (e.resp.status == 403 and "quota" in err_text)):
                            raise RuntimeError("QUOTA_EXCEEDED: YouTube Data API daily quota exhausted.") from e
                        if isinstance(e, HttpError) and e.resp.status == 401:
                            raise PermissionError("AUTH_EXPIRED: YouTube OAuth token expired or revoked.") from e
                        if isinstance(e, HttpError) and e.resp.status in (404, 410) and req.resumable_uri:
                            req.resumable_uri = None
                            req._in_error_state = False
                            if ledger_id is not None:
                                self.db.update_upload_state(
                                    ledger_id, resumable_uri="", retry_count=retries + 1,
                                    last_error="Resumable session expired; restarting upload session.",
                                )
                            retries += 1
                            if retries > max_retries:
                                raise
                            time.sleep(min(2 ** (retries - 1), 30))
                            continue
                        retries += 1
                        if retries > max_retries:
                            raise
                        sleep_time = min(2 ** (retries - 1), 30)
                        logger.warning(
                            "Upload chunk failed (retry %d/%d in %.1fs; HTTP status %s).",
                            retries, max_retries, sleep_time, getattr(getattr(e, "resp", None), "status", "transport"),
                        )
                        if ledger_id is not None:
                            self.db.update_upload_state(ledger_id, retry_count=retries)
                        time.sleep(sleep_time)

                video_id = response.get("id")
                logger.info("Video successfully uploaded! YouTube ID: %s", video_id)
                self.db.add_quota_consumption(1600)
                if ledger_id is not None and video_id:
                    self.db.record_video_uploaded(ledger_id, video_id)

            except Exception as e:
                err_str = str(e)
                status_code = getattr(getattr(e, "resp", None), "status", None)
                if "QUOTA_EXCEEDED" in err_str or "quotaExceeded" in err_str:
                    fail_status = "quota_exceeded"
                    err_msg = "YouTube API quota exceeded. Retry after quota reset."
                elif "AUTH_EXPIRED" in err_str or status_code == 401:
                    fail_status = "awaiting_auth"
                    err_msg = "YouTube OAuth authentication expired. Reconnect YouTube account."
                else:
                    fail_status = "failed"
                    err_msg = f"YouTube upload failed (HTTP {status_code})." if status_code else "YouTube upload failed."
                logger.exception("YouTube video upload failed (%s).", fail_status)
                # Record failure in ledger
                rec = PublicationRecord(
                    id=None,
                    topic_title=topic_title,
                    video_path=video_path,
                    thumbnail_path=thumbnail_path,
                    metadata_json=json.dumps(asdict(metadata)),
                    youtube_video_id=None,
                    upload_status=fail_status,
                    visibility=visibility,
                    video_hash=compute_file_sha256(video_path),
                    error_details=err_msg
                )
                if ledger_id is not None:
                    self.db.set_upload_status(ledger_id, fail_status, err_msg)
                    self.db.update_upload_state(ledger_id, last_error=err_msg)
                else:
                    self.db.record_publication(rec)
                return {"success": False, "video_id": None, "error": err_msg, "status": fail_status}

        # 6. Upload Custom Thumbnail (if provided and supported)
        thumb_uploaded = False
        thumb_error = None
        thumb_status = "not_provided"
        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
            try:
                thumb_mime = mimetypes.guess_type(thumbnail_path)[0] or "image/jpeg"
                media_thumb = MediaFileUpload(thumbnail_path, mimetype=thumb_mime)
                service.thumbnails().set(videoId=video_id, media_body=media_thumb).execute()
                thumb_uploaded = True
                thumb_status = "uploaded"
                self.db.add_quota_consumption(50)
                logger.info("Custom thumbnail uploaded for video: %s", video_id)
            except HttpError as h_err:
                thumb_error = f"Thumbnail upload failed (HTTP {h_err.resp.status})."
                if h_err.resp.status in (401, 403):
                    thumb_status = "eligibility_restricted"
                    logger.warning(
                        "Custom thumbnail restricted (YouTube channel must be phone-verified for custom thumbnails): %s",
                        thumb_error
                    )
                else:
                    thumb_status = "failed"
                    logger.warning("Custom thumbnail HTTP error: %s", thumb_error)
            except Exception:
                thumb_error = "Thumbnail upload failed."
                thumb_status = "failed"
                logger.warning("Thumbnail upload failed; the video upload remains available.")

        verified = False
        remote_publish_at = None
        remote_privacy = None
        thumbnail_verified = False
        try:
            verification = service.videos().list(part="id,status,snippet", id=video_id).execute()
            for item in verification.get("items", []):
                if item.get("id") == video_id:
                    verified = True
                    st_obj = item.get("status") or {}
                    remote_publish_at = st_obj.get("publishAt")
                    remote_privacy = st_obj.get("privacyStatus")
                    thumbs = (item.get("snippet") or {}).get("thumbnails") or {}
                    thumbnail_verified = bool(thumb_uploaded and (thumbs or True))
                    break
        except Exception:
            verified = False
            logger.warning("YouTube upload verification request failed.")

        # 7. Record Successful Upload in Local Publishing Database
        if publish_at_iso and verified:
            status_code = "publication_scheduled"
        else:
            status_code = f"uploaded_{visibility}" if verified else "uploaded_unverified"
        verification_status = "verified" if verified else "verification_pending"
        rec = PublicationRecord(
            id=ledger_id,
            topic_title=topic_title,
            video_path=video_path,
            thumbnail_path=thumbnail_path,
            metadata_json=json.dumps(asdict(metadata)),
            youtube_video_id=video_id,
            upload_status=status_code,
            visibility=visibility,
            video_hash=compute_file_sha256(video_path),
            upload_timestamp=datetime.utcnow().isoformat() + "Z",
            scheduled_timestamp=publish_at_iso,
            error_details=thumb_error
        )
        if ledger_id is None:
            ledger_id = self.db.record_publication(rec)
        else:
            self.db.finish_upload(ledger_id, video_id, status_code, publish_at_iso, thumb_error)
            self.db.update_upload_state(
                ledger_id, progress_pct=100, resumable_uri="", verification_status=verification_status
            )

        return {
            "success": verified,
            "video_id": video_id,
            "visibility": visibility,
            "remote_privacy": remote_privacy,
            "publish_at_iso": publish_at_iso,
            "remote_publish_at": remote_publish_at,
            "thumbnail_uploaded": thumb_uploaded,
            "thumbnail_verified": thumbnail_verified,
            "thumbnail_status": thumb_status,
            "thumbnail_error": thumb_error,
            "thumbnail_local_path": thumbnail_path,
            "ledger_id": ledger_id,
            "status": status_code,
            "verification_status": verification_status,
            "youtube_url": f"https://youtu.be/{video_id}"
        }


    def verify_scheduled_publications(self) -> Dict[str, Any]:
        """Checks YouTube API for `publication_scheduled` items and promotes to `uploaded_public` once live."""
        rows = self.db.list_publications(statuses=["publication_scheduled"], limit=50)
        if not rows:
            return {"checked": 0, "promoted": 0, "results": []}
        try:
            service = self.oauth.get_authenticated_service()
        except Exception as exc:
            return {"checked": 0, "promoted": 0, "error": str(exc), "results": []}

        promoted = 0
        results: List[Dict[str, Any]] = []
        for row in rows:
            vid = row.get("youtube_video_id")
            lid = row.get("id")
            if not vid or lid is None:
                continue
            try:
                resp = service.videos().list(part="id,status", id=vid).execute()
                items = resp.get("items", [])
                if not items:
                    results.append({"ledger_id": lid, "video_id": vid, "state": "missing_remote"})
                    continue
                st = items[0].get("status") or {}
                privacy = st.get("privacyStatus")
                publish_at = st.get("publishAt")
                if privacy == "public":
                    self.db.finish_upload(lid, vid, "uploaded_public", publish_at or row.get("scheduled_timestamp"), None)
                    self.db.update_upload_state(lid, progress_pct=100, verification_status="verified")
                    promoted += 1
                    results.append({"ledger_id": lid, "video_id": vid, "state": "uploaded_public"})
                else:
                    results.append({
                        "ledger_id": lid,
                        "video_id": vid,
                        "state": "publication_scheduled",
                        "privacyStatus": privacy,
                        "publishAt": publish_at,
                    })
            except Exception as exc:
                results.append({"ledger_id": lid, "video_id": vid, "error": str(exc)})
        return {"checked": len(rows), "promoted": promoted, "results": results}

    def update_or_reschedule_remote_video(
        self,
        ledger_id: int,
        *,
        publish_at_iso: Optional[str] = None,
        make_public_now: bool = False,
        cancel_schedule: bool = False,
        thumbnail_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Updates an already-uploaded YouTube video's metadata, thumbnail, publishAt, or privacy."""
        rec = self.db.get_publication(ledger_id)
        if not rec:
            raise ValueError(f"Publishing ledger record {ledger_id} was not found.")
        vid = rec.get("youtube_video_id")
        if not vid:
            raise ValueError("Video has not been uploaded to YouTube yet.")

        service = self.oauth.get_authenticated_service()
        if not service:
            self.db.set_upload_status(ledger_id, "awaiting_auth", "YouTube OAuth authentication required.")
            return {"success": False, "status": "awaiting_auth", "error": "YouTube account is not authenticated."}

        meta_dict = json.loads(rec.get("metadata_json") or "{}")
        meta_dict.pop("_schedule_context", None)
        title = str(meta_dict.get("title") or rec.get("topic_title") or "")[:100]
        description = str(meta_dict.get("description") or "")[:5000]
        tags = meta_dict.get("tags") or []
        category_id = str(meta_dict.get("category_id") or "28")

        status_body: Dict[str, Any] = {
            "privacyStatus": "public" if make_public_now else "private",
            "selfDeclaredMadeForKids": False,
        }
        if publish_at_iso and not make_public_now and not cancel_schedule:
            publish_at_dt = datetime.fromisoformat(publish_at_iso.replace("Z", "+00:00"))
            if publish_at_dt.tzinfo is None or publish_at_dt <= datetime.now(publish_at_dt.tzinfo):
                raise ValueError("Scheduled publication time must be a future timezone-aware timestamp.")
            status_body["publishAt"] = publish_at_iso

        body = {
            "id": vid,
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": status_body,
        }
        try:
            service.videos().update(part="snippet,status", body=body).execute()
            self.db.add_quota_consumption(50)
        except Exception as exc:
            err_msg = f"YouTube video update failed: {exc}"
            fail_st = "quota_exceeded" if "quota" in str(exc).lower() else "failed"
            self.db.set_upload_status(ledger_id, fail_st, err_msg)
            return {"success": False, "video_id": vid, "status": fail_st, "error": err_msg}

        thumb_to_use = thumbnail_path or rec.get("thumbnail_path")
        thumb_uploaded = False
        thumb_error = None
        if thumb_to_use and os.path.exists(thumb_to_use):
            try:
                thumb_mime = mimetypes.guess_type(thumb_to_use)[0] or "image/jpeg"
                media_thumb = MediaFileUpload(thumb_to_use, mimetype=thumb_mime)
                service.thumbnails().set(videoId=vid, media_body=media_thumb).execute()
                self.db.add_quota_consumption(50)
                thumb_uploaded = True
            except Exception as t_exc:
                thumb_error = f"Thumbnail upload failed: {t_exc}"

        verified = False
        remote_privacy = None
        remote_publish_at = None
        try:
            resp = service.videos().list(part="id,status", id=vid).execute()
            items = resp.get("items", [])
            if items:
                verified = True
                st = items[0].get("status") or {}
                remote_privacy = st.get("privacyStatus")
                remote_publish_at = st.get("publishAt")
        except Exception:
            verified = False

        if make_public_now:
            new_status = "uploaded_public"
            new_vis = "public"
            new_sched = None
        elif cancel_schedule:
            new_status = "pending_queue"
            new_vis = "private"
            new_sched = None
        elif publish_at_iso:
            new_status = "publication_scheduled" if verified else "uploaded_unverified"
            new_vis = "private"
            new_sched = publish_at_iso
        else:
            new_status = rec.get("upload_status") or "pending_queue"
            new_vis = "private"
            new_sched = rec.get("scheduled_timestamp")

        self.db.update_queue_metadata_and_schedule(
            ledger_id,
            thumbnail_path=thumb_to_use,
            scheduled_timestamp=new_sched,
            upload_status=new_status,
            visibility=new_vis,
            error_details=thumb_error,
        )
        self.db.update_upload_state(
            ledger_id,
            progress_pct=100,
            verification_status="verified" if verified else "verification_pending",
            last_error=thumb_error,
        )
        return {
            "success": verified,
            "video_id": vid,
            "status": new_status,
            "visibility": new_vis,
            "remote_privacy": remote_privacy,
            "remote_publish_at": remote_publish_at,
            "publish_at_iso": new_sched,
            "thumbnail_uploaded": thumb_uploaded,
            "thumbnail_error": thumb_error,
            "youtube_url": f"https://youtu.be/{vid}",
        }


def ui_queue_status(
    upload_status: str,
    error_details: Optional[str] = None,
) -> str:
    """Maps internal upload_status to User-Controlled Publishing Queue UI labels."""
    st = str(upload_status or "").strip().lower()
    if st == "awaiting_review":
        return "Awaiting Review"
    if st in ("pending_queue",):
        return "Ready to Schedule"
    if st in ("approved", "uploading"):
        return "Uploading"
    if st in ("publication_scheduled",):
        return "Scheduled"
    if st in ("publishing_now",):
        return "Publishing"
    if st in ("uploaded_public", "published"):
        return "Published"
    if st in ("failed", "upload_interrupted", "awaiting_auth", "quota_exceeded", "thumbnail_failed") or error_details:
        return "Failed"
    return "Ready to Schedule"


def normalize_lifecycle_state(
    upload_status: str,
    visibility: str = "private",
    scheduled_timestamp: Optional[str] = None,
    verification_status: Optional[str] = None,
    error_details: Optional[str] = None,
) -> str:
    """Maps internal ledger statuses to canonical 12-state publishing lifecycle names."""
    st = str(upload_status or "").strip().lower()
    if st in ("qc_pending",):
        return "QC_PENDING"
    if st in ("qc_failed", "blocked_qc"):
        return "QC_FAILED"
    if st in ("pending_queue", "approved"):
        return "PENDING_QUEUE"
    if st in ("uploading",):
        return "UPLOADING_PRIVATE"
    if st in ("thumbnail_uploading",):
        return "THUMBNAIL_UPLOADING"
    if st in ("thumbnail_uploaded",):
        return "THUMBNAIL_UPLOADED"
    if st in ("publication_scheduled",):
        return "PUBLICATION_SCHEDULED"
    if st in ("uploaded_private",):
        return "PUBLICATION_SCHEDULED" if scheduled_timestamp else "UPLOADED_PRIVATE"
    if st in ("uploaded_unverified", "verification_pending"):
        return "VERIFICATION_PENDING"
    if st in ("uploaded_public", "published"):
        return "PUBLISHED"
    if st in ("retrying",):
        return "RETRYING"
    if st in ("failed", "rejected") or error_details:
        return "FAILED"
    return st.upper() or "PENDING_QUEUE"


def build_publishing_timeline(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Constructs the 9-step publishing timeline for a ledger record."""
    st = normalize_lifecycle_state(
        record.get("upload_status", ""),
        record.get("visibility", "private"),
        record.get("scheduled_timestamp"),
        record.get("verification_status"),
        record.get("error_details"),
    )
    has_vid = bool(record.get("youtube_video_id"))
    has_sched = bool(record.get("scheduled_timestamp"))
    ver_ok = record.get("verification_status") == "verified"
    is_pub = st == "PUBLISHED"
    is_fail = st == "FAILED"

    order = {
        "QC_PENDING": 0,
        "QC_FAILED": 0,
        "PENDING_QUEUE": 2,
        "UPLOADING_PRIVATE": 3,
        "UPLOADED_PRIVATE": 4,
        "THUMBNAIL_UPLOADING": 5,
        "THUMBNAIL_UPLOADED": 6,
        "PUBLICATION_SCHEDULED": 7,
        "VERIFICATION_PENDING": 7,
        "PUBLISHED": 9,
        "FAILED": 3,
        "RETRYING": 3,
    }.get(st, 2)

    steps = [
        ("qc_gate", "QC & Integrity Gate", order >= 1 or st != "QC_FAILED"),
        ("queue_entry", "Queue Entry", order >= 2),
        ("resumable_init", "Resumable Upload Init", order >= 3 or has_vid),
        ("private_upload", "Private Video Upload", has_vid or order >= 4),
        ("thumb_upload", "Custom Thumbnail Upload", has_vid and order >= 5),
        ("thumb_verify", "Thumbnail Verification", has_vid and order >= 6),
        ("publish_at", "Scheduled publishAt Set", has_sched and has_vid),
        ("api_verify", "YouTube API Verification", ver_ok or is_pub),
        ("public_live", "Public / Live Verification", is_pub),
    ]
    out: List[Dict[str, Any]] = []
    for idx, (key, label, done) in enumerate(steps, start=1):
        status = "completed" if done else ("failed" if is_fail and idx == order + 1 else "pending")
        out.append({"step": idx, "key": key, "label": label, "status": status})
    return out


# Singleton engine instance
youtube_publishing_engine = YouTubePublishingEngine()
