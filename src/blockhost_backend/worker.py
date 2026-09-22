"""Dedicated background worker entry point (Now powered by Celery)."""

from __future__ import annotations

import logging
import sys
import subprocess

from blockhost_backend.services.startup import bootstrap_application_data, bootstrap_database

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    bootstrap_database()
    bootstrap_application_data()
    
    logger = logging.getLogger("worker")
    logger.info("Starting Celery worker and beat scheduler...")
    
    try:
        # Run Celery worker with embedded beat scheduler for convenience
        subprocess.run(
            ["celery", "-A", "blockhost_backend.worker.celery_app", "worker", "-B", "--loglevel=info"],
            check=True
        )
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
