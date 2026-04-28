"""APScheduler wrapper that runs the daily briefing job."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from its_briefing import generate
from its_briefing.config import Settings

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def start(settings: Settings) -> BackgroundScheduler:
    """Start the background scheduler. Idempotent — returns the running instance."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    trigger = CronTrigger(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        timezone=settings.timezone,
    )
    scheduler.add_job(
        generate.run,
        trigger=trigger,
        id="daily_briefing",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started; next run at %s", scheduler.get_job("daily_briefing").next_run_time
    )
    return scheduler


def next_run_time() -> Optional[datetime]:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_briefing")
    return job.next_run_time if job else None


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def reschedule(hour: int, minute: int, tz: str) -> None:
    """Replace the daily_briefing trigger with a new cron at the given time/timezone.

    Raises:
        RuntimeError: if the scheduler is not running (call start() first).
        ValueError: if the timezone string is not a valid IANA name (raised by CronTrigger).
    """
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        raise RuntimeError("scheduler not running; call start() first")
    trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    _scheduler.reschedule_job("daily_briefing", trigger=trigger)
    logger.info(
        "Scheduler rescheduled to %02d:%02d %s; next run %s",
        hour,
        minute,
        tz,
        _scheduler.get_job("daily_briefing").next_run_time,
    )
