# Stage 3.32E: Test Results

| | |
|---|---|
| **Date** | 2026-11-20 |
| **Base** | `687c156` plus the uncommitted Stage 3.32E work |
| **Worktree** | `crazzy-docs-handover-b1a088` |
| **Interpreter** | `D:/Agents/CrazzyDocs/.venv/Scripts/python.exe` (Python 3.11.9, Pillow 13.0.0) |
| **`PYTHONPATH`** | the worktree, plus `backend` and `backend/tests` |
| **Data** | Temporary SQLite databases and media folders only. The scratch-data dry runs used SQLite online-backup copies of `studio.db` and production `media/`, read-only |

## 1. New suite: `backend/tests/test_community_posts_332e.py`

**75 cases, all pass** (about 140 s).

| Area | Tests |
|---|---|
| **Content intelligence** | |
| Sources | `test_sources_read_dossiers_videos_and_candidates`: dossiers, public vs private video, candidates, truncated facts dropped |
| Topic relevance | `test_channel_relevance_and_excluded_categories` |
| Topic and format rotation | `test_topic_and_format_rotation_over_a_week` (7 days: 7 distinct topics, no format twice in a row, ≥ 5 formats); `test_permitted_formats_only` |
| Published-video context | `test_video_followup_links_only_public_videos` |
| Fallback without paid API | `test_template_generation_needs_no_network_or_paid_api` (sockets blocked) |
| Gemini optional and bounded | `test_gemini_is_optional_validated_and_bounded` (invalid output, template kept; 2 calls a day; never called when off) |
| Facts and quiz | `test_clean_fact_and_mcq_parts`, `test_multiple_choice_options_are_allowed_numbers` |
| Unsupported claims and warnings | 12 cases in `test_text_validation_checks`; `test_clean_text_is_valid_and_duplicates_are_rejected`; `test_warnings_never_count_as_valid` |
| **Image management** | |
| Valid and invalid images | 11 cases in `test_image_validation_checks`: missing, aspect, corrupt, oversized, duplicate hash, tampered, no provenance, CC BY without credit, licence, format, too small. Also `test_valid_image_and_credit_present` |
| Licences and provenance | `test_licence_normalisation`, `test_licensed_candidates_need_verified_manifest_match` |
| Safe replacement and no overwrite | `test_image_card_is_square_and_never_overwrites`; `test_regenerate_text_and_image`; `test_cc_by_image_adds_credit_line` |
| Windows file locking | `test_locked_source_image_falls_back_without_crashing` (a real `CreateFileW` handle with no sharing) |
| **Scheduling** | |
| Asia/Kolkata handling | `test_ist_schedule_and_next_run` |
| Daily idempotency and restart recovery | `test_daily_idempotency_and_restart` |
| Manual and scheduled overlap | `test_manual_and_scheduled_overlap` |
| Concurrent generation | `test_concurrent_generation_makes_one_draft` (6 threads) |
| Pause and resume | `test_pause_resume_disable` |
| Failure and retry | `test_failure_and_bounded_retry`, `test_retries_stop_after_limit`, `test_missed_window_is_recorded_and_notified`, `test_interrupted_run_is_reconciled_not_resumed`, `test_generation_timeout`, `test_daily_limits` |
| Reads create nothing | `test_tick_and_reads_before_configuration_create_nothing` |
| **Draft lifecycle** | |
| Valid and invalid transitions; edit history; approval and rejection; archiving | `test_lifecycle_transitions_and_audit` (also proves the append-only UPDATE trigger), `test_transition_table_is_closed` |
| Duplicate requests | `test_stale_and_duplicate_requests_are_refused` |
| Warning acknowledgement | `test_approval_of_warnings_needs_acknowledgement`, `test_rejected_validation_can_never_be_approved` |
| Regeneration | `test_regenerate_text_and_image` |
| Tamper and archive | `test_approve_refuses_tampered_image`, `test_archive_frees_the_date` |
| **Security** | |
| Unauthorised calls, CSRF, Origin, Sec-Fetch-Site, JSON-only, extra fields, reserved identities, bounded parameters | `test_api_guards` |
| Path traversal and arbitrary file access | `test_api_path_traversal_and_file_access` (a tampered image record pointing outside the folder is not served) |
| Arbitrary URLs | `test_text_validation_checks[urls]`, `test_video_followup_links_only_public_videos`; no route takes a URL |
| Publishing isolation | `test_publishing_is_unreachable_from_community_code` (AST import allow-list, forbidden strings, 16 routes, registry PROHIBITED) |
| Kill switches | 3 cases in `test_kill_switches_block_all_generation`; `test_operator_identity_is_validated` |
| Secrets | `test_secrets_are_redacted_from_errors` |
| **Integration** | |
| End-to-end API | `test_api_end_to_end_flow` |
| Scheduler isolation | `test_scheduler_job_is_isolated_from_production` |
| Production data untouched | `test_production_data_untouched_by_full_flow` (production tables and media byte-identical, network blocked) |
| Notifications | `test_notifications_default_to_dashboard_when_inbox_is_off` (Control Plane OFF: nothing submitted; daily cap) |
| Retention | `test_retention_prunes_old_drafts_and_files` |
| Dashboard | `test_dashboard_community_tab` (elements present, other tabs intact, `node` parses the script) |
| Performance | `test_performance_budget` |

