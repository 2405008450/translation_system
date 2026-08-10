"""
进程级并发闸门与令牌桶限流。

使用方式：
    async with llm_gate():
        result = await request_chat_completion(...)

设计要点：
- Semaphore 限制"同时在飞"的请求数（translation_review_max_concurrency, 默认 3）
- 令牌桶限制"每分钟总量"（translation_review_requests_per_minute, 默认 60）
- 两者同时生效，不可互相替代
- 模块级单例——在单个 worker 进程内是真正的全局单点
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from weakref import WeakKeyDictionary

from app.config import get_settings


class _TokenBucket:
    """简单异步令牌桶。"""

    def __init__(self, rate_per_minute: float) -> None:
        self._rate_per_second = rate_per_minute / 60.0
        self._tokens: float = rate_per_minute  # 满桶启动
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            # 补充令牌
            self._tokens = min(
                self._tokens + elapsed * self._rate_per_second,
                self._rate_per_second * 60,  # 上限 = 1 分钟满额
            )
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

        # 令牌不足，等待
        wait_seconds = (1.0 - self._tokens) / self._rate_per_second
        await asyncio.sleep(wait_seconds)
        async with self._lock:
            self._tokens = max(self._tokens - 1.0, 0)


_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_token_buckets: WeakKeyDictionary[asyncio.AbstractEventLoop, _TokenBucket] = WeakKeyDictionary()


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        config = get_settings()
        semaphore = asyncio.Semaphore(max(1, config.translation_review_max_concurrency))
        _semaphores[loop] = semaphore
    return semaphore


def _get_token_bucket() -> _TokenBucket:
    loop = asyncio.get_running_loop()
    token_bucket = _token_buckets.get(loop)
    if token_bucket is None:
        config = get_settings()
        token_bucket = _TokenBucket(max(1, config.translation_review_requests_per_minute))
        _token_buckets[loop] = token_bucket
    return token_bucket


@asynccontextmanager
async def llm_gate() -> AsyncGenerator[None, None]:
    """
    上下文管理器：持有 Semaphore 槽位 + 消耗令牌后执行，保证：
    1. 同时发出的请求数 ≤ max_concurrency
    2. 每分钟请求总量 ≤ requests_per_minute
    """
    await _get_token_bucket().acquire()
    async with _get_semaphore():
        yield
