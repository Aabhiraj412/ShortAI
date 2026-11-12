# Stage 3.21S Cleanup and Verification Report

## Outcome

Completed a hash-verified inventory and reference audit, added a dry-run-first retention CLI, and moved four audited intermediate-render directories to reversible quarantine. No permanent deletion, database mutation, production run, upload, notification, schedule activation, or publishing-policy change occurred.

## Storage results

| Directory | Before | After |
|---|---:|---:|
| `media/productions` | 456 files / 534,925,684 B | 364 files / 450,085,260 B |
| `media/quarantine` | 0 B | 93 files / 84,878,860 B |
| `media/video` | 1 file / 12,192,969 B | unchanged |
| `media/thumbnail` | 1 file / 115,373 B | unchanged |
| `media/thumbnails` | absent | absent |
| `media/audio` | 1 file / 345,168 B | unchanged |
| `media/subtitles` | 4 files / 9,092 B | unchanged |

The four moved directories total 84,840,424 bytes (80.91 MiB). **Actual disk space reclaimed: 0 bytes**, because quarantine is a same-volume move. The quarantine manifest adds 38,436 bytes beyond the moved files. Recorded free space was 162,146,648,064 bytes during the initial dry-run and 162,146,902,016 bytes on final measurement; this small filesystem fluctuation is not attributed to cleanup.

## Retained, quarantined, and removed files

- **Retained in place:** 364 files in `media/productions`, including all source/provenance, manifests, reports, historical records, and the complete Stage 3.21R workspace.
- **Quarantined:** 92 intermediate files (88 beat MP4s and four concat lists), preserving their internal list-to-render relationships. The batch manifest is [quarantine_manifest.json](../media/quarantine/stage_3_21s/20261112T011828+0530/quarantine_manifest.json).
- **Permanently removed:** none.
- **Database records removed or modified:** none.
- **Exact-hash duplicates removed:** none.

The original inventory contains 456 SHA-256 records. Every current file was checked against its inventory hash, including files moved to quarantine; verification found zero missing files and zero hash mismatches. Candidate references, run manifests and required source directories were checked before the move. No candidate had an external database, application, or manifest/report path dependency. Only the candidate's own concat list referenced its beat files, and both were moved together.

## Stage 3.21R and dashboard verification

- Canonical Stage 3.21R video remains readable: 1080×1920, 30 fps, 50.000 seconds, 12,192,969 bytes; SHA-256 `f6ed8b3d8b51af9f057f2e1e06d4ab94d6567ba0e52c71cd74fc0ebc995b3035`.
- Canonical thumbnail remains readable at 1080×1920, 115,373 bytes; SHA-256 `097745eca475efca508ca33e54548b53face88f809fbaceb86d503a84864fead`.
- Dashboard root returned HTTP 200. The `/api/overview` and `/api/dashboard/health` requests timed out during this check; API responsiveness was not verified. The dashboard process remains present.
- Active publishing ledger: 0 rows; archive: 253 rows. These counts match the earlier Stage 3.21R report; all database access for this stage was read-only.
- No production history or media outside the four named intermediate folders was removed.

## Retention implementation and validation

Added [the media retention policy](../backend/data/media_retention_policy.json), [the CLI](../backend/scripts/media_retention.py), and focused tests in [test_media_retention.py](../backend/tests/test_media_retention.py). The CLI defaults to dry-run, restricts quarantine to selected `render_work` directories, checks active records/process evidence and exact-path/unique-name references, rejects symlinks, verifies hashes, stores a quarantine manifest, and refuses restore overwrites. It has no delete or purge action and is not invoked during dashboard startup.

- `pytest backend/tests/test_media_retention.py backend/tests/test_stage_3_21k_reconciliation_freshness_storage.py -q`: **16 passed** (run with a workspace-local temporary directory).
- Python syntax compilation for the CLI and its tests: passed.
- `git diff --check`: passed after final edits.
- This is a focused test result, not a complete repository test-suite result.
- pytest emitted existing `pytest-asyncio` loop-scope and Pydantic config deprecation warnings.

## Final operational safety state

- Production scheduler task: **Disabled**.
- Scheduler daemon: **Stopped**; no scheduler heartbeat lock or dedicated scheduler process was present.
- Production/validation workers: **Idle**; no active progress entries, active database job rows, or worker process was found. The only observed Python background process was the dashboard service.
- Schedule profile: **FOUR_CYCLES_DAILY**; `SINGLE_DAILY_2300` remains inactive.
- Publishing mode: **manual_approval**.
- Active publishing ledger / archive: **0 / 253**, unchanged.
- YouTube uploads, production runs, and real notification sends: **none**.
- No commit or push was made.

## Remaining limitations

- Disk space was not reclaimed; the 80.91 MiB candidate set is preserved in quarantine for recovery.
- Regeneration was not exercised because production runs were prohibited; input folders, manifest, narration and completed videos were present for each selected run.
- The 90-day quarantine period is a review reminder only. There is no automatic expiration or purge.
- Retention handling intentionally supports only explicit `render_work` quarantine/restore. Cache deletion and broader archival automation remain out of scope.
- Dashboard HTML is responsive, but the two operational API health requests timed out, so API responsiveness remains unverified.
