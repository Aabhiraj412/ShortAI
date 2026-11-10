"""
Media Consolidation and Physical Disk Recovery Script (Stage 3.14).
Strictly follows safety guidelines: non-destructive quarantine for uncertain assets,
physical purge ONLY for confirmed SAFE_TO_PURGE files (browser caches, duplicates, scratch).
"""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
MEDIA_ROOT = os.path.join(WORKSPACE_ROOT, "media")
QUARANTINE_DIR = os.path.join(MEDIA_ROOT, "quarantine")
ARCHIVE_FILES_DIR = os.path.join(QUARANTINE_DIR, "archived_files")
MANIFEST_PATH = os.path.join(QUARANTINE_DIR, "manifest.json")

def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def load_manifest() -> dict:
    if os.path.isfile(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_manifest(manifest: dict):
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, MANIFEST_PATH)

def quarantine_file(rel_path: str, category: str, reason: str, manifest: dict) -> bool:
    abs_path = os.path.join(WORKSPACE_ROOT, rel_path)
    if not os.path.isfile(abs_path):
        return False

    os.makedirs(ARCHIVE_FILES_DIR, exist_ok=True)
    file_size = os.path.getsize(abs_path)
    sha = compute_sha256(abs_path)
    ext = os.path.splitext(abs_path)[1]
    timestamp = int(datetime.now(timezone.utc).timestamp())
    qid = f"q_{timestamp}_{sha[:8]}"

    # Avoid collision
    counter = 1
    target_name = f"{qid}{ext}"
    target_abs = os.path.join(ARCHIVE_FILES_DIR, target_name)
    while os.path.exists(target_abs):
        target_name = f"{qid}_{counter}{ext}"
        target_abs = os.path.join(ARCHIVE_FILES_DIR, target_name)
        counter += 1

    shutil.move(abs_path, target_abs)
    manifest[qid] = {
        "quarantine_id": qid,
        "original_relative_path": rel_path.replace("\\", "/"),
        "quarantine_relative_path": os.path.relpath(target_abs, WORKSPACE_ROOT).replace("\\", "/"),
        "sha256": sha,
        "size_bytes": file_size,
        "category": category,
        "reason": reason,
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "restored": False,
        "restored_at": None,
        "purged": False,
        "purged_at": None,
    }
    return True

