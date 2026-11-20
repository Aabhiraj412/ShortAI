# Stage 3.32D: Final Security Audit

**Audited revision:** `d2e0928` (on GitHub `main`), plus the remediation listed below. The remediation is uncommitted in worktree `crazzy-docs-verify-capabilities-ac634e`, branch `claude/scheduler-redesign-render-fix`.

**Date:** 2026-11-20.

**Production state during the audit:**
- Dashboard: PID 21420, running 3.32C code.
- Daemon: PID 22872.
- Monitor: PID 8132.
- All autonomy levels OFF; startup kill switch active.

Nothing in production was restarted, activated or modified.

## 1. Files inspected

| Area | Files |
|---|---|
| Stage 3.32D package | `backend/app/autonomy/self_healing/`: `__init__`, `classification`, `evidence`, `diagnostics`, `planner`, `store`, `services`, `handlers`, `engine` |
| Stage 3.32B | `autonomy/control_plane.py`, `autonomy/handlers.py`, `autonomy/registry.py` |
| Stage 3.32C | `autonomy/recommendations/`: `engine`, `signals`, `rules` (learning input) |
| Web | `main.py`: self-healing endpoints, `require_local_control`, `require_same_origin`, `require_autonomy_write`, `_autonomy_call`, CORS; `dashboard.html`: Self-Healing tab |
| Services reached through the adapters | `media/net.py` (`fetch`, `cached_fetch_json`, `tls_context`), `media/downloader.py` (`AssetDownloader`, `detect_magic_type`), media providers (Openverse, Wikimedia, NASA, Internet Archive), `core/paths.py` (ffprobe), `scheduler/retry_policy.py` (`classify_error`, read only), `services/operational_intelligence.py` |

## 2. Findings by severity

| ID | Severity | Finding | Status |
|---|---|---|---|
| F1 | **High** | **The staged re-download could leave the allow-list through redirects.** `AssetDownloader` uses `urllib.request.urlopen`, which follows up to 10 redirects to any host and scheme. Only the first URL was allow-listed, so a redirect could reach a non-allow-listed host or a local or private address. | **Fixed** |
| F2 | **High** | **A provider check could report a false success.** The provider clients catch every exception and return `[]`, so an unavailable provider looked like "answered, 0 records" and was VERIFIED. Openverse and Wikimedia also served up to 6-hour-old cached responses (`cached_fetch_json`), so a check could pass with no network contact, and wrote entries into the production provider cache. | **Fixed** |
| F3 | Medium | **File-reading diagnostics could interfere with a running job.** `collect_log_context`, `inspect_media_metadata`, `inspect_file_locks` and `validate_ffmpeg_filter` open job files. On Windows an open handle makes the job's own rename or delete fail with WinError 32, the very failure being diagnosed. These checked worker activity only at planning time, not when the diagnostic ran (and a standing approval runs them later). | **Fixed** |
| F4 | Medium | **Raw error text was stored and returned unredacted.** Incident `message`, `template` and `error_type` were stored as-is and returned by the API and dashboard. An error containing an API key (e.g. `?key=AIza…`) would have been exposed. The diagnosis and plan JSON were already redacted. | **Fixed** |
| F5 | Low | **Some history was unbounded.** `selfheal_recoveries` was never pruned (it kept orphans after incident pruning), `selfheal_notifications` keys accumulated forever, and two recovery queries had no LIMIT. Staged files and reports were never removed. | **Fixed** |
| F6 | Low | **Read endpoints created schema.** The first `GET /api/self-healing` (or incident list, history or detail) created the six `selfheal_*` tables, even with self-healing OFF. | **Fixed** |
| F7 | Low | **A timeout was reported as a plain failure.** The worker thread may still be running when the timeout fires. The lock already stayed held until the thread ended (no overlap was possible), but the record did not say so. | **Fixed** (wording, plus a regression test that proves the lock is held) |
| F8 | Info | **Windows lock-probe label was inaccurate.** Python's `open` reports a sharing violation as a plain `PermissionError` without `winerror`, so the detail now says "held by another process or not permitted". Detection itself was correct. | Fixed |
| R1 | Info (pre-existing, 3.32B) | **DNS rebinding.** A page that rebinds its own hostname to 127.0.0.1 could read the CSRF token via a same-origin GET. Every write still requires an allow-listed `Origin` (browsers send it on POST), so writes stay refused. A Host-header allow-list would add defence in depth. | Documented, not changed |

