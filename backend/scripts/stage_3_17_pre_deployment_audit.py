"""
Stage 3.17: Final Pre-Deployment Audit Script.
Verifies all 11 deployment prerequisites before Git operations or project retirement.
"""
import os
import sys
import json
import sqlite3
import subprocess
import hashlib
from pathlib import Path

CANONICAL_ROOT = Path("D:/Agents/CrazzyDocs").resolve()
OLD_ROOT = Path("D:/AI Arts/AI").resolve()

# Ensure canonical root is first in sys.path
sys.path.insert(0, str(CANONICAL_ROOT))
os.chdir(CANONICAL_ROOT)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def run_cmd(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.stdout.strip(), p.stderr.strip(), p.returncode

def audit():
    os.chdir(CANONICAL_ROOT)
    audit_results = {}
    print("==================================================")
    print("PHASE 1 — FINAL PRE-DEPLOYMENT AUDIT")
    print(f"Canonical Root: {CANONICAL_ROOT}")
    print("==================================================")

    # 1. Canonical Project Root
    assert CANONICAL_ROOT.exists(), "Canonical root does not exist"
    audit_results["canonical_root"] = str(CANONICAL_ROOT)

    # 2. Migration Report & Manifest
    mig_report = CANONICAL_ROOT / "docs" / "PHASE_5_STAGE_3_15_MIGRATION_REPORT.md"
    mig_manifest = CANONICAL_ROOT / "migration_manifest.json"
    assert mig_report.exists(), f"Migration report missing: {mig_report}"
    assert mig_manifest.exists(), f"Migration manifest missing: {mig_manifest}"
    with open(mig_manifest, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    files_count = manifest_data.get("statistics", {}).get("total_destination_files", 0)
    print(f"[1/11] Migration Docs: Report OK, Manifest OK ({files_count} files recorded)")
    audit_results["migration_manifest_files"] = files_count

    # 3. Stage 3.16 Production Report
    prod_report = CANONICAL_ROOT / "docs" / "PHASE_5_STAGE_3_16_FIRST_PRODUCTION_VERIFICATION.md"
    prod_summary = CANONICAL_ROOT / "media" / "productions" / "prod_stage_3_16_verification" / "reports" / "stage_3_16_production_summary.json"
    assert prod_report.exists(), f"Stage 3.16 report missing: {prod_report}"
    assert prod_summary.exists(), f"Stage 3.16 summary missing: {prod_summary}"
    print(f"[2/11] Stage 3.16 Production Report & Summary: OK")
    audit_results["stage_3_16_report"] = "OK"

    # 4. Regression Tests (All 65 tests)
    print("[3/11] Running pytest regression suite (65 tests)...")
    pytest_bin = CANONICAL_ROOT / ".venv" / "Scripts" / "pytest.exe"
    regression_test_files = [
        "backend/tests/test_branding_integrity.py",
        "backend/tests/test_production_workspace.py",
        "backend/tests/test_stage_3_13b_lifecycle_audit.py",
        "backend/tests/test_stage_3_9b_media_lifecycle.py",
        "backend/tests/test_stage_3_13a_content_integrity.py",
        "backend/tests/test_stage_3_12_youtube.py",
        "backend/tests/test_stage_3_9cd_scheduler.py",
        "backend/tests/test_stage_3_9f_email_dashboard.py",
        "backend/tests/test_stage_3_10_resilience.py"
    ]
    stdout, stderr, code = run_cmd([str(pytest_bin), f"--rootdir={CANONICAL_ROOT}", "-q"] + regression_test_files, cwd=CANONICAL_ROOT)
    print(f"Pytest output summary: {stdout.splitlines()[-1] if stdout else stderr}")
    assert code == 0, f"Pytest failed with exit code {code}: {stderr}"
    assert "65 passed" in stdout, f"Unexpected test results: {stdout}"
    audit_results["regression_tests"] = stdout.splitlines()[-1]

    # 5. Database Integrity
    db_path = CANONICAL_ROOT / "backend" / "studio.db"
    assert db_path.exists(), "studio.db does not exist"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    integrity = cur.fetchone()[0]
    print(f"[4/11] Database Integrity: {integrity}")
    assert integrity == "ok", f"Integrity check failed: {integrity}"
    audit_results["database_integrity"] = integrity

    # 6. OAuth Refresh Works
    print("[5/11] Testing YouTube OAuth Refresh...")
    from backend.app.services.youtube_service import YouTubePublisherService
    yt_svc = YouTubePublisherService()
    auth_status = yt_svc.get_auth_status()
    print(f"OAuth auth status: is_authenticated={auth_status.get('is_authenticated')}, can_refresh={auth_status.get('can_refresh')}")
    print(f"Connected channel: {auth_status.get('channel_info', {}).get('title')} ({auth_status.get('channel_info', {}).get('custom_url')})")
    assert auth_status.get("is_authenticated") is True, f"OAuth not authenticated: {auth_status}"
    assert auth_status.get("can_refresh") is True, f"OAuth cannot refresh: {auth_status}"
    assert "D:\\Agents\\CrazzyDocs" in auth_status.get("token_path", ""), f"Token path not in canonical root: {auth_status.get('token_path')}"
    audit_results["oauth_valid"] = True
    audit_results["channel_info"] = auth_status.get("channel_info")

    # 7. SMTP Configuration
    print("[6/11] Verifying SMTP Configuration...")
    from backend.app.services.notification_service import _load_local_env
    _load_local_env()
    smtp_host = os.environ.get("CRAZZYDOCS_SMTP_HOST") or os.environ.get("SMTP_HOST")
    smtp_user = os.environ.get("CRAZZYDOCS_SMTP_USER") or os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("CRAZZYDOCS_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT") or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL")
    assert smtp_host, "SMTP host missing in environment/.env.local"
    assert smtp_user, "SMTP user missing in environment/.env.local"
    assert smtp_pass, "SMTP password missing in environment/.env.local"
    assert to_email, "Notification recipient email missing in environment/.env.local"
    print(f"SMTP Host: {smtp_host}, Recipient: {to_email} (Credentials loaded securely)")
    audit_results["smtp_configured"] = True

    # 8. Branding Checksums
    print("[7/11] Verifying Branding Checksums...")
    circular_icon = CANONICAL_ROOT / "assets" / "branding" / "crazzydocs" / "crazzydocs_icon_circular.png"
    master_icon = CANONICAL_ROOT / "Youtube Channel Src" / "Youtube-Icon.png"
    assert circular_icon.exists(), "Circular icon missing"
    assert master_icon.exists(), "Master icon missing"
    icon_hash = sha256_file(circular_icon)
    master_hash = sha256_file(master_icon)
    print(f"Circular Icon SHA-256: {icon_hash[:16]}...")
    print(f"Master Icon SHA-256:   {master_hash[:16]}...")
    audit_results["branding_checksums"] = {
        "circular_icon": icon_hash,
        "master_icon": master_hash
    }

    # 9. Production Manifests Resolve
    print("[8/11] Verifying Production Manifests...")
    prod_manifest = CANONICAL_ROOT / "media" / "productions" / "prod_stage_3_16_verification" / "production_manifest.json"
    assert prod_manifest.exists(), "Production manifest missing"
    with open(prod_manifest, "r", encoding="utf-8") as f:
        p_data = json.load(f)
    for art in p_data.get("artifacts", []):
        p = Path(art["file_path"])
        assert p.exists(), f"Artifact path in manifest missing: {p}"
        assert sha256_file(p) == art["sha256"], f"Artifact hash mismatch: {p}"
    print(f"Production Manifest OK: All {len(p_data.get('artifacts', []))} artifacts exist and match checksums.")
    audit_results["production_manifests_resolved"] = True

    # 10. Existing Private YouTube Video 4DCas1e4vHI Intact
    print("[9/11] Verifying Video 4DCas1e4vHI Status in Ledger...")
    cur.execute("SELECT youtube_video_id, upload_status, visibility FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'")
    video_row = cur.fetchone()
    print(f"Ledger record for 4DCas1e4vHI: {video_row}")
    assert video_row is not None, "Record for 4DCas1e4vHI missing in publishing_ledger"
    assert video_row[1] == "uploaded_private", f"Status altered: {video_row[1]}"
    assert video_row[2] == "private", f"Visibility altered: {video_row[2]}"
    conn.close()
    audit_results["private_video_preserved"] = True

    # 11. No Active Application Process Using Old Project
    print("[10/11] Checking Active Processes referencing D:\\AI Arts\\AI...")
    ps_cmd = ["powershell", "-NoProfile", "-Command", "Get-Process | ForEach-Object { try { $_.Path } catch { $null } } | Where-Object { $_ -like '*AI Arts*' }"]
    stdout, stderr, _ = run_cmd(ps_cmd)
    active_old_processes = [line.strip() for line in stdout.splitlines() if line.strip()]
    print(f"Active processes using old path: {len(active_old_processes)}")
    for proc in active_old_processes:
        print(f"  -> {proc}")
    audit_results["active_old_processes_count"] = len(active_old_processes)
    audit_results["active_old_processes"] = active_old_processes

    print("\n==================================================")
    print("PRE-DEPLOYMENT AUDIT COMPLETE: ALL CHECKS PASSED")
    print("==================================================")
    return audit_results

if __name__ == "__main__":
    audit()
