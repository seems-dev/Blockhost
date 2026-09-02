from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import ServerCollaboratorCreate, ServerCollaboratorOut
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, ServerCollaborator, User

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.post("/{server_id}/collaborators", response_model=ServerCollaboratorOut)
def invite_collaborator(
    server_id: str,
    payload: ServerCollaboratorCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
        
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the server owner can manage collaborators")
        
    target_user = db.execute(select(User).where(User.email == payload.email)).scalars().first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User with this email not found")
        
    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot invite yourself")
        
    collab = db.execute(
        select(ServerCollaborator).where(
            ServerCollaborator.server_id == server.id,
            ServerCollaborator.user_id == target_user.id
        )
    ).scalars().first()
    
    if collab:
        # Update existing
        collab.permissions = payload.permissions
    else:
        collab = ServerCollaborator(
            server_id=server.id,
            user_id=target_user.id,
            permissions=payload.permissions
        )
        db.add(collab)
        
    db.commit()
    db.refresh(collab)
    
    return ServerCollaboratorOut(
        id=collab.id,
        server_id=collab.server_id,
        user_id=collab.user_id,
        nickname=target_user.nickname,
        email=target_user.email,
        permissions=collab.permissions,
        created_at=collab.created_at,
        updated_at=collab.updated_at
    )


@router.get("/{server_id}/collaborators", response_model=list[ServerCollaboratorOut])
def list_collaborators(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
        
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the server owner can view collaborators")
        
    collabs = db.execute(
        select(ServerCollaborator).where(ServerCollaborator.server_id == server.id)
    ).scalars().all()
    
    results = []
    for c in collabs:
        # We need the user info, we can access c.user since we have relationship configured
        results.append(
            ServerCollaboratorOut(
                id=c.id,
                server_id=c.server_id,
                user_id=c.user_id,
                nickname=c.user.nickname,
                email=c.user.email,
                permissions=c.permissions,
                created_at=c.created_at,
                updated_at=c.updated_at
            )
        )
    return results


@router.delete("/{server_id}/collaborators/{user_id}", status_code=204)
def remove_collaborator(
    server_id: str,
    user_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        server_uuid = uuid.UUID(server_id)
        target_user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
        
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the server owner can manage collaborators")
        
    collab = db.execute(
        select(ServerCollaborator).where(
            ServerCollaborator.server_id == server.id,
            ServerCollaborator.user_id == target_user_uuid
        )
    ).scalars().first()
    
    if collab:
        db.delete(collab)
        db.commit()
    return None
