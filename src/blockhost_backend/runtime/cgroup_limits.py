# blockhost_backend/runtime/cgroup_limits.py
import logging
import pathlib
import shutil

logger = logging.getLogger(__name__)

CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")


def apply_limits(pid: int, server_id: str, ram_mb: int, cpu_quota_pct: int) -> None:
    """Apply cgroup v2 memory + CPU limits to a running PID."""
    cg = CGROUP_ROOT / f"blockhost-{server_id}"
    try:
        cg.mkdir(parents=True, exist_ok=True)
        (cg / "memory.max").write_text(str(ram_mb * 1024 * 1024))
        (cg / "memory.swap.max").write_text("0")
        period_us = 100_000
        quota_us = int((cpu_quota_pct / 100) * period_us)
        (cg / "cpu.max").write_text(f"{quota_us} {period_us}")
        (cg / "cgroup.procs").write_text(str(pid))
        logger.info("cgroup limits applied: server=%s ram=%sMB cpu=%s%%", server_id, ram_mb, cpu_quota_pct)
    except PermissionError:
        logger.warning("No cgroup permission for server=%s — limits NOT applied", server_id)
    except Exception as e:
        logger.warning("cgroup apply failed for server=%s: %s", server_id, e)


def remove_limits(server_id: str) -> None:
    cg = CGROUP_ROOT / f"blockhost-{server_id}"
    if cg.exists():
        try:
            shutil.rmtree(cg)
        except Exception as e:
            logger.warning("cgroup cleanup failed for server=%s: %s", server_id, e)