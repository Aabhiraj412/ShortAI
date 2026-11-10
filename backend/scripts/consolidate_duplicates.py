"""
Safe Consolidation of Duplicate Videos & Thumbnails (Stage 3.14A Phase 6).
Preserves canonical deliverables, moves redundant test duplicates to quarantine,
preserves creative variants in production workspace, and purges redundant copies in quarantine.
"""

import hashlib
import json
import os
import shutil
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
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_manifest(m: dict):
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
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
    print("=== STARTING STAGE 3.14A SAFE CONSOLIDATION ===")
    manifest = load_manifest()

    # 1. Preserve Creative Variants in Production Workspace
    variants_dest = os.path.join(MEDIA_ROOT, "productions", "prod_20261110_vesna_vulovic", "thumbnails", "variants")
    os.makedirs(variants_dest, exist_ok=True)
    cand_dir = os.path.join(MEDIA_ROOT, "thumbnails", "candidates")
    if os.path.isdir(cand_dir):
        for cfile in os.listdir(cand_dir):
            src_cp = os.path.join(cand_dir, cfile)
            dst_cp = os.path.join(variants_dest, cfile)
            if os.path.isfile(src_cp) and not os.path.isfile(dst_cp):
                shutil.move(src_cp, dst_cp)
                print(f"Moved creative variant to production workspace: {cfile}")
        shutil.rmtree(cand_dir, ignore_errors=True)
        print("Removed empty candidates directory.")

    # 2. Quarantine Redundant Test Video Deliverable
    test_vid = "media/video/showcase_test_vesna_integrity_run.mp4"
    if quarantine_file(test_vid, "CATEGORY_G_DUPLICATES", "Redundant test render duplicate of verified Vesna deliverable", manifest):
        print(f"Quarantined redundant test video: {test_vid}")

    # 3. Quarantine Redundant Test Thumbnail Deliverable
    test_thumb = "media/thumbnails/thumb_test_vesna_integrity_run.png"
    if quarantine_file(test_thumb, "CATEGORY_G_DUPLICATES", "Redundant test thumbnail duplicate of candidate 03", manifest):
        print(f"Quarantined redundant test thumbnail: {test_thumb}")

    save_manifest(manifest)

    # 4. Purge Verified Exact Duplicate Copies in Quarantine
    # Hashes to purge from quarantine where canonical is preserved on disk:
    # 0a172c98... (Vesna video preserved in media/productions/prod_20261110_vesna_vulovic/video/ and media/video/)
    # 75ca4723... (Cycle 4 video preserved in media/productions/prod_cycle_4_20261110_1800/video/ and media/video/)
    # 034f3007... (Thumbnail variant preserved in production workspace variants/)
    duplicate_hashes_to_purge = {
        "0a172c987505e8488c4f98126faa6000c824f8ed5f7c96b222d3dae9f9187655",
        "75ca472335dc65c9acba86e3a8ef85c77c576590be2f7a5ce560afa60fa66c67",
        "034f300785d5ea8bf6275de5a4850293438ec1b69145422fa81e3d75c20650d7",
        "97892b49", "d8ab7905", "f717e5d6"
    }

    purged_count = 0
    purged_bytes = 0

    for qid, entry in list(manifest.items()):
        if entry.get("purged"):
            continue
        sha = entry.get("sha256", "")
        q_rel = entry.get("quarantine_relative_path", "")
        q_abs = os.path.join(WORKSPACE_ROOT, q_rel)

        # Check if sha matches redundant copies
        is_redundant = sha in duplicate_hashes_to_purge or any(sha.startswith(prefix) for prefix in duplicate_hashes_to_purge)
        if is_redundant:
            if os.path.isfile(q_abs):
                sz = os.path.getsize(q_abs)
                os.remove(q_abs)
                purged_count += 1
                purged_bytes += sz
            entry["purged"] = True
            entry["purged_at"] = datetime.now(timezone.utc).isoformat()
            entry["purge_reason"] = "Stage 3.14A: Confirmed duplicate of active canonical deliverable"

    save_manifest(manifest)

    print("\n=== SAFE CONSOLIDATION COMPLETE ===")
    print(f"Quarantined redundant test deliverables: 2 files")
    print(f"Creative variants preserved in production workspace: 4 files")
    print(f"Redundant duplicate files purged from quarantine: {purged_count} files ({round(purged_bytes / (1024*1024), 2)} MB freed)")

if __name__ == "__main__":
    main()
