# Stage 3.22 — Architecture Audit

## Scope and preservation

Audit of the current production scheduler, production/publishing path, SQLite ledgers, YouTube OAuth, dashboard API, email services, configuration, and relevant tests. No production was started, no OAuth or YouTube request was made, no email was sent, and no database record was modified during this audit. The pre-existing dirty worktree (Stages 3.21N–R and other work) is preserved.

## Current architecture

### Production and scheduler

- FastAPI application is `backend/app/main.py`; its lifespan initializes the SQLAlchemy SQLite schema. Embedded APScheduler startup is opt-in via `ENABLE_EMBEDDED_SCHEDULER`.
- `backend/app/scheduler/scheduler.py` selects profiles from `backend/app/scheduler/schedule_profiles.py`; code defaults to `FOUR_CYCLES_DAILY`.
- The existing pipeline performs QC before creating a `PublicationModel`. The old `auto_upload` request field can call YouTube directly, so it is important that the Stage 3.22 configuration/API continue to force manual approval rather than broaden this switch.
- The production scheduler Windows task was observed disabled and the daemon stopped during this stage. No production was started.

### YouTube OAuth and upload

- `backend/app/services/youtube_publishing_engine.py` already uses the official Google OAuth client and YouTube Data API v3 libraries listed in `backend/requirements.txt`.
- `YouTubeOAuthManager` supports installed-app OAuth, credential refresh, channel inspection, disconnection/revocation, and persistent token JSON. The existing scopes include `youtube.upload`, `youtube.readonly`, and the broader `youtube` scope.
- The engine supports private/unlisted upload, chunked resumable `MediaFileUpload`, callback progress, transient retry, local topic/hash duplicate checks, a limited remote duplicate scan, thumbnail upload, and quota tracking.
- Upload retry is process-local: resumable session URI, confirmed offset/progress, explicit authorization, and restart/recovery state are not persisted. A failed upload may be recorded, but there is no durable preflight/QC/approval transition API.
- Credential status includes filesystem paths and can include raw exception details. OAuth errors and upload errors can therefore disclose implementation or sensitive server details if returned directly by an API. Token persistence is plain JSON.

### Ledger and database findings

There are multiple existing stores and they must not be silently merged or rewritten:

1. `publishing_ledger` is the upload engine’s local YouTube ledger and duplicate/quota source.
2. `publications` is the SQLAlchemy application/pipeline queue used by existing publishing/dashboard APIs.
3. `publishing_ledger_archive` is the separate audited archive. It contains 253 historical records, all presently classified in the archive as `pending_queue` (66 private and 187 public); these records must not be replayed as current uploads.
4. `persistent_notifications` and `notification_delivery_ledger` are separate existing email/notification persistence paths.

The tables are in the existing `backend/studio.db`; do not recreate or bulk-reconcile them as part of this stage. The audit verified that YouTube ID `4DCas1e4vHI` is present in `deleted_video_audit` with visibility `private` and a Stage 3.21F deletion reason, and is absent from both publishing archives. This conflicts with the requested description of it as a private YouTube video. No remote lookup, restoration, deletion, or ledger change was attempted; this record needs operator verification before any action involving that ID.

### Notifications

- `backend/app/services/notification_service.py` implements SMTP/desktop delivery, in-memory cooldown, a JSONL audit log, and `notification_delivery_ledger`.
- `backend/app/services/email_monitoring_service.py` uses `PersistentNotificationModel` in `persistent_notifications` for pending/sent/failed email records, retry-after-restart, cycle notifications, and daily digest. `daily_publishing_summary.py` also uses this same ORM ledger.
- The Stage 3.22 integration should extend the persistent email queue/service and expose redacted history rather than invent a third notification store. SMTP settings are read from environment variables. Existing environment has SMTP host/recipient configuration; no delivery was tested or attempted.

### APIs, settings, and local access

- Existing APIs include `/api/publishing`, `/api/settings`, dashboard history/archive/recovery/activity, and production progress endpoints. `/api/publishing` mixes SQLAlchemy `publications` with an OAuth status response; no OAuth connect/disconnect, durable upload management, notification configuration/history, or local admin authentication endpoints exist.
- The application has wildcard CORS and no general authentication middleware. A Stage 3.22 control API must therefore explicitly fail closed without a locally configured control token and accept sensitive operations only from loopback clients.
- `Settings.PUBLISHING_MODE` is typed as `manual_approval`, and `backend/.env.example` contradictorily advertises `automatic_public` and enables scheduling. Correcting this template is necessary to avoid unsafe copy/paste setup; runtime publishing mode and schedule will remain unchanged.
- `backend/.env.local` was not read or copied. Only non-secret keys were selectively inspected; no publishing-mode, embedded-scheduler, or profile override was present. Secret values were not emitted.

## Stage 3.22 implementation constraints

1. Keep `manual_approval`, `FOUR_CYCLES_DAILY`, the disabled Windows scheduler, and idle workers unchanged.
2. Do not call Google OAuth, YouTube, or SMTP from implementation tests. Use injected/mocked clients and transports.
3. Keep the existing upload ledger authoritative for uploads and the existing persistent-notification model authoritative for email history; do not replay archived rows or rewrite the `publications`/archive contents.
4. Add a backup before any required SQLite structural migration and verify row counts/data are unchanged.
5. Redact OAuth, SMTP, resumable-session, and exception details from public API responses and logs.
6. Require local control authentication and explicit, per-upload approval; never enable automatic publication in this stage.
7. Report the `4DCas1e4vHI` database conflict and leave the record untouched pending operator verification.

## Baseline verification

- The working tree contained many pre-existing modified and untracked Stage 3.21 files; all were preserved.
- The DB audit used SQLite read-only mode. No `UPDATE`, `INSERT`, `DELETE`, migration, or schema change was performed.
- Current scheduler state was verified disabled/stopped; publishing mode is `manual_approval`.

