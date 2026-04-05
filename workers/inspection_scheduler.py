"""
Background scheduler for fetching and updating scheduled inspections.
Runs fetchers for public calendar data and stores results in database.
"""

import logging
import threading
from datetime import datetime
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from utils.inspection_calendar_fetchers import (
    ContraCostaFetcher,
    BerkeleyFetcher,
    SanJoseFetcher,
    ScheduledInspection,
)
from utils.web_db import insert_scheduled_inspection, cleanup_old_inspections

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler = None


def start_inspection_scheduler():
    """
    Start the background scheduler for inspection fetches.
    Called once during application startup.
    """
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Inspection scheduler already running")
        return

    try:
        _scheduler = BackgroundScheduler()

        # Fetch inspections every morning at 9:00 AM
        _scheduler.add_job(
            fetch_all_inspections,
            'cron',
            hour=9,
            minute=0,
            id='fetch_inspections_daily',
            name='Daily inspection fetch',
            misfire_grace_time=3600,  # Allow 1 hour grace period
        )

        # Cleanup old records every week (Monday at 2 AM)
        _scheduler.add_job(
            cleanup_old_inspection_data,
            'cron',
            day_of_week='mon',
            hour=2,
            minute=0,
            id='cleanup_inspections_weekly',
            name='Weekly cleanup of old inspections',
            misfire_grace_time=3600,
        )

        _scheduler.start()
        logger.info("Inspection scheduler started successfully")

    except Exception as e:
        logger.error(f"Failed to start inspection scheduler: {e}")
        raise


def stop_inspection_scheduler():
    """Stop the background scheduler."""
    global _scheduler

    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Inspection scheduler stopped")


def fetch_all_inspections():
    """
    Fetch inspection schedules from all available sources.
    This is called by the scheduler every morning.
    """
    logger.info("Starting scheduled inspection fetch...")

    try:
        all_inspections = []
        fetchers = [
            ContraCostaFetcher(),
            BerkeleyFetcher(),
            SanJoseFetcher(),
        ]

        for fetcher in fetchers:
            try:
                fetcher_name = fetcher.__class__.__name__
                logger.info(f"Fetching from {fetcher_name}...")

                inspections = fetcher.fetch()
                if inspections:
                    logger.info(f"{fetcher_name} returned {len(inspections)} inspections")
                    all_inspections.extend(inspections)
                else:
                    logger.warning(f"{fetcher_name} returned no results")

            except Exception as e:
                logger.error(f"Error fetching from {fetcher.__class__.__name__}: {e}")
                continue

        # Store all inspections in database
        saved_count = 0
        for inspection in all_inspections:
            try:
                row_id = insert_scheduled_inspection(inspection.to_dict())
                saved_count += 1
            except Exception as e:
                logger.error(f"Error saving inspection {inspection.permit_id}: {e}")
                continue

        logger.info(f"Successfully saved {saved_count} inspections to database")
        return saved_count

    except Exception as e:
        logger.error(f"Fatal error during inspection fetch: {e}")
        return 0


def fetch_inspections_now():
    """
    Manually trigger inspection fetch (useful for testing or admin requests).
    """
    logger.info("Manual inspection fetch triggered")
    count = fetch_all_inspections()
    logger.info(f"Manual fetch completed: {count} inspections saved")
    return count


def cleanup_old_inspection_data():
    """
    Remove old inspection records from database.
    This is called weekly by the scheduler.
    """
    logger.info("Starting weekly cleanup of old inspection records...")

    try:
        deleted_count = cleanup_old_inspections(older_than_days=60)
        logger.info(f"Cleanup completed: {deleted_count} old inspection records deleted")
        return deleted_count

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        return 0


def get_scheduler_status() -> dict:
    """
    Get current status of the inspection scheduler.

    Returns:
        Dictionary with scheduler status and job info
    """
    global _scheduler

    if not _scheduler:
        return {
            'running': False,
            'jobs': [],
        }

    try:
        jobs = []
        for job in _scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': str(job.next_run_time) if job.next_run_time else None,
            })

        return {
            'running': _scheduler.running,
            'jobs': jobs,
        }

    except Exception as e:
        logger.error(f"Error getting scheduler status: {e}")
        return {
            'running': False,
            'error': str(e),
        }
