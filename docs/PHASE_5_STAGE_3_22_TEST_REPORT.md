# Stage 3.22 Test Report

## Scope and safety

Tests ran in the repository's `TEST` execution context. YouTube operations and SMTP delivery were mocked; no OAuth consent, external email, upload, scheduler activation, or production run was performed. The configured production scheduler task remained disabled.

## Focused Stage 3.22 coverage

The nine tests in `backend/tests/test_stage_3_22_integrations.py` passed as part of the final focused selection:

- DPAPI-protected OAuth credentials and SMTP password.
- Expired OAuth refresh failure and expired credentials without a refresh token.
- Resumable upload state recovery after a simulated restart, followed by verified mocked completion.
- Loopback/bearer-token enforcement and rejection of automatic upload.
- Persistent notification deduplication and use of the configured recipient.
- IST timestamps in notification history.
- Daily-summary scheduler failure notification dispatch.

Final focused command selection covering Stage 3.22, YouTube, notification, email-dashboard, and daily-summary regressions: **43 passed**.

## Broader relevant regression selection

The broader selection covered Stage 3.6, 3.7, 3.9F, 3.10, 3.11, 3.12, 3.17, 3.18, daily publishing summary, and Stage 3.22:

- **92 passed**
- **1 failed**: `test_scenario_3_interrupted_rendering` in `test_stage_3_11_real_recovery.py`. It expected a legacy verified-video path but the recovery run returned the test job's generated path. This failure is outside the Stage 3.22 changes and was not modified.
- **1 deselected**: `test_existing_private_upload_preserved`. The fixture expects `4DCas1e4vHI` in `publishing_ledger`; the audited database instead has it in `deleted_video_audit`. It was excluded rather than mutating or fabricating a ledger record.

This is the full **relevant Stage 3.22 regression selection**, not the complete repository test suite.

## Static and persistence checks

- Pylance syntax checks passed for the changed OAuth engine, API entry point, scheduler, notification service, and Stage 3.22 test module.
- `git diff --check` passed. Git emitted line-ending conversion warnings for existing dirty files; no broad reformat was performed.
- Windows DPAPI round-trip passed with synthetic data.
- Read-only database comparison against the pre-migration backup found matching counts for the existing jobs, publications, publishing ledger/archive, notification, deleted-video audit, and settings tables. The new `youtube_upload_state` table is empty.

## Warnings and outstanding validation

- Pytest emitted an existing `pytest-asyncio` loop-scope deprecation warning and Pydantic's class-based `Config` deprecation warning.
- Google OAuth consent, real token refresh/revocation, channel authorization, actual YouTube upload/thumbnail eligibility, scheduled publication behavior, and SMTP delivery/retry against a provider remain unverified by design.
- The protected-video test discrepancy and unrelated interrupted-rendering failure remain open.
