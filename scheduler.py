import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import get_scheduler_config
from sync_engine import run_sync_all

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
JOB_ID = "nas_sync_job"


async def scheduled_sync():
    """Wrapper for scheduled sync execution."""
    logger.info("Scheduled sync triggered")
    try:
        result = await run_sync_all()
        logger.info(f"Scheduled sync result: {result['status']}")
    except Exception as e:
        logger.error(f"Scheduled sync error: {e}")


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
    return {
        "running": scheduler.running,
        "job_active": job is not None,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None
    }
