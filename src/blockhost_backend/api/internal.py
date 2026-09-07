import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, ServerState
from blockhost_backend.api.servers import _do_start_server

router = APIRouter(prefix="/internal/servers", tags=["Internal"])

security = HTTPBearer()

def verify_internal_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> None:
    settings = get_settings()
    if credentials.credentials != settings.worker_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )

@router.post("/{server_id}/wake", status_code=status.HTTP_202_ACCEPTED)
def wake_server_internal(
    server_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_token),
) -> dict[str, Any]:
    try:
        sid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server ID")

    server = db.get(Server, sid)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.state != ServerState.suspended:
        return {"message": "Server is not suspended", "state": server.state.value}

    _do_start_server(server, db)
    db.commit()

    return {"message": "Server wake-up initiated", "state": server.state.value}
