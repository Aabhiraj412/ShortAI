"""
Centralized Project-Root & Path Resolver for CrazzyDocs.
Phase 5, Stage 3.15 Standard.

Resolves all project directories and tool paths relative to the canonical project root.
Allows environment override via CRAZZYDOCS_PROJECT_ROOT.
"""

import os
from pathlib import Path
from typing import Optional

# 1. Determine canonical project root dynamically
_ENV_ROOT = os.getenv("CRAZZYDOCS_PROJECT_ROOT")
if _ENV_ROOT and os.path.isdir(_ENV_ROOT):
    PROJECT_ROOT = Path(_ENV_ROOT).resolve()
else:
    # __file__ is at: <PROJECT_ROOT>/backend/app/core/paths.py
    PROJECT_ROOT = Path(__file__).resolve().parents[3]

PROJECT_ROOT_STR = str(PROJECT_ROOT)

# 2. Key project subdirectories
BACKEND_DIR = PROJECT_ROOT / "backend"
APP_DIR = BACKEND_DIR / "app"
MEDIA_DIR = PROJECT_ROOT / "media"
ASSETS_DIR = PROJECT_ROOT / "assets"
# CRAZZYDOCS_TOOLS_DIR lets a checkout without the (gitignored) portable binaries,
# e.g. a git worktree under test, use another checkout's tools read-only.
_ENV_TOOLS = os.getenv("CRAZZYDOCS_TOOLS_DIR")
TOOLS_DIR = Path(_ENV_TOOLS).resolve() if _ENV_TOOLS and os.path.isdir(_ENV_TOOLS) else PROJECT_ROOT / "tools"
DOCS_DIR = PROJECT_ROOT / "docs"
LOGS_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = PROJECT_ROOT / "cache"

# Media subdirectories
MEDIA_PRODUCTIONS_DIR = MEDIA_DIR / "productions"
MEDIA_VIDEO_DIR = MEDIA_DIR / "video"
MEDIA_THUMBNAILS_DIR = MEDIA_DIR / "thumbnails"
MEDIA_THUMBNAIL_DIR = MEDIA_DIR / "thumbnail"
MEDIA_AUDIO_DIR = MEDIA_DIR / "audio"
MEDIA_IMAGES_DIR = MEDIA_DIR / "images"
MEDIA_SUBTITLES_DIR = MEDIA_DIR / "subtitles"
MEDIA_QUARANTINE_DIR = MEDIA_DIR / "quarantine"
MEDIA_SOURCE_DIR = MEDIA_DIR / "source"
BRANDING_DIR = MEDIA_SOURCE_DIR / "branding"

# Database and Secrets
# The canonical production database location for this checkout.
PRODUCTION_DATABASE_PATH = BACKEND_DIR / "studio.db"
# CRAZZYDOCS_DATABASE_PATH redirects every app module to another SQLite file.
# The test suite sets it (tests/conftest.py) before any app import so tests can
# never open the production database.
_ENV_DB = os.getenv("CRAZZYDOCS_DATABASE_PATH")
DATABASE_PATH = Path(_ENV_DB).resolve() if _ENV_DB else PRODUCTION_DATABASE_PATH
DATABASE_BACKUPS_DIR = BACKEND_DIR / "backups"
CLIENT_SECRETS_PATH = BACKEND_DIR / "client_secrets.json"
TOKEN_PATH = BACKEND_DIR / "token.json"
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"

# Portable Runtimes & Tools
FFMPEG_DEFAULT_PATH = TOOLS_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE_DEFAULT_PATH = TOOLS_DIR / "ffmpeg" / "bin" / "ffprobe.exe"
PYTHON_TOOLS_DIR = TOOLS_DIR / "python"
UV_PATH = TOOLS_DIR / "uv" / "uv.exe"

def get_project_root() -> Path:
    """Returns the resolved Path object of the project root."""
    return PROJECT_ROOT

def get_project_root_str() -> str:
    """Returns the resolved string path of the project root."""
    return PROJECT_ROOT_STR

def resolve_path(relative_or_absolute: str) -> str:
    """
    Resolves a path:
    - If already absolute, returns normalized string.
    - If relative, joins with PROJECT_ROOT and returns normalized string.
    """
    if not relative_or_absolute:
        return PROJECT_ROOT_STR
    p = Path(relative_or_absolute)
    if p.is_absolute():
        return str(p.resolve())
    return str((PROJECT_ROOT / p).resolve())

def get_ffmpeg_path() -> str:
    """Returns absolute path to ffmpeg executable."""
    if FFMPEG_DEFAULT_PATH.is_file():
        return str(FFMPEG_DEFAULT_PATH)
    return "ffmpeg"

def get_ffprobe_path() -> str:
    """Returns absolute path to ffprobe executable."""
    if FFPROBE_DEFAULT_PATH.is_file():
        return str(FFPROBE_DEFAULT_PATH)
    return "ffprobe"
