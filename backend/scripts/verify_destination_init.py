"""
Destination Environment & Application Initialization Verification.
Tests imports, paths, database, YouTube OAuth, scheduler, and orchestrator.
"""

import os
import sys

DEST_ROOT = r"D:\Agents\CrazzyDocs"
if DEST_ROOT not in sys.path:
    sys.path.insert(0, DEST_ROOT)

print("1. Testing core paths and resolver...")
from backend.app.core.paths import get_project_root, get_ffmpeg_path, get_ffprobe_path
assert str(get_project_root()).lower() == DEST_ROOT.lower()
print(f"   Project Root: {get_project_root()}")
print(f"   FFmpeg Path: {get_ffmpeg_path()}")
print(f"   FFprobe Path: {get_ffprobe_path()}")

print("\n2. Testing agent identity...")
from backend.app.core.agent_identity import get_agent_identity
identity = get_agent_identity()
print(f"   Agent ID: {identity.agent_id}")
print(f"   Agent Name: {identity.agent_name}")
print(f"   Agent Root: {identity.agent_root}")
assert identity.agent_id == "crazzydocs"

print("\n3. Testing settings and database connection...")
from backend.app.core.config import settings
print(f"   Settings BASE_DIR: {settings.BASE_DIR}")
print(f"   Settings DB_URL: {settings.DB_URL}")
assert DEST_ROOT.lower() in settings.BASE_DIR.lower()

import sqlite3
con = sqlite3.connect(os.path.join(DEST_ROOT, "backend", "studio.db"))
cur = con.cursor()
cur.execute("PRAGMA integrity_check")
ic = cur.fetchall()
assert ic == [("ok",)], f"Integrity check failed: {ic}"
print(f"   Database integrity: {ic[0][0]}")

cur.execute("SELECT COUNT(*) FROM publishing_ledger")
plc = cur.fetchone()[0]
print(f"   Publishing ledger records: {plc}")

cur.execute("SELECT youtube_video_id, upload_status, video_path FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'")
yt_row = cur.fetchone()
print(f"   Private YouTube upload row 32: {yt_row}")
assert yt_row is not None
assert yt_row[1] == "uploaded_private"
assert DEST_ROOT.lower() in yt_row[2].lower()
con.close()

print("\n4. Testing YouTube OAuth status and token refresh...")
from backend.app.services.youtube_service import YouTubePublisherService
yt = YouTubePublisherService()
auth_st = yt.get_auth_status()
print(f"   YouTube Auth Status: {auth_st}")
assert auth_st.get("is_authenticated") is True
assert auth_st.get("can_refresh") is True

print("\n5. Testing scheduler configuration...")
from backend.app.scheduler.schedule_profiles import get_active_profile
profile = get_active_profile()
print(f"   Active Profile: {profile.name}, Cycles: {len(profile.cycles)}")
assert len(profile.cycles) == 4

print("\n6. Testing Master Orchestrator initialization...")
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
orch = CrazzyDocsMasterOrchestrator()
print(f"   Master Orchestrator initialized successfully.")

print("\nALL DESTINATION INITIALIZATION CHECKS PASSED!")
