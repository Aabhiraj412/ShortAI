"""
Stage 3.21D — Media Workspace Audit & Safe Cleanup Script (Refined).
Performs comprehensive inventory, dependency tracing, duplicate analysis,
classification (A-H), and cleanup manifest generation.
"""
import os
import sys
import json
import hashlib
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from collections import defaultdict

WORKSPACE_ROOT = Path(r"D:\Agents\CrazzyDocs")
MEDIA_ROOT = WORKSPACE_ROOT / "media"
DB_PATH = WORKSPACE_ROOT / "backend" / "studio.db"

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

    print(f"Publishing Ledger: Active={active_cnt}, Archived={arch_cnt}")
    print(f"Active row: {active_row}")
    assert active_cnt == 1, f"Expected 1 active ledger entry, found {active_cnt}"
    assert arch_cnt == 253, f"Expected 253 archived ledger entries, found {arch_cnt}"
    assert active_row[1] == "4DCas1e4vHI", f"Expected video 4DCas1e4vHI, found {active_row[1]}"
    print("Database invariants: VERIFIED")

def collect_db_references():
    print("--- 2. Collecting Database References ---")
    db_refs = set()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in c.fetchall()]
    for table in tables:
        c.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in c.fetchall()]
        c.execute(f"SELECT * FROM {table}")
        rows = c.fetchall()
        for row in rows:
            for val in row:
                if isinstance(val, str):
                    if "media" in val or "/" in val or "\\" in val or any(ext in val for ext in [".mp4", ".png", ".jpg", ".mp3", ".json", ".ass", ".srt"]):
                        norm = val.replace("\\", "/").strip()
                        db_refs.add(norm)
                        db_refs.add(Path(norm).name)
    conn.close()
    print(f"Collected {len(db_refs)} potential references from database.")
    return db_refs

def collect_code_references():
    print("--- 3. Collecting Application & Test References ---")
    code_refs = {}
    search_dirs = [
        WORKSPACE_ROOT / "backend",
        WORKSPACE_ROOT / "docs"
    ]
    extensions = {".py", ".html", ".js", ".css", ".json", ".md", ".sh", ".ps1"}
    for sdir in search_dirs:
        for root, _, files in os.walk(sdir):
            for file in files:
                p = Path(root) / file
                if p.suffix.lower() in extensions:
                    try:
                        text = p.read_text(encoding="utf-8", errors="ignore")
                        rel = str(p.relative_to(WORKSPACE_ROOT)).replace("\\", "/")
                        code_refs[rel] = text
                    except Exception:
                        pass
    print(f"Indexed {len(code_refs)} project files for reference search.")
    return code_refs

def scan_media_files(db_refs, code_refs):
    print("--- 4. Scanning Media Files ---")
    files_info = []
    hash_map = defaultdict(list)
    
    for root, _, files in os.walk(MEDIA_ROOT):
        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(MEDIA_ROOT)
            rel_str = str(rel_path).replace("\\", "/")
            full_rel_str = f"media/{rel_str}"
            
            size = full_path.stat().st_size
            mtime = datetime.fromtimestamp(full_path.stat().st_mtime).isoformat()
            ext = full_path.suffix.lower()
            sha = sha256_file(full_path)
            
            hash_map[sha].append(full_rel_str)
            
            # DB match
            ref_db = False
            for r in db_refs:
                if full_rel_str in r or rel_str in r or f == r:
                    ref_db = True
                    break
            
            # Code matches
            code_matched = []
            for filepath, content in code_refs.items():
                if f in content or rel_str in content or full_rel_str in content:
                    code_matched.append(filepath)
            
            parts = rel_path.parts
            top_dir = parts[0] if parts else ""
            
            # Check production manifest
            in_manifest = False
            associated_job = "N/A"
            if top_dir == "productions" and len(parts) > 1:
                associated_job = parts[1]
                prod_dir = MEDIA_ROOT / "productions" / parts[1]
                manifest_file = prod_dir / "manifest.json"
                if not manifest_file.exists():
                    manifest_file = prod_dir / "production_manifest.json"
                if manifest_file.exists():
                    try:
                        m_text = manifest_file.read_text(encoding="utf-8", errors="ignore")
                        if f in m_text:
                            in_manifest = True
                    except Exception:
                        pass
            elif "vesna" in rel_str.lower():
                associated_job = "prod_20261110_vesna_vulovic"
            elif "yamaguchi" in rel_str.lower():
                associated_job = "showcase_yamaguchi_cycle_4"
            elif "55sec" in rel_str.lower() or "molasses" in rel_str.lower():
                associated_job = "stage_3_1_molasses_flood"
            elif "marathon" in rel_str.lower():
                associated_job = "stage_3_2_marathon"
            
            files_info.append({
                "path": full_rel_str,
                "filename": f,
                "ext": ext,
                "size_bytes": size,
                "size_mb": round(size / (1024 * 1024), 4),
                "sha256": sha,
                "mtime": mtime,
                "top_dir": top_dir,
                "associated_job": associated_job,
                "referenced_by_db": ref_db,
                "referenced_by_manifest": in_manifest,
                "referenced_by_code": code_matched,
                "code_ref_count": len(code_matched),
            })
            
    print(f"Total media files scanned: {len(files_info)}")
    return files_info, hash_map

