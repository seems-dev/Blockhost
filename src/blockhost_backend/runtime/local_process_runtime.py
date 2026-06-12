# blockhost_backend/runtime/local_process_runtime.py

from __future__ import annotations

import warnings
from blockhost_backend.runtime.systemd_runtime import SystemdRuntime
from blockhost_backend.runtime.interface import (
    RuntimeStartRequest,
    RuntimeStartResult,
    RuntimeStatus,
    RuntimeResourceStats,
    LogEntry,
    LogListener,
)

class LocalProcessRuntime(SystemdRuntime):
    """
    DEPRECATED:
    This runtime is replaced by SystemdRuntime.

    Kept only for backward compatibility.
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "LocalProcessRuntime is deprecated. Use SystemdRuntime instead.",
            DeprecationWarning
        )
        super().__init__()
        pass