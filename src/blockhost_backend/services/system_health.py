from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from blockhost_backend.config.config_manager import get_settings

logger = logging.getLogger(__name__)

_DISK_MONITOR_STARTED = False
_DISK_MONITOR_GUARD = threading.Lock()


class DiskProtectionError(RuntimeError):
    code = "disk_usage_critical"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorldSizeLimitError(RuntimeError):
    code = "world_size_limit_exceeded"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _bytes_to_gb(value: int) -> float:
    return round(value / (1024**3), 2)


def get_disk_usage(path: str | Path = "/") -> dict[str, float]:
    usage = shutil.disk_usage(path)
    used_percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return {
        "total_gb": _bytes_to_gb(usage.total),
        "used_gb": _bytes_to_gb(usage.used),
        "free_gb": _bytes_to_gb(usage.free),
        "used_percent": round(used_percent, 2),
    }


def assert_disk_usage_safe(operation: str) -> dict[str, float]:
    settings = get_settings()
    usage = get_disk_usage("/")
    used_percent = float(usage["used_percent"])

    if used_percent > settings.disk_critical_threshold_percent:
        raise DiskProtectionError(
            f"Disk usage exceeds {settings.disk_critical_threshold_percent:g}%. "
            "Operation blocked to protect VPS stability."
        )
    if used_percent > settings.disk_warning_threshold_percent:
        logger.warning(
            "VPS disk usage above %.0f%% before %s: %.2f%% used",
            settings.disk_warning_threshold_percent,
            operation,
            used_percent,
        )
    return usage


_DIR_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_DIR_SIZE_CACHE_LOCK = threading.Lock()
_DIR_SIZE_CACHE_TTL = 10.0  # seconds


def calculate_directory_size(path: Path) -> int:
    path_str = str(path.resolve())
    now = time.monotonic()
    with _DIR_SIZE_CACHE_LOCK:
        cached = _DIR_SIZE_CACHE.get(path_str)
        if cached:
            cached_at, size = cached
            if now - cached_at <= _DIR_SIZE_CACHE_TTL:
                return size

    total = 0
    if not path.exists():
        return total
    if path.is_file() and not path.is_symlink():
        return path.stat().st_size

    for child in path.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except FileNotFoundError:
            pass

    with _DIR_SIZE_CACHE_LOCK:
        _DIR_SIZE_CACHE[path_str] = (now, total)
    return total


def world_limit_bytes_for_tier(tier: object) -> int:
    settings = get_settings()
    tier_value = getattr(tier, "value", tier)
    if str(tier_value) == "premium":
        limit_gb = settings.premium_world_limit_gb
    else:
        limit_gb = settings.free_world_limit_gb
    return int(limit_gb * (1024**3))


def assert_world_size_within_plan(world_path: Path, tier: object) -> int:
    size = calculate_directory_size(world_path)
    limit = world_limit_bytes_for_tier(tier)
    if size > limit:
        raise WorldSizeLimitError("World size exceeds plan limit.")
    return size


def _disk_monitor_loop() -> None:
    settings = get_settings()
    interval_seconds = max(60, int(settings.disk_health_check_interval_seconds))
    while True:
        try:
            usage = get_disk_usage("/")
            used_percent = float(usage["used_percent"])
            if used_percent > settings.disk_critical_threshold_percent:
                logger.critical("CRITICAL: VPS disk usage above 90%% (%.2f%% used)", used_percent)
            elif used_percent > settings.disk_warning_threshold_percent:
                logger.warning("WARNING: VPS disk usage above 80%% (%.2f%% used)", used_percent)
        except Exception:
            logger.exception("Failed to check VPS disk usage")
        time.sleep(interval_seconds)


def start_disk_health_monitor_once() -> None:
    settings = get_settings()
    if not settings.disk_health_monitor_enabled:
        return

    global _DISK_MONITOR_STARTED
    with _DISK_MONITOR_GUARD:
        if _DISK_MONITOR_STARTED:
            return
        _DISK_MONITOR_STARTED = True
        thread = threading.Thread(target=_disk_monitor_loop, name="disk-health-monitor", daemon=True)
        thread.start()
