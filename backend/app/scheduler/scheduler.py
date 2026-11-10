"""
Persistent Scheduler for YouTube Shorts Studio.
Triggers daily production pipeline at 10:00 AM IST (04:30 UTC).
"""
import os
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.workers.pipeline import pipeline

scheduler = AsyncIOScheduler()

async def daily_production_job():
    """Daily automated job scheduled for 10:00 AM IST."""
    print(f"\n[SCHEDULER] Triggering scheduled daily production pipeline at {datetime.now()}...", flush=True)
    auto_pub = settings.PUBLISHING_MODE == "auto_publish"
    result = await pipeline.run_pipeline(auto_upload=auto_pub)
    print(f"[SCHEDULER] Daily production completed with status: {result.get('status')}", flush=True)

def start_scheduler():
    """Initializes and starts the background APScheduler."""
    # 10:00 AM IST = 04:30 UTC
    # User-configured time can be parsed from DAILY_PUBLISH_TIME_IST
    trigger = CronTrigger(hour=4, minute=30, timezone=timezone.utc)
    scheduler.add_job(daily_production_job, trigger, id="daily_shorts_pipeline", replace_existing=True)
    scheduler.start()
    print("APScheduler started: Daily automated pipeline set for 10:00 AM IST.", flush=True)

def get_scheduler_status():
    """Returns scheduler running state and next run time."""
    job = scheduler.get_job("daily_shorts_pipeline")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "is_running": scheduler.running,
        "next_scheduled_run": next_run,
        "schedule_time_ist": settings.DAILY_PUBLISH_TIME_IST,
        "publishing_mode": settings.PUBLISHING_MODE
    }
