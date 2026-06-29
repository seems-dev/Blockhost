from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import razorpay
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import (
    BillingAuditLog,
    BillingPlan,
    BillingSubscription,
    BillingSubscriptionStatus,
    BillingTransaction,
    BillingTransactionStatus,
    Server,
    ServerState,
    User,
    utcnow,
)
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.services.api_cache import get_api_cache

logger = logging.getLogger(__name__)

_EXPIRATION_STARTED = False
_EXPIRATION_GUARD = threading.Lock()


class BillingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProviderOrder:
    provider: str
    provider_order_id: str
    amount: Decimal
    currency: str


def create_provider_order(*, amount: Decimal, currency: str) -> ProviderOrder:
    """Create a Razorpay order and return its ID + metadata."""
    settings = get_settings()
    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    # Razorpay expects amount in paise (₹399 → 39900)
    amount_paise = int(amount * 100)

    try:
        order_data = client.order.create(data={
            "amount": amount_paise,
            "currency": currency.upper(),
            "payment_capture": 1,  # Auto-capture on payment
        })
        return ProviderOrder(
            provider="razorpay",
            provider_order_id=order_data["id"],  # e.g. order_P8i3jXYZ...
            amount=amount,
            currency=currency.upper(),
        )
    except razorpay.errors.RazorpayError as e:
        raise BillingError("razorpay_order_failed", str(e))


def verify_provider_signature(
    *,
    provider_order_id: str,
    provider_payment_id: str,
    amount: Decimal,
    currency: str,
    signature: str,
) -> bool:
    """
    Verify the Razorpay payment signature returned by the Checkout SDK.
    Uses the Razorpay SDK's built-in HMAC-SHA256 verification.
    """
    settings = get_settings()
    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": provider_order_id,
            "razorpay_payment_id": provider_payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def invalidate_billing_caches(*, user_id: uuid.UUID, server_id: uuid.UUID) -> None:
    cache = get_api_cache()
    cache.delete(f"servers:list:{user_id}")
    cache.delete(f"servers:detail:{user_id}:{server_id}")
    cache.delete(f"servers:config:{user_id}:{server_id}")
    cache.delete(f"servers:stats:{server_id}")
    cache.delete_prefix(f"billing:{user_id}")
    cache.delete_prefix(f"subscription:{server_id}")


def _apply_plan_limits_to_server(server: Server, plan: BillingPlan) -> None:
    cfg = dict(server.mc_config or {})
    cfg["billing_plan_id"] = plan.id
    cfg["resource_limits"] = {
        "ram_mb": plan.ram_mb,
        "cpu_quota_pct": plan.cpu_limit,
        "storage_mb": plan.storage_mb,
        "player_limit": plan.player_limit,
    }
    server.mc_config = cfg


