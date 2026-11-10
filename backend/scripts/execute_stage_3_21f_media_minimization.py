"""
Stage 3.21F: Final Workspace Media Minimization Script.
Permanently deletes obsolete production assets, historical research images,
reusable audio samples, narration benchmarks, telemetry files, and subtitle templates.
Preserves strictly and exclusively the 4 official YouTube branding assets.
"""

import os
import sys
import json
import hashlib
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(r"D:\Agents\CrazzyDocs")
MEDIA_ROOT = WORKSPACE / "media"
MANIFEST_PATH = WORKSPACE / "docs" / "STAGE_3_21F_DELETED_MEDIA_MANIFEST.json"

PRESERVED_ASSETS = {
    "media/source/branding/Compressed-Youtube-Banner.png",
    "media/source/branding/Compressed-Youtube-Icon.png",
    "media/source/branding/Youtube-Icon.png",
    "media/source/branding/crazzydocs_outro_1080x1920.mp4",
}

def sha256_file(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def main():
    print("=================================================================")
    print("STAGE 3.21F — FINAL MEDIA WORKSPACE MINIMIZATION")
    print("=================================================================")

    # 1. Scan current media workspace
    all_disk_files = {}
    for root, _, fs in os.walk(MEDIA_ROOT):
        for f in fs:
            fp = Path(root) / f
            rel = ("media/" + str(fp.relative_to(MEDIA_ROOT))).replace("\\", "/")
            sz = fp.stat().st_size
            sha = sha256_file(fp)
            all_disk_files[rel] = {
                "rel_path": rel,
                "abs_path": str(fp),
                "size_bytes": sz,
                "sha256": sha
            }

    initial_count = len(all_disk_files)
    initial_bytes = sum(x["size_bytes"] for x in all_disk_files.values())
    print(f"Initial files in media/: {initial_count}")
    print(f"Initial storage in media/: {initial_bytes} bytes ({initial_bytes / (1024*1024):.2f} MB)")

    # 2. Verify all preserved assets exist
    for p in PRESERVED_ASSETS:
        assert p in all_disk_files, f"CRITICAL ERROR: Preserved asset missing: {p}"
    print(f"Verified {len(PRESERVED_ASSETS)} preserved official branding assets.")

    # 3. Categorize candidates for permanent deletion
    candidates_to_delete = []
    retained_assets = []

    for rel, info in all_disk_files.items():
        if rel in PRESERVED_ASSETS:
            retained_assets.append(info)
            continue

        # Classify reason
        p_lower = rel.lower()
        if "prod_20261110_vesna_vulovic" in p_lower:
            reason = "Vesna Vulovic production assets associated with permanently deleted YouTube Short 4DCas1e4vHI"
        elif "prod_cycle_4_20261110_1800" in p_lower:
            reason = "Obsolete Cycle 4 showcase production deliverable; no active workflow or test dependency"
        elif "images/historical" in p_lower:
            reason = "Great Molasses Flood historical research download; can be re-acquired on demand"
        elif "images/yamaguchi" in p_lower:
            reason = "Tsutomu Yamaguchi Wikimedia Commons historical research download; can be re-acquired on demand"
        elif "source/reusable/audio_samples" in p_lower:
            reason = "Reusable reference audio sample; dynamically reproducible via TTS service"
        elif "media/audio" in p_lower:
            reason = "Obsolete narration benchmark recording or telemetry JSON; can be regenerated"
        elif "media/subtitles" in p_lower:
            reason = "Obsolete subtitle visual style template; dynamically reproducible via editorial subtitle engine"
        else:
            reason = "Obsolete unreferenced media asset"

        candidates_to_delete.append({
            "rel_path": rel,
            "abs_path": info["abs_path"],
            "size_bytes": info["size_bytes"],
            "sha256": info["sha256"],
            "deletion_reason": reason
        })

    print(f"\nCandidates to delete: {len(candidates_to_delete)} files")
    del_bytes = sum(x["size_bytes"] for x in candidates_to_delete)
    print(f"Bytes to permanently delete: {del_bytes} bytes ({del_bytes / (1024*1024):.2f} MB)")

    print(f"Assets to retain: {len(retained_assets)} files")
    ret_bytes = sum(x["size_bytes"] for x in retained_assets)
    print(f"Bytes to retain: {ret_bytes} bytes ({ret_bytes / (1024*1024):.2f} MB)")

    assert len(candidates_to_delete) + len(retained_assets) == initial_count
    assert del_bytes + ret_bytes == initial_bytes

    # 4. Execute permanent deletion
    print("\n--- Executing Permanent Deletion ---")
    deleted_records = []
    for item in candidates_to_delete:
        abs_p = item["abs_path"]
        rel_p = item["rel_path"]
        assert rel_p not in PRESERVED_ASSETS, f"SAFETY VIOLATION: Preserved asset in deletion queue: {rel_p}"
        assert os.path.exists(abs_p), f"File missing prior to deletion: {abs_p}"

        os.remove(abs_p)
        deleted_records.append({
            "path": rel_p,
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
            "reason": item["deletion_reason"],
            "deleted_at": datetime.now().isoformat()
        })

    print(f"Permanently deleted {len(deleted_records)} files.")

    # 5. Remove empty directories
    cleaned_dirs = []
    for root, dirs, files in os.walk(MEDIA_ROOT, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    cleaned_dirs.append(str(dir_path.relative_to(WORKSPACE)).replace("\\", "/"))
            except Exception:
                pass
    print(f"Removed {len(cleaned_dirs)} empty directories.")

    # 6. Post-cleanup re-scan & verification
    post_disk_files = {}
    for root, _, fs in os.walk(MEDIA_ROOT):
        for f in fs:
            fp = Path(root) / f
            rel = ("media/" + str(fp.relative_to(MEDIA_ROOT))).replace("\\", "/")
            post_disk_files[rel] = {
                "rel_path": rel,
                "size_bytes": fp.stat().st_size,
                "sha256": sha256_file(fp)
            }

    post_count = len(post_disk_files)
    post_bytes = sum(x["size_bytes"] for x in post_disk_files.values())
    print("\n--- Post-Minimization Workspace State ---")
    print(f"Remaining files in media/: {post_count}")
    print(f"Remaining storage in media/: {post_bytes} bytes ({post_bytes / (1024*1024):.2f} MB)")
    print(f"Actual storage freed: {del_bytes} bytes ({del_bytes / (1024*1024):.2f} MB)")

    assert post_count == len(PRESERVED_ASSETS), f"Expected {len(PRESERVED_ASSETS)} files, found {post_count}"
    assert set(post_disk_files.keys()) == PRESERVED_ASSETS, "Mismatch in preserved assets!"
    assert post_bytes == ret_bytes, f"Bytes mismatch: expected {ret_bytes}, got {post_bytes}"

    print("SUCCESS: Media workspace minimized to exactly 4 preserved branding assets!")

    # 7. Write manifest
    manifest_payload = {
        "minimization_timestamp": datetime.now().isoformat(),
        "initial_files_count": initial_count,
        "initial_storage_bytes": initial_bytes,
        "initial_storage_mb": round(initial_bytes / (1024 * 1024), 4),
        "deleted_files_count": len(deleted_records),
        "deleted_storage_bytes": del_bytes,
        "deleted_storage_mb": round(del_bytes / (1024 * 1024), 4),
        "retained_files_count": post_count,
        "retained_storage_bytes": post_bytes,
        "retained_storage_mb": round(post_bytes / (1024 * 1024), 4),
        "empty_directories_removed": cleaned_dirs,
        "retained_assets": [
            {
                "path": r["rel_path"],
                "size_bytes": r["size_bytes"],
                "sha256": r["sha256"]
            }
            for r in retained_assets
        ],
        "deleted_files": deleted_records
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)
    print(f"Wrote minimization manifest to {MANIFEST_PATH}")

if __name__ == "__main__":
    main()
