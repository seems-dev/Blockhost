from __future__ import annotations

from fastapi import APIRouter, Depends

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.schema import User
from blockhost_backend.services.system_health import get_disk_usage

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/system/disk")
def get_system_disk_usage(_user: User = Depends(get_current_user)) -> dict[str, float]:
    return get_disk_usage("/")