**No critical findings.** The audit found no bypass of the control plane, of approval signatures, of identity checks or of kill switches.

## 3. Fixes implemented

### F1 and F2: guarded network adapters (`self_healing/services.py`)

A new `guarded_open` helper is used by both network actions:
- HTTPS only; no credentials in the URL; port 443 only.
- The host must be allow-listed. Suffix matching is exact, so `upload.wikimedia.org.evil.example` and `evilwikimedia.org` are refused.
- Every resolved address must be globally routable. Loopback, private, link-local and unique-local addresses are refused; DNS failure fails closed.
- **Every redirect hop is re-validated**, with at most 3 redirects.
- No proxy from the environment; verified TLS (the production `tls_context`).

**Provider availability check**
- Exactly **one** uncached GET to the provider's public search endpoint, the same constant the production client uses.
- 15 s timeout, 2 MB cap, no retries, no API keys, no cache reads or writes.
- Success requires HTTP 200 **and** JSON with the provider's documented result structure.
- Errors, malformed or empty bodies, and error objects all fail.

**Staged re-download**
- Streams into `download.part`, opened with exclusive create (`xb`), so nothing is ever overwritten.
- Caps: 25 MB (declared and streamed) and 60 s overall.
- Media `Content-Type` required (`image/*`, `video/*` or `application/octet-stream`; enforced exactly since F9); the production `detect_magic_type` signature check plus a Pillow decode must pass.
- The file is then renamed to `staged_<sha16>.<ext>`. The rename fails rather than replace an existing file.
- On any failure the partial file is deleted.
- The destination is always `data/autonomy/self_healing/staging/<request id>/`, never a job folder.

### F3: live check before file-reading diagnostics (`handlers.py`)

Those four diagnostics now re-read the live job state at execution time. They refuse while the job is RUNNING or RETRYING, has a fresh heartbeat, or is QUEUED, RETRY_SCHEDULED, HANDOFF_PENDING or PAUSED. `check_disk_space` and `generate_diagnostic_report` open no job files and are unaffected.

### F4: redaction (`store.py`)

`message`, `template` and `error_type` pass through the control plane's `redact` before they are stored.

### F5: bounded history and quarantine (`store.py`, `engine.py`)

| Item | Bound |
|---|---|
| Finished recoveries | Orphans pruned; cap 20,000 |
| Notification keys | Pruned after 30 days |
| Recovery queries | `LIMIT` added |
| Staged downloads | Removed after 7 days |
| Diagnostic reports | Removed after 30 days |

Quarantine cleanup touches only regular, non-symlink files directly inside the engine's own folders.

### F6: reads create no schema (`engine.py`, `store.py`, `main.py`)

- `store.tables_exist()` is a read-only check.
- `overview`, `list_incidents`, `history`, `incident_detail`, operator actions and `sync` return empty or 404 until the first scan at OBSERVE or above creates the tables.
- The API routes go through the engine instead of the store.

### F7: timeout wording (`engine.py`)

The failure reason now says the operation may still have been running and that its resource lock stays held until it ends.

### Registry descriptions (`registry.py`)

Updated to describe the guarded behaviour exactly.

## 4. Verified properties (with evidence)

