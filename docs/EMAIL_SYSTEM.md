# CrazzyDocs email system: architecture, design and operations

Status: redesigned 2026-11. This document covers:
- the email architecture;
- the template inventory and trigger mapping;
- design guidelines;
- previews and testing;
- known limitations.

## 1. Architecture

```
scheduler / orchestrator / API routes           (decide WHEN: trigger semantics unchanged)
        │
        ▼
email_monitoring_service                         (decide WHAT: gather persisted facts, build a context)
  send_cycle_summary_report()  ─┐
  dispatch_stage_notification() ├─► email_templates (render HTML + plain text)
  send_daily_digest()           │       design.py      tokens, escaping, formatting, components, document shell
daily_publishing_summary        │       production.py  success / failure (incl. recovery)
  send_daily_summary()         ─┘       report.py      daily operations report
notification_service                    alerts.py      event alerts
  send_success/send_failure (legacy)    data.py        read-only DB lookups (ledger, lifecycle, attempts)
        │
        ▼
persistent_notifications ledger    (atomic claim, idempotency key, CONFIRMED_SENT / DELIVERY_UNKNOWN; unchanged)
        │
        ▼
notification_service.send_email_with_receipt → build_mime_message()
        multipart/alternative = text/plain + multipart/related(text/html + inline CID images)
```

**Unchanged:**
- triggers;
- idempotency keys for cycle reports and stage alerts;
- the claim/dispatch/ledger state machine;
- retry on startup;
- `DELIVERY_UNKNOWN` (never auto-resent);
- the desktop fallback;
- recipient configuration.

**Failure isolation:**
- Rendering runs in a try/except. On error a minimal plain report is still recorded and sent (`minimal` in the body), and the exception is logged.
- Email code never raises into the scheduler and never touches job state. The scheduler already wraps every call.

## 2. Template inventory and trigger mapping

| Email type | Trigger (unchanged) | Template | Ledger type / idempotency key | Recipient |
|---|---|---|---|---|
| Production success (incl. recovered) | Scheduler `run_cycle_generation`: terminal COMPLETED. Orchestrator for manual production runs. | `production.render_success` | `PRODUCTION_COMPLETED` / `cycle_report_<date>_<cycle>_SUCCESS`, plus a `job_id:type` guard | configured recipient |
| Production failure: FAILED / QC_REJECTED / MISSED_DEADLINE | Scheduler: terminal failure only (no email per retry); RECOVERY_WINDOW_EXPIRED | `production.render_failure` | `PRODUCTION_FAILED` / `cycle_report_<date>_<cycle>_<status>` | configured |
| Recovery | **No separate trigger exists.** Recovery is reported in the terminal email: a recovered success shows its attempts and the original failure; a failure shows the deadline, the expired window and whether another attempt is scheduled. No "recovered" email is sent before completion is confirmed. | (as above) | (as above) | |
| Daily operations report (formerly "Daily Publishing Summary") | Scheduler job `daily_publishing_summary_job`, 06:00 IST (`DAILY_SUMMARY_HOUR_IST`) | `report.render` | `DAILY_SUMMARY` / **`daily_operations_report_<date>`** | configured |
| Daily digest (optional) | `send_daily_digest`, only if `CRAZZYDOCS_ENABLE_DAILY_DIGEST=true` (off; no scheduler caller) | `report.render` | `DAILY_SUMMARY` / `daily_digest_<date>` | configured |
| Upload completed / failed | `main.py` upload worker | `alerts.render_event` | `UPLOAD_COMPLETED`/`UPLOAD_FAILED` / `stage322-upload-<id>-<status>` | configured |
| YouTube authorization problem | `main.py` connect route | `alerts.render_event` | `YOUTUBE_AUTH_PROBLEM` / `stage322-youtube-auth-reconnect` | configured |
| Scheduler job failed | `run_daily_summary_dispatch` error | `alerts.render_event` | `SCHEDULER_FAILED` / `stage322-scheduler-daily-summary-<date>` | configured |
| Legacy success / failure | `NotificationManager.send_success/send_failure`. Failure is used for manual (non-scheduler) production failures. Success is effectively unreachable: production uses the cycle report, other contexts are suppressed. | `production.*` via `_render_legacy_*` | `alert_events` table `SUCCESS:<job>` / `FAIL:<job>:<stage>` | configured |

Not emails (out of scope):
- the self-healing, Community and Autopilot notifications (`operator.notify` writes to the dashboard inbox);
- desktop toasts.

## 3. Delivery reliability fix: email no longer blocks the scheduler

The cycle report used to run `media_lifecycle_manager.daily_report()`, a full storage audit of about 60,000 files, while building its embedded dashboard. Because the scheduler sends the report from its event loop, every success email was created 12–14 minutes after the job completed, and during that time the daemon's heartbeat stopped and interval jobs slipped. This was verified in the ledger and the log on 2026-11-26.

The audit is no longer run per cycle (`test_cycle_report_does_not_run_the_storage_audit`). The daily report measures storage once a day with a plain directory walk (about 1.4 s).

Sending is still synchronous in the scheduler loop. SMTP is bounded by 25 s timeouts and 2 retries before the DATA phase. Moving it to a worker thread would change the scheduler and was left out of scope.

## 4. Reporting period of the daily report

The scheduled 06:00 IST run reports the **previous complete IST day** (00:00–24:00), with every cycle the active schedule runs that weekday (24 under the current profile), plus manual productions of that day.

Before the redesign it reported the current day at 06:00. It covered cycles 0–5, and the other 18 cycles of every day were never summarized.

