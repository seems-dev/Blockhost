from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessMetrics:
    cpu_usage_percent: float | None
    ram_usage_mb: float | None


class ProcessMetricsSampler:
    """Sample CPU/RAM for a child process (Linux /proc; limited support elsewhere)."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._prev_cpu_ticks: int | None = None
        self._prev_wall: float | None = None

    def sample(self) -> ProcessMetrics:
        if os.name == "nt":
            return ProcessMetrics(cpu_usage_percent=None, ram_usage_mb=None)
        try:
            return ProcessMetrics(
                cpu_usage_percent=self._cpu_percent(),
                ram_usage_mb=self._ram_mb(),
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            return ProcessMetrics(cpu_usage_percent=None, ram_usage_mb=None)

    def _ram_mb(self) -> float | None:
        status_path = f"/proc/{self.pid}/status"
        with open(status_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024.0
        return None

    def _cpu_percent(self) -> float | None:
        stat_path = f"/proc/{self.pid}/stat"
        with open(stat_path, encoding="utf-8") as f:
            parts = f.read().split()
        if len(parts) < 15:
            return None

        utime = int(parts[13])
        stime = int(parts[14])
        total = utime + stime
        now = time.monotonic()

        if self._prev_cpu_ticks is None or self._prev_wall is None:
            self._prev_cpu_ticks = total
            self._prev_wall = now
            return None

        delta_ticks = total - self._prev_cpu_ticks
        delta_wall = now - self._prev_wall
        self._prev_cpu_ticks = total
        self._prev_wall = now

        if delta_wall <= 0:
            return None

        # jiffies to percent (assume 100Hz clock)
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        cpu = (delta_ticks / hz) / delta_wall * 100.0
        return max(0.0, min(100.0, cpu))
