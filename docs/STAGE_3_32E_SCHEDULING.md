# Stage 3.32E: Scheduling (Community Draft Generation)

**Draft generation time** is when CrazzyDocs creates a Community post *draft* for review, and it is the only time scheduled here.

**YouTube publishing time** (when an approved post goes public) does not exist yet. It is a future Stage 3.32F capability, so the dashboard shows it only as "future capability" and offers no control for it. Approval stays manual, and publishing stays disabled.

The Community schedule is independent of the 24-cycle production schedule and of video publishing times.

## 1. Mechanism (no second scheduler)

- **One job** on the production daemon's existing APScheduler, the same way it runs the daily summary, analytics and junk-cleaner jobs:

  ```
  id=community_draft_tick  CronTrigger(minute="2-59/5")  max_instances=1  coalesce=True
  ```
- **Duplicate protection:**
  - `configure_scheduler_jobs` removes every job and re-adds each one by its fixed id with `replace_existing`;
  - the daemon's single-instance lock prevents a second daemon.
- **Configuration comes from the database.** Every tick reads the Community configuration, so time, timezone, pause and kill switches take effect without a scheduler reload.
- **Production is untouched.** Production cycles, recovery windows, retry logic and the schedule revision are unchanged; the revision is computed from cycles only.
- **Cost and failure behaviour:**
  - Before Stage 3.32E is configured, a tick doesn't touch the database (`not_configured`).
  - Otherwise it is a config read plus, when due, one generation. It runs in the scheduler's thread pool and never raises.

## 2. Configuration (`community_config`, one row per key)

| Key | Default | Validation |
|---|---|---|
| `enabled` | `false` | boolean |
| `paused` | `false` | boolean |
| `timezone` | `Asia/Kolkata` | IANA name present in tzdata. **Fixed-offset and legacy names are rejected:** `Etc/GMT±N`, `EST`, `MST`, `HST`, `EST5EDT`, `GMT…`; `UTC` is accepted |
| `generation_time` | `10:00` | `HH:MM`, 24-hour, local time in `timezone` |
| `catch_up_hours` | `12` | integer 0–23 |
| `max_auto_attempts` | `2` | integer 1–3 (scheduled attempts per local date) |
| `manual_daily_limit` | `3` | integer 1–10 |
| `formats` | all 8 | non-empty subset |
| `text_mode` | `template` | `template` or `gemini` |
| `kill_switch` | `false` | boolean |
| `excluded_categories` | `[]` | up to 20 names |
| `_enabled_since` | (internal) | UTC timestamp written when automation is switched on |
| `_maintenance_date` | (internal) | local date of the last retention run |

**Saving rules:**
- **Atomic:** every value is validated before anything is written; one invalid value rejects the whole request (400) and nothing is saved.
- **Only real changes are written:** a save that changes nothing writes nothing and adds no audit entry.
- **Audited:** each real change records a `CONFIG_CHANGED` event with the before and after values, the operator and the time. The dashboard shows the latest one under "Last configuration update".
- **No side effects:** saving never approves a draft, publishes, touches OAuth, changes video publishing times, the production schedule or autonomy, or generates a draft.

## 3. Timezones and DST

- **All arithmetic uses `zoneinfo`** (tzdata 2026f on this machine), never a fixed UTC offset.
- **Presets:**
  - US Eastern `America/New_York`
  - US Central `America/Chicago`
  - US Mountain `America/Denver`
  - US Pacific `America/Los_Angeles`
  - Arizona `America/Phoenix` (no DST)
  - Alaska `America/Anchorage`
  - Hawaii `Pacific/Honolulu` (no DST)
  - India `Asia/Kolkata`
- **Any other zone:** any of the 547 accepted IANA names can be searched and selected.
- **What the dashboard shows for the selected zone:** abbreviation, current UTC offset, whether DST is in effect, the next clock change, and the offset after it.

**How the target time is resolved each day** (RFC 5545 semantics, the same as calendar software):

