# Publishing Migration Report

## 1. Pre-Migration Backup

- **Backup File:** `D:\Agents\CrazzyDocs\backups\pre_user_controlled_publishing_backup.json`
- **Captured:** `settings=10`, `jobs=7`, `publications=0`, `video_jobs=0`, `publishing_ledger=1`
- Historical production jobs, existing YouTube video IDs, and OAuth credentials were preserved intact.

## 2. Migration & Reconciliation Behavior

- Existing `publishing_ledger` rows retain their `youtube_video_id`, `metadata_json`, `thumbnail_path`, and `visibility` without auto-publishing any private videos or altering existing scheduled release times.
- Newly completed QC-approved productions are inserted into `publishing_ledger` with `upload_status='pending_queue'`, `visibility='private'`, and `scheduled_timestamp=NULL` so they remain in `Ready to Schedule` until the user explicitly schedules or publishes them.
- `CrazzyDocs-Health-Dashboard` and `CrazzyDocs-Production-Scheduler` were restarted onto the updated codebase.