## 2. Related existing suites

Stages 3.32A, B, C and D, the 3.32D security suite, schedule profiles, user-controlled publishing, the scheduler tests and the dashboard tests:

**343 passed, 2 failed, 6 skipped.** Both failures are on the known baseline list:
- `test_dashboard_html_page_served` fails identically on the unmodified checkout: it expects an old page title.
- `test_slow_youtube_status_does_not_block_other_dashboard_requests` is timing-sensitive.

## 3. Full regression suite

```
4 failed, 1295 passed, 173 skipped, 3 warnings in 976.59s
```

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| `687c156` (before 3.32E) | 1220 | 4 | 173 |
| **`687c156` + Stage 3.32E** | **1295** (+75, exactly the new cases) | 4 | 173 |

The 4 failures are the known pre-existing ones:
- `test_dashboard_html_page_served`;
- `test_end_to_end_orchestration_run`;
- `test_youtube_branding` × 2.

The fifth known, timing-sensitive test passed in this run. **No regressions.**

## 4. Real-data dry runs (scratch copies, production untouched)

- **Ten simulated days** on a copy of `studio.db` with production `media/` read-only:
  - all eight formats appeared except video follow-up (only one public video exists);
  - 10 distinct topics;
  - licensed images (CC0, Public Domain, CC BY with credit) and title cards;
  - 9 of 10 drafts VALID; the contested-ancestry topic was routed to NEEDS_OPERATOR_REVIEW.
- **These runs found and fixed three issues before the tests were written:**
  - truncated dossier facts;
  - HTML entities and over-long image credits;
  - sensitive subjects reaching VALID.
- **The test suite then found two service bugs, both fixed:**
  - a missed generation window still generated a draft (the stale run status was checked);
  - the tick result shape overwrote its own status.
- **Dashboard (isolated preview on port 8765, scratch database):**
  - generate, replace image, approve and the audit history all work;
  - no console errors;
  - at 375 px wide there is no horizontal scroll, after an image-overflow fix.

## 5. Performance (this laptop: AMD Radeon RX Vega 10 with 2 GB VRAM, about 13.9 GB usable RAM; no GPU used)

| Measurement | Result |
|---|---|
| Draft generation, warm (real data) | 0.3–0.8 s total: sources 16–220 ms, text < 1 ms, image 110–690 ms, validation 60–160 ms |
| First generation in a cold process | 11–15 s, dominated by one-time imports of the `backend.app.content` package (about 3 s for `number_words`). The daemon and dashboard already have these modules loaded |
| Memory | Peak traced Python allocation 59 MB on a cold run, imports included; under 1 MB per warm generation (Pillow buffers about 3.5 MB for one 1080 × 1080 image) |
| Database operations | Overview 60 ms warm (188 ms cold); draft list 16–22 ms; draft detail 58 ms; schedule 29–37 ms; history 28 ms |
| Dashboard | Page load event 88 ms (local); the Community tab's API calls total about 150 ms |
| Scheduler cost | One config read every 5 minutes when nothing is due; zero database access before configuration |

