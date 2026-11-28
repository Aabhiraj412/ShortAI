"""
Unified YouTube publishing queue VIEW (2026-11-28).

One list for the dashboard: every English and Hindi video as one row (Video | Channel | QC | Thumbnail | Scheduled |
YouTube status | Actions), sorted by scheduled publication time. This module only MERGES the two existing queues for
display; it never uploads, schedules or authorizes anything:

* English rows come from ``YouTubePublisherService.list_uploads`` (the English publishing ledger) and their actions
  call the existing English endpoints (``/api/publishing/queue/{id}/...``), which use the English OAuth token.
* Hindi rows come from ``HindiPublisher.queue()`` (``hindi_publications``, only localizations that passed Hindi QC
  and whose MP4 lies under ``media/localizations/hi``) and their actions call ``/api/hindi-publishing/{id}/...``,
  which use the Hindi channel's own token, verify its identity and stay behind the Hindi gates.

Each row's ``actions`` list is what that channel's own state machine allows; the endpoints re-check everything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
CHANNELS = {"en": "English", "hi": "Hindi"}

HINDI_STATUS = {"DRAFT": "Not scheduled", "SCHEDULED": "Scheduled (uploads before its time)",
                "UPLOADING": "Uploading", "UPLOADED": "Uploaded (private until its publish time)",
                "FAILED": "Failed", "NEEDS_CHECK": "Needs check (upload result unknown)",
                "WITHDRAWN": "Withdrawn (re-rendered; needs QC and approval again)"}


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _utc(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _ist(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(IST).strftime("%Y-%m-%d %H:%M IST") if d else None


def english_row(it: Dict[str, Any]) -> Dict[str, Any]:
    st = it.get("ui_status") or "Ready to Schedule"
    at = _parse(it.get("scheduled_utc_publish_at"))
    vid = it.get("youtube_video_id")
    actions: List[str] = []
    if st != "Published":
        actions += ["edit", "thumbnail", "schedule"]
    if st == "Scheduled":
        actions.append("unschedule")
    if st not in ("Published", "Publishing"):
        actions.append("publish_now")
    if st == "Failed":
        actions.append("retry")
    if vid:
        actions.append("view")
    tr = it.get("thumbnail_ready") or ""
    return {
        "key": f"en:{it.get('id')}", "channel": "en", "channel_label": CHANNELS["en"], "id": it.get("id"),
        "title": it.get("title") or it.get("topic_title") or f"#{it.get('id')}",
        "subtitle": it.get("production_cycle_id") or it.get("topic_title") or "",
        "qc_status": "QC passed",              # the English queue holds QC-approved productions only
        "qc_passed": True,
        "thumbnail": {"preview_url": f"/api/publishing/queue/{it.get('id')}/thumbnail-preview" if it.get("thumbnail_path") else None,
                      "status": tr or ("custom/auto thumbnail" if it.get("thumbnail_path") else "no thumbnail"),
                      "editable": "thumbnail" in actions},
        "scheduled_at_utc": _utc(at), "scheduled_ist": _ist(at),
        "youtube_status": st, "privacy": it.get("youtube_privacy_status") or it.get("visibility") or "private",
        "youtube_video_id": vid, "youtube_url": it.get("youtube_url") or (f"https://youtu.be/{vid}" if vid else None),
        "error": it.get("error"), "actions": actions,
    }


def hindi_row(it: Dict[str, Any]) -> Dict[str, Any]:
    state = it.get("state") or "DRAFT"
    qc_ok = bool(it.get("qc_passed")) and it.get("localization_status") == "QC_PASSED"
    at = _parse(it.get("scheduled_at_utc"))
    vid = it.get("youtube_video_id")
    actions: List[str] = []
    if qc_ok:                                  # never an action on a video that has not passed Hindi QC
        if state in ("DRAFT", "SCHEDULED", "FAILED"):
            actions += ["edit", "schedule", "publish_now"]
        if state in ("SCHEDULED", "FAILED"):
            actions.append("unschedule")
        if state in ("FAILED", "NEEDS_CHECK"):
            actions.append("retry")
        if state in ("DRAFT", "SCHEDULED", "FAILED"):       # Stage 4.03: per-video approval
            actions.append("unapprove" if it.get("approved") else "approve")
    if vid:
        actions.append("view")
    th = it.get("thumbnail") or {}
    return {
        "key": f"hi:{it.get('id')}", "channel": "hi", "channel_label": CHANNELS["hi"], "id": it.get("id"),
        "title": it.get("title") or it.get("source_job_id"), "subtitle": it.get("source_job_id") or "",
        "qc_status": "Hindi QC passed" if qc_ok else f"Hindi QC not passed ({it.get('localization_status') or 'unknown'})",
        "qc_passed": qc_ok,
        "thumbnail": {"preview_url": f"/api/hindi-publishing/{it.get('id')}/thumbnail-preview" if it.get("thumbnail_asset") else None,
                      "status": th.get("state") or ("asset ready" if it.get("thumbnail_asset") else "no thumbnail asset"),
                      "editable": False, "note": "the English Short's thumbnail, set after YouTube processing"},
        "scheduled_at_utc": _utc(at), "scheduled_ist": _ist(at),
        "youtube_status": HINDI_STATUS.get(state, state), "state": state, "privacy": "private until publish time",
        "youtube_video_id": vid, "youtube_url": f"https://youtu.be/{vid}" if vid else None,
        "error": it.get("last_error"), "actions": actions,
        "approved": bool(it.get("approved")), "approved_by": it.get("approved_by"),
        "approved_at_utc": it.get("approved_at_utc"),
        "blocked_by": list((it.get("eligibility") or {}).get("reasons") or [])
        if state not in ("UPLOADED", "WITHDRAWN") else [],
    }


def merge(english_items: Iterable[Dict[str, Any]], hindi_items: Iterable[Dict[str, Any]],
          channel: str = "all", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """English + Hindi rows, one per video (deduplicated by channel and id), ordered by scheduled publication time:
    upcoming first (soonest first), then past publication times (most recent first), then unscheduled (newest
    first). ``channel``: all | en | hi."""
    now_s = _utc(now or datetime.now(timezone.utc))
    rows: Dict[str, Dict[str, Any]] = {}
    if channel in ("all", "en"):
        for it in english_items:
            r = english_row(it)
            rows.setdefault(r["key"], r)
    if channel in ("all", "hi"):
        for it in hindi_items:
            r = hindi_row(it)
            rows.setdefault(r["key"], r)
    upcoming = sorted((r for r in rows.values() if r["scheduled_at_utc"] and r["scheduled_at_utc"] >= now_s),
                      key=lambda r: (r["scheduled_at_utc"], r["key"]))
    past = sorted((r for r in rows.values() if r["scheduled_at_utc"] and r["scheduled_at_utc"] < now_s),
                  key=lambda r: (r["scheduled_at_utc"], r["key"]), reverse=True)
    rest = sorted((r for r in rows.values() if not r["scheduled_at_utc"]),
                  key=lambda r: (-(int(r["id"]) if str(r["id"]).isdigit() else 0), r["channel"]))
    return upcoming + past + rest
