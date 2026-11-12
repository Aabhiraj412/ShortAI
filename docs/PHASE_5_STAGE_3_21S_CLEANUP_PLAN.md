# Stage 3.21S Cleanup Plan

## Decision

Use reversible quarantine only for the four fully audited `render_work` directories below. The configured 30-day intermediate retention period had not elapsed. The requested cleanup permits safe actions, and the CLI's explicit override is limited to reversible quarantine; it was recorded in the batch manifest. No files were permanently deleted.

## Proposed and executed actions

| Path | Size | Action | Reference/regeneration audit | Recovery | Storage impact |
|---|---:|---|---|---|---:|
| `media/productions/val_3_21i_20261111_110449/render_work` | 33,739,703 B | Quarantine | 23 files; completed video, run manifest, assets and narration exist; no external DB/app/report references. Internal concat references move with the directory. | Restore using the batch manifest; hashes checked. | 0 disk bytes reclaimed |
| `media/productions/val_3_21j_20261111_120906/render_work` | 16,179,769 B | Quarantine | Same checks; no external references. | Same | 0 disk bytes reclaimed |
| `media/productions/val_3_21l_20261111_134455/render_work` | 12,569,846 B | Quarantine | Same checks; no external references. | Same | 0 disk bytes reclaimed |
| `media/productions/val_3_21l_20261111_135230/render_work` | 22,351,106 B | Quarantine | Same checks; no external references. | Same | 0 disk bytes reclaimed |
| **Total** | **84,840,424 B (80.91 MiB)** | **92 files moved** | Exact-path and unique-name checks; no active run/job. | `media/quarantine/stage_3_21s/20261112T011828+0530/quarantine_manifest.json` | **0 bytes** |

The `media/productions` directory is smaller by 84,840,424 bytes. The files remain under `media/quarantine`; quarantine adds its manifest, so the move does not reclaim filesystem space. No removal or purge is proposed.

## Retention policy and recovery

Configuration: [media_retention_policy.json](../backend/data/media_retention_policy.json). The policy defaults to dry-run, reports exact-hash groups without removing duplicates, protects unknown and active files, and never modifies database or publishing records. It prohibits automatic deletion and cleanup at dashboard startup. Historical provenance is retained; the CLI only accepts explicitly selected `render_work` directories.

The CLI in [media_retention.py](../backend/scripts/media_retention.py) supports:

- `python -m backend.scripts.media_retention plan` — storage report and dry-run eligibility.
- `python -m backend.scripts.media_retention quarantine --paths <workspace-relative-paths>` — dry-run by default.
- `--confirm --worker-idle-confirmed` — explicitly execute a quarantine after an independent worker check.
- `--override-retention` — bypass the configured intermediate age only for a reversible quarantine; the reason is recorded in its manifest.
- `python -m backend.scripts.media_retention restore --manifest <workspace-relative-manifest>` — dry-run restore by default; `--confirm` restores without overwriting and verifies hashes.

The configured 90-day quarantine period is a manual review point, not an automatic purge. The tool has no delete/purge operation. Other policy periods are retained as policy guidance; automated cleanup for caches, historical assets, and unknown files is intentionally not implemented.

## Deferred opportunities

- Review the 55 `UNKNOWN` entries only when their provenance and regeneration requirements can be proven.
- Review the 24 exact-hash duplicate groups individually; none is automatically removable.
- Consider future quarantine of the intermediates only after normal retention age elapses, or after a fresh audited, explicitly authorized override.
- Do not purge quarantined data without a separate, explicit review and authorization.

