# Stage 3.22 Readiness Audit: YouTube Publishing, OAuth, Notifications, Recovery

| | |
|---|---|
| Audited commit | `d5a3515` (HEAD of `main`, verified 2026-11-13 15:51 IST) |
| Method | Code reading; read-only SQLite (`mode=ro`); logs; offline library checks |
| Not done | No uploads, YouTube writes, OAuth changes, policy changes, DB writes, restarts or test emails |
| Live state at audit time | Policy `automatic_private_scheduled`. Ledger has 1 row (#284 `gPcd3gXF984`, public). Nothing running. Cycles 1–3 today are terminal; Cycle 4 is due 18:00 IST |

Classifications: **COMPLETE**, **PARTIAL**, **MISSING**, **BLOCKED**, **NEEDS_REAL_WORLD_VALIDATION** (abbreviated **NRWV**).

---

## 1. Executive summary

The publishing backend works and is largely safe by design:
- uploads are private-only and start only after QC;
- each video's YouTube ID is saved as soon as YouTube returns it;
- duplicates are guarded in three layers;
- interrupted uploads can resume;
- scheduling, verification and thumbnail retries each run on their own, and none of them uploads the video again.

The automatic path that the live policy now uses (`automatic_private_scheduled`) has never completed against real YouTube. Every scenario is covered only by mocks. Until 15:40 today, every YouTube API call on this machine was failing: IPv6 to Google is dropped, and httplib2 never falls back to IPv4. That was fixed in `d5a3515`.

Gaps that matter before trusting it unattended:
1. **Credentials are rewritten on every load.** The dashboard and scheduler race on the same token file.
2. **Interrupted-upload recovery runs only when the dashboard starts.** Uploads happen in the scheduler.
3. **Remote duplicate detection matches on title substrings.** A false match makes a new video look like an old one; the item goes to NEEDS_REVIEW and is never uploaded twice.
4. **Rescheduling an uploaded video needs the full `youtube` scope**, which was never granted.
5. **Emails misreport auto-published cycles** as "withheld at the safety gate". Several notification types are missing.
6. **No HTTP timeout is set on YouTube API calls.** A hung connection can stall the worker indefinitely.

**Verdict: PARTIAL.** It's safe against duplicates and accidental public publishing. It isn't yet proven or fully recoverable for unattended daily operation.

---

## 2. Feature classification

### 2.1 OAuth infrastructure
| Feature | Status | Evidence / notes |
|---|---|---|
| Authorization (installed-app flow, `access_type=offline`, `prompt=consent`) | COMPLETE | `start_interactive_auth`; no automated test exists (it needs a browser) |
| Refresh-token persistence | COMPLETE | Token holds a refresh token; `to_json()` keeps it; failed verification never writes credentials (tested) |
| Access-token refresh | COMPLETE | google-auth via `requests`, which falls back across addresses, so it worked even while IPv6 was broken |
| Credential storage | PARTIAL | Encrypted with Windows DPAPI and replaced atomically. **Bug:** `_load_credentials` checks `data.lstrip().startswith(b"{")` on already-*decrypted* bytes, so it re-encrypts and rewrites `token.json` on every load: about 44 writes in 12 min from dashboard polling. `_load_client_config` has the same pattern |
| Token recovery after reboot | COMPLETE | Survived the 13:19 IST reboot. DPAPI is per-user, so it depends on the Task Scheduler task running as the same Windows user |
| IPv4 preference for Google API hosts | COMPLETE | `core/network_preferences.py`, installed when the YouTube module loads in both processes. Live: 0.6 s via IPv4 vs TimeoutError after 21.4 s without it |
| Channel verification | COMPLETE | `channels.list(mine=True)`. Since `d5a3515` the real error is kept (`error_detail`) and quota and network failures are separated. A multi-channel account silently uses the first channel |
| Scope management | PARTIAL | Granted: `youtube.upload` and `youtube.readonly`. `videos.update` (reschedule, metadata edits) needs `youtube` or `youtube.force-ssl`; see §2.4 |
| Authentication status caching | COMPLETE | 300 s on success, 60 s on failure; shared by concurrent callers; cleared on connect, disconnect and secrets install; dashboard-only |
| Dashboard/scheduler credential consistency | PARTIAL | Both read and refresh the same file with no inter-process lock. Combined with the rewrite bug, a Windows `os.replace` during a read can raise, which a status read reports as `credential_load_failed` (and asks for reconnection) |

### 2.2 Upload pipeline
Path: `complete_queue_handoff` → `_auto_publish_after_qc` → `YouTubePublisherService.publish_after_qc` → `YouTubePublishingEngine.upload_short`.

| Feature | Status | Evidence / notes |
|---|---|---|
| Upload only after QC | COMPLETE | The queue entry is created only from the QC-passed handoff, and the video's fingerprint is re-checked before upload |
| Private only | COMPLETE | `publish_after_qc` passes `visibility="private"`; `upload_short` rejects public without a bypass and requires private with `publishAt` |
| Video ID saved immediately | COMPLETE | `record_video_uploaded` runs right after the final chunk returns |
| Retries never re-upload | COMPLETE | An existing `youtube_video_id` skips the upload; a later retry only does scheduling and thumbnail |
| Fingerprint duplicate protection | COMPLETE | `check_duplicate` uses the SHA-256 and title against in-progress and uploaded rows; live ledger has no duplicate IDs or fingerprints |
| Remote duplicate check | PARTIAL | Scans the latest 25 items in the uploads playlist; matches on equal title **or the topic as a substring**. Topics repeat ("Science fiction" was used three times today), so a false match routes to NEEDS_REVIEW and the video is held, never uploaded twice. Videos older than the last 25 are missed. It refuses to upload when the check itself fails |
| Interrupted uploads | PARTIAL | The resumable session URI is stored encrypted and reused, and an expired session restarts cleanly. **But** `recover_interrupted_uploads()` runs only at dashboard startup, while uploads run in the scheduler (see §5) |
| Upload failure keeps production files | COMPLETE | The upload never deletes media; the job keeps its artifacts; the ledger entry stays retryable |
| Local quota guard | PARTIAL | Counts only uploads (1,600) and thumbnails (50). `channels.list`, `playlistItems.list` and `videos.list` aren't counted, and the day is calculated in UTC, not Pacific time |
| AI-content disclosure (`status.containsSyntheticMedia`) | MISSING | Not sent. YouTube expects realistic synthetic media to be disclosed; a policy decision is needed |
| HTTP timeout on API calls | MISSING | `build()` uses a default httplib2 client with no timeout |

### 2.3 Thumbnails
| Feature | Status | Notes |
|---|---|---|
| Generation | COMPLETE | `thumb_engine.select_and_render_best_thumbnail` |
| Validation | PARTIAL | QC checks only that the file exists and is over 5 KB. YouTube's 2 MB limit, the 16:9 or 1280×720 shape, and the file type aren't checked |
| Upload | NRWV | Sent with `youtube.upload`. Custom thumbnails need a phone-verified channel; a 401/403 during upload is recorded as `eligibility_restricted` |
| Independent retries | COMPLETE | `retry_thumbnail` never touches the video; covered by `test_thumbnail_retry_never_uploads_a_second_copy` |
| Recovery without re-upload | PARTIAL | `retry_thumbnail` reports **any** error, including a permanent 403, as `thumbnail_failed`. That keeps the publish step pending until its 5 attempts run out, and the job is then marked FAILED although the video is uploaded and scheduled |

### 2.4 YouTube scheduling
| Feature | Status | Notes |
|---|---|---|
| publishAt calculation | COMPLETE | `ProductionCycle.target_publication_at`, stored in UTC when the job is claimed |
| Asia/Kolkata handling | COMPLETE | Every cycle time is in IST and converted to UTC (`…Z`) |
| Midnight rollover | COMPLETE | A target at or before the start rolls to the next day (18:00 → 00:00 next day); tested |
| 15-minute minimum lead | COMPLETE | `AUTO_MIN_LEAD_MINUTES = 15`; otherwise `NEEDS_PUBLICATION_TIME` and nothing is uploaded |
| Remote verification | COMPLETE | `verify_remote_schedule` confirms the video is private with a publishAt within 60 s of the target |
| Independent scheduling retries | **BLOCKED** | A mismatch calls `update_or_reschedule_remote_video`, which uses `videos.update`. That needs the **`youtube`** (or `youtube.force-ssl`) scope, which isn't granted, so the real call should fail with 403 `insufficientPermissions` (inferred from the API docs, not observed). Normally publishAt is set during the upload, so this path runs only on a mismatch |
| Expired target | COMPLETE | `NEEDS_PUBLICATION_TIME`; the video stays private and is never published early |
| Promotion once live | COMPLETE | `verify_scheduled_publications` runs every 5 min via `reconcile_youtube_publications`. Its failures are logged only at debug level |

**Scopes needed to update uploaded videos:** add `https://www.googleapis.com/auth/youtube` (or `youtube.force-ssl`). That means asking you to reconnect YouTube once, with consent; don't do it without explicit authorization.

### 2.5 Publishing ledger and queue
States: `pending_queue → approved → uploading → (publication_scheduled | uploaded_private | uploaded_unverified)`. Failure states: `failed`, `quota_exceeded`, `awaiting_auth`, `upload_interrupted`, `thumbnail_failed`. Completed states: `uploaded_public`, `published`.

| Check | Status | Notes |
|---|---|---|
| Duplicate entries | COMPLETE | Live: none. The handoff insert is idempotent on the video fingerprint |
| Missing video IDs | COMPLETE | Live: no uploaded row without an ID, no ID left on a pending row |
| Orphaned uploads | PARTIAL | Live: none. If the process dies after YouTube accepts the last chunk but before the response is saved, only the title-based remote check can find the video (see §2.2) |
| Local vs remote consistency | PARTIAL | Scheduled→public promotion exists. Remote deletion, or privacy changed by hand, isn't detected for non-scheduled rows. The reconciler swallows errors |
| Each step recoverable on its own | PARTIAL | Upload, schedule and thumbnail are separate steps. A row stuck in `uploading` after a scheduler crash can't be approved again (`approve_upload` rejects that status) until the dashboard restarts |
| Completion boundary | PARTIAL | The production job is `COMPLETED` only after the queue entry and publishing step finish. After 5 failed publishing attempts the **job** becomes FAILED even when the video is already uploaded or scheduled, which is misleading in the UI and emails |

### 2.6 Email notifications
| Event | Status | Where |
|---|---|---|
| Production started | MISSING | Only an observability event; no email |
| Production completed | PARTIAL | `send_cycle_summary_report`. **Bug:** the YouTube section uses `settings.PUBLISHING_MODE` (environment, default `manual_approval`) and `job.youtube_video_id`, which automatic publishing never sets. So an auto-scheduled cycle reports "Withheld at safety gate / AWAITING_MANUAL_APPROVAL" |
| Rendering failed | PARTIAL | Terminal failure is in the cycle summary with the error text; there's no render-specific type |
| Upload failed | PARTIAL | `UPLOAD_FAILED` is sent only for uploads started from the dashboard (`main.py:249`), not for the automatic path |
| Scheduling completed | MISSING | Not sent for the automatic path |
| OAuth disconnected | PARTIAL | `YOUTUBE_AUTH_PROBLEM` only when the connect flow fails, not when a token is revoked or expires during operation |
| Quota exceeded | MISSING | — |
| Recovery required (NEEDS_REVIEW, NEEDS_PUBLICATION_TIME, HANDOFF exhausted) | MISSING | — |
| Duplicate suppression and safe delivery states | COMPLETE | `persistent_notifications` with SENT, DISPATCHING and DELIVERY_UNKNOWN; never auto-resent after an uncertain SMTP timeout. Live: 9 of 9 CONFIRMED_SENT |
| Delivery retry | PARTIAL | `retry_pending_notifications()` runs **only at scheduler startup**; there's no periodic retry |

### 2.7 Failure recovery
| Scenario | Status | Behaviour |
|---|---|---|
| Windows reboot | COMPLETE | Task Scheduler → startup monitor → scheduler and dashboard; startup reconciliation; DPAPI token. Seen at 13:19 today |
| Dashboard restart | PARTIAL (risk) | On startup the dashboard marks **every** `uploading` row as `upload_interrupted`, including one the scheduler is still uploading. A manual retry from the dashboard could then start a second upload session (see §5, R2) |
| Scheduler restart or crash mid-upload | PARTIAL | The row stays `uploading`; publish retries fail with "not awaiting approval" until the dashboard restarts; the job can end FAILED. No duplicate is created |
| Worker crash | COMPLETE | The worker registry plus a heartbeat older than 180 s means stalled; tested |
| FFmpeg or render crash | COMPLETE | Render retried inside its stage (2 attempts); a deterministic failure is terminal with files kept; earlier stages cached |
| Network interruption | PARTIAL | Before dispatch it waits inside the recovery window. During upload: 3 chunk retries with backoff, then `failed` and publishing retries (5). Fine for short outages; no timeout for a connection that hangs |
| YouTube API timeout | PARTIAL | With IPv4 first, connection failures are fast. A stalled read has no timeout (§2.2) |
| Quota exhaustion | PARTIAL | `quota_exceeded` is recorded and the job is retried; retries all fail until the reset (13:30 IST). No email; the local quota counter is incomplete |
| No duplicates, no lost IDs | COMPLETE (by design) | ID saved straight away, plus fingerprint, remote check and resumable session. Residual windows are R2 and the orphan window |

---

## 3. Implemented functionality (COMPLETE)
- **OAuth:** authorization, refresh, encrypted storage, reboot survival.
- **Network:** the IPv4-first workaround.
- **Status:** detailed verification status and its cache.
- **Uploads:** private-only, after QC, ID saved straight away, fingerprint de-duplication, resumable uploads.
- **Scheduling:** publishAt computed in IST and stored in UTC, midnight rollover, the 15-minute guard, remote verification, scheduled→public promotion.
- **Thumbnails:** generation and separate retries.
- **Emails:** safe delivery states.
- **Recovery:** after a reboot, worker crash or render failure.

## 4. Partially implemented
- Credential storage and the dashboard/scheduler consistency (rewrite bug, no lock).
- Remote duplicate heuristic.
- Recovery of interrupted uploads across processes.
- Local quota accounting.
- Thumbnail validation, and the classification of permanent thumbnail errors.
- Local vs remote reconciliation.
- Completion boundary after publishing retries run out.
- Cycle-summary publishing section.
- Upload-failed and OAuth-problem alerts.
- Notification retry cadence.
- Network and quota recovery.

## 5. Missing functionality
- An HTTP timeout on YouTube API calls.
- `containsSyntheticMedia` disclosure.
- The full `youtube` scope (blocks rescheduling).
- Notifications: production started, scheduling completed, quota exceeded, recovery or review required.
- Periodic notification retry.
- Detection of videos removed or changed directly on YouTube.

## 6. Known bugs and risks (ranked)
| # | Severity | Issue |
|---|---|---|
| R1 | High | `_load_credentials` rewrites `token.json` on every load, and two processes write it with no lock. Risk: an intermittent `credential_load_failed` or a lost refresh |
| R2 | High | The dashboard's startup recovery marks the scheduler's live upload as interrupted. A manual retry from the dashboard could start a second session before the first returns its ID: a possible **duplicate video** |
| R3 | High | Rescheduling or metadata updates (`videos.update`) will fail with the current scopes |
| R4 | Medium | The cycle email reports automatic uploads as withheld (wrong mode source, job video ID never set) |
| R5 | Medium | No HTTP timeout: a stalled connection hangs the worker while its heartbeat keeps it "alive" |
| R6 | Medium | Title-substring remote duplicate match: a false NEEDS_REVIEW holds a new video (a safe failure, but it needs attention) |
| R7 | Medium | After 5 publishing attempts the job is FAILED even when the video is on YouTube |
| R8 | Medium | A scheduler crash mid-upload leaves `uploading` until the dashboard restarts |
| R9 | Low | Local quota counter misses read calls and uses the UTC date. Dashboard polling likely used up the 10k quota on 12–13 Nov (16,277 `quotaExceeded` warnings); the status cache now limits that |
| R10 | Low | Thumbnail QC doesn't check size or dimensions |
| R11 | Network | IPv6 to Google is dropped on this network; the app works around it, but the network itself still has the fault |

## 7. Security review
- **Strong:** tokens and client secrets encrypted with DPAPI; resumable session URIs encrypted; publishing and OAuth APIs localhost-only with explicit `confirm: true`; public uploads blocked without an explicit bypass; secrets redacted in logs and emails; no token printed anywhere in this audit.
- **Concerns:**
  - The token is rewritten constantly (R1), which widens the window for a partly written file to be read.
  - `disconnect_channel` revokes remotely in the best case and removes the file even if revocation failed. That's acceptable, but it's silent.
  - `install_client_secrets` accepts any filesystem path given by a localhost caller; low risk, local only.
  - The DPAPI token is tied to the Windows user, so changing the user the task runs as breaks authentication silently.

## 8. Test coverage
- **Mocked YouTube tests (about 120 in about 20 files):**
  - private upload with publishAt, the 15-minute guard, expired target, no re-upload on scheduling or thumbnail retry;
  - remote duplicate leading to NEEDS_REVIEW, resumable URI reuse, quota and auth error mapping;
  - verification classification and the IPv4 preference;
  - dashboard cache, email idempotency, the retry policy, the worker registry, schedule rollover.
- **Gaps against real YouTube:**
  1. No test has ever run the automatic path against real YouTube: insert with publishAt, processing delay, thumbnail eligibility, publishAt accepted.
  2. `videos.update` scope behaviour is assumed in mocks (mocks accept any call).
  3. Resumable resume uses `req._in_error_state = True`, a private googleapiclient attribute; only mocked.
  4. Concurrency between the two processes (R1, R2) isn't tested.
  5. `retry_thumbnail`, `start_interactive_auth` and `containsSyntheticMedia` have no tests.
  6. Mocked `playlistItems` returns tidy titles; real titles are truncated and edited.
- **Suite at `d5a3515`:** 513 passed, 4 failed (the same 4 failed before; unrelated dashboard branding and orchestration tests), 173 skipped (mostly live-only).

## 9. Dependencies and API scopes
- **Libraries:** google-api-python-client 2.202.0 (httplib2 0.32.1 transport), google-auth 2.62.2, google-auth-oauthlib, SQLite, Windows DPAPI.
- **Scopes granted:** `youtube.upload`, `youtube.readonly`.
- **Scopes needed for full Stage 3.22:** add `youtube` (or `youtube.force-ssl`) for `videos.update`.
- **API calls and quota units:**

| Call | Units |
|---|---|
| `videos.insert` | 1,600 |
| `thumbnails.set` | 50 |
| `videos.list` | 1 |
| `channels.list` | 1 |
| `playlistItems.list` | 1 |
| `videos.update` | 50 |

- **External requirements:** a phone-verified channel for custom thumbnails, the API enabled in the Cloud project, and a 10,000 units/day default quota (resets 00:00 Pacific = 13:30 IST, or 12:30 IST during US daylight saving).

## 10. Recommended implementation sequence
1. **R1 credentials:** compare the *raw* file bytes to decide whether migration is needed; load without rewriting; add a lock-file around refresh and save shared by both processes; tests with two concurrent processes.
2. **R2 and R8 upload ownership:** record the owner (PID or worker id plus heartbeat) on `uploading` rows. Mark rows interrupted only when the owner is dead, and run that recovery in the **scheduler** as well. Refuse a dashboard retry while the owner is alive.
3. **R5 timeouts:** build the API client with an `httplib2.Http(timeout=…)` and give `videos.insert` chunk calls a bounded timeout.
4. **R4 and notifications:** take the publishing mode from the control-center policy and the video ID from the ledger or `cycle_state.publishing`. Add `SCHEDULING_COMPLETED`, `QUOTA_EXCEEDED`, `RECOVERY_REQUIRED` and runtime `YOUTUBE_AUTH_PROBLEM`. Retry pending notifications periodically.
5. **R7 completion:** if the video is uploaded and scheduled but only the thumbnail is pending, finish the production job as COMPLETED and track the publishing issue separately. Treat a permanent thumbnail 403 as `eligibility_restricted`.
6. **R6 remote duplicate:** match on exact title plus upload time near the claim. Store a fingerprint in the description or tags to identify our own uploads.
7. **R3 scope:** with your authorization, reconnect once adding `youtube.force-ssl`; then enable the real reschedule path.
8. Thumbnail QC limits, quota accounting (all calls, Pacific date), and a `containsSyntheticMedia` decision.
9. **Real-world validation:** one supervised automatic cycle end to end, then 3 unattended days.

## 11. Explicit completion criteria for Stage 3.22
1. One real automatic cycle results in a private video with the correct publishAt, the thumbnail set or explicitly restricted, the ledger `publication_scheduled`, the job COMPLETED, and an accurate email.
2. That video goes public by itself at the target time and the ledger moves to `uploaded_public` within 10 min.
3. Killing the scheduler mid-upload, then restarting both processes, completes the upload or resumes it with exactly **one** remote video and its ID saved (tested with mocks, plus one supervised real check).
4. A dashboard restart during an upload never changes that upload's state or starts a second session.
5. The token file is written only on a real refresh or reconnect, and running both processes together for 24 h shows no `credential_load_failed`.
6. Quota exhaustion, auth loss, NEEDS_REVIEW and NEEDS_PUBLICATION_TIME each send exactly one email.
7. No YouTube call can block for more than its timeout; the worker becomes stalled or failed instead of hanging.
8. All new tests pass. The full suite has no failures beyond the 4 already known.
9. Three consecutive days of unattended cycles with zero duplicate videos, zero lost IDs and zero early public publications.

---

## Top five remaining tasks (by importance)
1. **Fix credential rewriting and add a cross-process lock (R1).** It affects every YouTube operation in both processes.
2. **Make upload recovery owner-aware and run it in the scheduler (R2 and R8).** This is the only remaining path to a duplicate video.
3. **Add HTTP timeouts to YouTube API calls (R5).** A hang currently stalls production silently.
4. **Fix the cycle email's publishing section and add the missing alerts (R4 and §2.6).** Otherwise you won't know whether automatic publishing worked.
5. **Supervise one real automatic cycle end to end** (Cycle 4 today is the first chance), then decide on the `youtube.force-ssl` scope for rescheduling (R3).

Stage 3.23 shouldn't start until this audit has been reviewed.
