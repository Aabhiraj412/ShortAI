# Stage 3.34: validation report

**Status markers:** **LIVE** = observed on a real scheduled production. **ARTIFACT** = measured on real production outputs. **SYNTHETIC** = isolated tests with fakes, synthetic media and temporary databases.

## 1. Live production observation (LIVE)

One naturally scheduled cycle was observed read-only, every 10 seconds: `cycle_4_20261121_0300`. No cycle was created or forced, and nothing was restarted while it ran.

| Measure | Result |
|---|---|
| Outcome | COMPLETED on attempt 1 (retry_count 0), 03:00:00 to 03:09:22 IST (**9.4 minutes**); queued for publishing |
| Stage timeline (progress events) | topic discovery 62 s, script 58 s, narration 13 s (49.2 s of audio), assets 24 s (7 acquired, 3 failed safely), animations and illustrations about 77 s, render plus mux about 5 minutes, thumbnail and handoff about 45 s |
| Worker heartbeat | **Maximum age 27.1 s** across 25 samples (30 s interval, 180 s stall threshold), including through the browser render |
| Renderer concurrency | One render tree (Node plus 12 Edge processes during frame capture); no second renderer |
| Peak memory (sampled) | About 2.9 GB in total during the final mux: FFmpeg about 1.6 GB, Edge about 0.8 GB, Node about 0.23 GB, Python processes about 0.28 GB |
| Demand shadow | 75 signals collected (60 Wikipedia, 15 Trends) and persisted; selection unchanged (shadow) |
| Stage display | Showed `VOICEOVER` for about 6 minutes while the worker was rendering: defect D5, fixed |

This was the code deployed before Stage 3.34. The Stage 3.34 fixes take effect from the next cycle after deployment; their live effect is observed from then on (see the known-issues file).

## 2. Real artifact validation (ARTIFACT)

| Check | Result |
|---|---|
| Re-render of a real Pop Info film (Stage 3.33) | 54.2 s film in 138.6 s, peak 1.38 GB, 1625 frames, valid MP4 |
| Colour range of production outputs | Full range, 97 % of frames at luma 255 → fix D7. The before/after on the 03:00 film is in the implementation report |
| Creative evaluation of live productions | 3 records, all linked to YouTube video IDs, 0 evaluation errors |
| Duration of recent finals | 51.2–54.2 s (accepted window 30–58 s) |
| ffprobe on a truncated MP4 (moov atom missing) | Detected as a validation failure; a good file is parsed (SYNTHETIC, `test_partial_mp4_and_probe_failure_are_validation_failures`) |

## 3. Failure scenarios (SYNTHETIC, isolated)

| Scenario | Expected behaviour | Evidence |
|---|---|---|
| Topic source unavailable | Provider error recorded; others continue; classified TRANSIENT | 3.31C/D provider-failure tests; `test_one_failing_provider_does_not_stop_others`; classification test |
| Empty candidate pool | Fails closed without an old-topic fallback; bounded exhaustion recovery | 3.31D tests (5); classification TRANSIENT |
| Script generation failure | Model chain, then extractive fallback; never fails the production | grounded script writer tests (in the 242 related tests) |
| Voiceover timeout | 3 bounded attempts; TRANSIENT; no partial file | `test_voiceover_timeout_retries_then_fails_transiently_without_partial_files` |
| Missing or corrupt visual asset | Rejected with cleanup; production continues with the remaining assets | downloader tests (corrupt, wrong type, network errors) |
| File-lock contention | Transient locks retried (bounded); other errors immediate; cleanup never raises | `fs_utils` tests; the WinError 32 documentation test |
| Renderer readiness timeout | Detected; a deterministic RenderFailure (no whole-pipeline retries) | `test_readiness_failure_is_detected_and_deterministic`, page-bounds test, existing render reliability tests |
| FFmpeg failure | Geometry errors DETERMINISTIC; bounded stderr; no partial output | 3.32H geometry tests (55) |
| Invalid or incomplete MP4 / ffprobe failure | Validation failure, distinct from render failure | partial-MP4 test; duration boundary test |
| Upload interruption | Connection errors TRANSIENT; interrupted uploads resumable at startup | classification test; existing publishing suite |
| YouTube quota exhaustion | PERMANENT; quota re-arm only inside the recovery window | classification test; existing quota suite (12 tests) |
| Duplicate job claim | Second live worker refused; duplicate trigger skipped | `test_worker_registry_refuses_a_second_worker_for_the_same_job`; scheduler suites |
| Scheduler restart while idle | Refused while a job runs; proceeds only when idle; synthetic jobs never block | `test_restart_is_refused_while_a_job_runs_and_allowed_when_idle`; daemon restart suite |
| Worker heartbeat expiry | Stalled only when the worker is dead **and** the heartbeat is older than 180 s; a live worker is never replaced | 4 liveness and stall tests; live heartbeat evidence (§1) |
| Recovery-window expiry | Terminal `RECOVERY_WINDOW_EXPIRED`, never retried after the window | existing recovery suites (13 tests) |

## 4. Test results

- `test_production_reliability_334.py`: **36 passed**.
- `test_topic_demand_331k.py`: **31 passed** (adds the shadow-evidence test).
- Pipeline-related suites (Pop Info, render, Lemo, orchestrator, download, media, TTS, deployment, worker, reconciliation, locks, daemon, health): **293 passed, 13 skipped**.
- Full suite: see section 12 of `STAGE_3_32E_TEST_RESULTS.md` for the final counts.

## 5. Not tested (and why)

- **A live cycle on the Stage 3.34 code:** deployment happens after testing; the next scheduled cycles provide that evidence (monitored by creative evaluation, the health monitor and the reports).
- **Real YouTube upload interruption and quota exhaustion:** these would need external actions; covered by the internal pathways and existing suites.
- **Public publishing and Community API publishing:** gated; not exercised.
- **Antivirus-induced locks:** not reproducible on demand; covered by the injected WinError 32 tests.
