# Stage 3.32F: YouTube Community Publishing (Capability Audit, Fail-Closed Publishing, Manual Handoff)

**Status:** implemented and tested in the development worktree; **not committed, not deployed**.
- API publishing is **OFF** and cannot be switched on for this project today: the capability audit found `PILOT_ACCESS_REQUIRED`.
- The working path is the **manual handoff**.

## 1. Capability audit of this channel and project (2026-11-20, read-only)

| Item | Finding |
|---|---|
| Google Cloud project | `crazzydocs-youtube` (OAuth client type: installed app) |
| Granted OAuth scopes (read from the protected token, values never printed or stored) | `youtube.upload`, `youtube.readonly` |
| `channels.list?part=id&mine=true` | succeeded; channel id resolved |
| `posts.list?part=id,snippet,status&channelId=…&maxResults=1` | **HTTP 404 `notFound`: "Method not found."** |
| **Outcome** | **`PILOT_ACCESS_REQUIRED`** (inferred, see below) |
| Additional blocker | `OAUTH_SCOPE_INSUFFICIENT`: the token holds no `posts.insert` scope (`youtube`, `youtube.force-ssl` or `youtubepartner`) |

**Why it is inferred:**
- `posts.list` accepts `youtube.readonly`, and the token holds that scope.
- So a 404 "Method not found" means the method is not exposed to this project. That is consistent with the documented restriction: "pilot phase with access limited to select launch partners (approved Google Cloud projects)".
- Google does not document the exact error a non-pilot project receives, so the classification is marked `inferred: true`.

**How the audit ran:**
- **Cost:** two read-only calls, `channels.list` (1 unit, default quota) and `posts.list` (1 unit, separate "Post List Queries" quota).
- **Nothing written to production:** the access token was refreshed in memory only; `backend/token.json` was not modified. The result was stored in a scratch copy of the database, not the live one.
- **Not done:** no upload, insert, update or delete, and no new scope requested.

**Outcome classification** (`publishing.classify`):

| Outcome | Rule |
|---|---|
| `API_AVAILABLE` | `posts.list` succeeded **and** the token holds a `posts.insert` scope |
| `OAUTH_SCOPE_INSUFFICIENT` | `posts.list` succeeded but there is no insert scope; or `insufficientPermissions` / `ACCESS_TOKEN_SCOPE_INSUFFICIENT` |
| `API_NOT_ENABLED` | `accessNotConfigured` / `SERVICE_DISABLED` / "has not been used in project" |
| `PILOT_ACCESS_REQUIRED` | any other 403 or 404 from `posts.list` (inferred) |
| `CHANNEL_PERMISSION_INSUFFICIENT` | `channelNotFound`, or `channels.list` refused |
| `UNKNOWN` | not connected, quota or rate limit, timeout, network error, 5xx, or anything unexpected: fails closed |

Every check is stored in `community_capability_checks` (the last 50 are kept), with the redacted diagnosis and **no credential**, and it adds a `PUBLISHING_CAPABILITY_CHECKED` event to the audit trail.

## 2. Official API facts (developers.google.com/youtube/data/reference/rest/v3/posts, read 2026-11-20)

| Method | Endpoint | Scopes | Quota |
|---|---|---|---|
| `posts.list` | `GET https://www.googleapis.com/youtube/v3/posts` (`part`; exactly one of `channelId` or `postId`; `maxResults` 1–50) | `youtube.readonly`, `youtube`, `youtube.force-ssl`, `youtubepartner` | 1 unit, **Post List Queries** |
| `posts.imageUpload` | `POST https://www.googleapis.com/upload/youtube/v3/posts/image?channelId=…` (raw image; returns `imageUploadToken`) | `youtube.upload`, `youtube`, `youtube.force-ssl`, `youtubepartner` | 1 unit, **Post Modifications and Image Uploads** |
| `posts.insert` | `POST https://www.googleapis.com/youtube/v3/posts?part=snippet[,attachments]` (`snippet.channelId`, `snippet.textDisplay`, `attachments.imageAttachments[].uploadToken`) | `youtube`, `youtube.force-ssl`, `youtubepartner` | 1 unit, **Post Uploads** |
| `posts.update` | `PUT …/posts`: text only ("You can't add, remove, or replace a post's images") | same as insert | 1 unit, Post Modifications |
| `posts.delete` | `DELETE …/posts?id=…` | same as insert | 1 unit, Post Modifications |

All three post quotas are separate from the default Data API quota, so posts never compete with video uploads.

**Pilot:**
- "This method is in a pilot phase with access limited to select launch partners (approved Google Cloud projects). Broader public API access is planned for General Availability in 2027."
- **No public application process is documented.** A US channel location does not grant access.

