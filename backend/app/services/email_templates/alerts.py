"""Event alerts (Stage 3.22 notification types): upload result, YouTube authorization, scheduler and system errors.

Replaces the former ``key: value`` text dump with the shared design. Trigger semantics are unchanged;
the caller still decides when an alert is sent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.app.services.email_templates import design as D

# event type -> (eyebrow, tone, headline, default action)
_EVENTS: Dict[str, Tuple[str, str, str, Optional[str]]] = {
    "UPLOAD_COMPLETED": ("YouTube upload", "success", "Upload to YouTube completed",
                         None),
    "UPLOAD_FAILED": ("YouTube upload", "danger", "Upload to YouTube failed",
                      "Open the Publishing Queue in the Control Center and retry the upload. The video file is kept."),
    "YOUTUBE_AUTH_PROBLEM": ("YouTube connection", "warning", "YouTube authorization needs attention",
                             "Reconnect YouTube from the Control Center. Uploads wait until the connection is restored."),
    "SCHEDULER_FAILED": ("Scheduler", "danger", "A scheduled job failed",
                         "Check the scheduler in the Control Center. Production cycles are not affected unless stated."),
    "CRITICAL_SYSTEM_ERROR": ("System", "danger", "Critical system error",
                              "Review the Control Center diagnostics."),
    "PRODUCTION_COMPLETED": ("Production", "success", "Production completed", None),
    "PRODUCTION_FAILED": ("Production", "danger", "Production failed", "Review the failure in the Control Center."),
}

_LABELS = {
    "upload_id": "Publishing queue ID", "video_title": "Video title", "status": "Status",
    "youtube_video_id": "YouTube video ID", "error": "Error", "error_type": "Error type", "job": "Job",
    "timestamp_ist": "Time", "production_id": "Production ID", "job_id": "Job ID", "cycle_id": "Cycle",
}
_SKIP = {"event_type"}


def _value(key: str, value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if key.endswith(("_ist", "_utc", "_at")) or key == "timestamp":
        return D.fmt_datetime(value) or str(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return None
    if key == "status" and isinstance(value, str) and value.replace("_", "").isalpha():
        return D.humanize(value)
    return D.clip(value, 400)


def render_event(event_type: str, subject: str, details: Dict[str, Any],
                 generated_at: Optional[datetime] = None) -> D.RenderedEmail:
    """``details`` must already be redacted by the caller (dispatch_stage_notification does this)."""
    eyebrow, tone, headline, action = _EVENTS.get(event_type, ("Notification", "info", D.humanize(event_type) or "Notification", None))
    rows: List[Tuple[str, Optional[str]]] = []
    for k, v in details.items():
        if k in _SKIP or k in ("error", "message"):
            continue
        rows.append((_LABELS.get(k, D.humanize(k) or k), _value(k, v)))
    vid = details.get("youtube_video_id")
    if vid and event_type == "UPLOAD_COMPLETED":
        rows.append(("Link", "https://youtu.be/%s" % vid))
        desc = "The video is on YouTube. Upload is not publication: check its visibility and schedule in the Publishing Queue."
    else:
        desc = None
    error = details.get("error") or details.get("message")
    sections = [
        D.section(None, D.status_banner(tone, headline, desc)),
        D.section("Recommended action", D.callout("info", "What to do", action)) if action else None,
        D.section("Details", D.kv_table(rows)),
        D.section("Technical details", D.tech_details(str(error))) if error else None,
    ]
    return D.document(subject=subject, eyebrow=eyebrow, title=headline,
                      subtitle=D.clip(details.get("video_title") or details.get("job"), 120) if (details.get("video_title") or details.get("job")) else None,
                      preheader=headline, sections=sections, generated_at=generated_at)