def classify_media(files_info, hash_map):
    print("--- 5. Classifying Media Files ---")
    
    for item in files_info:
        path = item["path"]
        p_lower = path.lower()
        sha = item["sha256"]
        dup_instances = hash_map[sha]
        dup_count = len(dup_instances)
        item["is_duplicate"] = dup_count > 1
        item["duplicate_instances"] = dup_instances if dup_count > 1 else []
        
        # 1. Official branding & outro assets (CRITICAL REUSABLE)
        if "source/branding" in p_lower:
            item["category"] = "B. REUSABLE ASSETS"
            item["action"] = "KEEP"
            item["risk"] = "CRITICAL"
            item["reason"] = "Official channel branding asset, browser favicon, or outro video"

        # 2. Reusable audio samples (HIGH REUSABLE)
        elif "source/reusable" in p_lower:
            item["category"] = "B. REUSABLE ASSETS"
            item["action"] = "KEEP"
            item["risk"] = "HIGH"
            item["reason"] = "Official reusable EdgeTTS audio reference sample"

        # 3. Canonical completed production: Vesna Vulovic (CRITICAL HISTORICAL ARCHIVE)
        elif "productions/prod_20261110_vesna_vulovic" in p_lower:
            item["category"] = "C. HISTORICAL PRODUCTION ARCHIVE"
            item["action"] = "KEEP"
            item["risk"] = "CRITICAL"
            item["reason"] = "Canonical assets for genuine completed production (Private YouTube Video 4DCas1e4vHI)"

        # 4. Quarantined assets (LOW RISK SAFE_TO_ARCHIVE)
        elif "quarantine" in p_lower:
            item["category"] = "F. QUARANTINED ASSETS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Pre-isolated quarantine files from prior stabilization stages"

        # 5. Audit contact sheets, inventories, reports (LOW RISK SAFE_TO_ARCHIVE)
        elif "audit" in p_lower:
            item["category"] = "E. AUDIT AND VERIFICATION ARTIFACTS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Quality audit contact sheet, integrity report, or workspace inventory"

        # 6. Email previews (LOW RISK SAFE_TO_ARCHIVE)
        elif "email_previews" in p_lower:
            item["category"] = "E. AUDIT AND VERIFICATION ARTIFACTS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Notification email template preview and sample payload"

        # 7. Independent evaluation: prod_cycle_4_20261110_1800 (MEDIUM RISK SAFE_TO_ARCHIVE)
        elif "productions/prod_cycle_4_20261110_1800" in p_lower:
            item["category"] = "C. HISTORICAL PRODUCTION ARCHIVE"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "MEDIUM"
            item["reason"] = "Historical automated Cycle 4 showcase production (archived ledger record #246)"

        # 8. Independent evaluation: prod_stage_3_16_verification (LOW RISK SAFE_TO_ARCHIVE)
        elif "productions/prod_stage_3_16_verification" in p_lower:
            item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Stage 3.16 end-to-end pipeline verification test render and reports"

        # 9. Test crash simulation directories under productions/ (LOW RISK SAFE_TO_ARCHIVE)
        elif "productions/test_" in p_lower:
            item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Automated test or crash simulation directory under productions"

        # 10. Stick figure animation prototypes in media/video (LOW RISK POSSIBLY_DISPOSABLE)
        elif "video/stick_" in p_lower:
            item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
            item["action"] = "POSSIBLY_DISPOSABLE"
            item["risk"] = "LOW"
            item["reason"] = "Early developmental animation smoke test / stick figure prototype"

        # 11. Redundant duplicate files (pointing to canonical instance)
        elif dup_count > 1 and not ("source/branding" in p_lower or "prod_20261110_vesna_vulovic" in p_lower):
            canonical = next((p for p in dup_instances if "prod_20261110_vesna_vulovic" in p or "source/" in p or "prod_cycle_4" in p), dup_instances[0])
            item["category"] = "G. DUPLICATES"
            item["action"] = "POSSIBLY_DISPOSABLE"
            item["risk"] = "LOW"
            item["reason"] = f"Identical byte duplicate of canonical file {canonical}"

        # 12. Historical research images (Molasses Flood, Yamaguchi)
        elif "images/historical" in p_lower or "images/yamaguchi" in p_lower:
            item["category"] = "C. HISTORICAL PRODUCTION ARCHIVE"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Historical public domain source images retrieved for completed showcase productions"

        # 13. Subtitle style templates and historical transcripts
        elif "subtitles" in p_lower:
            if any(s in p_lower for s in ["chalk_talk", "creator_motion", "data_story", "pop_info", "sample_expressive"]):
                item["category"] = "B. REUSABLE ASSETS"
                item["action"] = "KEEP"
                item["risk"] = "MEDIUM"
                item["reason"] = "Visual subtitle style template or sample animation asset"
            else:
                item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
                item["action"] = "SAFE_TO_ARCHIVE"
                item["risk"] = "LOW"
                item["reason"] = "Historical subtitle or transcript from prior test renders"

        # 14. Audio master files and templates
        elif "audio" in p_lower:
            if any(s in p_lower for s in ["chalk_talk", "creator_motion", "data_story", "pop_info", "sample_"]):
                item["category"] = "B. REUSABLE ASSETS"
                item["action"] = "KEEP"
                item["risk"] = "MEDIUM"
                item["reason"] = "Reusable narration style sample or audio benchmark"
            else:
                item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
                item["action"] = "SAFE_TO_ARCHIVE"
                item["risk"] = "LOW"
                item["reason"] = "Historical narration audio from previous test runs"

        # 15. Standalone video/thumbnail files
        elif "video/production_55sec_short.mp4" in p_lower:
            item["category"] = "D. TEST AND DEVELOPMENT ARTIFACTS"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Stage 3.1 55-second test video render"
        elif "thumbnails/candidates" in p_lower:
            item["category"] = "C. HISTORICAL PRODUCTION ARCHIVE"
            item["action"] = "SAFE_TO_ARCHIVE"
            item["risk"] = "LOW"
            item["reason"] = "Thumbnail candidate from Tsutomu Yamaguchi showcase production"
        else:
            item["category"] = "H. UNKNOWN / NEEDS REVIEW"
            item["action"] = "NEEDS_OPERATOR_REVIEW"
            item["risk"] = "MEDIUM"
            item["reason"] = "Uncategorized file requiring manual operator assessment"

    return files_info

