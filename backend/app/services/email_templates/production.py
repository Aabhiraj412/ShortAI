"""Production outcome emails: success (including recovered success) and terminal failure.

The context objects hold only verified values. Anything unknown stays ``None`` and is omitted or shown
as "Unavailable"; nothing here invents a QC result, a duration or a YouTube state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.app.services.email_templates import design as D

# ---- YouTube lifecycle (production, upload and publication are separate states) ------------------------------
YT_NOT_QUEUED = "NOT_QUEUED"            # produced, not handed to the publishing queue
YT_QUEUED = "QUEUED"                    # in the local publishing queue, not uploaded
YT_UPLOADED_PRIVATE = "UPLOADED_PRIVATE"
YT_UPLOADED = "UPLOADED"                # uploaded, visibility not recorded
YT_SCHEDULED = "SCHEDULED"              # uploaded privately with a confirmed publish time
YT_PUBLISHED = "PUBLISHED"              # visibility public
YT_UPLOAD_FAILED = "UPLOAD_FAILED"
YT_UNKNOWN = "UNKNOWN"

_YT_LABEL = {
    YT_NOT_QUEUED: ("Not queued for upload", "neutral"),
    YT_QUEUED: ("Queued, not yet uploaded", "info"),
    YT_UPLOADED_PRIVATE: ("Uploaded (private)", "info"),
    YT_UPLOADED: ("Uploaded (visibility not recorded)", "info"),
    YT_SCHEDULED: ("Uploaded (private), publication scheduled", "info"),
    YT_PUBLISHED: ("Published (public)", "success"),
    YT_UPLOAD_FAILED: ("Upload failed", "danger"),
    YT_UNKNOWN: ("Unavailable", "neutral"),
}


@dataclass
class YouTubeState:
    state: str = YT_UNKNOWN
    video_id: Optional[str] = None
    visibility: Optional[str] = None
    scheduled_for: Optional[Any] = None         # datetime/ISO of the confirmed publishAt
    detail: Optional[str] = None

    @property
    def label(self) -> str:
        return _YT_LABEL.get(self.state, _YT_LABEL[YT_UNKNOWN])[0]

    @property
    def url(self) -> Optional[str]:
        return "https://youtu.be/%s" % self.video_id if self.video_id else None


@dataclass
class RecoveryInfo:
    attempts: Optional[int] = None              # executions of this job (retries + 1)
    max_attempts: Optional[int] = None
    first_failure: Optional[str] = None         # the failure that triggered recovery (redacted, clipped)
    recovered_at: Optional[Any] = None
    deadline: Optional[Any] = None              # recovery window end
    window_expired: bool = False                # the deadline had passed when the cycle was closed
    no_time_left: bool = False                  # closed before the deadline: too little time for another attempt
    retry_scheduled: Optional[bool] = None      # True only if the scheduler will try again
    next_attempt_at: Optional[Any] = None

    @property
    def retries(self) -> Optional[int]:
        return None if self.attempts is None else max(0, self.attempts - 1)


@dataclass
class StageMark:
    stage: str
    at: Any


@dataclass
class DayProgress:
    """Counts for the production day so far, from the database (never extrapolated)."""
    date: str
    scheduled: int
    completed: int
    failed: int
    in_progress: int
    upcoming: int
    not_started: int

    def summary(self) -> str:
        parts = ["%d of %d completed" % (self.completed, self.scheduled)]
        if self.failed:
            parts.append("%d failed" % self.failed)
        if self.in_progress:
            parts.append("%d in progress" % self.in_progress)
        if self.not_started:
            parts.append("%d did not start" % self.not_started)
        if self.upcoming:
            parts.append("%d upcoming" % self.upcoming)
        return ", ".join(parts)


@dataclass
class ProductionEmailContext:
    outcome: str                                # SUCCESS | FAILED | QC_REJECTED | MISSED_DEADLINE
    cycle_label: str                            # 'Cycle 18' or 'Manual production'
    job_id: str
    cycle_number: Optional[int] = None
    cycle_id: Optional[str] = None
    scheduled_start: Optional[Any] = None
    topic: Optional[str] = None
    video_title: Optional[str] = None
    category: Optional[str] = None
    video_duration_sec: Optional[float] = None
    production_elapsed_sec: Optional[float] = None
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    thumbnail_score: Optional[float] = None
    thumbnail_data_uri: Optional[str] = None
    qc_checks: Optional[Dict[str, Any]] = None  # real check -> PASS/FAIL map when the run reported it
    qc_gate_passed: Optional[bool] = None       # True when the QC gate is recorded as passed
    youtube: Optional[YouTubeState] = None
    target_publication: Optional[Any] = None
    artifacts: List[Tuple[str, str]] = field(default_factory=list)   # (label, project-relative path)
    recovery: RecoveryInfo = field(default_factory=RecoveryInfo)
    stage_timeline: List[StageMark] = field(default_factory=list)
    # failure only
    failed_stage: Optional[str] = None
    last_completed_stage: Optional[str] = None
    error_message: Optional[str] = None         # already redacted
    error_at: Optional[Any] = None
    recommended_action: Optional[str] = None
    unapproved_artifacts: List[str] = field(default_factory=list)
    can_resume: Optional[bool] = None
    # shared
    day: Optional[DayProgress] = None
    extra_rows: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # caller-supplied verified details
    generated_at: Optional[datetime] = None


# ---- failure interpretation -------------------------------------------------------------------------------------
_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"CHECK_A_TOPIC_NARRATION", re.I),
     "The quality gate blocked the video: the narration did not mention the topic's key words.",
     "This is deterministic: retrying the same script will not pass. No action is needed for this slot; "
     "if it repeats across topics, review the narration generator."),
    (re.compile(r"VisualDiversityValidator", re.I),
     "The storyboard was rejected by the visual-diversity check (prohibited or repeated imagery).",
     "Usually recovers on the next attempt with new visuals. Investigate if several cycles fail this way."),
    (re.compile(r"recovery window expired|RECOVERY_WINDOW_EXPIRED", re.I),
     "The recovery window closed before the cycle could complete.",
     "No action is needed for this slot; the next cycle runs on schedule. Investigate if this repeats."),
    (re.compile(r"already selected|already used|integrity gate", re.I),
     "No new eligible topic was available: every discovered topic had already been used.",
     "Topic discovery recovers on its own as sources refresh. Check the topic pool if this repeats."),
    (re.compile(r"BLOCKED_QC|QC_REJECTED|qc check", re.I),
     "The quality gate blocked the video.",
     "The video was not published. Review the QC details below; the next cycle proceeds normally."),
    (re.compile(r"DEADLINE_EXCEEDED|missed", re.I),
     "The cycle missed its production deadline.",
     "No action is needed for this slot. Check that the studio PC and scheduler were running."),
    (re.compile(r"timed? ?out|timeout|connection|network|ssl|getaddrinfo|429|rate.?limit|5\d\d", re.I),
     "A network or provider error interrupted production.",
     "Usually transient. If it repeats, check the internet connection and provider status."),
]


_QUEUE_STATES = {"QUEUED", "PENDING", "DISPATCHING", "RUNNING", "RETRYING", "RETRY_SCHEDULED", "RECOVERY_PENDING",
                 "RECOVERY_WINDOW_EXPIRED", "FAILED"}
_STAGE_HINTS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"integrity gate|already selected|already used|topic discovery|no eligible topic", re.I), "TOPIC_SELECTION"),
    (re.compile(r"VisualDiversityValidator|storyboard", re.I), "STORYBOARD"),
    (re.compile(r"BLOCKED_QC|QC_REJECTED|quality control", re.I), "QUALITY_CONTROL"),
    (re.compile(r"ffmpeg|render", re.I), "RENDERING"),
]


def failed_stage_label(stage: Optional[str], error: Optional[str]) -> Optional[str]:
    """A real pipeline stage; job queue states (QUEUED, RUNNING...) are not stages, so infer from the error instead."""
    if stage and str(stage).upper() not in _QUEUE_STATES:
        return stage
    for pat, hint in _STAGE_HINTS:
        if error and pat.search(error):
            return hint
    return None


def interpret_failure(error: Optional[str], outcome: str) -> Tuple[str, Optional[str]]:
    """(human-readable reason, suggested action) for an error string."""
    text = error or ""
    for pat, reason, action in _PATTERNS:
        if pat.search(text) or (outcome == "MISSED_DEADLINE" and "missed" in reason.lower()):
            return reason, action
    first = re.sub(r"^\[(DETERMINISTIC|TRANSIENT|UNKNOWN)\]\s*", "", text.strip().splitlines()[0] if text.strip() else "")
    return (D.clip(first, 220) or "The pipeline stopped with an error."), None


def _qc_summary(checks: Optional[Dict[str, Any]]) -> Tuple[Optional[str], List[str]]:
    if not isinstance(checks, dict) or not checks:
        return None, []
    vals = {k: str(v).upper() for k, v in checks.items() if isinstance(v, (str, bool))}
    if not vals:
        return None, []
    passed = sum(1 for v in vals.values() if v in ("PASS", "PASSED", "TRUE", "OK"))
    failed = [D.humanize(k) or k for k, v in vals.items() if v in ("FAIL", "FAILED", "FALSE")]
    return "%d of %d checks passed" % (passed, len(vals)), failed


def _timeline_lines(marks: List[StageMark]) -> List[Tuple[str, Optional[str]]]:
    rows = []
    for m in marks:
        t = D.fmt_clock(m.at)
        if t:
            rows.append((D.humanize(m.stage) or m.stage, t))
    return rows


def pipeline_rows(yt: Optional[YouTubeState], target_publication: Any = None) -> List[Tuple[str, Optional[str]]]:
    """Three separate states: produced, uploaded, published. Unknown is never reported as done."""
    if yt is None or yt.state == YT_UNKNOWN:
        uploaded, published = D.UNAVAILABLE, D.UNAVAILABLE
    elif yt.state in (YT_NOT_QUEUED, YT_QUEUED):
        uploaded = "No" + (" (in the publishing queue)" if yt.state == YT_QUEUED else "")
        published = "No"
    elif yt.state == YT_UPLOAD_FAILED:
        uploaded, published = "Failed", "No"
    elif yt.state == YT_PUBLISHED:
        uploaded, published = "Yes", "Yes (public)"
    elif yt.state == YT_UPLOADED:
        uploaded, published = "Yes", D.UNAVAILABLE
    else:
        uploaded = "Yes (private)"
        published = ("Scheduled for " + D.fmt_datetime(yt.scheduled_for)) if yt.state == YT_SCHEDULED and D.fmt_datetime(yt.scheduled_for) else "Not yet"
    rows: List[Tuple[str, Optional[str]]] = [("Production", "Completed"), ("Uploaded to YouTube", uploaded),
                                             ("Published", published)]
    if yt is not None:
        rows += [("YouTube video", yt.url), ("Note", yt.detail)]
    if not (yt and yt.state in (YT_SCHEDULED, YT_PUBLISHED)):
        rows.append(("Target publication slot", D.fmt_datetime(target_publication) or D.fmt_time(target_publication)))
    return rows


def _subject(ctx: ProductionEmailContext, status: str, tail: str) -> str:
    # Subject format is kept stable on purpose (operators filter on it).
    label = ctx.cycle_label if ctx.cycle_number is None else "Cycle %d" % ctx.cycle_number
    return D.clip("CrazzyDocs | %s | %s | %s" % (label, status, tail), 200) or ""


def _eyebrow(ctx: ProductionEmailContext, kind: str) -> str:
    when = D.fmt_time(ctx.scheduled_start)
    return " · ".join(x for x in (kind, ctx.cycle_label, ("scheduled " + when) if when else None) if x)


# ---- success ----------------------------------------------------------------------------------------------------
def render_success(ctx: ProductionEmailContext) -> D.RenderedEmail:
    title = ctx.video_title or ctx.topic or "Production %s" % ctx.job_id
    rec = ctx.recovery
    recovered = bool(rec.attempts and rec.attempts > 1)
    yt = ctx.youtube

    if recovered:
        headline = "Production completed after recovery"
        desc = "The video was produced on attempt %d after an earlier failure." % rec.attempts
    else:
        headline = "Production completed successfully"
        desc = "The video passed the quality gate."
    if yt and yt.state == YT_PUBLISHED:
        desc += " It is published on YouTube."
    elif yt and yt.state == YT_UPLOADED:
        desc += " It is uploaded to YouTube."
    elif yt and yt.state in (YT_UPLOADED_PRIVATE, YT_SCHEDULED):
        desc += " It is uploaded to YouTube as private" + (", with publication scheduled." if yt.state == YT_SCHEDULED else ".")
    elif yt and yt.state in (YT_QUEUED, YT_NOT_QUEUED):
        desc += " It has not been uploaded to YouTube yet."

    qc_text, qc_failed = _qc_summary(ctx.qc_checks)
    if qc_text is None and ctx.qc_gate_passed:
        qc_text = "Passed"
    metrics = D.metric_grid([
        ("Video length", D.fmt_duration(ctx.video_duration_sec), None),
        ("Production time", D.fmt_duration(ctx.production_elapsed_sec), None),
        ("Thumbnail score", D.fmt_score(ctx.thumbnail_score), None),
        ("Retries", D.fmt_count(rec.retries), None),
    ])

    details = D.kv_table([
        ("Topic", ctx.topic),
        ("Video title", ctx.video_title),
        ("Category", ctx.category),
        ("Cycle", ctx.cycle_label),
        ("Job ID", ctx.job_id),
        ("Started", D.fmt_datetime(ctx.started_at) or D.fmt_time(ctx.started_at)),
        ("Completed", D.fmt_datetime(ctx.completed_at) or D.fmt_time(ctx.completed_at)),
        ("Quality gate", qc_text),
    ] + list(ctx.extra_rows))

    pipeline = D.kv_table(pipeline_rows(yt, ctx.target_publication))
    recovery_sec = None
    if recovered:
        recovery_sec = D.section(
            "Recovery",
            D.kv_table([("Attempts", "%d" % rec.attempts + (" of %d allowed" % rec.max_attempts if rec.max_attempts else "")),
                        ("Original failure", rec.first_failure),
                        ("Recovered", D.fmt_datetime(rec.recovered_at)),
                        ("Final result", "Completed")]),
        )

    qc_warn = D.callout("warning", "Checks reported as failed", items=qc_failed) if qc_failed else D.Block("", "")
    timeline = _timeline_lines(ctx.stage_timeline)
    artifacts = D.kv_table(ctx.artifacts)

    sections = [
        D.section(None, D.status_banner("success", headline, desc)),
        D.section("Key metrics", metrics),
        D.section("Production", details, qc_warn),
        D.section("Production, upload and publication", pipeline),
        recovery_sec,
        D.section("Thumbnail", D.image(ctx.thumbnail_data_uri, "Selected thumbnail for " + (ctx.topic or "this video"),
                                       "Selected thumbnail" + (" · score " + D.fmt_score(ctx.thumbnail_score) if ctx.thumbnail_score is not None else ""), width=150)),
        D.section("Stage timeline", D.kv_table(timeline)) if timeline else None,
        D.section("Today so far", D.paragraph(ctx.day.summary())) if ctx.day else None,
        D.section("Output files (studio PC)", artifacts) if ctx.artifacts else None,
    ]
    status = "SUCCESS"
    return D.document(
        subject=_subject(ctx, status, title),
        eyebrow=_eyebrow(ctx, "Production report"),
        title=title,
        subtitle=" · ".join(x for x in ("Completed " + (D.fmt_datetime(ctx.completed_at) or D.fmt_time(ctx.completed_at) or "") if ctx.completed_at else None,
                                       ctx.cycle_label) if x),
        preheader="%s completed: %s. %s" % (ctx.cycle_label, ctx.topic or title, (yt.label if yt else "")),
        sections=sections,
        generated_at=ctx.generated_at,
    )


# ---- failure ----------------------------------------------------------------------------------------------------
_OUTCOME_TEXT = {
    "FAILED": "Production failed",
    "QC_REJECTED": "Blocked by the quality gate",
    "MISSED_DEADLINE": "Production deadline missed",
}


def render_failure(ctx: ProductionEmailContext) -> D.RenderedEmail:
    rec = ctx.recovery
    reason, suggested = interpret_failure(ctx.error_message, ctx.outcome)
    action = ctx.recommended_action or suggested or "Review the failure in the Control Center."
    headline = _OUTCOME_TEXT.get(ctx.outcome, "Production failed")
    if rec.window_expired:
        headline += " · recovery window expired"
    elif rec.no_time_left:
        headline += " · no time left to retry"
    title = ctx.topic or "%s did not complete" % ctx.cycle_label

    if rec.retry_scheduled:
        retry_text = "Another attempt is scheduled" + (" for " + D.fmt_datetime(rec.next_attempt_at) if rec.next_attempt_at else "")
    elif rec.window_expired:
        retry_text = "No further attempts: the recovery window has expired"
    elif rec.no_time_left:
        retry_text = "No further attempts: not enough time remains before the recovery deadline"
    elif rec.retry_scheduled is False:
        retry_text = "No further automatic attempts"
    else:
        retry_text = None

    stage = failed_stage_label(ctx.failed_stage, ctx.error_message)
    metrics = D.metric_grid([
        ("Attempts", D.fmt_count(rec.attempts), ("of %d allowed" % rec.max_attempts) if rec.max_attempts and rec.attempts else None),
        ("Failed stage", D.humanize(stage), None),
    ])
    details = D.kv_table([
        ("Topic", ctx.topic),
        ("Cycle", ctx.cycle_label),
        ("Job ID", ctx.job_id),
        ("Scheduled start", D.fmt_datetime(ctx.scheduled_start)),
        ("Last completed stage", D.humanize(ctx.last_completed_stage)),
        ("Failed stage", D.humanize(stage)),
        ("Failed at", D.fmt_datetime(ctx.error_at)),
        ("Resumable checkpoint", None if ctx.can_resume is None else ("Yes" if ctx.can_resume else "No")),
    ] + list(ctx.extra_rows))
    recovery = D.kv_table([
        ("Retry status", retry_text),
        ("Recovery deadline", D.fmt_datetime(rec.deadline)),
        ("Window", "Expired" if rec.window_expired else None),
        ("First failure", rec.first_failure if rec.first_failure and rec.first_failure != ctx.error_message else None),
    ])
    unapproved = (D.callout("danger", "Unapproved artifacts: do not publish or use",
                            "These files were created before the failure and did not pass QC.",
                            items=ctx.unapproved_artifacts) if ctx.unapproved_artifacts else D.Block("", ""))
    yt_note = D.paragraph("Nothing from this attempt was uploaded or published.") if (
        not ctx.youtube or ctx.youtube.state in (YT_NOT_QUEUED, YT_UNKNOWN)) else D.kv_table([("YouTube", ctx.youtube.label)])

    sections = [
        D.section(None, D.status_banner("danger", headline, reason)),
        D.section("Recommended action", D.callout("info", "What to do", action)),
        D.section("Key facts", metrics),
        D.section("Details", details, yt_note),
        D.section("Recovery", recovery),
        D.section(None, unapproved),
        D.section("Today so far", D.paragraph(ctx.day.summary())) if ctx.day else None,
        D.section("Technical details", D.tech_details(ctx.error_message)),
    ]
    return D.document(
        subject=_subject(ctx, ctx.outcome, "Action Required"),
        eyebrow=_eyebrow(ctx, "Production alert"),
        title=title,
        subtitle=headline,
        preheader="%s: %s. %s" % (ctx.cycle_label, headline, reason),
        sections=sections,
        generated_at=ctx.generated_at,
    )
