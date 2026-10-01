"""
scheduler.py — Local Hourly Scheduler
======================================
Use this ONLY when running on a machine that stays on (not Cloud Run/Lambda).
For Cloud Run / Lambda, use Cloud Scheduler to call naukri_bot.py directly.

Usage:
    python scheduler.py

This script wakes up every hour, runs the bot, then sleeps again.
It uses minimal resources between runs (no busy loop).
"""

import time
import logging
import schedule
from naukri_bot import run_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def job():
    logger.info("⏰ Scheduled run starting...")
    try:
        run_bot()
    except Exception as e:
        logger.error(f"Scheduled run failed: {e}")
    logger.info("😴 Run complete. Sleeping until next hour.")


# Run immediately on startup, then every hour
logger.info("🚀 Scheduler started — will run every hour.")
job()  # immediate first run

schedule.every(1).hours.do(job)

while True:
    schedule.run_pending()
    time.sleep(60)   # check every minute — very low CPU usage