**Images:** 1–10 per post; at most 16 MB each; JPEG, PNG, GIF or WebP; aspect ratio between 2:5 and 5:2. Upload tokens are single-use. Our 1080 × 1080 JPEGs comply.

**Not stated in the documentation:** the exact `uploadType` for `imageUpload`. The client sends `uploadType=media`, which must be confirmed on first real use.

**Errors (insert):** `invalidMetadata`, `invalidImageUploadToken`, `forbidden`, `rateLimitExceeded`, `channelNotFound`.

## 3. Draft generation, approval and publication time

| Moment | What it is | How it works |
|---|---|---|
| **Draft generation time** | When CrazzyDocs creates a draft | Timezone-aware daily schedule (Stage 3.32E) |
| **Approval time** | When you approve the draft | Manual, in the dashboard. **Approval never publishes** |
| **Publication time** | When the post goes public on YouTube | API: immediately when you press "Publish now via API" (only if every gate passes). Manual: when you press Post in YouTube, or at the time you schedule there |

**Scheduling:** "Posts that you create with this method are public and are published immediately. Scheduled posts aren't supported."
- There is **no scheduled publishing through the API**, and no fake schedule control was built.
- Scheduling a post is done in the YouTube interface, which the manual handoff explains: the arrow next to Post → Schedule Post → date, time and time zone → Schedule.

## 4. Publishing architecture (`backend/app/community/`)

| Module | Role |
|---|---|
| `youtube_posts.py` | The only Community module with network or OAuth code. It calls only the documented endpoints above and `channels.list`. It reads the existing DPAPI-protected token and refreshes it **in memory** (never written). It classifies errors and marks a write as **ambiguous** on a timeout, a dropped connection or a 5xx. |
| `publishing.py` | Capability audit, gates, publication state machine, reconciliation, operator resolution, manual handoff. Its tables: `community_capability_checks` and `community_publications`. |
| `service.py` | Approved content is frozen while a publication is live or reported (edit and regenerate get 409). Draft generation never calls publishing. |

**Gates** (all re-checked at publish time; any failure means HTTP 423 and no YouTube call):
1. `publishing_enabled`: OFF by default. It can only be switched on through `POST /api/community/publishing/enabled` with `confirm`, and only when the latest capability check is `API_AVAILABLE` and less than 7 days old. The schedule endpoint cannot set it.
2. The capability check is `API_AVAILABLE`.
3. The capability check is fresh (7 days).
4. No kill switch is active (Community switch, Community sentinel or global sentinel).
5. The draft is `APPROVED`, and the request carries its current `version`.
6. The draft's text fingerprint and the image SHA-256, **re-hashed from disk**, equal the values recorded in the `DRAFT_APPROVED` event. The image is at most 16 MB.
7. `confirm=true` on the request, because the post is public and immediate.
8. At most 2 API publications per local day.

**Publication states:**

```
REQUESTED → IMAGE_UPLOADED → INSERTING → PUBLISHED
                │                 ├→ FAILED     (definitive error: 4xx, quota)            → explicit retry only
                └→ FAILED         └→ UNCERTAIN  (timeout / dropped / 5xx after sending)   → NEVER retried
UNCERTAIN → reconcile (posts.list: same text, published after the request) → PUBLISHED
UNCERTAIN → operator resolves: published (optional post id) | not_published → FAILED → explicit retry
manual:  HANDOFF_PREPARED → MANUAL_REPORTED_POSTED (operator-reported, not verified) | HANDOFF_CANCELLED
```

**Duplicate prevention:**
- `idem_key = api:<draft>:<text fingerprint>:<image sha256>` is UNIQUE.
- A **partial unique index** allows at most one live API publication per draft (REQUESTED, IMAGE_UPLOADED, INSERTING, UNCERTAIN or PUBLISHED).
- Every state change is a compare-and-set inside `BEGIN IMMEDIATE`.
- An image upload token is used once; a retry uploads again, as the documentation requires.

**What is recorded:**
- On the publication row: request metadata (channel, text fingerprint, image hash, send time), the response (post id, status, publish time), the post id, the post URL, attempts and the error (reason, HTTP status, stage).
- In the append-only audit trail: `PUBLISH_REQUESTED`, `PUBLISH_SUCCEEDED`, `PUBLISH_FAILED`, `PUBLISH_UNCERTAIN`, `PUBLISH_RECONCILED`, `PUBLISH_RESOLVED`, `MANUAL_HANDOFF_PREPARED`, `MANUAL_POST_REPORTED`, `MANUAL_HANDOFF_CANCELLED`, `PUBLISHING_ENABLED`, `PUBLISHING_DISABLED` and `PUBLISHING_CAPABILITY_CHECKED`.
- **Never recorded:** tokens and other credentials.

