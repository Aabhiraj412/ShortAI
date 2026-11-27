# Stage 4.01: Multi-channel foundation

**Status:** implemented and tested. Deployment is recorded in the handover §2/§3.
- **English** (master) is registered, and its identity is verified from `channels.list?mine=true`.
- **Hindi** (localized) is registered and **PENDING_VERIFICATION**. No Hindi authorization exists yet. It is **not production-ready** and must not be described as such until its own channel is connected and verified.

## Execution gate (2026-11-27 09:20 IST): passed

| Criterion | Evidence |
|---|---|
| Dashboard and daemon stability | 99.54 % availability (432 probes since 05:43; the only non-healthy probes were the two controlled deploys); 0 unplanned restarts; liveness p50 4.9 ms. Dashboard CPU 4.5 % (was about 90 %) after the media-walk fix. Daemon 0.2 %. |
| Shorts schedule unchanged and healthy | 24 cycles, revision `ccf0bd04f159`. 9/9 cycles since 01:00 completed. 5/5 render-slot leases acquired and released, 0 overlaps. Uploads private and scheduled as before. |
| Restricted autonomy and operator attention | 20 policy executions since 05:42, all SUCCEEDED (self-healing 11, recommendations 4 plus others). The attention feed shows RESTRICTED and 5 genuine items. |
| No unresolved critical regression | None. Non-critical items stay open: the evening topic-supply check, the Gemini free-tier quota, and the Pexels key. |

## Architecture

New package `backend/app/channels/`: `registry.py` and `status.py`. It reuses the scheduler, publishing engine, OAuth manager, analytics and dashboard. There is no new scheduler, worker or publisher.

| Table (`studio.db`, created on first use) | Purpose |
|---|---|
| `channel_registry` | One row per channel. Columns: key, name, language, role (`MASTER` or `LOCALIZED`), source channel, status and reason, verified `youtube_channel_id`, title and time, `credential_ref` (a **path**, never a secret), and publishing and analytics config. |
| `channel_events` | Append-only history: registration, verification, connect, identity conflict, activation, deactivation, localization planned. |
| `content_localizations` | Source-to-localized map, `UNIQUE(source_job_id, target_channel_key)`. Columns: source job, video id and English content identity (normalized topic title), target channel, language, status, localized artifact and video. |

| Channel | Role | Credential | Publishing queue | Analytics | Status now |
|---|---|---|---|---|---|
| `en` | MASTER | `backend/token.json` (unchanged) | `publishing_ledger` (unchanged) | `video_performance` (unchanged) | ACTIVE, verified |
| `hi` | LOCALIZED of `en` | `backend/channel_tokens/hi/token.json` (gitignored; does not exist yet) | `content_localizations` | not configured until active | PENDING_VERIFICATION |

## Identity and credential isolation

- **Identity:** the channel id comes from `channels.list?mine=true` (through `YouTubeOAuthManager.get_auth_status`) and never from a display name.
  - English reuses the status the dashboard already caches, so it costs no extra quota. A repeat verification writes nothing.
  - Hindi is re-checked at most every 6 h.
- **English preserved:** `DEFAULT_TOKEN_FILE` and the English connect flow are unchanged. No 4.01 code writes, refreshes for or revokes the English token; tests compare its hash.
- **Hindi isolated:**
  - **Own token file:** a separate `YouTubeOAuthManager(token_file=…, record_connection_events=False)`. Credential loading reads only `token_file`, with no fallback.
  - **Own connection history:** the manager never writes the English connection history.
  - **Shared consent lock:** it uses the same lock as the English consent flow, so only one authorization runs at a time.
- **Refusals:**
  - a Hindi `credential_ref` that points at the English token;
  - a Hindi authorization that verifies as the English channel (`IDENTITY_CONFLICT` → BLOCKED);
  - verifying Hindi before English;
  - an English token that turns into a different channel (`IDENTITY_CHANGED` → BLOCKED; the master is never switched).
- **Activation rules:** only a verified channel can be activated. The English master cannot be deactivated here, because English production is governed by the operating mode.

## Content relationship

- English remains the only discovering channel. Hindi never calls topic discovery or selection and never writes the topic ledger or identity registry; a test checks this.
- `plan_localization(source_job_id, "hi")` accepts only a **completed English production** (never test jobs) and only while Hindi is **verified and active**. It is idempotent per source job and channel, and keeps the English content identity and video id.
- **Gap:** the brief says Hindi reuses English Shorts through "the existing localization workflow". **No localization workflow exists in this codebase** (no translation, no Hindi voice, no localized render). 4.01 provides the mapping and state. Producing Hindi media is a separate decision and is not built here. Long-form localization stays in Stage 6.

## API and dashboard

API (all local-only):
- `GET /api/channels`
- `POST /api/channels/{key}/verify` (same origin)
- `POST /api/channels/{key}/connect` (same origin, `confirm`, non-master only)
- `POST /api/channels/{key}/activate|deactivate` (operator, reason, confirm, CSRF)

Dashboard: Publishing Queue tab → **Channels** card. For each channel it shows:
- identity and verification;
- language, role and active state;
- OAuth and credential (including whether the token file is present);
- publishing state (English: the last 7 days of `publishing_ledger`; Hindi: localizations);
- analytics, active processes by pipeline and stage;
- a "What to do" line;
- the channel history.

## Hindi activation (operator steps)

1. On the Channels card, select **Connect** for Hindi. Sign in with the **Hindi channel's** Google account, not the English one.
2. The registry verifies it automatically. VERIFIED means a distinct channel id was confirmed. BLOCKED / IDENTITY_CONFLICT means the English account was used: reconnect.
3. Select **Activate**, with a reason.
4. Producing Hindi Shorts additionally needs the localization pipeline (see Gap). Until then nothing is published to Hindi.
