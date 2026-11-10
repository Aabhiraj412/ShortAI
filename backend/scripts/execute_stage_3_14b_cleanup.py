"""
Stage 3.14B Final Visual Asset Cleanup Execution Script.

Performs:
1. Removal of redundant root-level video and thumbnail copies:
   - media/video/showcase_vesna_vulovic_verified.mp4
   - media/thumbnails/thumb_vesna_vulovic_verified.png
   - media/video/showcase_cycle_4_20261110_1800.mp4
   - media/thumbnails/thumb_cycle_4_20261110_1800.png
   - media/video/showcase_test_vesna_integrity_run.mp4
   - media/thumbnails/thumb_test_vesna_integrity_run.png
   Ensures canonical copies exist in media/productions/ before removal.
   Preserves baseline fixtures (showcase_stage_3_4_tsutomu_yamaguchi.mp4, showcase_stage_3_9a_yamaguchi_poster.png).

2. Permanent deletion of unwanted character assets:
   - assets/characters/ (16 files)
   - archive/legacy_assets/characters/ (28 files)
   - archive/experimental_prototypes/animation-engine/src/characters/ (2 files)
   - thumbnails/output/ (2 files)
   - media/audit/contact_sheet_hybrid_engine.png
   - media/audit/hybrid_telemetry.json

3. Quarantine character experiment purge:
   - media/quarantine/archived_files/q_1794327088_a72b7f99.mp4
   - media/quarantine/archived_files/q_1794327088_6d72fcb4.mp4
   - media/quarantine/archived_files/q_1794327088_88cb0c6d.mp4
   - Update media/quarantine/manifest.json with purged=True and purge_reason
"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
LOGS = []

def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def log(msg):
    print(msg)
    LOGS.append(msg)

def remove_file_with_check(file_path, description):
    if os.path.isfile(file_path):
        sz = os.path.getsize(file_path)
        os.remove(file_path)
        log(f"[DELETED FILE] {description}: {os.path.relpath(file_path, WORKSPACE_ROOT)} ({sz} bytes reclaimed)")
        return sz
    else:
        log(f"[SKIP] Not found: {os.path.relpath(file_path, WORKSPACE_ROOT)}")
        return 0

def remove_dir_with_check(dir_path, description):
    if os.path.isdir(dir_path):
        total_sz = 0
        file_count = 0
        for root, dirs, files in os.walk(dir_path):
            for f in files:
                fp = os.path.join(root, f)
                total_sz += os.path.getsize(fp)
                file_count += 1
        shutil.rmtree(dir_path)
        log(f"[DELETED DIRECTORY] {description}: {os.path.relpath(dir_path, WORKSPACE_ROOT)} ({file_count} files, {total_sz} bytes reclaimed)")
        return total_sz, file_count
    else:
        log(f"[SKIP] Directory not found: {os.path.relpath(dir_path, WORKSPACE_ROOT)}")
        return 0, 0

def main():
    log("=" * 80)
    log("STAGE 3.14B: FINAL VISUAL ASSET CLEANUP")
    log("=" * 80)

    total_bytes_reclaimed = 0

    # -------------------------------------------------------------
    # 1. VERIFY CANONICAL COPIES & DELETE REDUNDANT ROOT DELIVERABLES
    # -------------------------------------------------------------
    log("\n--- SECTION 1: REDUNDANT ROOT-LEVEL DELIVERABLES ---")
    redundant_pairs = [
        (
            os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_vesna_vulovic_verified.mp4"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_20261110_vesna_vulovic", "video", "showcase_vesna_vulovic_verified.mp4"),
            "Redundant root Vesna video"
        ),
        (
            os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "thumb_vesna_vulovic_verified.png"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_20261110_vesna_vulovic", "thumbnails", "thumb_vesna_vulovic_verified.png"),
            "Redundant root Vesna thumbnail"
        ),
        (
            os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_cycle_4_20261110_1800.mp4"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4"),
            "Redundant root Cycle 4 video"
        ),
        (
            os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "thumb_cycle_4_20261110_1800.png"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_cycle_4_20261110_1800", "thumbnails", "thumb_cycle_4_20261110_1800.png"),
            "Redundant root Cycle 4 thumbnail"
        ),
        (
            os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_test_vesna_integrity_run.mp4"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "test_vesna_integrity_run", "video", "showcase_test_vesna_integrity_run.mp4"),
            "Redundant root test integrity video"
        ),
        (
            os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "thumb_test_vesna_integrity_run.png"),
            os.path.join(WORKSPACE_ROOT, "media", "productions", "test_vesna_integrity_run", "thumbnails", "thumb_test_vesna_integrity_run.png"),
            "Redundant root test integrity thumbnail"
        ),
    ]

    for root_p, can_p, desc in redundant_pairs:
        if os.path.isfile(root_p):
            assert os.path.isfile(can_p), f"ABORT: Canonical file missing for {root_p} -> {can_p}"
            root_hash = compute_sha256(root_p)
            can_hash = compute_sha256(can_p)
            assert root_hash == can_hash, f"ABORT: Hash mismatch for {root_p} vs {can_p}"
            sz = remove_file_with_check(root_p, desc)
            total_bytes_reclaimed += sz
        else:
            log(f"[ALREADY REMOVED] {desc}: {os.path.relpath(root_p, WORKSPACE_ROOT)}")

    # Verify protected fixtures remain intact
    baseline_video = os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
    baseline_thumb = os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "showcase_stage_3_9a_yamaguchi_poster.png")
    assert os.path.isfile(baseline_video), f"CRITICAL: Baseline video missing: {baseline_video}"
    assert os.path.isfile(baseline_thumb), f"CRITICAL: Baseline thumbnail missing: {baseline_thumb}"
    log(f"[PROTECTED INTACT] Historical baseline video: {os.path.relpath(baseline_video, WORKSPACE_ROOT)} ({os.path.getsize(baseline_video)} bytes)")
    log(f"[PROTECTED INTACT] Historical baseline thumbnail: {os.path.relpath(baseline_thumb, WORKSPACE_ROOT)} ({os.path.getsize(baseline_thumb)} bytes)")

    # -------------------------------------------------------------
    # 2. PERMANENTLY DELETE UNWANTED CHARACTER ARTWORK & ASSETS
    # -------------------------------------------------------------
    log("\n--- SECTION 2: PERMANENT DELETION OF UNWANTED CHARACTER ASSETS ---")
    
    # 2a. assets/characters
    sz, cnt = remove_dir_with_check(os.path.join(WORKSPACE_ROOT, "assets", "characters"), "Character artwork directory")
    total_bytes_reclaimed += sz

    # 2b. archive/legacy_assets/characters
    sz, cnt = remove_dir_with_check(os.path.join(WORKSPACE_ROOT, "archive", "legacy_assets", "characters"), "Legacy character assets directory")
    total_bytes_reclaimed += sz

    # 2c. archive/experimental_prototypes/animation-engine/src/characters
    sz, cnt = remove_dir_with_check(os.path.join(WORKSPACE_ROOT, "archive", "experimental_prototypes", "animation-engine", "src", "characters"), "Character experiment source files")
    total_bytes_reclaimed += sz

    # 2d. thumbnails/output/
    stick_thumb = os.path.join(WORKSPACE_ROOT, "thumbnails", "output", "stick_sample_thumb.png")
    short_thumb = os.path.join(WORKSPACE_ROOT, "thumbnails", "output", "sample_short_thumb.png")
    sz = remove_file_with_check(stick_thumb, "Stick thumbnail sample")
    total_bytes_reclaimed += sz
    sz = remove_file_with_check(short_thumb, "Sample short thumbnail")
    total_bytes_reclaimed += sz

    # 2e. media/audit hybrid character files
    hybrid_sheet = os.path.join(WORKSPACE_ROOT, "media", "audit", "contact_sheet_hybrid_engine.png")
    hybrid_telem = os.path.join(WORKSPACE_ROOT, "media", "audit", "hybrid_telemetry.json")
    sz = remove_file_with_check(hybrid_sheet, "Hybrid engine contact sheet")
    total_bytes_reclaimed += sz
    sz = remove_file_with_check(hybrid_telem, "Hybrid telemetry json")
    total_bytes_reclaimed += sz

    # -------------------------------------------------------------
    # 3. QUARANTINE PURGE OF CHARACTER EXPERIMENTS
    # -------------------------------------------------------------
    log("\n--- SECTION 3: QUARANTINE PURGE OF CHARACTER EXPERIMENTS ---")
    manifest_path = os.path.join(WORKSPACE_ROOT, "media", "quarantine", "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    archived_dir = os.path.join(WORKSPACE_ROOT, "media", "quarantine", "archived_files")
    quarantine_char_ids = [
        ("q_1794327088_a72b7f99", "q_1794327088_a72b7f99.mp4", "media/video/hybrid_benchmark_short.mp4"),
        ("q_1794327088_6d72fcb4", "q_1794327088_6d72fcb4.mp4", "media/video/stick_sample_short.mp4"),
        ("q_1794327088_88cb0c6d", "q_1794327088_88cb0c6d.mp4", "media/video/stick_test_1sec.mp4"),
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    purged_count = 0
    for qid, stored_fn, orig_rel in quarantine_char_ids:
        stored_path = os.path.join(archived_dir, stored_fn)
        if os.path.isfile(stored_path):
            sz = os.path.getsize(stored_path)
            os.remove(stored_path)
            total_bytes_reclaimed += sz
            purged_count += 1
            log(f"[PURGED FROM QUARANTINE] {stored_fn} ({orig_rel}, {sz} bytes reclaimed)")
        else:
            log(f"[QUARANTINE FILE ALREADY ABSENT] {stored_fn}")

        if qid in manifest:
            manifest[qid]["purged"] = True
            manifest[qid]["purged_at"] = now_iso
            manifest[qid]["purge_reason"] = "User-directed permanent deletion of character artwork/experiments (Stage 3.14B)"

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    log(f"[MANIFEST UPDATED] Saved updated quarantine manifest with {purged_count} purged entries.")

    mb_reclaimed = total_bytes_reclaimed / (1024 * 1024)
    log("\n" + "=" * 80)
    log(f"STAGE 3.14B CLEANUP COMPLETED: Reclaimed {total_bytes_reclaimed:,} bytes ({mb_reclaimed:.2f} MB)")
    log("=" * 80)

if __name__ == "__main__":
    main()
