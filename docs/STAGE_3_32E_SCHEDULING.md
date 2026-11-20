# Stage 3.32E: Scheduling

## 1. Mechanism (no second scheduler)

- The production daemon's existing APScheduler instance gets **one** job, the same way it runs the daily summary, analytics and junk-cleaner jobs:

  ```
  id=community_draft_tick  trigger=CronTrigger(minute="2-59/5", timezone=profile tz)  max_instances=1  coalesce=True
  ```
- Each tick calls `CommunityService.tick()`, which reads the Community configuration and decides whether today's draft is due.
  - The generation time, timezone, pause and kill switches therefore take effect **without** reloading or restarting the scheduler.
  - The production cycle jobs, recovery windows and schedule revision are unchanged; the revision is computed from cycles only.
- **The tick is cheap and safe:**
  - Before Stage 3.32E is configured it does **not** touch the database at all (`not_configured`).
  - Otherwise it is one config read plus, when due, one generation (about 0.3–0.8 s warm).
  - It runs in the scheduler's thread pool, never on the asyncio loop, and never raises (errors return `{"status": "error"}`).
- Ticks fall on minutes 2, 7, 12 ... so they never coincide with a cycle start at :00.

## 2. Configuration (persistent, `community_config`)

| Setting | Default | Notes |
|---|---|---|
| `enabled` | `false` | Automatic generation |
| `paused` | `false` | Pause or resume without losing settings |
| `generation_time` | `10:00` | HH:MM, 24-hour |
| `timezone` | `Asia/Kolkata` | Any IANA zone; validated |
| `formats` | all 8 | Permitted formats, in rotation order |
| `kill_switch` | `false` | Blocks all generation and regeneration |
| `catch_up_hours` | `12` | How long after the scheduled time a missed run may still happen |
| `manual_daily_limit` | `3` | Manual generations per local day (1–10) |
| `text_mode` | `template` | `gemini` = optional wording, at most 2 calls a day |
| `excluded_categories` | `[]` | Never chosen |

Every change records an audit event with the before and after values.

## 3. When is a draft generated?

For the current local date D in the configured timezone, with S = D at `generation_time`:

| Situation | Outcome |
|---|---|
| Blocked: disabled, paused, or a kill switch | `blocked`; nothing written |
| Now < S | `not_due` |
| Now in [S, S + catch_up] and no run for D | **Generate** (run key `community:scheduled:D`) |
| A draft for D already exists or is being generated (for example manual) | Run recorded as `SKIPPED` |
| Now > S + catch_up and no run for D | Run recorded as `SKIPPED` (missed) and the operator notified |
| The run for D failed or was abandoned, attempts < 2, ≥ 10 min since, inside the window | Retry: same run row, attempt 2, new draft |
| Otherwise | `done` |

**Next scheduled run** (`GET /api/community/schedule`):
- tomorrow's S once today's run exists;
- "due now (catch-up)" inside the window;
- none while blocked.

## 4. Idempotency and concurrency

- **One scheduled run per date:** `community_runs.idem_key` UNIQUE = `community:scheduled:<local date>`. This survives dashboard and daemon restarts, task retries and duplicate ticks (tested with 3 extra ticks and a new service instance).
- **One active draft per date:** partial UNIQUE index on `community_drafts(post_date) WHERE active = 1`. Manual and scheduled generation cannot both produce a draft.
  - Manual first: the scheduled run is SKIPPED.
  - Scheduled first: manual gets 409.
  - Six concurrent threads (3 manual, 3 ticks): exactly one active draft and one scheduled run.
- **Claims** happen inside `BEGIN IMMEDIATE`, so two processes serialise.
- **A running run holds a 300 s lease.** Manual generation is refused (409) while a run for the date holds a lease.

## 5. Failure, retry and recovery

| Failure | Handling |
|---|---|
| No eligible topic | Draft `GENERATION_FAILED`, run `FAILED`, operator notified |
| No image could be produced | `IMAGE_FAILED` |
| Validation error | `VALIDATION_FAILED` |
| Deadline exceeded (120 s) | The draft fails at its current stage |
| Process stops mid-run | At the next tick the expired lease makes the run `ABANDONED` and the draft `CANCELLED` |
| Draft stuck in progress for > 10 min (an interrupted regeneration) | Moved to its failed state |

- Scheduled retries are limited to **2 attempts** per date, 10 minutes apart, within the catch-up window.
- Manual generations are limited per day.
- Nothing is resumed from a half-finished state.

## 6. Time zones

- All timestamps are stored in UTC.
- The draft date (`post_date`) is the local date in the configured timezone. At 23:45 UTC on 20 November the Asia/Kolkata date is already 21 November, and the 21st is used.
- **Changing the timezone or time mid-day:**
  - the new local date's key decides;
  - a date that already has a run is not generated twice.

## 7. Deployment note

- The tick needs the daemon to load the new code, which means a daemon restart. That is a separate operator approval because the daemon runs production.
- Until then:
  - manual generation and every review action work from the dashboard alone, once it is restarted;
  - automatic generation does not run (nothing is lost; the schedule simply starts after the restart).
