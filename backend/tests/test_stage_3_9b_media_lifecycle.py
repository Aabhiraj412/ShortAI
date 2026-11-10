"""
Unit & Integration Tests for Stage 3.9B: Intelligent Media Asset Lifecycle Manager.
Validates file classification into Categories A-I, strict dry-run behavior,
boundary enforcement, reversible quarantine, and checksum-verified restoration.
"""

from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import pytest

from backend.app.services.media_lifecycle_manager import (
    MediaLifecycleManager,
    AssetCategory,
    PROTECTED_CATEGORIES,
    compute_file_sha256,
)


@pytest.fixture
def mock_storage_env():
    """Sets up an isolated workspace hierarchy for testing lifecycle operations."""
    tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
    
    # Create test directories
    os.makedirs(os.path.join(tmpdir, "backend", "app"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "assets", "brand"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "assets", "audio", "music"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "video"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "thumbnails"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "audio", "scratch"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "cache"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "storage", "quarantine"), exist_ok=True)

    # Populate sample files
    core_file = os.path.join(tmpdir, "backend", "main.py")
    with open(core_file, "w") as f:
        f.write("# core code")

    music_file = os.path.join(tmpdir, "assets", "audio", "music", "dramatic_bed.mp3")
    with open(music_file, "w") as f:
        f.write("MOCK_MUSIC_BYTES")

    approved_video = os.path.join(tmpdir, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
    with open(approved_video, "w") as f:
        f.write("MOCK_MP4_BYTES")

    scratch_file = os.path.join(tmpdir, "media", "audio", "scratch", "temp_pipe.mp4")
    with open(scratch_file, "w") as f:
        f.write("MOCK_SCRATCH_BYTES")

    cache_file = os.path.join(tmpdir, "cache", "model_weights.bin")
    with open(cache_file, "w") as f:
        f.write("MOCK_CACHE_BYTES")

    orphaned_media = os.path.join(tmpdir, "media", "video", "unregistered_raw_clip.mp4")
    with open(orphaned_media, "w") as f:
        f.write("MOCK_ORPHAN_BYTES")

    manager = MediaLifecycleManager(
        workspace_root=tmpdir,
        db_path=os.path.join(tmpdir, "nonexistent.db"),
        quarantine_dir=os.path.join(tmpdir, "storage", "quarantine"),
    )

    yield {
        "tmpdir": tmpdir,
        "manager": manager,
        "core_file": core_file,
        "music_file": music_file,
        "approved_video": approved_video,
        "scratch_file": scratch_file,
        "cache_file": cache_file,
        "orphaned_media": orphaned_media,
    }

    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_classification_categories_and_protection(mock_storage_env):
    """Verifies that files are classified into correct categories with proper protection flags."""
    env = mock_storage_env
    mgr = env["manager"]
    deps = mgr.collect_dependencies()

    # Category A: Permanent Source
    cf_a = mgr.classify_file(env["core_file"], deps)
    assert cf_a.category == AssetCategory.CATEGORY_A_PERMANENT_SOURCE
    assert cf_a.is_protected is True
    assert cf_a.is_quarantinable is False

    # Category B: Reusable Asset
    cf_b = mgr.classify_file(env["music_file"], deps)
    assert cf_b.category == AssetCategory.CATEGORY_B_REUSABLE_ASSETS
    assert cf_b.is_protected is True
    assert cf_b.is_quarantinable is False

    # Category C: Approved Deliverable
    cf_c = mgr.classify_file(env["approved_video"], deps)
    assert cf_c.category == AssetCategory.CATEGORY_C_APPROVED_DELIVERABLES
    assert cf_c.is_protected is True
    assert cf_c.is_quarantinable is False

    # Category E: Temporary Scratch File
    cf_e = mgr.classify_file(env["scratch_file"], deps)
    assert cf_e.category == AssetCategory.CATEGORY_E_TEMPORARY_FILES
    assert cf_e.is_protected is False
    assert cf_e.is_quarantinable is True

    # Category F: Cache
    cf_f = mgr.classify_file(env["cache_file"], deps)
    assert cf_f.category == AssetCategory.CATEGORY_F_CACHE
    assert cf_f.is_protected is False
    assert cf_f.is_quarantinable is True

    # Category I: Orphaned Media
    cf_i = mgr.classify_file(env["orphaned_media"], deps)
    assert cf_i.category == AssetCategory.CATEGORY_I_ORPHANED
    assert cf_i.is_protected is False
    assert cf_i.is_quarantinable is True


def test_dry_run_safety_default(mock_storage_env):
    """Verifies that default quarantine operations never move or delete files."""
    env = mock_storage_env
    mgr = env["manager"]

    files = mgr.scan_workspace()
    scratch_cf = [f for f in files if f.category == AssetCategory.CATEGORY_E_TEMPORARY_FILES]
    assert len(scratch_cf) > 0

    # Execute quarantine in dry_run=True (default)
    res = mgr.quarantine_files(scratch_cf, dry_run=True)
    assert res["dry_run"] is True
    assert res["quarantined_count"] == len(scratch_cf)
    assert res["bytes_freed"] > 0

    # Ensure physical file still exists at original path
    assert os.path.isfile(env["scratch_file"])
    # Quarantine directory must be empty
    q_files = [f for f in os.listdir(mgr.quarantine_dir) if f != "quarantine_manifest.json"]
    assert len(q_files) == 0


def test_protected_category_defense(mock_storage_env):
    """Verifies that attempts to quarantine Category A, B, or C files are blocked."""
    env = mock_storage_env
    mgr = env["manager"]

    files = mgr.scan_workspace(subdirs=["backend", "media", "assets", "cache", "logs"])
    protected_files = [f for f in files if f.category in PROTECTED_CATEGORIES]
    assert len(protected_files) >= 3  # core, music, approved showcase

    # Attempt quarantine on protected files even with dry_run=False
    res = mgr.quarantine_files(protected_files, dry_run=False)
    assert res["quarantined_count"] == 0
    assert res["skipped_protected_count"] == len(protected_files)

    # All protected files must remain intact
    assert os.path.isfile(env["core_file"])
    assert os.path.isfile(env["music_file"])
    assert os.path.isfile(env["approved_video"])


def test_quarantine_and_restoration_roundtrip(mock_storage_env):
    """Verifies complete quarantine move, manifest integrity, and restoration."""
    env = mock_storage_env
    mgr = env["manager"]

    scratch_path = env["scratch_file"]
    original_sha256 = compute_file_sha256(scratch_path)
    deps = mgr.collect_dependencies()
    cf = mgr.classify_file(scratch_path, deps)

    # 1. Execute quarantine with dry_run=False
    res = mgr.quarantine_files([cf], dry_run=False)
    assert res["quarantined_count"] == 1
    assert len(res["quarantined_ids"]) == 1
    q_id = res["quarantined_ids"][0]

    # Physical file is moved from original path
    assert not os.path.isfile(scratch_path)

    # Manifest contains entry
    manifest = mgr._load_quarantine_manifest()
    assert q_id in manifest
    assert manifest[q_id]["sha256"] == original_sha256
    assert manifest[q_id]["restored"] is False

    # 2. Restore file
    restored = mgr.restore_file(q_id)
    assert restored is True

    # Physical file exists again at original path with matching sha256
    assert os.path.isfile(scratch_path)
    assert compute_file_sha256(scratch_path) == original_sha256

    # Manifest record updated
    updated_manifest = mgr._load_quarantine_manifest()
    assert updated_manifest[q_id]["restored"] is True


def test_storage_report_generation(mock_storage_env):
    """Verifies storage report structure, category accounting, and top files."""
    env = mock_storage_env
    mgr = env["manager"]
    rep_path = os.path.join(env["tmpdir"], "logs", "test_report.json")

    report = mgr.generate_storage_report(output_path=rep_path)
    assert os.path.isfile(rep_path)

    assert "total_files_scanned" in report
    assert "categories" in report
    assert "quarantinable_size_mb" in report
    assert "top_largest_files" in report

    assert report["total_files_scanned"] >= 5
    assert AssetCategory.CATEGORY_A_PERMANENT_SOURCE.value in report["categories"]
    assert AssetCategory.CATEGORY_C_APPROVED_DELIVERABLES.value in report["categories"]
