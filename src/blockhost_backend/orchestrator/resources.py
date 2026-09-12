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
        "provider_price_id": "pri_01m2a30sd71pwp2rpqxjqzdbay",
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
        "provider_price_id": "pri_01m2a2z6s8j3h1e6jmpg6z8ay4",
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
        "provider_price_id": "pri_01m2a2w94p4ptb9pkktfsxaymm",
    },
    {
        "id": "mega",
        "name": "Mega",
        "ram_mb": 4096,
        "cpu_limit": 300,
        "storage_mb": 40960,
        "player_limit": 75,
        "price": "1499.00",
        "duration_days": 30,
        "active": True,
        "provider_price_id": "pri_01m28wxe05yva8az0vb6jymxmz",
    },
    {
        "id": "titan",
        "name": "Titan",
        "ram_mb": 8192,
        "cpu_limit": 400,
        "storage_mb": 81920,
        "player_limit": 100,
        "price": "2499.00",
        "duration_days": 30,
        "active": True,
        "provider_price_id": "pri_01m2a32b1e7tayar7w8vej83hg",
    },
)


def seed_default_billing_plans(db: Session) -> None:
    changed = False
    for plan_data in DEFAULT_BILLING_PLANS:
        plan = db.get(BillingPlan, str(plan_data["id"]))
        if plan is None:
            db.add(BillingPlan(**plan_data))
            changed = True
        else:
            # Update existing plans with any new/changed fields
            for key, value in plan_data.items():
                if key != "id" and getattr(plan, key, None) != value:
                    setattr(plan, key, value)
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