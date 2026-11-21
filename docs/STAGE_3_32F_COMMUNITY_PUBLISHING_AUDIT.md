# Stage 3.32F: Community publishing audit (2026-11-21/22)

## 1. Workflow as built

| Step | Implementation | Status |
|---|---|---|
| Scheduled draft generation | `CommunityService.tick()`, every 5 min (`community_draft_tick`, `2-59/5`), timezone-aware target, catch-up window, at most N automatic attempts per day | Works |
| Manual generation | `generate_manual` (limit per day; refused while today has an active draft or a running generation) | Works |
| Validation | `validation.py`: VALID, VALID_WITH_WARNINGS, NEEDS_OPERATOR_REVIEW, REJECTED | Works |
| Approval and rejection | `approve` (READY_FOR_REVIEW only; warnings need an acknowledgement and a reason) and `reject`; append-only `community_events` | Works |
| Approved-draft storage | `community_drafts` and `community_images` (licence, attribution, SHA-256); the approval event binds the text fingerprint and image hash | Works |
| Edit and regenerate | Withdraw an approval (back to validation); refused while a publication is in flight or done | Works |
| Manual handoff | `prepare_handoff`, `report_manual_post` (operator-reported, not verified), `cancel_handoff` | Works |
| YouTube posts API integration | `youtube_posts.py` (documented endpoints only), `publishing.py` (8 fail-closed gates, compare-and-set states, uncertain outcomes never retried) | Built; **cannot run**, see §2 |
| OAuth and channel | Existing DPAPI token, refreshed in memory only; `channels.list` | Works (read-only) |
| Queue, retries, history | Explicit retry only; no queue, no automatic retry, no reauthorization state | **Gap**: completed in this milestone |
| Dashboard and notifications | Publishing-readiness card; notifications for draft ready, generation missed or failed, and uncertain outcomes | Partial: completed in this milestone |

## 2. Why an approved draft does not reach YouTube

Root causes, in order:

1. **No posts API access.** The posts resource is a pilot limited to approved Google Cloud projects. `posts.list` from this project returns HTTP 404 "Method not found" (`PILOT_ACCESS_REQUIRED`). This is external, and there is no documented application.
2. **No publishing scope.** The token holds `youtube.upload`, `youtube.readonly` and `yt-analytics.readonly`. `posts.insert` needs `youtube`, `youtube.force-ssl` or `youtubepartner` (`OAUTH_SCOPE_INSUFFICIENT`). Changing that needs your authorization.
3. **API publishing is OFF by design.** It can only be enabled after a fresh `API_AVAILABLE` check, which causes 1 and 2 make impossible today.
4. **The production database never had a capability check.** The 3.32F audit on 2026-11-20 ran against a scratch copy, so the live dashboard said "never checked". A read-only check is run after this deployment.

None of this is a software defect: approval correctly never publishes. The working path today is the **manual handoff**.

## 3. Scheduling audit (live records)

| Item | Finding |
|---|---|
| **2026-11-20 `SKIPPED`** | **Correct.** At 16:51 IST a *manual* draft was generated for 2026-11-20 (`cdraft_00eeba9275a0990d`). Automation was enabled at 19:43, and the target was set to 20:00 at 19:44. At 20:02 the scheduled run found an active draft for that date and recorded `SKIPPED` ("a draft for this date already exists"). That is duplicate prevention working. |
| 2026-11-21 | The target was changed from 20:00 to 17:00 by `local-dashboard` at 16:43 IST. At 17:02 the scheduled run generated `cdraft_9bfbeaddf191e5a1` (VALID, READY_FOR_REVIEW) and sent a draft-ready notification. **It ran on time.** |
| Manual versus scheduled | Manual generation for a date makes that date's scheduled run skip, by design. It never causes a second draft. |
| Catch-up, attempts, duplicates | Covered by the existing tests (missed windows outside catch-up are recorded `SKIPPED`/missed, with a notification). No live missed window occurred. |
| **"Kill switch shown as paused"** | **Confirmed display defect, not a state defect.** Stored state is consistent: enabled, not paused, no kill switch, no sentinel files. But when the Community tab opens, the schedule editor shows **blank defaults** (automation unchecked, kill switch unchecked, time empty) until `/api/community` answers, several seconds. It also **never populated** if focus was inside the editor during that load. The status panel had **no kill-switch row**, so the views could disagree. Saving a form that never loaded would have written the defaults, turning automation off. **Fixed:** the editor stays disabled ("Loading the saved settings...") until the first load; that load always populates; Save is refused before it; the status panel shows Paused and Kill switch rows. |

The schedule (17:00 Asia/Kolkata, enabled) and all drafts, approvals and events were left unchanged.

## 4. Gaps against the milestone (all addressed)

- No separate **publication authorization** or local queue for a later time.
- No automatic **retries with backoff**; quota and network errors became FAILED.
- **Expired or revoked credentials** were not detected. A google-auth `RefreshError` or a missing token during publishing was not converted to an API error, so the row stayed stuck in `REQUESTED` and the request failed with a 500.
- No recovery of rows interrupted by a **restart** mid-flight.
- No single **workflow state** per draft. APPROVED, QUEUED and PUBLISHED were not clearly distinguished.
- Missing **notifications**: approved and awaiting publication, manual handoff required, publish success or failure, authentication failure.
- Stale dashboard and API texts ("Publishing time is a future capability").
