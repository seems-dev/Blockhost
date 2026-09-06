import json
import logging
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_admin_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal, get_db
from blockhost_backend.database.schema import Node, NodeState, Server, User, utcnow
from blockhost_backend.services.node_auth import (
    generate_agent_token,
    hash_agent_token,
    verify_node_agent_token,
)
from blockhost_backend.services.node_capacity import (
    HEARTBEAT_STALE_SECONDS,
    is_node_heartbeat_fresh,
    mark_stale_nodes_offline,
    refresh_node_allocated_ram,
    suspend_running_servers_on_node,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nodes", tags=["nodes"])

_watchdog_started = False


class RegisterNodeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ip_address: str | None = Field(default=None, max_length=64)
    agent_port: int = Field(default=9000, ge=1, le=65535)


def _stale_node_watchdog_loop() -> None:
    while True:
        try:
            time.sleep(10)
            with SessionLocal() as db:
                affected = mark_stale_nodes_offline(db)
                if affected:
                    logger.warning("Marked %d stale node(s) offline: %s", len(affected), affected)
        except Exception:
            logger.exception("Stale node watchdog error")


def start_node_watchdog_once() -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(target=_stale_node_watchdog_loop, daemon=True, name="node-watchdog").start()


def _resolve_node_ip(register_data: dict, websocket: WebSocket) -> str:
    reported_ip = str(register_data.get("ip_address") or "").strip()
    if reported_ip:
        return reported_ip
    if websocket.client and websocket.client.host:
        return websocket.client.host
    return "127.0.0.1"


@router.websocket("/ws")
async def node_agent_websocket(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return

    await websocket.accept()
    node_name = "unknown"
    node_id = None
    settings = get_settings()

    try:
        init_data = await websocket.receive_text()
        msg = json.loads(init_data)

        if msg.get("type") != "register":
            await websocket.close(code=4001, reason="Must register first")
            return

        register_data = msg.get("data") or {}
        node_name = register_data.get("name")
        if not node_name:
            await websocket.close(code=4001, reason="Missing node name")
            return

        node_ip = _resolve_node_ip(register_data, websocket)
        try:
            node_port = int(register_data.get("agent_port") or 9000)
        except (TypeError, ValueError):
            await websocket.close(code=4001, reason="Invalid agent port")
            return
        if node_port < 1 or node_port > 65535:
            await websocket.close(code=4001, reason="Invalid agent port")
            return

        with SessionLocal() as db:
            node_obj = db.execute(select(Node).where(Node.name == node_name)).scalars().first()
            if not node_obj:
                if not settings.allow_auto_node_registration:
                    await websocket.close(code=4003, reason="Node not registered by admin")
                    return
                node_obj = Node(
                    name=node_name,
                    ip_address=node_ip,
                    agent_port=node_port,
                    approved=True,
                )
                db.add(node_obj)
                db.commit()
                db.refresh(node_obj)
            else:
                if node_obj.ip_address != node_ip:
                    node_obj.ip_address = node_ip
                    # Sync new IP to all servers on this node
                    from sqlalchemy import update
                    from blockhost_backend.database.schema import Server
                    db.execute(update(Server).where(Server.node_id == node_obj.id).values(vm_ipv4=node_ip))
                node_obj.agent_port = node_port

            if not verify_node_agent_token(node=node_obj, token=token):
                logger.warning(
                    "Node '%s' rejected: invalid token (has_agent_token=%s, production_mode=%s)",
                    node_name,
                    bool(node_obj.agent_token_hash),
                    settings.production_mode,
                )
                await websocket.close(code=1008, reason="Invalid token")
                return

            if not node_obj.approved:
                logger.warning("Node '%s' rejected: not approved", node_name)
                await websocket.close(code=4003, reason="Node not approved")
                return

            if node_obj.status == NodeState.offline:
                logger.warning("Node '%s' reconnecting from offline state. Initiating split-brain recovery.", node_name)
                import httpx
                agent_port = data.get("agent_port", 9000)
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(
                            f"http://{node_ip}:{agent_port}/agent/halt-all",
                            headers={"Authorization": f"Bearer {AGENT_TOKEN}"}
                        )
                        resp.raise_for_status()
                        logger.info("Split-brain recovery successful for node '%s'. Orphaned processes halted.", node_name)
                except Exception as e:
                    logger.error("Failed to halt orphaned processes on node '%s': %s", node_name, e)
                    await websocket.close(code=4003, reason="Split-brain recovery failed")
                    return
                
                # Update DB state for all servers on this node to suspended
                from blockhost_backend.database.schema import ServerState
                db.execute(
                    update(Server)
                    .where(Server.node_id == node_obj.id, Server.state == ServerState.running)
                    .values(state=ServerState.suspended)
                )

            node_obj.status = NodeState.online
            node_obj.status_updated_at = utcnow()
            node_obj.last_heartbeat = utcnow()
            db.commit()
            node_id = node_obj.id
            refresh_node_allocated_ram(db, node_id)
            db.commit()

        logger.info("Node '%s' connected (%s)", node_name, node_id)

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg["type"] != "heartbeat":
                continue

            stats = msg["data"]
            with SessionLocal() as db:
                node_obj = db.get(Node, node_id)
                if not node_obj:
                    continue

                if node_obj.status == NodeState.offline:
                    await websocket.close(code=4002, reason="Node marked offline")
                    return

                node_obj.total_ram_mb = stats["total_ram_mb"]
                node_obj.cpu_usage_percent = stats["cpu_usage_percent"]
                node_obj.cpu_cores = stats["cpu_cores"]
                node_obj.last_heartbeat = utcnow()
                refresh_node_allocated_ram(db, node_obj.id)
                
                # Update last_activity and players_online for active servers
                running_servers = stats.get("running_servers", {})
                
                # First reset players_online to 0 for all servers on this node
                db.execute(
                    update(Server)
                    .where(Server.node_id == node_obj.id)
                    .values(players_online=0)
                )

                if running_servers:
                    for sid, count in running_servers.items():
                        try:
                            server_uuid = uuid.UUID(sid)
                            # Update players_online for all, but last_activity only if count > 0
                            if count > 0:
                                db.execute(
                                    update(Server)
                                    .where(Server.id == server_uuid)
                                    .values(players_online=count, last_activity=utcnow())
                                )
                            else:
                                db.execute(
                                    update(Server)
                                    .where(Server.id == server_uuid)
                                    .values(players_online=0)
                                )
                        except ValueError:
                            pass
                
                db.commit()

    except WebSocketDisconnect:
        logger.info("Node '%s' disconnected.", node_name)
    finally:
        with SessionLocal() as db:
            node_obj = db.execute(select(Node).where(Node.name == node_name)).scalars().first()
            if node_obj:
                node_obj.status = NodeState.offline
                suspend_running_servers_on_node(db, node_obj.id)
                db.commit()


@router.post("/register")
def register_node(
    payload: RegisterNodeRequest,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Pre-register a worker node and receive a one-time agent token."""
    existing = db.execute(select(Node).where(Node.name == payload.name)).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Node name already exists")

    plain_token = generate_agent_token()
    node = Node(
        name=payload.name,
        ip_address=payload.ip_address or "0.0.0.0",
        agent_port=payload.agent_port,
        approved=True,
        agent_token_hash=hash_agent_token(plain_token),
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return {
        "id": node.id,
        "name": node.name,
        "ip_address": node.ip_address,
        "agent_port": node.agent_port,
        "approved": node.approved,
        "agent_token": plain_token,
        "message": "Save agent_token now — it will not be shown again.",
    }


@router.post("/{node_id}/approve")
def approve_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.approved = True
    db.commit()
    return {"status": "approved", "id": node.id}


@router.post("/{node_id}/rotate-token")
def rotate_node_token(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    plain_token = generate_agent_token()
    node.agent_token_hash = hash_agent_token(plain_token)
    node.approved = True
    db.commit()
    return {
        "id": node.id,
        "name": node.name,
        "agent_token": plain_token,
        "message": "Save agent_token now — it will not be shown again.",
    }


@router.get("")
def list_nodes(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    nodes = db.execute(select(Node)).scalars().all()
    return [
        {
            "id": n.id,
            "name": n.name,
            "ip": n.ip_address,
            "agent_port": n.agent_port,
            "status": n.status,
            "approved": n.approved,
            "has_agent_token": bool(n.agent_token_hash),
            "ram_mb": n.total_ram_mb,
            "used_ram_mb": n.used_ram_mb,
            "heartbeat_fresh": is_node_heartbeat_fresh(n),
        }
        for n in nodes
    ]


@router.get("/{node_id}")
def get_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return {
        "id": node.id,
        "name": node.name,
        "ip_address": node.ip_address,
        "agent_port": node.agent_port,
        "status": node.status,
        "approved": node.approved,
        "has_agent_token": bool(node.agent_token_hash),
        "total_ram_mb": node.total_ram_mb,
        "used_ram_mb": node.used_ram_mb,
        "cpu_usage_percent": node.cpu_usage_percent,
        "server_count": len(node.servers),
        "last_heartbeat": node.last_heartbeat,
        "heartbeat_fresh": is_node_heartbeat_fresh(node),
        "heartbeat_stale_seconds": HEARTBEAT_STALE_SECONDS,
    }


@router.post("/{node_id}/drain")
def drain_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Mark a node as draining — no new servers will be placed on it."""
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.status == NodeState.offline:
        raise HTTPException(status_code=400, detail="Cannot drain an offline node")
    node.status = NodeState.draining
    db.commit()
    return {"status": "draining", "id": node.id, "server_count": len(node.servers)}


@router.post("/{node_id}/undrain")
def undrain_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Return a draining node to online status (requires fresh heartbeat)."""
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.status != NodeState.draining:
        raise HTTPException(status_code=400, detail="Node is not draining")
    if not is_node_heartbeat_fresh(node):
        raise HTTPException(status_code=400, detail="Node heartbeat is stale; agent must reconnect first")
    node.status = NodeState.online
    db.commit()
    return {"status": "online", "id": node.id}


@router.delete("/{node_id}")
def delete_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    node = db.get(Node, uuid.UUID(node_id))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.servers:
        raise HTTPException(status_code=400, detail="Cannot delete node with assigned servers. Drain it first.")
    db.delete(node)
    db.commit()
    return {"status": "deleted"}
