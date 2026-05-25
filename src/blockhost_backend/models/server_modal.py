from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from blockhost_backend.database.schema import ServerState, VMProvider


class ServerModel(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    world_name: str
    join_code: str
    state: ServerState
    vm_provider: VMProvider
    vm_ipv4: str | None
    vm_port: int
    created_at: datetime
    last_activity: datetime