| Property | Evidence |
|---|---|
| Closed registry | All three actions are `PERMITTED` entries; handlers only for permitted types (import-time assert); 26 prohibited entries. Tests: `test_registry_permitted_and_prohibited_actions`. |
| No arbitrary execution | Exactly one `subprocess.run` (fixed ffprobe argv, no shell). No `eval`, `exec`, `compile`, `os.system`, `pickle`, `__import__`, `ctypes` or `urlopen`. Payloads are strictly validated (diagnostic choice list, provider choice list, query character class, `https://` URL that must be in the incident evidence). Tests: `test_static_no_dangerous_calls_or_production_writes`, `test_source_has_no_generic_execution_and_no_production_writes`. |
| No self-approval | Reserved identities are refused for approve, reject, execute, set_level and standing grants (API and engine). Tests: `test_self_approval_and_identity_protection`. |
| No self-execution without independent approval | Without a human-granted standing approval nothing runs. Medium-risk actions cannot get standing approval, even if one is forged into the configuration table. Test: `test_engine_never_executes_without_independent_approval`. |
| Identity normalization | ASCII-only identity pattern (Unicode lookalikes, full-width, zero-width and control characters refused); case-insensitive; separators ignored. Tests: 26 cases in `test_reserved_and_lookalike_identities_are_refused`. |
| Global and category levels | min(global, category); each combination tested. Test: `test_global_and_category_levels_are_both_enforced`. |
| Signatures and payload hashes | Tampered payload, forged status, broken signature, changed approver, expired, rejected and cancelled requests: all refused. A completed request cannot run twice. Tests: `test_tampered_or_closed_requests_never_execute` (7 cases), `test_completed_request_cannot_run_twice`. |
| Kill switches at execution time | The control plane gate runs inside the claim transaction and again before each attempt. A sentinel or dashboard switch set after approval blocks execution. Test: `test_kill_switch_checked_at_execution_time` (plus the 3.32B tests). |
| API security | Loopback-only, same-origin, `Sec-Fetch-Site`, JSON-only (415), CSRF token (constant-time compare), `extra=forbid` (422), bounded query parameters (400). CORS is limited to the dashboard origins, and the CSRF header is not an allowed CORS header. Test: `test_api_write_guards`. |
| Network destinations | Allow-list, public-address check, every redirect hop, size and time caps, no cache. Tests: 10 + 7 + 1 destination tests, redirect tests, 8 rejection cases, success, no-overwrite. |
| Path safety | `inside()` resolves the path, junctions included, against the media and assets roots; paths outside are never read. Staging and report paths are built from validated ids. Tests: `test_paths_outside_media_roots_are_never_read`, `test_guarded_download_never_overwrites`. |
| Windows locking | A real exclusive handle (`CreateFileW`, no sharing) is detected; our probe leaves no handle open (a rename afterwards succeeds). Test: `test_windows_exclusive_lock_is_detected`. |
| Concurrency | Atomic claim (one execution of 4 threads); single approval of 5 concurrent approvals; two engine instances scanning in parallel create no duplicates; lock held through a timeout; interrupted execution reconciled to FAILED and never re-run. Tests: `test_concurrent_execution_prevention`, `test_simultaneous_approvals_record_one_approval`, `test_concurrent_scans_do_not_duplicate`, `test_timeout_keeps_the_resource_locked_until_the_operation_ends`, `test_interrupted_execution_is_reconciled_not_rerun`. |
| Production isolation | Production tables are byte-identical after a full flow; no INSERT, UPDATE or DELETE on production tables in the package source; no scheduler, worker, rendering, publishing, OAuth, configuration or topic-history imports; no production media written. Tests: `test_production_tables_and_files_untouched`, static tests. |
| Privacy | Secret in error text is not present in any API response or `selfheal_*` row. Test: `test_api_never_exposes_secrets`. |
| Integration | Recommendation runs read self-healing data without writing it or proposing anything; scans never run recommendations; one kill switch stops both; no import side effects or cycles. Tests: `test_recommendation_engine_and_self_healing_stay_independent`, `test_import_has_no_side_effects_and_no_cycles`. |

## 5. Tests

- **New file `backend/tests/test_self_healing_332d_security.py`:** 50 test functions, **99 cases**.
- **Existing `test_self_healing_332d.py`:** 53 cases, unchanged, still pass.
- **Exact results:** see `STAGE_3_32D_TEST_RESULTS.md`.

## 6. Remaining limitations

- **Network actions depend on public CDNs.** The staged re-download only proposes URLs recorded in an error message on the allow-listed hosts. The address check runs before connecting, and a resolver race remains theoretically possible (mitigated: the allow-listed hosts are major public CDNs, TLS hostname verification applies, and the response must be valid media).
- **The provider check proves endpoint availability, not search quality.**
- **Timeouts cannot kill a running thread** (Python limitation). The resource lock stays held until it ends, so nothing overlaps, and network operations have their own socket timeouts and deadlines.
- **Scan time is dominated by SQLite commits.** It is 3.6–4.9 s while a production render is running and 1.3–2.2 s when idle, for 10 new incidents. Scans are operator-triggered only.
- **R1 (DNS rebinding) is unchanged** (pre-existing; writes remain protected).

## 7. Deployment prerequisites

1. **Review and commit the remediation** (not done; awaiting authorization).
2. **Back up** `studio.db` and the git state into a new folder.
3. **Restart only the dashboard** (Task Scheduler `CrazzyDocs-Health-Dashboard`, between :10 and :50, with no job in a critical stage). Since the 13:34 reboot, the dashboard already runs unremediated `d2e0928`, so this restart is what puts the fixes into effect.
   - The daemon does not use the self-healing package on any production path. After the commit it will report "stale code", which is harmless and the same situation as today's earlier deployments.
   - The monitor process is not running, so a stopped dashboard is not relaunched automatically. Start the scheduled task explicitly.
