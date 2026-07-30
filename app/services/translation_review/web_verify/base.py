"""联网查证插件接口定义。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WebVerifier(Protocol):
    key: str

    def is_available(self) -> bool:
        ...

    async def augment_messages(
        self,
        messages: list[dict],
        *,
        queries: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        """
        返回 (augmented_messages, extra_request_body)。
        extra_request_body 会 merge 进 request_chat_completion 的 extra_body。
        """
        ...
