# Named Production Schedule Profiles

**Status:** implemented and tested in the development worktree on top of `ba1cc0c`. **Not committed, not deployed.**

Save the active production schedule under any name, then restore it later. Profiles are a **library** next to the existing schedule system, not a second schedule manager.

## 1. How it fits the existing schedule system

| Concern | Where it lives | Changed by profiles? |
|---|---|---|
| Active production schedule | `studio_settings['control_center.schedule.active']` | Only by **Load**, through the existing `schedule_service.activate`, which calls `control_center_service.validate_saved_schedule_draft` and then `activate_schedule_draft` |
| Schedule draft / validation record | `control_center.schedule.draft` / `.validation` | Only as part of that existing activation (it clears the draft, as always) |
| Schedule version history | `control_center.schedule.history` | Only by that existing activation |
| Runtime profile (`set_runtime_profile`) and scheduler daemon | in-process / daemon poll | Load uses the existing `restore_active_schedule_profile`. **The daemon is not restarted**; it picks up the new revision on its normal poll. |
| YouTube publication schedule | `control_center_publication_schedule_*` | **Never** |
| Saved profiles | **new** table `production_schedule_profiles` | yes |
| Which profile is active (metadata) | **new** key `studio_settings['control_center.schedule.named_profile']` | yes |
| Audit | existing control-center change history (`record_change`) | new kinds: `schedule_profile_saved`, `_renamed`, `_duplicated`, `_deleted`, `_loaded` |

**The editor is unchanged.** Add or edit cycles, Validate and Activate Schedule work exactly as before. A manual activation simply shows up as "unsaved changes" against the loaded profile.

## 2. Data model

**Table `production_schedule_profiles`.** It is declared as `ProductionScheduleProfileModel` in `backend/app/database/db.py` and created by the existing `init_db()` (`Base.metadata.create_all`) the next time the dashboard or daemon starts. No existing table is changed.

| Column | Meaning |
|---|---|
| `id` | `sp_` + 16 hex characters (unique ID) |
| `name` | User-defined name, 1–80 characters. Whitespace is collapsed; any Unicode is allowed; control characters are refused; at least one letter or digit is required. |
| `name_key` | Case-folded name, **UNIQUE**. Duplicate names are refused case- and spacing-insensitively. |
| `description` | Optional, ≤ 500 characters |
| `config_json` | The complete schedule configuration: `{profile_name, timezone_name, cycles[]}`. Each cycle stores `cycle_id`, `name`, `start_time`, `recovery_duration_minutes`, `target_publish_time`, `enabled` and `active_weekdays`. The version, revision and activation metadata are deliberately not stored. |
| `checksum` | SHA-256 of the canonical configuration (sorted cycles, normalized fields) |
| `schema_version` | 1 |
| `created_at_utc`, `updated_at_utc` | ISO UTC; the dashboard shows IST |

**Checksum.** The checksum is order-independent, and it treats an empty target time the same as a missing one. Every read verifies it: a profile whose stored data no longer matches its checksum is shown as invalid, and Preview and Load refuse it.

## 3. Operations

| Operation | API (all under `/api/schedule/profiles`) | Behaviour |
|---|---|---|
| List | `GET` | Every profile: name, created, modified, cycles (enabled of total), timezone, recovery window, status (Active / Active · modified / Inactive). Also returns the active-profile indicator. |
| Get | `GET /{id}` | One profile including its configuration |
| **Save Current Schedule** | `POST {name, description?}` | Reads the **active** schedule, validates it and stores it under the name. **The active schedule is not changed.** Sets the active-profile marker, because the active schedule is now that profile. |
| **Preview** | `GET /{id}/preview` | Checksum and schema verification; full validation (errors and warnings); a **cycle-by-cycle comparison** with the active schedule (added, removed, changed fields, recovery shortened, timezone); the next 6 production cycles under the profile; safety blockers. Changes nothing. |
| **Load** | `POST /{id}/load {confirm: true, expected_checksum}` | Requires confirmation and the checksum from the preview, so the operator loads exactly what was previewed. Steps: verify the checksum, validate, run the safety check, call the existing `schedule_service.activate`, set the marker, record `schedule_profile_loaded`. Loading a profile identical to the active schedule returns `ALREADY_ACTIVE` and does not re-activate (no version bump). |
| **Rename** | `POST /{id}/rename {name}` | Name only; the configuration and checksum are unchanged |
| **Duplicate** | `POST /{id}/duplicate {name, description?}` | An independent copy with a new ID and name |
| **Delete** | `POST /{id}/delete {confirm: true}` | Requires confirmation. **Refused while the profile is the active schedule**: "Activate another schedule before deleting it". Clears the marker if it pointed at the deleted profile. |

