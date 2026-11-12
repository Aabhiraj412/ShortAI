"""
Database models and SQLite engine for AI Shorts Studio.
Includes persistent Job queue model with stage checkpointing, heartbeats, and idempotency.
"""
import os
from datetime import datetime
from typing import Generator
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from backend.app.core.paths import PROJECT_ROOT_STR as BASE_DIR
DB_PATH = os.path.join(BASE_DIR, "backend", "studio.db")
SQLITE_URL = f"sqlite:///{DB_PATH}"

Base = declarative_base()
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class JobModel(Base):
    """
    Persistent Job State Machine for autonomous production pipeline.
    Survives application restarts, power interruptions, and process failures.
    """
    __tablename__ = "persistent_jobs"
    
    id = Column(String(100), primary_key=True) # Unique Job ID e.g. job_20261110_230000
    date_str = Column(String(20), index=True, nullable=False) # e.g. "2026-11-10" for duplicate prevention
    topic_title = Column(String(255), nullable=True)
    
    # State tracking: PENDING, RUNNING, RETRYING, PAUSED, FAILED, COMPLETED, CANCELLED
    status = Column(String(50), default="PENDING", index=True)
    
    # Current stage:
    # RESEARCH, TOPIC_SELECTION, SCRIPT, STORYBOARD, ASSET_PREPARATION,
    # ANIMATION, VOICEOVER, SUBTITLES, VIDEO_COMPOSITION, THUMBNAIL,
    # SEO, QUALITY_CHECK, YOUTUBE_UPLOAD, YOUTUBE_PROCESSING, PUBLICATION_SCHEDULING, COMPLETED
    current_stage = Column(String(50), default="RESEARCH")
    progress_pct = Column(Float, default=0.0)
    
    # Heartbeat and retries
    heartbeat_at = Column(DateTime, default=datetime.utcnow)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    error_details = Column(Text, nullable=True)
    
    # Checkpointing: JSON object holding paths and outputs of completed stages
    # { "topic": {...}, "script": {...}, "audio_path": "...", "ass_path": "...", "video_path": "...", "thumbnail_path": "...", "seo": {...} }
    checkpoint_data_json = Column(Text, default="{}")
    
    # Publication metadata
    youtube_video_id = Column(String(100), nullable=True)
    scheduled_publication_time = Column(String(50), default="10:00 AM IST")
    publishing_mode = Column(String(50), default="MANUAL_APPROVAL")

    # Stage 3.10 Resilient Recovery & Slot Management
    recovery_reason = Column(String(100), nullable=True) # STARTUP_RECONCILIATION, INTERNET_RESTORED, CHECKPOINT_RESUMED
    recovery_policy = Column(String(50), default="STRICT_WINDOW")
    original_target_pub = Column(String(100), nullable=True)
    revised_target_pub = Column(String(100), nullable=True)
    actual_start_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class TopicModel(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(100), default="science")
    score = Column(Float, default=0.0)
    sources_json = Column(Text, default="[]")
    is_selected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class ScriptModel(Base):
    __tablename__ = "scripts"
    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    title = Column(String(255), nullable=False)
    hook = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    estimated_duration = Column(Float, default=0.0)
    references_json = Column(Text, default="[]")
    is_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class StoryboardModel(Base):
    __tablename__ = "storyboards"
    id = Column(Integer, primary_key=True, index=True)
    script_id = Column(Integer, ForeignKey("scripts.id"), nullable=True)
    title = Column(String(255), nullable=False)
    total_duration = Column(Float, default=16.0)
    scenes_json = Column(Text, nullable=False)
    is_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class VideoJobModel(Base):
    __tablename__ = "video_jobs"
    id = Column(Integer, primary_key=True, index=True)
    storyboard_id = Column(Integer, ForeignKey("storyboards.id"), nullable=True)
    status = Column(String(50), default="pending")
    video_path = Column(String(500), nullable=True)
    thumbnail_path = Column(String(500), nullable=True)
    duration = Column(Float, default=0.0)
    file_size_mb = Column(Float, default=0.0)
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PublicationModel(Base):
    __tablename__ = "publications"
    id = Column(Integer, primary_key=True, index=True)
    video_job_id = Column(Integer, ForeignKey("video_jobs.id"), nullable=True)
    youtube_video_id = Column(String(100), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    tags_json = Column(Text, default="[]")
    status = Column(String(50), default="draft")
    scheduled_time = Column(String(50), default="10:00 AM IST")
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class StudioSettingModel(Base):
    __tablename__ = "studio_settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)

class ResearchRecordModel(Base):
    __tablename__ = "research_records"
    id = Column(Integer, primary_key=True, index=True)
    topic_title = Column(String(255), nullable=False, index=True)
    category = Column(String(100), default="science")
    factual_claims_json = Column(Text, default="[]")
    sources_json = Column(Text, default="[]")
    verification_status = Column(String(50), default="VERIFIED")
    verification_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MediaAssetModel(Base):
    __tablename__ = "media_assets"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String(100), unique=True, index=True)
    title = Column(String(255), nullable=False)
    creator = Column(String(255), nullable=True)
    source_repository = Column(String(100), nullable=False)  # Openverse, Wikimedia, NASA
    source_url = Column(String(500), nullable=False)
    download_url = Column(String(500), nullable=False)
    license_id = Column(String(50), nullable=False)          # CC0, Public Domain, CC BY 4.0
    license_url = Column(String(500), nullable=True)
    attribution_text = Column(Text, nullable=False)
    local_path = Column(String(500), nullable=True)
    portrait_path = Column(String(500), nullable=True)
    asset_hash = Column(String(100), nullable=True, index=True)
    mime_type = Column(String(50), nullable=True)
    file_size_bytes = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    aspect_ratio = Column(Float, default=1.0)
    relevance_score = Column(Float, default=0.0)
    search_query = Column(String(255), nullable=True)
    verification_status = Column(String(50), default="APPROVED")  # APPROVED, REJECTED, PENDING
    rejection_reason = Column(Text, nullable=True)
    job_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PersistentNotificationModel(Base):
    """
    Persistent notification ledger for cycle summaries, daily digests, and alerts.
    Enforces idempotency, restart survival, and delivery status tracking.
    """
    __tablename__ = "persistent_notifications"

    id = Column(String(100), primary_key=True)  # notification_id
    job_id = Column(String(100), index=True, nullable=True)
    cycle_id = Column(String(50), index=True, nullable=True)
    notification_type = Column(String(50), default="CYCLE_SUMMARY") # CYCLE_SUMMARY, DAILY_DIGEST, ALERT
    recipient = Column(String(255), nullable=False)
    status = Column(String(50), default="PENDING", index=True) # PENDING, DISPATCHING, SENT, FAILED, DELIVERY_UNKNOWN, SUPPRESSED
    delivery_state = Column(String(50), default="PENDING", index=True) # PENDING, DISPATCHING, CONFIRMED_SENT, FAILED_RETRYABLE, DELIVERY_UNKNOWN, SUPPRESSED
    error_category = Column(String(50), nullable=True)
    provider_response = Column(Text, nullable=True)
    attempt_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    idempotency_key = Column(String(255), unique=True, index=True, nullable=False)
    subject = Column(String(500), nullable=False)
    body_plain = Column(Text, nullable=False)
    body_html = Column(Text, nullable=True)
    payload_json = Column(Text, default="{}")


