import logging
from celery import shared_task

from blockhost_backend.api.nodes import perform_node_health_checks
from blockhost_backend.orchestrator.backup import dispatch_due_backup_schedules
from blockhost_backend.orchestrator.database_backup_scheduler import dispatch_daily_database_backups
from blockhost_backend.services.auto_sleeper import _auto_sleeper_cycle
from blockhost_backend.services.node_rebalancer import _rebalance_cycle
from blockhost_backend.services.system_health import check_all_volumes_disk_health
from blockhost_backend.services.billing import expire_subscriptions_now

logger = logging.getLogger(__name__)

@shared_task(name="blockhost_backend.worker.tasks.run_node_watchdog")
def run_node_watchdog():
    logger.info("Running node watchdog task")
    try:
        perform_node_health_checks()
    except Exception as e:
        logger.exception(f"Node watchdog task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_backup_scheduler")
def run_backup_scheduler():
    logger.info("Running backup scheduler task")
    try:
        dispatch_due_backup_schedules()
    except Exception as e:
        logger.exception(f"Backup scheduler task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_auto_sleeper")
def run_auto_sleeper():
    logger.info("Running auto sleeper task")
    try:
        _auto_sleeper_cycle()
    except Exception as e:
        logger.exception(f"Auto sleeper task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_node_rebalancer")
def run_node_rebalancer():
    logger.info("Running node rebalancer task")
    try:
        _rebalance_cycle()
    except Exception as e:
        logger.exception(f"Node rebalancer task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_disk_health_monitor")
def run_disk_health_monitor():
    logger.info("Running disk health monitor task")
    try:
        check_all_volumes_disk_health()
    except Exception as e:
        logger.exception(f"Disk health monitor task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_database_backup_scheduler")
def run_database_backup_scheduler():
    logger.info("Running database backup scheduler task")
    try:
        dispatch_daily_database_backups()
    except Exception as e:
        logger.exception(f"Database backup scheduler task failed: {e}")

@shared_task(name="blockhost_backend.worker.tasks.run_subscription_expiration")
def run_subscription_expiration():
    logger.info("Running subscription expiration task")
    try:
        expire_subscriptions_now()
    except Exception as e:
        logger.exception(f"Subscription expiration task failed: {e}")