| Situation | Example (America/New_York) | Behaviour |
|---|---|---|
| Normal day | 09:00 | 09:00 local: 14:00 UTC in winter (EST), 13:00 UTC in summer (EDT). The wall clock stays the same all year |
| Spring forward: the time does not exist | 02:30 on 2026-03-08 | Shifted forward by the length of the gap: **03:30 EDT** (07:30 UTC) |
| Fall back: the time occurs twice | 01:30 on 2026-11-01 | **First occurrence** only: 01:30 EDT (05:30 UTC). The second 01:30 never starts another run, because runs are keyed by the local date |

The dashboard lists the next affected dates in plain words, for example "02:30 does not exist on 2027-03-14; the draft is generated at 03:30 EDT instead".

**The catch-up window is elapsed time:** `end = start (UTC) + catch_up_hours`. A DST change inside the window neither stretches nor shortens it.

## 4. When is a draft generated? (`schedule.decide`)

Each tick evaluates **yesterday's and today's local dates**. Yesterday matters when a late target time's catch-up window crosses local midnight. For each date D, with window [S, S + catch-up]:

| State | Condition | Action |
|---|---|---|
| `waiting` | now < S | none |
| `not_enabled_yet` | S is before the moment automation was switched on, and D has no run | none. **Enabling never generates retroactively** for a window that had already started |
| `due` | S ≤ now ≤ end, no run for D | claim run `community:scheduled:D` and generate (or record SKIPPED if D already has an active draft, for example a manual one) |
| `running` | the run for D is RUNNING | none |
| `retry` / `retry_pending` | the run FAILED or was ABANDONED, attempts < `max_auto_attempts`, now ≤ end, D has no active draft; retry once 10 minutes have passed since it finished | restart the same run row (attempt + 1) with a new draft |
| `completed` | a run for D exists and needs nothing more | none |
| `missed` | now > end and no run for D (and the window began after enabling) | record one SKIPPED run and notify once |

Dashboard and API status values are `waiting`, `due`, `running`, `retry_pending`, `completed`, `missed`, `not_enabled_yet` and `blocked` (disabled, paused or kill switch). The response also gives:
- the next generation time in the selected timezone and in UTC; the browser additionally shows it in the viewer's local time;
- while blocked, the time it would run if enabled now.

## 5. Duplicate prevention

- **One scheduled run per local date:** `community_runs.idem_key` is UNIQUE (`community:scheduled:<local date>`). This holds across dashboard and daemon restarts, retried tasks and duplicate ticks.
- **One active draft per local date:** a partial UNIQUE index on `community_drafts(post_date) WHERE active = 1`.
  - Manual first: the scheduled run is SKIPPED.
  - Scheduled first: manual generation gets 409.
- **Atomic decisions:** the decision is re-made on fresh rows inside `BEGIN IMMEDIATE`, so concurrent ticks or processes serialise.
- **Tested cases:**
  - every 5-minute tick through a fall-back night gives one draft;
  - ticks every 15 minutes across two UTC dates give one draft for the Kolkata date;
  - six concurrent threads give one draft;
  - a restarted service gives no new run.

## 6. Failure, retry and recovery

| Failure | Handling |
|---|---|
| No eligible topic | `GENERATION_FAILED` |
| No image could be produced | `IMAGE_FAILED` |
| Validation error | `VALIDATION_FAILED` |
| Deadline exceeded (120 s) | The draft fails at its current stage |
| Expired run lease (process stopped) | Run `ABANDONED`, draft `CANCELLED`; never resumed blindly |

Scheduled retries follow `max_auto_attempts` (default 2), 10 minutes apart, inside the catch-up window.

## 7. Choosing a US time

The dashboard does not assume a timezone; the operator selects one. A YouTube account's country does not decide the schedule.

To be ready before US mornings:
1. Choose the audience's timezone, for example `America/New_York`.
2. Choose a draft generation time early enough to review the draft before posting it by hand.
