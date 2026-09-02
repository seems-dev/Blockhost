"""Background worker orchestration — run in a dedicated process in production."""

from __future__ import annotations

import logging
import signal
import threading

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.billing import start_subscription_expiration_worker_once
from blockhost_backend.services.system_health import start_disk_health_monitor_once
from blockhost_backend.orchestrator.backup import start_backup_scheduler_once

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_started = False
_start_lock = threading.Lock()


def should_run_workers_in_api() -> bool:
    settings = get_settings()
    if settings.background_workers_in_api is not None:
        return settings.background_workers_in_api
    return not settings.production_mode


def start_background_workers() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    from blockhost_backend.api.nodes import start_node_watchdog_once
    from blockhost_backend.services.node_rebalancer import start_node_rebalancer_once

    start_subscription_expiration_worker_once()
    start_disk_health_monitor_once()
    start_node_watchdog_once()
    start_backup_scheduler_once()
    start_node_rebalancer_once()
    logger.info("Background workers started")


def run_background_workers_forever() -> None:
    """Block until SIGTERM/SIGINT (for dedicated worker container/process)."""
    start_background_workers()
    logger.info("BlockHost background worker running — waiting for shutdown signal")

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("Received signal %s — shutting down worker", signum)
        _stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _stop_event.wait()
    logger.info("Background worker stopped")