def ensure_notification_ledger_schema(db_engine=None):
    """Safely applies backward-compatible columns to persistent_notifications without modifying existing rows."""
    target_engine = db_engine or engine
    try:
        from sqlalchemy import text
        with target_engine.begin() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(persistent_notifications)")).fetchall()}
            if not cols:
                return
            additions = [
                ("delivery_state", "VARCHAR(50) DEFAULT 'PENDING'"),
                ("error_category", "VARCHAR(50)"),
                ("provider_response", "TEXT"),
                ("updated_at", "DATETIME"),
            ]
            for col_name, col_type in additions:
                if col_name not in cols:
                    conn.execute(text(f"ALTER TABLE persistent_notifications ADD COLUMN {col_name} {col_type}"))
            conn.execute(text("""
                UPDATE persistent_notifications
                SET delivery_state = CASE
                    WHEN status = 'SENT' THEN 'CONFIRMED_SENT'
                    WHEN status = 'FAILED' THEN 'FAILED_RETRYABLE'
                    WHEN status = 'DELIVERY_UNKNOWN' THEN 'DELIVERY_UNKNOWN'
                    ELSE COALESCE(delivery_state, 'PENDING')
                END
                WHERE delivery_state IS NULL
                   OR delivery_state = ''
                   OR (status = 'SENT' AND delivery_state = 'PENDING')
                   OR (status = 'FAILED' AND delivery_state = 'PENDING')
                   OR (status = 'DELIVERY_UNKNOWN' AND delivery_state != 'DELIVERY_UNKNOWN')
            """))
    except Exception as e:
        print(f"Notification ledger migration note: {e}")


def init_db():
    Base.metadata.create_all(bind=engine)
    # Safe migration for Stage 3.10 persistent_jobs columns
    try:
        import sqlite3
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("PRAGMA table_info(persistent_jobs)")
        existing_cols = {row[1] for row in cur.fetchall()}
        
        new_cols = [
            ("recovery_reason", "VARCHAR(100)"),
            ("recovery_policy", "VARCHAR(50) DEFAULT 'STRICT_WINDOW'"),
            ("original_target_pub", "VARCHAR(100)"),
            ("revised_target_pub", "VARCHAR(100)"),
            ("actual_start_at", "DATETIME"),
        ]
        for col_name, col_type in new_cols:
            if col_name not in existing_cols:
                cur.execute(f"ALTER TABLE persistent_jobs ADD COLUMN {col_name} {col_type}")
        con.commit()
        con.close()
    except Exception as e:
        print(f"Migration note: {e}")
    ensure_notification_ledger_schema(engine)
    print("Database tables initialized successfully.")