def create_upgrade_transaction(
    *,
    db: Session,
    user: User,
    server_id: uuid.UUID,
    target_plan_id: str,
    currency: str = "INR",
) -> tuple[BillingTransaction, ProviderOrder]:
    server = db.get(Server, server_id)
    if server is None or server.owner_id != user.id:
        raise BillingError("server_not_found", "Server not found")
    plan = db.get(BillingPlan, target_plan_id)
    if plan is None or not plan.active:
        raise BillingError("plan_not_found", "Plan not found")

    order = create_provider_order(amount=plan.price, currency=currency)
    tx = BillingTransaction(
        user_id=user.id,
        server_id=server.id,
        provider=order.provider,
        provider_order_id=order.provider_order_id,
        amount=order.amount,
        currency=order.currency,
        status=BillingTransactionStatus.pending,
        target_plan_id=plan.id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx, order


def _activate_subscription(
    *,
    db: Session,
    tx: BillingTransaction,
    provider_payment_id: str,
) -> tuple[BillingTransaction, BillingSubscription]:
    """
    Internal: assumes payment authenticity is already verified by the caller.
    Creates the subscription record and marks the transaction as paid.
    Safe to call from both the /verify endpoint and the Razorpay webhook handler.
    """
    # Already paid — idempotent early return
    if tx.status == BillingTransactionStatus.paid:
        subscription = db.get(BillingSubscription, tx.subscription_id) if tx.subscription_id else None
        if subscription is None:
            raise BillingError("transaction_inconsistent", "Paid transaction is missing its subscription record")
        return tx, subscription

    if tx.status != BillingTransactionStatus.pending:
        raise BillingError("transaction_not_pending", "Transaction is not in a pending state")

    try:
        server = db.get(Server, tx.server_id)
        plan = db.get(BillingPlan, tx.target_plan_id)
        if server is None or plan is None or not plan.active:
            raise BillingError("upgrade_target_missing", "Upgrade target server or plan no longer exists")

        now = utcnow()

        # Cancel any existing active/grace-period subscriptions for this server
        existing = db.execute(
            select(BillingSubscription).where(
                BillingSubscription.server_id == tx.server_id,
                BillingSubscription.status.in_([
                    BillingSubscriptionStatus.active,
                    BillingSubscriptionStatus.grace_period,
                ]),
            )
        ).scalars().all()
        for sub in existing:
            sub.status = BillingSubscriptionStatus.cancelled
            sub.updated_at = now
            db.add(sub)

        subscription = BillingSubscription(
            user_id=tx.user_id,
            server_id=tx.server_id,
            plan_id=plan.id,
            starts_at=now,
            expires_at=now + timedelta(days=plan.duration_days),
            status=BillingSubscriptionStatus.active,
        )
        db.add(subscription)
        db.flush()

        tx.status = BillingTransactionStatus.paid
        tx.provider_payment_id = provider_payment_id
        tx.subscription_id = subscription.id
        tx.completed_at = now
        db.add(tx)

        _apply_plan_limits_to_server(server, plan)
        db.add(server)
        db.add(BillingAuditLog(
            user_id=tx.user_id,
            server_id=tx.server_id,
            transaction_id=tx.id,
            action="subscription_upgraded",
            details={
                "plan_id": plan.id,
                "provider": tx.provider,
                "provider_order_id": tx.provider_order_id,
                "provider_payment_id": provider_payment_id,
            },
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BillingError("payment_id_reused", "This payment ID has already been processed")
    except BillingError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    invalidate_billing_caches(user_id=tx.user_id, server_id=tx.server_id)
    db.refresh(tx)
    db.refresh(subscription)
    return tx, subscription


def verify_upgrade_payment(
    *,
    db: Session,
    user_id: uuid.UUID,
    provider_order_id: str,
    provider_payment_id: str,
    signature: str,
    amount: Decimal | None = None,
    currency: str | None = None,
) -> tuple[BillingTransaction, BillingSubscription]:
    """
    Client-side payment verification flow (called from POST /api/billing/verify).

    1. Loads the pending transaction.
    2. Verifies the Razorpay HMAC signature from the Checkout SDK callback.
    3. Creates the subscription and marks the transaction paid.
    """
    tx = db.execute(
        select(BillingTransaction)
        .where(BillingTransaction.provider_order_id == provider_order_id)
        .with_for_update()
    ).scalars().one_or_none()

    if tx is None:
        raise BillingError("order_not_found", "Payment order not found")
    if tx.user_id != user_id:
        raise BillingError("unauthorized", "You do not own this transaction")

    # If already paid (e.g. webhook beat us here), return idempotently
    if tx.status == BillingTransactionStatus.paid:
        return _activate_subscription(db=db, tx=tx, provider_payment_id=provider_payment_id)

    # Verify the Razorpay payment signature
    if not verify_provider_signature(
        provider_order_id=provider_order_id,
        provider_payment_id=provider_payment_id,
        amount=tx.amount,
        currency=tx.currency,
        signature=signature,
    ):
        raise BillingError("invalid_signature", "Payment signature is invalid")

    return _activate_subscription(db=db, tx=tx, provider_payment_id=provider_payment_id)


def fulfill_webhook_payment(
    *,
    db: Session,
    provider_order_id: str,
    provider_payment_id: str,
) -> tuple[BillingTransaction, BillingSubscription] | None:
    """
    Webhook-triggered fulfillment (called from POST /api/billing/webhook/razorpay).

    The caller (webhook handler) has already verified the Razorpay webhook HMAC signature,
    so we trust the payment data and skip the per-payment signature check here.

    Returns None if the transaction is not found or already processed (safe to ignore).
    """
    tx = db.execute(
        select(BillingTransaction)
        .where(
            BillingTransaction.provider_order_id == provider_order_id,
            BillingTransaction.status == BillingTransactionStatus.pending,
        )
        .with_for_update()
    ).scalars().one_or_none()

    if tx is None:
        # Transaction not found or already fulfilled — not an error for the webhook
        return None

    return _activate_subscription(db=db, tx=tx, provider_payment_id=provider_payment_id)


def ensure_server_not_billing_suspended(*, db: Session, server: Server) -> None:
    suspended = db.execute(
        select(BillingSubscription).where(
            BillingSubscription.server_id == server.id,
            BillingSubscription.status == BillingSubscriptionStatus.suspended,
        )
    ).scalars().first()
    if suspended is not None:
        raise BillingError("subscription_suspended", "Subscription is suspended. Renew before starting this server.")


def process_subscription_expirations(db: Session) -> dict[str, int]:
    settings = get_settings()
    now = utcnow()
    grace_delta = timedelta(days=max(0, settings.subscription_grace_period_days))
    moved_to_grace = 0
    suspended_count = 0
    to_invalidate: set[tuple[uuid.UUID, uuid.UUID]] = set()

    stmt_active = select(BillingSubscription).where(
        BillingSubscription.status == BillingSubscriptionStatus.active,
        BillingSubscription.expires_at <= now,
    )
    bind = db.get_bind()
    if bind and bind.dialect.name == "postgresql":
        stmt_active = stmt_active.with_for_update(skip_locked=True)
    else:
        stmt_active = stmt_active.with_for_update()

    expired_active = db.execute(stmt_active).scalars().all()

    for sub in expired_active:
        sub.status = BillingSubscriptionStatus.grace_period
        sub.updated_at = now
        db.add(sub)
        moved_to_grace += 1
        to_invalidate.add((sub.user_id, sub.server_id))

    stmt_grace = select(BillingSubscription).where(
        BillingSubscription.status == BillingSubscriptionStatus.grace_period,
        BillingSubscription.expires_at + grace_delta <= now,
    )
    if bind and bind.dialect.name == "postgresql":
        stmt_grace = stmt_grace.with_for_update(skip_locked=True)
    else:
        stmt_grace = stmt_grace.with_for_update()

    expired_grace = db.execute(stmt_grace).scalars().all()

    orchestrator = get_server_lifecycle_orchestrator()
    for sub in expired_grace:
        server = db.get(Server, sub.server_id)
        sub.status = BillingSubscriptionStatus.suspended
        sub.updated_at = now
        db.add(sub)
        if server is not None:
            try:
                orchestrator.stop_server(server)
            except Exception:
                logger.exception("Failed to stop suspended server %s", server.id)
            server.state = ServerState.suspended
            # Clear resource limits — server is no longer on a paid plan
            cfg = dict(server.mc_config or {})
            cfg.pop("resource_limits", None)
            cfg.pop("billing_plan_id", None)
            server.mc_config = cfg
            db.add(server)

        db.add(
            BillingAuditLog(
                user_id=sub.user_id,
                server_id=sub.server_id,
                transaction_id=None,
                action="subscription_suspended",
                details={"subscription_id": str(sub.id), "expired_at": sub.expires_at.isoformat()},
            )
        )
        suspended_count += 1
        to_invalidate.add((sub.user_id, sub.server_id))

    db.commit()

    for user_id, server_id in to_invalidate:
        invalidate_billing_caches(user_id=user_id, server_id=server_id)

    return {"grace_period": moved_to_grace, "suspended": suspended_count}


def _expiration_loop() -> None:
    settings = get_settings()
    interval = max(10, int(settings.subscription_expiration_poll_seconds))
    while True:
        db = SessionLocal()
        try:
            process_subscription_expirations(db)
        except Exception:
            logger.exception("Subscription expiration pass failed")
            db.rollback()
        finally:
            db.close()
        time.sleep(interval)


def start_subscription_expiration_worker_once() -> None:
    settings = get_settings()
    if not settings.subscription_expiration_enabled:
        return
    global _EXPIRATION_STARTED
    with _EXPIRATION_GUARD:
        if _EXPIRATION_STARTED:
            return
        _EXPIRATION_STARTED = True
        thread = threading.Thread(target=_expiration_loop, name="subscription-expiration", daemon=True)
        thread.start()