## 6. Windows compatibility

- Exclusive-create (`xb`) temporary files are renamed into place, and the rename fails rather than overwrite.
- A real exclusive `CreateFileW` lock on a source asset is handled by falling back without a crash.
- Paths are rebuilt only from validated ids. Junctions and symlinks are rejected through `resolve()` containment and `is_symlink()`.
- Fonts come from `C:\Windows\Fonts` (Segoe UI), with an Arial or default fallback.
- Time zones: `zoneinfo` with `Asia/Kolkata`.

## 7. Timezone-aware schedule (2026-11-20, evening)

**New suite `backend/tests/test_community_schedule_tz_332e.py`: 52 cases, all pass.** Imports were confirmed to come from the development worktree (`...\crazzy-docs-handover-b1a088\backend\app\community\schedule.py`).

| Area | Tests |
|---|---|
| Every preset timezone (8) | `test_every_preset_timezone`: same 09:00 wall clock in winter and summer, correct UTC offsets, DST observed or not, next transition |
| Preset and zone lists | `test_presets_listing_and_zone_list` (547 IANA ids, no fixed-offset or legacy names) |
| Invalid timezone rejection (13) | `test_invalid_timezones_are_rejected_without_saving` (`EST`, `Etc/GMT+5`, `UTC+5`, wrong case, trailing space, ...): nothing saved, no audit entry |
| Invalid settings (11) | `test_invalid_settings_are_rejected_atomically`: the valid timezone in the same request is not saved |
| DST transitions | `test_dst_transitions_keep_the_local_wall_clock` |
| Spring-forward nonexistent time | `test_spring_forward_nonexistent_time_shifts_forward`, `test_spring_forward_day_generates_exactly_once` (runs at 03:30 EDT) |
| Fall-back ambiguous time | `test_fall_back_ambiguous_time_uses_first_occurrence`, `test_fall_back_day_generates_exactly_once` (ticks every 5 minutes through the repeated hour: one draft) |
| Catch-up across DST | `test_catch_up_window_is_elapsed_time_across_dst` |
| Next-run calculation | `test_next_run_in_selected_timezone` |
| Duplicate prevention by local date | `test_duplicate_prevention_by_local_calendar_date`, `test_late_target_catch_up_crosses_local_midnight` |
| Catch-up and missed | `test_catch_up_and_missed` |
| Maximum daily attempts (1, 2, 3) | `test_maximum_daily_attempts` |
| Save and load, audit, no duplicate rows or events | `test_settings_save_load_and_audit` |
| Automation OFF | `test_automation_off_generates_nothing` |
| No retroactive run on enabling | `test_enabling_after_todays_target_waits_for_tomorrow` |
| Preview saves nothing | `test_preview_does_not_save` |
| API authorization, CSRF, Origin, JSON-only, extra fields, reserved identity, validation | `test_schedule_api_security_and_validation` |
| No YouTube or network access; production jobs and profile unchanged | `test_scheduling_never_reaches_youtube_or_production` |

**Main 3.32E suite:** 76 cases, all pass. Five tests now enable automation before the day's target time, because enabling is no longer retroactive. The route-count and import allow-list assertions were updated (18 routes, the `schedule` module).

**Full regression suite:**

```
5 failed, 1347 passed, 173 skipped, 3 warnings in 1777.43s
```

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| `89ea49b` | 1295 | 4 | 173 |
| **`97b7990` + timezone-aware schedule** | **1347** (+1 validator test, +52 schedule tests) | 5 | 173 |

All 5 failures are the known pre-existing ones:
- `test_dashboard_html_page_served`;
- `test_end_to_end_orchestration_run`;
- `test_youtube_branding` × 2;
- the timing-sensitive `test_slow_youtube_status_does_not_block_other_dashboard_requests`, which passed in the previous run.

The totals reconcile exactly (1352 = 1299 + 53). **No regressions.**

