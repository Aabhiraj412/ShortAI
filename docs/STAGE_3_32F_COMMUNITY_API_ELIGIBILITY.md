# Stage 3.32F: YouTube Community Posts API eligibility

Re-verified 2026-11-22 against the official documentation (developers.google.com/youtube/v3/docs/posts, `posts/insert`, `posts/imageUpload`, `posts/list`; all pages "Last updated 2026-11-16 UTC"). Live check: see §4.

## 1. Summary

| Question | Finding | Evidence class |
|---|---|---|
| Is the posts API available to this project? | **No.** `posts.list` answered HTTP 404 "Method not found". | **Pilot-program restriction** (inferred from the 404 plus the documented pilot) |
| Is there an application process? | None documented. "Pilot phase with access limited to select launch partners (approved Google Cloud projects)"; general availability is planned for 2027. | Verified (documentation) |
| Required OAuth scope for `posts.insert` | One of `youtube`, `youtube.force-ssl`, `youtubepartner` | Verified (documentation) |
| Scopes the current token holds | `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly` | Verified (token, names only) |
| Does the current token support publishing? | **No.** No insert scope. | **Missing permission** |
| Image upload scope | `youtube.upload` is accepted by `posts.imageUpload` (already held) | Verified (documentation) |
| Channel permission | `channels.list mine=true` resolves the channel for the connected account | Verified (live check) |
| Google Cloud project configuration | YouTube Data API v3 is enabled (`channels.list` works); the project is not a posts pilot partner | Verified (enabled) / inferred (pilot) |
| Can access be checked without new permissions? | **Yes.** `posts.list` accepts `youtube.readonly`; the capability check is read-only (2 calls, 1 unit each) | Verified |
| Post formats | Text posts and image posts (1–10 images, ≤16 MB, JPEG/PNG/GIF/WebP, aspect 2:5 to 5:2). No polls, quizzes or video posts in the API. | Verified (documentation) |
| Scheduling | **Immediate only.** "Scheduled posts aren't supported." (The resource schema lists a `scheduled` publishing status for owner-visible posts, but there is no way to create one.) | Verified (documentation) |
| Quota | 1 unit each, in separate post buckets (Post Uploads; Post Modifications and Image Uploads; Post List Queries), not the default video quota | Verified (documentation) |

## 2. Classification used by the capability check

| Outcome | Meaning |
|---|---|
| `API_AVAILABLE` | `posts.list` works **and** the token holds an insert scope |
| `OAUTH_SCOPE_INSUFFICIENT` | `posts.list` works but there is no insert scope (**missing permission**) |
| `PILOT_ACCESS_REQUIRED` | 403 or 404 from `posts.list` (**pilot restriction**, marked *inferred*) |
| `API_NOT_ENABLED` | Data API disabled for the project (**configuration problem**) |
| `CHANNEL_PERMISSION_INSUFFICIENT` | channel not found or not manageable |
| `UNKNOWN` | network, quota or anything unexpected (**unverified**; fails closed) |

## 3. What it would take

All of these need your explicit authorization, and the first one is outside our control:

1. **Pilot access or general availability.** Google approves the `crazzydocs-youtube` project for the posts pilot, or general availability arrives (planned 2027). There is no documented application. Until then `posts.list` returns "Method not found" and nothing can be published by API.
2. **OAuth re-consent with `youtube.force-ssl`** (the narrowest scope that allows insert). Preferably in a separate Community token so the video-upload token stays narrow. **Not done.**
3. Run the capability check. It must return `API_AVAILABLE`.
4. **Enable API publishing** in the dashboard, with confirmation.
5. Per post: **Publish now** or **Authorize for a later time**, each confirmed.

## 4. Live read-only check

Recorded after deployment in `STAGE_3_32F_COMMUNITY_PUBLISHING_IMPLEMENTATION_REPORT.md` §8.
