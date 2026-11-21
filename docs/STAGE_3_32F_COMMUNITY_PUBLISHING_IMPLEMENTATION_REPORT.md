# Stage 3.32F: Community publishing, implementation report (completion milestone)

Companions:
- `STAGE_3_32F_COMMUNITY_PUBLISHING_AUDIT.md` (audit and root causes)
- `STAGE_3_32F_COMMUNITY_API_ELIGIBILITY.md`
- `STAGE_3_32F_COMMUNITY_PUBLISHING_KNOWN_ISSUES.md`
- `STAGE_3_32F_PUBLISHING.md` (the original 3.32F design: gates, uncertain outcomes, manual handoff; still valid)

## 1. What was built on top of the existing 3.32F

Existing pieces that work were **kept, not rebuilt**:
- the capability audit;
- the 8 fail-closed gates;
- idempotency and compare-and-set states;
- uncertain-outcome reconciliation and resolution;
- the manual handoff;
- approval binding to the text fingerprint and image hash.

Added:

| Area | Change |
|---|---|
| **Publication authorization** (separate from approval) | `authorize(draft, version, confirm, publish_at)` creates a `QUEUED` publication bound to the approved text and image. It requires `confirm=true` and every gate. It is refused (HTTP 423, "use the manual handoff") while API publishing is unavailable. |
| **Local publishing queue** | `process_queue()` runs on the existing 5-minute Community tick, independent of draft-generation automation. It sends at most one due, operator-authorized post per tick, only after **re-checking every gate**. Otherwise it records `blocked_reason` and waits. If the draft no longer matches its approval or authorization, the authorization is **cancelled**, never sent. |
| **Bounded retries** | Quota, rate limit, 5xx and network errors become `RETRY_PENDING` with backoff of 5 min, then 30 min (at most 3 attempts per authorization), then `FAILED` with `retries_exhausted`. An insert with an unknown outcome is `UNCERTAIN` and **never retried**. |
| **Reauthorization detection** | HTTP 401, `invalid_grant`, revoked or expired refresh tokens, a missing token or an insufficient scope become `REQUIRES_REAUTHORIZATION`. Nothing is retried, and a notification is sent. **Fixed defect:** a token refresh error used to leave the row stuck in `REQUESTED`, and the request failed with a 500. |
| **Restart recovery** | A row interrupted more than 15 min ago in `REQUESTED` or `IMAGE_UPLOADED` (nothing posted; an unused image token is harmless) becomes `RETRY_PENDING`. One interrupted in `INSERTING` (may have posted) becomes `UNCERTAIN`. |
| **Cancellation** | Operator cancel for queued, retry-pending, reauthorization-pending and failed rows. An **edit, regeneration or archive cancels** pending authorizations, because approval is withdrawn. A stale-version edit cancels nothing. |
| **Duplicate prevention** | The unique index now covers queued, pending and in-flight rows (`uq_community_pub_active_api`), on top of the idempotency key and compare-and-set claims. |
| **Workflow state** | `lifecycle(draft, publication)`, one per draft: `DRAFT → VALIDATED → APPROVED → QUEUED → PUBLISHING → PUBLISHED`, plus `FAILED`, `RETRY_PENDING`, `CANCELLED`, `REQUIRES_REAUTHORIZATION`, `MANUAL_HANDOFF` and `REJECTED`. A manual handoff is never PUBLISHED; it reports `handoff_completed` and `verified: false`. |
| **Persistence** | Additive columns `publish_at_utc`, `next_attempt_at_utc`, `blocked_reason` and `authorized_by`. Migration on first use; existing rows and history are untouched. The post ID, URL and publish time were already persisted. |
| **Notifications** | New: approved and awaiting publication, **manual handoff required** (on approval while the API is unavailable), published, publishing failed (with the retry time), authentication failure, and queued post cancelled. Existing: draft ready, generation failed or missed, outcome uncertain. Failures never interrupt anything; the existing preferences and daily cap apply. |
| **Dashboard** | See §3. |
| **API** | `POST /api/community/drafts/{id}/authorize-publication {version, confirm, publish_at?}` and `POST /api/community/publications/{id}/cancel {reason?}`. Same loopback, same-origin, CSRF and unknown-field protection. There are now 29 Community routes; still no upload, update or delete route. |

## 2. State transitions (publication row)