The idempotency prefix changed to `daily_operations_report_`. Without the change, the first corrected report would collide with the old partial-day report of the same date, and the dedup guard would suppress it.

## 5. Design guidelines (for every new email)

- **Structure:**
  1. dark brand header (logo, "CrazzyDocs.", eyebrow = email type, title, subtitle);
  2. status banner;
  3. key metrics;
  4. detail sections;
  5. technical details last;
  6. footer (generated time, timezone, Control Center link).
- **Components** (`design.py`):
  - `status_banner`, `metric_grid`, `kv_table`, `stacked_list` (cycle rows that stack at 320 px);
  - `callout`, `tech_details` (keeps the head and the tail of long errors);
  - `image`, `paragraph`, `section`, `document`.
- **Colours:** tokens in `design.C` and `design.TONES` (success, danger, warning, info, neutral) only. The light body with a dark header matches the Control Center and survives client dark-mode inversion. Explicit `bgcolor` and text colours everywhere.
- **Layout:**
  - tables, inline styles, max width 640 px, one column;
  - a small `<style>` block holds only optional mobile padding tweaks;
  - an Outlook (MSO) fixed-width wrapper.
- **Never:** JavaScript, external CSS, remote images, local file paths as image URLs.
- **Images:** the brand mark (`email_templates/assets/crazzydocs_mark_80.png`, 5 KB) and the optional thumbnail are `data:` URIs in the stored HTML. `build_mime_message` converts them to CID inline parts at send time, because Gmail strips `data:` images. Stored copies stay self-contained for retries and previews.
- **Data rules:**
  - every dynamic value goes through `esc()`, and error text through `redact_sensitive_string`;
  - unknown is `None`: the row or metric is omitted, or shown as `Unavailable`;
  - zero is shown as zero;
  - production, upload and publication are separate rows and are never inferred from each other;
  - times use `fmt_datetime`/`fmt_time`/`fmt_clock` (IST; naive datetimes are UTC as stored by SQLAlchemy);
  - durations use `fmt_duration`.
- **Subjects** keep the established `CrazzyDocs | <scope> | <STATUS> | <tail>` format, so operator mail filters keep working.

### Adding a new email type
1. Build the facts in the caller, read-only. Add lookups to `data.py`.
2. Write `render_x(ctx) -> RenderedEmail` with `D.document(...)` and the components. Do not write raw HTML.
3. Dispatch through the existing ledger: `dispatch_stage_notification` (add the type to `allowed_types`) or `dispatch_summary_notification`, with a stable idempotency key.
4. Add a preview to `backend/scripts/render_email_previews.py` and tests to `backend/tests/test_email_redesign.py`.

## 6. Previews

```
python backend/scripts/render_email_previews.py      # writes docs/email_previews/*.html + *.txt + index.html
```

Open `docs/email_previews/index.html`. There are 10 representative renders, modelled on real records of 2026-11-24:
- success;
- recovered success with multiple retries;
- missing optional metrics;
- QC rejection;
- expired recovery window with a long error;
- daily report: all-success, mixed, partial day with a manual run;
- the upload and authorization alerts.

Nothing is sent, and no database is used.

**Validation done (2026-11-26):**
- Desktop 800 px and mobile 375 px in the Control Center browser with mobile emulation: 0 elements overflow the viewport and there is no horizontal scroll in any preview.
- Headless Edge could not validate 320/375 px: on Windows it enforces a minimum window width of about 500 px, so its narrow screenshots are cropped.
- HTML size: 13–48 KB including images; about 40 KB without the images for a 24-cycle report. Below Gmail's 102 KB clipping limit (the old success emails were 55–130 KB).

## 7. Testing

```
pytest backend/tests/test_email_redesign.py           # 35 tests: formatting, escaping, redaction, state accuracy,
                                                      # recovery, dedup, SMTP-failure isolation, render fallback,
                                                      # CID conversion, size, report aggregation and period, 24 cycles
pytest backend/tests/test_stage_3_9f_email_dashboard.py backend/tests/test_daily_publishing_summary.py \
       backend/tests/test_stage_3_23_2_email_idempotency.py backend/tests/test_stage_3_6_notifications.py
```

Existing tests updated for the new design keep their intent. Assertions on fabricated defaults are now inverted: `1080x1920 @ 30 FPS`, `SYNCHRONIZED` and `Withheld at safety gate` must no longer appear.

## 8. Known limitations

- **Rendering time** is not persisted separately. The `RENDERING` lifecycle marker is set after rendering, so the gap to the next marker is seconds. Emails show total production time and the stage timeline instead of a rendering duration.
- **"Quality score"** means the thumbnail score; no other per-video score is persisted. QC is shown as `N of M checks passed` when the run reports its checks, otherwise "Passed" (a COMPLETED run has passed the QC gate).
- **Upload timing:** the success email reflects the publishing ledger at send time. An upload that happens after the email is reported by the upload alert path, not by a second production email.
- **Health snapshot:** the daily report's operational health (scheduler, worker, quota, delivery) is taken at report time, not as a historical value for the reported day. It is labelled "at report time".
- **YouTube quota** is CrazzyDocs' local estimate for the Pacific day, not Google-reported.
- **Control Center link:** `http://127.0.0.1:8000/` only opens on the studio PC. Override it with `CRAZZYDOCS_DASHBOARD_URL`.
- **Client testing:** Outlook and Gmail rendering was validated by construction (table layout, inline styles, CID images, MSO wrapper) and in a Chromium engine. No real client was tested, because no test emails were sent.
