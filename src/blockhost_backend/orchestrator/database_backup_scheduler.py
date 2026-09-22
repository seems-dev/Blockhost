import logging
import threading
import time
from datetime import datetime, timedelta
import httpx
from sqlalchemy import select

from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import DatabaseInstance, Node, utcnow
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.s3_storage import list_s3_objects

logger = logging.getLogger(__name__)

_SCHEDULER_STARTED = False
_SCHEDULER_GUARD = threading.Lock()

def dispatch_daily_database_backups() -> None:
    db = SessionLocal()
    settings = get_settings()
    try:
        databases = db.execute(select(DatabaseInstance)).scalars().all()
        for db_instance in databases:
            if not db_instance.node_id:
                continue
                
            # Check last backup in S3
            prefix = f"databases/{db_instance.id}/backups/"
            keys = list_s3_objects(prefix)
            
            needs_backup = True
            now = utcnow()
            
            if keys:
                keys.sort()
                last_key = keys[-1]
                filename = last_key[len(prefix):]
                if filename.endswith(".backup"):
                    ts_str = filename.replace(".backup", "")
                    try:
                        last_dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                        from datetime import timezone
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                        if now - last_dt < timedelta(hours=24):
                            needs_backup = False
                    except ValueError:
                        pass
            
            if needs_backup:
                logger.info("Triggering automated daily backup for database %s", db_instance.id)
                node = db.get(Node, db_instance.node_id)
                if not node:
                    continue
                    
                timestamp = now.strftime("%Y%m%d_%H%M%S")
                s3_key = f"{prefix}{timestamp}.backup"
                agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{db_instance.id}/backup"
                
                payload = {
                    "engine": db_instance.engine.value,
                    "username": db_instance.db_username,
                    "password": db_instance.db_password or "",
                    "database": db_instance.db_name,
                    "s3_key": s3_key
                }
                
                try:
                    with httpx.Client(timeout=300.0) as client:
                        resp = client.post(
                            agent_url,
                            headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                            json=payload
                        )
                        resp.raise_for_status()
                    logger.info("Successfully triggered automated backup for database %s", db_instance.id)
                except Exception as e:
                    logger.error("Failed to trigger automated backup for database %s: %s", db_instance.id, e)
                    
    except Exception as exc:
        logger.error("Error in dispatch_daily_database_backups: %s", exc)
    finally:
        db.close()

def _scheduler_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        dispatch_daily_database_backups()
        # Sleep for an hour before checking again
        stop_event.wait(3600.0)

def run_database_backup_scheduler_forever(*, stop_event: threading.Event | None = None) -> None:
    logger.info("Database Backup scheduler is starting")
    evt = stop_event or threading.Event()
    _scheduler_loop(evt)
    logger.info("Database Backup scheduler stopped")

def start_database_backup_scheduler_once() -> None:
    global _SCHEDULER_STARTED
    with _SCHEDULER_GUARD:
        if _SCHEDULER_STARTED:
            return
        _SCHEDULER_STARTED = True
    
    t = threading.Thread(target=run_database_backup_scheduler_forever, name="db-backup-scheduler", daemon=True)
    t.start()
