# Schedule Profile Architecture & Operational Comparison

**Document Purpose:** Defines and contrasts the two operational schedule profiles for CrazzyDocs in `Asia/Kolkata` (IST).  
**Current Active Profile:** `FOUR_CYCLES_DAILY`  
**Prepared Dormant Profile:** `SINGLE_DAILY_2300`  

---

## 1. Profile Specifications Matrix

| Dimension | Profile A: `FOUR_CYCLES_DAILY` (ACTIVE) | Profile B: `SINGLE_DAILY_2300` (PREPARED) |
|---|---|---|
| **Primary Intent** | High-cadence autonomous publication | Single daily flagship documentary |
| **Videos per Day** | 4 public Shorts | 1 public Short |
| **Production Starts (IST)** | 00:00, 06:00, 12:00, 18:00 | 23:00 (Overnight) |
| **Target Publications (IST)** | 06:00, 12:00, 18:00, 00:00 (Next Day) | 05:00 (Early Morning Next Day) |
| **Lead Time Target** | Exactly 6.0 Hours | Exactly 6.0 Hours |
| **Recovery Window** | 4.0 Hours per cycle | 4.0 Hours (23:00 – 03:00 IST) |
| **Daily Quota Consumption** | ~6,600 units (4 uploads + 4 thumbs) | ~1,650 units (1 upload + 1 thumb) |
| **Max Catch-up Allowed** | 1 cycle per reconciliation | 1 cycle (no backlog accumulation) |
| **Publishing Mode** | `automatic_public` | `automatic_public` |

---

## 2. Operational Implications Comparison

### A. Channel Growth & Audience Dynamics
- **`FOUR_CYCLES_DAILY`:** Maximizes reach across multiple global timezones (Morning IST, Noon IST, Evening IST, US/European prime time). Captures varied audience segments across history, science, and true-life mysteries.
- **`SINGLE_DAILY_2300`:** Focuses subscriber attention on one primary release per day. Reduces notification fatigue while maintaining consistent daily algorithmic indexing.

### B. Compute & Infrastructure Footprint
- **`FOUR_CYCLES_DAILY`:** Requires four independent rendering passes per day. Total rendering time is ~8.5 minutes/day. Peak memory utilization is intermittent.
- **`SINGLE_DAILY_2300`:** Executes overnight when system load is minimal. System can idle for 23 hours a day with zero resource contention.

### C. Quota Margin & Safety Headroom
- **`FOUR_CYCLES_DAILY`:** Consumes 6,600 of 10,000 daily YouTube Data API units (66% utilization). Leaves 3,400 units (34%) for retries and metadata sync.
- **`SINGLE_DAILY_2300`:** Consumes 1,650 of 10,000 units (16.5% utilization). Leaves 8,350 units (83.5%) headroom.

---

## 3. Atomic Profile Switching Mechanics

To switch profiles cleanly without duplicate jobs or orphaned cron triggers:

1. **Atomic Switch Method (`set_active_profile`):**
   ```python
   from backend.app.scheduler.schedule_profiles import set_active_profile
   set_active_profile("SINGLE_DAILY_2300")
   ```
2. **Scheduler Job Resynchronization (`reload_scheduler`):**
   ```python
   from backend.app.scheduler.scheduler import reload_scheduler
   reload_scheduler("SINGLE_DAILY_2300")
   ```
3. **Safety Invariants:**
   - Clears all prior APScheduler cron triggers before registering new cycle jobs.
   - Preserves existing completed jobs in SQLite `studio.db`.
   - Never retroactively publishes missed cycles from previous profiles.
   - Enforces `single_active_profile` at runtime.

---

## 4. Policy Decision

Per user instruction, **`FOUR_CYCLES_DAILY` remains the active production schedule**. `SINGLE_DAILY_2300` remains fully prepared, tested, and ready for one-command activation whenever channel strategy dictates.