def main():
    verify_invariants()
    db_refs = collect_db_references()
    code_refs = collect_code_references()
    files_info, hash_map = scan_media_files(db_refs, code_refs)
    classified = classify_media(files_info, hash_map)
    
    total_bytes = sum(x["size_bytes"] for x in classified)
    dir_storage = defaultdict(int)
    cat_storage = defaultdict(lambda: {"count": 0, "bytes": 0})
    action_storage = defaultdict(lambda: {"count": 0, "bytes": 0})
    
    for item in classified:
        top = item["top_dir"]
        cat = item["category"]
        act = item["action"]
        sz = item["size_bytes"]
        
        dir_storage[top] += sz
        cat_storage[cat]["count"] += 1
        cat_storage[cat]["bytes"] += sz
        action_storage[act]["count"] += 1
        action_storage[act]["bytes"] += sz
        
    print("\n=======================================================")
    print("           MEDIA WORKSPACE AUDIT SUMMARY")
    print("=======================================================")
    print(f"Total Files Scanned : {len(classified)}")
    print(f"Total Storage Size  : {total_bytes / (1024*1024):.2f} MB ({total_bytes} bytes)")
    
    print("\n--- Storage by Directory ---")
    for d, b in sorted(dir_storage.items(), key=lambda x: x[1], reverse=True):
        print(f"  {d:20}: {b / (1024*1024):8.2f} MB ({b:10} bytes)")
        
    print("\n--- Storage by Category (A - H) ---")
    for c, data in sorted(cat_storage.items(), key=lambda x: x[0]):
        print(f"  {c:37}: {data['count']:4} files, {data['bytes'] / (1024*1024):8.2f} MB")
        
    print("\n--- Storage by Proposed Action ---")
    for a, data in sorted(action_storage.items(), key=lambda x: x[1]["bytes"], reverse=True):
        print(f"  {a:25}: {data['count']:4} files, {data['bytes'] / (1024*1024):8.2f} MB")
        
    dups = {k: v for k, v in hash_map.items() if len(v) > 1}
    total_dup_redundant_bytes = sum(next(x["size_bytes"] for x in classified if x["sha256"] == k) * (len(v) - 1) for k, v in dups.items())
    print(f"\nTotal Duplicate Hash Sets   : {len(dups)}")
    print(f"Potential Duplicate Savings : {total_dup_redundant_bytes / (1024*1024):.2f} MB")

    manifest_path = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_CLEANUP_CANDIDATES.json"
    manifest_data = {
        "generated_at": datetime.now().isoformat(),
        "total_files": len(classified),
        "total_bytes": total_bytes,
        "total_megabytes": round(total_bytes / (1024*1024), 2),
        "directory_summary": {k: {"bytes": v, "mb": round(v / (1024*1024), 2)} for k, v in dir_storage.items()},
        "category_summary": {k: {"count": v["count"], "mb": round(v["bytes"] / (1024*1024), 2)} for k, v in sorted(cat_storage.items())},
        "action_summary": {k: {"count": v["count"], "mb": round(v["bytes"] / (1024*1024), 2)} for k, v in sorted(action_storage.items())},
        "duplicate_hash_sets_count": len(dups),
        "potential_duplicate_redundant_mb": round(total_dup_redundant_bytes / (1024*1024), 2),
        "files": classified
    }
    
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
    print(f"\nWritten manifest to: {manifest_path}")

if __name__ == "__main__":
    main()
