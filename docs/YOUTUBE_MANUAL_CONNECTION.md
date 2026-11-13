# Manual YouTube Connect / Reconnect

## What the user sees (Overview tab → "YouTube Connection")
- **Status:** `CONNECTED`, `DISCONNECTED`, `REAUTHENTICATION_REQUIRED`, `REFRESHING`, `CHANNEL_VERIFICATION_FAILED`, `API_UNAVAILABLE` or `QUOTA_EXCEEDED`, with a plain-language message.
- **Details:** channel name, channel verification, last successful verification, last token refresh, and the last error (redacted, rendered as text only).
- **Buttons:**

| Status | Connect | Reconnect | Retry Connection Check |
|---|---|---|---|
| CONNECTED | | | ✓ |
| DISCONNECTED | ✓ | | |
| REAUTHENTICATION_REQUIRED | | ✓ | |
| CHANNEL_VERIFICATION_FAILED | | ✓ | ✓ |
| API_UNAVAILABLE / QUOTA_EXCEEDED | | | ✓ |
| REFRESHING | (no buttons while authorization is in progress) | | |

Only a genuine authorization failure asks you to reconnect:
- Google rejects the refresh token (`RefreshError`), or the API returns 401.
- Network failures, including during a token refresh, show **API_UNAVAILABLE**.
- Quota errors show **QUOTA_EXCEEDED**.
- In both of those cases the stored credentials are kept.

## How Connect works (reuses the existing flow)
`POST /api/youtube/connection/connect {"confirm": true}` → `youtube_connection_service.connect()` → the existing `YouTubeOAuthManager.start_interactive_auth` (google-auth-oauthlib 1.5.0 `InstalledAppFlow.run_local_server`):
- **Secure state:** a random `state` is generated and checked when the response comes back (`MismatchingStateError` otherwise). PKCE (`code_verifier`) is on by default.
- **Redirect:** fixed to the flow's own listener, `http://localhost:8088/`; no other redirect URL is accepted.
- **Options:** `access_type=offline`, `prompt=consent`, 180 s timeout.
- **Code exchange:** happens inside the library; the code is never logged, stored or returned. Failures are classified (`CANCELLED`, `INVALID_STATE`, `CODE_EXPIRED`, `TIMED_OUT`, `NETWORK`, `FAILED`) without the exception text, which can contain the redirect URL.
- **Saving:** credentials are encrypted with Windows DPAPI and saved **only on success**, so a failed reconnect keeps the old connection. If Google omits `refresh_token`, the stored one is kept.
- **Afterwards:** the channel is verified (`channels.list mine=true`), the status cache is cleared, and the dashboard re-renders from the response.
- **One at a time:** a second connect gets HTTP 409, and the card shows REFRESHING.

The legacy `POST /api/stage-3-22/youtube/connect` goes through the same single-flight path and the same origin guard.

## Endpoints
| Endpoint | Guard |
|---|---|
| `GET /api/youtube/connection` | localhost only |
| `POST /api/youtube/connection/check` (bypasses the 5-min cache; never changes credentials) | localhost and same origin |
| `POST /api/youtube/connection/connect` | localhost and same origin, plus `confirm: true` |

**Same-origin guard (CSRF):** loopback alone isn't enough, because any website open in the local browser can send a simple POST to `127.0.0.1`.
- Refused: `Sec-Fetch-Site: cross-site/same-site`, and any `Origin` other than `http://127.0.0.1:8000`, `http://localhost:8000` or the request's own origin.
- Allowed: non-browser local clients, which send neither header.
- Existing endpoints keep their guards; none were weakened.

## Stored history
The studio setting `youtube.connection_state` is shared by the dashboard and the scheduler. It holds timestamps, status, HTTP status, Google reason, exception type, a redacted message and the channel title. It never holds tokens, codes or secrets. Unchanged successful checks are written at most every 10 min.

## Credential storage fix (audit R1)
`_load_credentials` used to rewrite `token.json` on **every** load, because it tested already-decrypted bytes for plaintext. It now migrates only a file that is plaintext on disk.

## Scopes (unchanged)
`youtube.upload` and `youtube.readonly`, enough for private uploads with `publishAt`, custom thumbnails (`thumbnails.set`), and channel and video status reads.

**Not included, on purpose:** changing an already-uploaded video (`videos.update`: reschedule, metadata edits, cancelling a schedule) needs `https://www.googleapis.com/auth/youtube` or `youtube.force-ssl`. Adding it means a broader permission (full YouTube account management) and a fresh consent. It's a separate decision for you; until then the automatic path relies on `publishAt` being set during the upload itself.

## Tests
`backend/tests/test_youtube_manual_connection.py` (35 tests, all mocked):
- successful connection and reconnection;
- cancelled, invalid-state, expired-code, network and timeout authorizations, each keeping existing credentials;
- a refresh token missing on connect and on refresh;
- an invalid refresh token, so reauthentication is required;
- network failure during refresh and during verification;
- quota exhaustion and channel-verification failure;
- verification succeeding after a reconnect;
- cache use, bypass and invalidation;
- button visibility and the dashboard card;
- no tokens in responses, cross-site refusals, remote and unconfirmed refusals;
- single-flight connects, and no authorization code in logs or stored state;
- redaction;
- the no-rewrite and one-time plaintext migration fixes.
