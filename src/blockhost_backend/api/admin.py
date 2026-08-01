from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_admin_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import User, Server, BillingTransaction, BillingSubscription, ServerState, BlockcoinTransaction, BlockcoinReason, Node
from blockhost_backend.database.schema import utcnow

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.get("/stats")
def get_global_stats(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Dashboard overview numbers"""
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_servers = db.scalar(select(func.count(Server.id))) or 0
    running_servers = db.scalar(select(func.count(Server.id)).where(Server.state == ServerState.running)) or 0
    
    total_revenue = db.scalar(
        select(func.sum(BillingTransaction.amount)).where(
            BillingTransaction.status == "paid"
        )
    ) or 0.0
    
    return {
        "total_users": total_users,
        "total_servers": total_servers,
        "running_servers": running_servers,
        "total_revenue": float(total_revenue),
    }

@router.get("/users")
def list_all_users(
    offset: int = 0,
    limit: int = 50,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all registered users"""
    users = db.execute(
        select(User).order_by(desc(User.created_at)).offset(offset).limit(limit)
    ).scalars().all()
    
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "nickname": u.nickname,
            "created_at": u.created_at.isoformat(),
        } for u in users
    ]

@router.get("/servers")
def list_all_servers(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all servers across all users (crucial for monitoring)"""
    servers = db.execute(
        select(Server).order_by(desc(Server.created_at)).limit(100)
    ).scalars().all()
    
    return [
        {
            "id": str(s.id),
            "owner_id": str(s.owner_id),
            "world_name": s.world_name,
            "state": s.state.value,
            "created_at": s.created_at.isoformat(),
        } for s in servers
    ]

@router.get("/transactions")
def list_all_transactions(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all money movements"""
    txs = db.execute(
        select(BillingTransaction).order_by(desc(BillingTransaction.created_at)).limit(100)
    ).scalars().all()
    
    return [
        {
            "id": str(t.id),
            "user_id": str(t.user_id),
            "server_id": str(t.server_id),
            "amount": float(t.amount),
            "status": t.status.value,
            "created_at": t.created_at.isoformat(),
        } for t in txs
    ]

@router.post("/servers/{server_id}/force-stop")
def admin_force_stop_server(
    server_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Kill a rogue server eating RAM"""
    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    orchestrator = get_server_lifecycle_orchestrator()
    
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    try:
        orchestrator.stop_server(server)
        server.state = ServerState.suspended
        db.commit()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/users/{user_id}/ban")
def admin_ban_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Soft delete a user"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.deleted_at = utcnow()
    db.commit()
    return {"status": "banned"}

@router.post("/users/{user_id}/grant-blockcoins")
def admin_grant_blockcoins(
    user_id: str,
    amount: float = Query(...),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Manually add blockcoins to a user"""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    amount_int = int(amount)
    user.blockcoin_balance += amount_int
    
    tx = BlockcoinTransaction(
        user_id=user.id,
        amount=amount_int,
        reason=BlockcoinReason.admin_adjustment,
        meta={"granted_by": str(admin.id)}
    )
    db.add(tx)
    db.commit()
    return {"status": "granted", "new_balance": user.blockcoin_balance}

@router.get("/nodes")
def list_all_nodes(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """See all worker nodes"""
    nodes = db.execute(select(Node).order_by(Node.created_at)).scalars().all()
    return [
        {
            "id": str(n.id),
            "name": n.name,
            "ip_address": n.ip_address,
            "status": n.status.value,
            "total_ram_mb": n.total_ram_mb,
            "used_ram_mb": n.used_ram_mb,
            "cpu_usage_percent": n.cpu_usage_percent,
        } for n in nodes
    ]