"""Production environment validation."""

from __future__ import annotations

from blockhost_backend.config.config_manager import Settings

_FORBIDDEN_SECRETS = frozenset(
    {
        "change-me",
        "change-me-in-dev",
        "change-me-in-prod",
        "change-me-billing-secret",
    }
)


def validate_production_settings(settings: Settings) -> None:
    if not settings.production_mode:
        return

    errors: list[str] = []

    if settings.database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must be PostgreSQL when PRODUCTION_MODE=true")

    if settings.jwt_secret_key in _FORBIDDEN_SECRETS or len(settings.jwt_secret_key) < 32:
        errors.append("JWT_SECRET_KEY must be set to a strong secret (32+ characters)")

    if settings.worker_agent_token in _FORBIDDEN_SECRETS or len(settings.worker_agent_token) < 32:
        errors.append("WORKER_AGENT_TOKEN must be set to a strong secret (32+ characters)")

    if settings.billing_provider == "razorpay":
        if not settings.razorpay_key_id or settings.razorpay_key_id.startswith("change-me"):
            errors.append("RAZORPAY_KEY_ID must be set for live billing")
        if not settings.razorpay_key_secret or settings.razorpay_key_secret.startswith("change-me"):
            errors.append("RAZORPAY_KEY_SECRET must be set for live billing")
        if not settings.razorpay_webhook_secret or settings.razorpay_webhook_secret.startswith("change-me"):
            errors.append("RAZORPAY_WEBHOOK_SECRET must be set for live billing")

    if settings.cors_origins.strip() == "*":
        errors.append("CORS_ORIGINS must list explicit origins in production (not '*')")

    if settings.allow_auto_node_registration:
        errors.append("ALLOW_AUTO_NODE_REGISTRATION must be false in production")

    if errors:
        raise RuntimeError(
            "Production configuration invalid:\n" + "\n".join(f"  - {e}" for e in errors)
        )
