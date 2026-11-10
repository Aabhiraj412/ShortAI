"""
Controlled Workspace Cleaner for Stage 3.13B Clean-Slate Reset.
Executes reversible quarantine into media/quarantine/archived_files/
and records all operations in media/quarantine/manifest.json.

Guarantees:
- Protected categories (Permanent Source, Immutable Branding, Approved Deliverables, Test Fixtures) are NEVER touched.
- YouTube upload 4DCas1e4vHI (showcase_cycle_4_20261110_1800.mp4) is preserved.
- Verified Vesna deliverables are preserved.
- Database references in studio.db are strictly respected.
- Reversible: Every file can be restored using MediaLifecycleManager.restore_all().
"""

from datetime import datetime, timezone
import json
import logging
import os
import sys

WORKSPACE = r"D:\Agents\CrazzyDocs"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from backend.app.services.media_lifecycle_manager import (
    MediaLifecycleManager,
    AssetCategory,
    PROTECTED_CATEGORIES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("controlled_cleaner")


def run_controlled_cleanup(dry_run: bool = False):
    logger.info("=== STARTING STAGE 3.13B CONTROLLED CLEANUP (dry_run=%s) ===", dry_run)
    mgr = MediaLifecycleManager(
        workspace_root=WORKSPACE,
        db_path=os.path.join(WORKSPACE, "backend", "studio.db"),
        quarantine_dir=os.path.join(WORKSPACE, "media", "quarantine"),
    )

    # Scan media, cache, storage directories
    logger.info("Scanning workspace directories...")
    files = mgr.scan_workspace(subdirs=["media", "cache"], compute_hashes_for_duplicates=True)
    logger.info("Total files scanned: %d", len(files))

    quarantinable = [f for f in files if f.is_quarantinable and not f.is_protected]
    protected = [f for f in files if f.is_protected or f.category in PROTECTED_CATEGORIES]

    logger.info("Identified %d protected files (Preserved unconditionally).", len(protected))
    logger.info("Identified %d quarantine-eligible files.", len(quarantinable))

    # Log breakdown of quarantinable files
    by_cat = {}
    for f in quarantinable:
        by_cat[f.category.value] = by_cat.get(f.category.value, 0) + 1
    for cat_name, cnt in by_cat.items():
        logger.info("  -> %s: %d files", cat_name, cnt)

    # Execute quarantine
    res = mgr.quarantine_files(quarantinable, dry_run=dry_run)

    logger.info("=== CLEANUP RESULTS ===")
    logger.info("Dry Run: %s", res["dry_run"])
    logger.info("Files Quarantined: %d", res["quarantined_count"])
    logger.info("Space Recovered: %.2f MB", res["bytes_freed"] / (1024 * 1024))
    logger.info("Protected Files Skipped: %d", res["skipped_protected_count"])
    logger.info("Quarantine Manifest: %s", mgr.manifest_path)

    return res


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    run_controlled_cleanup(dry_run=is_dry)
