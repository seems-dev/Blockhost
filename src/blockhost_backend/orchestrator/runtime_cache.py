"""Invalidate NodeRouter runtime cache after failover or node reassignment."""

from __future__ import annotations

import uuid

from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.orchestrator.node_router import NodeRouter


def invalidate_server_runtime_cache(server_id: str | uuid.UUID) -> None:
    runtime = get_server_lifecycle_orchestrator()._runtime
    if isinstance(runtime, NodeRouter):
        runtime.invalidate_server(str(server_id))


def invalidate_node_runtime_cache(node_id: str | uuid.UUID) -> None:
    runtime = get_server_lifecycle_orchestrator()._runtime
    if isinstance(runtime, NodeRouter):
        runtime.invalidate_node(uuid.UUID(str(node_id)))