4. **Configuration:** no migration and no configuration change.
   - The six `selfheal_*` tables already exist, empty, created by F6 before the fix. They are compatible: the fixes add no columns.
   - No scan can run while all levels are OFF and the startup kill switch is active.

## 8. Production event during the audit (not caused by the audit)

At **13:33:42 IST** Windows Update (`MoNotificationUx.exe`, reason "Operating System: Service pack (Planned)") restarted the computer. At logon (13:34:48) the scheduled tasks relaunched production from the files on disk.

**What is running now:**
- daemon PID 7648 and dashboard PID 15704, both on revision `a497740e5c2df372` = **`d2e0928`**;
- **Stage 3.32D as committed, without this audit's remediation**, is therefore already running.

**State verified read-only afterwards:**

| Item | State |
|---|---|
| Self-healing mode | BLOCKED (`startup_config` kill switch; no `autonomy_startup.json`) |
| Autonomy levels | All OFF; `autonomy_config` empty |
| `selfheal_*` tables | Exist and are **empty**: no scan ran, and there are no requests or executions |
| Audit trail | 1 event only (the blocked recommendation run from the 3.32C deployment check) |
| Schedule | `ccf0bd04f159`, in sync |
| Jobs | Progressing normally |

- **How the tables appeared:** a read of the self-healing endpoint created them. That is finding **F6**, observed in production; the caller could not be determined.
- **Exposure:** F1–F5 cannot be reached in this state. They require a scan or an execution, and both are blocked.
- **Monitor:** the process (`startup_manager.py --monitor`, previously PID 8132) did not restart after the reboot. The `CrazzyDocs-Startup-Manager` task completed (result 0); the Health-Dashboard and Health-Monitoring tasks report result 1. It was not restarted by the audit.

## 9. Independent re-verification (after `7a0de45`)

A second pass re-audited the committed code at `7a0de45` (`main` = `origin/main`): `services.py`, `handlers.py`, artifact pruning and `sync` in `engine.py`, and the self-healing routes in `main.py`. F1–F8 are confirmed fixed in code. Two further gaps were found and fixed:

| ID | Severity | Finding | Status |
|---|---|---|---|
| F9 | Low | **The Content-Type check did not match its description.** The docs said a media `Content-Type` was required, but a response with **no** `Content-Type` was accepted, and the check was a substring match, so `text/html; profile=image` also passed. The signature check and Pillow decode still applied, so no non-media file could be staged. | **Fixed.** The media type is parsed (parameters ignored) and must be `image/*`, `video/*` or `application/octet-stream`; a missing header is refused. 2 new test cases. |
| F10 | Info | **IPv6 forms that embed a private IPv4 address passed the public-address check.** On Python 3.11, `ipaddress` reports NAT64 (`64:ff9b::7f00:1`) and 6to4 (`2002:7f00:1::`) addresses as global. Reaching this would need an allow-listed CDN hostname to resolve to such an address (DNS compromise) on a NAT64-capable network. | **Fixed.** `_address_public` also rejects private IPv4 embedded through mapped, 6to4 or NAT64 forms. 9 new test cases (5 refused, 4 public allowed). |

**Reviewed and left unchanged (by design):**
- `GET` history, overview and incident detail call `sync()`. It only mirrors decisions already made in the control plane into `selfheal_*` rows, and returns early when the tables do not exist. It runs no action and creates no schema.
- File diagnostics check that the job is idle (`_require_job_idle`) before taking the self-healing lock. The scheduler does not observe those locks, so a job could in theory restart in that window. The window is only milliseconds, a failed job restarts only inside its recovery window, and the diagnostics only read with bounded size.
- R1 (DNS rebinding) is unchanged: a pre-existing 3.32B issue, and every write still needs an allow-listed `Origin`.

**Security suite:** 110 cases (was 99).

## 10. Confirmation

**The audit itself did not modify production.** The only change was the Windows Update restart described in section 8. Beyond that:
- No process was restarted.
- No autonomy level, standing approval or kill switch was changed.
- No schedule, recovery window, retry policy, job, media, `.env.local` or OAuth data was touched.
- All tests ran against temporary databases with fake network transports.
- Dry runs used a scratch copy of the 2026-11-20 backup.
- The live database was read only (read-only connections) to confirm its state.
