from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

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


def _amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def sign_payment_payload(*, provider_order_id: str, provider_payment_id: str, amount: Decimal, currency: str) -> str:
    settings = get_settings()
    body = f"{provider_order_id}|{provider_payment_id}|{_amount(amount)}|{currency.upper()}".encode("utf-8")
    return hmac.new(settings.billing_signature_secret.encode("utf-8"), body, "sha256").hexdigest()


def create_provider_order(*, amount: Decimal, currency: str) -> ProviderOrder:
    return ProviderOrder(
        provider=get_settings().billing_provider,
        provider_order_id=f"order_{uuid.uuid4().hex}",
        amount=amount,
        currency=currency.upper(),
    )


def verify_provider_signature(
    *,
    provider_order_id: str,
    provider_payment_id: str,
    amount: Decimal,
    currency: str,
    signature: str,
) -> bool:
    expected = sign_payment_payload(
        provider_order_id=provider_order_id,
        provider_payment_id=provider_payment_id,
        amount=amount,
        currency=currency,
    )
    return hmac.compare_digest(expected, signature)


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
    tx = db.execute(
        select(BillingTransaction)
        .where(BillingTransaction.provider_order_id == provider_order_id)
        .with_for_update()
    ).scalars().one_or_none()
    if tx is None:
        raise BillingError("order_not_found", "Payment order not found")
    if tx.user_id != user_id:
        raise BillingError("unauthorized", "User does not own this transaction")

    if tx.status == BillingTransactionStatus.paid:
        subscription = db.get(BillingSubscription, tx.subscription_id) if tx.subscription_id else None
        if subscription is None:
            raise BillingError("transaction_inconsistent", "Paid transaction is missing subscription")
        return tx, subscription
    if tx.status != BillingTransactionStatus.pending:
        raise BillingError("transaction_not_pending", "Transaction is not pending")

    # Check for duplicate payment ID to prevent replay/double-spend attacks
    dup = db.execute(
        select(BillingTransaction).where(
            BillingTransaction.provider_payment_id == provider_payment_id,
            BillingTransaction.status == BillingTransactionStatus.paid
        )
    ).scalars().first()
    if dup is not None:
        raise BillingError("payment_id_reused", "This payment ID has already been verified and processed")

    expected_amount = Decimal(tx.amount)
    expected_currency = tx.currency.upper()
    if amount is not None and Decimal(amount).quantize(Decimal("0.01")) != expected_amount.quantize(Decimal("0.01")):
        raise BillingError("amount_mismatch", "Payment amount does not match")
    if currency is not None and currency.upper() != expected_currency:
        raise BillingError("currency_mismatch", "Payment currency does not match")
    if not verify_provider_signature(
        provider_order_id=provider_order_id,
        provider_payment_id=provider_payment_id,
        amount=expected_amount,
        currency=expected_currency,
        signature=signature,
    ):
        raise BillingError("invalid_signature", "Payment signature verification failed")

    try:
        server = db.get(Server, tx.server_id)
        plan = db.get(BillingPlan, tx.target_plan_id)
        if server is None or plan is None or not plan.active:
            raise BillingError("upgrade_target_missing", "Upgrade target no longer exists")

        now = utcnow()
        existing = db.execute(
            select(BillingSubscription).where(
                BillingSubscription.server_id == tx.server_id,
                BillingSubscription.status.in_(
                    [BillingSubscriptionStatus.active, BillingSubscriptionStatus.grace_period]
                ),
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
        db.add(
            BillingAuditLog(
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
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "provider_payment_id" in str(exc).lower():
            raise BillingError("payment_id_reused", "This payment ID has already been verified and processed")
        raise
    except Exception:
        db.rollback()
        raise

    invalidate_billing_caches(user_id=tx.user_id, server_id=tx.server_id)
    db.refresh(tx)
    db.refresh(subscription)
    return tx, subscription


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
            # Reset mc_config limits to tier defaults or clear resource_limits
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