**Authorization:**
- Reads: `require_local_control` (localhost only).
- Every change: also `require_same_origin` (the existing CSRF guard, which refuses a cross-site `Sec-Fetch-Site` or a foreign `Origin`).
- Request bodies are strict: extra fields give 422.

## 4. Active profile indicator

The indicator compares the checksum of the active schedule with the marker and the saved profiles.

| State | Meaning | Shown |
|---|---|---|
| `PROFILE_ACTIVE` | The active schedule equals the loaded or saved profile | "Active profile: \<name\>" (green) |
| `PROFILE_MODIFIED` | Loaded from a profile, then changed manually | "Active profile: \<name\>" + **unsaved changes** badge; the row shows "Active · modified" |
| `MATCHES_PROFILE` | No marker, but a saved profile has the same configuration | "Active profile: \<name\>" |
| `UNSAVED` | The active schedule is not saved under any name | "Active profile: unnamed" + **unsaved changes** |
| `NO_ACTIVE_SCHEDULE` | — | — |

## 5. Safety rules (enforced in `schedule_profile_library.safety_check`, before any activation)

1. **No activation while a production job is active.** Any `cycle_*` job in RUNNING, RETRYING, PAUSED, QUEUED, RETRY_SCHEDULED, PENDING or INTERRUPTED blocks it.
   - The refusal names the job, its status and stage, and says to try again after it finishes.
   - HANDOFF_PENDING (publication handoff) does not block, because a production schedule change does not affect it.
2. **The open recovery window is never removed, moved or shortened.** If a cycle is inside its recovery window and its job is not COMPLETED (missing, FAILED, …), a profile that removes or disables that cycle, moves its start time or shortens its recovery is refused until the window closes. Lengthening is allowed.
3. **Saving, renaming, duplicating, deleting and previewing never touch** the active schedule, the draft, the version history, the runtime profile, jobs or the publication schedule (tested).
4. **Load uses the existing activation path.** All existing validation and safety rules apply. No production cycle is created, no worker is started, no lock is touched, and the scheduler is not restarted.
5. **Load needs explicit confirmation** and the previewed checksum. Production timings never change silently.

## 6. Dashboard (Production Schedule tab)

- **Under the title:** the active profile indicator.
- **Editor buttons:** a new **Save Current Schedule** button. If the editor has unsaved edits, it warns that the *active* schedule is what gets saved.
- **New card "Saved Production Schedules":** a table with name and description, created, last modified, cycles, timezone, recovery window and status. Each row has **Preview & load**, **Rename**, **Duplicate** and **Delete** (Delete is disabled for the active profile).
- **Preview panel:**
  - summary and differences (green = added, red = removed, amber = changed, "recovery shortened" flagged);
  - validation errors and warnings;
  - ⛔ safety blockers (the Load button is disabled while blocked);
  - the next cycles;
  - **Load this schedule**, behind a confirmation dialog.
- **Refresh behaviour:** the list loads when the tab opens, after every action, and every 60 s while the tab is open.
- **Phone width:** the card fits; the table scrolls inside it.
- **Rendering safety:** all server values are HTML-escaped; profile IDs are passed through `data-` attributes.

## 7. Files

| File | Change |
|---|---|
| `backend/app/services/schedule_profile_library.py` | **New**: storage, checksum, comparison, safety check, operations, active indicator |
| `backend/app/database/db.py` | **Modified**: `ProductionScheduleProfileModel` (new table) |
| `backend/app/main.py` | **Modified**: 8 endpoints under `/api/schedule/profiles`, strict request models |
| `backend/app/templates/dashboard.html` | **Modified**: indicator, Save button, Saved Production Schedules card, preview panel, JS |
| `backend/tests/test_production_schedule_profiles.py` | **New**: 26 tests |
| `docs/PRODUCTION_SCHEDULE_PROFILES.md`, `docs/PRODUCTION_SCHEDULE_PROFILE_TESTS.md` | **New** |

## 8. Known limitations

- **Timing of activation.** Load is refused whenever any production job is active. With the current hourly schedule, the usable moments are between a cycle's completion and the next start. That is the intended trade-off: "refuse and explain" rather than change timing under a running job.
- **What a profile captures.** A profile captures the *active* schedule, not unsaved editor edits. To save an edited schedule, activate it first (existing flow), then save it.
- **No undo.** Deleting a profile is permanent. The audit history keeps its name and checksum, but not its configuration.
