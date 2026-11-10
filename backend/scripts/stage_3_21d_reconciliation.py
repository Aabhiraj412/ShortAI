"""
Stage 3.21D-R — Independent Media Cleanup Validation & Reconciliation Script.
Read-only script that validates inventory, recalculates exact byte savings,
reconciles quarantine manifest, produces verified mutually exclusive classification,
and generates STAGE_3_21D_CLEANUP_CANDIDATES_VERIFIED.json.
"""
import os
import sys
import json
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict, Counter

WORKSPACE_ROOT = Path(r"D:\Agents\CrazzyDocs")
MEDIA_ROOT = WORKSPACE_ROOT / "media"
DB_PATH = WORKSPACE_ROOT / "backend" / "studio.db"
STAGE_3_21D_JSON = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_CLEANUP_CANDIDATES.json"

def sha256_file(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def verify_invariants():
    print("--- 1. Verifying System Invariants ---")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM publishing_ledger")
    active_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM publishing_ledger_archive")
    arch_cnt = c.fetchone()[0]
    c.execute("SELECT id, youtube_video_id, upload_status, visibility FROM publishing_ledger")
    active_row = c.fetchone()
    conn.close()

    assert active_cnt == 1, f"Expected 1 active ledger entry, found {active_cnt}"
    assert arch_cnt == 253, f"Expected 253 archived ledger entries, found {arch_cnt}"
    assert active_row[1] == "4DCas1e4vHI", f"Expected video 4DCas1e4vHI, found {active_row[1]}"
    print(f"Invariants verified: Active Ledger={active_cnt}, Archive={arch_cnt}, Video={active_row[1]}")

def perform_reconciliation():
    print("--- 2. Independent Re-scan of media/ ---")
    current_files = {}
    hash_to_paths = defaultdict(list)
    dir_storage = defaultdict(lambda: {"count": 0, "bytes": 0})
    
    for root, _, files in os.walk(MEDIA_ROOT):
        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(MEDIA_ROOT)
            rel_str = str(rel_path).replace("\\", "/")
            full_rel = f"media/{rel_str}"
            
            stat = full_path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
            ext = full_path.suffix.lower()
            sha = sha256_file(full_path)
            
            top_dir = rel_path.parts[0] if rel_path.parts else ""
            
            current_files[full_rel] = {
                "path": full_rel,
                "filename": f,
                "ext": ext,
                "size_bytes": size,
                "sha256": sha,
                "mtime": mtime,
                "top_dir": top_dir,
            }
            hash_to_paths[sha].append(full_rel)
            dir_storage[top_dir]["count"] += 1
            dir_storage[top_dir]["bytes"] += size

    total_files = len(current_files)
    total_bytes = sum(x["size_bytes"] for x in current_files.values())
    print(f"Total current files: {total_files}")
    print(f"Total current bytes: {total_bytes} ({total_bytes / (1024*1024):.4f} MB)")

    # Compare with 3.21D JSON
    print("--- 3. Comparing with Stage 3.21D Manifest ---")
    with open(STAGE_3_21D_JSON, "r", encoding="utf-8") as f:
        prev_data = json.load(f)
        
    prev_files = {x["path"]: x for x in prev_data["files"]}
    
    missing_files = set(prev_files.keys()) - set(current_files.keys())
    new_files = set(current_files.keys()) - set(prev_files.keys())
    modified_files = []
    sha_mismatches = []
    
    for p, c_info in current_files.items():
        if p in prev_files:
            p_info = prev_files[p]
            if c_info["size_bytes"] != p_info["size_bytes"]:
                modified_files.append((p, p_info["size_bytes"], c_info["size_bytes"]))
            if c_info["sha256"] != p_info["sha256"]:
                sha_mismatches.append((p, p_info["sha256"], c_info["sha256"]))

    print(f"Missing files: {len(missing_files)}")
    print(f"New files: {len(new_files)}")
    print(f"Modified files: {len(modified_files)}")
    print(f"SHA-256 mismatches: {len(sha_mismatches)}")
    assert len(missing_files) == 0
    assert len(new_files) == 0
    assert len(modified_files) == 0
    assert len(sha_mismatches) == 0
    print("Exact 1:1 match with previous manifest: CONFIRMED")

    # Forensic Quarantine Inspection
    print("--- 4. Forensic Quarantine Review ---")
    qdir = MEDIA_ROOT / "quarantine"
    q_manifest_file = qdir / "manifest.json"
    with open(q_manifest_file, "r", encoding="utf-8") as f:
        q_manifest = json.load(f)
        
    quarantine_items = []
    for p, c_info in current_files.items():
        if c_info["top_dir"] == "quarantine":
            if c_info["filename"] == "manifest.json":
                quarantine_items.append({
                    "path": p,
                    "type": "manifest",
                    "size_bytes": c_info["size_bytes"],
                    "sha256": c_info["sha256"],
                    "original_path": "N/A",
                    "reason": "Master quarantine registry (2317 historical ledger entries)",
                    "has_surviving_copy": False,
                    "forensic_value": "HIGH - Contains full historical stabilization audit trail",
                    "restoration_recommended": False,
                    "disposal_justified": False,
                    "action": "SAFE_TO_ARCHIVE"
                })
            else:
                stem = Path(c_info["filename"]).stem
                q_entry = q_manifest.get(stem, {})
                orig_path = q_entry.get("original_relative_path", "UNKNOWN")
                q_reason = q_entry.get("reason", "N/A")
                sha = c_info["sha256"]
                
                # Check surviving copies outside quarantine
                copies_outside = [cp for cp in hash_to_paths[sha] if not cp.startswith("media/quarantine")]
                has_surviving = len(copies_outside) > 0
                
                quarantine_items.append({
                    "path": p,
                    "type": "archived_file",
                    "size_bytes": c_info["size_bytes"],
                    "sha256": sha,
                    "original_path": orig_path,
                    "original_category": q_entry.get("category", "N/A"),
                    "reason": q_reason,
                    "has_surviving_copy": has_surviving,
                    "surviving_copies": copies_outside,
                    "forensic_value": "MEDIUM - Isolated during Stage 3.14 stabilization",
                    "restoration_recommended": False,
                    "disposal_justified": True,
                    "action": "SAFE_TO_ARCHIVE"
                })

    print(f"Quarantine files reviewed: {len(quarantine_items)}")

    # Mutually Exclusive Primary Classification
    print("--- 5. Assigning Mutually Exclusive Classifications ---")
    verified_files = []
    
    # Classifications:
    # A. ACTIVE PRODUCTION ASSETS (0)
    # B. REUSABLE ASSETS
    # C. HISTORICAL PRODUCTION ARCHIVE
    # D. TEST AND DEVELOPMENT ARTIFACTS
    # E. AUDIT AND VERIFICATION ARTIFACTS
    # F. QUARANTINED ASSETS
    # H. UNKNOWN / NEEDS REVIEW (0)
    
    for p, item in current_files.items():
        p_lower = p.lower()
        sha = item["sha256"]
        dup_paths = hash_to_paths[sha]
        is_dup = len(dup_paths) > 1
        
        # Determine canonical copy for duplicate groups
        if is_dup:
            # Canonical priority:
            # 1. Official source/branding or source/reusable
            # 2. Canonical production (prod_20261110_vesna_vulovic)
            # 3. Canonical showcase (prod_cycle_4_20261110_1800)
            # 4. media/thumbnails/candidates/ or media/video/
            # 5. First alphabetically
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
            
            canonical_path = sorted(dup_paths, key=sort_key)[0]
            is_canonical = (p == canonical_path)
            dup_role = "canonical" if is_canonical else "redundant_copy"
        else:
            canonical_path = None
            dup_role = "unique"

        # PRIMARY MUTUALLY EXCLUSIVE CLASSIFICATION
        if "source/branding" in p_lower:
            primary_cat = "B. REUSABLE ASSETS"
            action = "KEEP"
            confidence = "CRITICAL"
            reason = "Official YouTube branding asset, channel banner, or outro video"
            sub_tag = "branding"
        elif "source/reusable" in p_lower:
            primary_cat = "B. REUSABLE ASSETS"
            action = "KEEP"
            confidence = "HIGH"
            reason = "Official reusable EdgeTTS voice synthesis benchmark sample"
            sub_tag = "audio_sample"
        elif "productions/prod_20261110_vesna_vulovic" in p_lower:
            primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
            action = "KEEP"
            confidence = "CRITICAL"
            reason = "Genuine completed production asset for private YouTube Short 4DCas1e4vHI"
            sub_tag = "canonical_production"
        elif "quarantine" in p_lower:
            primary_cat = "F. QUARANTINED ASSETS"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Isolated in quarantine during prior stabilization stages"
            sub_tag = "quarantine_archive"
        elif "audit" in p_lower:
            primary_cat = "E. AUDIT AND VERIFICATION ARTIFACTS"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Inspection contact sheet, test integrity report, or workspace audit log"
            sub_tag = "audit_report"
        elif "email_previews" in p_lower:
            primary_cat = "E. AUDIT AND VERIFICATION ARTIFACTS"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Stage 3.18 notification email HTML and plaintext preview template"
            sub_tag = "email_preview"
        elif "productions/prod_cycle_4_20261110_1800" in p_lower:
            primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
            action = "SAFE_TO_ARCHIVE"
            confidence = "MEDIUM"
            reason = "Automated Cycle 4 showcase production (archived ledger record #246)"
            sub_tag = "showcase_production"
        elif "images/historical" in p_lower or "images/yamaguchi" in p_lower:
            primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Historical public domain source images retrieved for showcase productions"
            sub_tag = "historical_source_image"
        elif "thumbnails/candidates" in p_lower:
            primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Tsutomu Yamaguchi showcase thumbnail candidate variant"
            sub_tag = "thumbnail_candidate"
        elif "productions/prod_stage_3_16_verification" in p_lower:
            primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Stage 3.16 pipeline verification test render and associated audit summary"
            sub_tag = "verification_run"
        elif "productions/test_" in p_lower:
            primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
            # If it is a redundant copy of Vesna or Yamaguchi video, mark POSSIBLY_DISPOSABLE
            if is_dup and dup_role == "redundant_copy" and item["size_bytes"] > 1000000:
                action = "POSSIBLY_DISPOSABLE"
            else:
                action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Automated pipeline test run or crash simulation fixture"
            sub_tag = "crash_test_fixture"
        elif "video/stick_" in p_lower:
            primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
            action = "POSSIBLY_DISPOSABLE"
            confidence = "LOW"
            reason = "Early developmental animation smoke test / stick figure prototype"
            sub_tag = "early_prototype"
        elif "video/production_55sec_short.mp4" in p_lower:
            primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
            action = "SAFE_TO_ARCHIVE"
            confidence = "LOW"
            reason = "Stage 3.1 55-second test video render"
            sub_tag = "test_render"
        elif "subtitles" in p_lower:
            if any(s in p_lower for s in ["chalk_talk", "creator_motion", "data_story", "pop_info", "sample_expressive"]):
                primary_cat = "B. REUSABLE ASSETS"
                action = "KEEP"
                confidence = "MEDIUM"
                reason = "Reusable subtitle visual style template or sample typography asset"
                sub_tag = "subtitle_style_template"
            else:
                primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
                action = "SAFE_TO_ARCHIVE"
                confidence = "LOW"
                reason = "Historical subtitle ASS/SRT file or transcript from prior test runs"
                sub_tag = "test_subtitles"
        elif "audio" in p_lower:
            if any(s in p_lower for s in ["chalk_talk", "creator_motion", "data_story", "pop_info", "sample_"]):
                primary_cat = "B. REUSABLE ASSETS"
                action = "KEEP"
                confidence = "MEDIUM"
                reason = "Reusable style narration audio benchmark or voice sample"
                sub_tag = "narration_style_sample"
            elif is_dup and dup_role == "redundant_copy":
                primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
                action = "POSSIBLY_DISPOSABLE"
                confidence = "LOW"
                reason = f"Redundant narration audio copy of canonical {canonical_path}"
                sub_tag = "duplicate_audio"
            else:
                primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
                action = "SAFE_TO_ARCHIVE"
                confidence = "LOW"
                reason = "Historical narration audio from previous test runs"
                sub_tag = "test_narration"
        elif is_dup and dup_role == "redundant_copy":
            # If in images/vesna_vulovic, it's a duplicate of prod_20261110_vesna_vulovic/assets
            if "images/vesna_vulovic" in p_lower:
                primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
                action = "POSSIBLY_DISPOSABLE"
                confidence = "LOW"
                reason = f"Duplicate historical asset copy of canonical {canonical_path}"
                sub_tag = "duplicate_image"
            elif "thumbnails" in p_lower:
                primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
                action = "POSSIBLY_DISPOSABLE"
                confidence = "LOW"
                reason = f"Duplicate thumbnail render copy of canonical {canonical_path}"
                sub_tag = "duplicate_thumbnail"
            elif "video" in p_lower:
                primary_cat = "C. HISTORICAL PRODUCTION ARCHIVE"
                action = "POSSIBLY_DISPOSABLE"
                confidence = "LOW"
                reason = f"Duplicate showcase video copy of canonical {canonical_path}"
                sub_tag = "duplicate_video"
            else:
                primary_cat = "D. TEST AND DEVELOPMENT ARTIFACTS"
                action = "POSSIBLY_DISPOSABLE"
                confidence = "LOW"
                reason = f"Redundant duplicate of canonical {canonical_path}"
                sub_tag = "duplicate_file"
        else:
            primary_cat = "H. UNKNOWN / NEEDS REVIEW"
            action = "NEEDS_OPERATOR_REVIEW"
            confidence = "MEDIUM"
            reason = "Uncategorized file requiring manual operator assessment"
            sub_tag = "unresolved"

        verified_files.append({
            "path": p,
            "filename": item["filename"],
            "ext": item["ext"],
            "size_bytes": item["size_bytes"],
            "size_mb": round(item["size_bytes"] / (1024 * 1024), 4),
            "sha256": sha,
            "mtime": item["mtime"],
            "top_dir": item["top_dir"],
            "primary_classification": primary_cat,
            "secondary_tag": sub_tag,
            "is_duplicate": is_dup,
            "duplicate_role": dup_role,
            "canonical_path": canonical_path,
            "action_recommendation": action,
            "confidence_level": confidence,
            "reason": reason
        })

    # Assert exactly 324 files
    assert len(verified_files) == 324
    
    # Check mutually exclusive primary classification
    cat_counts = Counter(x["primary_classification"] for x in verified_files)
    cat_bytes = defaultdict(int)
    for x in verified_files:
        cat_bytes[x["primary_classification"]] += x["size_bytes"]

    print("\n--- Mutually Exclusive Primary Classification Totals ---")
    sum_cat_count = 0
    sum_cat_bytes = 0
    for cat in sorted(cat_counts.keys()):
        cnt = cat_counts[cat]
        b = cat_bytes[cat]
        sum_cat_count += cnt
        sum_cat_bytes += b
        print(f"  {cat:35}: {cnt:3} files, {b:10} bytes ({b/(1024*1024):8.2f} MB)")
        
    print(f"  SUM OF PRIMARY CLASSIFICATIONS    : {sum_cat_count:3} files, {sum_cat_bytes:10} bytes ({sum_cat_bytes/(1024*1024):8.2f} MB)")
    assert sum_cat_count == 324
    assert sum_cat_bytes == total_bytes

    # Check Actions
    act_counts = Counter(x["action_recommendation"] for x in verified_files)
    act_bytes = defaultdict(int)
    for x in verified_files:
        act_bytes[x["action_recommendation"]] += x["size_bytes"]

    print("\n--- Action Recommendations ---")
    sum_act_count = 0
    sum_act_bytes = 0
    for act in sorted(act_counts.keys()):
        cnt = act_counts[act]
        b = act_bytes[act]
        sum_act_count += cnt
        sum_act_bytes += b
        print(f"  {act:25}: {cnt:3} files, {b:10} bytes ({b/(1024*1024):8.2f} MB)")
    assert sum_act_count == 324
    assert sum_act_bytes == total_bytes

    # Duplicate Reconciliation
    print("\n--- 6. Duplicate Groups Analysis ---")
    dup_groups = {}
    for sha, paths in hash_to_paths.items():
        if len(paths) > 1:
            sz = current_files[paths[0]]["size_bytes"]
            redundant_bytes = sz * (len(paths) - 1)
            dup_groups[sha] = {
                "sha256": sha,
                "copy_count": len(paths),
                "individual_size_bytes": sz,
                "total_group_bytes": sz * len(paths),
                "redundant_bytes": redundant_bytes,
                "paths": paths
            }

    print(f"Total duplicate groups: {len(dup_groups)}")
    total_redundant_dup_bytes = sum(g["redundant_bytes"] for g in dup_groups.values())
    print(f"Total redundant duplicate bytes: {total_redundant_dup_bytes} ({total_redundant_dup_bytes / (1024*1024):.2f} MB)")

    # Storage Scenarios Recalculation (Exact Bytes)
    print("\n--- 7. Storage Savings Scenarios ---")
    # Current
    curr_b = total_bytes
    
    # Scenario A: Archive candidates without deleting (0 bytes recovered)
    scen_a_affected_b = sum(x["size_bytes"] for x in verified_files if x["action_recommendation"] == "SAFE_TO_ARCHIVE")
    scen_a_files = sum(1 for x in verified_files if x["action_recommendation"] == "SAFE_TO_ARCHIVE")
    scen_a_recovered_b = 0
    scen_a_remaining_b = curr_b
    
    # Scenario B: Move archive candidates to another physical drive
    scen_b_affected_b = scen_a_affected_b
    scen_b_files = scen_a_files
    scen_b_recovered_b = scen_b_affected_b
    scen_b_remaining_b = curr_b - scen_b_recovered_b
    
    # Scenario C: Remove verified redundant duplicate copies only (33 files)
    scen_c_affected_b = sum(x["size_bytes"] for x in verified_files if x["duplicate_role"] == "redundant_copy")
    scen_c_files = sum(1 for x in verified_files if x["duplicate_role"] == "redundant_copy")
    scen_c_recovered_b = scen_c_affected_b
    scen_c_remaining_b = curr_b - scen_c_recovered_b
    
    # Scenario D: Remove approved disposable test artifacts only (35 files = 33 redundant copies + 2 stick prototypes)
    scen_d_affected_b = sum(x["size_bytes"] for x in verified_files if x["action_recommendation"] == "POSSIBLY_DISPOSABLE")
    scen_d_files = sum(1 for x in verified_files if x["action_recommendation"] == "POSSIBLY_DISPOSABLE")
    scen_d_recovered_b = scen_d_affected_b
    scen_d_remaining_b = curr_b - scen_d_recovered_b
    
    # Scenario E: Combined progressive optimization (Remove POSSIBLY_DISPOSABLE + Move SAFE_TO_ARCHIVE to external drive)
    scen_e_affected_b = scen_d_affected_b + scen_b_affected_b
    scen_e_files = scen_d_files + scen_b_files
    scen_e_recovered_b = scen_e_affected_b
    scen_e_remaining_b = curr_b - scen_e_recovered_b
    keep_b = sum(x["size_bytes"] for x in verified_files if x["action_recommendation"] == "KEEP")
    keep_files = sum(1 for x in verified_files if x["action_recommendation"] == "KEEP")
    
    assert scen_e_remaining_b == keep_b
    assert scen_e_files + keep_files == 324
    
    print(f"Scenario A (In-Place Archive): Affected={scen_a_files} files ({scen_a_affected_b} bytes), Recovered=0 bytes")
    print(f"Scenario B (Offload to External Drive): Affected={scen_b_files} files ({scen_b_affected_b} bytes), Recovered={scen_b_recovered_b} bytes ({scen_b_recovered_b/(1024*1024):.2f} MB)")
    print(f"Scenario C (Remove 33 Redundant Duplicates): Affected={scen_c_files} files ({scen_c_affected_b} bytes), Recovered={scen_c_recovered_b} bytes ({scen_c_recovered_b/(1024*1024):.2f} MB)")
    print(f"Scenario D (Remove 35 Disposable Artifacts): Affected={scen_d_files} files ({scen_d_affected_b} bytes), Recovered={scen_d_recovered_b} bytes ({scen_d_recovered_b/(1024*1024):.2f} MB)")
    print(f"Scenario E (Combined Progressive Clean): Recovered={scen_e_recovered_b} bytes ({scen_e_recovered_b/(1024*1024):.2f} MB), Remaining KEEP={keep_b} bytes ({keep_b/(1024*1024):.2f} MB, {keep_files} files)")

    # Output Verified Manifest
    verified_manifest_path = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_CLEANUP_CANDIDATES_VERIFIED.json"
    manifest_payload = {
        "verified_at": datetime.now().isoformat(),
        "total_physical_files": total_files,
        "total_storage_bytes": total_bytes,
        "total_storage_mb": round(total_bytes / (1024 * 1024), 4),
        "primary_classification_summary": {
            cat: {
                "file_count": cat_counts[cat],
                "bytes": cat_bytes[cat],
                "mb": round(cat_bytes[cat] / (1024 * 1024), 4),
            } for cat in sorted(cat_counts.keys())
        },
        "action_recommendation_summary": {
            act: {
                "file_count": act_counts[act],
                "bytes": act_bytes[act],
                "mb": round(act_bytes[act] / (1024 * 1024), 4),
            } for act in sorted(act_counts.keys())
        },
        "storage_recovery_scenarios": {
            "scenario_a_inplace_archive": {
                "description": "Archive candidates without deleting (moving/compressing on same drive)",
                "files_affected": scen_a_files,
                "bytes_affected": scen_a_affected_b,
                "mb_affected": round(scen_a_affected_b / (1024 * 1024), 4),
                "actual_disk_recovered_bytes": 0,
                "actual_disk_recovered_mb": 0.0,
                "remaining_storage_bytes": scen_a_remaining_b,
                "remaining_storage_mb": round(scen_a_remaining_b / (1024 * 1024), 4),
            },
            "scenario_b_external_drive_offload": {
                "description": "Move SAFE_TO_ARCHIVE candidates to another physical drive",
                "files_affected": scen_b_files,
                "bytes_affected": scen_b_affected_b,
                "mb_affected": round(scen_b_affected_b / (1024 * 1024), 4),
                "actual_disk_recovered_bytes": scen_b_recovered_b,
                "actual_disk_recovered_mb": round(scen_b_recovered_b / (1024 * 1024), 4),
                "remaining_storage_bytes": scen_b_remaining_b,
                "remaining_storage_mb": round(scen_b_remaining_b / (1024 * 1024), 4),
            },
            "scenario_c_remove_redundant_duplicates": {
                "description": "Remove 33 exact redundant byte copies while preserving one canonical copy",
                "files_affected": scen_c_files,
                "bytes_affected": scen_c_affected_b,
                "mb_affected": round(scen_c_affected_b / (1024 * 1024), 4),
                "actual_disk_recovered_bytes": scen_c_recovered_b,
                "actual_disk_recovered_mb": round(scen_c_recovered_b / (1024 * 1024), 4),
                "remaining_storage_bytes": scen_c_remaining_b,
                "remaining_storage_mb": round(scen_c_remaining_b / (1024 * 1024), 4),
            },
            "scenario_d_remove_disposable_artifacts": {
                "description": "Remove approved POSSIBLY_DISPOSABLE items (33 redundant copies + 2 stick prototypes)",
                "files_affected": scen_d_files,
                "bytes_affected": scen_d_affected_b,
                "mb_affected": round(scen_d_affected_b / (1024 * 1024), 4),
                "actual_disk_recovered_bytes": scen_d_recovered_b,
                "actual_disk_recovered_mb": round(scen_d_recovered_b / (1024 * 1024), 4),
                "remaining_storage_bytes": scen_d_remaining_b,
                "remaining_storage_mb": round(scen_d_remaining_b / (1024 * 1024), 4),
            },
            "scenario_e_combined_progressive_clean": {
                "description": "Combined progressive optimization (Purge POSSIBLY_DISPOSABLE + Offload SAFE_TO_ARCHIVE)",
                "files_affected": scen_e_files,
                "bytes_affected": scen_e_affected_b,
                "mb_affected": round(scen_e_affected_b / (1024 * 1024), 4),
                "actual_disk_recovered_bytes": scen_e_recovered_b,
                "actual_disk_recovered_mb": round(scen_e_recovered_b / (1024 * 1024), 4),
                "remaining_storage_bytes": keep_b,
                "remaining_storage_mb": round(keep_b / (1024 * 1024), 4),
                "retained_keep_files": keep_files,
            }
        },
        "duplicate_groups_count": len(dup_groups),
        "total_redundant_duplicate_bytes": total_redundant_dup_bytes,
        "total_redundant_duplicate_mb": round(total_redundant_dup_bytes / (1024 * 1024), 4),
        "duplicate_groups": dup_groups,
        "quarantine_forensic_review": quarantine_items,
        "files": verified_files
    }

    with open(verified_manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)
    print(f"\nWrote verified manifest to: {verified_manifest_path}")

if __name__ == "__main__":
    verify_invariants()
    perform_reconciliation()
