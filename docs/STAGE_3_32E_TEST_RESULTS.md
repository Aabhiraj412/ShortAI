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
