"""
Configuration settings for AI-Powered YouTube Shorts Studio.
Reads from environment variables and local .env / .env.local files.
Phase 5, Stage 3.15 Standard with Centralized Path Resolution.
"""
import os
from pydantic_settings import BaseSettings
from backend.app.core.paths import (
    PROJECT_ROOT_STR,
    DATABASE_PATH,
    CLIENT_SECRETS_PATH,
    TOKEN_PATH,
    TOOLS_DIR,
    MEDIA_DIR,
    LOGS_DIR,
    CACHE_DIR,
)

BASE_DIR = PROJECT_ROOT_STR

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI-Powered YouTube Shorts Studio"
    BASE_DIR: str = PROJECT_ROOT_STR
    DB_URL: str = f"sqlite+aiosqlite:///{str(DATABASE_PATH).replace(os.sep, '/')}"
    
    # AI Credentials
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_GEMINI_MODEL: str = "gemini-3.8-flash"
    
    # YouTube API / OAuth
    YOUTUBE_CLIENT_SECRETS_FILE: str = str(CLIENT_SECRETS_PATH)
    YOUTUBE_TOKEN_FILE: str = str(TOKEN_PATH)
    
    # Publishing & Automation Settings
    PUBLISHING_MODE: str = "manual_approval" # "manual_approval", "auto_publish", "unlisted_test", "private"
    DAILY_PUBLISH_TIME_IST: str = "10:00"     # 10:00 AM IST
    DEFAULT_VOICE: str = "en-US-ChristopherNeural"
    
    # Storage Directories
    TOOLS_DIR: str = str(TOOLS_DIR)
    MEDIA_DIR: str = str(MEDIA_DIR)
    THUMB_DIR: str = os.path.join(str(MEDIA_DIR), "thumbnails")
    LOGS_DIR: str = str(LOGS_DIR)
    CACHE_DIR: str = str(CACHE_DIR)
    
    class Config:
        env_file = os.path.join(PROJECT_ROOT_STR, ".env.local")
        extra = "allow"

settings = Settings()
