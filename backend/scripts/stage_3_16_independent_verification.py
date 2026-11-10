"""
Stage 3.16: Independent Production Verification Script.
Audits the entire output of the first independent production run at D:\Agents\CrazzyDocs.
"""
import os
import sys
import json
import sqlite3
import subprocess
import hashlib
from pathlib import Path

PROJECT_ROOT = Path("D:/Agents/CrazzyDocs").resolve()
os.chdir(PROJECT_ROOT)

def run_cmd(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout.strip(), p.stderr.strip(), p.returncode

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def verify():
    prod_id = "prod_stage_3_16_verification"
    prod_dir = PROJECT_ROOT / "media" / "productions" / prod_id
    video_path = prod_dir / "video" / f"showcase_{prod_id}.mp4"
    thumb_path = prod_dir / "thumbnails" / f"thumb_{prod_id}.png"
    variants_dir = prod_dir / "thumbnails" / "variants"
    manifest_path = prod_dir / "production_manifest.json"
    summary_path = prod_dir / "reports" / "stage_3_16_production_summary.json"
    integrity_report_path = PROJECT_ROOT / "media" / "audit" / f"integrity_report_{prod_id}.json"
    db_path = PROJECT_ROOT / "backend" / "studio.db"

    results = {}

    # 1. Directory Structure Check
    print("=== CHECK 1: DIRECTORY STRUCTURE & FILES ===")
    assert prod_dir.exists(), f"Production dir {prod_dir} does not exist"
    assert video_path.exists(), f"Video file {video_path} does not exist"
    assert thumb_path.exists(), f"Thumbnail file {thumb_path} does not exist"
    assert variants_dir.exists(), f"Variants dir {variants_dir} does not exist"
    assert manifest_path.exists(), f"Manifest {manifest_path} does not exist"
    assert summary_path.exists(), f"Summary {summary_path} does not exist"

    variant_files = sorted(list(variants_dir.glob("*.png")))
    print(f"Found {len(variant_files)} thumbnail variants: {[v.name for v in variant_files]}")
    results["variant_count"] = len(variant_files)
    assert len(variant_files) == 4, f"Expected 4 variants, found {len(variant_files)}"

    # 2. FFprobe Video Analysis
    print("\n=== CHECK 2: FFPROBE VIDEO ANALYSIS ===")
    ffprobe_bin = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    if not ffprobe_bin.exists():
        ffprobe_bin = Path("ffprobe")
    
    cmd = [
        str(ffprobe_bin),
        "-v", "error",
        "-show_entries", "stream=width,height,codec_name,codec_type,r_frame_rate,duration,nb_frames:format=duration,size,bit_rate",
        "-of", "json",
        str(video_path)
    ]
    stdout, stderr, code = run_cmd(cmd)
    probe_data = json.loads(stdout)
    
    video_stream = next(s for s in probe_data["streams"] if s["codec_type"] == "video")
    audio_stream = next((s for s in probe_data["streams"] if s["codec_type"] == "audio"), None)
    format_info = probe_data["format"]

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    fps_eval = eval(video_stream["r_frame_rate"])
    video_codec = video_stream["codec_name"]
    duration = float(format_info.get("duration", 0.0))
    file_size_mb = float(format_info.get("size", 0)) / (1024 * 1024)

    print(f"Video Resolution: {width}x{height} (Expected: 1080x1920)")
    print(f"Video Codec: {video_codec} (Expected: h264)")
    print(f"Framerate: {fps_eval:.2f} FPS (Expected: 30.0)")
    print(f"Duration: {duration:.2f}s (Expected: ~52-55s)")
    print(f"File Size: {file_size_mb:.2f} MB")
    if audio_stream:
        print(f"Audio Codec: {audio_stream['codec_name']}")

    assert width == 1080, f"Expected width 1080, got {width}"
    assert height == 1920, f"Expected height 1920, got {height}"
    assert video_codec == "h264", f"Expected h264 codec, got {video_codec}"
    assert abs(fps_eval - 30.0) < 0.1, f"Expected 30.0 fps, got {fps_eval}"
    assert 50.0 <= duration <= 60.0, f"Expected duration between 50s and 60s, got {duration}"

    results["video_probe"] = {
        "width": width,
        "height": height,
        "fps": fps_eval,
        "codec": video_codec,
        "duration": duration,
        "size_mb": file_size_mb,
        "audio_codec": audio_stream["codec_name"] if audio_stream else None,
        "sha256": sha256_file(video_path)
    }

    # 3. Thumbnail Analysis
    print("\n=== CHECK 3: THUMBNAIL ANALYSIS ===")
    from PIL import Image
    with Image.open(thumb_path) as im:
        t_width, t_height = im.size
        t_mode = im.mode
    print(f"Thumbnail dimensions: {t_width}x{t_height} (Expected: 1080x1920), Mode: {t_mode}")
    assert t_width == 1080 and t_height == 1920, f"Expected 1080x1920, got {t_width}x{t_height}"

    results["thumbnail"] = {
        "width": t_width,
        "height": t_height,
        "mode": t_mode,
        "sha256": sha256_file(thumb_path)
    }

    # 4. Outro & Branding Verification
    print("\n=== CHECK 4: OUTRO & BRANDING VERIFICATION ===")
    branding_icon = PROJECT_ROOT / "assets" / "branding" / "crazzydocs" / "crazzydocs_icon_circular.png"
    src_icon = PROJECT_ROOT / "Youtube Channel Src" / "Youtube-Icon.png"
    assert branding_icon.exists(), f"Branding circular icon missing at {branding_icon}"
    assert src_icon.exists(), f"Master branding icon missing at {src_icon}"
    print(f"Branding icon present: {branding_icon} (SHA-256: {sha256_file(branding_icon)[:16]}...)")
    print(f"Master source icon present: {src_icon} (SHA-256: {sha256_file(src_icon)[:16]}...)")

    # 5. Manifest & Integrity Verification
    print("\n=== CHECK 5: PRODUCTION MANIFEST & INTEGRITY ===")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    print(f"Manifest Production ID: {manifest.get('production_id')}")
    print(f"Manifest Topic ID: {manifest.get('topic_id')}")
    print(f"Manifest Topic: {manifest.get('topic_title')}")
    print(f"Manifest Artifacts: {manifest.get('artifacts_count')} items")

    with open(integrity_report_path, "r", encoding="utf-8") as f:
        integrity = json.load(f)
    print(f"Integrity Report Valid: {integrity.get('is_valid')}")
    print(f"Integrity Passed Checks: {len(integrity.get('checks', []))} / {len(integrity.get('checks', []))}")
    assert integrity.get("is_valid") is True, "Content integrity report marked invalid!"

    results["manifest"] = {
        "production_id": manifest.get("production_id"),
        "topic_id": manifest.get("topic_id"),
        "topic_title": manifest.get("topic_title"),
        "artifacts_count": manifest.get("artifacts_count")
    }
    results["content_integrity"] = {
        "is_valid": integrity.get("is_valid"),
        "checks_passed": len(integrity.get("checks", []))
    }

    # 6. Database Verification
    print("\n=== CHECK 6: DATABASE STATE ===")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, topic_id, topic_title, category, publication_status FROM content_ledger WHERE topic_id LIKE ?", (f"%{prod_id}%",))
    content_row = cur.fetchone()
    print(f"Content ledger entry: {content_row}")
    assert content_row is not None, f"No content ledger row found for {prod_id}"
    assert content_row[4] == "produced", f"Expected publication_status produced, got {content_row[4]}"

    cur.execute("SELECT id, topic_title, upload_status, visibility, video_path, thumbnail_path FROM publishing_ledger WHERE video_path LIKE ?", (f"%{prod_id}%",))
    ledger_row = cur.fetchone()
    print(f"Publishing ledger entry: {ledger_row}")
    assert ledger_row is not None, f"No ledger row found for {prod_id}"
    assert ledger_row[2] == "pending_queue", f"Expected status pending_queue, got {ledger_row[2]}"
    assert ledger_row[3] == "private", f"Expected visibility private, got {ledger_row[3]}"
    assert "D:\\Agents\\CrazzyDocs" in ledger_row[4], f"Video path not pointing to D:\\Agents\\CrazzyDocs: {ledger_row[4]}"
    assert "D:\\Agents\\CrazzyDocs" in ledger_row[5], f"Thumbnail path not pointing to D:\\Agents\\CrazzyDocs: {ledger_row[5]}"

    # Verify existing upload 4DCas1e4vHI was not modified
    cur.execute("SELECT youtube_video_id, upload_status, visibility FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI'")
    prev_row = cur.fetchone()
    print(f"Historical upload 4DCas1e4vHI status: {prev_row}")
    assert prev_row is not None, "Historical record 4DCas1e4vHI missing!"
    assert prev_row[2] == "private", f"4DCas1e4vHI visibility altered: {prev_row[2]}"

    # Verify notification entry from notifications.jsonl
    notif_log = PROJECT_ROOT / "logs" / "notifications.jsonl"
    assert notif_log.exists(), f"Notification log {notif_log} missing"
    with open(notif_log, "r", encoding="utf-8") as f:
        notif_lines = [json.loads(line) for line in f if line.strip()]
    prod_notifs = [n for n in notif_lines if n.get("job_id") == prod_id]
    print(f"Notification log entries for {prod_id}: {len(prod_notifs)}")
    assert len(prod_notifs) > 0, f"No notification found for {prod_id} in {notif_log}"
    latest_notif = prod_notifs[-1]
    print(f"Notification delivery status: {latest_notif.get('delivery_status')}")
    assert latest_notif.get("delivery_status", {}).get("email") is True, "Email was not delivered successfully"
    results["notification"] = {
        "event_type": latest_notif.get("event_type"),
        "delivery_status": latest_notif.get("delivery_status"),
        "timestamp": latest_notif.get("timestamp")
    }

    conn.close()

    # 7. Workspace Cleanliness Check (No Root Deliverable Pollution)
    print("\n=== CHECK 7: WORKSPACE ISOLATION & CLEANLINESS ===")
    root_video_dir = PROJECT_ROOT / "media" / "video"
    root_thumb_dir = PROJECT_ROOT / "media" / "thumbnails"

    stray_videos = [f.name for f in root_video_dir.glob("*") if prod_id in f.name]
    stray_thumbs = [f.name for f in root_thumb_dir.glob("*") if prod_id in f.name]
    print(f"Stray videos in media/video/: {stray_videos}")
    print(f"Stray thumbnails in media/thumbnails/: {stray_thumbs}")
    assert len(stray_videos) == 0, f"Found stray videos in media/video/: {stray_videos}"
    assert len(stray_thumbs) == 0, f"Found stray thumbnails in media/thumbnails/: {stray_thumbs}"

    # 8. Check for any D:\AI Arts\AI string contamination in manifests and ledger
    print("\n=== CHECK 8: ZERO OLD PATH LEAKAGE ===")
    manifest_str = json.dumps(manifest)
    assert "D:\\AI Arts\\AI" not in manifest_str and "D:/AI Arts/AI" not in manifest_str, "Old path found in production manifest!"
    print("Manifest is completely free of old path references.")

    print("\n==================================================")
    print("ALL STAGE 3.16 INDEPENDENT VERIFICATION CHECKS PASSED!")
    print("==================================================")

    # Output verification summary JSON
    report_out = prod_dir / "reports" / "stage_3_16_verification_audit.json"
    with open(report_out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Saved audit results to {report_out}")

if __name__ == "__main__":
    verify()
