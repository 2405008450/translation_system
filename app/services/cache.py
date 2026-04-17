from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any

from app.config import get_settings

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ModuleNotFoundError:  # pragma: no cover - 允许在未安装 redis 的环境中走内存兜底
    Redis = None

    class RedisError(RuntimeError):
        pass


logger = logging.getLogger(__name__)


@dataclass
class _MemoryCacheEntry:
    payload: str
    expires_at: float | None


class _InMemoryJsonCache:
    def __init__(self) -> None:
        self._entries: dict[str, _MemoryCacheEntry] = {}
        self._lock = Lock()

    def get_json(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at is not None and entry.expires_at <= time.time():
                self._entries.pop(key, None)
                return None
            return json.loads(entry.payload)

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = None if ttl_seconds is None else time.time() + ttl_seconds
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._entries[key] = _MemoryCacheEntry(payload=payload, expires_at=expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class _RedisJsonCache:
    def __init__(self, redis_url: str) -> None:
        self._client = Redis.from_url(redis_url, decode_responses=True)

    def get_json(self, key: str) -> Any | None:
        try:
            payload = self._client.get(key)
        except RedisError as exc:
            logger.warning("cache redis get failed key=%s error=%s", key, exc)
            return None
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("cache redis payload is not valid json key=%s", key)
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        try:
            if ttl_seconds is None:
                self._client.set(key, payload)
            else:
                self._client.set(key, payload, ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("cache redis set failed key=%s error=%s", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except RedisError as exc:
            logger.warning("cache redis delete failed key=%s error=%s", key, exc)


@lru_cache
def _get_cache_backend() -> _InMemoryJsonCache | _RedisJsonCache:
    settings = get_settings()
    if settings.redis_url and Redis is not None:
        logger.info("json cache backend initialized with redis")
        return _RedisJsonCache(settings.redis_url)

    if settings.redis_url and Redis is None:
        logger.warning("redis_url is configured but redis dependency is unavailable, fallback to memory cache")

    return _InMemoryJsonCache()


def get_json(key: str) -> Any | None:
    return _get_cache_backend().get_json(key)


def set_json(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    _get_cache_backend().set_json(key, value, ttl_seconds)


def delete(key: str) -> None:
    _get_cache_backend().delete(key)
