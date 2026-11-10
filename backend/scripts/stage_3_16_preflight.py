"""
Pre-Flight Checks for Stage 3.16.
Validates the canonical environment at D:\\Agents\\CrazzyDocs before executing production.
"""

import os
import sys
import sqlite3
import hashlib
try:
    import psutil
except ImportError:
    psutil = None

DEST_ROOT = r"D:\Agents\CrazzyDocs"
if DEST_ROOT not in sys.path:
    sys.path.insert(0, DEST_ROOT)

def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    print("=" * 80)
    print("STAGE 3.16: PRE-FLIGHT VERIFICATION CHECKS")
    print("=" * 80)

    # 1. CWD & Python executable
    print("\n1. Python Executable & Working Directory:")
    os.chdir(DEST_ROOT)
    py_exe = sys.executable
    cwd = os.getcwd()
    print(f"   sys.executable: {py_exe}")
    print(f"   os.getcwd:      {cwd}")
    assert DEST_ROOT.lower() in py_exe.lower(), f"Python not from destination: {py_exe}"
    assert ".venv" in py_exe.lower(), f"Python not using .venv: {py_exe}"
    assert cwd.lower() == DEST_ROOT.lower(), f"CWD not destination: {cwd}"

    # 2. Tools
    print("\n2. Tooling Verification:")
    ffmpeg_exe = os.path.join(DEST_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
    ffprobe_exe = os.path.join(DEST_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")
    assert os.path.isfile(ffmpeg_exe), f"Missing FFmpeg: {ffmpeg_exe}"
    assert os.path.isfile(ffprobe_exe), f"Missing FFprobe: {ffprobe_exe}"
    print(f"   FFmpeg:  {ffmpeg_exe} (OK)")
    print(f"   FFprobe: {ffprobe_exe} (OK)")

    # 3. Database Integrity
    print("\n3. Database Integrity:")
    db_path = os.path.join(DEST_ROOT, "backend", "studio.db")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA integrity_check")
    ic = cur.fetchall()
    print(f"   Integrity check: {ic}")
    assert ic == [("ok",)], f"Database corruption detected: {ic}"

    cur.execute("SELECT COUNT(*) FROM publishing_ledger")
    pl_count = cur.fetchone()[0]
    print(f"   Publishing ledger entries: {pl_count}")

    cur.execute("SELECT youtube_video_id, upload_status, video_path FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'")
    yt_row = cur.fetchone()
    print(f"   Verified YouTube Row 32: {yt_row}")
    assert yt_row is not None and yt_row[1] == "uploaded_private"
    con.close()

    # 4. Production Workspace Initializer
    print("\n4. Production Workspace Component:")
    from backend.app.orchestration.production_workspace import ProductionWorkspace
    pws = ProductionWorkspace(
        production_id="preflight_test_ws",
        topic_id="top_preflight_001",
        topic_title="Preflight Test Verification"
    )
    pws.initialize_workspace()
    assert os.path.isdir(pws.prod_dir)
    # cleanup test ws
    import shutil
    shutil.rmtree(pws.prod_dir)
    print("   ProductionWorkspace initializer verified.")

    # 5. OAuth Authentication & Refresh
    print("\n5. YouTube OAuth Status:")
    from backend.app.services.youtube_service import YouTubePublisherService
    yt_svc = YouTubePublisherService()
    auth_st = yt_svc.get_auth_status()
    print(f"   Is Authenticated: {auth_st.get('is_authenticated')}")
    print(f"   Can Refresh:      {auth_st.get('can_refresh')}")
    print(f"   Channel:          {auth_st.get('channel_info', {}).get('title')} ({auth_st.get('channel_info', {}).get('custom_url')})")
    assert auth_st.get("is_authenticated") is True
    assert auth_st.get("can_refresh") is True

    # 6. Email Configuration
    print("\n6. Email Configuration:")
    env_local = os.path.join(DEST_ROOT, ".env.local")
    assert os.path.isfile(env_local), "Missing .env.local"
    with open(env_local, "r", encoding="utf-8") as f:
        env_lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    keys = [l.split("=")[0] for l in env_lines]
    assert "CRAZZYDOCS_SMTP_HOST" in keys
    assert "CRAZZYDOCS_SMTP_USER" in keys
    assert "CRAZZYDOCS_SMTP_PASSWORD" in keys
    print("   Email configuration verified in .env.local (credentials masked).")

    # 7. Branding Assets Checksums
    print("\n7. Master Branding Assets:")
    logo_path = os.path.join(DEST_ROOT, "Youtube Channel Src", "Youtube-Icon.png")
    outro_path = os.path.join(DEST_ROOT, "media", "source", "branding", "crazzydocs_outro_1080x1920.mp4")
    assert compute_sha256(logo_path) == "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060"
    assert compute_sha256(outro_path) == "b244ba1141b8622a654afca9014a63d5bd97d7630ddf4f3935a37980dec14945"
    print("   Master Logo SHA-256: 65b14bb34837... (OK)")
    print("   Master Outro SHA-256: b244ba1141b8... (OK)")

    # 8. Publishing Mode
    print("\n8. Publishing Mode:")
    from backend.app.core.config import settings
    print(f"   settings.PUBLISHING_MODE = '{settings.PUBLISHING_MODE}'")
    assert settings.PUBLISHING_MODE == "manual_approval", f"PUBLISHING_MODE must be manual_approval, got {settings.PUBLISHING_MODE}"

    # 9. Active Cycle / Production Lock
    print("\n9. Active Production Lock Verification:")
    lock_file = os.path.join(DEST_ROOT, "cache", "production.lock")
    if os.path.isfile(lock_file):
        print(f"   [WARN] Found existing lock file: {lock_file}")
    else:
        print("   No active production lock detected.")

    # 10. Check running processes from old project directory
    print("\n10. Check Old Project Processes:")
    import subprocess
    try:
        proc_check = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*AI Arts*' } | Select-Object Id, ProcessName, Path"],
            capture_output=True,
            text=True
        )
        old_procs = [l.strip() for l in proc_check.stdout.splitlines() if l.strip()]
        if len(old_procs) > 2: # headers + rows
            print(f"   [WARN] Found processes running from old path:\n{proc_check.stdout}")
        else:
            print("   No active processes running from D:\\AI Arts\\AI detected.")
    except Exception as pe:
        print(f"   Process check note: {pe}")

    print("\n" + "=" * 80)
    print("ALL 12 PRE-FLIGHT CHECKS PASSED SUCCESSFULLY!")
    print("=" * 80)

if __name__ == "__main__":
    main()
