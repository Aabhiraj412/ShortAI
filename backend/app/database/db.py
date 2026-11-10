"""
Database models and SQLite engine for AI Shorts Studio.
Includes persistent Job queue model with stage checkpointing, heartbeats, and idempotency.
"""
import os
from datetime import datetime
from typing import Generator
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = r"D:\AI Arts\AI"
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
    source_repository = Column(String(100), nullable=False)
    source_url = Column(String(500), nullable=False)
    download_url = Column(String(500), nullable=False)
    license_id = Column(String(50), nullable=False)
    license_url = Column(String(500), nullable=True)
    attribution_text = Column(Text, nullable=False)
    local_path = Column(String(500), nullable=True)
    asset_hash = Column(String(100), nullable=True)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    verification_status = Column(String(50), default="VERIFIED")
    job_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database tables initialized successfully.")
