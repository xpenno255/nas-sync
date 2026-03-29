import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import get_scheduler_config, get_f1_config
from sync_engine import run_sync_all
from f1_organizer import scan_and_organize

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
JOB_ID = "nas_sync_job"
F1_JOB_ID = "f1_scan_job"


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


async def update_scheduler():
    """Update scheduler based on current config."""
    config = await get_scheduler_config()
    
    # Remove existing job if present
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    
    if config['enabled']:
        scheduler.add_job(
            scheduled_sync,
            trigger=IntervalTrigger(minutes=config['interval_minutes']),
            id=JOB_ID,
            replace_existing=True
        )
        logger.info(f"Scheduler enabled: running every {config['interval_minutes']} minutes")
    else:
        logger.info("Scheduler disabled")


async def update_f1_scheduler():
    """Update F1 scheduler based on current config."""
    config = await get_f1_config()

    # Remove existing job if present
    if scheduler.get_job(F1_JOB_ID):
        scheduler.remove_job(F1_JOB_ID)

    if config.get('enabled') and config.get('watch_folder') and config.get('output_folder'):
        scheduler.add_job(
            scheduled_f1_scan,
            trigger=IntervalTrigger(minutes=config.get('scan_interval_minutes', 15)),
            id=F1_JOB_ID,
            replace_existing=True
        )
        logger.info(f"F1 scheduler enabled: scanning every {config.get('scan_interval_minutes', 15)} minutes")
    else:
        logger.info("F1 scheduler disabled")


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


def get_scheduler_status():
    """Get current scheduler status."""
    job = scheduler.get_job(JOB_ID)
    f1_job = scheduler.get_job(F1_JOB_ID)
    return {
        "running": scheduler.running,
        "job_active": job is not None,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "f1_job_active": f1_job is not None,
        "f1_next_run": f1_job.next_run_time.isoformat() if f1_job and f1_job.next_run_time else None
    }
