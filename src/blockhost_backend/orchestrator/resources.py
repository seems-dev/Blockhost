from __future__ import annotations

from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import (
    BillingPlan,
    BillingSubscription,
    BillingSubscriptionStatus,
    Server,
    utcnow,
)

# Default limits for a server with NO active subscription (unpaid)
UNPAID_SERVER_LIMITS: dict[str, int] = {
    "ram_mb": 0, 
    "cpu_quota_pct": 0, 
    "storage_mb": 0, 
    "player_limit": 0
}

DEFAULT_BILLING_PLANS: tuple[dict[str, object], ...] = (
    {
        "id": "basic",
        "name": "Basic",
        "ram_mb": 512,
        "cpu_limit": 50,
        "storage_mb": 2048,
        "player_limit": 10,
        "price": "199.00",
        "duration_days": 30,
        "active": True,
    },
    {
        "id": "pro",
        "name": "Pro",
        "ram_mb": 1024,
        "cpu_limit": 100,
        "storage_mb": 10240,
        "player_limit": 25,
        "price": "399.00",
        "duration_days": 30,
        "active": True,
    },
    {
        "id": "ultra",
        "name": "Ultra",
        "ram_mb": 2048,
        "cpu_limit": 200,
        "storage_mb": 20480,
        "player_limit": 50,
        "price": "799.00",
        "duration_days": 30,
        "active": True,
    },
)


def seed_default_billing_plans(db: Session) -> None:
    changed = False
    for plan_data in DEFAULT_BILLING_PLANS:
        plan = db.get(BillingPlan, str(plan_data["id"]))
        if plan is None:
            db.add(BillingPlan(**plan_data))
            changed = True
    if changed:
        db.commit()


def active_subscription_for_server(db: Session, server_id: object) -> BillingSubscription | None:
    now = utcnow()
    settings = get_settings()
    grace_delta = timedelta(days=max(0, settings.subscription_grace_period_days))
    return db.execute(
        select(BillingSubscription)
        .where(
            BillingSubscription.server_id == server_id,
            (
                (BillingSubscription.status == BillingSubscriptionStatus.active) &
                (BillingSubscription.expires_at > now)
            ) | (
                (BillingSubscription.status == BillingSubscriptionStatus.grace_period) &
                (BillingSubscription.expires_at + grace_delta > now)
            )
        )
        .order_by(BillingSubscription.expires_at.desc())
    ).scalars().first()


def get_effective_server_resource_limits(
    *,
    db: Session | None,
    server: Server,
) -> dict[str, int]:
    # 100% rely on the per-server billing subscription
    if db is not None:
        subscription = active_subscription_for_server(db, server.id)
        if subscription and subscription.plan:
            return {
                "ram_mb": subscription.plan.ram_mb,
                "cpu_quota_pct": subscription.plan.cpu_limit,
                "storage_mb": subscription.plan.storage_mb,
                "player_limit": subscription.plan.player_limit,
            }
    
    # Fallback if no DB or no active subscription
    return UNPAID_SERVER_LIMITS