**Dashboard check:** done in an isolated preview (port 8765, scratch copy of the database, worktree code):
- presets, IANA search, offset, DST and next-change line;
- live preview in the selected timezone and the browser's;
- save with the confirmation text, status panel and last configuration update;
- the future-capability publishing box.

No console errors except connection refusals while the preview server restarted, and a 404 for the draft image the scratch copy does not hold.

## 8. Stage 3.32F: publishing, capability audit, operator attribution (2026-11-20, night)

**New suite `backend/tests/test_community_publishing_332f.py`: 36 cases, all pass.** Every YouTube call is a fake; no network.

| Area | Tests |
|---|---|
| Capability detection (10 outcomes and error shapes) | `test_capability_detection`: available; read-only scopes; pilot 404 and 403; API not enabled; scope error; channelNotFound; channel lookup refused; quota; timeout. Only `channels.list` and `posts.list` are ever called, and the token never reaches the database |
| Pilot denied plus missing insert scope | `test_pilot_denied_also_reports_missing_insert_scope` |
| Not connected and unexpected errors fail closed (redacted) | `test_not_connected_and_unexpected_errors_fail_closed` |
| Enabling needs a fresh API_AVAILABLE and confirm; the schedule path cannot enable | `test_enabling_requires_fresh_api_available` |
| Publishing disabled by default: no calls | `test_publishing_disabled_by_default_makes_no_calls` |
| Pilot denied blocks even a forged enable flag | `test_pilot_denied_blocks_publishing` |
| Success: request, response, post id and URL, audit; the draft is frozen afterwards | `test_successful_publish_records_everything` |
| Duplicate prevention and idempotency (application and DB partial unique index) | `test_duplicate_and_idempotent_publication` |
| Invalid or stale draft, missing confirm, image and text tampering | `test_invalid_or_stale_drafts_are_refused` (5 cases): no upload or insert |
| Timeout after a possibly successful insert | `test_timeout_after_insert_is_uncertain_and_never_retried`: UNCERTAIN, no retry, frozen draft, reconcile with no match and then a match |
| Operator resolution, then explicit retry | `test_operator_resolution_of_uncertain` |
| Quota exhaustion and definitive failures | `test_definitive_failures_and_quota` (4 cases) |
| Daily API publication limit | `test_daily_api_publication_limit` |
| Manual handoff (approval required, idempotent, URL validation, operator-reported, frozen draft) | `test_manual_handoff`, `test_handoff_refuses_changed_content_and_can_be_cancelled` |
| Audit integrity | `test_audit_trail_is_append_only` |
| No publishing from automation, ON or OFF | `test_automation_never_publishes` |
| No network when unavailable | `test_no_network_when_publishing_is_unavailable` |
| API: loopback, CSRF, confirm, 423 gates, extra fields, ids, download header; operator optional (`local-dashboard`) | `test_api_routes_security_and_operator_optional` |
| Reads create nothing | `test_publishing_status_before_anything_creates_nothing` |

**Updated 3.32E tests:**
- `test_publishing_is_confined_to_the_gated_publishing_modules` replaces the old "no publish route" test with the new boundary: network and OAuth code only in `youtube_posts.py`, using documented endpoints only, imported only by `publishing.py`; draft generation never publishes; autonomy publishing still PROHIBITED; 27 routes.
- `test_operator_identity_is_validated`: a blank or absent operator means `local-dashboard`.
- `test_dashboard_community_tab`: the Publish button is rendered only behind `api_publishing_ready`, and there is no Operator field.

**Full regression suite:**

```
5 failed, 1383 passed, 173 skipped, 3 warnings in 1273.13s
```

1388 tests = 1352 + 36 new. All 5 failures are the known pre-existing ones. **No regressions.**

**Live read-only capability audit:** `PILOT_ACCESS_REQUIRED`, plus the missing insert scope. See `STAGE_3_32F_PUBLISHING.md` §1.

**Dashboard preview** (scratch copy, port 8766): no Operator field; readiness card with the real audit result; only "Run capability check" and "Manual handoff" buttons, with no Publish button; the handoff panel shows the image, text, download, the Open YouTube link and the steps.

## 9. Stage 3.32G: Controlled Autopilot (2026-11-20, late evening)