```
authorize ──► QUEUED ──(due, all gates pass, CAS claim)──► REQUESTED ─► IMAGE_UPLOADED ─► INSERTING ─► PUBLISHED
   │            │  (gate fails: stays QUEUED + blocked_reason)                 │                │
   │            └─(content/approval changed, edit, archive, cancel)─► CANCELLED              ├─► UNCERTAIN (never retried;
publish now ─► REQUESTED                                                                     │     reconcile / resolve)
                 any stage: quota/rate/5xx/network ─► RETRY_PENDING ─(backoff, gates)─► REQUESTED …  (max 3 attempts → FAILED)
                 any stage: 401 / revoked / no token / scope ─► REQUIRES_REAUTHORIZATION (stop; cancel or publish now later)
                 definitive 4xx ─► FAILED (explicit retry or handoff)
restart: stale REQUESTED/IMAGE_UPLOADED ─► RETRY_PENDING;  stale INSERTING ─► UNCERTAIN
manual:  HANDOFF_PREPARED ─► MANUAL_REPORTED_POSTED (operator-reported) | HANDOFF_CANCELLED
```

**Approval** (`DRAFT_APPROVED`) and **authorization** (`PUBLICATION_AUTHORIZED` or an explicit publish-now) are separate audited events. Every transition is in the append-only `community_events`.

## 3. Dashboard (Community Posts)

**Generation:**
- **Fixed the schedule-editor inconsistency.** The editor is disabled until the saved settings load. The first load always fills it, and Save is refused before then.
- The status panel now shows **Paused** and **Kill switch** rows, next to automation, timezone, target time, next and last generation, and outcome.

**Draft workspace:**
- Each draft card shows its workflow badge (APPROVED reads "approved, NOT published"; PUBLISHED reads "PUBLISHED on YouTube").
- Validation status, plus created and modified times. Image provenance and licence were already shown.

**Publishing readiness:** eligibility, scopes, channel, project, last check and gates (existing).

**Publishing queue (new):**
- counts per workflow state;
- a table of queued, publishing, retry-pending, reauthorization-pending, failed and published posts, with time, attempts, the waiting reason or error, and the post link;
- Cancel for pending rows;
- the retry policy.

**Authorize for a later time** appears beside "Publish now" **only** when the server reports every gate passing, which is not the case today.

## 4. Safety

- **Nothing was published.** No OAuth scope or credential changed, and no consent flow was started.
- Autonomy can still never publish (`youtube.community.publish` PROHIBITED).
- **Untouched:** draft generation and the schedule (17:00 Asia/Kolkata, enabled); existing drafts, approvals and events; the 24-cycle production schedule; the renderer.
- The queue only ever sends posts an operator authorized one by one, and only while API publishing is explicitly enabled. That cannot happen today.

## 5. Tests

| Suite | Result |
|---|---|
| `test_community_publishing_queue_332f.py` (new: lifecycle, authorization, publish-time validation, duplicates, queue gates, edit and stale-edit cancellation, tampered content, operator cancel, bounded retries with backoff, transient recovery, uncertain never retried, auth failures, missing credentials, token-refresh error typing, restart recovery, daily limit, tick integration and isolation, notifications, status separation, migration, dashboard editor) | 39 passed |
| `test_community_publishing_332f.py` (one test updated: quota and timeout are now `RETRY_PENDING` with a next attempt time, instead of `FAILED`) | passed |
| `test_community_posts_332e.py` (route allowlist extended by exactly the two new routes) and `test_community_schedule_tz_332e.py` | passed |
| Full suite | see `STAGE_3_32E_TEST_RESULTS.md` §15 |

## 6. Controlled validation (isolated world, mocked YouTube)

1. A draft was generated (VALIDATED).
2. It passed validation (VALID).
3. It was approved (APPROVED), with **no YouTube call**.
4. Authorization was **refused while publishing is off**. With a mocked `API_AVAILABLE` and publishing enabled, it was QUEUED for +1 h, and the queue waited.
5. The queue published it at the time: PUBLISHED, with the post ID and URL recorded.
6. A quota failure became RETRY_PENDING, and the automatic retry published it on attempt 2.
7. The manual handoff was prepared, and the workflow state shows MANUAL_HANDOFF (never PUBLISHED).

Notifications were produced along the way.

## 7. Operator actions to enable public API publishing (all need your authorization)

1. Obtain posts API pilot access for project `crazzydocs-youtube`, or wait for general availability (2027).
2. Authorize an OAuth re-consent adding `youtube.force-ssl`, preferably as a separate Community token.
3. Run the capability check. It must return `API_AVAILABLE`.
4. Enable API publishing (confirmation required).
5. For each post: **Publish now** or **Authorize for a later time** (confirmation required).

Until then, approve the draft and use **Manual handoff**: copy the text, download the image, and post or schedule it in YouTube.

## 8. Live verification after deployment

Recorded in the final report of this milestone:
- the read-only capability check outcome;
- drafts, approvals, the schedule and OAuth scopes unchanged;
- no publication rows sent.
