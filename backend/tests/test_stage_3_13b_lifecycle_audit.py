"""
Comprehensive Test Suite for Stage 3.13B: Media Lifecycle Cleaner & Architecture Audit.

Covers all 10 mandatory audit criteria:
1. Duplicate identification
2. Protected source file preservation
3. Approved deliverable preservation
4. Quarantine functionality into media/quarantine/archived_files/
5. Full restoration back to original paths
6. Hash integrity verification
7. Interrupted cleanup recovery (.tmp manifest recovery)
8. Unknown file handling (preserved by default)
9. Database reference protection (preserves active SQLite records)
10. Repeated cleaner execution (idempotency)
"""

from datetime import datetime, timezone
import json
import os
import shutil
import sqlite3
import tempfile
import pytest

from backend.app.services.media_lifecycle_manager import (
    MediaLifecycleManager,
    AssetCategory,
    PROTECTED_CATEGORIES,
    compute_file_sha256,
)


@pytest.fixture
def lifecycle_env():
    """Sets up an isolated realistic sandbox workspace for cleaner audit."""
    tmpdir = tempfile.mkdtemp(prefix="test_audit_lifecycle_")

    # Target folder layout
    os.makedirs(os.path.join(tmpdir, "backend", "app"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "Youtube Channel Src"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "docs", "reference_styles"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "assets", "branding", "crazzydocs"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "assets", "audio", "music"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "video"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "thumbnails"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "audio", "scratch"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "media", "quarantine", "archived_files"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "cache"), exist_ok=True)

    db_path = os.path.join(tmpdir, "backend", "studio.db")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE publishing_ledger (
            id INTEGER PRIMARY KEY,
            topic_title TEXT,
            video_path TEXT,
            thumbnail_path TEXT,
            upload_status TEXT,
            video_hash TEXT,
            youtube_video_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE persistent_jobs (
            id TEXT PRIMARY KEY,
            topic_title TEXT,
            status TEXT,
            checkpoint_data_json TEXT
        )
    """)

    # Populate files:
    # 1. Category A: Permanent source
    src_file = os.path.join(tmpdir, "backend", "app", "main.py")
    with open(src_file, "w") as f:
        f.write("# Source Code")

    # 2. Category B: Immutable branding
    logo_file = os.path.join(tmpdir, "Youtube Channel Src", "Youtube-Icon.png")
    with open(logo_file, "wb") as f:
        f.write(b"AUTHORITATIVE_BRANDING_BYTES_12345")

    style_file = os.path.join(tmpdir, "docs", "reference_styles", "pop_info.html")
    with open(style_file, "w") as f:
        f.write("<html>Immutable Style</html>")

    # 3. Category C: Reusable assets
    reusable_audio = os.path.join(tmpdir, "assets", "audio", "music", "bed.mp3")
    with open(reusable_audio, "wb") as f:
        f.write(b"AUDIO_BED_CONTENT_XYZ")

    # 4. Category D: Approved deliverable referenced in DB as uploaded
    approved_video = os.path.join(tmpdir, "media", "video", "showcase_cycle_4_20261110_1800.mp4")
    with open(approved_video, "wb") as f:
        f.write(b"APPROVED_VIDEO_PAYLOAD_55SEC")
    approved_thumb = os.path.join(tmpdir, "media", "thumbnails", "thumb_cycle_4_20261110_1800.png")
    with open(approved_thumb, "wb") as f:
        f.write(b"APPROVED_THUMB_PAYLOAD")

    cur.execute(
        "INSERT INTO publishing_ledger VALUES (?, ?, ?, ?, ?, ?, ?)",
        (32, "Vesna Vulovic", approved_video, approved_thumb, "uploaded_private", "fakehash", "4DCas1e4vHI"),
    )
    con.commit()
    con.close()

    # 5. Category E: Historical showcase
    old_showcase = os.path.join(tmpdir, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
    with open(old_showcase, "wb") as f:
        f.write(b"OLD_YAMAGUCHI_SHOWCASE_BYTES")

    # 6. Category G: Duplicate file
    dup_file = os.path.join(tmpdir, "media", "video", "showcase_stage_3_4_copy.mp4")
    with open(dup_file, "wb") as f:
        f.write(b"OLD_YAMAGUCHI_SHOWCASE_BYTES")

    # 7. Category H: Temp file
    scratch_file = os.path.join(tmpdir, "media", "audio", "scratch", "concat_temp.mp4")
    with open(scratch_file, "wb") as f:
        f.write(b"SCRATCH_BYTES")

    # 8. Category I: Obsolete cache
    cache_file = os.path.join(tmpdir, "cache", "weights.bin")
    with open(cache_file, "wb") as f:
        f.write(b"CACHE_BYTES")

    # 9. Category J: Unknown file
    unknown_file = os.path.join(tmpdir, "media", "custom_unclassified_asset.dat")
    with open(unknown_file, "wb") as f:
        f.write(b"UNKNOWN_METADATA")

    manager = MediaLifecycleManager(
        workspace_root=tmpdir,
        db_path=db_path,
        quarantine_dir=os.path.join(tmpdir, "media", "quarantine"),
    )

    yield {
        "tmpdir": tmpdir,
        "manager": manager,
        "db_path": db_path,
        "src_file": src_file,
        "logo_file": logo_file,
        "style_file": style_file,
        "reusable_audio": reusable_audio,
        "approved_video": approved_video,
        "approved_thumb": approved_thumb,
        "old_showcase": old_showcase,
        "dup_file": dup_file,
        "scratch_file": scratch_file,
        "cache_file": cache_file,
        "unknown_file": unknown_file,
    }

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_1_duplicate_identification(lifecycle_env):
    """Criterion 1: Accurately identifies identical SHA-256 byte duplicates."""
    mgr = lifecycle_env["manager"]
    files = mgr.scan_workspace(subdirs=["media"], compute_hashes_for_duplicates=True)
    dup_candidates = [f for f in files if "copy" in f.relative_path]
    assert len(dup_candidates) == 1
    assert dup_candidates[0].category == AssetCategory.CATEGORY_G_DUPLICATES
    assert dup_candidates[0].is_quarantinable is True


def test_2_protected_source_preservation(lifecycle_env):
    """Criterion 2: Core source and immutable branding are strictly protected."""
    mgr = lifecycle_env["manager"]
    deps = mgr.collect_dependencies()

    cf_src = mgr.classify_file(lifecycle_env["src_file"], deps)
    assert cf_src.is_protected is True
    assert cf_src.is_quarantinable is False

    cf_logo = mgr.classify_file(lifecycle_env["logo_file"], deps)
    assert cf_logo.is_protected is True
    assert cf_logo.is_quarantinable is False

    cf_style = mgr.classify_file(lifecycle_env["style_file"], deps)
    assert cf_style.is_protected is True
    assert cf_style.is_quarantinable is False


def test_3_approved_deliverable_preservation(lifecycle_env):
    """Criterion 3: Approved deliverables referenced in database are strictly preserved."""
    mgr = lifecycle_env["manager"]
    deps = mgr.collect_dependencies()

    cf_vid = mgr.classify_file(lifecycle_env["approved_video"], deps)
    assert cf_vid.is_protected is True
    assert cf_vid.is_quarantinable is False


def test_4_quarantine_functionality(lifecycle_env):
    """Criterion 4: Quarantine safely moves eligible files to media/quarantine/archived_files/."""
    mgr = lifecycle_env["manager"]
    files = mgr.scan_workspace(subdirs=["media", "cache"])
    scratch_cf = next(f for f in files if "concat_temp.mp4" in f.relative_path)

    res = mgr.quarantine_files([scratch_cf], dry_run=False)
    assert res["quarantined_count"] == 1
    assert not os.path.exists(lifecycle_env["scratch_file"])

    # Physical file is in archived_files
    archived_dir = os.path.join(mgr.quarantine_dir, "archived_files")
    assert len(os.listdir(archived_dir)) == 1

    # Manifest is updated
    manifest = mgr._load_quarantine_manifest()
    assert len(manifest) == 1
    q_rec = list(manifest.values())[0]
    assert q_rec["original_relative_path"] == scratch_cf.relative_path
    assert q_rec["restored"] is False


def test_5_and_6_restoration_and_hash_integrity(lifecycle_env):
    """Criteria 5 & 6: Full restoration back to original path with SHA-256 verification."""
    mgr = lifecycle_env["manager"]
    orig_path = lifecycle_env["scratch_file"]
    expected_hash = compute_file_sha256(orig_path)

    files = mgr.scan_workspace(subdirs=["media"])
    scratch_cf = next(f for f in files if "concat_temp.mp4" in f.relative_path)

    res = mgr.quarantine_files([scratch_cf], dry_run=False)
    q_id = res["quarantined_ids"][0]

    # File missing from original
    assert not os.path.exists(orig_path)

    # Restore
    ok = mgr.restore_file(q_id)
    assert ok is True
    assert os.path.isfile(orig_path)
    restored_hash = compute_file_sha256(orig_path)
    assert restored_hash == expected_hash

    # Manifest marks as restored
    manifest = mgr._load_quarantine_manifest()
    assert manifest[q_id]["restored"] is True


def test_7_interrupted_cleanup_recovery(lifecycle_env):
    """Criterion 7: Recovers gracefully if an interrupted write leaves a .tmp manifest."""
    mgr = lifecycle_env["manager"]
    tmp_manifest = mgr.manifest_path + ".tmp"
    sample_data = {
        "q_mock": {
            "quarantine_id": "q_mock",
            "original_relative_path": "media/test.txt",
            "quarantine_relative_path": "media/quarantine/archived_files/q_mock.txt",
            "sha256": "abc123",
            "size_bytes": 100,
            "category": "CATEGORY_H_TEMPORARY_FILES",
            "reason": "Test",
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "restored": False,
        }
    }
    with open(tmp_manifest, "w", encoding="utf-8") as f:
        json.dump(sample_data, f)

    # Calling _load_quarantine_manifest recovers the file
    loaded = mgr._load_quarantine_manifest()
    assert "q_mock" in loaded
    assert os.path.isfile(mgr.manifest_path)
    assert not os.path.isfile(tmp_manifest)


def test_8_unknown_file_handling(lifecycle_env):
    """Criterion 8: Unknown/unclassified files are protected by default and not quarantined."""
    mgr = lifecycle_env["manager"]
    deps = mgr.collect_dependencies()

    cf_unknown = mgr.classify_file(lifecycle_env["unknown_file"], deps)
    assert cf_unknown.category == AssetCategory.CATEGORY_J_UNKNOWN
    assert cf_unknown.is_protected is True
    assert cf_unknown.is_quarantinable is False

    res = mgr.quarantine_files([cf_unknown], dry_run=False)
    assert res["quarantined_count"] == 0
    assert res["skipped_protected_count"] == 1
    assert os.path.isfile(lifecycle_env["unknown_file"])


def test_9_database_reference_protection(lifecycle_env):
    """Criterion 9: Blocks quarantine for any file actively referenced in publishing ledger."""
    mgr = lifecycle_env["manager"]
    deps = mgr.collect_dependencies()

    approved_v = lifecycle_env["approved_video"]
    cf_vid = mgr.classify_file(approved_v, deps)

    res = mgr.quarantine_files([cf_vid], dry_run=False)
    assert res["quarantined_count"] == 0
    assert res["skipped_protected_count"] == 1
    assert os.path.isfile(approved_v)


def test_10_repeated_cleaner_execution_idempotency(lifecycle_env):
    """Criterion 10: Repeated execution of the cleaner is idempotent and safe."""
    mgr = lifecycle_env["manager"]
    files = mgr.scan_workspace(subdirs=["media", "cache"])
    eligible = [f for f in files if f.is_quarantinable]

    # Pass 1
    res1 = mgr.quarantine_files(eligible, dry_run=False)
    initial_quarantined = res1["quarantined_count"]
    assert initial_quarantined > 0

    # Pass 2 immediately afterwards
    files_pass2 = mgr.scan_workspace(subdirs=["media", "cache"])
    eligible_pass2 = [f for f in files_pass2 if f.is_quarantinable]
    res2 = mgr.quarantine_files(eligible_pass2, dry_run=False)

    assert res2["quarantined_count"] == 0
    assert res2["bytes_freed"] == 0
