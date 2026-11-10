"""
Stage 3.21D-R3 — Final Duplicate Byte Reconciliation Script.
Reads every file directly from disk, verifies byte counts, duplicate group assignments,
dependency classes, scenario memberships, and generates:
- docs/STAGE_3_21D_FINAL_CLEANUP_PLAN_VERIFIED.json
"""
import os
import sys
import json
import hashlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict

WORKSPACE_ROOT = Path(r"D:\Agents\CrazzyDocs")
MEDIA_ROOT = WORKSPACE_ROOT / "media"
FINAL_PLAN_JSON = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_FINAL_CLEANUP_PLAN.json"
VERIFIED_OUTPUT_JSON = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_FINAL_CLEANUP_PLAN_VERIFIED.json"

def sha256_file(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def main():
    print("--- 1. Scanning media directly from disk ---")
    disk_files = {}
    hash_to_paths = defaultdict(list)
    
    for root, _, fs in os.walk(MEDIA_ROOT):
        for f in fs:
            fp = Path(root) / f
            rel = str(fp.relative_to(MEDIA_ROOT)).replace("\\", "/")
            full_rel = f"media/{rel}"
            sz = fp.stat().st_size
            sha = sha256_file(fp)
            disk_files[full_rel] = {
                "path": full_rel,
                "size_bytes": sz,
                "sha256": sha
            }
            hash_to_paths[sha].append(full_rel)

    print(f"Total physical files on disk: {len(disk_files)}")
    total_disk_bytes = sum(x["size_bytes"] for x in disk_files.values())
    print(f"Total physical bytes on disk: {total_disk_bytes} ({total_disk_bytes / (1024*1024):.4f} MiB)")

    # Identify 41 duplicate groups and assign group IDs
    dup_groups = {}
    group_idx = 1
    
    # Sort groups by redundant size descending
    sorted_dup_hashes = sorted(
        [h for h, paths in hash_to_paths.items() if len(paths) > 1],
        key=lambda h: disk_files[hash_to_paths[h][0]]["size_bytes"] * (len(hash_to_paths[h]) - 1),
        reverse=True
    )
    
    for h in sorted_dup_hashes:
        paths = hash_to_paths[h]
        sz = disk_files[paths[0]]["size_bytes"]
        gid = f"DUP_{group_idx:02d}"
        group_idx += 1
        
        # Determine canonical copy
        def sort_key(cp):
            if "source/branding" in cp: return 0
            if "source/reusable" in cp: return 1
            if "prod_20261110_vesna_vulovic" in cp: return 2
            if "prod_cycle_4_20261110_1800" in cp: return 3
            if "thumbnails/candidates" in cp: return 4
            if "audit/thumbnails_3_9a" in cp: return 5
            if "media/audit/workspace_inventory.json" == cp: return 6
            if "media/video/" in cp: return 7
            return 10
            
        canonical = sorted(paths, key=sort_key)[0]
        redundant_copies = [p for p in paths if p != canonical]
        
        dup_groups[gid] = {
            "group_id": gid,
            "sha256": h,
            "canonical_path": canonical,
            "redundant_paths": redundant_copies,
            "individual_size_bytes": sz,
            "copy_count": len(paths),
            "redundant_copies_count": len(redundant_copies),
            "total_group_bytes": sz * len(paths),
            "redundant_bytes": sz * len(redundant_copies),
        }

    print(f"Total duplicate groups: {len(dup_groups)}")
    total_redundant_files_count = sum(g["redundant_copies_count"] for g in dup_groups.values())
    total_redundant_bytes = sum(g["redundant_bytes"] for g in dup_groups.values())
    print(f"Total redundant duplicate files: {total_redundant_files_count}")
    print(f"Total redundant bytes: {total_redundant_bytes} ({total_redundant_bytes / (1024*1024):.4f} MiB)")

    # Read previous final plan to map action recommendations (Scenario B vs D)
    with open(FINAL_PLAN_JSON, "r", encoding="utf-8") as f:
        prev_plan = json.load(f)
    prev_by_path = {x["path"]: x for x in prev_plan["files"]}

    # Classify each of the 66 redundant files into Class A, B, or C
    # Dependency rules:
    # Class B: 5 specific files with active code/test imports
    class_b_paths = {
        "media/audio/stage_3_13_vesna_voice.mp3",
        "media/images/vesna_vulovic/attribution_manifest.json",
        "media/thumbnails/showcase_stage_3_5_yamaguchi_refined.png",
        "media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png",
        "media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4"
    }
    # Class C: 8 manifest-bound files inside test run bundles
    class_c_paths = {
        "media/productions/prod_stage_3_16_verification/thumbnails/thumb_prod_stage_3_16_verification.png",
        "media/productions/test_auto_job_001/thumbnails/thumb_test_auto_job_001.png",
        "media/productions/test_auto_job_001/video/showcase_test_auto_job_001.mp4",
        "media/productions/test_scen_11_corrupt_cp/thumbnails/thumb_test_scen_11_corrupt_cp.png",
        "media/productions/test_scen_11_corrupt_cp/video/showcase_test_scen_11_corrupt_cp.mp4",
        "media/productions/test_vesna_integrity_run/thumbnails/thumb_test_vesna_integrity_run.png",
        "media/productions/test_vesna_integrity_run/video/showcase_test_vesna_integrity_run.mp4",
        "media/quarantine/archived_files/q_1794327087_6f866b27.json"
    }

    reconciled_redundant_files = []
    class_a_bytes = 0
    class_b_bytes = 0
    class_c_bytes = 0
    
    class_a_count = 0
    class_b_count = 0
    class_c_count = 0

    for gid, g in dup_groups.items():
        canon = g["canonical_path"]
        sha = g["sha256"]
        sz = g["individual_size_bytes"]
        
        for red_path in g["redundant_paths"]:
            prev_info = prev_by_path.get(red_path, {})
            action = prev_info.get("action_recommendation", "UNKNOWN")
            
            # Determine Scenario membership
            if action == "SAFE_TO_ARCHIVE":
                scen_membership = "Scenario B (SAFE_TO_ARCHIVE)"
            elif action == "POSSIBLY_DISPOSABLE":
                scen_membership = "Scenario D (POSSIBLY_DISPOSABLE)"
            else:
                scen_membership = "Scenario KEEP"

            if red_path in class_b_paths:
                dep_class = "Class B (Path dependency must be updated first)"
                class_b_bytes += sz
                class_b_count += 1
            elif red_path in class_c_paths:
                dep_class = "Class C (Keep for reproducibility / folder manifest)"
                class_c_bytes += sz
                class_c_count += 1
            else:
                dep_class = "Class A (Safe to remove after backup)"
                class_a_bytes += sz
                class_a_count += 1

            reconciled_redundant_files.append({
                "group_id": gid,
                "relative_path": red_path,
                "size_bytes": sz,
                "size_kib": round(sz / 1024, 2),
                "size_mib": round(sz / (1024 * 1024), 4),
                "sha256": sha,
                "canonical_path": canon,
                "dependency_class": dep_class,
                "scenario_membership": scen_membership
            })

    # Assert exactly 66 files
    assert len(reconciled_redundant_files) == 66
    assert class_a_count == 53
    assert class_b_count == 5
    assert class_c_count == 8
    
    sum_classes_bytes = class_a_bytes + class_b_bytes + class_c_bytes
    print("\n--- 2. Exact Dependency Classification Byte Totals ---")
    print(f"Class A (53 files): {class_a_bytes:10} bytes ({class_a_bytes / (1024*1024):.4f} MiB, {class_a_bytes / 1e6:.4f} Decimal MB)")
    print(f"Class B ( 5 files): {class_b_bytes:10} bytes ({class_b_bytes / (1024*1024):.4f} MiB, {class_b_bytes / 1e6:.4f} Decimal MB)")
    print(f"Class C ( 8 files): {class_c_bytes:10} bytes ({class_c_bytes / (1024*1024):.4f} MiB, {class_c_bytes / 1e6:.4f} Decimal MB)")
    print(f"Sum of Classes   : {sum_classes_bytes:10} bytes ({sum_classes_bytes / (1024*1024):.4f} MiB, {sum_classes_bytes / 1e6:.4f} Decimal MB)")
    print(f"Total Redundant  : {total_redundant_bytes:10} bytes ({total_redundant_bytes / (1024*1024):.4f} MiB, {total_redundant_bytes / 1e6:.4f} Decimal MB)")
    assert sum_classes_bytes == total_redundant_bytes

    # Recalculate Storage Scenarios with exact byte precision
    print("\n--- 3. Exact Storage Scenarios Recalculation ---")
    # All 324 files
    all_files_enriched = []
    scen_b_bytes = 0
    scen_d_bytes = 0
    keep_bytes = 0
    
    scen_b_count = 0
    scen_d_count = 0
    keep_count = 0

    for p, d_info in disk_files.items():
        prev_info = prev_by_path.get(p, {})
        action = prev_info.get("action_recommendation", "UNKNOWN")
        sz = d_info["size_bytes"]
        
        if action == "SAFE_TO_ARCHIVE":
            scen_b_bytes += sz
            scen_b_count += 1
        elif action == "POSSIBLY_DISPOSABLE":
            scen_d_bytes += sz
            scen_d_count += 1
        elif action == "KEEP":
            keep_bytes += sz
            keep_count += 1

    scen_e_bytes = scen_b_bytes + scen_d_bytes
    scen_e_count = scen_b_count + scen_d_count
    
    print(f"Total disk files : {len(disk_files)} files, {total_disk_bytes} bytes")
    print(f"Scenario B (SAFE_TO_ARCHIVE)   : {scen_b_count:3} files, {scen_b_bytes:10} bytes ({scen_b_bytes / (1024*1024):.4f} MiB)")
    print(f"Scenario D (POSSIBLY_DISPOSABLE): {scen_d_count:3} files, {scen_d_bytes:10} bytes ({scen_d_bytes / (1024*1024):.4f} MiB)")
    print(f"Scenario E (Combined B + D)    : {scen_e_count:3} files, {scen_e_bytes:10} bytes ({scen_e_bytes / (1024*1024):.4f} MiB)")
    print(f"Retained KEEP                  : {keep_count:3} files, {keep_bytes:10} bytes ({keep_bytes / (1024*1024):.4f} MiB)")

    assert scen_b_count == 216
    assert scen_d_count == 41
    assert keep_count == 67
    assert scen_e_count == 257
    assert scen_e_bytes + keep_bytes == total_disk_bytes

    # Build Verified Plan JSON
    verified_payload = {
        "verified_at": datetime.now().isoformat(),
        "total_physical_files": len(disk_files),
        "total_storage_bytes": total_disk_bytes,
        "total_storage_mib": round(total_disk_bytes / (1024 * 1024), 4),
        "total_storage_decimal_mb": round(total_disk_bytes / 1e6, 4),
        "discrepancy_forensic_resolution": {
            "issue": "Reported 107.73 MB sum of dependency classes vs 106.33 MB total duplicate size",
            "root_cause": "Class A bytes (30,244,097 bytes) was mislabelled as '30.24 MB' by confusing decimal SI megabytes (30.24 MB) with binary mebibytes (28.84 MiB). Adding 30.24 + 23.11 + 54.38 resulted in 107.73.",
            "corrected_binary_mib_values": {
                "class_a_mib": round(class_a_bytes / (1024 * 1024), 4),
                "class_b_mib": round(class_b_bytes / (1024 * 1024), 4),
                "class_c_mib": round(class_c_bytes / (1024 * 1024), 4),
                "sum_classes_mib": round(sum_classes_bytes / (1024 * 1024), 4),
                "total_redundant_duplicates_mib": round(total_redundant_bytes / (1024 * 1024), 4),
                "discrepancy_bytes": sum_classes_bytes - total_redundant_bytes
            },
            "exact_byte_values": {
                "class_a_bytes": class_a_bytes,
                "class_b_bytes": class_b_bytes,
                "class_c_bytes": class_c_bytes,
                "sum_classes_bytes": sum_classes_bytes,
                "total_redundant_duplicate_bytes": total_redundant_bytes,
                "exact_difference": 0
            }
        },
        "dependency_classification_summary": {
            "Class_A_Safe_to_Remove_After_Backup": {
                "files_count": class_a_count,
                "exact_bytes": class_a_bytes,
                "size_mib": round(class_a_bytes / (1024 * 1024), 4)
            },
            "Class_B_Path_Dependency_Must_Be_Updated_First": {
                "files_count": class_b_count,
                "exact_bytes": class_b_bytes,
                "size_mib": round(class_b_bytes / (1024 * 1024), 4)
            },
            "Class_C_Keep_for_Reproducibility": {
                "files_count": class_c_count,
                "exact_bytes": class_c_bytes,
                "size_mib": round(class_c_bytes / (1024 * 1024), 4)
            },
            "Total_Redundant_Duplicates": {
                "files_count": total_redundant_files_count,
                "exact_bytes": total_redundant_bytes,
                "size_mib": round(total_redundant_bytes / (1024 * 1024), 4)
            }
        },
        "exact_storage_scenarios": {
            "total_media_workspace_bytes": total_disk_bytes,
            "scenario_b_safe_to_archive": {
                "unique_files": scen_b_count,
                "exact_bytes": scen_b_bytes,
                "size_mib": round(scen_b_bytes / (1024 * 1024), 4)
            },
            "scenario_d_possibly_disposable": {
                "unique_files": scen_d_count,
                "exact_bytes": scen_d_bytes,
                "size_mib": round(scen_d_bytes / (1024 * 1024), 4)
            },
            "scenario_e_combined_b_and_d": {
                "unique_files": scen_e_count,
                "exact_bytes": scen_e_bytes,
                "size_mib": round(scen_e_bytes / (1024 * 1024), 4),
                "remaining_keep_bytes": keep_bytes,
                "remaining_keep_mib": round(keep_bytes / (1024 * 1024), 4),
                "remaining_keep_files": keep_count
            }
        },
        "reconciled_66_duplicate_files": reconciled_redundant_files
    }

    with open(VERIFIED_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(verified_payload, f, indent=2)
    print(f"\nWritten verified plan to: {VERIFIED_OUTPUT_JSON}")

if __name__ == "__main__":
    main()
