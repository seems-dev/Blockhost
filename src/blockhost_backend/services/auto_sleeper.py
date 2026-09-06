"""Background worker that sleeps idle servers locally on the node."""

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import select, update

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Server, ServerState, utcnow
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator

logger = logging.getLogger(__name__)

SLEEP_INTERVAL_SECONDS = 300  # 5 minutes
IDLE_TIMEOUT_MINUTES = 15

def _auto_sleeper_cycle() -> None:
    """Find idle servers and suspend them locally (no S3 backup)."""
    settings = get_settings()
    cutoff_time = utcnow() - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    
    with SessionLocal() as db:
        # 1. Find all running servers that haven't been active
        idle_servers = db.execute(
            select(Server).where(
                Server.state == ServerState.running,
                Server.last_activity < cutoff_time
            )
        ).scalars().all()
        
        if not idle_servers:
            return
            
        orchestrator = get_server_lifecycle_orchestrator()
        
        for server in idle_servers:
            sid = str(server.id)
            logger.info("[auto-sleeper] Suspending idle server %s (last active: %s)", sid, server.last_activity)
            
            try:
                # 2. Stop the systemd process locally using the agent
                # The orchestrator routes this to the correct node agent.
                # The agent stops the service but DOES NOT upload to S3.
                orchestrator.stop_server(sid)
                
                # 3. Mark as suspended in the database
                server.state = ServerState.suspended
                if server.node_id:
                    from blockhost_backend.services.node_capacity import refresh_node_allocated_ram
                    refresh_node_allocated_ram(db, server.node_id)
                db.commit()
                
            except Exception as e:
                logger.error("[auto-sleeper] Failed to sleep server %s: %s", sid, e)
                db.rollback()


def _auto_sleeper_loop() -> None:
    """Background daemon loop."""
    logger.info("Auto-Sleeper started (interval=%ds, timeout=%dm)", SLEEP_INTERVAL_SECONDS, IDLE_TIMEOUT_MINUTES)
    while True:
        try:
            time.sleep(SLEEP_INTERVAL_SECONDS)
            _auto_sleeper_cycle()
        except Exception:
            logger.exception("Auto-sleeper cycle failed")


_sleeper_started = False
_start_lock = threading.Lock()


def start_auto_sleeper_once() -> None:
    global _sleeper_started
    with _start_lock:
        if _sleeper_started:
            return
        _sleeper_started = True

    t = threading.Thread(target=_auto_sleeper_loop, daemon=True, name="AutoSleeper")
    t.start()