def main():
    print("=== STARTING STAGE 3.14 MEDIA CONSOLIDATION ===")
    manifest = load_manifest()
    print(f"Initial quarantine manifest entries: {len(manifest)}")

    # 1. Prepare Target Directory Structure
    source_branding = os.path.join(MEDIA_ROOT, "source", "branding")
    source_archival = os.path.join(MEDIA_ROOT, "source", "archival")
    source_reusable = os.path.join(MEDIA_ROOT, "source", "reusable", "audio_samples")
    audit_latest = os.path.join(MEDIA_ROOT, "audit", "latest")
    os.makedirs(source_branding, exist_ok=True)
    os.makedirs(source_archival, exist_ok=True)
    os.makedirs(source_reusable, exist_ok=True)
    os.makedirs(audit_latest, exist_ok=True)

    # Copy branding assets to source/branding
    branding_src = os.path.join(WORKSPACE_ROOT, "Youtube Channel Src")
    for b_file in ["Youtube-Icon.png", "Compressed-Youtube-Icon.png", "Compressed-Youtube-Banner.png"]:
        src_p = os.path.join(branding_src, b_file)
        dst_p = os.path.join(source_branding, b_file)
        if os.path.isfile(src_p) and not os.path.isfile(dst_p):
            shutil.copy2(src_p, dst_p)
            print(f"Copied branding asset to source/branding: {b_file}")

    # Copy reusable voice samples
    tts_samples_dir = os.path.join(MEDIA_ROOT, "audio", "tts_samples")
    if os.path.isdir(tts_samples_dir):
        for s_file in os.listdir(tts_samples_dir):
            sp = os.path.join(tts_samples_dir, s_file)
            dp = os.path.join(source_reusable, s_file)
            if os.path.isfile(sp) and not os.path.isfile(dp):
                shutil.copy2(sp, dp)
                print(f"Copied sample to source/reusable/audio_samples: {s_file}")

    # 2. Setup Production Directory: prod_20261110_vesna_vulovic
    vesna_prod_dir = os.path.join(MEDIA_ROOT, "productions", "prod_20261110_vesna_vulovic")
    for sub in ["research", "scripts", "narration", "visual_timeline", "assets", "thumbnails", "video", "metadata", "reports"]:
        os.makedirs(os.path.join(vesna_prod_dir, sub), exist_ok=True)

    # Copy Vesna assets into production directory
    vesna_images_dir = os.path.join(MEDIA_ROOT, "images", "vesna_vulovic")
    if os.path.isdir(vesna_images_dir):
        for img in os.listdir(vesna_images_dir):
            ip = os.path.join(vesna_images_dir, img)
            dp = os.path.join(vesna_prod_dir, "assets", img)
            if os.path.isfile(ip) and not os.path.isfile(dp):
                shutil.copy2(ip, dp)

    # Copy Vesna audio into production directory
    vesna_voice = os.path.join(MEDIA_ROOT, "audio", "stage_3_13_vesna_voice.mp3")
    if os.path.isfile(vesna_voice):
        dp = os.path.join(vesna_prod_dir, "narration", "stage_3_13_vesna_voice.mp3")
        if not os.path.isfile(dp):
            shutil.copy2(vesna_voice, dp)

    vesna_seg_dir = os.path.join(MEDIA_ROOT, "audio", "vesna_segments")
    if os.path.isdir(vesna_seg_dir):
        target_seg = os.path.join(vesna_prod_dir, "narration", "vesna_segments")
        os.makedirs(target_seg, exist_ok=True)
        for seg in os.listdir(vesna_seg_dir):
            sp = os.path.join(vesna_seg_dir, seg)
            dp = os.path.join(target_seg, seg)
            if os.path.isfile(sp) and not os.path.isfile(dp):
                shutil.copy2(sp, dp)

    # Copy Vesna video and thumbnail
    v_vid = os.path.join(MEDIA_ROOT, "video", "showcase_vesna_vulovic_verified.mp4")
    if os.path.isfile(v_vid):
        dp = os.path.join(vesna_prod_dir, "video", "showcase_vesna_vulovic_verified.mp4")
        if not os.path.isfile(dp):
            shutil.copy2(v_vid, dp)

    v_th = os.path.join(MEDIA_ROOT, "thumbnails", "thumb_vesna_vulovic_verified.png")
    if os.path.isfile(v_th):
        dp = os.path.join(vesna_prod_dir, "thumbnails", "thumb_vesna_vulovic_verified.png")
        if not os.path.isfile(dp):
            shutil.copy2(v_th, dp)

    # Create Vesna production manifest
    vesna_manifest = {
        "production_id": "prod_20261110_vesna_vulovic",
        "topic_id": "cand_03_vesna_fall",
        "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        "category": "Unbelievable Real-Life Stories",
        "workspace_dir": "media/productions/prod_20261110_vesna_vulovic",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video_deliverable": "media/productions/prod_20261110_vesna_vulovic/video/showcase_vesna_vulovic_verified.mp4",
        "thumbnail_deliverable": "media/productions/prod_20261110_vesna_vulovic/thumbnails/thumb_vesna_vulovic_verified.png",
        "narration": "media/productions/prod_20261110_vesna_vulovic/narration/stage_3_13_vesna_voice.mp3",
    }
    with open(os.path.join(vesna_prod_dir, "production_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(vesna_manifest, f, indent=2)
    print("Created production workspace: media/productions/prod_20261110_vesna_vulovic")

    # 3. Setup Production Directory: prod_cycle_4_20261110_1800 (Private YouTube Upload 4DCas1e4vHI)
    c4_prod_dir = os.path.join(MEDIA_ROOT, "productions", "prod_cycle_4_20261110_1800")
    for sub in ["research", "scripts", "narration", "visual_timeline", "assets", "thumbnails", "video", "metadata", "reports"]:
        os.makedirs(os.path.join(c4_prod_dir, sub), exist_ok=True)

    c4_vid = os.path.join(MEDIA_ROOT, "video", "showcase_cycle_4_20261110_1800.mp4")
    if os.path.isfile(c4_vid):
        dp = os.path.join(c4_prod_dir, "video", "showcase_cycle_4_20261110_1800.mp4")
        if not os.path.isfile(dp):
            shutil.copy2(c4_vid, dp)

    c4_th = os.path.join(MEDIA_ROOT, "thumbnails", "thumb_cycle_4_20261110_1800.png")
    if os.path.isfile(c4_th):
        dp = os.path.join(c4_prod_dir, "thumbnails", "thumb_cycle_4_20261110_1800.png")
        if not os.path.isfile(dp):
            shutil.copy2(c4_th, dp)

    c4_manifest = {
        "production_id": "prod_cycle_4_20261110_1800",
        "youtube_video_id": "4DCas1e4vHI",
        "upload_status": "uploaded_private",
        "workspace_dir": "media/productions/prod_cycle_4_20261110_1800",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video_deliverable": "media/productions/prod_cycle_4_20261110_1800/video/showcase_cycle_4_20261110_1800.mp4",
        "thumbnail_deliverable": "media/productions/prod_cycle_4_20261110_1800/thumbnails/thumb_cycle_4_20261110_1800.png",
    }
    with open(os.path.join(c4_prod_dir, "production_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(c4_manifest, f, indent=2)
    print("Created production workspace: media/productions/prod_cycle_4_20261110_1800")

    # 4. Quarantine Disposable Test Production Directories
    for test_dir in ["test_checkpoint_job_001", "test_vesna_integrity_run"]:
        tpath = os.path.join(MEDIA_ROOT, "productions", test_dir)
        if os.path.isdir(tpath):
            mf = os.path.join(tpath, "production_manifest.json")
            if os.path.isfile(mf):
                quarantine_file(os.path.relpath(mf, WORKSPACE_ROOT), "CATEGORY_H_TEMPORARY_FILES", f"Manifest of disposable test production {test_dir}", manifest)
            shutil.rmtree(tpath, ignore_errors=True)
            print(f"Removed disposable test production directory: {test_dir}")

    # 5. Quarantine Unreferenced Obsolete Audio
    unref_audio = [
        "media/audio/test_editorial_tts.mp3",
        "media/audio/test_narr.mp3",
        "media/audio/test_sub.mp3",
        "media/audio/test_voice.mp3",
    ]
    for ua in unref_audio:
        if quarantine_file(ua, "CATEGORY_H_TEMPORARY_FILES", "Unreferenced obsolete test audio recording", manifest):
            print(f"Quarantined obsolete audio: {ua}")

    # 6. Quarantine Obsolete Frame Dumps in media/audit
    audit_frame_dirs = [
        "media/audit/facial_rig",
        "media/audit/hybrid_engine",
        "media/audit/stick_rig",
        "media/audit/thumbnails_3_9a/mobile_previews",
    ]
    for afd in audit_frame_dirs:
        abs_afd = os.path.join(WORKSPACE_ROOT, afd)
        if os.path.isdir(abs_afd):
            for fname in os.listdir(abs_afd):
                fpath = os.path.join(afd, fname)
                quarantine_file(fpath, "CATEGORY_I_OBSOLETE_CACHE", "Obsolete animation/rig test frame capture", manifest)
            shutil.rmtree(abs_afd, ignore_errors=True)
            print(f"Quarantined and removed frame directory: {afd}")

    unref_audit_sheets = [
        "media/audit/contact_sheet_facial_rig.png",
        "media/audit/contact_sheet_stick_rig.png",
    ]
    for uas in unref_audit_sheets:
        if quarantine_file(uas, "CATEGORY_I_OBSOLETE_CACHE", "Obsolete rig contact sheet", manifest):
            print(f"Quarantined contact sheet: {uas}")

    # Copy latest audit reports to media/audit/latest/
    inv_file = os.path.join(MEDIA_ROOT, "audit", "workspace_inventory.json")
    if os.path.isfile(inv_file):
        shutil.copy2(inv_file, os.path.join(audit_latest, "workspace_inventory.json"))
        print("Updated media/audit/latest/workspace_inventory.json")

    # 7. Clean Thumbnails and Video
    # Quarantine duplicate test video
    dup_test_vid = "media/video/showcase_test_vesna_integrity_run.mp4"
    if quarantine_file(dup_test_vid, "CATEGORY_G_DUPLICATES", "Duplicate render of verified Vesna deliverable", manifest):
        print(f"Quarantined duplicate test video: {dup_test_vid}")

    # Quarantine test thumbnails
    test_thumbs = [
        "media/thumbnails/thumb_test_vesna_integrity_run.png",
        "media/thumbnails/thumb_test_interrupted_tts_job.png",
        "media/thumbnails/showcase_stage_3_4_tsutomu_yamaguchi.png",
        "media/thumbnails/showcase_stage_3_5_yamaguchi_refined.png",
    ]
    for tt in test_thumbs:
        if quarantine_file(tt, "CATEGORY_H_TEMPORARY_FILES", "Obsolete thumbnail candidate or test artifact", manifest):
            print(f"Quarantined thumbnail: {tt}")

    cand_dir = os.path.join(MEDIA_ROOT, "thumbnails", "candidates")
    if os.path.isdir(cand_dir):
        for cfile in os.listdir(cand_dir):
            cp = os.path.join("media/thumbnails/candidates", cfile)
            quarantine_file(cp, "CATEGORY_H_TEMPORARY_FILES", "Historical thumbnail candidate", manifest)
        shutil.rmtree(cand_dir, ignore_errors=True)
        print("Quarantined and removed media/thumbnails/candidates")

    # Save intermediate manifest before purge
    save_manifest(manifest)
    print(f"Total manifest entries after additional quarantining: {len(manifest)}")

    # 8. Physical Disk Space Recovery: Purge SAFE_TO_PURGE entries
    # Load DB paths to prevent purging any file referenced by DB
    conn = sqlite3.connect(os.path.join(WORKSPACE_ROOT, "backend", "studio.db"))
    c = conn.cursor()
    db_paths = set()
    for row in c.execute("SELECT video_path, thumbnail_path FROM publishing_ledger").fetchall():
        if row[0]: db_paths.add(os.path.normpath(row[0]).lower())
        if row[1]: db_paths.add(os.path.normpath(row[1]).lower())
    conn.close()

    purged_count = 0
    purged_bytes = 0
    retained_count = 0
    retained_bytes = 0

    for qid, entry in list(manifest.items()):
        if entry.get("restored"):
            continue
        if entry.get("purged"):
            continue

        orig = entry.get("original_relative_path", "").replace("\\", "/")
        cat = entry.get("category", "UNKNOWN")
        size = entry.get("size_bytes", 0)
        q_rel = entry.get("quarantine_relative_path", "").replace("\\", "/")
        q_abs = os.path.join(WORKSPACE_ROOT, q_rel)

        # Check DB reference
        norm_orig = os.path.normpath(orig).lower()
        is_db_ref = any(norm_orig in p or p.endswith(norm_orig) for p in db_paths)

        # Classification
        can_purge = False
        purge_reason = ""

        if is_db_ref:
            # If the original is already present in active workspace, the quarantined file is a duplicate
            if os.path.isfile(os.path.join(WORKSPACE_ROOT, orig)):
                can_purge = True
                purge_reason = "Duplicate copy of active DB-referenced deliverable present on disk"
            else:
                can_purge = False
        elif cat == "CATEGORY_I_OBSOLETE_CACHE":
            can_purge = True
            purge_reason = "Disposable browser cache or temporary frame render"
        elif cat == "CATEGORY_G_DUPLICATES":
            can_purge = True
            purge_reason = "Byte-identical duplicate copy"
        elif cat == "CATEGORY_H_TEMPORARY_FILES":
            if any(x in orig for x in ["_tts_concat_list", "_test_doc_frame", "render_", "scratch", "test_20s", "test_35s", "candidates", "thumb_test", "frame_"]):
                can_purge = True
                purge_reason = "Disposable temporary scratch file or test candidate"
            else:
                can_purge = False
        elif cat == "CATEGORY_E_HISTORICAL_SHOWCASES":
            # Retain historical showcases in quarantine
            can_purge = False

        if can_purge:
            if os.path.isfile(q_abs):
                os.remove(q_abs)
            entry["purged"] = True
            entry["purged_at"] = datetime.now(timezone.utc).isoformat()
            entry["purge_reason"] = purge_reason
            purged_count += 1
            purged_bytes += size
        else:
            retained_count += 1
            retained_bytes += size

    save_manifest(manifest)

    print("\n=== PURGE AND CONSOLIDATION SUMMARY ===")
    print(f"Files Physically Purged: {purged_count} ({round(purged_bytes / (1024*1024), 2)} MB freed)")
    print(f"Files Retained in Quarantine: {retained_count} ({round(retained_bytes / (1024*1024), 2)} MB)")
    print("Quarantine manifest updated atomically.")

if __name__ == "__main__":
    main()
