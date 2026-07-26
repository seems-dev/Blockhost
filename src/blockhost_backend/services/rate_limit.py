"""Sliding-window rate limiting with Redis + in-process fallback."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_LOCAL_BUCKETS: dict[str, deque[float]] = {}
_LOCAL_LOCK = threading.Lock()


def _local_check(key: str, *, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _LOCAL_LOCK:
        bucket = _LOCAL_BUCKETS.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _redis_check(key: str, *, limit: int, window_seconds: int) -> bool | None:
    try:
        from blockhost_backend.services.api_cache import _get_redis_client

        client = _get_redis_client()
        if client is None:
            return None
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception as exc:
        logger.debug("Redis rate limit unavailable: %s", exc)
        return None


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Raise HTTP 429 if the key exceeded its limit within the window."""
    allowed = _redis_check(key, limit=limit, window_seconds=window_seconds)
    if allowed is None:
        allowed = _local_check(key, limit=limit, window_seconds=window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(window_seconds)},
        )