**New suite `backend/tests/test_autopilot_332g.py`: 31 cases, all pass.**

| Area | Tests |
|---|---|
| All modes | `test_all_autonomy_modes` (OFF, OBSERVE, RECOMMEND, APPROVED_EXECUTION) |
| Startup safety default | `test_startup_default_blocks_everything` |
| Permission policy and tiers | `test_centralized_action_policy` |
| Prohibited actions | `test_prohibited_actions_cannot_be_proposed` (5, including the new external communication and financial entries) |
| Approval lifecycle and expiry | `test_approval_lifecycle_and_expiry` |
| Duplicates | `test_duplicate_proposals_and_executions` |
| Concurrency | `test_concurrent_execution_runs_once` (5 threads, 1 run, never 2 at once) |
| Timeout safety | `test_timed_out_handler_keeps_its_lease_until_it_really_ends` |
| Heartbeat and ownership | `test_stale_heartbeat_with_live_owner_is_never_taken_over`, `test_lease_of_a_dead_owner_is_reclaimed_and_audited`, `test_fresh_lease_of_a_dead_owner_is_not_reclaimed_early` |
| Interrupted execution | `test_interrupted_execution_is_reconciled_not_rerun` |
| Circuit breaker | `test_circuit_breaker_opens_after_repeated_failures` |
| Retry limit and backoff | `test_retry_limit_and_backoff` |
| Kill switches | `test_kill_switch_blocks_execution` (sentinel and dashboard) |
| Unsafe AI recommendations | `test_bridge_creates_pending_requests_from_mapped_actions_only`, `test_unsafe_or_unmapped_follow_ups_are_refused` (6), `test_bridge_respects_levels_status_and_identity` |
| Audit completeness | `test_audit_trail_is_complete_and_chained` |
| Dashboard and backend consistency | `test_overview_reflects_persisted_state`, `test_dashboard_autopilot_tab_never_executes` |
| API security | `test_autopilot_api` |

**Updated existing tests:**
- The 3.32B and 3.32C table-set assertions now include `autonomy_execution_leases`.
- The 3.32B import allow-list now includes the four modules that `autopilot.py` reads for status.

**Full regression suite:** `5 failed, 1414 passed, 173 skipped in 1542.47s` (1419 tests = 1388 + 31).
- One failure was new: the 3.32B import allow-list, a test-only expectation. After updating it, the 3.32B and 3.32G suites pass (78 cases).
- **Effective result: 1415 passed, 4 failed.** All 4 are the known baseline (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, `test_youtube_branding` × 2); the timing-sensitive dashboard test passed in this run.
- **No regressions.**

## 10. Stages 3.32H, 3.32I and 3.32J: reliability, activation, reports (2026-11-21)

| Suite | Result |
|---|---|
| `test_render_geometry_332h.py` (real FFmpeg on synthetic media: 7 sources × 5 targets, Priority B adapter with MP4 and GIF, invalid input, timeout) | 55 passed |
| `test_reliability_activation_reports_332hij.py` (health evaluator, probes, monitor loop, endpoints, every phase transition and refusal, reports, API, dashboard and scheduler wiring) | 40 passed |
| Related suites (autonomy, autopilot, recommendations, self-healing, startup, daemon restart, scheduler, retry, community) | 674 passed + 118 after the allow-list update |
| **Full suite** | **1510 passed, 4 failed, 173 skipped** (20 min 37 s) |

The 4 failures are the known baseline: `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run` and both `test_youtube_branding` tests. No new failures. The 3.32B source-safety allow-list now includes `autonomy.activation`, `autonomy.reports`, `core.health_monitoring` and `rendering.ffmpeg_geometry`, with a rationale.

## 11. Roadmap stages 3.31K, 3.32 (creative intelligence) and 3.33 (2026-11-21)

| Suite | Result |
|---|---|
| `test_topic_demand_331k.py` (filters, collector, provenance, ranking, scoring v2 bounds, analytics provider, expanded scope, flags, engine off/shadow/on) | 30 passed |
| `test_creative_intel_332.py` (script, scene, subtitle and render dimensions on synthetic video; store and video linking; experiments and hook assignment; runner; API) | 16 passed |
| Related existing suites (3.31A–D, topic, script writer, scheduler, category, hook, creative) | 242 passed |
| **Full suite** | **1557 passed, 4 failed, 173 skipped** (17 min 47 s) |

