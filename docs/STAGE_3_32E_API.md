# Stage 3.32E: API

All routes are under `/api/community`. They follow the existing autonomy API conventions.

| Concern | Rule |
|---|---|
| Reads | `require_local_control`: loopback clients only (403 otherwise) |
| Writes | `require_autonomy_write`: loopback, same-origin (`Origin` allow-list, `Sec-Fetch-Site`), `Content-Type: application/json` (415), and the per-process CSRF header `X-CrazzyDocs-CSRF` from `GET /api/autonomy/session` (403) |
| Bodies | Pydantic models with `extra="forbid"` (422). `operator` is **optional** (Stage 3.32F): the dashboard sends none and actions are attributed to `local-dashboard`, an unauthenticated local request that passed the loopback, same-origin and CSRF checks. A supplied label is validated (reserved machine identities such as `system`, `autonomy` and `self-healing` give 403) and recorded as `operator:<label>`; it is not authentication |
| Ids | `cdraft_<16 hex>` and `cimg_<16 hex>` are checked before any lookup (404 otherwise); no route takes a path or URL |
| Errors | `{"detail": "..."}`, at most 300 characters; unexpected errors give 503 with a generic message; secrets in exception text are redacted |
| Status codes | 400 invalid input · 403 forbidden · 404 not found · 409 conflict (stale version, invalid transition, date already has a draft, warnings not acknowledged) · 415 not JSON · 422 schema · 423 kill switch active · 429 daily limit · 503 unavailable |

There is **no publish route** and no generic execution route.

## Reads

| Method and path | Returns |
|---|---|
| `GET /api/community` | Overview: schedule status, counts (ready, approved, rejected, failed drafts, failed runs, validation warnings), active drafts, last 10 notifications, formats, `publishing: {available: false}` |
| `GET /api/community/schedule` | Config, kill switches, blocked reason, `state` (waiting / due / running / retry_pending / completed / missed / not_enabled_yet / blocked) and `state_text`, `next_run_local` / `next_run_utc`, `next_run_if_enabled_*` while blocked, `timezone_details` (abbreviation, UTC offset, DST active, next clock change), `dst_notes`, `windows` (yesterday and today), `last_result`, `last_config_change` (who, when, which settings), `enabled_since_utc`, `configured`, and `youtube_publishing: {available: false}` |
| `GET /api/community/timezones` | The 8 presets with their current abbreviation, UTC offset and DST state, all accepted IANA ids (547; fixed-offset and legacy names excluded) and the tzdata version |
| `GET /api/community/schedule/preview?timezone=&generation_time=&catch_up_hours=` | **Read-only, saves nothing.** The next three target instants (local and UTC, with any DST note) and the upcoming DST gap or overlap dates for that time. 400 on an invalid timezone or time |
| `GET /api/community/drafts?status=&q=&format=&validation=&date_from=&date_to=&limit=&offset=` | Filtered history. `status` is all, active, failed or a draft status; `q` is at most 100 characters (escaped LIKE); dates are YYYY-MM-DD; `limit` 1–200; `offset` 0–100000 |
| `GET /api/community/drafts/{id}` | Draft (generation metadata without the raw corpus), current image record, all images, the last 100 audit events, the run, and a 1,500-character research excerpt |
| `GET /api/community/drafts/{id}/validation` | Validation summary for text and image |
| `GET /api/community/drafts/{id}/image-options` | Licensed assets of the draft's topic (`asset:<sha16>`), plus `title_card` |
| `GET /api/community/images/{image_id}` | The JPEG, served only from `backend/data/community/images/<draft>/<image>.jpg`. The path is rebuilt from validated ids, symlinks are refused, and the response sends `no-store` and `nosniff` |
| `GET /api/community/history?limit=` | Recent runs and audit events (`limit` 1–500) |

## Writes (each records an audit event)

