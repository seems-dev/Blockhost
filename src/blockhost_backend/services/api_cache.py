from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover - depends on deployment extras
    redis = None

from blockhost_backend.config.config_manager import get_settings

logger = logging.getLogger(__name__)

_LOCAL_CACHE: dict[str, tuple[float, Any]] = {}
_LOCAL_CACHE_LOCK = threading.Lock()
_REDIS_CLIENT: Any | None = None
_REDIS_LOCK = threading.Lock()
_REDIS_DISABLED_UNTIL = 0.0
_REDIS_BACKOFF_SECONDS = 30.0


def _local_get(key: str) -> Any | None:
    now = time.monotonic()
    with _LOCAL_CACHE_LOCK:
        cached = _LOCAL_CACHE.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= now:
            _LOCAL_CACHE.pop(key, None)
            return None
        return value


def _local_set(key: str, value: Any, ttl_seconds: int) -> None:
    with _LOCAL_CACHE_LOCK:
        _LOCAL_CACHE[key] = (time.monotonic() + ttl_seconds, value)


def _local_delete(key: str) -> None:
    with _LOCAL_CACHE_LOCK:
        _LOCAL_CACHE.pop(key, None)


def _local_delete_prefix(prefix: str) -> None:
    with _LOCAL_CACHE_LOCK:
        for key in list(_LOCAL_CACHE):
            if key.startswith(prefix):
                _LOCAL_CACHE.pop(key, None)


def _disable_redis_temporarily(exc: Exception) -> None:
    global _REDIS_DISABLED_UNTIL
    _REDIS_DISABLED_UNTIL = time.monotonic() + _REDIS_BACKOFF_SECONDS
    logger.debug("Redis cache unavailable; falling back to local cache: %s", exc)


def _get_redis_client() -> Any | None:
    global _REDIS_CLIENT
    if redis is None:
        return None
    if time.monotonic() < _REDIS_DISABLED_UNTIL:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        settings = get_settings()
        try:
            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.05,
                socket_timeout=0.05,
            )
            client.ping()
        except Exception as exc:
            _disable_redis_temporarily(exc)
            return None
        _REDIS_CLIENT = client
        return client


class ApiCache:
    def get_json(self, key: str) -> Any | None:
        local = _local_get(key)
        if local is not None:
            return local

        client = _get_redis_client()
        if client is None:
            return None
        try:
            raw = client.get(key)
        except Exception as exc:
            _disable_redis_temporarily(exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self.delete(key)
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        ttl = max(1, int(ttl_seconds))
        _local_set(key, value, ttl)

        client = _get_redis_client()
        if client is None:
            return
        try:
            client.setex(key, ttl, json.dumps(value, separators=(",", ":")))
        except Exception as exc:
            _disable_redis_temporarily(exc)

    def get_or_set_json(self, key: str, ttl_seconds: int, factory: Callable[[], Any]) -> Any:
        cached = self.get_json(key)
        if cached is not None:
            return cached
        value = factory()
        self.set_json(key, value, ttl_seconds)
        return value

    def delete(self, key: str) -> None:
        _local_delete(key)
        client = _get_redis_client()
        if client is None:
            return
        try:
            client.delete(key)
        except Exception as exc:
            _disable_redis_temporarily(exc)

    def delete_prefix(self, prefix: str) -> None:
        _local_delete_prefix(prefix)
        client = _get_redis_client()
        if client is None:
            return
        try:
            for key in client.scan_iter(match=f"{prefix}*"):
                client.delete(key)
        except Exception as exc:
            _disable_redis_temporarily(exc)


_API_CACHE = ApiCache()


def get_api_cache() -> ApiCache:
    return _API_CACHE
