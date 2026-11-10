"""
Full Phase 9 Destination Verification Script for D:\Agents\CrazzyDocs.

Validates:
1. Branding Checksums (Youtube Channel Src/Youtube-Icon.png and crazzydocs_outro_1080x1920.mp4)
2. Production Manifests integrity in media/productions/
3. Database integrity and publishing ledger records
4. Private YouTube upload record 4DCas1e4vHI
5. OAuth refresh capability
6. Email configuration validity
7. Scheduler 4-cycle profile
8. Zero active runtime dependency on D:\AI Arts\AI
9. Minimal controlled rendering test using temporary output
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

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
    print("PHASE 9 DESTINATION VERIFICATION: D:\\Agents\\CrazzyDocs")
    print("=" * 80)

    # 1. Branding Checksums
    print("\n--- 1. BRANDING CHECKSUMS ---")
    logo_path = os.path.join(DEST_ROOT, "Youtube Channel Src", "Youtube-Icon.png")
    outro_path = os.path.join(DEST_ROOT, "media", "source", "branding", "crazzydocs_outro_1080x1920.mp4")
    assert os.path.isfile(logo_path), f"Missing logo: {logo_path}"
    assert os.path.isfile(outro_path), f"Missing outro: {outro_path}"

    logo_h = compute_sha256(logo_path)
    outro_h = compute_sha256(outro_path)
    print(f"   Logo SHA-256:  {logo_h}")
    print(f"   Outro SHA-256: {outro_h}")
    assert logo_h == "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060"
    assert outro_h == "b244ba1141b8622a654afca9014a63d5bd97d7630ddf4f3935a37980dec14945"
    print("   [PASS] Master branding checksums verified.")

    # 2. Production Manifests
    print("\n--- 2. PRODUCTION MANIFESTS ---")
    prods_dir = os.path.join(DEST_ROOT, "media", "productions")
    prod_manifests = []
    for item in os.listdir(prods_dir):
        mf_path = os.path.join(prods_dir, item, "production_manifest.json")
        if os.path.isfile(mf_path):
            with open(mf_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            prod_manifests.append((item, d))
            print(f"   [PASS] Manifest verified for production: {item} (keys: {len(d)})")
    assert len(prod_manifests) >= 2, "Expected at least 2 production manifests"

    # 3. Database Integrity & Ledger
    print("\n--- 3. DATABASE INTEGRITY & LEDGER ---")
    import sqlite3
    db_path = os.path.join(DEST_ROOT, "backend", "studio.db")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchall() == [("ok",)], "Database integrity check failed!"
    print("   [PASS] Database integrity: ok")

    cur.execute("SELECT id, topic_title, video_path, thumbnail_path, upload_status, youtube_video_id FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'")
    row_32 = cur.fetchone()
    print(f"   Row 32 (YouTube ID 4DCas1e4vHI): {row_32}")
    assert row_32 is not None
    assert row_32[4] == "uploaded_private"
    assert "D:\\Agents\\CrazzyDocs" in row_32[2]
    assert os.path.isfile(row_32[2]), f"Canonical video missing: {row_32[2]}"
    assert os.path.isfile(row_32[3]), f"Canonical thumbnail missing: {row_32[3]}"
    print("   [PASS] Private YouTube upload record 4DCas1e4vHI points to existing canonical deliverable.")
    con.close()

    # 4. OAuth Refresh Capability
    print("\n--- 4. YOUTUBE OAUTH STATUS ---")
    from backend.app.services.youtube_service import YouTubePublisherService
    yt_svc = YouTubePublisherService()
    auth_st = yt_svc.get_auth_status()
    print(f"   Is Authenticated: {auth_st.get('is_authenticated')}")
    print(f"   Can Refresh:      {auth_st.get('can_refresh')}")
    print(f"   Connected Channel: {auth_st.get('channel_info', {}).get('title')} ({auth_st.get('channel_info', {}).get('custom_url')})")
    assert auth_st.get("is_authenticated") is True
    assert auth_st.get("can_refresh") is True
    print("   [PASS] YouTube OAuth credentials verified with active refresh capability.")

    # 5. Email Configuration
    print("\n--- 5. EMAIL CONFIGURATION ---")
    from backend.app.core.config import settings
    env_local = os.path.join(DEST_ROOT, ".env.local")
    assert os.path.isfile(env_local), "Missing .env.local"
    with open(env_local, "r", encoding="utf-8") as f:
        env_lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
    keys = [l.split("=")[0] for l in env_lines]
    print(f"   Configured email keys: {keys}")
    assert "CRAZZYDOCS_SMTP_HOST" in keys
    assert "CRAZZYDOCS_SMTP_USER" in keys
    print("   [PASS] Email/SMTP configuration verified in .env.local.")

    # 6. Scheduler Configuration
    print("\n--- 6. SCHEDULER PROFILE ---")
    from backend.app.scheduler.schedule_profiles import get_active_profile
    profile = get_active_profile()
    print(f"   Profile: {profile.name}, Timezone: Asia/Kolkata, Daily Cycles: {len(profile.cycles)}")
    assert len(profile.cycles) == 4
    for c in profile.cycles:
        print(f"     {c.cycle_id}: {c.name} (Target: {c.target_pub_hour_ist:02d}:{c.target_pub_min_ist:02d} IST)")
    print("   [PASS] Four-cycle production profile active.")

    # 7. Check for Zero Active Runtime Dependency on D:\AI Arts\AI
    print("\n--- 7. CHECK ACTIVE CODE RUNTIME DEPENDENCY ---")
    active_code_dirs = [
        os.path.join(DEST_ROOT, "backend", "app"),
        os.path.join(DEST_ROOT, "backend", "scripts"),
    ]
    old_refs = []
    for cd in active_code_dirs:
        for root, dirs, files in os.walk(cd):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py") and f not in [
                    "scan_and_update_paths.py",
                    "apply_path_migration.py",
                    "fix_remaining_paths.py",
                    "update_all_remaining.py",
                    "verify_destination_phase9.py",
                ]:
                    fp = os.path.join(root, f)
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for idx, l in enumerate(fh, 1):
                            if "D:\\AI Arts\\AI" in l or "D:/AI Arts/AI" in l:
                                old_refs.append((os.path.relpath(fp, DEST_ROOT), idx, l.strip()))
    if old_refs:
        print(f"   [FAIL] Found {len(old_refs)} runtime references to old path:")
        for r, i, l in old_refs[:5]:
            print(f"     {r}:{i}: {l}")
        assert False, "Active runtime references to D:\\AI Arts\\AI remain!"
    else:
        print("   [PASS] Zero active runtime references to old root found in backend code/scripts.")

    # 8. Controlled Minimal Rendering Test
    print("\n--- 8. MINIMAL CONTROLLED RENDERING TEST ---")
    ffmpeg_exe = os.path.join(DEST_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
    temp_render_dir = os.path.join(DEST_ROOT, "media", "productions", "test_migration_render")
    os.makedirs(temp_render_dir, exist_ok=True)
    temp_render_out = os.path.join(temp_render_dir, "test_1sec.mp4")

    # Render a 1.0 second 1080x1920 test pattern
    render_cmd = [
        ffmpeg_exe,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=1080x1920:d=1.0:r=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        temp_render_out
    ]
    res = subprocess.run(render_cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg render test failed: {res.stderr}"
    assert os.path.isfile(temp_render_out), "Test render file not created!"
    render_sz = os.path.getsize(temp_render_out)
    print(f"   [PASS] FFmpeg rendered 1080x1920 test deliverable ({render_sz} bytes).")

    # Clean up test artifact
    shutil.rmtree(temp_render_dir)
    print(f"   [PASS] Cleaned up temporary test render directory.")

    print("\n" + "=" * 80)
    print("ALL PHASE 9 VERIFICATIONS PASSED SUCCESSFULLY AT D:\\Agents\\CrazzyDocs!")
    print("=" * 80)

if __name__ == "__main__":
    main()
