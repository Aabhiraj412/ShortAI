# Stage 3.22 Setup Guide

## Safety defaults

- Keep `PUBLISHING_MODE=manual_approval`.
- Keep `ENABLE_EMBEDDED_SCHEDULER=false` and the Windows production scheduler task disabled until separately authorized.
- Automatic publishing is rejected. Each upload needs a separate local API approval.
- Do not use the setup steps below to initiate OAuth, upload, or email without an operator decision.

## Local environment

1. Copy the secret-free [`.env.example`](../.env.example) to `.env.local`.
2. Set `CRAZZYDOCS_CONTROL_TOKEN` in `.env.local` to a random, private value. For example, generate a candidate locally with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. Keep `.env.local` out of version control and restrict access to the Windows account running the service.
4. Leave the schedule profile as `FOUR_CYCLES_DAILY`; leave `SINGLE_DAILY_2300` inactive.
5. Run one API process. Restart recovery currently has no multi-process worker lease.

The control token is never returned by the API. Supply it as an HTTP bearer token from a local client only. Every Stage 3.22 control route rejects non-loopback clients.

The existing application has legacy routes (including `/api/jobs/run` and `/api/settings`) outside this token gate. Keep the whole backend bound to a trusted local interface and do not expose it on an untrusted network; these legacy routes need unified authentication before remote dashboard control.

## Google OAuth prerequisites

1. Create/select a Google Cloud project, enable **YouTube Data API v3**, and configure the OAuth consent screen.
2. Create an OAuth client compatible with the installed-app local redirect flow; configure any required consent-screen test users.
3. Provide the OAuth client JSON only to the loopback client-config endpoint. Do not paste it into source control, logs, or issue reports.
4. Obtain operator approval before starting the interactive authorization. The current OAuth flow uses `youtube.upload` and `youtube.readonly` and runs a local callback on port `8088`.
5. Confirm the result with the status endpoint. A revoked/expired token may require the operator to reconnect.

The service protects OAuth client/token files with Windows DPAPI under the current Windows identity. A restart under a different Windows account cannot decrypt them.

## SMTP configuration

The notification API stores SMTP settings in the existing `studio_settings` table and protects the password with Windows DPAPI. Environment-based SMTP settings remain supported; protect `.env.local` accordingly. Supported security values are `STARTTLS`, `SSL`, and `NONE`.

There is deliberately no “send test email” endpoint. Configure the recipient and settings, then use an approved operational event when ready. Tests mock email delivery.

## API contract

Base prefix: `/api/stage-3-22`. Every route below requires a loopback caller and `Authorization: Bearer <CRAZZYDOCS_CONTROL_TOKEN>`.

| Method and route | Purpose | Request |
|---|---|---|
| `GET /youtube/status` | Redacted channel/token status | — |
| `POST /youtube/client-config` | Install OAuth client JSON securely | `{"client_secrets_json":"<OAuth client JSON>"}` |
| `POST /youtube/connect` | Start interactive local OAuth | — |
| `POST /youtube/disconnect` | Revoke/remove local token | `{"confirm":true}` |
| `GET /publishing/config` | Read policy and permitted modes | — |
| `PUT /publishing/config` | Keep manual approval | `{"mode":"manual_approval"}` |
| `POST /publishing/queue` | Queue a completed, QC-ready production | `{"publication_id":123}` |
| `GET /publishing/pending` | List uploads requiring attention | — |
| `GET /publishing/history?limit=100` | Read upload history | — |
| `GET /publishing/{ledger_id}` | Read safe upload status/progress | — |
| `POST /publishing/{ledger_id}/approve` | Explicitly approve one upload | `{"confirm":true,"publish_at":"<optional future timezone-aware ISO-8601>"}` |
| `GET /notifications/config` | Read redacted SMTP config | — |
| `PUT /notifications/config` | Save SMTP settings | `host`, `port`, `username`, `password`, `recipient`, optional `sender` and `security` |
| `GET /notifications/history?limit=100` | Read notification history and IST timestamps | — |

Only completed videos under the configured local media directory can be queued. Queueing requires the application publication to be `ready_for_approval` and its video job to be `completed`. Stage 3.22 approval uploads privately; a future `publish_at` may be supplied for an individually approved scheduled publication. Requests to change the active policy to `automatic` or `scheduled` are rejected.

OAuth client JSON and bearer credentials are secrets. Do not log request headers/bodies or return them in dashboard responses.

## Operational checks

Before any operator-authorized upload:

- Verify `PUBLISHING_MODE=manual_approval`, the connected channel, local media path, metadata, and the intended visibility/time.
- Verify the item is QC-approved and explicitly approve only its ledger ID.
- Confirm upload status and remote verification through the status/history APIs.
- Confirm notification configuration and inspect history; email delivery failures remain separate from production/upload completion.

This setup guide does not authorize scheduler activation, automatic publishing, live OAuth, uploads, or external email.
