"""
Phase 1: Full Project Asset Inventory Generator for CrazzyDocs.
Calculates SHA-256 hashes, file sizes, creation/modification dates,
dependency associations, and categorizes into Categories A through J.
Outputs to media/audit/workspace_inventory.json and docs/WORKSPACE_ASSET_INVENTORY.md.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Set, Tuple

WORKSPACE = r"D:\Agents\CrazzyDocs"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

AUDIT_DIR = os.path.join(WORKSPACE, "media", "audit")
DB_PATH = os.path.join(WORKSPACE, "backend", "studio.db")
JSON_OUTPUT = os.path.join(AUDIT_DIR, "workspace_inventory.json")
MD_OUTPUT = os.path.join(WORKSPACE, "docs", "WORKSPACE_ASSET_INVENTORY.md")

os.makedirs(AUDIT_DIR, exist_ok=True)


def compute_sha256(filepath: str) -> str:
    if not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def get_file_metadata(filepath: str) -> Tuple[int, str, str]:
    try:
        st = os.stat(filepath)
        size = st.st_size
        ctime = datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        return size, ctime, mtime
    except Exception:
        now = datetime.now(timezone.utc).isoformat()
        return 0, now, now


def collect_database_references(db_path: str) -> Dict[str, List[str]]:
    """Gathers all file paths referenced by SQLite database tables."""
    references: Dict[str, List[str]] = {}
    if not os.path.isfile(db_path):
        return references

    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        # 1. publishing_ledger
        try:
            cur.execute("SELECT id, topic_title, video_path, thumbnail_path, upload_status, youtube_video_id FROM publishing_ledger")
            for pid, title, v_path, t_path, status, yt_id in cur.fetchall():
                ref_label = f"publishing_ledger:id={pid},status={status},yt={yt_id}"
                if v_path:
                    norm = os.path.abspath(v_path)
                    references.setdefault(norm, []).append(ref_label)
                if t_path:
                    norm = os.path.abspath(t_path)
                    references.setdefault(norm, []).append(ref_label)
        except Exception:
            pass

        # 2. persistent_jobs
        try:
            cur.execute("SELECT id, status, checkpoint_data_json FROM persistent_jobs")
            for jid, status, cp_json in cur.fetchall():
                ref_label = f"persistent_jobs:id={jid},status={status}"
                if cp_json:
                    try:
                        data = json.loads(cp_json)
                        for k in ("video_path", "thumbnail_path", "audio_path", "ass_path"):
                            val = data.get(k)
                            if val and isinstance(val, str):
                                norm = os.path.abspath(val)
                                references.setdefault(norm, []).append(ref_label)
                    except Exception:
                        pass
        except Exception:
            pass

        con.close()
    except Exception as e:
        print(f"Error reading database references: {e}")

    return references


def classify_asset(
    rel_path: str,
    abs_path: str,
    size_bytes: int,
    db_refs: List[str],
    seen_hashes: Dict[str, str],
    file_hash: str,
) -> Tuple[str, str, bool, bool]:
    """
    Classifies file into Categories A through J:
    Returns (category, reason, is_protected, is_quarantinable).
    """
    norm_rel = rel_path.replace("\\", "/")
    norm_lower = norm_rel.lower()
    base_name = os.path.basename(abs_path).lower()

    # Category B: IMMUTABLE BRANDING
    if norm_rel.startswith("Youtube Channel Src/") or norm_rel.startswith("docs/reference_styles/"):
        return (
            "CATEGORY_B_IMMUTABLE_BRANDING",
            "Authoritative channel branding asset or immutable HTML reference style",
            True,
            False,
        )

    # Category A: PERMANENT SOURCE
    source_dirs = (
        "backend/", "frontend/", "tests/", "docs/", "scripts/", "tools/",
        ".git/", ".gemini/",
    )
    core_files = {
        "readme.md", "gemini.md", ".gitignore", "run_in_env.cmd", "run_in_env.ps1",
        "run_pipeline.bat", "start_studio.bat", "start_studio.ps1", "env.ps1",
        "package.json", "tsconfig.json", "vite.config.ts",
    }
    if any(norm_rel.startswith(sd) for sd in source_dirs) or base_name in core_files:
        return (
            "CATEGORY_A_PERMANENT_SOURCE",
            "Core application source code, configuration, or documentation",
            True,
            False,
        )

    # Category C: APPROVED REUSABLE ASSETS
    if (
        norm_rel.startswith("assets/audio/music/")
        or norm_rel.startswith("assets/audio/sfx/")
        or norm_rel.startswith("assets/fonts/")
        or norm_rel == "assets/branding/crazzydocs/crazzydocs_outro_1080x1920.mp4"
        or norm_rel.startswith("media/source/")
    ):
        return (
            "CATEGORY_C_APPROVED_REUSABLE_ASSETS",
            "Standard audio bed, sound effect, font, or master outro template",
            True,
            False,
        )

    # Category D: APPROVED DELIVERABLES
    approved_files = {
        "showcase_vesna_vulovic_verified.mp4",
        "thumb_vesna_vulovic_verified.png",
        "showcase_cycle_4_20261110_1800.mp4",  # Real YouTube upload 4DCas1e4vHI
        "stage_3_13_vesna_voice.mp3",
    }
    # Check if referenced as uploaded in publishing ledger
    is_uploaded_yt = any("status=uploaded_private" in ref or "yt=4DCas1e4vHI" in ref for ref in db_refs)
    if base_name in approved_files or is_uploaded_yt:
        return (
            "CATEGORY_D_APPROVED_DELIVERABLES",
            "Approved production deliverable or published YouTube Short asset",
            True,
            False,
        )

    # Category H: TEMPORARY FILES
    temp_indicators = [
        ".tmp", ".temp", ".lock", "concat_", "_test_", "_scratch",
        "media/audio/scratch/", "media/video/scratch/",
    ]
    if any(ti in norm_lower for ti in temp_indicators):
        return (
            "CATEGORY_H_TEMPORARY_FILES",
            "Intermediate render artifact, scratch audio/video, or lock file",
            False,
            True,
        )

    # Category I: OBSOLETE CACHE
    cache_indicators = [
        "cache/", ".pytest_cache/", "__pycache__/", "media/cache/",
    ]
    if any(ci in norm_lower for ci in cache_indicators):
        return (
            "CATEGORY_I_OBSOLETE_CACHE",
            "Execution cache or build artifact",
            False,
            True,
        )

    # Category G: DUPLICATES
    if file_hash and size_bytes > 0:
        if file_hash in seen_hashes and seen_hashes[file_hash] != norm_rel:
            # If not protected by DB or approved
            if not is_uploaded_yt and base_name not in approved_files:
                return (
                    "CATEGORY_G_DUPLICATES",
                    f"Binary duplicate (SHA-256: {file_hash[:12]}...) of {seen_hashes[file_hash]}",
                    False,
                    True,
                )
        else:
            seen_hashes[file_hash] = norm_rel

    # Category E: HISTORICAL SHOWCASES
    historical_showcases = [
        "showcase_stage_3_4_tsutomu_yamaguchi.mp4",
        "showcase_stage_3_4_tsutomu_yamaguchi.png",
        "showcase_stage_3_5_yamaguchi_refined.png",
        "showcase_stage_3_9a_yamaguchi_poster.png",
        "stage_3_4_yamaguchi_voice.mp3",
        "showcase_test_auto_job_001.mp4",
        "showcase_test_interrupted_tts_job.mp4",
    ]
    if base_name in historical_showcases:
        return (
            "CATEGORY_E_HISTORICAL_SHOWCASES",
            "Historical showcase prototype from prior development stages",
            False,
            True,
        )

    # Category F: FAILED AND INCOMPLETE OUTPUTS
    if (
        "_failed" in norm_lower
        or "_partial" in norm_lower
        or any("status=FAILED" in r for r in db_refs)
    ):
        return (
            "CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS",
            "Incomplete, corrupted, or failed render artifact",
            False,
            True,
        )

    # Reusable verified media (e.g. Wikimedia images for active topic)
    if norm_rel.startswith("media/images/vesna_vulovic/"):
        return (
            "CATEGORY_C_APPROVED_REUSABLE_ASSETS",
            "Authentic Public Domain source media for current production",
            True,
            False,
        )

    # Old media folders
    if norm_rel.startswith("media/images/yamaguchi/") or norm_rel.startswith("media/images/molasses_flood/"):
        return (
            "CATEGORY_E_HISTORICAL_SHOWCASES",
            "Archival media from previous completed topics",
            False,
            True,
        )

    # Email previews
    if norm_rel.startswith("media/email_previews/"):
        return (
            "CATEGORY_C_APPROVED_REUSABLE_ASSETS",
            "Email dashboard notification previews",
            True,
            False,
        )

    # Category J: UNKNOWN (Preserve until manually classified)
    return (
        "CATEGORY_J_UNKNOWN",
        "Unclassified asset preserved safely pending manual inspection",
        True,
        False,
    )


def run_inventory():
    print("=== STARTING WORKSPACE ASSET INVENTORY ===")
    db_refs = collect_database_references(DB_PATH)
    print(f"Collected {len(db_refs)} database referenced paths.")

    inventory: List[Dict] = []
    seen_hashes: Dict[str, str] = {}
    category_summary: Dict[str, Dict] = {}

    # Target scan roots: media, assets, Youtube Channel Src, docs/reference_styles, cache
    scan_roots = [
        "media",
        "assets",
        "Youtube Channel Src",
        "docs/reference_styles",
        "cache",
    ]

    for root_folder in scan_roots:
        full_root = os.path.join(WORKSPACE, root_folder)
        if not os.path.exists(full_root):
            continue

        for dirpath, _, filenames in os.walk(full_root):
            for fn in filenames:
                abs_p = os.path.join(dirpath, fn)
                rel_p = os.path.relpath(abs_p, WORKSPACE).replace("\\", "/")
                size_b, ctime, mtime = get_file_metadata(abs_p)
                f_hash = compute_sha256(abs_p)
                refs = db_refs.get(os.path.abspath(abs_p), [])

                cat, reason, is_prot, is_quar = classify_asset(
                    rel_path=rel_p,
                    abs_path=abs_p,
                    size_bytes=size_b,
                    db_refs=refs,
                    seen_hashes=seen_hashes,
                    file_hash=f_hash,
                )

                item = {
                    "relative_path": rel_p,
                    "absolute_path": abs_p,
                    "size_bytes": size_b,
                    "size_mb": round(size_b / (1024 * 1024), 3),
                    "sha256": f_hash,
                    "created_at": ctime,
                    "modified_at": mtime,
                    "category": cat,
                    "reason": reason,
                    "is_protected": is_prot,
                    "is_quarantinable": is_quar,
                    "database_references": refs,
                    "is_actively_used": is_prot or len(refs) > 0,
                }
                inventory.append(item)

                # Track summary
                stats = category_summary.setdefault(cat, {"count": 0, "size_bytes": 0})
                stats["count"] += 1
                stats["size_bytes"] += size_b

    # Save JSON inventory
    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "workspace_root": WORKSPACE,
            "total_files": len(inventory),
            "total_size_mb": round(sum(i["size_bytes"] for i in inventory) / (1024 * 1024), 2),
            "category_summary": {
                k: {
                    "count": v["count"],
                    "size_mb": round(v["size_bytes"] / (1024 * 1024), 2),
                }
                for k, v in category_summary.items()
            },
            "inventory": inventory,
        }, f, indent=2)

    print(f"Inventory saved: {JSON_OUTPUT} ({len(inventory)} files)")

    # Generate Markdown Report
    total_bytes = sum(i["size_bytes"] for i in inventory)
    quarantinable_items = [i for i in inventory if i["is_quarantinable"]]
    quar_bytes = sum(i["size_bytes"] for i in quarantinable_items)

    md_lines = [
        "# CrazzyDocs Workspace Asset Inventory (Stage 3.13B)",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Total Files Inventoried:** {len(inventory)}  ",
        f"**Total Size:** {round(total_bytes / (1024 * 1024), 2)} MB  ",
        f"**Quarantinable Candidates:** {len(quarantinable_items)} files ({round(quar_bytes / (1024 * 1024), 2)} MB)  ",
        "",
        "## 1. Category Breakdown",
        "| Category | File Count | Total Size (MB) | Policy |",
        "|---|---|---|---|",
    ]

    for cat in sorted(category_summary.keys()):
        s = category_summary[cat]
        prot = "PROTECTED (Preserve)" if "PERMANENT" in cat or "IMMUTABLE" in cat or "APPROVED" in cat or "UNKNOWN" in cat else "QUARANTINE ELIGIBLE"
        md_lines.append(f"| `{cat}` | {s['count']} | {round(s['size_bytes'] / (1024 * 1024), 2)} MB | {prot} |")

    md_lines.extend([
        "",
        "## 2. Immutable Branding & Reference Styles (Protected)",
        "| Relative Path | Size | SHA-256 (64-char hex) |",
        "|---|---|---|",
    ])

    for i in inventory:
        if i["category"] == "CATEGORY_B_IMMUTABLE_BRANDING":
            md_lines.append(f"| `{i['relative_path']}` | {i['size_mb']} MB | `{i['sha256']}` |")

    md_lines.extend([
        "",
        "## 3. Approved Deliverables (Protected)",
        "| Relative Path | Size | Database References |",
        "|---|---|---|",
    ])

    for i in inventory:
        if i["category"] == "CATEGORY_D_APPROVED_DELIVERABLES":
            ref_str = ", ".join(i["database_references"]) if i["database_references"] else "None (Protected Showcase)"
            md_lines.append(f"| `{i['relative_path']}` | {i['size_mb']} MB | {ref_str} |")

    md_lines.extend([
        "",
        "## 4. Quarantinable Assets (Proposed for Stage 3.13B Clean-Slate Reset)",
        "| Relative Path | Category | Size | Reason |",
        "|---|---|---|---|",
    ])

    for i in quarantinable_items:
        md_lines.append(f"| `{i['relative_path']}` | `{i['category']}` | {i['size_mb']} MB | {i['reason']} |")

    with open(MD_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"Markdown report saved: {MD_OUTPUT}")


if __name__ == "__main__":
    run_inventory()
