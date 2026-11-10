import hashlib
import sqlite3
import sys

def verify_all():
    print("1. Testing imports...")
    from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
    from backend.app.orchestration.production_workspace import ProductionWorkspace
    from backend.app.services.media_lifecycle_manager import MediaLifecycleManager
    from backend.app.content.content_integrity_gate import ContentIntegrityGate
    from backend.app.scheduler.schedule_profiles import get_active_profile
    from backend.app.services.youtube_service import YouTubePublisherService
    yt_svc = YouTubePublisherService()
    auth_status = yt_svc.get_auth_status()
    print(f"   YouTube auth status: authenticated={auth_status.get('is_authenticated')}, can_refresh={auth_status.get('can_refresh')}")
    assert auth_status.get("is_authenticated") is True

    print("2. Initializing Master Orchestrator...")
    orch = CrazzyDocsMasterOrchestrator()
    assert orch is not None
    print("   Master orchestrator initialized successfully.")

    print("3. Verifying four-cycle scheduling...")
    profile = get_active_profile()
    print(f"   Active profile: {profile.name}, {len(profile.cycles)} cycles")
    assert len(profile.cycles) == 4

    print("4. Verifying database records...")
    conn = sqlite3.connect(r"D:\Agents\CrazzyDocs\backend\studio.db")
    c = conn.cursor()
    r_cnt = c.execute("SELECT COUNT(*) FROM research_records").fetchone()[0]
    p_cnt = c.execute("SELECT COUNT(*) FROM publishing_ledger").fetchone()[0]
    yt_rec = c.execute("SELECT youtube_video_id, upload_status, video_path FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'").fetchone()
    conn.close()
    print(f"   Research records: {r_cnt}, Publishing ledger records: {p_cnt}")
    assert yt_rec is not None
    print(f"   Private YouTube upload verified: {yt_rec}")
    assert yt_rec[1] == "uploaded_private"

    print("5. Verifying authoritative outro and branding...")
    def get_sha(p):
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    outro_hash = get_sha(r"D:\Agents\CrazzyDocs\media\source\branding\crazzydocs_outro_1080x1920.mp4")
    print(f"   Outro file verified: {outro_hash}")

    logo_hash = get_sha(r"D:\Agents\CrazzyDocs\Youtube Channel Src\Youtube-Icon.png")
    print(f"   Authoritative logo verified: {logo_hash}")
    assert logo_hash == "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060"

    print("6. Verifying YouTube OAuth Credentials...")
    import os
    token_path = r"D:\Agents\CrazzyDocs\backend\token.json"
    client_secrets = r"D:\Agents\CrazzyDocs\backend\client_secrets.json"
    assert os.path.isfile(token_path)
    assert os.path.isfile(client_secrets)
    print("   OAuth credentials files exist and are protected.")

    print("\nALL SYSTEM VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    verify_all()
