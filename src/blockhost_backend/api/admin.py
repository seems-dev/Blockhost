from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc, and_, or_
from sqlalchemy.orm import Session, joinedload

from blockhost_backend.api.deps import get_admin_user
from blockhost_backend.core.security import create_access_token
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import (
    User, Server, BillingTransaction, BillingSubscription,
    ServerState, BlockcoinTransaction, BlockcoinReason, Node, NodeState,
    BillingTransactionStatus, BillingAuditLog,
)
from blockhost_backend.database.schema import utcnow

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
def get_global_stats(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Enhanced dashboard overview numbers including MRR, RAM, and user splits."""
    total_users = db.scalar(select(func.count(User.id)).where(User.deleted_at == None)) or 0
    suspended_users = db.scalar(select(func.count(User.id)).where(User.deleted_at != None)) or 0
    total_servers = db.scalar(select(func.count(Server.id))) or 0
    running_servers = db.scalar(select(func.count(Server.id)).where(Server.state == ServerState.running)) or 0
    suspended_servers = db.scalar(select(func.count(Server.id)).where(Server.state == ServerState.suspended)) or 0

    total_revenue = db.scalar(
        select(func.sum(BillingTransaction.amount)).where(
            BillingTransaction.status == BillingTransactionStatus.paid
        )
    ) or 0.0

    # MRR: sum of paid transactions in the current month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mrr = db.scalar(
        select(func.sum(BillingTransaction.amount)).where(
            and_(
                BillingTransaction.status == BillingTransactionStatus.paid,
                BillingTransaction.created_at >= month_start,
            )
        )
    ) or 0.0

    # Node stats
    nodes = db.execute(select(Node)).scalars().all()
    total_nodes = len(nodes)
    offline_nodes = sum(1 for n in nodes if n.status in (NodeState.offline,))
    total_ram_mb = sum(n.total_ram_mb for n in nodes)
    active_ram_mb = sum(n.used_ram_mb for n in nodes)

    # Approximate suspended RAM = servers in suspended state * their plan RAM
    # We use a simple heuristic from server count for now
    suspended_ram_mb = max(0, int(active_ram_mb * 0.15))  # rough estimate

    # Recent failed transactions as alerts
    failed_txs = db.execute(
        select(BillingTransaction)
        .where(BillingTransaction.status == BillingTransactionStatus.failed)
        .order_by(desc(BillingTransaction.created_at))
        .limit(5)
    ).scalars().all()

    alerts = []
    for tx in failed_txs:
        user = db.get(User, tx.user_id)
        alerts.append({
            "type": "payment_failed",
            "message": f"Payment failed for {user.email if user else tx.user_id} — ${tx.amount}",
            "severity": "error",
            "created_at": tx.created_at.isoformat(),
        })

    for node in nodes:
        ram_pct = (node.used_ram_mb / max(1, node.total_ram_mb)) * 100
        if ram_pct > 85:
            alerts.append({
                "type": "node_ram_high",
                "message": f"Node {node.name} RAM at {ram_pct:.0f}% ({node.used_ram_mb}MB / {node.total_ram_mb}MB)",
                "severity": "warning",
                "created_at": utcnow().isoformat(),
            })
        if node.status == NodeState.offline:
            alerts.append({
                "type": "node_offline",
                "message": f"Node {node.name} ({node.ip_address}) is OFFLINE",
                "severity": "error",
                "created_at": node.status_updated_at.isoformat() if node.status_updated_at else utcnow().isoformat(),
            })

    # Sort alerts by severity then time
    alerts = sorted(alerts, key=lambda a: (0 if a["severity"] == "error" else 1, a.get("created_at", "")), reverse=False)[:20]

    return {
        "total_users": total_users,
        "suspended_users": suspended_users,
        "total_servers": total_servers,
        "running_servers": running_servers,
        "suspended_servers": suspended_servers,
        "total_revenue": float(total_revenue),
        "mrr": float(mrr),
        "total_nodes": total_nodes,
        "offline_nodes": offline_nodes,
        "total_ram_mb": total_ram_mb,
        "active_ram_mb": active_ram_mb,
        "suspended_ram_mb": suspended_ram_mb,
        "free_ram_mb": max(0, total_ram_mb - active_ram_mb - suspended_ram_mb),
        "alerts": alerts,
    }


@router.get("/users")
def list_all_users(
    offset: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all registered users with enriched data."""
    query = select(User).order_by(desc(User.created_at)).offset(offset).limit(limit)

    if search:
        query = select(User).where(
            or_(
                User.email.ilike(f"%{search}%"),
                User.nickname.ilike(f"%{search}%"),
            )
        ).order_by(desc(User.created_at)).offset(offset).limit(limit)

    users = db.execute(query).scalars().all()

    result = []
    for u in users:
        server_count = db.scalar(select(func.count(Server.id)).where(Server.owner_id == u.id)) or 0
        result.append({
            "id": str(u.id),
            "email": u.email,
            "nickname": u.nickname,
            "created_at": u.created_at.isoformat(),
            "is_banned": u.deleted_at is not None,
            "is_admin": u.is_admin,
            "server_count": server_count,
            "blockcoin_balance": u.blockcoin_balance,
        })
    return result


@router.get("/servers")
def list_all_servers(
    search: Optional[str] = None,
    state: Optional[str] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all servers across all users with owner + node enrichment."""
    query = select(Server).options(
        joinedload(Server.owner),
        joinedload(Server.node),
    ).order_by(desc(Server.created_at)).limit(200)

    servers = db.execute(query).unique().scalars().all()

    result = []
    for s in servers:
        owner_email = s.owner.email if s.owner else str(s.owner_id)
        node_name = s.node.name if s.node else None
        node_ip = s.node.ip_address if s.node else None

        row = {
            "id": str(s.id),
            "owner_id": str(s.owner_id),
            "owner_email": owner_email,
            "world_name": s.world_name,
            "state": s.state.value,
            "flavor": s.flavor.value if s.flavor else "bedrock",
            "node_name": node_name,
            "node_ip": node_ip,
            "port": s.proxy_port or s.vm_port,
            "players_online": s.players_online,
            "last_activity": s.last_activity.isoformat() if s.last_activity else None,
            "created_at": s.created_at.isoformat(),
        }

        if search:
            needle = search.lower()
            if not (
                needle in row["world_name"].lower()
                or needle in row["owner_email"].lower()
                or (node_name and needle in node_name.lower())
            ):
                continue
        if state and row["state"] != state:
            continue

        result.append(row)

    return result


@router.get("/transactions")
def list_all_transactions(
    status_filter: Optional[str] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all money movements with user email enrichment."""
    query = select(BillingTransaction).order_by(desc(BillingTransaction.created_at)).limit(200)
    txs = db.execute(query).scalars().all()

    result = []
    for t in txs:
        if status_filter and t.status.value != status_filter:
            continue
        user = db.get(User, t.user_id)
        result.append({
            "id": str(t.id),
            "user_id": str(t.user_id),
            "user_email": user.email if user else str(t.user_id),
            "server_id": str(t.server_id),
            "amount": float(t.amount),
            "currency": t.currency,
            "provider": t.provider,
            "provider_order_id": t.provider_order_id,
            "status": t.status.value,
            "target_plan_id": t.target_plan_id,
            "created_at": t.created_at.isoformat(),
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })
    return result


@router.get("/nodes")
def list_all_nodes(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all worker nodes with server count."""
    nodes = db.execute(select(Node).order_by(Node.created_at)).scalars().all()

    result = []
    for n in nodes:
        server_count = db.scalar(select(func.count(Server.id)).where(Server.node_id == n.id)) or 0
        running_count = db.scalar(
            select(func.count(Server.id)).where(
                and_(Server.node_id == n.id, Server.state == ServerState.running)
            )
        ) or 0
        result.append({
            "id": str(n.id),
            "name": n.name,
            "ip_address": n.ip_address,
            "agent_port": n.agent_port,
            "status": n.status.value,
            "provider": n.provider or "unknown",
            "provider_instance_id": n.provider_instance_id,
            "total_ram_mb": n.total_ram_mb,
            "used_ram_mb": n.used_ram_mb,
            "cpu_cores": n.cpu_cores,
            "cpu_usage_percent": n.cpu_usage_percent,
            "server_count": server_count,
            "running_count": running_count,
            "last_heartbeat": n.last_heartbeat.isoformat() if n.last_heartbeat else None,
            "approved": n.approved,
        })
    return result


@router.get("/audit-logs")
def list_audit_logs(
    limit: int = 100,
    search: Optional[str] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get billing audit logs enriched with user emails."""
    logs = db.execute(
        select(BillingAuditLog)
        .order_by(desc(BillingAuditLog.created_at))
        .limit(limit)
    ).scalars().all()

    result = []
    for log in logs:
        user = db.get(User, log.user_id)
        result.append({
            "id": str(log.id),
            "user_id": str(log.user_id),
            "user_email": user.email if user else str(log.user_id),
            "server_id": str(log.server_id),
            "action": log.action,
            "details": log.details,
            "created_at": log.created_at.isoformat(),
        })
    return result


# ─── Server Actions ───────────────────────────────────────────────────────────

@router.post("/servers/{server_id}/force-stop")
def admin_force_stop_server(
    server_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Kill a rogue server eating RAM."""
    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    orchestrator = get_server_lifecycle_orchestrator()

    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        orchestrator.stop_server(server=server)
        server.state = ServerState.suspended
        db.commit()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{server_id}/force-start")
def admin_force_start_server(
    server_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Force-start a suspended server."""
    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    orchestrator = get_server_lifecycle_orchestrator()

    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        orchestrator.start_server(server=server)
        server.state = ServerState.running
        db.commit()
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── User Actions ─────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/ban")
def admin_ban_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Soft-delete (ban) a user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.deleted_at = utcnow()
    db.commit()
    return {"status": "banned"}


@router.post("/users/{user_id}/unban")
def admin_unban_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Restore a banned user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.deleted_at = None
    db.commit()
    return {"status": "unbanned"}


@router.post("/users/{user_id}/grant-blockcoins")
def admin_grant_blockcoins(
    user_id: str,
    amount: float = Query(...),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Manually add blockcoins to a user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    amount_int = int(amount)
    user.blockcoin_balance += amount_int

    tx = BlockcoinTransaction(
        user_id=user.id,
        amount=amount_int,
        reason=BlockcoinReason.admin_adjustment,
        meta={"granted_by": str(admin.id)},
    )
    db.add(tx)
    db.commit()
    return {"status": "granted", "new_balance": user.blockcoin_balance}


@router.post("/users/{user_id}/impersonate")
def admin_impersonate_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Issue a short-lived JWT scoped to another user for debugging."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 15-minute impersonation token
    try:
        token = create_access_token(
            subject=str(user.id),
            expires_seconds=900,  # 15 minutes
        )
    except Exception:
        raise HTTPException(status_code=501, detail="Impersonation token creation not supported by current auth config")

    return {
        "access_token": token,
        "user_email": user.email,
        "expires_in": 900,
    }


# ─── Node Actions ─────────────────────────────────────────────────────────────

@router.post("/nodes/{node_id}/evacuate")
def admin_evacuate_node(
    node_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Trigger the rebalancer to drain all servers off this node."""
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.status = NodeState.draining
    db.commit()
    return {"status": "draining", "node_id": node_id}
