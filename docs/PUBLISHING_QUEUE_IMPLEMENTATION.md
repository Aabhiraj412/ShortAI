# Publishing Queue Implementation Report

## 1. Backend Data & Service Layer

- **`backend/app/services/youtube_publishing_engine.py`**
  - `PublishingDatabase.record_video_uploaded(ledger_id, video_id)`: Persists `youtube_video_id` immediately after `videos().insert()` succeeds (before thumbnail upload), preventing duplicate video uploads on thumbnail retries.
  - `PublishingDatabase.update_queue_metadata_and_schedule(...)`: Updates title, description, tags, hashtags, thumbnail path, `scheduled_timestamp`, `upload_status`, `visibility`, and `error_details`.
  - `YouTubePublishingEngine.upload_short(...)`: Skips `videos().insert()` if `youtube_video_id` is already recorded on the ledger row; classifies `quotaExceeded` (`quota_exceeded`) and `401` (`awaiting_auth`) errors cleanly.
  - `YouTubePublishingEngine.update_or_reschedule_remote_video(...)`: Updates snippet/status/thumbnail or cancels `publishAt` / flips to `public` via `videos().update()` + `videos().list()` verification without re-uploading.
  - `ui_queue_status(...)`: Maps internal states to `Awaiting Review`, `Ready to Schedule`, `Uploading`, `Scheduled`, `Publishing`, `Published`, and `Failed`.

- **`backend/app/services/youtube_service.py`**
  - `parse_ist_to_utc_iso(ist_dt_str)`: Parses `Asia/Kolkata` datetime strings, rejects past timestamps, and converts to UTC ISO-8601 (`...Z`).
  - `YouTubePublisherService`: Exposes `update_queue_item`, `edit_and_schedule`, `publish_now`, and `cancel_schedule`.

## 2. REST API Endpoints (`backend/app/main.py`)

- `GET /api/publishing/queue`
- `GET /api/publishing/queue/{ledger_id}/thumbnail-preview`
- `PUT /api/publishing/queue/{ledger_id}`
- `POST /api/publishing/queue/{ledger_id}/thumbnail`
- `POST /api/publishing/queue/{ledger_id}/schedule`
- `POST /api/publishing/queue/{ledger_id}/publish-now`
- `POST /api/publishing/queue/{ledger_id}/cancel-schedule`
- `POST /api/publishing/verify-scheduled`

## 3. Frontend UI (`backend/app/templates/dashboard.html`)

- **Publishing Queue** tab displays `Video | Production Status | Metadata | Thumbnail | Scheduled Time | YouTube Status | Actions`.
- Includes an **Edit & Schedule** modal with IST `<input type="datetime-local">`, metadata fields, and custom thumbnail upload.
