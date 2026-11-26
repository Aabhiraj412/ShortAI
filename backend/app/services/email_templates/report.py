"""Daily operations report: every scheduled cycle of one IST day, aggregates, health and an assessment.

Compiled read-only from persistent_jobs, publishing_ledger, production_lifecycle_events, the
notification ledger and the YouTube quota ledger. The assessment is derived from these counts only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from backend.app.services.email_templates import design as D
from backend.app.services.email_templates import data as Q
from backend.app.services.email_templates.production import (
    YouTubeState, YT_PUBLISHED, YT_SCHEDULED, YT_UPLOADED, YT_UPLOADED_PRIVATE, interpret_failure,
)

logger = logging.getLogger(__name__)

# display status -> tone
ST_COMPLETED = "Completed"
ST_RECOVERED = "Completed after recovery"
ST_FAILED = "Failed"
ST_RUNNING = "In progress"
ST_RETRY = "Retry pending"
ST_SKIPPED = "Skipped"
ST_NOT_STARTED = "Did not start"
ST_UPCOMING = "Upcoming"
_TONE = {ST_COMPLETED: "success", ST_RECOVERED: "success", ST_FAILED: "danger", ST_RUNNING: "info",
         ST_RETRY: "warning", ST_SKIPPED: "neutral", ST_NOT_STARTED: "warning", ST_UPCOMING: "neutral"}

_DONE = {"COMPLETED", "SUCCESS", "RECOVERED_SUCCESS"}
_FAILED = {"FAILED", "BLOCKED_QC", "FAILED_QC", "QC_REJECTED", "RECOVERY_WINDOW_EXPIRED", "ABORTED_PRE_RESET",
           "CANCELLED", "MISSED_DEADLINE", "PUBLISHING_FAILED"}
_RETRY = {"RETRYING", "RETRY_SCHEDULED", "RECOVERY_PENDING", "WAITING_FOR_INTERNET", "INTERRUPTED", "PAUSED"}
_RUNNING = {"RUNNING", "QUEUED", "PENDING", "DISPATCHING"}
_SKIPPED = {"SKIPPED", "MISSED"}


@dataclass
class CycleRow:
    label: str
    scheduled: datetime
    job_id: Optional[str]
    status: str
    raw_status: Optional[str] = None
    topic: Optional[str] = None
    production_sec: Optional[float] = None
    video_sec: Optional[float] = None
    retries: Optional[int] = None
    thumbnail_score: Optional[float] = None
    reason: Optional[str] = None
    youtube: Optional[YouTubeState] = None
    manual: bool = False


@dataclass
class OperationsReport:
    date: str
    generated_at: datetime
    complete_day: bool                       # the reporting day has fully elapsed
    rows: List[CycleRow] = field(default_factory=list)
    health: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    storage_mb: Optional[float] = None
    disk_free_gb: Optional[float] = None

    # ---- aggregates (derived, never stored) ----
    def scheduled_rows(self) -> List[CycleRow]:
        return [r for r in self.rows if not r.manual]

    def count(self, *statuses: str, manual: bool = False) -> int:
        rows = self.rows if manual else self.scheduled_rows()
        return sum(1 for r in rows if r.status in statuses)

    @property
    def completed(self) -> int:
        return self.count(ST_COMPLETED, ST_RECOVERED)

    @property
    def failed(self) -> int:
        return self.count(ST_FAILED)

    @property
    def recovered(self) -> int:
        return self.count(ST_RECOVERED)

    @property
    def pending(self) -> int:
        return self.count(ST_RUNNING, ST_RETRY, ST_UPCOMING)

    def averages(self) -> Dict[str, Optional[float]]:
        done = [r for r in self.rows if r.status in (ST_COMPLETED, ST_RECOVERED)]

        def avg(vals):
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else None
        return {"production_sec": avg(r.production_sec for r in done), "video_sec": avg(r.video_sec for r in done),
                "thumbnail_score": avg(r.thumbnail_score for r in done)}

    def assessment(self) -> Tuple[str, str]:
        """(tone, sentence) derived from the counts."""
        n = len(self.scheduled_rows())
        c, f, rec = self.completed, self.failed, self.recovered
        not_started = self.count(ST_NOT_STARTED)
        skipped = self.count(ST_SKIPPED)
        pending = self.pending
        if n == 0:
            return "neutral", "No production cycles were scheduled for this day."
        rec_note = (" %d of them needed recovery." % rec) if rec else ""
        if c == n:
            return "success", "All %d scheduled cycles completed successfully.%s" % (n, rec_note)
        parts = ["%d of %d scheduled cycles completed" % (c, n)]
        if f:
            parts.append("%d failed" % f)
        if not_started:
            parts.append("%d did not start" % not_started)
        if skipped:
            parts.append("%d were skipped" % skipped)
        if pending:
            parts.append("%d still pending" % pending)
        sentence = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."
        if rec:
            sentence += " %d completed after recovery." % rec
        if f or not_started:
            tone = "danger" if (f + not_started) * 4 >= n else "warning"
            sentence += " The failed cycles are listed below with their reasons."
        elif pending:
            tone = "info"
            sentence = "Production is partially complete: " + sentence[0].lower() + sentence[1:]
        else:
            tone = "warning"
        return tone, sentence


# ---- compilation ----------------------------------------------------------------------------------------------
def _job_status(job: Any, now: datetime, slot: datetime) -> str:
    if job is None:
        return ST_UPCOMING if slot > now else ST_NOT_STARTED
    st = str(job.status or "").upper()
    if st in _DONE:
        return ST_RECOVERED if int(job.retry_count or 0) > 0 else ST_COMPLETED
    if st in _FAILED:
        return ST_FAILED
    if st in _RETRY:
        return ST_RETRY
    if st in _RUNNING:
        return ST_RUNNING
    if st in _SKIPPED:
        return ST_SKIPPED
    return ST_RUNNING if st else ST_NOT_STARTED


def _row_from_job(db, label: str, slot: datetime, job: Any, now: datetime, manual: bool = False) -> CycleRow:
    status = _job_status(job, now, slot)
    row = CycleRow(label=label, scheduled=slot, job_id=getattr(job, "id", None), status=status,
                   raw_status=getattr(job, "status", None), manual=manual)
    if job is None:
        return row
    cp = Q.checkpoint(job)
    row.topic = job.topic_title or cp.get("topic") or cp.get("topic_title")
    row.retries = int(job.retry_count or 0)
    if status in (ST_COMPLETED, ST_RECOVERED):
        row.production_sec = _num(cp.get("total_duration_sec"))
        row.video_sec = _num(cp.get("duration_sec"))
        row.thumbnail_score = _num(cp.get("thumbnail_score"))
        row.youtube = Q.youtube_state(db, job, cp)
    elif status == ST_FAILED:
        row.reason = interpret_failure(job.error_details, "FAILED")[0]
    elif status == ST_RETRY:
        nxt = (cp.get("cycle_state") or {}).get("next_retry_at_utc")
        row.reason = "Next attempt " + (D.fmt_datetime(nxt) or "time not recorded")
    return row


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def compile_report(db, date_str: str, *, profile: Any, now: Optional[datetime] = None,
                   job_model: Any = None) -> OperationsReport:
    """All cycles the active profile schedules on ``date_str`` (IST) plus manual productions of that day."""
    now = (now or datetime.now(timezone.utc)).astimezone(D.IST)
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=D.IST)
    rep = OperationsReport(date=date_str, generated_at=now, complete_day=now >= day + timedelta(days=1))
    if job_model is None:
        from backend.app.database.db import JobModel as job_model  # noqa: N813
    seen = set()
    cycles = [c for c in getattr(profile, "cycles", []) if day.weekday() in tuple(getattr(c, "active_weekdays", range(7)))]
    cycles.sort(key=lambda c: (c.start_hour_ist, c.start_min_ist))
    for c in cycles:
        slot = day.replace(hour=c.start_hour_ist, minute=c.start_min_ist)
        job_id = "%s_%s_%02d%02d" % (c.cycle_id, day.strftime("%Y%m%d"), c.start_hour_ist, c.start_min_ist)
        job = db.query(job_model).filter(job_model.id == job_id).first()
        seen.add(job_id)
        num = c.cycle_id.split("_")[-1]
        label = ("Cycle %s" % num) if num.isdigit() else (c.name or c.cycle_id)
        rep.rows.append(_row_from_job(db, label, slot, job, now))
    try:
        others = (db.query(job_model).filter(job_model.date_str == date_str).all())
    except Exception:
        others = []
    for j in others:
        jid = str(j.id or "")
        if jid in seen or jid.startswith(("test_", "mock_", "digest_")):
            continue
        created = D.to_ist(j.created_at) or day
        rep.rows.append(_row_from_job(db, "Manual production", created, j, now, manual=True))
    rep.health = _health(db, now)
    return rep


def _health(db, now: datetime) -> List[Tuple[str, Optional[str]]]:
    rows: List[Tuple[str, Optional[str]]] = []
    try:
        from backend.app.services.deployment_diagnostics import get_deployment_status
        st = get_deployment_status()
        d = st.get("daemon") or {}
        age = d.get("heartbeat_age_seconds")
        state = {"CURRENT": "Running", "STALE_CODE": "Running (code update pending restart)",
                 "NOT_RUNNING": "Not running", "UNKNOWN_REVISION": "Running (revision unknown)"}.get(d.get("state"), d.get("state"))
        rows.append(("Scheduler", (state or D.UNAVAILABLE) + ((", heartbeat %ds ago" % int(age)) if age is not None else "")))
        w = st.get("worker") or {}
        rows.append(("Worker", ("Busy: %s" % w["active_job_id"]) if w.get("active_job_id") else "Idle"))
        rows.append(("Schedule revision", (st.get("scheduler") or {}).get("active_schedule_revision")))
    except Exception as exc:
        logger.debug("deployment status unavailable: %s", type(exc).__name__)
        rows.append(("Scheduler", D.UNAVAILABLE))
    try:
        pending = db.execute(text("SELECT COUNT(*) FROM persistent_jobs WHERE status IN "
                                  "('RETRYING','RETRY_SCHEDULED','RECOVERY_PENDING','WAITING_FOR_INTERNET')")).scalar()
        rows.append(("Pending recovery operations", D.fmt_count(pending)))
    except Exception:
        pass
    try:
        since = (now - timedelta(hours=24)).astimezone(timezone.utc).replace(tzinfo=None)
        r = db.execute(text("SELECT SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN status='DELIVERY_UNKNOWN' THEN 1 ELSE 0 END), COUNT(*) "
                            "FROM persistent_notifications WHERE created_at >= :s"), {"s": since}).fetchone()
        if r is not None and r[2]:
            bad = int(r[0] or 0) + int(r[1] or 0)
            rows.append(("Email delivery (24 h)", "%d sent or pending, %d failed or unconfirmed" % (int(r[2]) - bad, bad)))
    except Exception:
        pass
    try:
        from backend.app.core.paths import DATABASE_PATH
        from backend.app.services.youtube_quota import QuotaLedger
        usage = QuotaLedger(str(DATABASE_PATH)).usage()
        for b, u in usage.items():
            if u.get("requests"):
                rows.append(("YouTube quota: %s" % (u.get("label") or b),
                             "%s of %s %s used (local estimate, Pacific day)" % (D.fmt_count(u.get("used_estimate")),
                                                                                D.fmt_count(u.get("daily_limit")),
                                                                                u.get("unit") or "units")))
    except Exception as exc:
        logger.debug("quota usage unavailable: %s", type(exc).__name__)
    return rows


# ---- rendering ----------------------------------------------------------------------------------------------------
def _row_view(r: CycleRow) -> D.ListRow:
    lines = []
    meta = []
    if r.production_sec is not None:
        meta.append("Production " + D.fmt_duration(r.production_sec))
    if r.video_sec is not None:
        meta.append("Video " + D.fmt_duration(r.video_sec))
    if r.retries:
        meta.append("%d retr%s" % (r.retries, "y" if r.retries == 1 else "ies"))
    if r.thumbnail_score is not None:
        meta.append("Thumbnail " + (D.fmt_score(r.thumbnail_score) or ""))
    if meta:
        lines.append(" · ".join(meta))
    if r.youtube is not None and r.status in (ST_COMPLETED, ST_RECOVERED):
        lines.append("YouTube: " + r.youtube.label)
    return D.ListRow(title=r.topic or ("No topic selected" if r.job_id else "Not started"),
                     status=r.status, tone=_TONE.get(r.status, "neutral"),
                     eyebrow="%s · %s" % (r.label, r.scheduled.strftime("%H:%M IST")),
                     lines=lines, note=r.reason)


def render(rep: OperationsReport) -> D.RenderedEmail:
    sched = rep.scheduled_rows()
    n = len(sched)
    tone, sentence = rep.assessment()
    finished = rep.completed + rep.failed
    av = rep.averages()
    retries_total = sum(r.retries or 0 for r in rep.rows)
    retried = [r for r in rep.rows if (r.retries or 0) > 0 and r.status in (ST_COMPLETED, ST_RECOVERED, ST_FAILED)]
    rec_rate = D.fmt_pct(sum(1 for r in retried if r.status in (ST_COMPLETED, ST_RECOVERED)), len(retried))
    yt = [r.youtube for r in rep.rows if r.youtube is not None]
    uploaded = sum(1 for y in yt if y.state in (YT_UPLOADED, YT_UPLOADED_PRIVATE, YT_SCHEDULED, YT_PUBLISHED))
    scheduled_pub = sum(1 for y in yt if y.state == YT_SCHEDULED)
    published = sum(1 for y in yt if y.state == YT_PUBLISHED)
    manual = [r for r in rep.rows if r.manual]

    overview = D.metric_grid([
        ("Scheduled cycles", D.fmt_count(n), None),
        ("Completed", D.fmt_count(rep.completed), ("%d after recovery" % rep.recovered) if rep.recovered else None),
        ("Failed", D.fmt_count(rep.failed), None),
        ("Did not start / skipped", D.fmt_count(rep.count(ST_NOT_STARTED, ST_SKIPPED)), None),
        ("In progress or upcoming", D.fmt_count(rep.pending) if rep.pending else None, None),
        ("Success rate", D.fmt_pct(rep.completed, finished), "of %d finished cycles" % finished if finished else None),
    ])
    perf = D.metric_grid([
        ("Videos produced", D.fmt_count(sum(1 for r in rep.rows if r.status in (ST_COMPLETED, ST_RECOVERED))),
         "including manual runs" if manual else None),
        ("Failure rate", D.fmt_pct(rep.failed, finished), None),
        ("Avg production time", D.fmt_duration(av["production_sec"]), None),
        ("Avg video length", D.fmt_duration(av["video_sec"]), None),
        ("Avg thumbnail score", D.fmt_score(av["thumbnail_score"]), None),
        ("Total retries", D.fmt_count(retries_total), None),
        ("Recovery success", rec_rate, ("%d cycle%s retried" % (len(retried), "" if len(retried) == 1 else "s")) if retried else None),
    ])
    publishing = D.kv_table([
        ("Uploaded to YouTube", D.fmt_count(uploaded) if yt else None),
        ("Publication scheduled", D.fmt_count(scheduled_pub) if yt else None),
        ("Published (public)", D.fmt_count(published) if yt else None),
    ])
    failures = [r for r in rep.rows if r.status in (ST_FAILED, ST_NOT_STARTED)]
    attention = (D.callout("danger" if rep.failed else "warning", "Needs attention",
                           items=["%s (%s): %s" % (r.label, r.scheduled.strftime("%H:%M"), r.reason or r.status) for r in failures])
                 if failures else D.Block("", ""))
    storage = []
    if rep.storage_mb is not None:
        storage.append(("Media storage", "%s MB" % D.fmt_count(round(rep.storage_mb))))
    if rep.disk_free_gb is not None:
        storage.append(("Free disk space", "%.1f GB" % rep.disk_free_gb))

    period_note = ("Reporting period: %s, 00:00 to 24:00 IST." % D.fmt_date(rep.date)) + (
        "" if rep.complete_day else " The day is still in progress; pending cycles are shown as upcoming.")
    sections = [
        D.section(None, D.status_banner(tone, "Final assessment", sentence)),
        D.section("Executive overview", overview, D.paragraph(period_note)),
        D.section(None, attention),
        D.section("Production cycles", D.stacked_list([_row_view(r) for r in sched])),
        D.section("Manual productions", D.stacked_list([_row_view(r) for r in manual])) if manual else None,
        D.section("Aggregate performance", perf, D.paragraph("Rendering time is not recorded separately; production time "
                                                             "covers the whole pipeline.")),
        D.section("Publishing", publishing) if yt else None,
        D.section("Operational health (at report time)", D.kv_table(list(rep.health) + storage)),
    ]
    return D.document(
        subject="CrazzyDocs | Daily Operations Report | %s" % rep.date,
        eyebrow="Daily operations report",
        title="Production report · %s" % (D.fmt_date(rep.date) or rep.date),
        subtitle="%d scheduled cycles · %d completed · %d failed" % (n, rep.completed, rep.failed),
        preheader=sentence,
        sections=sections,
        generated_at=rep.generated_at,
    )