**Automation never publishes.** The scheduler tick only generates drafts; publishing is always a separate, explicit, confirmed action. The 3.32B autonomy registry keeps `youtube.community.publish` PROHIBITED.

## 5. Manual handoff (works today)

- **Preparing it:** `POST /api/community/drafts/{id}/handoff` (APPROVED drafts only, and only if the content still matches the approval) returns:
  - the approved text, the image URL and a download URL (`?download=1`), and `https://www.youtube.com/`;
  - the official steps: Create → Create post (or the channel's Posts tab), paste the text, add the image, then Post, or the arrow → Schedule Post → date, time and time zone → Schedule;
  - the note "Nothing has been published".
- **Repeating it** returns the same handoff for the same content, so no duplicate record is created.
- **Afterwards:**
  - "I posted it manually" (`/manual-posted`, with an optional `https://www.youtube.com/post/<id>` link) records `MANUAL_REPORTED_POSTED`, explicitly **operator-reported and not verified**. No post id is invented.
  - The handoff can also be cancelled.

## 6. Operator attribution (Part 1)

- The dashboard has no user login, so the Operator field was removed from the Community tab.
- Community actions are recorded as **`local-dashboard`**: an unauthenticated request from this computer that passed the loopback, same-origin and CSRF checks. No person is identified.
- The API still accepts an optional `operator` label for scripts. It is validated (reserved machine identities refused) and recorded as `operator:<label>`, but it is **not** authentication.
- Approvals made before this change keep the label typed at the time (for example `operator:Abhiraj`).

## 7. Dashboard (Community Posts → Publishing readiness)

**What it shows:**
- API eligibility (outcome, inferred or confirmed, basis, additional blockers);
- granted scopes against the required ones, channel permission, project, and the last check (who, when, fresh or not);
- the API publishing state with the failing gates, and the scheduling limitation;
- approved drafts awaiting publication, each with **Manual handoff**;
- publications and handoffs, with the post link, attempts and error. UNCERTAIN rows offer **Reconcile** and **Resolve…**.

**The "Publish now via API (public, immediate)" button is rendered only when the server reports `api_publishing_ready`**, meaning every gate passes. Today it does not exist on the page. "Enable API publishing" appears only after a fresh `API_AVAILABLE` check.

## 8. API routes added (27 Community routes in total)

| Route | Purpose |
|---|---|
| `GET /api/community/publishing` | Readiness, gates, awaiting, published, attention, handoffs (read-only) |
| `POST /api/community/publishing/capability-check` | Read-only YouTube probe |
| `POST /api/community/publishing/enabled` | `{enabled, confirm}`; enabling needs a fresh `API_AVAILABLE` |
| `POST /api/community/drafts/{id}/publish` | `{version, confirm: true}`; all gates |
| `POST /api/community/drafts/{id}/handoff` | Prepare the manual handoff |
| `POST /api/community/publications/{pid}/reconcile` | Reconcile an UNCERTAIN publication through `posts.list` |
| `POST /api/community/publications/{pid}/resolve` | `{outcome: published\|not_published, reason, post_id?}` |
| `POST /api/community/publications/{pid}/manual-posted` | `{post_url?}`: operator-reported |
| `POST /api/community/publications/{pid}/cancel-handoff` | Cancel a prepared handoff |
| `GET /api/community/images/{id}?download=1` | Image as an attachment |

All writes use loopback, same-origin, JSON and CSRF (`require_autonomy_write`), and reject unknown fields. There is no generic execution route, and no upload, update or delete route.

## 9. What it would take to activate API publishing

All of the following are outside this project's control except steps 2, 3 and 5:
1. **Pilot or GA access:** Google approves project `crazzydocs-youtube` for the posts pilot, or the API reaches general availability (planned for 2027). Until then `posts.list` answers "Method not found".
2. **OAuth:** with your explicit approval, re-consent the existing OAuth client with an insert scope (`youtube.force-ssl` is the narrowest that allows `posts.insert`). Ideally keep it in a separate token so the video upload token stays narrow. This stage changed no scope.
3. **Deploy** the code that adds it (commit, push, dashboard restart).
4. **Run the capability check.** It must return `API_AVAILABLE`.
5. **Enable API publishing** (confirmation required), then publish an approved draft with "Publish now via API" (public and immediate). Confirm the first `imageUpload` request format (`uploadType`) on that first real use.

Scheduled publication will stay manual in the YouTube interface unless Google adds an officially supported way to schedule posts through the API.
