from __future__ import annotations

from functools import lru_cache

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.orchestrator.server_lifecycle import ServerLifecycleOrchestrator
from blockhost_backend.runtime.systemd_runtime import SystemdRuntime


@lru_cache(maxsize=1)
def get_server_lifecycle_orchestrator() -> ServerLifecycleOrchestrator:
    settings = get_settings()
    driver = settings.bedrock_runtime_driver.strip().lower()
    if driver != "systemd":
        raise RuntimeError(f"Unsupported Bedrock runtime driver: {settings.bedrock_runtime_driver}. Only 'systemd' is supported.")
    runtime = SystemdRuntime()
    return ServerLifecycleOrchestrator(runtime=runtime)
