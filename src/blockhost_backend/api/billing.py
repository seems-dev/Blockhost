from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

import razorpay
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import (
    BillingPlan,
    BillingSubscription,
    BillingTransaction,
    BillingTransactionStatus,
    Server,
    User,
)
from blockhost_backend.services.billing import (
    BillingError,
    create_upgrade_transaction,
    fulfill_webhook_payment,
    verify_upgrade_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

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
    """
    Fields returned by the Razorpay Checkout SDK success callback:
      - razorpay_order_id   → provider_order_id
      - razorpay_payment_id → provider_payment_id
      - razorpay_signature  → signature
    """
    provider_order_id: str = Field(min_length=1, max_length=128)
    provider_payment_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1, max_length=512)
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class VerifyPaymentOut(BaseModel):
    transaction_id: uuid.UUID
    subscription_id: uuid.UUID
    status: str
    plan_id: str
    expires_at: object


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _billing_http_error(exc: BillingError) -> HTTPException:
    status = 400
    if exc.code in {"server_not_found", "plan_not_found", "order_not_found"}:
        status = 404
    elif exc.code == "unauthorized":
        status = 403
    elif exc.code == "subscription_suspended":
        status = 402
    return HTTPException(status_code=status, detail={"error": exc.code, "message": exc.message})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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
    """
    Create a Razorpay order for the requested plan upgrade.
    Idempotent: if a pending order already exists for the same server+plan,
    it is returned as-is (avoids creating duplicate Razorpay orders).
    """
    # Idempotency: reuse an existing pending order for the same server + plan
    existing_tx = db.execute(
        select(BillingTransaction).where(
            BillingTransaction.server_id == payload.server_id,
            BillingTransaction.user_id == user.id,
            BillingTransaction.target_plan_id == payload.target_plan_id,
            BillingTransaction.status == BillingTransactionStatus.pending,
        )
    ).scalars().first()

    if existing_tx is not None:
        return UpgradeOrderOut(
            transaction_id=existing_tx.id,
            provider=existing_tx.provider,
            provider_order_id=existing_tx.provider_order_id,
            amount=existing_tx.amount,
            currency=existing_tx.currency,
            status=existing_tx.status.value,
        )

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
    """
    Verify the Razorpay payment signature from the Checkout SDK callback
    and activate the subscription.

    The Flutter client should call this immediately after the Razorpay
    Checkout onSuccess callback, passing:
      - provider_order_id   = response['razorpay_order_id']
      - provider_payment_id = response['razorpay_payment_id']
      - signature           = response['razorpay_signature']
    """
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
        .where(
            BillingSubscription.server_id == server_id,
            BillingSubscription.user_id == user.id,
        )
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


# ---------------------------------------------------------------------------
# Razorpay Webhook
# ---------------------------------------------------------------------------

@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Razorpay webhook handler — fallback payment confirmation.

    This fires when Razorpay captures a payment. It acts as a safety net:
    if the client's /verify call failed or was never made, this ensures the
    subscription is still activated.

    Razorpay dashboard webhook URL: POST /api/billing/webhook/razorpay
    Events to subscribe: payment.captured
    """
    settings = get_settings()
    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    # Step 1: Verify the HMAC signature — confirms this came from Razorpay
    try:
        client.utility.verify_webhook_signature(
            body.decode("utf-8"),
            signature,
            settings.razorpay_webhook_secret,
        )
    except Exception:
        logger.warning("Razorpay webhook: invalid signature — possible spoofing attempt")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = json.loads(body)
    event = payload.get("event")
    logger.info("Razorpay webhook received event: %s", event)

    if event == "payment.captured":
        # Correct Razorpay payload structure: payload > payment > entity
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        provider_order_id = payment_entity.get("order_id")
        provider_payment_id = payment_entity.get("id")

        if not provider_order_id or not provider_payment_id:
            logger.error(
                "Razorpay webhook: payment.captured missing order_id or payment_id. entity=%s",
                payment_entity,
            )
            # Return 200 anyway — returning 4xx would cause Razorpay to retry indefinitely
            return {"status": "ok"}

        logger.info(
            "Razorpay webhook: fulfilling payment order=%s payment=%s",
            provider_order_id, provider_payment_id,
        )
        try:
            result = fulfill_webhook_payment(
                db=db,
                provider_order_id=provider_order_id,
                provider_payment_id=provider_payment_id,
            )
            if result is None:
                logger.info(
                    "Razorpay webhook: order %s not found or already processed — skipping",
                    provider_order_id,
                )
            else:
                tx, sub = result
                logger.info(
                    "Razorpay webhook: subscription %s activated for server %s via order %s",
                    sub.id, sub.server_id, provider_order_id,
                )
        except BillingError as e:
            logger.warning(
                "Razorpay webhook BillingError for order %s: [%s] %s",
                provider_order_id, e.code, e.message,
            )
        except Exception:
            logger.exception("Razorpay webhook unexpected error for order %s", provider_order_id)

    return {"status": "ok"}