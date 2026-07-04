import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from database import get_scheduler_config, get_f1_config, get_cleanup_config
from sync_engine import run_sync_all
from f1_organizer import scan_and_organize
from download_cleanup import run_cleanup

logger = logging.getLogger(__name__)

# coalesce + generous misfire grace: if a run is delayed (long sync, container
# restart), run it once late instead of silently dropping it.
scheduler = AsyncIOScheduler(
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
)
JOB_ID = "nas_sync_job"
F1_JOB_ID = "f1_scan_job"
CLEANUP_JOB_ID = "cleanup_job"


def build_trigger(schedule_mode: str, interval_minutes: int, daily_time: str,
                  hourly_minute: int = 0):
    """Build an APScheduler trigger from a schedule config.

    hourly_minute staggers the hourly jobs so they don't all fire at once.
    """
    if schedule_mode == "daily":
        try:
            hour, minute = (int(p) for p in (daily_time or "03:00").split(":")[:2])
        except (ValueError, AttributeError):
            hour, minute = 3, 0
        return CronTrigger(hour=hour, minute=minute), f"daily at {hour:02d}:{minute:02d}"

    if schedule_mode == "hourly":
        return CronTrigger(minute=hourly_minute), f"hourly at :{hourly_minute:02d}"

    minutes = max(1, int(interval_minutes or 15))
    return IntervalTrigger(minutes=minutes), f"every {minutes} minutes"


async def scheduled_sync():
    """Wrapper for scheduled sync execution."""
    logger.info("Scheduled sync triggered")
    try:
        result = await run_sync_all()
        logger.info(f"Scheduled sync result: {result['status']}")
    except Exception as e:
        logger.error(f"Scheduled sync error: {e}")


async def scheduled_f1_scan():
    """Wrapper for scheduled F1 scan execution."""
    logger.info("Scheduled F1 scan triggered")
    try:
        result = await scan_and_organize()
        logger.info(f"Scheduled F1 scan result: {result['status']} - moved: {result.get('moved', 0)}")
    except Exception as e:
        logger.error(f"Scheduled F1 scan error: {e}")


async def scheduled_cleanup():
    """Wrapper for scheduled download cleanup execution."""
    logger.info("Scheduled download cleanup triggered")
    try:
        result = await run_cleanup(dry_run=False)
        logger.info(
            f"Scheduled cleanup result: {result['status']} - "
            f"renamed: {result.get('renamed', 0)}, junk removed: {result.get('junk_removed', 0)}"
        )
    except Exception as e:
        logger.error(f"Scheduled cleanup error: {e}")


def _apply_job(job_id: str, func, enabled: bool, trigger, description: str, label: str):
    """Add, replace or remove a job to match the desired config."""
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if enabled:
        scheduler.add_job(func, trigger=trigger, id=job_id, replace_existing=True)
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"
        logger.info(f"{label} scheduled {description} (next run: {next_run})")
    else:
        logger.info(f"{label} schedule disabled")


async def update_scheduler():
    """Update sync scheduler based on current config."""
    config = await get_scheduler_config()
    trigger, description = build_trigger(
        config.get("schedule_mode", "interval"),
        config.get("interval_minutes", 15),
        config.get("daily_time", "03:00"),
        hourly_minute=0
    )
    _apply_job(JOB_ID, scheduled_sync, bool(config.get("enabled")), trigger, description, "NAS sync")


async def update_f1_scheduler():
    """Update F1 scheduler based on current config."""
    config = await get_f1_config()
    enabled = bool(config.get("enabled") and config.get("watch_folder") and config.get("output_folder"))
    trigger, description = build_trigger(
        config.get("schedule_mode", "interval"),
        config.get("scan_interval_minutes", 15),
        config.get("daily_time", "03:00"),
        hourly_minute=15
    )
    _apply_job(F1_JOB_ID, scheduled_f1_scan, enabled, trigger, description, "F1 scan")


async def update_cleanup_scheduler():
    """Update download cleanup scheduler based on current config."""
    config = await get_cleanup_config()
    enabled = bool(config.get("enabled") and config.get("watch_folder"))
    trigger, description = build_trigger(
        config.get("schedule_mode", "hourly"),
        config.get("interval_minutes", 60),
        config.get("daily_time", "03:00"),
        hourly_minute=30
    )
    _apply_job(CLEANUP_JOB_ID, scheduled_cleanup, enabled, trigger, description, "Download cleanup")


def start_scheduler():
    """Start the scheduler."""
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")


def _job_status(job_id: str):
    job = scheduler.get_job(job_id)
    return (
        job is not None,
        job.next_run_time.isoformat() if job and job.next_run_time else None
    )


def get_scheduler_status():
    """Get current scheduler status."""
    job_active, next_run = _job_status(JOB_ID)
    f1_active, f1_next = _job_status(F1_JOB_ID)
    cleanup_active, cleanup_next = _job_status(CLEANUP_JOB_ID)
    return {
        "running": scheduler.running,
        "job_active": job_active,
        "next_run": next_run,
        "f1_job_active": f1_active,
        "f1_next_run": f1_next,
        "cleanup_job_active": cleanup_active,
        "cleanup_next_run": cleanup_next
    }