The 4 failures are the known baseline (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, both `test_youtube_branding`). No new failures. Note: `test_stage_3_19_creative_identity_e2e.py` hardcodes the main checkout path and trips the worktree isolation guard when run on its own selection (pre-existing).

Stage 3.33 benchmarks: `docs/benchmarks/stage_3_33_render_benchmarks.json` (run with no production job active).

## 12. Stage 3.34: controlled production validation and reliability (2026-11-21)

| Suite | Result |
|---|---|
| `test_production_reliability_334.py` (liveness, stall detection, heartbeat pump, file-lock retries, downloader oversize/retry/cleanup, voiceover timeout, failure classification, readiness, partial MP4, duplicate claims, idle restart, render slot, live stage, delivery colour format) | 36 passed |
| `test_topic_demand_331k.py` (+ shadow evidence persisted) | 31 passed |
| Pipeline-related suites (Pop Info, render, Lemo, orchestrator, download, media, TTS, deployment, worker, reconciliation, locks, daemon, health) | 293 passed, 13 skipped |
| **Full suite** | **1594 passed, 4 failed, 173 skipped** (18 min 20 s) |

The 4 failures are the known baseline (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, both `test_youtube_branding`). No new failures, none resolved, no flaky tests observed. No lint, type-check or dependency-audit tool is configured in this repository; the changed modules compile, and no dependency was added. Tests use temporary databases; the production database is refused under pytest (existing guard).

## 13. Stage 3.35: analytics, learning, providers and footage (2026-11-21)

| Suite | Result |
|---|---|
| `test_analytics_learning_footage_335.py` (OAuth scope merge, lock, rotation, revocation, expiry, concurrency and restart; analytics statuses, first-7-days window, idempotency, budget; learning thresholds, outliers, Spearman, proposals; footage providers, licences, relevance with wind-turbine and abstract-query regressions, validation, extraction, end to end, fallback; manifest; provider states; TTS backoff and fallback) | 56 passed |
| Related suites (Pop Info, render, footage, photos, media, Lemo, analytics, creative intelligence, providers, orchestrator, TTS, YouTube) | 339 passed, 14 skipped, 2 known failures (`test_youtube_branding`) |
| **Full suite, first run** (before the footage relevance iteration) | **1646 passed, 4 failed, 173 skipped** (19 min 38 s) |
| **Full suite, final** | **1649 passed, 5 failed, 173 skipped** (19 min 1 s) |

**Final-run failures:** the 4 known baseline failures (`test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, both `test_youtube_branding`) and the known timing-sensitive `test_slow_youtube_status_does_not_block_other_dashboard_requests`. The latter passed 3 of 3 runs on its own and had passed in the first full run.

**Existing tests changed:**
- `test_youtube_analytics_330.py`: analytics now verifies the grant instead of trusting the stored scope list. The no-request guarantee without a usable grant is still asserted.
- `test_creative_intel_332.py`: the API key set gained `analytics`, `providers` and `visual`.

No test was weakened.

## 14. Stage 3.35b: Wikimedia rate limiting in topic discovery (2026-11-21)

| Suite | Result |
|---|---|
| `test_wikimedia_rate_limit_335b.py` (pacing, Retry-After cap and default, bounded retries, wait budget, host matching, gate routing, not-judged enrichment, stub requests, rejection reason, budget reset) | 19 passed |
| Topic discovery suites (3.21h–m, 3.31A–D, 3.31K, exhaustion recovery, creative identity, orchestrator) | 181 passed, 44 skipped |
| **Full suite** | **1668 passed, 5 failed, 173 skipped** (26 min 49 s, under concurrent production load) |

**Failures:** the same 5 as §13: 4 baseline plus the timing-sensitive dashboard test. Discovery tests take the same time on the old and new code (31.2 s against 31.2 s for the slowest), so the pacing does not slow the suite.
