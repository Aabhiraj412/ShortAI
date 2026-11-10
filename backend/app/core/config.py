"""
Configuration settings for AI-Powered YouTube Shorts Studio.
Reads from environment variables and local .env file.
"""
import os
from pydantic_settings import BaseSettings

BASE_DIR = r"D:\AI Arts\AI"

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI-Powered YouTube Shorts Studio"
    BASE_DIR: str = BASE_DIR
    DB_URL: str = f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, 'backend', 'studio.db')}"
    
    # AI Credentials
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_GEMINI_MODEL: str = "gemini-3.8-flash"
    
    # YouTube API / OAuth
    YOUTUBE_CLIENT_SECRETS_FILE: str = os.path.join(BASE_DIR, "backend", "client_secrets.json")
    YOUTUBE_TOKEN_FILE: str = os.path.join(BASE_DIR, "backend", "token.json")
    
    # Publishing & Automation Settings
    PUBLISHING_MODE: str = "manual_approval" # "manual_approval", "auto_publish", "unlisted_test", "private"
    DAILY_PUBLISH_TIME_IST: str = "10:00"     # 10:00 AM IST
    DEFAULT_VOICE: str = "en-US-ChristopherNeural"
    
    # Storage Directories
    TOOLS_DIR: str = os.path.join(BASE_DIR, "tools")
    MEDIA_DIR: str = os.path.join(BASE_DIR, "media")
    THUMB_DIR: str = os.path.join(BASE_DIR, "thumbnails", "output")
    LOGS_DIR: str = os.path.join(BASE_DIR, "logs")
    CACHE_DIR: str = os.path.join(BASE_DIR, "cache")
    
    class Config:
        env_file = os.path.join(BASE_DIR, ".env")
        extra = "allow"

settings = Settings()
