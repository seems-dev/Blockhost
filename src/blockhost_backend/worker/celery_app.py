from celery import Celery
from celery.schedules import crontab
from blockhost_backend.config.config_manager import get_settings

settings = get_settings()

celery_app = Celery(
    "blockhost_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["blockhost_backend.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
)

# Celery Beat Schedule
celery_app.conf.beat_schedule = {
    "node-watchdog-every-minute": {
        "task": "blockhost_backend.worker.tasks.run_node_watchdog",
        "schedule": 60.0,
    },
    "backup-scheduler-every-minute": {
        "task": "blockhost_backend.worker.tasks.run_backup_scheduler",
        "schedule": 60.0,
    },
    "auto-sleeper-every-5-minutes": {
        "task": "blockhost_backend.worker.tasks.run_auto_sleeper",
        "schedule": 300.0,
    },
    "node-rebalancer-every-15-minutes": {
        "task": "blockhost_backend.worker.tasks.run_node_rebalancer",
        "schedule": 900.0,
    },
    "disk-health-every-hour": {
        "task": "blockhost_backend.worker.tasks.run_disk_health_monitor",
        "schedule": 3600.0,
    },
    "database-backup-every-hour": {
        "task": "blockhost_backend.worker.tasks.run_database_backup_scheduler",
        "schedule": 3600.0,
    },
    "expire-subscriptions-daily": {
        "task": "blockhost_backend.worker.tasks.run_subscription_expiration",
        "schedule": crontab(hour=0, minute=0),
    },
}
