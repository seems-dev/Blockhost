from pathlib import Path

content = Path("src/blockhost_backend/api/servers.py").read_text()

helper = """
def _get_server_for_user(server_id: str, user: User, db: Session, required_permission: str | None = None) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if server.owner_id == user.id:
        return server
        
    # Check collaborators
    from blockhost_backend.database.schema import ServerCollaborator
    from sqlalchemy import select
    collab = db.execute(
        select(ServerCollaborator).where(
            ServerCollaborator.server_id == server.id,
            ServerCollaborator.user_id == user.id
        )
    ).scalars().first()
    
    if not collab:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if required_permission and required_permission not in collab.permissions:
        raise HTTPException(status_code=403, detail=f"Missing required permission: {required_permission}")
        
    return server

"""

if "def _get_server_for_user(" not in content:
    idx = content.find("def _guard_disk_for_operation(operation: str) -> None:")
    new_content = content[:idx] + helper + content[idx:]
    Path("src/blockhost_backend/api/servers.py").write_text(new_content)
    print("Added _get_server_for_user to servers.py")
else:
    print("Already added")