| Method and path | Body | Effect |
|---|---|---|
| `POST /api/community/schedule` | `operator` plus any of `enabled`, `paused`, `kill_switch`, `generation_time`, `timezone` (IANA), `formats`, `text_mode`, `excluded_categories`, `catch_up_hours`, `manual_daily_limit`, `max_auto_attempts` (1–3) | Validates **every** value first: one invalid value means 400 and nothing is saved. Writes only the settings that actually changed, records `CONFIG_CHANGED`, and returns the schedule status. Enabling stores the switch-on time (no retroactive run). The first save creates the tables. Publishes nothing, approves nothing, never touches the production schedule |
| `POST /api/community/generate` | `operator` | Generates today's draft now (409 if one is active or running; 423 when killed; 429 over the limit) |
| `POST /api/community/drafts/{id}/edit` | `operator`, `version`, `text` (≤ 2000) | Sanitises, re-validates, withdraws an approval |
| `POST /api/community/drafts/{id}/regenerate-text` | `operator`, `version` | New wording from the same research (sometimes a different format); re-validates |
| `POST /api/community/drafts/{id}/regenerate-image` | `operator`, `version`, optional `choice` (`title_card` or `asset:<sha16>` from image-options) | Next unused licensed image, or the chosen one; replaces the credit line; re-validates |
| `POST /api/community/drafts/{id}/approve` | `operator`, `version`, `acknowledge_warnings`, `reason` | `READY_FOR_REVIEW` → `APPROVED`. Non-VALID drafts need the acknowledgement and a reason (≥ 5 characters); REJECTED never. **Publishes nothing** |
| `POST /api/community/drafts/{id}/reject` | `operator`, `version`, `reason` (3–500) | `READY_FOR_REVIEW` → `REJECTED` |
| `POST /api/community/drafts/{id}/archive` | `operator`, `version`, optional `reason` | From a reviewable, decided or failed state → `ARCHIVED`; frees the date |

18 draft and schedule routes (10 reads, 8 writes). Stage 3.32F adds 9 publishing routes, 27 in total; see `STAGE_3_32F_PUBLISHING.md` §8.

## Future publishing boundary (Stage 3.32F, not implemented)

Verified on 2026-11-20 against the official YouTube Data API documentation (revision history entry of 2026-11-16; `posts` reference pages).

| Topic | What the documentation says | Consequence for CrazzyDocs |
|---|---|---|
| Availability | The `posts` resource (`list`, `insert`, `update`, `delete`, `imageUpload`) is in a **pilot**: "available only to select launch partners (approved Google Cloud projects)", with broader access "planned for General Availability in 2027" | Our Cloud project is not known to be in the pilot. 3.32F stays **blocked** until access is confirmed |
| OAuth scopes | `posts.insert`: one of `youtube`, `youtube.force-ssl`, `youtubepartner` (not `youtube.upload`). `imageUpload` also accepts `youtube.upload`. `list`: none for public posts | Our token holds `youtube.upload` and `youtube.readonly`. Publishing needs a **broader scope** through incremental consent, in a **separate token**, with explicit user approval. This stage requests nothing |
| Ownership | The authorised user must own the channel or have permission to create posts for it | Operator permission on the channel is required |
| Quota | `insert`, `imageUpload` and `list` cost **1 unit** each, in separate "Post Uploads", "Post Modifications and Image Uploads" and "Post List Queries" quotas, apart from the default Data API quota | No impact on video uploads; record them in the existing quota ledger under new buckets |
| Scheduling and visibility | "Posts that you create with this method are public and are published immediately. Scheduled posts aren't supported." | Drafts must stay local (this stage). Scheduling must be our own, and every publish is irreversible and public |
| Images | 1–10 images per post through single-use `imageUpload` tokens; ≤ 16 MB; JPEG, PNG, GIF or WebP; aspect ratio between 2:5 and 5:2 | Our 1080 × 1080 JPEGs (≤ 5 MB) already comply |
| Text | No length limit is stated | We cap at 500 characters |
| Errors | `invalidMetadata`, `invalidImageUploadToken`, `forbidden`, `rateLimitExceeded`, `channelNotFound` (insert) | `insert` is not idempotent: reconcile with `posts.list` before any retry |

**3.32F prerequisites:**
1. Pilot or GA access confirmed.
2. User approval of the broader scope, held in a separate token file.
3. A T3 action with per-post approval.
4. A kill-switch check before `imageUpload` and `insert`.
5. Reconcile-before-retry.
6. 30 clean days of 3.32E drafts.
