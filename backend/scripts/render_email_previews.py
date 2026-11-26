"""Render every CrazzyDocs email type to local HTML + text previews (no database, no SMTP, nothing is sent).

Usage::

    python backend/scripts/render_email_previews.py [--out docs/email_previews]

Open ``<out>/index.html`` in a browser. Resize the window (or use device emulation at 375 px) to check
the mobile layout. The data is representative, modelled on real production records of 2026-11-24.
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app.services.email_templates import alerts as A  # noqa: E402
from backend.app.services.email_templates import design as D  # noqa: E402
from backend.app.services.email_templates import production as P  # noqa: E402
from backend.app.services.email_templates import report as R  # noqa: E402

IST = D.IST
DAY = datetime(2026, 11, 24, tzinfo=IST)
GEN = datetime(2026, 11, 24, 17, 19, tzinfo=IST)


def _thumb() -> str:
    """A small generated stand-in thumbnail (vertical 9:16 gradient with a caption bar)."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (180, 320), "#0b1220")
        d = ImageDraw.Draw(img)
        for y in range(320):
            d.line([(0, y), (180, y)], fill=(20 + y // 6, 40 + y // 8, 90 + y // 5))
        d.rectangle([12, 230, 168, 290], fill="#fee004")
        d.text((22, 250), "NEW LASER WELDING", fill="#0b1220")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _day(completed, failed, running=0, upcoming=0, not_started=0, scheduled=24):
    return P.DayProgress("2026-11-24", scheduled, completed, failed, running, upcoming, not_started)


def success_contexts():
    base = dict(cycle_label="Cycle 18", cycle_number=18, cycle_id="cycle_18", job_id="cycle_18_20261124_1700",
                scheduled_start=DAY.replace(hour=17), generated_at=GEN)
    timeline = [P.StageMark(s, DAY.replace(hour=17, minute=m, second=sec)) for s, m, sec in (
        ("STORY_DEVELOPMENT", 3, 10), ("VOICEOVER", 7, 10), ("RENDERING", 11, 14), ("THUMBNAIL", 11, 16),
        ("Video rendered", 11, 18), ("Quality gate passed", 11, 18), ("Uploaded to YouTube", 11, 31),
        ("Publication scheduled", 11, 31))]
    ok = P.ProductionEmailContext(
        outcome="SUCCESS", topic="New laser welding process joins aluminum castings with fewer pores and less spatter",
        video_title="New laser welding process joins aluminum castings with fewer pores and less spatter #Shorts",
        category="Technology", video_duration_sec=52.26, production_elapsed_sec=676.42,
        started_at=DAY.replace(hour=17, second=1), completed_at=DAY.replace(hour=17, minute=11, second=31),
        thumbnail_score=9.46, thumbnail_data_uri=_thumb(), qc_gate_passed=True,
        qc_checks={"video_file": "PASS", "file_size": "PASS", "resolution": "PASS", "duration": "PASS", "video_codec": "PASS",
                   "thumbnail_file": "PASS", "title_seo": "PASS", "description_seo": "PASS", "tags_seo": "PASS",
                   "category": "PASS", "copyright_safety": "PASS", "black_frames": "PASS",
                   "visual_diversity_reuse": "PASS", "canonical_asset_persistence": "PASS", "renderer_compliance": "PASS"},
        youtube=P.YouTubeState(P.YT_SCHEDULED, "itAn8UlBC5s", "private", "2026-11-24T12:30:00Z"),
        recovery=P.RecoveryInfo(attempts=1), stage_timeline=timeline, day=_day(17, 1),
        artifacts=[("Video", "media/productions/cycle_18_20261124_1700/video/showcase_cycle_18_20261124_1700.mp4"),
                   ("Thumbnail", "media/productions/cycle_18_20261124_1700/thumbnails/thumb_cycle_18_20261124_1700.png")],
        **base)
    recovered = P.ProductionEmailContext(
        outcome="SUCCESS", cycle_label="Cycle 17", cycle_number=17, cycle_id="cycle_17", job_id="cycle_17_20261124_1600",
        scheduled_start=DAY.replace(hour=16), generated_at=GEN, topic="Talking to Yourself",
        video_title="Talking to Yourself #Shorts", category="Human Biology", video_duration_sec=55.1,
        production_elapsed_sec=963.0, started_at=DAY.replace(hour=16, minute=10, second=29),
        completed_at=DAY.replace(hour=16, minute=16, second=20), thumbnail_score=9.12, qc_gate_passed=True,
        youtube=P.YouTubeState(P.YT_SCHEDULED, "NdNW7eQyR6k", "private", "2026-11-24T11:30:00Z"),
        recovery=P.RecoveryInfo(attempts=3, first_failure="Stage 3.21J VisualDiversityValidator rejected storyboard: "
                                "[live_talking_to_yourself_b16] Prohibited stick-figure/cartoon imagery",
                                recovered_at=DAY.replace(hour=16, minute=16, second=20)),
        day=_day(16, 1))
    minimal = P.ProductionEmailContext(
        outcome="SUCCESS", cycle_label="Manual production", job_id="manual_20261124_1412",
        topic="Antikythera mechanism", generated_at=GEN, youtube=P.YouTubeState(P.YT_QUEUED))
    return {"01_success": ok, "02_success_recovered_multiple_retries": recovered, "03_success_missing_optional_metrics": minimal}


def failure_contexts():
    qc = P.ProductionEmailContext(
        outcome="QC_REJECTED", cycle_label="Cycle 10", cycle_number=10, cycle_id="cycle_10", job_id="cycle_10_20261124_0900",
        scheduled_start=DAY.replace(hour=9), topic="Fiona (hippopotamus)", failed_stage="BLOCKED_QC",
        last_completed_stage="RENDERING", error_at=DAY.replace(hour=9, minute=14, second=40),
        error_message=("[DETERMINISTIC] ValueError: BLOCKED_QC: [\"[CHECK_A_TOPIC_NARRATION] Narration lacks topic keywords "
                       "(found ['fiona'] out of ['fiona', 'hippopotamus']).\"] (attempt 1 failed; no time for another attempt "
                       "before the recovery deadline 2026-11-24T04:15:00Z (needs 20 min))"),
        recovery=P.RecoveryInfo(attempts=1, deadline="2026-11-24T04:15:00Z", window_expired=True, retry_scheduled=False),
        youtube=P.YouTubeState(P.YT_NOT_QUEUED), day=_day(9, 1), generated_at=GEN)
    long_err = "\n".join(["Traceback (most recent call last):"] + [
        '  File "D:\\\\Agents\\\\CrazzyDocs\\\\backend\\\\app\\\\render\\\\stage_%02d.py", line %d, in run' % (i, 100 + i) for i in range(30)]
        + ["RuntimeError: ffmpeg exited with status 1 after 3 in-stage attempts: Conversion failed! " * 3])
    expired = P.ProductionEmailContext(
        outcome="FAILED", cycle_label="Cycle 3", cycle_number=3, cycle_id="cycle_3", job_id="cycle_3_20261123_0200",
        scheduled_start=datetime(2026, 11, 23, 2, tzinfo=IST), topic=None, failed_stage="TOPIC_DISCOVERY",
        error_at=datetime(2026, 11, 23, 2, 43, 45, tzinfo=IST),
        error_message="Recovery window expired at 2026-11-23T02:45:00+05:30; reconciliation occurred at 02:43:45 after a host outage. " + long_err,
        recovery=P.RecoveryInfo(attempts=None, deadline="2026-11-22T21:15:00Z", window_expired=True, retry_scheduled=False),
        generated_at=GEN)
    return {"04_failure_qc_rejected": qc, "05_failure_recovery_window_expired_long_error": expired}


def _rows(spec):
    rows = []
    for i, (st, topic, prod, vid, retries, score, reason) in enumerate(spec):
        slot = DAY.replace(hour=i)
        rows.append(R.CycleRow(label="Cycle %d" % (i + 1), scheduled=slot, job_id="cycle_%d_20261124_%02d00" % (i + 1, i) if st != R.ST_UPCOMING else None,
                               status=st, topic=topic, production_sec=prod, video_sec=vid, retries=retries,
                               thumbnail_score=score, reason=reason,
                               youtube=P.YouTubeState(P.YT_SCHEDULED, "vid%02d" % i, "private", slot + timedelta(hours=1))
                               if st in (R.ST_COMPLETED, R.ST_RECOVERED) else None))
    return rows


TOPICS = ["Ancient Carthage", "Assyriology", "Mohenjo-daro", "Curiosity rover spots layered rocks", "James Webb Space Telescope",
          "A cactus that changes gears: how the prickly pear makes the most of scarce water", "Thomas Edison", "Levent Alpöge",
          "NSA encryption systems", "Fiona (hippopotamus)", "Flying shuttle", "Riemann hypothesis", "Physics",
          "Verrazzano–Narrows Bridge", "Eurozone crisis", "Monopoly", "Talking to Yourself",
          "New laser welding process joins aluminum castings with fewer pores and less spatter", "Strait of Magellan",
          "Jet engine", "Proto-cuneiform", "One-time pad", "Lebanese liquidity crisis", "Nancy Grace Roman Space Telescope"]
HEALTH = [("Scheduler", "Running, heartbeat 12s ago"), ("Worker", "Idle"), ("Schedule revision", "ccf0bd04f159"),
          ("Pending recovery operations", "0"), ("Email delivery (24 h)", "25 sent or pending, 0 failed or unconfirmed"),
          ("YouTube quota: Data API", "3,250 of 10,000 units used (local estimate, Pacific day)")]


def report_contexts():
    allok = R.OperationsReport(date="2026-11-24", generated_at=datetime(2026, 11, 25, 6, 0, tzinfo=IST), complete_day=True,
                               rows=_rows([(R.ST_COMPLETED, t, 650 + 7 * i, 50 + (i % 9), 0, 9.1 + (i % 5) / 10, None)
                                           for i, t in enumerate(TOPICS)]), health=HEALTH, storage_mb=48213.4, disk_free_gb=211.6)
    spec = []
    for i, t in enumerate(TOPICS):
        if i == 9:
            spec.append((R.ST_FAILED, t, None, None, 1, None, "The quality gate blocked the video: the narration did not mention the topic's key words."))
        elif i == 16:
            spec.append((R.ST_RECOVERED, t, 963, 55.1, 2, 9.12, None))
        elif i == 20:
            spec.append((R.ST_NOT_STARTED, None, None, None, None, None, None))
        else:
            spec.append((R.ST_COMPLETED, t, 640 + 9 * i, 49 + (i % 10), 0, 9.0 + (i % 6) / 10, None))
    mixed = R.OperationsReport(date="2026-11-24", generated_at=datetime(2026, 11, 25, 6, 0, tzinfo=IST), complete_day=True,
                               rows=_rows(spec), health=HEALTH, storage_mb=48213.4, disk_free_gb=211.6)
    partial_spec = [(R.ST_COMPLETED, t, 660 + 5 * i, 52, 0, 9.3, None) for i, t in enumerate(TOPICS[:17])]
    partial_spec.append((R.ST_RUNNING, TOPICS[17], None, None, 0, None, None))
    partial_spec += [(R.ST_UPCOMING, None, None, None, None, None, None)] * 6
    manual = R.CycleRow(label="Manual production", scheduled=DAY.replace(hour=14, minute=12), job_id="manual_20261124_1412",
                        status=R.ST_COMPLETED, topic="Antikythera mechanism", production_sec=702, video_sec=53.4, retries=0, manual=True)
    partial = R.OperationsReport(date="2026-11-24", generated_at=GEN, complete_day=False,
                                 rows=_rows(partial_spec) + [manual], health=HEALTH)
    return {"06_report_all_success": allok, "07_report_mixed_success_failure": mixed, "08_report_partial_day": partial}


def alert_renders():
    return {
        "09_alert_upload_completed": A.render_event("UPLOAD_COMPLETED", "YouTube upload completed: Jet engine #Shorts",
                                                    {"upload_id": 412, "video_title": "Jet engine #Shorts", "status": "uploaded_private",
                                                     "youtube_video_id": "a1B2c3D4e5F", "timestamp_ist": "2026-11-24T20:11:31+05:30"},
                                                    generated_at=GEN),
        "10_alert_youtube_auth": A.render_event("YOUTUBE_AUTH_PROBLEM", "CrazzyDocs YouTube authorization needs attention",
                                                {"status": "reconnect_required", "error": "invalid_grant: Token has been expired or revoked.",
                                                 "timestamp_ist": "2026-11-24T08:02:11+05:30"}, generated_at=GEN),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "email_previews"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    out = {}
    for k, c in success_contexts().items():
        out[k] = P.render_success(c)
    for k, c in failure_contexts().items():
        out[k] = P.render_failure(c)
    for k, c in report_contexts().items():
        out[k] = R.render(c)
    out.update(alert_renders())
    links = []
    for name, r in out.items():
        with open(os.path.join(a.out, name + ".html"), "w", encoding="utf-8") as fh:
            fh.write(r.html)
        with open(os.path.join(a.out, name + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("Subject: %s\n\n%s" % (r.subject, r.text))
        links.append('<li><a href="%s.html">%s</a> (<a href="%s.txt">text</a>) &middot; %s &middot; %.1f KB</li>'
                     % (name, D.esc(name), name, D.esc(r.subject), len(r.html.encode("utf-8")) / 1024))
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<!DOCTYPE html><meta charset='utf-8'><title>CrazzyDocs email previews</title>"
                 "<body style='font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:32px auto;padding:0 16px'>"
                 "<h1>CrazzyDocs email previews</h1><p>Representative data, rendered locally. Nothing is sent.</p><ul>"
                 + "".join(links) + "</ul></body>")
    print("wrote %d previews to %s" % (len(out), a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
