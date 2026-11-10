# CrazzyDocs Media Storage & Asset Lifecycle Management Standard
**Phase 5, Stage 3.9B Standard Operating Procedure**  
**Workspace:** `D:\AI Arts\AI\`  
**Enforcement:** Strict Local-First Quarantine, Dry-Run by Default, Zero Inadvertent Deletion

---

## 1. Overview & Architectural Principles

The CrazzyDocs YouTube Shorts studio generates high-resolution video frames, audio stems, subtitles, and thumbnails autonomously. To prevent disk bloat while guaranteeing that no approved deliverable or historical asset is ever destroyed, the **Intelligent Media Asset Lifecycle Manager** enforces a 9-category file taxonomy with SQLite database cross-referencing and reversible quarantine.

### Core Principles
1. **Zero Raw Deletions:** Files eligible for space recovery are never deleted via unrecoverable unlink. They are reversibly moved to `storage/quarantine/` with an atomic manifest entry tracking their original relative path, sha256 checksum, category, reason, and timestamp.
2. **Dry-Run by Default:** All scanning, auditing, and quarantine routines execute in `dry_run=True` mode unless explicitly overridden with `dry_run=False`.
3. **Database & Manifest Dependency Gate:** Before any file is considered for quarantine, the lifecycle manager inspects SQLite tables (`persistent_jobs`, `publishing_ledger`, `media_assets`, `video_jobs`, `content_ledger`) and JSON attribution manifests. If any active or approved reference exists, the file is automatically protected.
4. **Permanent Workspace Invariants:** Core repository code, immutable reference templates (`docs/reference_styles/*.html`), and master channel branding (`Youtube Channel Src/`) are strictly protected under Category A.

---

## 2. File Classification Taxonomy (Categories A – I)

| Category | Identifier | Description | Protected? | Quarantinable? |
| :--- | :--- | :--- | :---: | :---: |
| **A** | `CATEGORY_A_PERMANENT_SOURCE` | Source code, config, documentation, test suites, Git history, immutable templates (`docs/reference_styles/*.html`), original channel branding (`Youtube Channel Src/`), runtime environments. | **YES** | NO |
| **B** | `CATEGORY_B_REUSABLE_ASSETS` | Reusable channel assets, background music library (`assets/audio/music/`), channel logos, fonts, actively referenced attribution media. | **YES** | NO |
| **C** | `CATEGORY_C_APPROVED_DELIVERABLES` | Approved production videos (`media/video/showcase_*.mp4`), approved thumbnails (`media/thumbnails/showcase_*.png`), and deliverables marked `COMPLETED` or `PUBLISHED`. | **YES** | NO |
| **D** | `CATEGORY_D_UNPUBLISHED_DELIVERABLES` | Ready for publication or queued in `persistent_jobs` (`PENDING`, `RUNNING`, `READY_FOR_PUBLICATION`). | **YES** | NO |
| **E** | `CATEGORY_E_TEMPORARY_FILES` | Scratch render files, FFmpeg pipes (`_pipe.mp4`), temporary concat lists, locks (`.lock`), intermediate voiceover stems (`media/audio/scratch/`). | NO | **YES** |
| **F** | `CATEGORY_F_CACHE` | Reproducible intermediate render caches (`cache/`), Python bytecode (`__pycache__`), Pytest cache (`.pytest_cache/`). | NO | **YES** |
| **G** | `CATEGORY_G_FAILED_ARTIFACTS` | Artifacts produced by failed or aborted jobs (`FAILED`, `CANCELLED`), partial video/audio files (`_partial_*`, `_failed_*`). | NO | **YES** |
| **H** | `CATEGORY_H_DUPLICATES` | Files possessing identical SHA-256 checksums to another file where one copy is already preserved in an approved or reusable location. | NO | **YES** |
| **I** | `CATEGORY_I_ORPHANED` | Media assets residing in `media/` that possess no corresponding entry in SQLite or attribution manifests and are not approved deliverables. | NO | **YES** |

---

## 3. Quarantine & Restoration Workflow

### Quarantine Protocol
When quarantine is executed with `dry_run=False`:
1. The target file's SHA-256 hash and size are computed.
2. A unique quarantine ID is generated: `q_<timestamp>_<hash_prefix>`.
3. The physical file is moved to `storage/quarantine/<quarantine_id>.<ext>`.
4. A record is added to `storage/quarantine/quarantine_manifest.json`:
```json
{
  "q_1731245000_a1b2c3d4": {
    "quarantine_id": "q_1731245000_a1b2c3d4",
    "original_relative_path": "media/video/scratch/test_pipe.mp4",
    "quarantine_relative_path": "storage/quarantine/q_1731245000_a1b2c3d4.mp4",
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "size_bytes": 1048576,
    "category": "CATEGORY_E_TEMPORARY_FILES",
    "reason": "Intermediate scratch render file",
    "quarantined_at": "2026-11-10T13:30:00Z",
    "restored": false,
    "restored_at": null
  }
}
```

### Restoration Protocol
Any quarantined file can be restored instantly:
- **Single File:** `media_lifecycle_manager.restore_file(quarantine_id)` moves the file back to its exact `original_relative_path` (recreating directories if necessary), verifies its SHA-256 checksum against the manifest, and updates `restored: true`.
- **Full Rollback:** `media_lifecycle_manager.restore_all()` restores all currently unrestored files in the manifest.

---

## 4. Operational Commands & API Usage

### 4.1 Daily Storage Audit (Safe Dry-Run)
```python
from backend.app.services.media_lifecycle_manager import media_lifecycle_manager

report = media_lifecycle_manager.daily_report()
print(f"Total Workspace Storage: {report['total_size_mb']} MB")
print(f"Quarantinable Space: {report['quarantinable_size_mb']} MB")
```
Generates: `logs/storage_audit_report.json`.

### 4.2 Weekly Temporary Quarantine
Quarantines Category E and G files older than the retention threshold (default: 7 days):
```python
# 1. Preview candidates (Dry Run)
preview = media_lifecycle_manager.weekly_temp_quarantine(retention_days=7, dry_run=True)
print(f"Eligible for quarantine: {preview['eligible_count']} files ({preview['bytes_freed']} bytes)")

# 2. Execute quarantine
result = media_lifecycle_manager.weekly_temp_quarantine(retention_days=7, dry_run=False)
print(f"Quarantined {result['quarantined_count']} files.")
```

### 4.3 Monthly Comprehensive Audit & Reversible Rollback
```python
# Comprehensive scan and dry-run audit
audit = media_lifecycle_manager.monthly_audit(dry_run=True)

# Restore a specific file if needed
media_lifecycle_manager.restore_file("q_1731245000_a1b2c3d4")

# Or restore all quarantined files
media_lifecycle_manager.restore_all()
```
