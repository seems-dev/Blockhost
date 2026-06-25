from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import BillingPlan, BillingSubscription, BillingTransaction, User, Server
from blockhost_backend.services.billing import (
    BillingError,
    create_upgrade_transaction,
    verify_upgrade_payment,
)
#file_name = billing.py
router = APIRouter(prefix="/api/billing", tags=["billing"])


class UpgradeRequest(BaseModel):
    server_id: uuid.UUID
    target_plan_id: str = Field(min_length=1, max_length=64)


class UpgradeOrderOut(BaseModel):
    transaction_id: uuid.UUID
    provider: str
    provider_order_id: str
    amount: Decimal
    currency: str
    status: str


class VerifyPaymentRequest(BaseModel):
    provider_order_id: str = Field(min_length=1, max_length=128)
    provider_payment_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=16, max_length=256)
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class VerifyPaymentOut(BaseModel):
    transaction_id: uuid.UUID
    subscription_id: uuid.UUID
    status: str
    plan_id: str
    expires_at: object


def _billing_http_error(exc: BillingError) -> HTTPException:
    status = 400
    if exc.code in {"server_not_found", "plan_not_found", "order_not_found"}:
        status = 404
    if exc.code == "subscription_suspended":
        status = 402
    return HTTPException(status_code=status, detail={"error": exc.code, "message": exc.message})


@router.get("/plans", response_model=list[dict])
def list_billing_plans(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    plans = db.execute(
        select(BillingPlan).where(BillingPlan.active == True).order_by(BillingPlan.price.asc())
    ).scalars().all()
    return [
        {
            "id": plan.id,
            "name": plan.name,
            "ram_mb": plan.ram_mb,
            "cpu_limit": plan.cpu_limit,
            "storage_mb": plan.storage_mb,
            "player_limit": plan.player_limit,
            "price": str(plan.price),
            "duration_days": plan.duration_days,
            "active": plan.active,
        }
        for plan in plans
    ]


@router.post("/upgrade", response_model=UpgradeOrderOut)
def create_upgrade_order(
    payload: UpgradeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UpgradeOrderOut:
    try:
        tx, order = create_upgrade_transaction(
            db=db,
            user=user,
            server_id=payload.server_id,
            target_plan_id=payload.target_plan_id,
        )
    except BillingError as exc:
        raise _billing_http_error(exc)
    return UpgradeOrderOut(
        transaction_id=tx.id,
        provider=order.provider,
        provider_order_id=order.provider_order_id,
        amount=order.amount,
        currency=order.currency,
        status=tx.status.value,
    )


@router.post("/verify", response_model=VerifyPaymentOut)
def verify_payment(
    payload: VerifyPaymentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VerifyPaymentOut:
    existing_tx = db.execute(
        select(BillingTransaction).where(
            BillingTransaction.provider_order_id == payload.provider_order_id,
            BillingTransaction.user_id == user.id,
        )
    ).scalars().one_or_none()
    if existing_tx is None:
        raise HTTPException(status_code=404, detail={"error": "order_not_found", "message": "Payment order not found"})
    try:
        tx, sub = verify_upgrade_payment(
            db=db,
            user_id=user.id,
            provider_order_id=payload.provider_order_id,
            provider_payment_id=payload.provider_payment_id,
            signature=payload.signature,
            amount=payload.amount,
            currency=payload.currency,
        )
    except BillingError as exc:
        raise _billing_http_error(exc)
    return VerifyPaymentOut(
        transaction_id=tx.id,
        subscription_id=sub.id,
        status=tx.status.value,
        plan_id=sub.plan_id,
        expires_at=sub.expires_at,
    )


@router.get("/subscriptions/{server_id}", response_model=dict)
def get_server_subscription(
    server_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    server = db.get(Server, server_id)
    if server is None or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    sub = db.execute(
        select(BillingSubscription)
        .where(BillingSubscription.server_id == server_id, BillingSubscription.user_id == user.id)
        .order_by(BillingSubscription.created_at.desc())
    ).scalars().first()
    if sub is None:
        return {"server_id": str(server_id), "subscription": None}
    return {
        "server_id": str(server_id),
        "subscription": {
            "id": str(sub.id),
            "plan_id": sub.plan_id,
            "status": sub.status.value,
            "starts_at": sub.starts_at.isoformat(),
            "expires_at": sub.expires_at.isoformat(),
        },
    }
