"""句段变更事件的 Redis pub/sub 通道。

保存/确认/项目同步落库后发布文件级变更事件；Web 进程的 SSE 端点订阅后
通知前端拉取增量句段。Redis 不可用时静默降级（前端保留轮询兜底）。
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Iterable
from uuid import UUID

from app.config import get_settings

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ModuleNotFoundError:  # pragma: no cover - 未安装 redis 时完全降级
    Redis = None

    class RedisError(RuntimeError):
        pass

try:
    import redis.asyncio as aioredis
except ModuleNotFoundError:  # pragma: no cover
    aioredis = None


logger = logging.getLogger(__name__)

SEGMENT_EVENT_CHANNEL_PREFIX = "segment-events:file:"
_PUBLISH_CONNECT_TIMEOUT_SECONDS = 0.3
_PUBLISH_OPERATION_TIMEOUT_SECONDS = 0.5


def segment_event_channel(file_record_id: UUID | str) -> str:
    return f"{SEGMENT_EVENT_CHANNEL_PREFIX}{file_record_id}"


def segment_events_available() -> bool:
    settings = get_settings()
    return bool(
        getattr(settings, "segment_events_enabled", True)
        and settings.redis_url
        and aioredis is not None
    )


@lru_cache
def _get_publish_client():
    settings = get_settings()
    if not settings.redis_url or Redis is None:
        return None
    try:
        return Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=_PUBLISH_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_PUBLISH_OPERATION_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
    except (RedisError, OSError) as exc:
        logger.warning("segment events redis unavailable: %s", exc)
        return None


def publish_segment_changes(file_record_ids: Iterable[UUID | str]) -> None:
    """发布文件句段变更事件；必须在数据库事务提交后调用。"""
    settings = get_settings()
    if not getattr(settings, "segment_events_enabled", True):
        return
    unique_ids = list(dict.fromkeys(str(item) for item in file_record_ids if item))
    if not unique_ids:
        return
    client = _get_publish_client()
    if client is None:
        return
    payload_ts = time.time()
    try:
        for file_record_id in unique_ids:
            client.publish(
                segment_event_channel(file_record_id),
                json.dumps({"file_record_id": file_record_id, "ts": payload_ts}),
            )
    except (RedisError, OSError) as exc:
        logger.debug("segment events publish failed: %s", exc)


def create_async_subscriber_client():
    """为 SSE 端点创建独立的异步 Redis 客户端；调用方负责关闭。"""
    settings = get_settings()
    if not segment_events_available():
        return None
    try:
        return aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
        )
    except Exception as exc:  # pragma: no cover - 连接串异常等罕见情况
        logger.warning("segment events async client init failed: %s", exc)
        return None
