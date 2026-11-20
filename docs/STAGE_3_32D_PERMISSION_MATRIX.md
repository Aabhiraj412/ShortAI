# Stage 3.32D: Permission Matrix

**Category:** `self_healing`. Its effective level is min(global level, category level). It is **OFF by default**, and **OFF in production**.

## What each level allows

| Effective level | Scan / detect / classify / diagnose | Propose actions | Execute actions |
|---|---|---|---|
| OFF | no (nothing is written) | no | no |
| OBSERVE | yes (incidents stored as observations) | no | no |
| RECOMMEND | yes | yes (approval queue) | no |
| APPROVED_EXECUTION | yes | yes | only approved requests, through `ControlPlane.execute` |
| Any kill switch active | blocked (one audit event) | no | no (also re-checked before each attempt) |

## Permitted action types

| Action type | Risk | Approval | Timeout | Retries | Daily limit | Network | Writes |
|---|---|---|---|---|---|---|---|
| `selfheal.diagnostic.run` | LOW | standing or explicit | 60 s | 1 | 96 | none | reports only, in `data/autonomy/self_healing/reports` |
| `selfheal.provider.retry_readonly` | MEDIUM | **explicit for every request** | 45 s | 0 | 12 | exactly one uncached GET to the provider's public search endpoint (15 s, 2 MB; Openverse, Wikimedia Commons, NASA, Internet Archive; no keys) | none (no cache) |
| `selfheal.asset.reacquire_staged` | MEDIUM | **explicit for every request** | 90 s | 0 | 12 | one guarded HTTPS download: allow-listed public hosts on every redirect hop (≤ 3), 25 MB, 60 s | the staging folder only (exclusive create, never overwrites) |

Both network actions use `guarded_open`:
- HTTPS on port 443 only, with no credentials in the URL;
- exact host allow-list;
- every resolved address must be public;
- every redirect hop is re-validated;
- no environment proxy; verified TLS.

The four file-reading diagnostics (`collect_log_context`, `inspect_media_metadata`, `inspect_file_locks`, `validate_ffmpeg_filter`) re-check the live job state at execution time. They refuse while a worker or the scheduler is using the job.

**Allowed diagnostics** (closed list): `collect_log_context`, `inspect_media_metadata`, `check_disk_space`, `inspect_file_locks`, `validate_ffmpeg_filter`, `generate_diagnostic_report`.

**Payload restrictions**
- `incident_id` must match `inc_` + 12 hex characters, and the incident must exist.
- The provider retry query must equal the topic recorded in the incident evidence (character allow-list).
- The asset URL must be HTTPS on an allow-listed provider host, and must be recorded in the incident evidence.

**Execution environment**
- File reads are limited to the `media/` and `assets/` roots.
- ffprobe runs with a fixed argument vector, without a shell, with a 20 s timeout.

## Prohibited (no level or approval can enable these)

**New in 3.32D**
- `recovery.job.retry`: retry, resume or re-run a production job.
- `render.video.rerender`: re-render a video or regenerate production media inside a job.
- `render.worker.restart`: restart, stop or kill workers or jobs.
- `production.asset.replace`: place, replace or delete assets inside a job's media folder.
- `autonomy.code.execute`: shell commands, arbitrary Python, or commands built from recommendation or AI text.

**Still prohibited from 3.32B**
- scheduler start and stop;
- cycle creation;
- schedule changes;
- render-worker launch and lock bypass;
- recovery-deadline and retry-policy changes;
- topic-history modification;
- duplicate-detection and quality-check bypass;
- OAuth credential and scope changes;
- YouTube video and Community publishing;
- production configuration and feature-flag changes;
- daemon and dashboard restart;
- production data deletion;
- Git branch changes and destructive Git;
- autonomy changing its own policy.

## Identities

| Identity | Can propose | Can approve / reject / execute as operator | Can change levels, standing approvals or kill switches |
|---|---|---|---|
| `self-healing-engine` | yes (recorded as proposer) | **no** | **no** |
| Any reserved prefix: `autonomy`, `engine`, `system`, `control-plane`, `standing-approval`, `recommendation`, `self-healing`, `selfheal` | n/a | **no** | **no** |
| Human operator (validated name) | n/a | yes, via the CSRF-protected local API | yes, in Autonomy Control |

Reserved-name matching ignores spaces, `.`, `_`, `@` and `-`. Examples refused: `Self.Healing`, `self healing`, `SELFHEAL bot`, `Re-commendation`.

## API (all local-only)

**Reads**

| Endpoint | Purpose |
|---|---|
| `GET /api/self-healing` | State, statistics, incidents, approval queue, history, learning |
| `GET /api/self-healing/incidents?status=` | Incident list |
| `GET /api/self-healing/incidents/{id}` | Incident detail: diagnosis, plan, recoveries, audit trail |
| `GET /api/self-healing/history` | Finished recoveries, statistics, learning |

**Writes** (CSRF token and operator identity required)

| Endpoint | Purpose |
|---|---|
| `POST /api/self-healing/scan` | Scan now |
| `POST /api/self-healing/recoveries/{id}/approve` | Approve; `payload_hash` required |
| `POST /api/self-healing/recoveries/{id}/reject` | Reject; reason required |
| `POST /api/self-healing/recoveries/{id}/execute` | Execute an approved recovery |
