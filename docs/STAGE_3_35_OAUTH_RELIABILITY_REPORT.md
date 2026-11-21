# Stage 3.35: YouTube Analytics OAuth reliability

## 1. Symptom

The YouTube Analytics connection "disconnected" repeatedly. The operator re-authorized it four times: 2026-11-20 at 10:53, 14:14 and 14:33 IST, and 2026-11-21 at 01:08 IST. Each consent requested `youtube.upload youtube.readonly yt-analytics.readonly`, as the dashboard log shows. Yet `video_performance` held **0 rows**, and the stored token listed only the two upload scopes.

## 2. Root cause (confirmed)

Both clients share one token file (`backend/token.json`, DPAPI-protected).

1. The **upload client** (`YouTubeOAuthManager`, used by the dashboard's status check and by the scheduler's uploads) loads the token with its **own** two scopes: `Credentials.from_authorized_user_info(info, YOUTUBE_SCOPES)`.
2. When its access token expires (hourly), it refreshes and calls `save_credentials(creds)`. That wrote `creds.to_json()`, whose `scopes` list is **only the two upload scopes**.
3. So about an hour after every consent, a routine refresh **erased `yt-analytics.readonly` from the stored scope list**.
4. The analytics collector trusted that stored list. It saw no analytics scope, reported `reauthorization_required`, made no API call, and the dashboard showed "disconnected".

**Evidence that it was not token expiry or revocation:** after the fix, an in-memory refresh requesting only `yt-analytics.readonly` succeeded (`granted: [yt-analytics.readonly]`). Google still held the grant on the refresh token; only the local scope list had been wrong. Refresh-token persistence was already correct, because `save_credentials` keeps the stored refresh token when Google omits it.

**A second defect found during the investigation:** `google_auth_oauthlib.flow` logs the local redirect request line at INFO, so `logs/dashboard_service.log` contains OAuth **authorization codes** from past consents. They are single-use and already exchanged, so they cannot be replayed, but they must not be logged.

## 3. Fixes

| Fix | Where |
|---|---|
| The stored scope list is the set of **granted** scopes: a refresh only ever adds to it (`merge_token_scopes`); only a completed consent may replace it | `youtube_publishing_engine.save_credentials` |
| Cross-process lock around every token read-modify-write (dashboard and scheduler both refresh) | `_TokenFileLock` (msvcrt / fcntl lock file) |
| Analytics loads the shared refresh token with **only** `yt-analytics.readonly`, refreshes **in memory**, and **never writes the shared token file** (it cannot narrow the upload client's credentials) | `youtube_analytics.analytics_credentials` |
| Analytics verifies the grant with Google instead of trusting the stored list, and records a confirmed grant (scope name only, access token untouched) | `record_granted_scope` |
| Concurrent analytics refreshes are serialised in-process; the access token is cached until 2 minutes before expiry | `_refresh_lock`, `_cached` |
| Bounded retries with backoff (1 s, 2 s) for network errors only | `REFRESH_ATTEMPTS = 3` |
| Persisted auth state shared by the dashboard and scheduler (settings table), with transition history | `youtube_analytics_auth_state` |
| The OAuth library logger is set to WARNING: authorization codes are no longer logged | module import |

**States:**

| State | Meaning |
|---|---|
| `CONNECTED` | Analytics token refreshed successfully |
| `REFRESHING` | Reserved for an in-progress refresh |
| `EXPIRED` | Refresh failed on the network (retried with backoff) |
| `REVOKED` | Google returned `invalid_grant` |
| `AUTHENTICATION_REQUIRED` | No token, no refresh token, or `invalid_scope` |
| `ERROR` | Anything else |

**Scopes:** the minimum Analytics scope, `yt-analytics.readonly`. **No scope was added, and no consent flow was started.**

## 4. Behaviour after restarts and expiry

- Both processes read the same DPAPI-protected token and the same persisted state.
- An upload refresh can no longer remove the analytics grant.
- After deployment the stored list self-heals at the first analytics refresh: the confirmed grant is recorded.

Verified in tests (expiry, rotation, revoked, missing credentials, concurrent refreshes and writes, restart via persisted state) and with the read-only live refresh above.

## 5. Operator action

**None required.** The existing grant is valid, so no new consent is needed. If Google ever revokes it (state `REVOKED` or `AUTHENTICATION_REQUIRED`), the dashboard shows the one action: an operator-started one-time consent for `yt-analytics.readonly` on the existing OAuth client.

The old authorization codes in `logs/dashboard_service.log` (2026-11-12 to 2026-11-21) are inert. Delete or rotate that log at your discretion; it was left untouched.
