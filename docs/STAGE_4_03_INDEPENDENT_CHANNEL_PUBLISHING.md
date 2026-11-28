# Stage 4.03: Independent Channel Publishing

Scope (roadmap unchanged): each channel publishes only its own, verified, approved, QC-passed video. Hindi publishing
stays **unauthorized** until the operator authorizes it on the dashboard; nothing in code grants it.

## What is enforced

| Area | Where | Rule |
|---|---|---|
| Shared guard | `channels/publish_guard.py` `evaluate()` | All conditions listed at once: channel ACTIVE, verified identity, publishing authorization (Hindi: `publishing_authorized is True` + schedule enabled; English: existing policy and ledger approval), per-video approval, current QC, file integrity (resolved path, SHA-256), schedule (private, future `publishAt`). |
| English upload | `youtube_publishing_engine.upload_short` (the only English `videos().insert`; reached by the orchestrator, approve, publish now, edit and schedule, retry, recovery) | Guard plus destination identity before reconciliation or upload. The identity comes from the duplicate check's existing `channels.list` response (no extra quota). A different channel blocks English (`IDENTITY_CHANGED`); a missing id refuses. Localized media (`media/localizations/**`) is refused here and in `queue_approved_production`. |
| Hindi upload | `localization/publishing.HindiPublisher.upload` (the only Hindi `videos().insert`) | Guard before the claim (a refusal changes nothing and is not an attempt), then `registry.confirm_upload_identity("hi", ...)`: a Hindi token that is the English channel blocks Hindi. `tick()` uploads the first due video that the guard allows; unapproved ones wait. |
| QC hash | `localization/pipeline.run_step("qc")` | On QC pass, `final_mp4_sha256` is stored in `artifacts_json` and `qc_json`. |
| Re-render | `HindiLocalizer.reopen` -> `HindiPublisher.withdraw_for_localization` | DRAFT / SCHEDULED / FAILED -> WITHDRAWN, approval cleared, old schedule kept in the event. NEEDS_CHECK keeps its state, approval cleared. UPLOADING or UPLOADED: the re-open is refused (history kept). After a new QC pass the row returns as DRAFT with the new hash and **no** approval. |
| Approval | `HindiPublisher.approve / unapprove`, `POST /api/hindi-publishing/{pid}/approve|unapprove` | Bound to the file's SHA-256 (must equal the QC hash when recorded). Legacy rows (queued before 4.03, no QC hash) get the hash captured at approval (`LEGACY_HASH_CAPTURED_AT_APPROVAL`). Metadata edits clear approval. Approval never authorizes the channel. |
| Automatic approval (refinement) | `HindiPublisher._auto_approve`, `_invalidate_stale_approvals` (both run in `sync`) | A row newly queued (or re-queued after a re-render) is approved by `auto:qc_passed` only when the localization is QC_PASSED, its QC result passed and recorded the SHA-256, the row has the same hash and the file still has it (event `AUTO_APPROVED_AFTER_QC`). Any approval is cleared when the localization is no longer QC_PASSED, its QC hash changes, or the file is missing or changed; re-render and metadata edits clear it as before (a metadata edit needs a manual re-approval). Rows queued before QC hashes existed are never auto-approved. Never sets `publishing_authorized`. |
| Authorization | `ChannelRegistry.set_publishing_authorization` (the only writer, enforced by a static test), `GET/POST /api/channels/{key}/publishing-authorization` | Local only, same origin + CSRF token, `confirm`, typed phrase `AUTHORIZE HINDI PUBLISHING` to authorize. Flag and `channel_events` row in one transaction (no event, no change). English refused (existing policy). Also written to the audit chain. Revoke is always allowed and stops the next tick. |
| Quota | `services/youtube_quota.QuotaLedger` | One project-wide ledger (both channels share one Cloud project). Channel attribution by purpose (`hindi_` prefix; `channel_key="hi"` adds it), no schema change. Hindi `check(..., channel_key="hi")`: own caps (6 uploads, 1,500 general units per Pacific day) and the English reserve (30 uploads, 5,000 units; English used at most 24 and ~4,600 in 2026-11-21..28) must stay available. English checks unchanged. Google exhaustion blocks both. Fixed: the Hindi upload checked bucket `"uploads"`, which does not exist (it would have raised KeyError). |
| Dashboard | Channels card, unified queue | Hindi authorization state, Authorize / Revoke, last events; per-video Approve / Unapprove, "Approved / Not approved" and the guard's "Blocked by" reasons. No redesign. |

## Database

`hindi_publications`: additive nullable columns `qc_sha256`, `approved_by`, `approved_at_utc`, `approved_sha256`,
`withdrawn_at_utc` (`ALTER TABLE ADD COLUMN`, run once when the publisher starts). New state value `WITHDRAWN`.
Existing rows keep every value; their new columns are empty, so they are not approved and cannot upload.
No other schema change (quota attribution uses the existing `purpose` column).

## Rollback

Revert the commit. Old code ignores the added columns (it names its columns). A WITHDRAWN row is never uploaded by
old code. The authorization flag stays unset unless an operator set it.

## Tests

`backend/tests/test_stage_403_channel_publishing.py` (29). Existing tests adapted only where 4.03 adds a required
gate or action: realistic channel ids in two English fakes, an `approve(1)` step in the Hindi upload tests, the new
`approve` action in the queue expectations, and the 4.01 "English engine does not read the registry" test narrowed
to its two guard helpers. `conftest.py`: MagicMock YouTube responses identify as one fixed test channel; dict
responses keep the strict checks.
