from __future__ import annotations

from blockhost_backend.database.schema import SubscriptionTier


TIER_RESOURCE_LIMITS: dict[SubscriptionTier, dict[str, int]] = {
    SubscriptionTier.free: {"ram_mb": 512, "cpu_quota_pct": 50},
    SubscriptionTier.premium: {"ram_mb": 1024, "cpu_quota_pct": 100},
}
